"""Strict GitHub REST pagination with verified repository endpoint aliases."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .errors import PaginationError


def _checked_url(url, expected_paths, operation):
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.github.com"
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or "#" in url
            or parsed.path not in expected_paths
            or any(c.isspace() or ord(c) < 32 for c in url)
            or "\\" in url
        ):
            raise ValueError
        query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
        return urlunsplit(("https", "api.github.com", parsed.path, query, ""))
    except (ValueError, TypeError):
        raise PaginationError(
            "unsafe or malformed REST pagination URL", operation=operation
        ) from None


def _next_link(header, operation):
    if header is None:
        return None
    if not isinstance(header, str) or not header.strip():
        raise PaginationError("malformed REST Link header", operation=operation)
    # Split only at link boundaries; commas can occur within quoted parameters.
    entries = re.split(r",\s*(?=<)", header)
    next_url = None
    for entry in entries:
        match = re.fullmatch(
            r'\s*<([^<>]+)>((?:\s*;\s*[\w-]+\s*=\s*(?:"[^"\r\n]*"|[^\s;,"<>]+))+)\s*',
            entry,
        )
        if match is None:
            raise PaginationError("malformed REST Link header", operation=operation)
        relations = re.findall(
            r';\s*rel\s*=\s*(?:"([^"]*)"|([^\s;]+))', match[2], re.IGNORECASE
        )
        if len(relations) != 1:
            raise PaginationError(
                "missing or repeated REST link relation", operation=operation
            )
        if "next" in (relations[0][0] or relations[0][1]).split():
            if next_url is not None:
                raise PaginationError("repeated REST next link", operation=operation)
            next_url = match[1]
    return next_url


def iter_rest_list(client, url, *, operation_name, repository_database_id=None):
    expected_paths = {urlsplit(url).path}
    if repository_database_id is not None:
        if type(repository_database_id) is not int or repository_database_id <= 0:
            raise PaginationError(
                "invalid repository database ID", operation=operation_name
            )
        # GitHub Link headers can replace owner/name with this verified numeric ID.
        expected_paths.add(f"/repositories/{repository_database_id}/issues")
    seen = set()
    while url is not None:
        normalized = _checked_url(url, expected_paths, operation_name)
        if normalized in seen:
            raise PaginationError(
                "repeated REST pagination URL", operation=operation_name
            )
        seen.add(normalized)
        items, link = client._get_rest_list(normalized, operation_name=operation_name)
        url = _next_link(link, operation_name)
        if url is not None:
            # Reject unsafe or looping links before exposing any items from this page.
            target = _checked_url(url, expected_paths, operation_name)
            if target in seen:
                raise PaginationError(
                    "repeated REST pagination URL", operation=operation_name
                )
        yield from items

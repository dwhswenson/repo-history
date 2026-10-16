"""Checked synchronous GitHub requests with bounded retries and separate budgets."""

import logging
import math
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from .authentication import resolve_token
from .errors import (
    AuthenticationError,
    GraphQLError,
    QueryResourceError,
    RateLimitError,
    TransportError,
)

LOG = logging.getLogger(__name__)
ENDPOINT = "https://api.github.com/graphql"
_PARENT_CONTEXT = ContextVar("github_parent_log_context", default="")


@contextmanager
def _parent_log_context(kind: str, number: int):
    """Carry a parent number through nested requests and restore it on exit."""
    token = _PARENT_CONTEXT.set(f" {kind}={number}")
    try:
        yield
    finally:
        _PARENT_CONTEXT.reset(token)


def _log_embedded_page(operation: str, count: int, more: bool):
    LOG.debug(
        "GitHub operation=%s%s page=1 source=embedded nodes=%s has_next_page=%s",
        operation,
        _PARENT_CONTEXT.get(),
        count,
        more,
    )


@dataclass
class _APIUsage:
    """Primary-budget points measured during this client lifetime."""

    points: int = 0
    unmeasured_attempts: int = 0
    attempts: int = 0
    retries: int = 0

    def record(self, cost):
        if type(cost) is int and cost >= 0:
            self.points += cost
            self.unmeasured_attempts -= 1


class _BearerAuth(requests.auth.AuthBase):
    """Explicit auth prevents Requests' implicit netrc auth from overriding it."""

    def __init__(self, token):
        self._token = token

    def __call__(self, request):
        request.headers["Authorization"] = f"Bearer {self._token}"
        return request


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _reset(value):
    number = _number(value)
    if number is not None:
        try:
            return datetime.fromtimestamp(number, timezone.utc)
        except (OverflowError, ValueError, OSError):
            return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (AttributeError, ValueError, TypeError):
        return None


def _retry_after(value, now):
    number = _number(value)
    if number is not None:
        return number
    try:
        date = parsedate_to_datetime(value)
        return max(0, date.timestamp() - now) if date.tzinfo else None
    except (ValueError, TypeError, OverflowError, AttributeError):
        return None


def _error_kind(error):
    extension = error.get("extensions")
    extension = extension if isinstance(extension, dict) else {}
    kind = error.get("type") or extension.get("type") or extension.get("code")
    return kind.upper() if isinstance(kind, str) else None


def _resource_error(error):
    message = error.get("message", "")
    return _error_kind(error) in {
        "RESOURCE_LIMITS_EXCEEDED",
        "MAX_NODE_LIMIT_EXCEEDED",
        "TIMEOUT",
    } or (
        isinstance(message, str)
        and any(
            text in message.lower()
            for text in ("resource limit", "couldn't respond to your request in time")
        )
    )


class GraphQLClient:
    """Client-owned session; sequential read-only queries, never partial success."""

    def __init__(
        self,
        token: str | None = None,
        *,
        timeout: tuple[float, float] = (10, 60),
        max_attempts: int = 4,
    ) -> None:
        token = resolve_token(token)
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if (
            not isinstance(timeout, (tuple, list))
            or len(timeout) != 2
            or any(
                type(n) not in (int, float) or not math.isfinite(n) or n <= 0
                for n in timeout
            )
        ):
            raise ValueError("timeout must contain positive connect/read seconds")
        self.timeout = tuple(timeout)
        self.max_attempts = max_attempts
        self._session = requests.Session()
        self._session.auth = _BearerAuth(token)
        self._session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "repo-history-tracking",
            }
        )
        self._sleep = time.sleep
        self._clock = time.time
        self._closed = False
        self._blocked = {}
        self._api_usage = {"rest": _APIUsage(), "graphql": _APIUsage()}

    def close(self) -> None:
        if not self._closed:
            self._session.close()
            self._closed = True

    def __enter__(self):
        if self._closed:
            raise TransportError("client is closed")
        return self

    def __exit__(self, *exc):
        self.close()

    def execute(
        self,
        document: str,
        variables: Mapping[str, Any],
        *,
        operation_name: str,
    ) -> dict[str, Any]:
        """Execute a named query, rejecting incomplete responses before returning."""
        data, _ = self._execute(document, variables, operation_name=operation_name)
        return data

    def iter_connection(
        self,
        document: str,
        variables: Mapping[str, Any],
        *,
        operation_name: str,
        connection_path: tuple[str, ...] | list[str],
        cursor_variable: str = "after",
        page_size_variable: str = "first",
        page_size: int = 100,
        start_page: int = 1,
    ) -> Iterator[dict[str, Any] | None]:
        """Yield checked pages; start_page sets local logging, never API variables.

        Callers must discard all results after any failure.
        """
        from .pagination import iter_connection

        return iter_connection(
            self,
            document,
            variables,
            operation_name=operation_name,
            connection_path=connection_path,
            cursor_variable=cursor_variable,
            page_size_variable=page_size_variable,
            page_size=page_size,
            start_page=start_page,
        )

    def _execute(
        self,
        document,
        variables,
        *,
        operation_name,
        page_size_variable=None,
        page_number=None,
    ):
        if self._closed:
            raise TransportError("client is closed", operation=operation_name)
        if not isinstance(document, str) or not document.strip():
            raise ValueError("document must be a nonempty string")
        if not isinstance(operation_name, str) or not operation_name.strip():
            raise ValueError("operation_name must be a nonempty string")
        if not isinstance(variables, Mapping):
            raise ValueError("variables must be a mapping")
        values = dict(variables)
        log_context = _PARENT_CONTEXT.get()
        if page_number is not None:
            log_context += f" page={page_number}"
        for attempt in range(1, self.max_attempts + 1):
            self._check_budget("graphql", operation_name)
            diagnostic = dict(operation=operation_name, attempts=attempt)
            resource_failure = False
            self._api_usage["graphql"].unmeasured_attempts += 1
            self._api_usage["graphql"].attempts += 1
            self._api_usage["graphql"].retries += int(attempt > 1)
            try:
                response = self._session.post(
                    ENDPOINT,
                    json={
                        "query": document,
                        "variables": values.copy(),
                        "operationName": operation_name,
                    },
                    timeout=self.timeout,
                    allow_redirects=False,
                )
            except requests.exceptions.SSLError:
                raise TransportError("TLS verification failed", **diagnostic) from None
            except (requests.Timeout, requests.ConnectionError):
                failure = TransportError("network request failed", **diagnostic)
            except requests.RequestException:
                raise TransportError("request failed", **diagnostic) from None
            else:
                try:
                    headers = {k.lower(): v for k, v in response.headers.items()}
                    diagnostic.update(
                        status=response.status_code,
                        request_id=headers.get("x-github-request-id"),
                    )
                    try:
                        body = response.json()
                    except ValueError:
                        body = None
                    errors = body.get("errors") if isinstance(body, dict) else None
                    data = body.get("data") if isinstance(body, dict) else None
                    rate = data.get("rateLimit") if isinstance(data, dict) else None
                    rate = rate if isinstance(rate, dict) else {}
                    self._api_usage["graphql"].record(rate.get("cost"))
                    remaining = _number(
                        headers.get("x-ratelimit-remaining", rate.get("remaining"))
                    )
                    reset = _reset(
                        headers.get("x-ratelimit-reset", rate.get("resetAt"))
                    )
                    retry = _retry_after(headers.get("retry-after"), self._clock())
                    entries = errors if isinstance(errors, list) else []
                    kinds = set()
                    messages = []
                    for error in entries:
                        if isinstance(error, dict):
                            extension = error.get("extensions")
                            extension = extension if isinstance(extension, dict) else {}
                            kind = (
                                error.get("type")
                                or extension.get("type")
                                or extension.get("code")
                            )
                            if isinstance(kind, str):
                                kinds.add(kind.upper())
                            if isinstance(error.get("message"), str):
                                messages.append(error["message"].lower())
                    if isinstance(body, dict) and isinstance(body.get("message"), str):
                        messages.append(body["message"].lower())
                    limited_message = any(
                        "rate limit" in message or "rate-limit" in message
                        for message in messages
                    )
                    status = response.status_code
                    limited = (
                        status == 429
                        or "RATE_LIMITED" in kinds
                        or limited_message
                        or (bool(errors) and remaining == 0)
                        or (
                            status == 403
                            and ("retry-after" in headers or remaining == 0)
                        )
                    )
                    LOG.debug(
                        "GitHub operation=%s%s attempt=%s status=%s request_id=%s remaining=%s reset=%s retry_after=%s",
                        operation_name,
                        log_context,
                        attempt,
                        status,
                        diagnostic["request_id"],
                        remaining,
                        reset.astimezone() if reset is not None else None,
                        retry,
                    )
                    if limited:
                        self._record_limit(
                            "graphql",
                            remaining,
                            reset,
                            retry,
                            secondary=any("secondary" in m for m in messages),
                        )
                        raise RateLimitError(
                            "GitHub rate limit reached",
                            retry_after=retry,
                            reset_at=reset,
                            **diagnostic,
                        )
                    if status in (401, 403):
                        raise AuthenticationError(
                            "GitHub authentication or permission denied", **diagnostic
                        )
                    resource_failure = (
                        status in (502, 504)
                        or bool(
                            kinds
                            & {
                                "RESOURCE_LIMITS_EXCEEDED",
                                "MAX_NODE_LIMIT_EXCEEDED",
                                "TIMEOUT",
                            }
                        )
                        or any(
                            "resource limit" in message
                            or "couldn't respond to your request in time" in message
                            for message in messages
                        )
                    )
                    if status in (500, 502, 503, 504):
                        failure = (
                            QueryResourceError if resource_failure else TransportError
                        )("GitHub server request failed", **diagnostic)
                    elif not 200 <= status < 300:
                        raise TransportError("GitHub HTTP request failed", **diagnostic)
                    else:
                        if (
                            not isinstance(body, dict)
                            or (
                                errors is not None
                                and (
                                    not isinstance(errors, list)
                                    or any(
                                        not isinstance(e, dict)
                                        or not isinstance(e.get("message"), str)
                                        or (
                                            "extensions" in e
                                            and not isinstance(e["extensions"], dict)
                                        )
                                        for e in errors
                                    )
                                )
                            )
                            or ("errors" in body and errors is None)
                        ):
                            raise GraphQLError(
                                "malformed GraphQL response envelope", **diagnostic
                            )
                        if errors:
                            recoverable = all(
                                _resource_error(e)
                                or _error_kind(e)
                                in {"INTERNAL", "INTERNAL_SERVER_ERROR"}
                                for e in errors
                            )
                            if resource_failure and recoverable:
                                failure = QueryResourceError(
                                    "GraphQL query resource limit exceeded",
                                    **diagnostic,
                                )
                            elif recoverable:
                                failure = GraphQLError(
                                    "transient GraphQL error", **diagnostic
                                )
                            else:
                                raise GraphQLError(
                                    "GraphQL operation failed; partial data discarded",
                                    **diagnostic,
                                )
                        else:
                            if not isinstance(data, dict):
                                raise GraphQLError(
                                    "missing or malformed GraphQL data", **diagnostic
                                )
                            if remaining == 0:
                                self._blocked["graphql"] = (reset, None)
                            return data, values.get(page_size_variable)
                finally:
                    response.close()
            if resource_failure and page_size_variable is not None:
                size = values[page_size_variable]
                if size == 1:
                    raise failure
                values[page_size_variable] = max(1, size // 2)
                LOG.info(
                    "GitHub operation=%s%s attempt=%s page_size=%s",
                    operation_name,
                    log_context,
                    attempt,
                    values[page_size_variable],
                )
            if attempt == self.max_attempts:
                raise failure
            LOG.info(
                "GitHub operation=%s%s retry_after=%s attempt=%s",
                operation_name,
                log_context,
                2 ** (attempt - 1),
                attempt,
            )
            self._sleep(2 ** (attempt - 1))
        raise AssertionError("unreachable")

    def _check_budget(self, resource, operation):
        for key in ("secondary", resource):
            if key not in self._blocked:
                continue
            reset, retry_at = self._blocked[key]
            now = self._clock()
            unknown = reset is None and (key != "secondary" or retry_at is None)
            if (
                unknown
                or (reset is not None and now < reset.timestamp())
                or (retry_at is not None and now < retry_at)
            ):
                raise RateLimitError(
                    "GitHub rate budget exhausted",
                    operation=operation,
                    reset_at=reset,
                    retry_after=max(0, retry_at - now)
                    if retry_at is not None
                    else None,
                )
            del self._blocked[key]

    def _record_limit(self, resource, remaining, reset, retry, *, secondary=False):
        retry_at = self._clock() + retry if retry is not None else None
        if remaining == 0:
            self._blocked[resource] = (reset, retry_at)
        if secondary or retry is not None or remaining != 0:
            self._blocked["secondary"] = (reset, retry_at)

    def _get_rest_list(self, url, *, operation_name):
        """Fetch one checked REST page; URL safety is enforced by the paginator."""
        if self._closed:
            raise TransportError("client is closed", operation=operation_name)
        for attempt in range(1, self.max_attempts + 1):
            self._check_budget("rest", operation_name)
            diagnostic = dict(operation=operation_name, attempts=attempt)
            self._api_usage["rest"].unmeasured_attempts += 1
            self._api_usage["rest"].attempts += 1
            self._api_usage["rest"].retries += int(attempt > 1)
            try:
                response = self._session.get(
                    url,
                    headers={"X-GitHub-Api-Version": "2026-03-10"},
                    timeout=self.timeout,
                    allow_redirects=False,
                )
            except requests.exceptions.SSLError:
                raise TransportError("TLS verification failed", **diagnostic) from None
            except (requests.Timeout, requests.ConnectionError):
                failure = TransportError("network request failed", **diagnostic)
            except requests.RequestException:
                raise TransportError("request failed", **diagnostic) from None
            else:
                try:
                    headers = {k.lower(): v for k, v in response.headers.items()}
                    status = response.status_code
                    if status == 200:
                        self._api_usage["rest"].record(1)
                    diagnostic.update(
                        status=status, request_id=headers.get("x-github-request-id")
                    )
                    try:
                        body = response.json()
                    except ValueError:
                        body = None
                    message = body.get("message", "") if isinstance(body, dict) else ""
                    message = message.lower() if isinstance(message, str) else ""
                    remaining = _number(headers.get("x-ratelimit-remaining"))
                    reset = _reset(headers.get("x-ratelimit-reset"))
                    retry = _retry_after(headers.get("retry-after"), self._clock())
                    limited = status == 429 or (
                        status == 403
                        and (
                            remaining == 0
                            or "retry-after" in headers
                            or "rate limit" in message
                            or "rate-limit" in message
                        )
                    )
                    LOG.debug(
                        "GitHub REST operation=%s%s attempt=%s status=%s request_id=%s remaining=%s reset=%s retry_after=%s",
                        operation_name,
                        _PARENT_CONTEXT.get(),
                        attempt,
                        status,
                        diagnostic["request_id"],
                        remaining,
                        reset.astimezone() if reset is not None else None,
                        retry,
                    )
                    if limited:
                        self._record_limit(
                            "rest",
                            remaining,
                            reset,
                            retry,
                            secondary="secondary" in message,
                        )
                        raise RateLimitError(
                            "GitHub rate limit reached",
                            retry_after=retry,
                            reset_at=reset,
                            **diagnostic,
                        )
                    if status in (401, 403):
                        raise AuthenticationError(
                            "GitHub authentication or permission denied", **diagnostic
                        )
                    if status in (500, 502, 503, 504):
                        failure = TransportError(
                            "GitHub server request failed", **diagnostic
                        )
                    elif status != 200:
                        raise TransportError("GitHub HTTP request failed", **diagnostic)
                    else:
                        if not isinstance(body, list) or any(
                            not isinstance(item, dict) for item in body
                        ):
                            raise TransportError(
                                "malformed REST list response", **diagnostic
                            )
                        if remaining == 0:
                            self._blocked["rest"] = (reset, None)
                        return body, headers.get("link")
                finally:
                    response.close()
            if attempt == self.max_attempts:
                raise failure
            LOG.info(
                "GitHub REST operation=%s%s retry_after=%s attempt=%s",
                operation_name,
                _PARENT_CONTEXT.get(),
                2 ** (attempt - 1),
                attempt,
            )
            self._sleep(2 ** (attempt - 1))
        raise AssertionError("unreachable")

"""Internal full and experimental REST-bounded parent discovery."""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

from .errors import GraphQLError, TransportError
from .queries import (
    HISTORY_ISSUES_QUERY,
    HISTORY_PARENT_PAGE_SIZE,
    HISTORY_PULL_REQUESTS_QUERY,
    ISSUES_QUERY,
    PULL_REQUESTS_QUERY,
    parent_hydration_query,
)
from .rest_pagination import iter_rest_list

LOG = logging.getLogger(__name__)
OPERATION = "RepositoryParentDiscovery"
BOUNDED_DISCOVERY_LIMITATION = (
    "Experimental bounded parent discovery uses REST parent update timestamps; "
    "activities that do not advance those timestamps may be omitted. "
    "Completeness has not been established."
)


def _timestamp(item, name):
    # REST discovery timestamps are independent of GraphQL subject timestamps.
    value = item.get(name)
    try:
        if not isinstance(value, str):
            raise ValueError
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise TransportError(
            f"missing or malformed discovery timestamp: {name}", operation=OPERATION
        ) from None


def select_strategy(start, strategy):
    """Select and log the actual parent-discovery strategy before traversal."""
    if strategy == "bounded":
        try:
            start - timedelta(seconds=1)
        except OverflowError:
            LOG.info("Parent discovery selected full traversal: overlap underflow")
            strategy = "full"
    LOG.info("Parent discovery strategy=%s", strategy)
    return strategy


def discover_parents(
    client, repository, start, end, *, strategy, include_activity=False
):
    """Traverse using the strategy already selected by select_strategy."""
    if strategy == "full":
        for issues in (True, False):
            if issues and not repository.issues_enabled:
                continue
            operation = "RepositoryIssues" if issues else "RepositoryPullRequests"
            query = ISSUES_QUERY if issues else PULL_REQUESTS_QUERY
            if include_activity:
                query = HISTORY_ISSUES_QUERY if issues else HISTORY_PULL_REQUESTS_QUERY
            for item in client.iter_connection(
                query,
                {"id": repository.repository_node_id},
                operation_name=operation,
                connection_path=("node", "issues" if issues else "pullRequests"),
                page_size=HISTORY_PARENT_PAGE_SIZE if include_activity else 100,
            ):
                yield issues, item, operation
        return

    parts = repository.repository_full_name.split("/")
    if len(parts) != 2 or any(not part or part in (".", "..") for part in parts):
        raise GraphQLError("malformed repository full name", operation=OPERATION)
    endpoint = "https://api.github.com/repos/{}/{}/issues".format(
        *(quote(part, safe="") for part in parts)
    )
    since = start - timedelta(seconds=1)
    url = (
        endpoint
        + "?"
        + urlencode(
            {
                "state": "all",
                "per_page": 100,
                "since": f"{since.year:04d}-{since.month:02d}-{since.day:02d}T{since:%H:%M:%S}Z",
            }
        )
    )
    observed = {}
    retained = {}
    for item in iter_rest_list(
        client,
        url,
        operation_name=OPERATION,
        repository_database_id=repository.database_id,
    ):
        number = item.get("number")
        if type(number) is not int or not 1 <= number <= 2**31 - 1:
            raise TransportError("malformed discovery number", operation=OPERATION)
        created = _timestamp(item, "created_at")
        updated = _timestamp(item, "updated_at")
        issues = "pull_request" not in item
        if not issues:
            marker = item["pull_request"]
            if (
                not isinstance(marker, dict)
                or not isinstance(marker.get("url"), str)
                or not marker["url"]
            ):
                raise TransportError(
                    "malformed discovery PR marker", operation=OPERATION
                )
        identity = (issues, created)
        if number in observed and observed[number] != identity:
            raise TransportError("conflicting discovery identity", operation=OPERATION)
        observed[number] = identity
        if (
            (not issues or repository.issues_enabled)
            and updated >= start
            and created < end
        ):
            retained[number] = issues

    candidates = list(retained.items())
    for offset in range(0, len(candidates), 50):
        batch = candidates[offset : offset + 50]
        variables = {"id": repository.repository_node_id}
        variables.update({f"number{i}": number for i, (number, _) in enumerate(batch)})
        operation = "HydrateRepositoryParents"
        data = client.execute(
            parent_hydration_query(len(batch)), variables, operation_name=operation
        )
        node = data.get("node")
        if not isinstance(node, dict):
            raise GraphQLError("missing hydration repository", operation=operation)
        # Validate batch identities before handing parents to the shared collector.
        parents = []
        for i, (number, issues) in enumerate(batch):
            parent = node.get(f"parent{i}")
            if (
                not isinstance(parent, dict)
                or type(parent.get("number")) is not int
                or parent["number"] != number
                or parent.get("__typename") != ("Issue" if issues else "PullRequest")
            ):
                raise GraphQLError(
                    "missing or mismatched hydrated parent", operation=operation
                )
            parents.append((issues, parent, operation))
        yield from parents

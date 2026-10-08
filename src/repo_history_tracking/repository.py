"""Repository identity and captured default-head context, without collection."""

import re
from dataclasses import dataclass

from .errors import GraphQLError, RepositoryUnavailableError
from .queries import REPOSITORY_QUERY
from .transport import GraphQLClient


@dataclass(frozen=True)
class RepositorySnapshot:
    repository_node_id: str
    repository_full_name: str
    repository_url: str
    issues_enabled: bool
    default_branch: str | None
    default_branch_head: str | None


def lookup_repository(
    client: GraphQLClient, owner: str, repo: str
) -> RepositorySnapshot:
    if any(not isinstance(value, str) or not value.strip() for value in (owner, repo)):
        raise ValueError("owner and repo must be nonempty strings")
    operation = "RepositoryLookup"
    data = client.execute(
        REPOSITORY_QUERY, {"owner": owner, "name": repo}, operation_name=operation
    )
    if "repository" not in data:
        raise GraphQLError("missing repository result", operation=operation)
    item = data["repository"]
    if item is None:
        raise RepositoryUnavailableError(
            "repository unavailable to this credential", operation=operation
        )
    if (
        not isinstance(item, dict)
        or any(
            not isinstance(item.get(key), str) or not item[key]
            for key in ("id", "nameWithOwner", "url")
        )
        or type(item.get("hasIssuesEnabled")) is not bool
        or type(item.get("isEmpty")) is not bool
        or "defaultBranchRef" not in item
    ):
        raise GraphQLError("malformed repository metadata", operation=operation)
    branch = item["defaultBranchRef"]
    name = head = None
    if branch is None:
        if not item["isEmpty"]:
            raise GraphQLError(
                "nonempty repository has no captured default head", operation=operation
            )
    else:
        target = branch.get("target") if isinstance(branch, dict) else None
        if (
            not isinstance(branch, dict)
            or not isinstance(branch.get("name"), str)
            or not branch["name"]
            or not isinstance(target, dict)
            or target.get("__typename") != "Commit"
            or not isinstance(target.get("oid"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", target["oid"])
        ):
            raise GraphQLError("malformed default branch target", operation=operation)
        name, head = branch["name"], target["oid"]
    return RepositorySnapshot(
        item["id"],
        item["nameWithOwner"],
        item["url"],
        item["hasIssuesEnabled"],
        name,
        head,
    )

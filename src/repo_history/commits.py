"""Buffered commit history, anchored to a captured repository head."""

import re
from datetime import datetime, timedelta, timezone

from .collection import _actor, _field, _in_interval, _object, _string, _timestamp
from .errors import GraphQLError, PaginationError
from .identities import unresolved_git_actor
from .models import Activity, CommitMetadata, Credit, GitIdentity, Subject
from .queries import (
    COMMIT_ACTOR_QUERY,
    COMMIT_AUTHORS_QUERY,
    COMMIT_HISTORY_QUERY,
    COMMIT_PARENTS_QUERY,
)
from .transport import _log_embedded_page

BUFFER_SECONDS = 86400
FILTER_MIN = datetime(1970, 1, 1, tzinfo=timezone.utc)
# The GraphQL reference documents December 13, not REST's December 31.
FILTER_MAX = datetime(2099, 12, 13, tzinfo=timezone.utc)
BOUNDED_LIMITATION = (
    "Commit history uses a 24-hour buffer on either side of the month; unusual "
    "timestamps can hide matching ancestors before the buffered lower bound. "
    "Commit counts support trend reporting, not exhaustive historical audits."
)
OPERATION = "CommitHistory"


def history_bounds(start, end):
    """Return strategy and variables; unsupported bounds use full history."""
    try:
        since = start - timedelta(seconds=BUFFER_SECONDS)
        until = end + timedelta(seconds=BUFFER_SECONDS)
    except OverflowError:
        return "full", {}
    if since < FILTER_MIN or until >= FILTER_MAX + timedelta(days=1):
        return "full", {}
    return (
        "bounded",
        {
            "since": since.isoformat(),
            "until": until.isoformat(),
        },
    )


def _sha(item, operation):
    sha = _string(_object(item, operation), "oid", operation)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise GraphQLError("malformed commit SHA", operation=operation)
    return sha


def _identity(value, operation, client, records, resolved):
    if value is None:
        return None, GitIdentity()
    item = _object(value, operation)
    name = _string(item, "name", operation, nullable=True, empty=True)
    email = _string(item, "email", operation, nullable=True, empty=True)
    user = _field(item, "user", operation)
    actor = unresolved_git_actor(email, name)
    if user is not None:
        linked = _actor(user, operation)
        # GitActor.user can project a Bot as User (e.g. Copilot), even changing
        # its login. Lifecycle actors and node(id:) provide canonical identity.
        actor = resolved.get(linked.key) or records.actors.get(linked.key)
        if actor is None:
            data = client.execute(
                COMMIT_ACTOR_QUERY, {"id": linked.key}, operation_name="CommitActor"
            )
            actor = _actor(_field(data, "node", "CommitActor"), "CommitActor")
            if actor is None or actor.key != linked.key:
                raise GraphQLError(
                    "missing or mismatched canonical commit actor",
                    operation="CommitActor",
                )
        resolved[linked.key] = actor
    return actor, GitIdentity(name, email, actor.key if actor else None)


def _nested(client, item, field, query, operation):
    """Validate an embedded first page, then independently fetch continuation pages."""
    page = _object(_field(item, field, operation), operation)
    nodes = page.get("nodes")
    info = page.get("pageInfo")
    if (
        not isinstance(nodes, list)
        or any(not isinstance(node, dict) for node in nodes)
        or not isinstance(info, dict)
        or type(info.get("hasNextPage")) is not bool
        or "endCursor" not in info
        or (
            info["endCursor"] is not None
            and (not isinstance(info["endCursor"], str) or not info["endCursor"])
        )
        or (info.get("hasNextPage") and info.get("endCursor") is None)
    ):
        raise PaginationError(
            "malformed initial commit connection", operation=operation
        )
    _log_embedded_page(operation, len(nodes), info["hasNextPage"])
    yield from nodes
    if info["hasNextPage"]:
        continuation = client.iter_connection(
            query,
            {"id": _string(item, "id", operation), "after": info["endCursor"]},
            operation_name=operation,
            connection_path=("node", field),
            page_size=10,
            start_page=2,
        )
        for node in continuation:
            yield _object(node, operation)


def collect_commits(client, repository, start, end, records):
    strategy, bounds = (
        ("full", {}) if start is None and end is None else history_bounds(start, end)
    )
    if repository.default_branch_head is None:
        return strategy
    resolved = {}
    history = client.iter_connection(
        COMMIT_HISTORY_QUERY,
        {
            "id": repository.repository_node_id,
            "head": repository.default_branch_head,
            **bounds,
        },
        operation_name=OPERATION,
        connection_path=("node", "object", "history"),
    )
    for value in history:
        item = _object(value, OPERATION)
        sha = _sha(item, OPERATION)
        occurred = _timestamp(item, OPERATION, "committedDate")
        if not _in_interval(occurred, start, end):
            continue
        node_id = _string(item, "id", OPERATION)
        url = _string(item, "url", OPERATION)
        author, _ = _identity(
            _field(item, "author", OPERATION), OPERATION, client, records, resolved
        )
        committer, git_committer = _identity(
            _field(item, "committer", OPERATION), OPERATION, client, records, resolved
        )
        author_key = author.key if author else None
        credits = {Credit(author_key, "author")}
        actors = [author, committer]
        for value in _nested(
            client, item, "authors", COMMIT_AUTHORS_QUERY, "CommitAuthors"
        ):
            actor, _ = _identity(value, "CommitAuthors", client, records, resolved)
            actors.append(actor)
            key = actor.key if actor else None
            if key != author_key:
                credits.add(Credit(key, "co_author"))
        parents = []
        for value in _nested(
            client, item, "parents", COMMIT_PARENTS_QUERY, "CommitParents"
        ):
            parent = _sha(value, "CommitParents")
            if parent in parents:
                raise GraphQLError(
                    "duplicate commit parent SHA", operation="CommitParents"
                )
            parents.append(parent)
        records.add(
            Activity(
                f"commit:{repository.repository_node_id}:{sha}",
                "commit",
                occurred,
                url,
                Subject(
                    "commit",
                    node_id,
                    _string(item, "messageHeadline", OPERATION, empty=True),
                    url,
                    author_key=author_key,
                ),
                tuple(sorted(credits, key=lambda c: (c.actor_key or "", c.role))),
                CommitMetadata(
                    sha,
                    _timestamp(item, OPERATION, "authoredDate"),
                    git_committer,
                    tuple(parents),
                ),
            ),
            tuple(actors),
            OPERATION,
        )
    return strategy

"""Synchronous monthly lifecycle collection; no report files are written."""

import re
from dataclasses import replace
from datetime import datetime, timezone

from .errors import GraphQLError
from .models import (
    Activity,
    Actor,
    Credit,
    IssueClosedMetadata,
    IssueOpenedMetadata,
    IssueReopenedMetadata,
    MonthlyReport,
    PRMergedMetadata,
    PROpenedMetadata,
    ReportMetadata,
    Subject,
)
from .queries import (
    ISSUE_TIMELINE_QUERY,
    ISSUES_QUERY,
    PR_TIMELINE_QUERY,
    PULL_REQUESTS_QUERY,
)
from .reports import build_report
from .repository import lookup_repository
from .transport import GraphQLClient

COVERAGE_LIMITATIONS = (
    "Lifecycle-only collection: comments, reviews, and commits are not collected; "
    "zero counts for these types do not establish absence of activity.",
    "Deleted or inaccessible items and transferred issues can limit historical "
    "reconstruction.",
    "Repository and actor names, subject titles, and URLs reflect collection-time "
    "metadata; historical display names are not reconstructed.",
    "Collection is not a transactional GitHub snapshot; concurrent changes and "
    "newly available data may revise results on a later rerun.",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    if type(year) is not int or type(month) is not int:
        raise ValueError("year and month must be integers")
    if not 1 <= year <= 9999 or not 1 <= month <= 12:
        raise ValueError("unsupported calendar month")
    try:
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("unsupported calendar month") from exc
    return start, end


def collect_month(
    owner: str, repo: str, year: int, month: int, *, token: str | None = None
) -> MonthlyReport:
    """Return a validated lifecycle-only report for one UTC calendar month.

    Comments, reviews, and commits are not yet collected. See the report's
    coverage limitations before interpreting zero counts. A failed traversal
    raises without returning a partial report. Authentication falls back to
    GH_TOKEN, then GITHUB_TOKEN. The client is always closed on exit.
    """
    if any(not isinstance(value, str) or not value.strip() for value in (owner, repo)):
        raise ValueError("owner and repo must be nonempty strings")
    start, end = _month_bounds(year, month)
    started = _utc_now()
    if start > started:
        raise ValueError("future month")
    with GraphQLClient(token) as client:
        return _collect_month(client, owner, repo, start, end, started)


def _object(value, operation: str) -> dict:
    if not isinstance(value, dict):
        raise GraphQLError("missing or malformed lifecycle object", operation=operation)
    return value


def _field(item: dict, name: str, operation: str):
    if name not in item:
        raise GraphQLError(f"missing lifecycle field: {name}", operation=operation)
    return item[name]


def _string(item: dict, name: str, operation: str, *, nullable=False, empty=False):
    value = _field(item, name, operation)
    if (value is None and nullable) or (isinstance(value, str) and (value or empty)):
        return value
    raise GraphQLError(f"malformed lifecycle field: {name}", operation=operation)


def _timestamp(item: dict, operation: str) -> datetime:
    value = _string(item, "createdAt", operation)
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError("timezone required")
        return result.astimezone(timezone.utc)
    except ValueError as exc:
        raise GraphQLError(
            "malformed lifecycle timestamp", operation=operation
        ) from exc


def _actor(value, operation: str) -> Actor | None:
    if value is None:
        return None
    item = _object(value, operation)
    kind = _string(item, "__typename", operation)
    if kind not in ("User", "Bot"):
        raise GraphQLError("unsupported lifecycle actor type", operation=operation)
    key = _string(item, "id", operation)
    database_id = _field(item, "databaseId", operation)
    if database_id is not None and (type(database_id) is not int or database_id < 1):
        raise GraphQLError("malformed actor database ID", operation=operation)
    return Actor(
        key=key,
        classification="bot" if kind == "Bot" else "human",
        github_node_id=key,
        database_id=database_id,
        login=_string(item, "login", operation, nullable=True),
        display_name=(
            _string(item, "name", operation, nullable=True, empty=True)
            if kind == "User"
            else None
        ),
        url=_string(item, "url", operation, nullable=True),
    )


def _subject(item: dict, kind: str, author: Actor | None, operation: str) -> Subject:
    number = _field(item, "number", operation)
    if type(number) is not int or number < 1:
        raise GraphQLError("malformed lifecycle subject number", operation=operation)
    return Subject(
        kind=kind,
        node_id=_string(item, "id", operation),
        title=_string(item, "title", operation, empty=True),
        url=_string(item, "url", operation),
        number=number,
        author_key=author.key if author else None,
    )


class _Records:
    def __init__(self):
        self.actors: dict[str, Actor] = {}
        self.activities: dict[str, Activity] = {}

    def add(self, activity: Activity, actors: tuple[Actor | None, ...], operation: str):
        previous = self.activities.get(activity.id)
        if previous is not None and previous != activity:
            raise GraphQLError("conflicting lifecycle event ID", operation=operation)
        self.activities[activity.id] = activity
        for actor in actors:
            if actor is None:
                continue
            previous = self.actors.get(actor.key)
            if previous is not None:
                if previous.classification != actor.classification:
                    raise GraphQLError(
                        "conflicting actor classification", operation=operation
                    )
                actor = replace(
                    previous,
                    **{
                        field: getattr(actor, field)
                        for field in ("database_id", "login", "display_name", "url")
                        if getattr(actor, field) is not None
                    },
                )
            self.actors[actor.key] = actor


def _collect_parents(client, repository_id, start, end, records, *, issues):
    operation = "RepositoryIssues" if issues else "RepositoryPullRequests"
    parents = client.iter_connection(
        ISSUES_QUERY if issues else PULL_REQUESTS_QUERY,
        {"id": repository_id},
        operation_name=operation,
        connection_path=("node", "issues" if issues else "pullRequests"),
    )
    for value in parents:
        parent = _object(value, operation)
        author = _actor(_field(parent, "author", operation), operation)
        subject = _subject(
            parent, "issue" if issues else "pull_request", author, operation
        )
        created = _timestamp(parent, operation)
        if start <= created < end:
            kind = "issue_opened" if issues else "pr_opened"
            details = (
                IssueOpenedMetadata(subject.node_id)
                if issues
                else PROpenedMetadata(subject.node_id)
            )
            records.add(
                Activity(
                    f"{kind}:{subject.node_id}",
                    kind,
                    created,
                    subject.url,
                    subject,
                    (Credit(subject.author_key, "author"),),
                    details,
                ),
                (author,),
                operation,
            )
        _collect_timeline(client, subject, author, start, end, records, issues=issues)


def _collect_timeline(client, subject, author, start, end, records, *, issues):
    operation = "IssueLifecycle" if issues else "PullRequestLifecycle"
    events = client.iter_connection(
        ISSUE_TIMELINE_QUERY if issues else PR_TIMELINE_QUERY,
        {"id": subject.node_id},
        operation_name=operation,
        connection_path=("node", "timelineItems"),
    )
    for value in events:
        event = _object(value, operation)
        event_type = _string(event, "__typename", operation)
        allowed = ("ClosedEvent", "ReopenedEvent") if issues else ("MergedEvent",)
        if event_type not in allowed:
            raise GraphQLError("unexpected lifecycle event type", operation=operation)
        occurred = _timestamp(event, operation)
        source_id = _string(event, "id", operation)
        performer = _actor(_field(event, "actor", operation), operation)
        url = None
        if event_type == "ClosedEvent":
            kind, role = "issue_closed", "closer"
            details = IssueClosedMetadata(
                source_id,
                _string(event, "stateReason", operation, nullable=True, empty=True),
            )
            url = _string(event, "url", operation)
        elif event_type == "ReopenedEvent":
            kind, role = "issue_reopened", "reopener"
            details = IssueReopenedMetadata(source_id)
        else:
            kind, role = "pr_merged", "merger"
            commit = _field(event, "commit", operation)
            sha = None
            if commit is not None:
                sha = _string(_object(commit, operation), "oid", operation)
                if not re.fullmatch(r"[0-9a-f]{40}", sha):
                    raise GraphQLError(
                        "malformed merge commit SHA", operation=operation
                    )
            details = PRMergedMetadata(source_id, sha)
            url = _string(event, "url", operation)
        if start <= occurred < end:
            records.add(
                Activity(
                    f"{kind}:{source_id}",
                    kind,
                    occurred,
                    url,
                    subject,
                    (Credit(performer.key if performer else None, role),),
                    details,
                ),
                (author, performer),
                operation,
            )


def _collect_month(client, owner, repo, start, end, started) -> MonthlyReport:
    repository = lookup_repository(client, owner, repo)
    records = _Records()
    if repository.issues_enabled:
        _collect_parents(
            client, repository.repository_node_id, start, end, records, issues=True
        )
    _collect_parents(
        client, repository.repository_node_id, start, end, records, issues=False
    )
    limitations = COVERAGE_LIMITATIONS
    if not repository.issues_enabled:
        limitations += ("Issues are disabled; the issue connection was not collected.",)
    metadata = ReportMetadata(
        repository_node_id=repository.repository_node_id,
        repository_full_name=repository.repository_full_name,
        repository_url=repository.repository_url,
        month=f"{start.year:04d}-{start.month:02d}",
        month_start=start,
        month_end=end,
        collection_started_at=started,
        collection_finished_at=_utc_now(),
        ongoing=start <= started < end,
        default_branch=repository.default_branch,
        default_branch_head=repository.default_branch_head,
        coverage_limitations=limitations,
    )
    return build_report(metadata, records.actors.values(), records.activities.values())

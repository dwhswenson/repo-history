"""Shared normalization and embedded-page activity traversal."""

import re
from dataclasses import replace
from datetime import datetime, timezone

from ._progress import emit
from .errors import GraphQLError
from .models import (
    Activity,
    Actor,
    Credit,
    IssueClosedMetadata,
    IssueCommentMetadata,
    IssueOpenedMetadata,
    IssueReopenedMetadata,
    PRConversationCommentMetadata,
    PRInlineReviewCommentMetadata,
    PRMergedMetadata,
    PROpenedMetadata,
    PRReviewMetadata,
    Subject,
)
from .pagination import iter_embedded_connection
from .queries import (
    HISTORY_PR_THREADS_QUERY,
    HISTORY_THREAD_PAGE_SIZE,
    ISSUE_COMMENTS_QUERY,
    ISSUE_TIMELINE_QUERY,
    PR_COMMENTS_QUERY,
    PR_REVIEWS_QUERY,
    PR_TIMELINE_QUERY,
    THREAD_COMMENTS_QUERY,
)
from .transport import _parent_log_context

COVERAGE_LIMITATIONS = (
    "Unlinked Git identities are estimates based on email or name; account "
    "matches and bot status are not inferred.",
    "Commit dates do not establish when commits reached the default branch. "
    "Removed commits, force-pushes, rebases, and default-branch changes can "
    "revise historical results.",
    "Deleted or inaccessible items and transferred issues can limit historical "
    "reconstruction.",
    "Repository and actor names, subject titles, and URLs reflect collection-time "
    "metadata; historical display names are not reconstructed.",
    "Review states and thread resolved/outdated flags reflect collection-time metadata.",
    "Collection is not a transactional GitHub snapshot; concurrent changes and "
    "newly available data may revise results on a later rerun.",
)


def _in_interval(occurred, start, end):
    """None bounds retain all timestamps for history-first collection."""
    return (start is None or start <= occurred) and (end is None or occurred < end)


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


def _timestamp(
    item: dict, operation: str, name="createdAt", *, nullable=False
) -> datetime | None:
    value = _string(item, name, operation, nullable=nullable)
    if value is None:
        return None
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


def _collect_parent(client, value, operation, start, end, records, *, issues):
    parent = _object(value, operation)
    author = _actor(_field(parent, "author", operation), operation)
    subject = _subject(parent, "issue" if issues else "pull_request", author, operation)
    created = _timestamp(parent, operation)
    if _in_interval(created, start, end):
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
    emit("detail", detail=f"{'Issue' if issues else 'PR'} #{subject.number}")
    with _parent_log_context("issue" if issues else "pr", subject.number):
        _collect_timeline(
            client, subject, author, start, end, records, issues=issues, initial=parent
        )
        _collect_comments(
            client, subject, author, start, end, records, issues=issues, initial=parent
        )
        if not issues:
            _collect_reviews(
                client, subject, author, start, end, records, initial=parent
            )
            _collect_threads(
                client, subject, author, start, end, records, initial=parent
            )


def _collect_timeline(client, subject, author, start, end, records, *, issues, initial):
    operation = "IssueLifecycle" if issues else "PullRequestLifecycle"
    events = _nodes(
        client,
        ISSUE_TIMELINE_QUERY if issues else PR_TIMELINE_QUERY,
        operation,
        subject.node_id,
        "timelineItems",
        initial=initial,
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
        if _in_interval(occurred, start, end):
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


def _nodes(client, query, operation, node_id, field, *, initial, page_size=100):
    return iter_embedded_connection(
        client,
        _field(initial, field, operation),
        query,
        node_id,
        field,
        operation,
        page_size=page_size,
    )


def _add_authored(
    records,
    item,
    subject,
    subject_author,
    occurred,
    kind,
    details,
    start,
    end,
    operation,
):
    source_id = _string(item, "id", operation)
    author = _actor(_field(item, "author", operation), operation)
    url = _string(item, "url", operation)
    if _in_interval(occurred, start, end):
        records.add(
            Activity(
                f"{kind}:{source_id}",
                kind,
                occurred,
                url,
                subject,
                (Credit(author.key if author else None, "author"),),
                details,
            ),
            (subject_author, author),
            operation,
        )


def _collect_comments(client, subject, author, start, end, records, *, issues, initial):
    operation = "IssueComments" if issues else "PullRequestComments"
    query = ISSUE_COMMENTS_QUERY if issues else PR_COMMENTS_QUERY
    kind = "issue_comment" if issues else "pr_conversation_comment"
    metadata = IssueCommentMetadata if issues else PRConversationCommentMetadata
    for value in _nodes(
        client, query, operation, subject.node_id, "comments", initial=initial
    ):
        item = _object(value, operation)
        occurred = _timestamp(item, operation)
        details = metadata(_string(item, "id", operation))
        _add_authored(
            records,
            item,
            subject,
            author,
            occurred,
            kind,
            details,
            start,
            end,
            operation,
        )


def _collect_reviews(client, subject, author, start, end, records, *, initial):
    operation = "PullRequestReviews"
    for value in _nodes(
        client, PR_REVIEWS_QUERY, operation, subject.node_id, "reviews", initial=initial
    ):
        item = _object(value, operation)
        source_id = _string(item, "id", operation)
        state = _string(item, "state", operation, nullable=True)
        submitted = _timestamp(item, operation, "submittedAt", nullable=True)
        if state is not None and state.upper() == "PENDING":
            if submitted is not None:
                raise GraphQLError(
                    "pending review has submission timestamp", operation=operation
                )
            continue
        if submitted is None:
            raise GraphQLError(
                "review submission cannot be established", operation=operation
            )
        _add_authored(
            records,
            item,
            subject,
            author,
            submitted,
            "pr_review",
            PRReviewMetadata(source_id, submitted, state),
            start,
            end,
            operation,
        )


def _boolean(item, name, operation):
    value = _field(item, name, operation)
    if type(value) is not bool:
        raise GraphQLError(f"malformed boolean field: {name}", operation=operation)
    return value


def _collect_threads(client, subject, author, start, end, records, *, initial):
    operation = "PullRequestReviewThreads"
    for value in _nodes(
        client,
        HISTORY_PR_THREADS_QUERY,
        operation,
        subject.node_id,
        "reviewThreads",
        initial=initial,
        page_size=HISTORY_THREAD_PAGE_SIZE,
    ):
        thread = _object(value, operation)
        thread_id = _string(thread, "id", operation)
        resolved = _boolean(thread, "isResolved", operation)
        outdated = _boolean(thread, "isOutdated", operation)
        _collect_inline_comments(
            client,
            subject,
            author,
            thread_id,
            resolved,
            outdated,
            start,
            end,
            records,
            initial=thread,
        )


def _collect_inline_comments(
    client,
    subject,
    author,
    thread_id,
    resolved,
    outdated,
    start,
    end,
    records,
    *,
    initial,
):
    operation = "ReviewThreadComments"
    for value in _nodes(
        client, THREAD_COMMENTS_QUERY, operation, thread_id, "comments", initial=initial
    ):
        item = _object(value, operation)
        source_id = _string(item, "id", operation)
        created = _timestamp(item, operation)
        published = _timestamp(item, operation, "publishedAt", nullable=True)
        state = _string(item, "state", operation)
        if state.upper() == "PENDING":
            if published is not None:
                raise GraphQLError(
                    "pending comment has publication timestamp", operation=operation
                )
            continue
        if published is None and state.upper() != "SUBMITTED":
            raise GraphQLError(
                "comment publication cannot be established", operation=operation
            )
        review = _field(item, "pullRequestReview", operation)
        review_id = (
            None
            if review is None
            else _string(_object(review, operation), "id", operation)
        )
        details = PRInlineReviewCommentMetadata(
            source_id,
            review_id,
            thread_id,
            created,
            published,
            True,
            resolved,
            outdated,
            published is None,
        )
        _add_authored(
            records,
            item,
            subject,
            author,
            published or created,
            "pr_inline_review_comment",
            details,
            start,
            end,
            operation,
        )

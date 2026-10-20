"""Activity semantics through the shared embedded-page history collector."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

import repo_history as api
from repo_history import history
from repo_history.collection import _month_bounds

from .github import (
    NOW,
    actor,
    comment,
    connection,
    event,
    inline,
    page,
    parent,
    review,
    thread,
)
from .helpers import Response, Session, errors, repository, without_run_ids


@pytest.fixture
def session(monkeypatch):
    result = Session()
    monkeypatch.setattr("requests.Session", lambda: result)
    monkeypatch.setattr(history, "_utc_now", lambda: NOW)
    return result


def source(session, *, issues=(), prs=(), tail=(), enabled=True):
    session.responses = [
        repository(hasIssuesEnabled=enabled, isEmpty=True, defaultBranchRef=None),
        *([connection("issues", issues)] if enabled else []),
        connection("pullRequests", prs),
        *tail,
    ]


def collect(month=2, year=2024):
    snapshot = api.collect_history("owner", "repo", token="test")
    key = f"{year:04d}-{month:02d}"
    return api.history_to_months(snapshot, key, key)[0]


def test_multi_month_lifecycle_history(session):
    issue = parent(
        timelineItems=page(
            [
                event("E_jan", date="2024-01-31T23:59:59Z"),
                event(stateReason="NOT_PLANNED"),
                event("E_reopen", kind="ReopenedEvent", actor=actor("U_reopener")),
                event("E_again", stateReason=None, actor=None),
                event("E_end", date="2024-03-01T00:00:00Z"),
            ]
        )
    )
    pr = parent(
        "P",
        author=actor("U_pr_author"),
        timelineItems=page(
            [event("E_merge", kind="MergedEvent", actor=actor("B_merger", kind="Bot"))]
        ),
    )
    source(
        session, issues=[issue, parent("I_new", date="2024-02-01T00:00:00Z")], prs=[pr]
    )
    snapshot = api.collect_history("o", "r", token="test")
    report = api.history_to_months(snapshot, "2024-02", "2024-02")[0]
    activities = {a.id: a for a in report.activities}
    assert set(activities) == {
        "issue_opened:I_new",
        "issue_closed:E_close",
        "issue_reopened:E_reopen",
        "issue_closed:E_again",
        "pr_merged:E_merge",
    }
    assert activities["issue_closed:E_close"].metadata.reason == "NOT_PLANNED"
    assert activities["issue_closed:E_again"].metadata.reason is None
    assert activities["issue_closed:E_again"].credits == (api.Credit(None, "closer"),)
    merge = activities["pr_merged:E_merge"]
    assert merge.metadata.merge_commit_sha == "b" * 40
    assert merge.credits == (api.Credit("B_merger", "merger"),)
    assert merge.subject.author_key == "U_pr_author"
    assert report.summaries.repository_counts["issue_closed"] == 2
    assert report.summaries.unattributed_total == 1
    january = api.history_to_months(snapshot, "2024-01", "2024-01")[0]
    assert {a.id for a in january.activities} == {
        "issue_opened:I_old",
        "issue_closed:E_jan",
        "pr_opened:P",
    }
    assert session.closed and not session.responses
    assert api.report_from_json(api.report_to_json(report)) == report
    assert len(session.calls) == 3  # All first pages are embedded.


@pytest.mark.parametrize(
    "reason", [None, "COMPLETED", "NOT_PLANNED", "DUPLICATE", "FUTURE_REASON"]
)
def test_closure_reason_is_event_metadata(session, reason):
    source(
        session,
        issues=[
            parent(
                stateReason="UNRELATED", timelineItems=page([event(stateReason=reason)])
            )
        ],
    )
    assert collect().activities[0].metadata.reason == reason


@pytest.mark.parametrize("kind", ["ClosedEvent", "ReopenedEvent", "MergedEvent"])
def test_missing_performer_and_subject_author(session, kind):
    issues = kind != "MergedEvent"
    item = parent(
        author=None,
        timelineItems=page(
            [event(kind=kind, actor=None, **({"commit": None} if not issues else {}))]
        ),
    )
    source(session, issues=[item] if issues else [], prs=[] if issues else [item])
    report = collect()
    assert not report.actors
    assert report.activities[0].subject.author_key is None
    assert report.summaries.unattributed_total == 1
    if not issues:
        assert report.activities[0].metadata.merge_commit_sha is None


def test_actor_registry_retains_metadata_across_renames(session):
    source(
        session,
        prs=[
            parent(
                date="2024-02-01T00:00:00Z",
                author=actor(login="old-login"),
                timelineItems=page(
                    [
                        event(
                            kind="MergedEvent",
                            actor=actor(login="new-login", name=None, databaseId=None),
                        )
                    ]
                ),
            )
        ],
    )
    report = collect()
    assert len(report.actors) == 1
    assert report.actors[0].login == "new-login"
    assert report.actors[0].display_name == "U_author"
    assert report.actors[0].database_id == 1


def test_projection_excludes_unreferenced_context_actors(session):
    source(
        session,
        prs=[
            parent(
                timelineItems=page(
                    [event(kind="MergedEvent", date="2024-03-01T00:00:00Z")]
                )
            )
        ],
    )
    report = collect()
    assert not report.activities and not report.actors


@pytest.mark.parametrize("enabled", [False, True])
def test_empty_month_and_disabled_issues(session, enabled):
    source(session, enabled=enabled)
    report = collect()
    assert not report.actors and not report.activities
    assert all(n == 0 for n in report.summaries.repository_counts.values())
    assert report.summaries.distinct_actors.total == 0
    assert any(
        "Issues are disabled" in text for text in report.metadata.coverage.limitations
    ) == (not enabled)


@pytest.mark.parametrize(
    "year,month",
    [
        (True, 2),
        (2024, False),
        (2024.0, 2),
        (2024, "2"),
        (0, 2),
        (2024, 13),
        (9999, 12),
        (10**100, 2),
        (2024, 10**100),
    ],
)
def test_invalid_calendar_month(year, month):
    with pytest.raises(ValueError):
        _month_bounds(year, month)


@pytest.mark.parametrize(
    "owner,repo", [("", "repo"), ("owner", " "), (None, "repo"), ("o/r", "r")]
)
def test_invalid_repository_before_network(session, owner, repo):
    with pytest.raises(ValueError):
        api.collect_history(owner, repo, token="test")
    assert not session.calls


@pytest.mark.parametrize(
    "year,month,end",
    [(2023, 2, "2023-03-01"), (2024, 2, "2024-03-01"), (2023, 12, "2024-01-01")],
)
def test_month_boundaries_and_offset_timestamps(session, year, month, end):
    start = f"{year:04d}-{month:02d}-01T00:00:00Z"
    source(
        session,
        prs=[
            parent(
                date=start,
                timelineItems=page(
                    [
                        event("E_start", kind="MergedEvent", date=start),
                        event("E_end", kind="MergedEvent", date=end + "T00:00:00Z"),
                        event(
                            "E_offset", kind="MergedEvent", date=end + "T01:00:00+01:00"
                        ),
                    ]
                ),
            )
        ],
    )
    report = collect(year=year, month=month)
    assert {a.id for a in report.activities} == {"pr_opened:I_old", "pr_merged:E_start"}
    assert report.metadata.month_end == datetime.fromisoformat(end).replace(
        tzinfo=timezone.utc
    )


def test_is_partial_is_fixed_at_collection_start(session, monkeypatch):
    times = iter([datetime(2024, 2, 29, 23, 59, tzinfo=timezone.utc), NOW])
    monkeypatch.setattr(history, "_utc_now", lambda: next(times))
    source(session)
    report = collect()
    assert report.metadata.is_partial
    assert report.metadata.provenance.last_updated_by.finished_at == NOW


def test_environment_authentication(session, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "environment-token")
    source(session)
    api.collect_history("owner", "repo")
    assert session.headers["Authorization"] == "Bearer environment-token"
    assert session.closed


@pytest.mark.parametrize(
    "change",
    [
        {"createdAt": "invalid"},
        {"createdAt": "2024-02-01T00:00:00"},
        {"actor": actor(kind="Organization")},
        {"actor": {}},
        {"actor": actor(databaseId=True)},
        {"commit": {"oid": "invalid"}},
        {"__typename": "ClosedEvent"},
        {"id": None},
    ],
)
def test_malformed_events_fail(session, change):
    source(
        session, prs=[parent(timelineItems=page([event(kind="MergedEvent", **change)]))]
    )
    with pytest.raises(api.GraphQLError):
        collect()
    assert session.closed


@pytest.mark.parametrize(
    "change",
    [
        {"number": True},
        {"number": 0},
        {"title": None},
        {"author": {}},
        {"createdAt": None},
    ],
)
def test_malformed_parents_fail(session, change):
    source(session, prs=[parent(**change)])
    with pytest.raises(api.GraphQLError):
        collect()
    assert session.closed


@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_lifecycle_events(session, conflicting):
    first = event(kind="MergedEvent")
    second = deepcopy(first)
    if conflicting:
        second["actor"] = actor("U_other")
    source(session, prs=[parent(timelineItems=page([first, second]))])
    if conflicting:
        with pytest.raises(api.GraphQLError, match="conflicting lifecycle event ID"):
            collect()
    else:
        assert collect().summaries.repository_counts["pr_merged"] == 1
    assert session.closed


def test_conflicting_actor_classification_fails(session):
    source(
        session,
        prs=[
            parent(
                date="2024-02-01T00:00:00Z",
                timelineItems=page(
                    [event(kind="MergedEvent", actor=actor(kind="Bot"))]
                ),
            )
        ],
    )
    with pytest.raises(api.GraphQLError, match="conflicting actor classification"):
        collect()


def pr_source(session, *, comments=(), reviews=(), inline_comments=()):
    source(
        session,
        prs=[
            parent(
                "P_old",
                isDraft=True,
                state="CLOSED",
                comments=page(comments),
                reviews=page(reviews),
                reviewThreads=page([thread(comments=page(inline_comments))]),
            )
        ],
    )


def test_comments_reviews_and_publication_semantics(session):
    pr_source(
        session,
        comments=[comment("C_pr"), comment("C_end", createdAt="2024-03-01T00:00:00Z")],
        reviews=[
            review(state="DISMISSED"),
            review(
                "V_pending",
                state="PENDING",
                submittedAt=None,
                author=actor("U_excluded"),
            ),
        ],
        inline_comments=[
            inline(createdAt="2024-01-31T00:00:00Z"),
            inline(
                "C_reply", pullRequestReview=None, author=actor("B_reply", kind="Bot")
            ),
            inline("C_fallback", publishedAt=None),
            inline("C_pending", publishedAt=None, state="PENDING"),
        ],
    )
    report = collect()
    by_id = {a.id: a for a in report.activities}
    assert set(by_id) == {
        "pr_discussion_comment:C_pr",
        "pr_review:V_one",
        "pr_review_comment:C_inline",
        "pr_review_comment:C_reply",
        "pr_review_comment:C_fallback",
    }
    assert by_id["pr_review:V_one"].metadata.state == "DISMISSED"
    assert by_id["pr_review_comment:C_inline"].occurred_at.day == 12
    reply = by_id["pr_review_comment:C_reply"]
    assert reply.metadata.review_node_id is None
    assert reply.metadata.resolved and reply.metadata.outdated
    fallback = by_id["pr_review_comment:C_fallback"]
    assert (
        fallback.metadata.timestamp_fallback and fallback.metadata.publication_confirmed
    )
    assert fallback.occurred_at == fallback.metadata.created_at
    assert "U_excluded" not in {a.key for a in report.actors}
    assert "U_author" not in report.summaries.actor_counts
    assert api.report_from_json(api.report_to_json(report)) == report


@pytest.mark.parametrize("kind", ["conversation", "review", "inline"])
@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_authored_events(session, kind, conflicting):
    item = {"conversation": comment, "review": review, "inline": inline}[kind]()
    duplicate = deepcopy(item)
    if conflicting:
        duplicate["author"] = actor("U_other")
    pr_source(
        session,
        **{
            {
                "conversation": "comments",
                "review": "reviews",
                "inline": "inline_comments",
            }[kind]: [item, duplicate]
        },
    )
    if conflicting:
        with pytest.raises(api.GraphQLError, match="conflicting lifecycle event ID"):
            collect()
    else:
        assert sum(collect().summaries.repository_counts.values()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"publishedAt": None, "state": "FUTURE"},
        {"publishedAt": "2024-02-12T00:00:00Z", "state": "PENDING"},
        {"publishedAt": "invalid"},
        {"createdAt": "2024-02-10T00:00:00"},
        {"author": {}},
        {"pullRequestReview": {}},
        {"state": None},
    ],
)
def test_invalid_inline_comment_fails(session, changes):
    pr_source(session, inline_comments=[inline(**changes)])
    with pytest.raises(api.GraphQLError):
        collect()
    assert session.closed


@pytest.mark.parametrize(
    "changes",
    [
        {"submittedAt": None},
        {"submittedAt": "invalid"},
        {"submittedAt": "2024-02-01T00:00:00"},
        {"state": "PENDING"},
    ],
)
def test_invalid_review_fails(session, changes):
    pr_source(session, reviews=[review(**changes)])
    with pytest.raises(api.GraphQLError):
        collect()


@pytest.mark.parametrize(
    "state", ["APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "FUTURE", None]
)
def test_submitted_review_states_and_null_author(session, state):
    pr_source(session, reviews=[review(state=state, author=None)])
    report = collect()
    assert report.activities[0].metadata.state == state
    assert report.summaries.unattributed_by_type["pr_review"] == 1


@pytest.mark.parametrize(
    "field", ["timelineItems", "comments", "reviews", "reviewThreads"]
)
def test_null_nested_nodes_fail(session, field):
    source(session, prs=[parent(**{field: page([None])})])
    with pytest.raises(api.GraphQLError):
        collect()
    assert session.closed


@pytest.mark.parametrize("kind", ["conversation", "review", "inline"])
@pytest.mark.parametrize(
    "date,kept",
    [
        ("2024-02-01T01:00:00+01:00", True),
        ("2024-03-01T01:00:00+01:00", False),
        ("2024-02-29T23:59:59Z", True),
    ],
)
def test_authored_activity_month_boundaries(session, kind, date, kept):
    pr_source(
        session,
        comments=[comment(createdAt=date)] if kind == "conversation" else [],
        reviews=[review(submittedAt=date)] if kind == "review" else [],
        inline_comments=[inline(createdAt="2024-01-01T00:00:00Z", publishedAt=date)]
        if kind == "inline"
        else [],
    )
    report = collect()
    assert len(report.activities) == int(kept)
    if not kept:
        assert not report.actors


@pytest.mark.parametrize(
    "changes", [{"isResolved": None}, {"isOutdated": 1}, {"id": None}]
)
def test_malformed_thread_fails(session, changes):
    source(session, prs=[parent(reviewThreads=page([thread(**changes)]))])
    with pytest.raises(api.GraphQLError):
        collect()


@pytest.mark.parametrize(
    "kind,field",
    [
        ("conversation", "author"),
        ("conversation", "createdAt"),
        ("review", "submittedAt"),
        ("review", "state"),
        ("inline", "publishedAt"),
        ("inline", "pullRequestReview"),
        ("lifecycle", "actor"),
    ],
)
def test_missing_required_source_field_fails(session, kind, field):
    item = {
        "conversation": comment,
        "review": review,
        "inline": inline,
        "lifecycle": event,
    }[kind]()
    del item[field]
    if kind == "lifecycle":
        source(session, issues=[parent(timelineItems=page([item]))])
    else:
        pr_source(
            session,
            **{
                {
                    "conversation": "comments",
                    "review": "reviews",
                    "inline": "inline_comments",
                }[kind]: [item]
            },
        )
    with pytest.raises(api.GraphQLError, match=field):
        collect()


def test_inline_unknown_state_with_publication_and_null_author(session):
    pr_source(session, inline_comments=[inline(state="FUTURE", author=None)])
    report = collect()
    assert report.activities[0].credits == (api.Credit(None, "author"),)
    assert report.summaries.unattributed_by_type["pr_review_comment"] == 1
    assert not report.activities[0].metadata.timestamp_fallback


def test_inline_fallback_uses_creation_month(session):
    pr_source(
        session,
        inline_comments=[inline(createdAt="2024-01-31T23:59:59Z", publishedAt=None)],
    )
    report = collect()
    assert not report.activities and not report.actors


@pytest.mark.parametrize(
    "created,published,kept",
    [
        ("2024-02-10T18:23:22Z", "2024-02-10T18:23:21Z", True),
        ("2024-03-01T00:00:00Z", "2024-02-29T23:59:59Z", True),
        ("2024-01-30T18:23:22Z", "2024-01-30T18:23:21Z", False),
    ],
)
def test_inline_publication_before_creation_preserves_source_times(
    session, created, published, kept
):
    pr_source(
        session, inline_comments=[inline(createdAt=created, publishedAt=published)]
    )
    report = collect()
    assert len(report.activities) == int(kept)
    if kept:
        activity = report.activities[0]
        assert activity.occurred_at == datetime.fromisoformat(published)
        assert activity.metadata.created_at == datetime.fromisoformat(created)
        assert activity.metadata.publication_confirmed
        assert not activity.metadata.timestamp_fallback


def test_nested_comment_page_reduction(session, monkeypatch):
    monkeypatch.setattr("repo_history.transport.time.sleep", lambda *_: None)
    item = parent(
        reviewThreads=page([thread(comments=page([inline()], more=True, cursor="tc"))])
    )
    session.responses = [
        repository(hasIssuesEnabled=False, isEmpty=True, defaultBranchRef=None),
        connection("pullRequests", [item]),
        errors("MAX_NODE_LIMIT_EXCEEDED"),
        connection("comments"),
    ]
    assert len(collect().activities) == 1
    payloads = [
        kw["json"]
        for _, kw in session.calls
        if kw["json"]["operationName"] == "ReviewThreadComments"
    ]
    assert [(p["variables"]["first"], p["variables"]["after"]) for p in payloads] == [
        (100, "tc"),
        (50, "tc"),
    ]


def test_repeat_collection_is_deterministic(session):
    source(session, issues=[parent(timelineItems=page([event()]))])
    responses = deepcopy(session.responses)
    first = collect()
    session.responses = responses
    assert without_run_ids(collect()) == without_run_ids(first)


@pytest.mark.parametrize("kind", ["lifecycle", "conversation", "review", "inline"])
@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_activity_across_continuation_pages(session, kind, conflicting):
    item = {
        "lifecycle": lambda: event(kind="MergedEvent"),
        "conversation": comment,
        "review": review,
        "inline": inline,
    }[kind]()
    duplicate = deepcopy(item)
    if conflicting:
        duplicate["actor" if kind == "lifecycle" else "author"] = actor("U_other")
    field = {
        "lifecycle": "timelineItems",
        "conversation": "comments",
        "review": "reviews",
        "inline": "comments",
    }[kind]
    first = page([item], more=True, cursor="next")
    pr = (
        parent(**{field: first})
        if kind != "inline"
        else parent(reviewThreads=page([thread(comments=first)]))
    )
    source(session, prs=[pr], tail=[connection(field, [duplicate])])
    if conflicting:
        with pytest.raises(api.GraphQLError, match="conflicting lifecycle event ID"):
            collect()
    else:
        assert len(collect().activities) == 1
    assert session.closed


@pytest.mark.parametrize(
    "tail",
    [
        errors("FORBIDDEN"),
        connection("comments", more=True, cursor="next"),
        Response({"data": {"node": None}}),
    ],
)
def test_late_inline_failure_closes_client(session, tail):
    pr = parent(
        reviewThreads=page(
            [thread(comments=page([inline()], more=True, cursor="next"))]
        )
    )
    source(session, prs=[pr], tail=[tail])
    with pytest.raises(api.GitHubAPIError):
        collect()
    assert session.closed


@pytest.mark.parametrize("inline_node", [False, True])
def test_null_parent_or_inline_node_fails(session, inline_node):
    source(
        session,
        prs=[parent(reviewThreads=page([thread(comments=page([None]))]))]
        if inline_node
        else [None],
    )
    with pytest.raises(api.GraphQLError):
        collect()
    assert session.closed

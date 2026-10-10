"""Offline lifecycle histories exercise the real client and report contract."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

import repo_history_tracking as api
from repo_history_tracking import collection

from .helpers import Response, Session, empty_commits, errors, repository


@pytest.fixture(autouse=True)
def isolate_commits(monkeypatch):
    monkeypatch.setattr("repo_history_tracking.commits.collect_commits", empty_commits)


NOW = datetime(2024, 4, 15, tzinfo=timezone.utc)


def actor(key="U_author", *, kind="User", login=None, **updates):
    result = {
        "__typename": kind,
        "id": key,
        "databaseId": 1,
        "login": login or key,
        "url": f"https://github.com/{key}",
    }
    if kind == "User":
        result["name"] = key
    result.update(updates)
    return result


def parent(key="I_old", *, date="2024-01-01T00:00:00Z", **updates):
    result = {
        "id": key,
        "number": 1,
        "title": "Current title",
        "url": f"https://github.com/owner/repo/issues/{key}",
        "createdAt": date,
        "author": actor(),
    }
    result.update(updates)
    return result


def event(key="E_close", *, kind="ClosedEvent", date="2024-02-10T00:00:00Z", **updates):
    result = {
        "__typename": kind,
        "id": key,
        "createdAt": date,
        "actor": actor("U_closer"),
    }
    if kind == "ClosedEvent":
        result.update(
            stateReason="COMPLETED", url=f"https://github.com/owner/repo/event/{key}"
        )
    if kind == "MergedEvent":
        result.update(
            commit={"oid": "b" * 40}, url=f"https://github.com/owner/repo/event/{key}"
        )
    result.update(updates)
    return result


def connection(field, nodes=(), *, cursor=None, more=False):
    return Response(
        {
            "data": {
                "node": {
                    field: {
                        "nodes": list(nodes),
                        "pageInfo": {"endCursor": cursor, "hasNextPage": more},
                    }
                }
            }
        }
    )


@pytest.fixture
def session(monkeypatch):
    result = Session()
    monkeypatch.setattr("requests.Session", lambda: result)
    monkeypatch.setattr(collection, "_utc_now", lambda: NOW)
    return result


def collect(session, responses, *, year=2024, month=2):
    session.responses = responses
    report = api.collect_month(
        "old-owner", "old-name", year, month, token="test-token", discovery="full"
    )
    assert session.closed
    assert not session.responses
    return report


def test_multi_month_history(session):
    """Old parents, repeated transitions, new openings, and every lifecycle type."""

    def history():
        return [
            repository(hasIssuesEnabled=True),
            connection("issues", [parent()], more=True, cursor="issues-1"),
            connection(
                "timelineItems",
                [
                    event("E_jan", date="2024-01-31T23:59:59Z"),
                    event("E_close", stateReason="NOT_PLANNED"),
                ],
                more=True,
                cursor="timeline-1",
            ),
            connection(
                "timelineItems",
                [
                    event(
                        "E_reopen",
                        kind="ReopenedEvent",
                        date="2024-02-11T00:00:00Z",
                        actor=actor("U_reopener"),
                    ),
                    event(
                        "E_close_again",
                        date="2024-02-12T00:00:00Z",
                        stateReason=None,
                        actor=None,
                    ),
                    event("E_march", date="2024-03-01T00:00:00Z"),
                ],
            ),
            connection("comments"),
            connection(
                "issues",
                [parent("I_new", date="2024-02-01T00:00:00Z", author=actor("U_new"))],
            ),
            connection("timelineItems"),
            connection("comments"),
            connection(
                "pullRequests",
                [parent("P_old", author=actor("U_pr_author"))],
                more=True,
                cursor="prs-1",
            ),
            connection(
                "timelineItems",
                [
                    event(
                        "E_merge",
                        kind="MergedEvent",
                        actor=actor("B_merger", kind="Bot"),
                    )
                ],
                more=True,
                cursor="merges-1",
            ),
            connection("timelineItems"),
            connection("comments"),
            connection("reviews"),
            connection("reviewThreads"),
            connection(
                "pullRequests",
                [
                    parent(
                        "P_new",
                        date="2024-02-29T23:59:59Z",
                        author=actor("U_new"),
                        isDraft=True,
                    )
                ],
            ),
            connection("timelineItems"),
            connection("comments"),
            connection("reviews"),
            connection("reviewThreads"),
        ]

    report = collect(session, history())
    assert [a.id for a in report.activities] == [
        "issue_opened:I_new",
        "issue_closed:E_close",
        "pr_merged:E_merge",
        "issue_reopened:E_reopen",
        "issue_closed:E_close_again",
        "pr_opened:P_new",
    ]
    assert [a.credits for a in report.activities] == [
        (api.Credit("U_new", "author"),),
        (api.Credit("U_closer", "closer"),),
        (api.Credit("B_merger", "merger"),),
        (api.Credit("U_reopener", "reopener"),),
        (api.Credit(None, "closer"),),
        (api.Credit("U_new", "author"),),
    ]
    assert report.activities[1].metadata.reason == "NOT_PLANNED"
    assert report.activities[4].metadata.reason is None
    assert report.activities[2].metadata.merge_commit_sha == "b" * 40
    assert report.activities[2].subject.author_key == "U_pr_author"
    assert report.activities[3].url is None
    assert report.summaries.repository_counts == {
        **dict.fromkeys(api.ACTIVITY_TYPES, 0),
        "issue_opened": 1,
        "issue_closed": 2,
        "issue_reopened": 1,
        "pr_opened": 1,
        "pr_merged": 1,
    }
    assert report.summaries.distinct_actors == api.PopulationCounts(4, 3, 1, 0)
    assert report.summaries.unattributed_total == 1
    assert report.summaries.actor_counts["U_new"]["pr_opened"] == 1
    assert "U_author" not in report.summaries.actor_counts
    assert "U_pr_author" not in report.summaries.actor_counts
    assert {a.key for a in report.actors} == {
        "U_new",
        "U_author",
        "U_closer",
        "U_reopener",
        "U_pr_author",
        "B_merger",
    }
    assert report.metadata.default_branch_head == "a" * 40
    assert report.metadata.repository_full_name == "new-owner/new-name"
    assert report.metadata.month_end == datetime(2024, 3, 1, tzinfo=timezone.utc)
    assert not report.metadata.ongoing
    assert any(
        "24-hour buffer" in text for text in report.metadata.coverage_limitations
    )
    api.validate_report(report)
    assert api.report_from_json(api.report_to_json(report)) == report

    payloads = [call[1]["json"] for call in session.calls]
    assert [(p["operationName"], p["variables"].get("after")) for p in payloads] == [
        ("RepositoryLookup", None),
        ("RepositoryIssues", None),
        ("IssueLifecycle", None),
        ("IssueLifecycle", "timeline-1"),
        ("IssueComments", None),
        ("RepositoryIssues", "issues-1"),
        ("IssueLifecycle", None),
        ("IssueComments", None),
        ("RepositoryPullRequests", None),
        ("PullRequestLifecycle", None),
        ("PullRequestLifecycle", "merges-1"),
        ("PullRequestComments", None),
        ("PullRequestReviews", None),
        ("PullRequestReviewThreads", None),
        ("RepositoryPullRequests", "prs-1"),
        ("PullRequestLifecycle", None),
        ("PullRequestComments", None),
        ("PullRequestReviews", None),
        ("PullRequestReviewThreads", None),
    ]
    assert payloads[1]["variables"] == {"id": "R_example", "first": 100, "after": None}
    assert payloads[2]["variables"]["id"] == "I_old"
    session.closed = False
    assert collect(session, history()) == report
    session.closed = False
    january = collect(session, history(), month=1)
    assert {a.id for a in january.activities} == {
        "issue_opened:I_old",
        "issue_closed:E_jan",
        "pr_opened:P_old",
    }


@pytest.mark.parametrize(
    "reason", [None, "COMPLETED", "NOT_PLANNED", "DUPLICATE", "FUTURE_REASON"]
)
def test_closure_event_reason_not_current_issue_reason(session, reason):
    report = collect(
        session,
        [
            repository(hasIssuesEnabled=True),
            connection("issues", [parent(stateReason="UNRELATED_CURRENT_REASON")]),
            connection("timelineItems", [event(stateReason=reason)]),
            connection("comments"),
            connection("pullRequests"),
        ],
    )
    assert report.activities[0].metadata.reason == reason


@pytest.mark.parametrize("kind", ["ClosedEvent", "ReopenedEvent", "MergedEvent"])
def test_missing_performer_and_subject_author(session, kind):
    issues = kind != "MergedEvent"
    responses = [repository(hasIssuesEnabled=issues)]
    responses += [
        connection("issues" if issues else "pullRequests", [parent(author=None)]),
        connection(
            "timelineItems",
            [event(kind=kind, actor=None, **({"commit": None} if not issues else {}))],
        ),
        connection("comments"),
    ]
    if issues:
        responses.append(connection("pullRequests"))
    else:
        responses.extend([connection("reviews"), connection("reviewThreads")])
    report = collect(session, responses)
    assert not report.actors
    assert report.activities[0].subject.author_key is None
    assert report.summaries.unattributed_total == 1
    if not issues:
        assert report.activities[0].metadata.merge_commit_sha is None


def test_actor_registry_renames_and_null_metadata(session):
    report = collect(
        session,
        [
            repository(),
            connection(
                "pullRequests",
                [parent(date="2024-02-01T00:00:00Z", author=actor(login="old-login"))],
            ),
            connection(
                "timelineItems",
                [
                    event(
                        kind="MergedEvent",
                        actor=actor(login="new-login", name=None, databaseId=None),
                    )
                ],
            ),
            connection("comments"),
            connection("reviews"),
            connection("reviewThreads"),
        ],
    )
    assert len(report.actors) == 1
    assert report.actors[0].login == "new-login"
    assert report.actors[0].display_name == "U_author"
    assert report.actors[0].database_id == 1
    assert report.summaries.distinct_actors.total == 1


def test_unretained_context_actors_are_not_registered(session):
    report = collect(
        session,
        [
            repository(),
            connection("pullRequests", [parent()]),
            connection(
                "timelineItems",
                [event(kind="MergedEvent", date="2024-03-01T00:00:00Z")],
            ),
            connection("comments"),
            connection("reviews"),
            connection("reviewThreads"),
        ],
    )
    assert not report.activities and not report.actors


@pytest.mark.parametrize("empty_repository", [False, True])
def test_empty_month_and_disabled_issues(session, empty_repository):
    report = collect(
        session,
        [
            repository(isEmpty=empty_repository, defaultBranchRef=None)
            if empty_repository
            else repository(),
            connection("pullRequests"),
        ],
    )
    assert not report.actors and not report.activities
    assert all(n == 0 for n in report.summaries.repository_counts.values())
    assert report.summaries.distinct_actors.total == 0
    assert report.metadata.default_branch_head == (
        None if empty_repository else "a" * 40
    )
    assert any(
        "Issues are disabled" in value for value in report.metadata.coverage_limitations
    )


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
        (2025, 1),
    ],
)
def test_invalid_month_before_network(session, year, month):
    with pytest.raises(ValueError):
        api.collect_month("owner", "repo", year, month, token="test", discovery="full")
    assert not session.calls


@pytest.mark.parametrize("owner,repo", [("", "repo"), ("owner", " "), (None, "repo")])
def test_invalid_repository_before_network(session, owner, repo):
    with pytest.raises(ValueError):
        api.collect_month(owner, repo, 2024, 2, token="test", discovery="full")
    assert not session.calls


@pytest.mark.parametrize(
    "year,month,end",
    [(2023, 2, "2023-03-01"), (2024, 2, "2024-03-01"), (2023, 12, "2024-01-01")],
)
def test_month_boundaries_and_offset_timestamps(session, year, month, end):
    start = f"{year:04d}-{month:02d}-01T00:00:00Z"
    report = collect(
        session,
        [
            repository(),
            connection("pullRequests", [parent(date=start)]),
            connection(
                "timelineItems",
                [
                    event("E_at_start", kind="MergedEvent", date=start),
                    event("E_at_end", kind="MergedEvent", date=end + "T00:00:00Z"),
                    event(
                        "E_offset_end", kind="MergedEvent", date=end + "T01:00:00+01:00"
                    ),
                ],
            ),
            connection("comments"),
            connection("reviews"),
            connection("reviewThreads"),
        ],
        year=year,
        month=month,
    )
    assert {a.id for a in report.activities} == {
        "pr_opened:I_old",
        "pr_merged:E_at_start",
    }
    assert report.metadata.month_end == datetime.fromisoformat(end).replace(
        tzinfo=timezone.utc
    )


def test_ongoing_is_fixed_at_collection_start(session, monkeypatch):
    times = iter([datetime(2024, 2, 29, 23, 59, tzinfo=timezone.utc), NOW])
    monkeypatch.setattr(collection, "_utc_now", lambda: next(times))
    report = collect(session, [repository(), connection("pullRequests")])
    assert report.metadata.ongoing
    assert report.metadata.collection_finished_at == NOW


def test_environment_authentication(session, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "environment-token")
    session.responses = [repository(), connection("pullRequests")]
    api.collect_month("owner", "repo", 2024, 2, discovery="full")
    assert session.headers["Authorization"] == "Bearer environment-token"
    assert session.closed


@pytest.mark.parametrize(
    "tail,expected",
    [
        (errors("FORBIDDEN"), api.GraphQLError),
        (connection("timelineItems", more=True, cursor="repeat"), api.PaginationError),
        (Response({"data": {"node": None}}), api.PaginationError),
    ],
)
def test_late_failure_closes_client_without_returning_report(session, tail, expected):
    session.responses = [
        repository(),
        connection("pullRequests", [parent()]),
        connection(
            "timelineItems", [event(kind="MergedEvent")], more=True, cursor="repeat"
        ),
        tail,
    ]
    with pytest.raises(expected):
        api.collect_month("owner", "repo", 2024, 2, token="test", discovery="full")
    assert session.closed


@pytest.mark.parametrize(
    "field,nodes", [("pullRequests", [None]), ("timelineItems", [None])]
)
def test_null_nodes_fail(session, field, nodes):
    session.responses = [repository()]
    if field == "timelineItems":
        session.responses.append(connection("pullRequests", [parent()]))
    session.responses.append(connection(field, nodes))
    with pytest.raises(api.GraphQLError):
        api.collect_month("owner", "repo", 2024, 2, token="test", discovery="full")
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
    session.responses = [
        repository(),
        connection("pullRequests", [parent()]),
        connection("timelineItems", [event(kind="MergedEvent", **change)]),
        connection("comments"),
        connection("reviews"),
        connection("reviewThreads"),
    ]
    with pytest.raises(api.GraphQLError):
        api.collect_month("owner", "repo", 2024, 2, token="test", discovery="full")
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
    session.responses = [repository(), connection("pullRequests", [parent(**change)])]
    with pytest.raises(api.GraphQLError):
        api.collect_month("owner", "repo", 2024, 2, token="test", discovery="full")
    assert session.closed


def test_missing_required_field_is_not_treated_as_null(session):
    merge = event(kind="MergedEvent")
    del merge["actor"]
    session.responses = [
        repository(),
        connection("pullRequests", [parent()]),
        connection("timelineItems", [merge]),
        connection("comments"),
        connection("reviews"),
        connection("reviewThreads"),
    ]
    with pytest.raises(api.GraphQLError, match="actor"):
        api.collect_month("owner", "repo", 2024, 2, token="test", discovery="full")


@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_events(session, conflicting):
    first = event(kind="MergedEvent")
    second = deepcopy(first)
    if conflicting:
        second["actor"] = actor("U_other")
    session.responses = [
        repository(),
        connection("pullRequests", [parent()]),
        connection("timelineItems", [first], more=True, cursor="next"),
        connection("timelineItems", [second]),
        connection("comments"),
        connection("reviews"),
        connection("reviewThreads"),
    ]
    if conflicting:
        with pytest.raises(api.GraphQLError, match="conflicting lifecycle event ID"):
            api.collect_month("owner", "repo", 2024, 2, token="test", discovery="full")
    else:
        report = api.collect_month(
            "owner", "repo", 2024, 2, token="test", discovery="full"
        )
        assert len(report.activities) == 1
        assert report.summaries.repository_counts["pr_merged"] == 1
    assert session.closed


@pytest.mark.parametrize("year,month", [(10**100, 2), (2024, 10**100)])
def test_out_of_range_integers_are_value_errors(session, year, month):
    with pytest.raises(ValueError, match="unsupported calendar month"):
        api.collect_month("owner", "repo", year, month, token="test", discovery="full")
    assert not session.calls


def test_early_year_month_is_zero_padded(session):
    report = collect(session, [repository(), connection("pullRequests")], year=1)
    assert report.metadata.month == "0001-02"


def test_conflicting_actor_classification_fails(session):
    session.responses = [
        repository(),
        connection("pullRequests", [parent(date="2024-02-01T00:00:00Z")]),
        connection(
            "timelineItems", [event(kind="MergedEvent", actor=actor(kind="Bot"))]
        ),
        connection("comments"),
        connection("reviews"),
        connection("reviewThreads"),
    ]
    with pytest.raises(api.GraphQLError, match="conflicting actor classification"):
        api.collect_month("owner", "repo", 2024, 2, token="test", discovery="full")
    assert session.closed


def comment(key="C_one", **updates):
    result = {
        "id": key,
        "createdAt": "2024-02-10T00:00:00Z",
        "url": f"https://github.com/owner/repo/comment/{key}",
        "author": actor("U_commenter"),
    }
    result.update(updates)
    return result


def review(key="V_one", **updates):
    result = comment(key)
    result.pop("createdAt")
    result.update(submittedAt="2024-02-11T00:00:00Z", state="COMMENTED")
    result.update(updates)
    return result


def inline(key="C_inline", **updates):
    result = comment(key)
    result.update(
        publishedAt="2024-02-12T00:00:00Z",
        state="SUBMITTED",
        pullRequestReview={"id": "V_one"},
    )
    result.update(updates)
    return result


def thread(key="T_one", **updates):
    return {"id": key, "isResolved": True, "isOutdated": True, **updates}


def pr_history(*, comments=(), reviews=(), threads=()):
    return [
        repository(),
        connection("pullRequests", [parent("P_old", isDraft=True, state="CLOSED")]),
        connection("timelineItems"),
        connection("comments", comments),
        connection("reviews", reviews),
        *threads,
    ]


def test_comments_reviews_multi_month_history(session):
    def history():
        return [
            repository(hasIssuesEnabled=True),
            connection(
                "issues",
                [
                    parent(state="CLOSED"),
                    parent("I_new", date="2024-02-01T00:00:00Z", author=actor("U_new")),
                ],
            ),
            connection(
                "timelineItems", [event(), event("E_reopen", kind="ReopenedEvent")]
            ),
            connection(
                "comments", [comment("C_issue", author=None)], more=True, cursor="ic"
            ),
            connection(
                "comments", [comment("C_before", createdAt="2024-01-31T23:59:59Z")]
            ),
            connection("timelineItems"),
            connection("comments"),
            connection(
                "pullRequests",
                [parent("P_old", state="MERGED", isDraft=True)],
                more=True,
                cursor="pr",
            ),
            connection("timelineItems", [event(kind="MergedEvent")]),
            connection("comments", [comment("C_pr")], more=True, cursor="pc"),
            connection(
                "comments", [comment("C_end", createdAt="2024-03-01T00:00:00Z")]
            ),
            connection("reviews", [review(state="DISMISSED")], more=True, cursor="rv"),
            connection(
                "reviews",
                [
                    review(
                        "V_pending",
                        state="PENDING",
                        submittedAt=None,
                        author=actor("U_excluded"),
                    )
                ],
            ),
            connection("reviewThreads", [thread()], more=True, cursor="th"),
            connection(
                "comments",
                [inline(createdAt="2024-01-31T00:00:00Z")],
                more=True,
                cursor="tc",
            ),
            connection(
                "comments",
                [
                    inline(
                        "C_reply",
                        pullRequestReview=None,
                        author=actor("B_reply", kind="Bot"),
                    )
                ],
            ),
            connection(
                "reviewThreads", [thread("T_two", isResolved=False, isOutdated=False)]
            ),
            connection(
                "comments",
                [
                    inline("C_fallback", publishedAt=None),
                    inline("C_pending", publishedAt=None, state="PENDING"),
                ],
            ),
            connection(
                "pullRequests",
                [parent("P_new", date="2024-02-01T00:00:00Z", author=actor("U_new"))],
            ),
            connection("timelineItems"),
            connection("comments"),
            connection("reviews"),
            connection("reviewThreads"),
        ]

    report = collect(session, history())
    by_id = {a.id: a for a in report.activities}
    assert set(by_id) == {
        "issue_opened:I_new",
        "issue_reopened:E_reopen",
        "issue_closed:E_close",
        "issue_comment:C_issue",
        "pr_merged:E_close",
        "pr_opened:P_new",
        "pr_conversation_comment:C_pr",
        "pr_review:V_one",
        "pr_inline_review_comment:C_inline",
        "pr_inline_review_comment:C_reply",
        "pr_inline_review_comment:C_fallback",
    }
    assert by_id["pr_review:V_one"].metadata.state == "DISMISSED"
    assert by_id["pr_inline_review_comment:C_inline"].occurred_at.day == 12
    assert by_id["pr_inline_review_comment:C_reply"].metadata.review_node_id is None
    assert by_id["pr_inline_review_comment:C_reply"].metadata.resolved
    assert by_id["pr_inline_review_comment:C_reply"].metadata.outdated
    fallback = by_id["pr_inline_review_comment:C_fallback"]
    assert (
        fallback.metadata.timestamp_fallback and fallback.metadata.publication_confirmed
    )
    assert not fallback.metadata.resolved and not fallback.metadata.outdated
    assert fallback.occurred_at == fallback.metadata.created_at
    assert report.summaries.repository_counts["pr_inline_review_comment"] == 3
    assert report.summaries.unattributed_by_type["issue_comment"] == 1
    assert "U_excluded" not in {a.key for a in report.actors}
    assert "U_author" not in report.summaries.actor_counts
    api.validate_report(report)
    assert api.report_from_json(api.report_to_json(report)) == report
    payloads = [call[1]["json"] for call in session.calls]
    continuations = [
        (p["operationName"], p["variables"]["id"], p["variables"]["after"])
        for p in payloads[1:]
        if p["variables"]["after"]
    ]
    assert continuations == [
        ("IssueComments", "I_old", "ic"),
        ("PullRequestComments", "P_old", "pc"),
        ("PullRequestReviews", "P_old", "rv"),
        ("ReviewThreadComments", "T_one", "tc"),
        ("PullRequestReviewThreads", "P_old", "th"),
        ("RepositoryPullRequests", "R_example", "pr"),
    ]
    session.closed = False
    assert collect(session, history()) == report
    session.closed = False
    january = collect(session, history(), month=1)
    assert "issue_comment:C_before" in {a.id for a in january.activities}
    assert "pr_inline_review_comment:C_inline" not in {a.id for a in january.activities}


@pytest.mark.parametrize("kind", ["conversation", "review", "inline"])
@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_authored_events(session, kind, conflicting):
    item = {"conversation": comment, "review": review, "inline": inline}[kind]()
    duplicate = deepcopy(item)
    if conflicting:
        duplicate["author"] = actor("U_other")
    history = pr_history(
        comments=[item, duplicate] if kind == "conversation" else [],
        reviews=[item, duplicate] if kind == "review" else [],
        threads=[
            connection("reviewThreads", [thread()]),
            connection("comments", [item, duplicate]),
        ]
        if kind == "inline"
        else [connection("reviewThreads")],
    )
    if conflicting:
        session.responses = history
        with pytest.raises(api.GraphQLError, match="conflicting lifecycle event ID"):
            api.collect_month("owner", "repo", 2024, 2, token="test", discovery="full")
        assert session.closed
    else:
        report = collect(session, history)
        assert len(report.activities) == 1
        assert sum(report.summaries.repository_counts.values()) == 1


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
    session.responses = pr_history(
        threads=[
            connection("reviewThreads", [thread()]),
            connection("comments", [inline(**changes)]),
        ]
    )
    with pytest.raises(api.GraphQLError):
        api.collect_month("owner", "repo", 2024, 2, token="test", discovery="full")
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
    session.responses = pr_history(reviews=[review(**changes)])
    with pytest.raises(api.GraphQLError):
        api.collect_month("owner", "repo", 2024, 2, token="test", discovery="full")
    assert session.closed


@pytest.mark.parametrize(
    "state", ["APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "FUTURE", None]
)
def test_submitted_review_states_and_null_author(session, state):
    report = collect(
        session,
        pr_history(
            reviews=[review(state=state, author=None)],
            threads=[connection("reviewThreads")],
        ),
    )
    assert report.activities[0].metadata.state == state
    assert report.summaries.unattributed_by_type["pr_review"] == 1


@pytest.mark.parametrize("field", ["reviewThreads", "comments", "reviews"])
def test_null_nested_nodes_fail(session, field):
    history = pr_history(threads=[connection("reviewThreads")])
    index = {"comments": 3, "reviews": 4, "reviewThreads": 5}[field]
    history[index] = connection(field, [None])
    session.responses = history
    with pytest.raises(api.GraphQLError):
        api.collect_month("owner", "repo", 2024, 2, token="test", discovery="full")
    assert session.closed


@pytest.mark.parametrize(
    "tail", [errors("FORBIDDEN"), connection("comments", more=True, cursor="repeat")]
)
def test_late_thread_comment_failure(session, tail):
    session.responses = pr_history(
        threads=[
            connection("reviewThreads", [thread()]),
            connection("comments", [inline()], more=True, cursor="repeat"),
            tail,
        ]
    )
    with pytest.raises(api.GitHubAPIError):
        api.collect_month("owner", "repo", 2024, 2, token="test", discovery="full")
    assert session.closed


def test_nested_comment_page_reduction(session, monkeypatch):
    monkeypatch.setattr(
        "repo_history_tracking.transport.time.sleep", lambda *args: None
    )
    report = collect(
        session,
        pr_history(
            threads=[
                connection("reviewThreads", [thread()]),
                errors("MAX_NODE_LIMIT_EXCEEDED"),
                connection("comments", [inline()], more=True, cursor="tc"),
                connection("comments"),
            ]
        ),
    )
    assert len(report.activities) == 1
    payloads = [
        call[1]["json"]
        for call in session.calls
        if call[1]["json"]["operationName"] == "ReviewThreadComments"
    ]
    assert [(p["variables"]["first"], p["variables"]["after"]) for p in payloads] == [
        (100, None),
        (50, None),
        (50, "tc"),
    ]


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
    report = collect(
        session,
        pr_history(
            comments=[comment(createdAt=date)] if kind == "conversation" else [],
            reviews=[review(submittedAt=date)] if kind == "review" else [],
            threads=[
                connection("reviewThreads", [thread()]),
                connection(
                    "comments",
                    [inline(createdAt="2024-01-01T00:00:00Z", publishedAt=date)],
                ),
            ]
            if kind == "inline"
            else [connection("reviewThreads")],
        ),
    )
    assert len(report.activities) == int(kept)
    if not kept:
        assert not report.actors


@pytest.mark.parametrize(
    "changes", [{"isResolved": None}, {"isOutdated": 1}, {"id": None}]
)
def test_malformed_thread_fails(session, changes):
    session.responses = pr_history(
        threads=[connection("reviewThreads", [thread(**changes)])]
    )
    with pytest.raises(api.GraphQLError):
        api.collect_month("owner", "repo", 2024, 2, token="test", discovery="full")
    assert session.closed


@pytest.mark.parametrize(
    "kind,field",
    [
        ("conversation", "author"),
        ("conversation", "createdAt"),
        ("review", "submittedAt"),
        ("review", "state"),
        ("inline", "publishedAt"),
        ("inline", "pullRequestReview"),
    ],
)
def test_missing_authored_source_field_fails(session, kind, field):
    item = {"conversation": comment, "review": review, "inline": inline}[kind]()
    del item[field]
    session.responses = pr_history(
        comments=[item] if kind == "conversation" else [],
        reviews=[item] if kind == "review" else [],
        threads=[
            connection("reviewThreads", [thread()]),
            connection("comments", [item]),
        ]
        if kind == "inline"
        else [connection("reviewThreads")],
    )
    with pytest.raises(api.GraphQLError, match=field):
        api.collect_month("owner", "repo", 2024, 2, token="test", discovery="full")
    assert session.closed


def test_inline_unknown_state_with_publication_and_null_author(session):
    report = collect(
        session,
        pr_history(
            threads=[
                connection("reviewThreads", [thread()]),
                connection("comments", [inline(state="FUTURE", author=None)]),
            ]
        ),
    )
    assert report.activities[0].credits == (api.Credit(None, "author"),)
    assert report.summaries.unattributed_by_type["pr_inline_review_comment"] == 1
    assert not report.activities[0].metadata.timestamp_fallback


def test_inline_fallback_uses_creation_month(session):
    report = collect(
        session,
        pr_history(
            threads=[
                connection("reviewThreads", [thread()]),
                connection(
                    "comments",
                    [inline(createdAt="2024-01-31T23:59:59Z", publishedAt=None)],
                ),
            ]
        ),
    )
    assert not report.activities and not report.actors


def test_duplicate_inline_comments_across_pages(session):
    item = inline()
    report = collect(
        session,
        pr_history(
            threads=[
                connection("reviewThreads", [thread()]),
                connection("comments", [item], more=True, cursor="tc"),
                connection("comments", [deepcopy(item)]),
            ]
        ),
    )
    assert report.summaries.repository_counts["pr_inline_review_comment"] == 1


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
    # GitHub comment 2747458489 exhibits this one-second inversion.
    report = collect(
        session,
        pr_history(
            threads=[
                connection("reviewThreads", [thread()]),
                connection(
                    "comments", [inline(createdAt=created, publishedAt=published)]
                ),
            ]
        ),
    )
    assert len(report.activities) == int(kept)
    if kept:
        activity = report.activities[0]
        assert activity.occurred_at == datetime.fromisoformat(published)
        assert activity.metadata.published_at == datetime.fromisoformat(published)
        assert activity.metadata.created_at == datetime.fromisoformat(created)
        assert activity.metadata.publication_confirmed
        assert not activity.metadata.timestamp_fallback
        assert api.report_from_json(api.report_to_json(report)) == report
    else:
        assert not report.actors

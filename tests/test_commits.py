"""Offline commit collection through the real transport and report builder."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

import repo_history as api
from repo_history import history as collection
from repo_history.commits import history_bounds
from repo_history.identities import git_identity_key

from .github import (
    NOW,
    actor,
    collect_update_report,
    commit,
    connection,
    history,
    hydrated,
    identity,
    page,
    parent,
    rest_parent,
)
from .helpers import Response, Session, errors, replace, repository


@pytest.fixture
def session(monkeypatch):
    class CommitSession(Session):
        def __init__(self):
            super().__init__()
            self.canonical = {
                key: actor(key) for key in ("U_author", "U_committer", "U_co")
            }

        def post(self, endpoint, **kwargs):
            payload = kwargs.get("json", {})
            if payload.get("operationName") == "CommitActor":
                self.calls.append((endpoint, deepcopy(kwargs)))
                value = self.canonical[payload["variables"]["id"]]
                if isinstance(value, Response):
                    return value
                return Response({"data": {"node": value, "rateLimit": {"cost": 2}}})
            return super().post(endpoint, **kwargs)

    result = CommitSession()
    monkeypatch.setattr("requests.Session", lambda: result)
    monkeypatch.setattr(collection, "_utc_now", lambda: NOW)
    return result


def collect(session, responses, *, year=2024, month=2):
    session.responses = [
        repository(),
        connection("pullRequests") if (year, month) == (1, 1) else Response([]),
        *responses,
    ]
    result = collect_update_report("old-owner", "old-name", year, month, token="test")
    assert session.closed and not session.responses
    return result


def payloads(session, operation="CommitHistory"):
    return [
        kwargs["json"]
        for _, kwargs in session.calls
        if kwargs.get("json", {}).get("operationName") == operation
    ]


def test_update_uses_buffered_history(session):
    report = collect(session, [history([commit()])])
    request = payloads(session)[0]
    assert request["variables"] == {
        "id": "R_example",
        "head": "a" * 40,
        "first": 100,
        "after": None,
        "since": "2024-01-31T00:00:00+00:00",
        "until": "2024-03-02T00:00:00+00:00",
    }
    assert report.metadata.provenance.last_updated_by.commit_history == "bounded"
    assert report.activities[0].occurred_at.month == 2
    assert report.activities[0].metadata.authored_at.month == 1
    assert report.summaries.repository_counts["commit"] == 1
    assert report.summaries.actor_counts["U_author"]["commit"] == 1
    assert "U_committer" not in report.summaries.actor_counts
    assert any(
        "24-hour buffer" in text for text in report.metadata.coverage.limitations
    )
    assert api.report_from_json(api.report_to_json(report)) == report


def test_pagination_crosses_old_pages_and_deduplicates_merges(session):
    merged = commit(parents=page([{"oid": "c" * 40}, {"oid": "d" * 40}]))
    report = collect(
        session,
        [
            history(
                [commit("e", date="2024-01-31T23:59:59Z")], more=True, cursor="old"
            ),
            history([merged, deepcopy(merged)]),
        ],
    )
    assert len(report.activities) == 1
    assert report.activities[0].metadata.parent_shas == ("c" * 40, "d" * 40)
    requests = payloads(session)
    assert requests[1]["variables"]["after"] == "old"
    assert all(p["variables"]["head"] == "a" * 40 for p in requests)


def test_exact_boundaries_and_no_nested_requests_for_excluded_commits(session):
    dates = [
        "2024-01-31T23:59:59Z",
        "2024-02-01T00:00:00Z",
        "2024-02-29T23:59:59Z",
        "2024-03-01T00:00:00Z",
    ]
    commits = [commit(sha, date=date) for sha, date in zip("bcde", dates)]
    for item in (commits[0], commits[-1]):
        item["authors"] = page([], more=True, cursor="unused")
        item["parents"] = page([], more=True, cursor="unused")
    report = collect(session, [history(commits)])
    assert [a.metadata.sha for a in report.activities] == ["c" * 40, "d" * 40]
    assert not payloads(session, "CommitAuthors")
    assert not payloads(session, "CommitParents")


def test_nested_pagination_and_distinct_author_credits(session):
    primary = identity(user=actor())
    coauthor = identity("Coauthor", "co@example.org")
    value = commit(
        authors=page([primary, coauthor], more=True, cursor="authors-1"),
        parents=page([{"oid": "c" * 40}], more=True, cursor="parents-1"),
    )
    report = collect(
        session,
        [
            history([value]),
            connection("authors", [primary, coauthor, identity(user=actor("U_co"))]),
            connection("parents", [{"oid": "d" * 40}]),
        ],
    )
    activity = report.activities[0]
    assert {(c.actor_key, c.role) for c in activity.credits} == {
        ("U_author", "author"),
        (git_identity_key("co@example.org", "Coauthor"), "co_author"),
        ("U_co", "co_author"),
    }
    assert report.summaries.distinct_actors.total == 3
    assert all(v["commit"] == 1 for v in report.summaries.actor_counts.values())
    for operation, cursor in [
        ("CommitAuthors", "authors-1"),
        ("CommitParents", "parents-1"),
    ]:
        assert payloads(session, operation)[0]["variables"] == {
            "id": "C_b",
            "first": 10,
            "after": cursor,
        }


@pytest.mark.parametrize(
    "name,email", [("Name", None), (None, "email@example.org"), (None, None)]
)
def test_unresolved_and_missing_authors(session, name, email):
    author = identity(name, email)
    report = collect(
        session,
        [history([commit(author=author, authors=page([author]), committer=None)])],
    )
    key = git_identity_key(email, name)
    assert report.activities[0].subject.author_key == key
    assert report.activities[0].metadata.committer.actor_key is None
    assert report.summaries.unattributed_by_type["commit"] == (key is None)
    if key:
        assert report.actors[0].classification == "unresolved_git"


def test_null_primary_and_bot_looking_unlinked_identity(session):
    co = identity("robot[bot]", "robot@users.noreply.github.com")
    report = collect(
        session, [history([commit(author=None, authors=page([co]), committer=None)])]
    )
    assert report.activities[0].subject.author_key is None
    assert report.actors[0].classification == "unresolved_git"
    assert report.summaries.distinct_actors.bot == 0


def test_empty_repository_skips_history(session):
    session.responses = [
        repository(isEmpty=True, defaultBranchRef=None),
        Response([]),
    ]
    report = collect_update_report("o", "r", 2024, 2, token="test")
    assert not payloads(session)
    assert report.summaries.repository_counts["commit"] == 0
    assert report.metadata.provenance.last_updated_by.commit_history == "bounded"


@pytest.mark.parametrize("year,month", [(1, 1), (1969, 12), (1970, 1)])
def test_unsupported_dates_select_unbounded_history(session, year, month):
    report = collect(session, [history()], year=year, month=month)
    variables = payloads(session)[0]["variables"]
    assert "since" not in variables and "until" not in variables
    assert report.metadata.provenance.last_updated_by.commit_history == "full"
    assert not any(
        "24-hour buffer" in text for text in report.metadata.coverage.limitations
    )


def test_date_range_edges_and_overflow():
    def bounds(year, month, day=1):
        return datetime(year, month, day, tzinfo=timezone.utc)

    assert history_bounds(bounds(1970, 2), bounds(1970, 3))[0] == "bounded"
    assert history_bounds(bounds(2099, 12), bounds(2100, 1))[0] == "full"
    assert history_bounds(bounds(9999, 12), bounds(9999, 12, 31))[0] == "full"


def test_unbounded_fallback_traverses_old_pages_to_matching_ancestors(session):
    report = collect(
        session,
        [
            history(
                [commit("b", date="1969-12-31T23:59:59Z")], more=True, cursor="old"
            ),
            history([commit("c", date="1970-01-01T00:39:57Z")]),
        ],
        year=1970,
        month=1,
    )
    assert [a.metadata.sha for a in report.activities] == ["c" * 40]
    assert all("since" not in p["variables"] for p in payloads(session))


@pytest.mark.parametrize("field", ["authors", "parents"])
def test_null_nested_continuation_node_is_incomplete(session, field):
    value = commit(**{field: page([], more=True, cursor="next")})
    with pytest.raises(api.GraphQLError):
        collect(session, [history([value]), connection(field, [None])])


@pytest.mark.parametrize("more,cursor", [(True, None), (True, "same")])
def test_missing_or_repeated_history_cursor_fails(session, more, cursor):
    responses = [history([], more=more, cursor=cursor)]
    if cursor:
        responses.append(history([], more=True, cursor=cursor))
    with pytest.raises(api.PaginationError):
        collect(session, responses)
    assert session.closed


@pytest.mark.parametrize(
    "field,operation", [("authors", "CommitAuthors"), ("parents", "CommitParents")]
)
def test_nested_repeated_cursor_fails(session, field, operation):
    value = commit(**{field: page([], more=True, cursor="same")})
    with pytest.raises(api.PaginationError):
        collect(
            session, [history([value]), connection(field, [], more=True, cursor="same")]
        )
    assert payloads(session, operation)


@pytest.mark.parametrize(
    "updates",
    [
        {"oid": "invalid"},
        {"committedDate": "no-date"},
        {"authoredDate": None},
        {"id": None},
        {"authors": None},
        {"parents": page([{"oid": "invalid"}])},
        {"author": {"name": "x"}},
        {"committer": {"name": "x"}},
        {"parents": page([{"oid": "a" * 40}, {"oid": "a" * 40}])},
        {"authors": page([], more=True)},
    ],
)
def test_malformed_retained_commits_fail(session, updates):
    with pytest.raises((api.GraphQLError, api.PaginationError)):
        collect(session, [history([commit(**updates)])])


def test_conflicting_duplicate_commit_fails(session):
    with pytest.raises(api.GraphQLError, match="conflicting"):
        collect(session, [history([commit(), commit(messageHeadline="Changed")])])


@pytest.mark.parametrize(
    "response",
    [
        errors("FORBIDDEN"),
        Response({"data": {"node": {"object": None}}}),
        history([None]),
    ],
)
def test_unavailable_or_incomplete_history_fails(session, response):
    with pytest.raises((api.GraphQLError, api.PaginationError)):
        collect(session, [response])
    assert session.closed


def test_small_inversion_is_recovered_and_large_inversion_is_disclosed(session):
    # A buffered API response can contain older descendants before a matching ancestor.
    result = collect(
        session,
        [
            history(
                [
                    commit("b", date="2024-01-31T23:56:05Z"),
                    commit("c", date="2024-02-01T00:39:57Z"),
                ]
            )
        ],
    )
    assert [a.metadata.sha for a in result.activities] == ["c" * 40]
    # GitHub may prune before an in-month ancestor when an intermediate date
    # predates January 31. The API returns no evidence of that hidden ancestor.
    session.calls.clear()
    result = collect(session, [history()])
    assert result.activities == ()
    assert any(
        "hide matching ancestors" in text
        for text in result.metadata.coverage.limitations
    )


def test_combined_pr_and_commit_counts_and_api_cost(session, caplog):
    caplog.set_level("INFO", logger="repo_history.history")
    responses = [
        repository(),
        Response([rest_parent(pr=True)]),
        hydrated(parent(date="2024-02-02T00:00:00Z", __typename="PullRequest")),
        history([commit()]),
    ]
    for response in responses:
        if not isinstance(response.body, dict):
            continue
        response.body["data"]["rateLimit"] = {"cost": 2}
    session.responses = responses
    report = collect_update_report("o", "r", 2024, 2, token="test")
    assert report.summaries.repository_counts["pr_opened"] == 1
    assert report.summaries.repository_counts["commit"] == 1
    assert report.summaries.distinct_actors.total == 1
    assert "GraphQL requests=4 retries=0 points=8 unmeasured_attempts=0" in caplog.text


@pytest.mark.parametrize("strategy", ["unknown", True, 0])
def test_invalid_provenance_rejected(mixed, strategy):
    report = replace(mixed, metadata=replace(mixed.metadata, commit_history=strategy))
    with pytest.raises(api.ReportValidationError):
        api.validate_report(report)


def test_commit_queries_are_anchored_and_only_fetch_headlines():
    from repo_history.queries import COMMIT_HISTORY_QUERY

    assert "object(oid: $head)" in COMMIT_HISTORY_QUERY
    assert "defaultBranchRef" not in COMMIT_HISTORY_QUERY
    assert "messageHeadline" in COMMIT_HISTORY_QUERY
    assert "messageBody" not in COMMIT_HISTORY_QUERY


@pytest.mark.parametrize("seen_in_pr", [False, True])
def test_commit_user_projection_of_bot_preserves_canonical_identity(
    session, seen_in_pr
):
    key = "BOT_kgDOCnlnWA"
    projected = identity(user=actor(key, login="Copilot"))
    canonical = actor(key, kind="Bot", login="copilot-pull-request-reviewer")
    session.canonical[key] = canonical
    value = commit(author=projected, authors=page([projected]), committer=projected)
    pr_responses = (
        [
            Response([rest_parent(pr=True)]),
            hydrated(
                parent(
                    author=canonical,
                    date="2024-02-02T00:00:00Z",
                    __typename="PullRequest",
                )
            ),
        ]
        if seen_in_pr
        else [Response([])]
    )
    session.responses = [repository(), *pr_responses, history([value, deepcopy(value)])]
    report = collect_update_report("o", "r", 2024, 2, token="test")
    registered = next(a for a in report.actors if a.key == key)
    assert registered.classification == "bot"
    assert registered.login == "copilot-pull-request-reviewer"
    assert report.summaries.distinct_actors.bot == 1
    assert report.summaries.distinct_actors.human == 0
    assert report.summaries.actor_counts[key]["commit"] == 1
    assert len(payloads(session, "CommitActor")) == (0 if seen_in_pr else 1)
    assert api.report_from_json(api.report_to_json(report)) == report


@pytest.mark.parametrize(
    "canonical",
    [
        None,
        actor("wrong"),
        actor(kind="Organization"),
        Response({"data": {}}),
        errors("FORBIDDEN"),
    ],
)
def test_missing_or_failed_canonical_actor_resolution_fails(session, canonical):
    session.canonical["U_author"] = canonical
    with pytest.raises(api.GitHubAPIError):
        collect(session, [history([commit()])])
    assert session.closed


def test_embedded_commit_connections_log_first_page_and_continuations(session, caplog):
    caplog.set_level("DEBUG", logger="repo_history.transport")
    item = commit(
        authors=page([], more=True, cursor="authors"),
        parents=page([], more=True, cursor="parents"),
    )
    collect(
        session,
        [
            history([item]),
            connection("authors", [identity()]),
            connection("parents", [{"oid": "c" * 40}]),
        ],
    )
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "operation=CommitAuthors page=1 source=embedded" in message
        for message in messages
    )
    assert any(
        "operation=CommitAuthors page=2 attempt=1 status=200" in message
        for message in messages
    )
    assert any(
        "operation=CommitParents page=1 source=embedded" in message
        for message in messages
    )
    assert any(
        "operation=CommitParents page=2 attempt=1 status=200" in message
        for message in messages
    )
    assert any(
        "operation=CommitHistory page=1 attempt=1" in message for message in messages
    )
    assert all(
        "page=" not in message
        for message in messages
        if "operation=CommitActor " in message
    )

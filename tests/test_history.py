"""History-first collection, offline projection, and backfill acceptance."""

import importlib
import logging
from collections import Counter
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from time import perf_counter

import pytest

import repo_history as api
from repo_history import cli, collection
from repo_history import history as module

from .helpers import Response, Session, errors, repository
from .test_collection import (
    actor,
    comment,
    connection,
    event,
    inline,
    parent,
    review,
    thread,
)
from .test_commits import commit, history, identity, page

backfill_module = importlib.import_module("repo_history.backfill")
NOW = datetime(2024, 4, 15, tzinfo=timezone.utc)


class HistorySession(Session):
    """Replay a fixed history by connection/cursor, independently of month."""

    def __init__(self):
        super().__init__()
        self.pages = {}
        self.failures = {}
        self.prune_commits = False

    def post(self, endpoint, **kwargs):
        self.calls.append((endpoint, deepcopy(kwargs)))
        payload = kwargs["json"]
        operation, values = payload["operationName"], payload["variables"]
        key = (operation, values.get("id"), values.get("after"))
        if key in self.failures:
            return deepcopy(self.failures[key])
        if operation == "RepositoryLookup":
            result = deepcopy(self.repository)
        elif operation == "CommitActor":
            result = Response({"data": {"node": actor(values["id"])}})
        elif operation == "CommitHistory" and self.prune_commits and "since" in values:
            result = history()
        else:
            result = deepcopy(self.pages[key])
        if "fragment HistoryIssue on Issue" in payload["query"]:
            self.embed_parents(result, "issues", issues=True)
        if "fragment HistoryPullRequest on PullRequest" in payload["query"]:
            self.embed_parents(result, "pullRequests", issues=False)
        if (
            operation == "PullRequestReviewThreads"
            and "comments(first:" in payload["query"]
        ):
            self.embed_threads(result.body["data"]["node"]["reviewThreads"])
        result.body.setdefault("data", {})["rateLimit"] = {"cost": 1}
        return result

    def embed_parents(self, response, field, *, issues):
        for item in response.body["data"]["node"][field]["nodes"]:
            connections = {
                "timelineItems": "IssueLifecycle" if issues else "PullRequestLifecycle",
                "comments": "IssueComments" if issues else "PullRequestComments",
            }
            if not issues:
                connections.update(
                    reviews="PullRequestReviews",
                    reviewThreads="PullRequestReviewThreads",
                )
            for name, operation in connections.items():
                item[name] = deepcopy(
                    self.pages[(operation, item["id"], None)].body["data"]["node"][name]
                )
            if not issues:
                self.embed_threads(item["reviewThreads"])

    def embed_threads(self, connection):
        for item in connection["nodes"]:
            item["comments"] = deepcopy(
                self.pages[("ReviewThreadComments", item["id"], None)].body["data"][
                    "node"
                ]["comments"]
            )

    def add(self, operation, node_id, response, after=None):
        self.pages[(operation, node_id, after)] = response


@pytest.fixture
def session(monkeypatch):
    result = HistorySession()
    result.repository = repository(
        createdAt="2024-01-01T00:00:00Z", hasIssuesEnabled=True
    )
    result.add(
        "RepositoryIssues",
        "R_example",
        connection("issues", [parent("I")], more=True, cursor="i"),
    )
    result.add("RepositoryIssues", "R_example", connection("issues"), after="i")
    result.add(
        "IssueLifecycle",
        "I",
        connection(
            "timelineItems", [event(date="2024-02-01T00:00:00Z")], more=True, cursor="e"
        ),
    )
    result.add(
        "IssueLifecycle",
        "I",
        connection(
            "timelineItems",
            [event("E_reopen", kind="ReopenedEvent", date="2024-02-29T23:59:59Z")],
        ),
        after="e",
    )
    result.add(
        "IssueComments",
        "I",
        connection(
            "comments",
            [comment(createdAt="2024-03-01T01:00:00+01:00")],
            more=True,
            cursor="c",
        ),
    )
    result.add("IssueComments", "I", connection("comments"), after="c")
    result.add(
        "RepositoryPullRequests",
        "R_example",
        connection(
            "pullRequests",
            [parent("P", date="2024-02-01T00:00:00Z")],
            more=True,
            cursor="p",
        ),
    )
    result.add(
        "RepositoryPullRequests", "R_example", connection("pullRequests"), after="p"
    )
    result.add(
        "PullRequestLifecycle",
        "P",
        connection("timelineItems", [event("E_merge", kind="MergedEvent")]),
    )
    result.add("PullRequestComments", "P", connection("comments", [comment("C_pr")]))
    result.add(
        "PullRequestReviews",
        "P",
        connection(
            "reviews",
            [review(), review("V_pending", state="PENDING", submittedAt=None)],
            more=True,
            cursor="v",
        ),
    )
    result.add(
        "PullRequestReviews",
        "P",
        connection("reviews", [review("V_march", submittedAt="2024-03-01T00:00:00Z")]),
        after="v",
    )
    result.add(
        "PullRequestReviewThreads",
        "P",
        connection("reviewThreads", [thread()], more=True, cursor="t"),
    )
    result.add("PullRequestReviewThreads", "P", connection("reviewThreads"), after="t")
    result.add(
        "ReviewThreadComments",
        "T_one",
        connection(
            "comments",
            [
                inline(createdAt="2024-01-01T00:00:00Z"),
                inline("C_pending", state="PENDING", publishedAt=None),
            ],
            more=True,
            cursor="tc",
        ),
    )
    result.add(
        "ReviewThreadComments",
        "T_one",
        connection("comments", [inline("C_fallback", publishedAt=None)]),
        after="tc",
    )
    # Author and parent overflow connections are independently paginated too.
    imported = commit(
        "b", date="2023-12-01T00:00:00Z", authors=page([], more=True, cursor="a")
    )
    imported["parents"] = page([], more=True, cursor="cp")
    result.add("CommitHistory", "R_example", history([imported], more=True, cursor="h"))
    result.add("CommitHistory", "R_example", history([commit("c")]), after="h")
    result.add(
        "CommitAuthors",
        "C_b",
        connection("authors", [identity(user=actor("U_co"))]),
        after="a",
    )
    result.add(
        "CommitParents", "C_b", connection("parents", [{"oid": "d" * 40}]), after="cp"
    )
    monkeypatch.setattr("requests.Session", lambda: result)
    monkeypatch.setattr(module, "_utc_now", lambda: NOW)
    monkeypatch.setattr(collection, "_utc_now", lambda: NOW)
    monkeypatch.setattr(backfill_module, "_utc_now", lambda: NOW)
    monkeypatch.setattr("repo_history.transport.time.sleep", lambda _: None)
    return result


def collect():
    return api.collect_history("o", "r", token="test")


def requests(session):
    return [kwargs["json"] for _, kwargs in session.calls]


def test_snapshot_and_offline_months(session):
    snapshot = collect()
    assert session.closed
    assert isinstance(snapshot, api.RepositoryHistory)
    with pytest.raises(FrozenInstanceError):
        snapshot.default_branch = "other"
    calls = len(session.calls)
    reports = api.history_to_months(snapshot)
    assert [r.metadata.month for r in reports] == [
        "2023-12",
        "2024-01",
        "2024-02",
        "2024-03",
        "2024-04",
    ]
    assert {a.type for a in snapshot.activities} == set(api.ACTIVITY_TYPES)
    assert not any("pending" in a.id for a in snapshot.activities)
    assert reports[-1].activities == reports[-1].actors == ()
    assert reports[-1].metadata.ongoing
    assert reports[2].metadata.month_end - reports[2].metadata.month_start == timedelta(
        days=29
    )
    for report in reports:
        assert (
            report.metadata.parent_discovery == report.metadata.commit_history == "full"
        )
        assert (
            report.metadata.collection_started_at
            == report.metadata.collection_finished_at
            == NOW
        )
        assert not any(
            "24-hour" in text or "Experimental" in text
            for text in report.metadata.coverage_limitations
        )
        assert api.report_from_json(api.report_to_json(report)) == report
    assert "U_committer" in {a.key for a in reports[0].actors}
    assert "U_co" not in {a.key for a in reports[2].actors}
    inline_event = next(a for a in reports[2].activities if a.id.endswith(":C_inline"))
    assert (
        inline_event.occurred_at.month == 2
        and inline_event.metadata.created_at.month == 1
    )
    fallback = next(a for a in reports[2].activities if a.id.endswith(":C_fallback"))
    assert fallback.metadata.timestamp_fallback
    assert api.history_to_months(snapshot, "2024-02", "2024-02") == reports[2:3]
    assert len(session.calls) == calls
    keys = [
        (p["operationName"], p["variables"].get("id"), p["variables"].get("after"))
        for p in requests(session)
    ]
    assert max(Counter(keys).values()) == 1
    assert sum(p["operationName"] == "RepositoryLookup" for p in requests(session)) == 1
    assert all(
        "since" not in p["variables"] and "until" not in p["variables"]
        for p in requests(session)
        if p["operationName"] == "CommitHistory"
    )


def test_monthly_equivalence_and_request_measurement(session, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="repo_history")
    timer = perf_counter()
    paths = api.backfill(
        "o",
        "r",
        "2024-01",
        "2024-03",
        output_directory=tmp_path / "history",
        token="test",
    )
    history_seconds = perf_counter() - timer
    grouped = [api.report_from_json(p.read_text()) for p in paths]
    history_requests = len(session.calls)
    history_cost = sum(int("points=" in r.message) for r in caplog.records)
    assert history_cost == 1
    assert (
        f"GraphQL requests={history_requests} retries=0 points={history_requests}"
        in caplog.text
    )
    session.calls.clear()
    timer = perf_counter()
    paths = api.backfill(
        "o",
        "r",
        "2024-01",
        "2024-03",
        mode="monthly",
        discovery="full",
        output_directory=tmp_path / "monthly",
        token="test",
    )
    monthly_seconds = perf_counter() - timer
    monthly = [api.report_from_json(p.read_text()) for p in paths]
    for actual, expected in zip(grouped, monthly, strict=True):
        assert actual.activities == expected.activities
        assert actual.actors == expected.actors
        assert actual.summaries == expected.summaries
    assert history_requests < len(session.calls)
    print(
        f"fixture history: {history_requests} requests/points, {history_seconds:.3f}s; monthly: {len(session.calls)} requests/points, {monthly_seconds:.3f}s"
    )


def test_unfiltered_history_recovers_commit_hidden_by_date_pruning(session):
    session.prune_commits = True
    monthly = api.collect_month("o", "r", 2024, 2, token="test", discovery="full")
    assert not any(a.type == "commit" for a in monthly.activities)
    full = api.history_to_months(collect(), "2024-02", "2024-02")[0]
    assert [a.metadata.sha for a in full.activities if a.type == "commit"] == ["c" * 40]


@pytest.mark.parametrize("enabled", [True, False])
def test_empty_repository_and_creation_month(session, enabled):
    session.repository = repository(
        createdAt="2024-03-01T00:30:00+01:00",
        hasIssuesEnabled=enabled,
        isEmpty=True,
        defaultBranchRef=None,
    )
    session.add("RepositoryIssues", "R_example", connection("issues"))
    session.add("RepositoryPullRequests", "R_example", connection("pullRequests"))
    reports = api.history_to_months(collect())
    assert [r.metadata.month for r in reports] == ["2024-02", "2024-03", "2024-04"]
    assert all(not r.actors and not r.activities for r in reports)
    operations = [p["operationName"] for p in requests(session)]
    assert "CommitHistory" not in operations
    assert ("RepositoryIssues" in operations) == enabled


@pytest.mark.parametrize(
    "start,end",
    [("bad", None), ("2024-03", "2024-02"), (None, "2024-05"), ("9999-12", None)],
)
def test_invalid_offline_ranges(session, start, end):
    snapshot = collect()
    count = len(session.calls)
    with pytest.raises(ValueError):
        api.history_to_months(snapshot, start, end)
    assert len(session.calls) == count


@pytest.mark.parametrize("failure", ["api", "pagination", "conflict"])
def test_collection_failure_keeps_existing_files(session, tmp_path, failure, caplog):
    caplog.set_level(logging.INFO, logger="repo_history")
    snapshot = collect()
    original = api.write_report(
        api.history_to_months(snapshot, "2024-02", "2024-02")[0], tmp_path
    )
    before = original.read_bytes()
    key = ("IssueLifecycle", "I", "e")
    session.failures[key] = {
        "api": errors("FORBIDDEN"),
        "pagination": connection("timelineItems", more=True, cursor="e"),
        "conflict": connection("timelineItems", [event(date="2024-02-02T00:00:00Z")]),
    }[failure]
    with pytest.raises(api.GitHubAPIError):
        api.backfill(
            "o", "r", "2024-01", "2024-03", output_directory=tmp_path, token="test"
        )
    assert original.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2024-02.json"]
    assert session.closed and "History collection status=failed" in caplog.text


def test_destination_identity_checked_before_traversal(session, tmp_path):
    report = api.history_to_months(collect(), "2024-02", "2024-02")[0]
    api.write_report(report, tmp_path)
    session.calls.clear()
    session.repository.body["data"]["repository"]["id"] = "R_other"
    with pytest.raises(api.ReportWriteError):
        api.backfill("o", "r", output_directory=tmp_path, token="test")
    assert len(session.calls) == 1
    assert session.closed


def test_destination_scanned_once_and_writing_failure_preserves_completed(
    session, tmp_path, monkeypatch
):
    checks = []
    check = backfill_module._check_directory
    monkeypatch.setattr(
        backfill_module,
        "_check_directory",
        lambda *args: (checks.append(args), check(*args))[1],
    )
    writer = backfill_module._write_report

    def fail_second(report, directory):
        if report.metadata.month == "2024-02":
            raise OSError("disk full")
        return writer(report, directory)

    monkeypatch.setattr(backfill_module, "_write_report", fail_second)
    written = []
    with pytest.raises(OSError, match="disk full"):
        backfill_module._run_backfill(
            "o",
            "r",
            "2024-01",
            "2024-03",
            output_directory=tmp_path,
            token="test",
            on_written=written.append,
        )
    assert len(checks) == 1
    assert written == [tmp_path / "2024-01.json"]
    assert not (tmp_path / "2024-02.json").exists()


def test_invalid_later_report_does_not_write_earlier_month(session, tmp_path):
    session.add(
        "IssueComments",
        "I",
        connection(
            "comments", [comment(url="invalid URL", createdAt="2024-03-01T00:00:00Z")]
        ),
    )
    with pytest.raises(api.ReportValidationError):
        api.backfill(
            "o", "r", "2024-01", "2024-03", output_directory=tmp_path, token="test"
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "kwargs",
    [{"discovery": "full"}, {"mode": "bad"}, {"start": "bad"}, {"end": "2024-05"}],
)
def test_invalid_backfill_before_network(session, tmp_path, kwargs):
    with pytest.raises(ValueError):
        api.backfill("o", "r", output_directory=tmp_path, token="test", **kwargs)
    assert not session.calls


def test_cli_history_default_and_discovery_error(
    session, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("GH_TOKEN", "test")
    monkeypatch.setattr(cli, "_utc_now", lambda: NOW)
    assert (
        cli.main(
            [
                "backfill",
                "o/r",
                "--start",
                "2024-02",
                "--end",
                "2024-03",
                "--output-directory",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "2024-02.json",
        "2024-03.json",
    ]
    calls = len(session.calls)
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "backfill",
                "o/r",
                "--discovery",
                "full",
                "--output-directory",
                str(tmp_path),
            ]
        )
    assert error.value.code == 2 and len(session.calls) == calls
    assert "--mode monthly" in capsys.readouterr().err


@pytest.mark.parametrize("created", [None, "bad", "2024-01-01T00:00:00", 123])
def test_bad_creation_closes_client(session, created):
    session.repository.body["data"]["repository"]["createdAt"] = created
    with pytest.raises(api.GraphQLError):
        collect()
    assert session.closed and len(session.calls) == 1


def test_retry_counts_and_costs_on_failed_collection(session, caplog):
    caplog.set_level(logging.INFO, logger="repo_history.history")
    session.failures[("IssueLifecycle", "I", "e")] = errors("MAX_NODE_LIMIT_EXCEEDED")
    with pytest.raises(api.QueryResourceError):
        collect()
    assert f"GraphQL requests={len(session.calls)}" in caplog.text
    assert "retries=3" in caplog.text
    assert "unmeasured_attempts=4" in caplog.text


@pytest.mark.parametrize("mode", ["history", "monthly"])
def test_parent_numbers_in_nested_request_logs(session, caplog, mode):
    caplog.set_level(logging.DEBUG, logger="repo_history.transport")
    session.pages[("RepositoryIssues", "R_example", None)].body["data"]["node"][
        "issues"
    ]["nodes"][0]["number"] = 42
    session.pages[("RepositoryPullRequests", "R_example", None)].body["data"]["node"][
        "pullRequests"
    ]["nodes"][0]["number"] = 87
    if mode == "history":
        collect()
    else:
        api.collect_month("o", "r", 2024, 2, token="test", discovery="full")
    issue_operations = {"IssueLifecycle", "IssueComments"}
    pr_operations = {
        "PullRequestLifecycle",
        "PullRequestComments",
        "PullRequestReviews",
        "PullRequestReviewThreads",
        "ReviewThreadComments",
    }
    seen = set()
    for record in caplog.records:
        message = record.getMessage()
        if not message.startswith("GitHub operation="):
            continue
        operation = message.split("operation=", 1)[1].split()[0]
        seen.add(operation)
        if operation in issue_operations:
            assert " issue=42 " in message and " pr=" not in message
        elif operation in pr_operations:
            assert " pr=87 " in message and " issue=" not in message
        else:
            assert " issue=" not in message and " pr=" not in message
    expected_pr = (
        pr_operations
        if mode == "monthly"
        else pr_operations - {"PullRequestLifecycle", "PullRequestComments"}
    )
    assert (
        issue_operations
        | expected_pr
        | {
            "RepositoryLookup",
            "RepositoryIssues",
            "RepositoryPullRequests",
            "CommitHistory",
        }
        <= seen
    )


def test_embedded_connections_fetch_only_their_continuations(session):
    snapshot = collect()
    nested = [
        p
        for p in requests(session)
        if p["operationName"]
        in {
            "IssueLifecycle",
            "IssueComments",
            "PullRequestLifecycle",
            "PullRequestComments",
            "PullRequestReviews",
            "PullRequestReviewThreads",
            "ReviewThreadComments",
        }
    ]
    assert {(p["operationName"], p["variables"]["after"]) for p in nested} == {
        ("IssueLifecycle", "e"),
        ("IssueComments", "c"),
        ("PullRequestReviews", "v"),
        ("PullRequestReviewThreads", "t"),
        ("ReviewThreadComments", "tc"),
    }
    assert len(nested) == 5
    assert {a.type for a in snapshot.activities} == set(api.ACTIVITY_TYPES)
    parent_requests = [
        p
        for p in requests(session)
        if p["operationName"] in {"RepositoryIssues", "RepositoryPullRequests"}
    ]
    assert all(p["variables"]["first"] == 20 for p in parent_requests)


def test_all_nested_connections_and_later_threads_keep_paginating(session):
    for operation, field in [
        ("PullRequestLifecycle", "timelineItems"),
        ("PullRequestComments", "comments"),
    ]:
        original = deepcopy(session.pages[(operation, "P", None)])
        cursor = operation + "-next"
        session.add(operation, "P", connection(field, more=True, cursor=cursor))
        session.add(operation, "P", original, after=cursor)
    session.add(
        "PullRequestReviewThreads",
        "P",
        connection(
            "reviewThreads", [thread("T_later", isResolved=False, isOutdated=False)]
        ),
        after="t",
    )
    session.add(
        "ReviewThreadComments",
        "T_later",
        connection("comments", [inline("C_later")], more=True, cursor="later-comments"),
    )
    session.add(
        "ReviewThreadComments",
        "T_later",
        connection("comments", [inline("C_reply")], more=True, cursor="last-comments"),
        after="later-comments",
    )
    session.add(
        "ReviewThreadComments",
        "T_later",
        connection("comments", [inline("C_last")]),
        after="last-comments",
    )
    grouped = api.history_to_months(collect(), "2024-02", "2024-02")[0]
    optimized_requests = requests(session)
    assert any(
        p["operationName"] == "ReviewThreadComments"
        and p["variables"]["id"] == "T_later"
        and p["variables"]["after"] == "later-comments"
        for p in optimized_requests
    )
    assert not any(
        p["operationName"] == "ReviewThreadComments" and p["variables"]["after"] is None
        for p in optimized_requests
    )
    continuation = next(
        p
        for p in optimized_requests
        if p["operationName"] == "PullRequestReviewThreads"
    )
    assert "comments(first:" in continuation["query"]
    assert continuation["variables"]["first"] == 10
    reference = api.collect_month("o", "r", 2024, 2, token="test", discovery="full")
    assert grouped.activities == reference.activities
    assert grouped.actors == reference.actors
    assert grouped.summaries == reference.summaries
    later = [
        a
        for a in grouped.activities
        if a.id.endswith((":C_later", ":C_reply", ":C_last"))
    ]
    assert len(later) == 3
    assert all(
        a.metadata.thread_node_id == "T_later" and not a.metadata.resolved
        for a in later
    )


@pytest.mark.parametrize(
    "bad_page",
    [
        None,
        {},
        {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": None}},
        {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": 12}},
    ],
)
def test_malformed_embedded_page_fails_without_fetching_a_replacement(
    session, bad_page
):
    response = session.pages[("IssueComments", "I", None)]
    response.body["data"]["node"]["comments"] = bad_page
    with pytest.raises(api.PaginationError):
        collect()
    assert session.closed
    assert not any(p["operationName"] == "IssueComments" for p in requests(session))


def test_missing_embedded_page_is_not_silently_treated_as_empty(session, monkeypatch):
    embed = session.embed_parents

    def incomplete(response, field, *, issues):
        embed(response, field, issues=issues)
        for item in response.body["data"]["node"][field]["nodes"]:
            item.pop("comments")

    monkeypatch.setattr(session, "embed_parents", incomplete)
    with pytest.raises(api.GraphQLError, match="missing lifecycle field: comments"):
        collect()
    assert session.closed


def test_parent_page_reduction_retains_embedded_child_limits(session, monkeypatch):
    post = session.post
    first = True

    def oversized(endpoint, **kwargs):
        nonlocal first
        if first and kwargs["json"]["operationName"] == "RepositoryPullRequests":
            first = False
            session.calls.append((endpoint, deepcopy(kwargs)))
            return errors("MAX_NODE_LIMIT_EXCEEDED")
        return post(endpoint, **kwargs)

    monkeypatch.setattr(session, "post", oversized)
    collect()
    parents = [
        p for p in requests(session) if p["operationName"] == "RepositoryPullRequests"
    ]
    assert [p["variables"]["first"] for p in parents] == [20, 10, 10]
    assert [p["variables"]["after"] for p in parents] == [None, None, "p"]
    assert all("reviews(first: 20)" in p["query"] for p in parents)

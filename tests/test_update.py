"""Update inference, single-pass interval collection, and failure preservation."""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

import repo_history as api
from repo_history import cli, history, update

from . import test_history
from .github import comment, connection, hydrated, inline, parent, rest_parent, thread
from .helpers import Response, replace, repository
from .test_history import NOW

history_session = test_history.session

ACTIVITY_CONNECTIONS = {
    "IssueLifecycle": "timelineItems",
    "IssueComments": "comments",
    "PullRequestLifecycle": "timelineItems",
    "PullRequestComments": "comments",
    "PullRequestReviews": "reviews",
    "PullRequestReviewThreads": "reviewThreads",
    "ReviewThreadComments": "comments",
}


@pytest.fixture
def session(history_session, monkeypatch):
    """Replay REST discovery and hydration against the shared lifecycle history."""
    result = history_session
    result.pages[("RepositoryPullRequests", "R_example", None)].body["data"]["node"][
        "pullRequests"
    ]["nodes"][0]["number"] = 2
    post = result.post

    def parents():
        values = []
        for field, operation, issues in (
            ("issues", "RepositoryIssues", True),
            ("pullRequests", "RepositoryPullRequests", False),
        ):
            page = result.pages[(operation, "R_example", None)]
            for item in page.body["data"]["node"][field]["nodes"]:
                item = deepcopy(item)
                item["number"] = 1 if issues else 2
                item["__typename"] = "Issue" if issues else "PullRequest"
                values.append((issues, item))
        return values

    def get(url, **kwargs):
        result.calls.append((url, deepcopy(kwargs)))
        return Response(
            [
                rest_parent(
                    item["number"],
                    pr=not issues,
                    created_at=item["createdAt"],
                    updated_at="2024-09-01T00:00:00Z",
                )
                for issues, item in parents()
            ]
        )

    def hydrate(endpoint, **kwargs):
        payload = kwargs["json"]
        if payload["operationName"] == "HydrateRepositoryParents":
            result.calls.append((endpoint, deepcopy(kwargs)))
            by_number = {item["number"]: item for _, item in parents()}
            items = [
                by_number[value]
                for key, value in payload["variables"].items()
                if key.startswith("number")
            ]
            for item in items:
                issues = item["__typename"] == "Issue"
                fragment = "HistoryIssue" if issues else "HistoryPullRequest"
                if f"...{fragment}" in payload["query"]:
                    result.embed_parent(item, issues=issues)
            response = hydrated(*items)
            response.body["data"]["rateLimit"] = {"cost": 1}
            return response
        return post(endpoint, **kwargs)

    monkeypatch.setattr(result, "get", get)
    monkeypatch.setattr(result, "post", hydrate)
    return result


def requests(session):
    return [kwargs["json"] for _, kwargs in session.calls if "json" in kwargs]


@pytest.fixture(autouse=True)
def invocation(monkeypatch):
    monkeypatch.setattr(cli, "_utc_now", lambda: NOW)
    monkeypatch.setenv("GH_TOKEN", "test")


def run(directory, *args):
    return cli.main(["update", "o/r", "--output-directory", str(directory), *args])


def seed(directory, empty, month="2024-02", repository_id="R_example"):
    from repo_history.collection import _month_bounds

    start, end = _month_bounds(*map(int, month.split("-")))
    report = replace(
        empty,
        metadata=replace(
            empty.metadata,
            month=month,
            month_start=start,
            month_end=end,
            collection_started_at=NOW,
            collection_finished_at=NOW,
            is_partial=start <= NOW < end,
            repository_node_id=repository_id,
            repository=replace(
                empty.metadata.repository,
                node_id=repository_id,
                created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            ),
        ),
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{month}.json"
    path.write_text(api.report_to_json(report))
    return path


def test_latest_month_recollected_and_earlier_gaps_untouched(session, tmp_path, empty):
    old = seed(tmp_path, empty, "2023-12")
    latest = seed(tmp_path, empty)
    before = old.read_bytes()
    (tmp_path / "2025-03.json").mkdir()
    (tmp_path / "summary.json").write_text("ignored")
    assert run(tmp_path) == 0
    assert old.read_bytes() == before
    assert not (tmp_path / "2024-01.json").exists()
    assert api.report_from_json(latest.read_text()).activities
    assert (tmp_path / "2024-04.json").exists()
    assert (tmp_path / "summary.json").read_text() == "ignored"
    payloads = requests(session)
    counts = Counter(
        (p["operationName"], p["variables"].get("id"), p["variables"].get("after"))
        for p in payloads
    )
    assert max(counts.values()) == 1
    commits = next(p for p in payloads if p["operationName"] == "CommitHistory")
    assert commits["variables"]["since"] == "2024-01-31T00:00:00+00:00"
    assert commits["variables"]["until"] == "2024-05-02T00:00:00+00:00"


def test_explicit_start_and_end_override_latest(session, tmp_path, empty):
    seed(tmp_path, empty, "2024-03")
    assert run(tmp_path, "--start", "2024-01", "--end", "2024-02") == 0
    assert (tmp_path / "2024-01.json").exists()
    assert (tmp_path / "2024-02.json").exists()
    assert not (tmp_path / "2024-04.json").exists()


@pytest.mark.parametrize("destination", ["missing", "empty", "unrelated"])
def test_no_reports_falls_back_to_history(session, tmp_path, destination):
    directory = tmp_path / "reports"
    if destination != "missing":
        directory.mkdir()
    if destination == "unrelated":
        (directory / "summary.json").write_text("ignored")
        (directory / "2025-01.json").mkdir()
    assert run(directory, "--end", "2024-02") == 0
    assert (directory / "2023-12.json").exists()  # imported commit before creation
    report = api.report_from_json((directory / "2024-02.json").read_text())
    assert (
        report.metadata.provenance.last_updated_by.parent_discovery
        == report.metadata.provenance.last_updated_by.commit_history
        == "full"
    )
    assert all("since" not in p["variables"] for p in requests(session))


def test_explicit_start_empty_directory_uses_range(session, tmp_path):
    assert run(tmp_path, "--start", "2024-02", "--end", "2024-03") == 0
    assert sorted(p.name for p in tmp_path.glob("[0-9]*.json")) == [
        "2024-02.json",
        "2024-03.json",
    ]
    assert any("since" in p["variables"] for p in requests(session))


def test_inferred_reversed_range_before_network(session, tmp_path, empty):
    seed(tmp_path, empty, "2024-03")
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, "--end", "2024-02")
    assert exc.value.code == 2
    assert not session.calls


@pytest.mark.parametrize("problem", ["broken", "mismatch", "mixed"])
def test_invalid_destination_does_not_fallback(session, tmp_path, empty, problem):
    path = seed(tmp_path, empty)
    if problem == "broken":
        path.write_text("broken")
    elif problem == "mismatch":
        path.rename(tmp_path / "2024-01.json")
    else:
        seed(tmp_path, empty, "2024-03", "R_other")
    assert run(tmp_path) == 1
    assert not session.calls


def test_identity_checked_before_traversal(session, tmp_path, empty):
    seed(tmp_path, empty)
    session.repository.body["data"]["repository"]["id"] = "R_other"
    assert run(tmp_path) == 1
    assert len(session.calls) == 1


def test_range_matches_full_history_projection(session, tmp_path):
    assert run(tmp_path, "--start", "2024-01", "--end", "2024-04") == 0
    snapshot = api.collect_history("o", "r", token="test")
    for month in range(1, 5):
        grouped = api.report_from_json(
            (tmp_path / f"2024-{month:02d}.json").read_text()
        )
        monthly = api.history_to_months(
            snapshot, f"2024-{month:02d}", f"2024-{month:02d}"
        )[0]
        assert grouped.activities == monthly.activities
        assert grouped.actors == monthly.actors
        assert grouped.summaries == monthly.summaries
        assert grouped.metadata.provenance.last_updated_by.parent_discovery == "bounded"
        assert grouped.metadata.provenance.last_updated_by.commit_history == "bounded"


@pytest.mark.parametrize("failure", ["collection", "projection", "writing"])
def test_failure_preservation(session, tmp_path, empty, monkeypatch, failure, capsys):
    old = seed(tmp_path, empty)
    before = old.read_bytes()
    if failure == "collection":
        session.failures[("IssueComments", "I", "c")] = connection(
            "comments", more=True, cursor="c"
        )
    elif failure == "projection":
        session.add(
            "IssueComments",
            "I",
            connection(
                "comments",
                [comment(url="invalid URL", createdAt="2024-03-01T00:00:00Z")],
            ),
        )
    else:
        writer = update._write_report

        def fail(report, directory, **kwargs):
            if report.metadata.month == "2024-03":
                raise OSError("disk full")
            return writer(report, directory)

        monkeypatch.setattr(update, "_write_report", fail)
    assert run(tmp_path) == 1
    assert not (tmp_path / "2024-03.json").exists()
    if failure == "writing":
        assert old.read_bytes() != before
        assert str(old) in capsys.readouterr().out
    else:
        assert old.read_bytes() == before
        assert capsys.readouterr().out == ""


def test_destination_validated_once(session, tmp_path, empty, monkeypatch):
    seed(tmp_path, empty)
    checks = []
    check = update._check_directory

    def checked(*args):
        checks.append(args)
        return check(*args)

    monkeypatch.setattr(update, "_check_directory", checked)
    assert run(tmp_path) == 0
    assert len(checks) == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_bounded_range_collects_once_and_groups_months(tmp_path, monkeypatch, enabled):
    from .github import commit, page
    from .github import history as commit_history
    from .helpers import Session

    def make_session():
        result = Session()
        item = parent("P", __typename="PullRequest", date="2024-02-01T00:00:00Z")
        activity_pages = [
            connection("timelineItems"),
            connection("comments", [comment(createdAt="2024-03-01T00:00:00Z")]),
            connection("reviews"),
            connection("reviewThreads"),
        ]
        for response in activity_pages:
            item.update(response.body["data"]["node"])
        result.responses = [
            repository(createdAt="2024-01-01T00:00:00Z", hasIssuesEnabled=enabled),
            Response([rest_parent(pr=True, updated_at="2024-09-01T00:00:00Z")]),
            hydrated(item),
            commit_history(
                [
                    commit(
                        "a",
                        date="2024-02-29T23:59:59Z",
                        author=None,
                        committer=None,
                        authors=page(),
                    )
                ]
            ),
        ]
        return result

    result = make_session()
    monkeypatch.setattr("requests.Session", lambda: result)
    monkeypatch.setattr(history, "_utc_now", lambda: NOW)
    assert run(tmp_path, "--start", "2024-02", "--end", "2024-03") == 0
    rest = [(url, kw) for url, kw in result.calls if "json" not in kw]
    assert len(rest) == 1
    assert parse_qs(urlsplit(rest[0][0]).query)["since"] == ["2024-01-31T23:59:59Z"]
    assert (
        sum(
            kw.get("json", {}).get("operationName") == "RepositoryLookup"
            for _, kw in result.calls
        )
        == 1
    )
    for month, expected in (
        (2, {"pr_opened", "commit"}),
        (3, {"pr_discussion_comment"}),
    ):
        grouped = api.report_from_json(
            (tmp_path / f"2024-{month:02d}.json").read_text()
        )
        assert {a.type for a in grouped.activities} == expected
        assert grouped.summaries.repository_counts["commit"] == (1 if month == 2 else 0)
        assert grouped.metadata.provenance.last_updated_by.parent_discovery == "bounded"
        assert grouped.metadata.provenance.last_updated_by.commit_history == "bounded"
        assert any("Experimental" in x for x in grouped.metadata.coverage.limitations)
    assert len(result.calls) == 4
    assert not result.responses


def test_range_strategy_fallbacks_record_actual_provenance(session, tmp_path):
    assert run(tmp_path, "--start", "0001-01", "--end", "0001-02") == 0
    for name in ("0001-01", "0001-02"):
        report = api.report_from_json((tmp_path / f"{name}.json").read_text())
        assert not report.activities
        assert (
            report.metadata.provenance.last_updated_by.parent_discovery
            == report.metadata.provenance.last_updated_by.commit_history
            == "full"
        )
        assert not any(
            "Experimental" in x or "24-hour" in x
            for x in report.metadata.coverage.limitations
        )
    assert not any("since" in p["variables"] for p in requests(session))


@pytest.mark.parametrize("enabled", [False, True])
def test_range_empty_repository(session, tmp_path, enabled):
    session.repository = repository(
        createdAt="2024-01-01T00:00:00Z",
        hasIssuesEnabled=enabled,
        isEmpty=True,
        defaultBranchRef=None,
    )
    session.add("RepositoryIssues", "R_example", connection("issues"))
    session.add("RepositoryPullRequests", "R_example", connection("pullRequests"))
    assert run(tmp_path, "--start", "2023-12", "--end", "2024-01") == 0
    for month in ("2023-12", "2024-01"):
        report = api.report_from_json((tmp_path / f"{month}.json").read_text())
        assert report.activities == report.actors == ()
    operations = [p["operationName"] for p in requests(session)]
    assert "CommitHistory" not in operations
    assert "HydrateRepositoryParents" not in operations


@pytest.mark.parametrize("paginated", [False, True])
def test_embedded_update_request_budget(session, tmp_path, paginated):
    if not paginated:
        # Same source history, with each activity connection fitting in one page.
        for (operation, node_id, after), response in list(session.pages.items()):
            if after is not None or operation not in ACTIVITY_CONNECTIONS:
                continue
            field = ACTIVITY_CONNECTIONS[operation]
            page = deepcopy(response.body["data"]["node"][field])
            while page["pageInfo"]["hasNextPage"]:
                cursor = page["pageInfo"]["endCursor"]
                following = session.pages[(operation, node_id, cursor)]
                following = following.body["data"]["node"][field]
                page["nodes"].extend(deepcopy(following["nodes"]))
                page["pageInfo"] = deepcopy(following["pageInfo"])
            session.add(operation, node_id, connection(field, page["nodes"]))

    assert run(tmp_path, "--start", "2024-02", "--end", "2024-02") == 0
    optimized = api.report_from_json((tmp_path / "2024-02.json").read_text())
    optimized_calls = list(session.calls)
    optimized_payloads = requests(session)
    nested = [
        p for p in optimized_payloads if p["operationName"] in ACTIVITY_CONNECTIONS
    ]
    assert all(p["variables"]["after"] is not None for p in nested)
    if paginated:
        assert {(p["operationName"], p["variables"]["after"]) for p in nested} == {
            ("IssueLifecycle", "e"),
            ("IssueComments", "c"),
            ("PullRequestReviews", "v"),
            ("PullRequestReviewThreads", "t"),
            ("ReviewThreadComments", "tc"),
        }
    else:
        assert not nested
    assert len(optimized_calls) == (11 if paginated else 6)
    assert sum("json" not in kw for _, kw in optimized_calls) == 1
    assert (
        sum(
            p["operationName"] == "HydrateRepositoryParents" for p in optimized_payloads
        )
        == 1
    )
    assert set(optimized.summaries.repository_counts) == set(api.ACTIVITY_TYPES)
    assert optimized.summaries.repository_counts["pr_review"] == 1
    assert optimized.summaries.repository_counts["pr_review_comment"] == 2


def test_update_all_activity_overflows_and_later_thread_comments(session, tmp_path):
    # Give each parent connection its own first continuation, including otherwise
    # complete PR connections. The existing fixture adds further overflow pages.
    for operation, field in ACTIVITY_CONNECTIONS.items():
        if operation == "ReviewThreadComments":
            continue
        node_id = "I" if operation.startswith("Issue") else "P"
        first = deepcopy(session.pages[(operation, node_id, None)])
        cursor = f"{operation}-next"
        session.add(operation, node_id, connection(field, more=True, cursor=cursor))
        session.add(operation, node_id, first, after=cursor)
    session.add(
        "PullRequestReviewThreads",
        "P",
        connection("reviewThreads", [thread("T_later", isResolved=False)]),
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
    assert run(tmp_path, "--start", "2024-01", "--end", "2024-03") == 0
    nested = [
        p for p in requests(session) if p["operationName"] in ACTIVITY_CONNECTIONS
    ]
    assert {p["operationName"] for p in nested} == set(ACTIVITY_CONNECTIONS)
    for payload in nested:
        assert payload["variables"]["after"] is not None
        if payload["operationName"] == "PullRequestReviewThreads":
            assert payload["variables"]["first"] == 10
            assert "comments(first: 20)" in payload["query"]
        else:
            assert payload["variables"]["first"] == 100
    assert {
        p["variables"]["after"]
        for p in nested
        if p["operationName"] == "ReviewThreadComments"
        and p["variables"]["id"] == "T_later"
    } == {"later-comments", "last-comments"}
    snapshot = api.collect_history("o", "r", token="test")
    for month in (1, 2, 3):
        actual = api.report_from_json((tmp_path / f"2024-{month:02d}.json").read_text())
        reference = api.history_to_months(
            snapshot, f"2024-{month:02d}", f"2024-{month:02d}"
        )[0]
        assert actual.activities == reference.activities
        assert actual.actors == reference.actors
        assert actual.summaries == reference.summaries


@pytest.mark.parametrize(
    "operation,node_id",
    [
        ("IssueLifecycle", "I"),
        ("IssueComments", "I"),
        ("PullRequestLifecycle", "P"),
        ("PullRequestComments", "P"),
        ("PullRequestReviews", "P"),
        ("PullRequestReviewThreads", "P"),
        ("ReviewThreadComments", "T_one"),
    ],
)
@pytest.mark.parametrize("failure", ["missing", "malformed", "cursor", "loop"])
def test_bad_embedded_update_preserves_reports_and_state(
    session, tmp_path, monkeypatch, operation, node_id, failure
):
    assert run(tmp_path, "--start", "2024-02", "--end", "2024-03") == 0
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    field = ACTIVITY_CONNECTIONS[operation]
    page = session.pages[(operation, node_id, None)].body["data"]["node"][field]
    if failure == "missing":
        # Remove the selected connection from the hydrated response after embedding.
        post = session.post

        def incomplete(endpoint, **kwargs):
            response = post(endpoint, **kwargs)
            if kwargs["json"]["operationName"] == "HydrateRepositoryParents":
                for item in response.body["data"]["node"].values():
                    if item["id"] == node_id:
                        del item[field]
                    elif (
                        operation == "ReviewThreadComments" and "reviewThreads" in item
                    ):
                        for value in item["reviewThreads"]["nodes"]:
                            if value["id"] == node_id:
                                del value["comments"]
            return response

        monkeypatch.setattr(session, "post", incomplete)
    elif failure == "malformed":
        page["pageInfo"] = None
    elif failure == "cursor":
        page["pageInfo"] = {"hasNextPage": True, "endCursor": None}
    else:
        page["pageInfo"] = {"hasNextPage": True, "endCursor": "loop"}
        session.add(
            operation,
            node_id,
            connection(field, more=True, cursor="loop"),
            after="loop",
        )
    session.calls.clear()
    assert run(tmp_path, "--start", "2024-02", "--end", "2024-04") == 1
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    assert not any(
        p["operationName"] == operation and p["variables"].get("after") is None
        for p in requests(session)
    )
    assert session.closed

"""Update inference, single-pass interval collection, and failure preservation."""

from collections import Counter
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest

import repo_history as api
from repo_history import cli, history, update

from .helpers import Response, repository
from .test_collection import comment, connection, parent
from .test_discovery import hydrated, rest_parent
from .test_history import NOW, requests
from .test_history import session as session


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
            ongoing=start <= NOW < end,
            repository_node_id=repository_id,
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
    (tmp_path / "manifest.json").write_text("ignored")
    assert run(tmp_path, "--discovery", "full") == 0
    assert old.read_bytes() == before
    assert not (tmp_path / "2024-01.json").exists()
    assert api.report_from_json(latest.read_text()).activities
    assert (tmp_path / "2024-04.json").exists()
    assert (tmp_path / "manifest.json").read_text() == "ignored"
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
    assert (
        run(tmp_path, "--start", "2024-01", "--end", "2024-02", "--discovery", "full")
        == 0
    )
    assert (tmp_path / "2024-01.json").exists()
    assert (tmp_path / "2024-02.json").exists()
    assert not (tmp_path / "2024-04.json").exists()


@pytest.mark.parametrize("destination", ["missing", "empty", "unrelated"])
def test_no_reports_falls_back_to_history(session, tmp_path, destination):
    directory = tmp_path / "reports"
    if destination != "missing":
        directory.mkdir()
    if destination == "unrelated":
        (directory / "manifest.json").write_text("ignored")
        (directory / "2025-01.json").mkdir()
    assert run(directory, "--end", "2024-02") == 0
    assert (directory / "2023-12.json").exists()  # imported commit before creation
    report = api.report_from_json((directory / "2024-02.json").read_text())
    assert report.metadata.parent_discovery == report.metadata.commit_history == "full"
    assert all("since" not in p["variables"] for p in requests(session))


def test_explicit_start_empty_directory_uses_range(session, tmp_path):
    assert (
        run(tmp_path, "--start", "2024-02", "--end", "2024-03", "--discovery", "full")
        == 0
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == [
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


def test_full_range_matches_independent_months(session, tmp_path):
    assert (
        run(tmp_path, "--start", "2024-01", "--end", "2024-04", "--discovery", "full")
        == 0
    )
    for month in range(1, 5):
        grouped = api.report_from_json(
            (tmp_path / f"2024-{month:02d}.json").read_text()
        )
        monthly = api.collect_month("o", "r", 2024, month, discovery="full")
        assert grouped.activities == monthly.activities
        assert grouped.actors == monthly.actors
        assert grouped.summaries == monthly.summaries
        assert grouped.metadata.parent_discovery == "full"
        assert grouped.metadata.commit_history == "bounded"


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

        def fail(report, directory):
            if report.metadata.month == "2024-03":
                raise OSError("disk full")
            return writer(report, directory)

        monkeypatch.setattr(update, "_write_report", fail)
    assert run(tmp_path, "--discovery", "full") == 1
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
    assert run(tmp_path, "--discovery", "full") == 0
    assert len(checks) == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_bounded_range_one_discovery_and_monthly_equivalence(
    tmp_path, monkeypatch, enabled
):
    from .helpers import Session
    from .test_commits import commit, page
    from .test_commits import history as commit_history

    def make_session():
        result = Session()
        result.responses = [
            repository(createdAt="2024-01-01T00:00:00Z", hasIssuesEnabled=enabled),
            Response([rest_parent(pr=True, updated_at="2024-09-01T00:00:00Z")]),
            hydrated(
                parent("P", __typename="PullRequest", date="2024-02-01T00:00:00Z")
            ),
            connection("timelineItems"),
            connection("comments", [comment(createdAt="2024-03-01T00:00:00Z")]),
            connection("reviews"),
            connection("reviewThreads"),
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
    monkeypatch.setattr("repo_history.collection._utc_now", lambda: NOW)
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
    for month in (2, 3):
        result = make_session()
        grouped = api.report_from_json(
            (tmp_path / f"2024-{month:02d}.json").read_text()
        )
        monthly = api.collect_month("o", "r", 2024, month)
        assert grouped.activities == monthly.activities
        assert grouped.summaries == monthly.summaries
        assert (
            grouped.metadata.parent_discovery
            == grouped.metadata.commit_history
            == "bounded"
        )
        assert any("Experimental" in x for x in grouped.metadata.coverage_limitations)


def test_range_strategy_fallbacks_record_actual_provenance(session, tmp_path):
    assert run(tmp_path, "--start", "0001-01", "--end", "0001-02") == 0
    for name in ("0001-01", "0001-02"):
        report = api.report_from_json((tmp_path / f"{name}.json").read_text())
        assert not report.activities
        assert (
            report.metadata.parent_discovery == report.metadata.commit_history == "full"
        )
        assert not any(
            "Experimental" in x or "24-hour" in x
            for x in report.metadata.coverage_limitations
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
    assert (
        run(tmp_path, "--start", "2023-12", "--end", "2024-01", "--discovery", "full")
        == 0
    )
    for month in ("2023-12", "2024-01"):
        report = api.report_from_json((tmp_path / f"{month}.json").read_text())
        assert report.activities == report.actors == ()
    operations = [p["operationName"] for p in requests(session)]
    assert "CommitHistory" not in operations
    assert ("RepositoryIssues" in operations) == enabled

"""Mutable offline GitHub histories exercise checkpoint-backed backfilling."""

import importlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

import repo_history as api
from repo_history import cli, history, update
from repo_history.state import verify_collection_state

from .github import (
    actor,
    comment,
    commit,
    connection,
    event,
    hydrated,
    inline,
    page,
    parent,
    rest_parent,
    review,
    thread,
)
from .github import history as commit_history
from .helpers import Response, Session, repository


class Source(Session):
    """Serve filtered REST discovery and complete GraphQL histories independently."""

    def __init__(self):
        super().__init__()
        self.now = datetime(2024, 4, 15, tzinfo=timezone.utc)
        self.branch = "main"
        self.head = "a" * 40
        self.commits = [self.commit("a", "2024-01-01T00:00:00Z")]
        self.parents = [
            parent(
                "P",
                __typename="PullRequest",
                number=1,
                date="2024-03-05T00:00:00Z",
                reviews=page([review(submittedAt="2024-03-10T00:00:00Z")]),
                reviewThreads=page(
                    [
                        thread(
                            comments=page(
                                [
                                    inline(
                                        createdAt="2024-03-11T00:00:00Z",
                                        publishedAt="2024-03-11T00:00:00Z",
                                    )
                                ]
                            )
                        )
                    ]
                ),
            ),
            parent(
                "I",
                __typename="Issue",
                number=2,
                date="2024-02-01T00:00:00Z",
                comments=page([comment("C_untouched")]),
            ),
        ]
        self.updated = {1: "2024-03-12T00:00:00Z", 2: "2024-02-12T00:00:00Z"}

    @staticmethod
    def commit(sha, date):
        return commit(sha, date=date, author=None, committer=None, authors=page())

    def get(self, url, **kwargs):
        self.calls.append((url, deepcopy(kwargs)))
        since = parse_qs(urlsplit(url).query)["since"][0]
        return Response(
            [
                rest_parent(
                    p["number"],
                    pr=p["__typename"] == "PullRequest",
                    created_at=p["createdAt"],
                    updated_at=self.updated[p["number"]],
                )
                for p in self.parents
                if self.updated[p["number"]] > since
            ]
        )

    def post(self, url, **kwargs):
        self.calls.append((url, deepcopy(kwargs)))
        payload = kwargs["json"]
        operation, values = payload["operationName"], payload["variables"]
        if operation == "RepositoryLookup":
            result = repository(
                createdAt="2024-01-01T00:00:00Z",
                hasIssuesEnabled=True,
                isEmpty=self.head is None,
                defaultBranchRef=None
                if self.head is None
                else {
                    "name": self.branch,
                    "target": {"__typename": "Commit", "oid": self.head},
                },
            )
        elif operation in ("RepositoryIssues", "RepositoryPullRequests"):
            kind, field = (
                ("Issue", "issues")
                if operation == "RepositoryIssues"
                else ("PullRequest", "pullRequests")
            )
            result = connection(
                field, [p for p in self.parents if p["__typename"] == kind]
            )
        elif operation == "HydrateRepositoryParents":
            by_number = {p["number"]: p for p in self.parents}
            result = hydrated(
                *[
                    by_number[number]
                    for key, number in values.items()
                    if key.startswith("number")
                ]
            )
        elif operation == "CommitHistory":
            # Small pages force complete traversal even across timestamp inversions.
            offset = int(values["after"] or 0)
            more = len(self.commits) > offset + 2
            result = commit_history(
                self.commits[offset : offset + 2],
                more=more,
                cursor=str(offset + 2) if more else None,
            )
        elif operation == "CommitActor":
            result = Response({"data": {"node": actor(values["id"])}})
        else:
            raise AssertionError(operation)
        result = deepcopy(result)
        result.body["data"]["rateLimit"] = {"cost": 1}
        return result

    def operations(self):
        return [kw["json"]["operationName"] for _, kw in self.calls if "json" in kw]


@pytest.fixture
def source(monkeypatch):
    result = Source()
    monkeypatch.setattr("requests.Session", lambda: result)
    monkeypatch.setattr(history, "_utc_now", lambda: result.now)
    monkeypatch.setattr(cli, "_utc_now", lambda: result.now)
    monkeypatch.setattr(
        importlib.import_module("repo_history.collect"), "_utc_now", lambda: result.now
    )
    monkeypatch.setenv("GH_TOKEN", "test")
    return result


def run(directory, *args):
    return cli.main(["update", "o/r", "--output-directory", str(directory), *args])


def state(directory):
    value = json.loads((directory / "collection-state.json").read_text())
    verify_collection_state(value, directory)
    return value


def reports(directory):
    return {
        p.stem: api.report_from_json(p.read_text())
        for p in directory.glob("[0-9]*.json")
    }


def test_late_merge_backfills_and_matches_collect(source, tmp_path):
    assert run(tmp_path) == 0
    before = reports(tmp_path)
    baseline = state(tmp_path)
    untouched = (tmp_path / "2024-02.json").read_bytes()
    source.now = datetime(2024, 5, 15, tzinfo=timezone.utc)
    source.commits.extend(
        [
            source.commit("b", "2024-03-15T00:00:00Z"),
            source.commit("c", "2024-04-15T00:00:00Z"),
        ]
    )
    source.parents[0]["timelineItems"] = page(
        [event(kind="MergedEvent", date="2024-05-01T00:00:00Z")]
    )
    source.updated[1] = "2024-05-01T00:00:00Z"
    source.head = "d" * 40
    source.calls.clear()
    assert run(tmp_path) == 0
    actual = reports(tmp_path)
    assert (tmp_path / "2024-02.json").read_bytes() == untouched
    assert actual["2024-03"].summaries.repository_counts["commit"] == 1
    assert actual["2024-04"].summaries.repository_counts["commit"] == 1
    assert not actual["2024-04"].metadata.is_partial
    assert actual["2024-05"].metadata.is_partial
    for month in ("2024-03", "2024-04"):
        assert (
            actual[month].metadata.provenance.created_by
            == before[month].metadata.provenance.created_by
        )
        assert (
            actual[month].metadata.provenance.last_rebuilt_by
            == before[month].metadata.provenance.last_rebuilt_by
        )
        assert actual[month].metadata.provenance.last_updated_by.mode == "incremental"
    checkpoint = state(tmp_path)["checkpoint"]
    assert checkpoint["baseline_run"] == baseline["checkpoint"]["baseline_run"]
    assert checkpoint["issues"]["last_full_scan_at"] == "2024-04-15T00:00:00Z"
    assert checkpoint["commits"]["checked_through"] == "2024-05-15T00:00:00Z"
    assert checkpoint["commits"]["default_branch_head"] == "d" * 40
    assert source.operations().count("RepositoryLookup") == 1
    assert source.operations().count("CommitHistory") == 2
    assert "RepositoryPullRequests" not in source.operations()
    for _, kw in source.calls:
        if kw.get("json", {}).get("operationName") == "CommitHistory":
            assert "since" not in kw["json"]["variables"]
            assert "until" not in kw["json"]["variables"]
    reference = api.history_to_months(api.collect_history("o", "r", token="test"))
    for expected in reference:
        got = actual[expected.metadata.month]
        assert (got.activities, got.actors, got.summaries) == (
            expected.activities,
            expected.actors,
            expected.summaries,
        )
    assert run(tmp_path) == 0
    again = reports(tmp_path)
    assert {m: r.activities for m, r in again.items()} == {
        m: r.activities for m, r in actual.items()
    }
    state(tmp_path)


def test_refresh_removes_deleted_events_and_updates_state_and_actors(source, tmp_path):
    assert run(tmp_path) == 0
    source.now = datetime(2024, 5, 15, tzinfo=timezone.utc)
    pr = source.parents[0]
    pr["reviews"]["nodes"][0]["state"] = "DISMISSED"
    pr["reviewThreads"]["nodes"][0]["isResolved"] = False
    pr["reviewThreads"]["nodes"][0]["isOutdated"] = False
    pr["author"] = actor(login="renamed")
    # Update inside the overlap would be excluded by a non-overlapping local filter.
    source.updated[1] = "2024-04-14T23:59:59Z"
    source.calls.clear()
    assert run(tmp_path) == 0
    march = reports(tmp_path)["2024-03"]
    by_type = {a.type: a for a in march.activities}
    assert by_type["pr_review"].metadata.state == "DISMISSED"
    assert not by_type["pr_review_comment"].metadata.resolved
    assert not by_type["pr_review_comment"].metadata.outdated
    assert next(a for a in march.actors if a.key == "U_author").login == "renamed"
    assert reports(tmp_path)["2024-02"].activities  # Undiscovered issue retained.
    rest = [url for url, kw in source.calls if "json" not in kw]
    assert parse_qs(urlsplit(rest[0]).query)["since"] == ["2024-04-14T23:59:58Z"]
    pr["reviews"] = page()
    pr["reviewThreads"] = page()
    source.updated[1] = "2024-05-15T00:00:00Z"
    assert run(tmp_path) == 0
    march = reports(tmp_path)["2024-03"]
    assert {a.type for a in march.activities} == {"pr_opened"}
    assert {a.key for a in march.actors} == {"U_author"}
    state(tmp_path)


@pytest.mark.parametrize("empty", [False, True])
def test_commit_replacement_and_empty_month_rollover(source, tmp_path, empty):
    assert run(tmp_path) == 0
    source.now = datetime(2024, 7, 1, tzinfo=timezone.utc)
    source.commits = (
        []
        if empty
        else [
            source.commit("b", "2024-03-01T00:00:00Z"),
            source.commit("c", "2024-01-02T00:00:00Z"),
            source.commit("d", "2024-02-01T00:00:00Z"),
        ]
    )
    source.branch, source.head = (None, None) if empty else ("new-default", "f" * 40)
    source.calls.clear()
    assert run(tmp_path) == 0
    actual = reports(tmp_path)
    ids = {a.id for r in actual.values() for a in r.activities if a.type == "commit"}
    assert f"commit:R_example:{'a' * 40}" not in ids
    assert len(ids) == (0 if empty else 3)
    assert not actual["2024-06"].activities
    assert not actual["2024-06"].metadata.is_partial
    assert actual["2024-07"].metadata.is_partial
    assert source.operations().count("CommitHistory") == (0 if empty else 2)
    state(tmp_path)


@pytest.mark.parametrize(
    "problem", ["missing", "malformed", "schema", "null", "changed", "gap", "extra"]
)
def test_unusable_checkpoint_automatically_rebuilds(source, tmp_path, problem):
    assert run(tmp_path) == 0
    path = tmp_path / "collection-state.json"
    if problem == "missing":
        path.unlink()
    elif problem == "malformed":
        path.write_text("not json")
    elif problem in ("schema", "null"):
        value = json.loads(path.read_text())
        value["schema_version" if problem == "schema" else "checkpoint"] = (
            "bad" if problem == "schema" else None
        )
        path.write_text(json.dumps(value))
    elif problem == "changed":
        report = tmp_path / "2024-03.json"
        report.write_text(report.read_text() + "\n")
    elif problem == "extra":
        source.now = datetime(2024, 5, 15, tzinfo=timezone.utc)
        assert run(tmp_path, "--start", "2024-05") == 0
        # Restore the old inventory so the added report is detected.
        value = json.loads(path.read_text())
        del value["reports"]["2024-05"]
        path.write_text(json.dumps(value))
    else:
        (tmp_path / "2024-02.json").unlink()
    source.calls.clear()
    assert run(tmp_path) == 0
    assert "RepositoryPullRequests" in source.operations()
    assert "2024-02" in reports(tmp_path)
    assert state(tmp_path)["checkpoint"] is not None


def test_earlier_history_reuses_capture_and_establishes_boundary(source, tmp_path):
    assert run(tmp_path) == 0
    source.now = datetime(2024, 5, 15, tzinfo=timezone.utc)
    source.commits.append(source.commit("b", "2023-12-01T00:00:00Z"))
    source.calls.clear()
    assert run(tmp_path) == 0
    assert source.operations().count("RepositoryLookup") == 1
    assert source.operations().count("CommitHistory") == 1
    assert "RepositoryPullRequests" in source.operations()
    value = state(tmp_path)
    assert value["repository"]["history_start_month"] == "2023-12"
    assert value["last_applied_run"]["parent_discovery"] == "full"
    assert reports(tmp_path)["2023-12"].activities


def test_explicit_month_is_strict_and_invalidates_checkpoint(source, tmp_path):
    assert run(tmp_path) == 0
    before = {p.name: p.read_bytes() for p in tmp_path.glob("[0-9]*.json")}
    source.commits.append(source.commit("b", "2024-03-01T00:00:00Z"))
    assert run(tmp_path, "--start", "2024-04", "--end", "2024-04") == 0
    for name, content in before.items():
        if name != "2024-04.json":
            assert (tmp_path / name).read_bytes() == content
    assert state(tmp_path)["checkpoint"] is None
    assert run(tmp_path) == 0
    assert reports(tmp_path)["2024-03"].summaries.repository_counts["commit"] == 1
    assert state(tmp_path)["checkpoint"] is not None


@pytest.mark.parametrize("failure", ["collection", "report", "state"])
def test_failed_incremental_update_preserves_checkpoint_and_recovers(
    source, tmp_path, monkeypatch, failure
):
    assert run(tmp_path) == 0
    source.now = datetime(2024, 5, 15, tzinfo=timezone.utc)
    source.commits.append(source.commit("b", "2024-03-01T00:00:00Z"))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with monkeypatch.context() as patch:
        if failure == "collection":
            post = source.post

            def fail(url, **kwargs):
                if kwargs["json"]["operationName"] == "CommitHistory":
                    return Response({"data": {"node": {"object": None}}})
                return post(url, **kwargs)

            patch.setattr(source, "post", fail)
        elif failure == "report":
            writer = update._write_report

            def fail(report, directory, **kwargs):
                if report.metadata.month == "2024-05":
                    raise OSError("disk full")
                return writer(report, directory, **kwargs)

            patch.setattr(update, "_write_report", fail)
        else:
            module = importlib.import_module("repo_history.state")
            actual = module.os.replace

            def fail(source_path, target):
                if str(target).endswith("collection-state.json"):
                    raise OSError("state failed")
                return actual(source_path, target)

            patch.setattr(module.os, "replace", fail)
        assert run(tmp_path) == 1
    assert (tmp_path / "collection-state.json").read_bytes() == before[
        "collection-state.json"
    ]
    if failure == "collection":
        assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    source.calls.clear()
    assert run(tmp_path) == 0
    assert ("RepositoryPullRequests" in source.operations()) == (
        failure != "collection"
    )
    assert reports(tmp_path)["2024-03"].summaries.repository_counts["commit"] == 1
    state(tmp_path)


def test_timestamp_invisible_changes_wait_for_manual_collect(source, tmp_path):
    assert run(tmp_path) == 0
    source.now = datetime(2024, 5, 15, tzinfo=timezone.utc)
    source.parents[0]["reviewThreads"]["nodes"][0]["isResolved"] = False
    assert run(tmp_path) == 0
    march = reports(tmp_path)["2024-03"]
    assert next(
        a for a in march.activities if a.type == "pr_review_comment"
    ).metadata.resolved
    api.collect("o", "r", output_directory=tmp_path, token="test")
    march = reports(tmp_path)["2024-03"]
    assert not next(
        a for a in march.activities if a.type == "pr_review_comment"
    ).metadata.resolved
    state(tmp_path)


@pytest.mark.parametrize("problem", ["projection", "classification"])
def test_reconciliation_validation_finishes_before_any_writes(
    source, tmp_path, problem
):
    assert run(tmp_path) == 0
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    source.now = datetime(2024, 5, 15, tzinfo=timezone.utc)
    if problem == "projection":
        source.commits.append(source.commit("b", "2024-03-01T00:00:00Z"))
        source.commits[-1]["url"] = "invalid URL"
    else:
        source.updated[1] = "2024-05-01T00:00:00Z"
        source.parents[0]["author"] = actor(kind="Bot")
    assert run(tmp_path) == 1
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize("args", [("--start", "2024-04"), ("--end", "2024-04")])
def test_either_explicit_flag_prevents_historical_backfill(source, tmp_path, args):
    assert run(tmp_path) == 0
    march = (tmp_path / "2024-03.json").read_bytes()
    source.commits.append(source.commit("b", "2024-03-01T00:00:00Z"))
    assert run(tmp_path, *args) == 0
    assert (tmp_path / "2024-03.json").read_bytes() == march
    assert state(tmp_path)["checkpoint"] is None


def test_unmaterialized_future_activity_invalidates_incremental_checkpoint(
    source, tmp_path
):
    assert run(tmp_path) == 0
    source.now = datetime(2024, 5, 15, tzinfo=timezone.utc)
    source.commits.append(source.commit("b", "2024-07-01T00:00:00Z"))
    assert run(tmp_path) == 0
    assert "2024-07" not in reports(tmp_path)
    assert state(tmp_path)["checkpoint"] is None
    source.now = datetime(2024, 7, 15, tzinfo=timezone.utc)
    assert run(tmp_path) == 0
    assert reports(tmp_path)["2024-07"].summaries.repository_counts["commit"] == 1
    assert state(tmp_path)["checkpoint"] is not None

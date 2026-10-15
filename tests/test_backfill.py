"""Tests for chronological backfill and failure preservation."""

import importlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from repo_history import (
    GraphQLError,
    ReportWriteError,
    RepositoryUnavailableError,
    backfill,
    report_from_json,
    report_to_json,
)
from repo_history.collection import _month_bounds

from .helpers import Response

module = importlib.import_module("repo_history.backfill")


@pytest.fixture
def collector(monkeypatch, empty):
    calls = []

    def collect(owner, repo, year, month, **kwargs):
        calls.append((owner, repo, year, month, kwargs))
        start, end = _month_bounds(year, month)
        return replace(
            empty,
            metadata=replace(
                empty.metadata,
                month=f"{year:04d}-{month:02d}",
                month_start=start,
                month_end=end,
                collection_started_at=end,
                collection_finished_at=end,
                ongoing=False,
            ),
        )

    monkeypatch.setattr(module, "collect_month", collect)
    monkeypatch.setattr(
        module, "_utc_now", lambda: datetime(2025, 1, 1, tzinfo=timezone.utc)
    )
    return calls, collect


def test_chronological_inclusive_range_and_replacement(tmp_path, collector):
    calls, _ = collector
    paths = backfill(
        "owner",
        "repo",
        "2023-12",
        "2024-03",
        mode="monthly",
        output_directory=tmp_path,
        token="secret",
        discovery="bounded",
    )
    assert [p.name for p in paths] == [
        "2023-12.json",
        "2024-01.json",
        "2024-02.json",
        "2024-03.json",
    ]
    assert [(c[2], c[3]) for c in calls] == [
        (2023, 12),
        (2024, 1),
        (2024, 2),
        (2024, 3),
    ]
    assert all(c[4] == {"token": "secret", "discovery": "bounded"} for c in calls)
    leap = report_from_json(paths[2].read_text()).metadata
    assert leap.month_end - leap.month_start == timedelta(days=29)
    before = {p: p.read_bytes() for p in paths}
    backfill(
        "owner", "repo", "2024-02", "2024-02", mode="monthly", output_directory=tmp_path
    )
    assert len(calls) == 5
    assert calls[-1][2:4] == (2024, 2)
    assert all(p.read_bytes() == text for p, text in before.items())


@pytest.mark.parametrize(
    "start,end",
    [
        ("2024-2", "2024-03"),
        ("2024-00", "2024-03"),
        ("0000-01", "2024-03"),
        ("2024-03", "2024-02"),
        ("2024-03", "2025-02"),
        ("9999-12", "9999-12"),
        ("２０２４-02", "2024-03"),
    ],
)
def test_invalid_range_before_network(tmp_path, collector, start, end):
    calls, _ = collector
    with pytest.raises(ValueError):
        backfill("owner", "repo", start, end, mode="monthly", output_directory=tmp_path)
    assert not calls
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "owner,repo,discovery",
    [("", "repo", "full"), ("o/r", "repo", "full"), ("o", "r", "bad")],
)
def test_invalid_request(tmp_path, collector, owner, repo, discovery):
    calls, _ = collector
    with pytest.raises(ValueError):
        backfill(
            owner,
            repo,
            "2024-01",
            "2024-02",
            mode="monthly",
            output_directory=tmp_path,
            discovery=discovery,
        )
    assert not calls


def test_bad_destination_before_network(tmp_path, collector):
    calls, _ = collector
    (tmp_path / "2024-01.json").write_text("broken")
    with pytest.raises(ReportWriteError):
        backfill(
            "o", "r", "2024-02", "2024-03", mode="monthly", output_directory=tmp_path
        )
    assert not calls


@pytest.mark.parametrize("failure", ["collection", "writing", "identity"])
def test_failure_keeps_completed_months_and_stops(
    tmp_path, collector, monkeypatch, failure
):
    calls, collect = collector
    untouched = tmp_path / "notes.txt"
    untouched.write_text("keep")

    def failing_collect(owner, repo, year, month, **kwargs):
        report = collect(owner, repo, year, month, **kwargs)
        if month == 2:
            if failure == "collection":
                raise RuntimeError("collection failed")
            if failure == "identity":
                return replace(
                    report,
                    metadata=replace(report.metadata, repository_node_id="R_other"),
                )
        return report

    monkeypatch.setattr(module, "collect_month", failing_collect)
    writer = module._write_report

    def failing_write(report, directory):
        if report.metadata.month == "2024-02":
            raise OSError("writing failed")
        return writer(report, directory)

    if failure == "writing":
        monkeypatch.setattr(module, "_write_report", failing_write)
    completed = []
    with pytest.raises((RuntimeError, OSError, ReportWriteError)):
        module._run_backfill(
            "o",
            "r",
            "2024-01",
            "2024-03",
            mode="monthly",
            output_directory=tmp_path,
            on_written=completed.append,
        )
    assert [c[3] for c in calls] == [1, 2]
    assert completed == [tmp_path / "2024-01.json"]
    assert report_from_json(completed[0].read_text()).metadata.month == "2024-01"
    assert untouched.read_text() == "keep"
    assert not (tmp_path / "2024-02.json").exists()


def test_current_month_accepted(tmp_path, collector):
    paths = backfill(
        "o", "r", "2025-01", "2025-01", mode="monthly", output_directory=tmp_path
    )
    assert len(paths) == 1


def test_mixed_repository_directory_fails_before_network(tmp_path, collector):
    calls, collect = collector
    first = collect("o", "r", 2024, 1)
    second = collect("o", "r", 2024, 2)
    second = replace(
        second, metadata=replace(second.metadata, repository_node_id="R_other")
    )
    (tmp_path / "2024-01.json").write_text(report_to_json(first))
    (tmp_path / "2024-02.json").write_text(report_to_json(second))
    calls.clear()
    with pytest.raises(ReportWriteError, match="2024-02.json"):
        backfill(
            "o", "r", "2024-03", "2024-03", mode="monthly", output_directory=tmp_path
        )
    assert not calls


@pytest.mark.parametrize(
    "start,end,expected",
    [
        (None, None, [(2024, 11), (2024, 12), (2025, 1)]),
        (None, "2024-12", [(2024, 11), (2024, 12)]),
        ("2024-12", None, [(2024, 12), (2025, 1)]),
    ],
)
def test_automatic_endpoints(tmp_path, collector, monkeypatch, start, end, expected):
    calls, _ = collector
    lookups = []

    def lookup(owner, repo, token):
        lookups.append((owner, repo, token))
        return "R_example", "2024-11"

    monkeypatch.setattr(module, "_lookup_start", lookup)
    paths = backfill(
        "o", "r", start, end, mode="monthly", output_directory=tmp_path, token="test"
    )
    assert [(c[2], c[3]) for c in calls] == expected
    assert all(c[4]["discovery"] == "bounded" for c in calls)
    assert len(paths) == len(expected)
    assert lookups == ([("o", "r", "test")] if start is None else [])


def test_automatic_start_keeps_lookup_identity(tmp_path, collector, monkeypatch):
    monkeypatch.setattr(module, "_lookup_start", lambda *args: ("R_other", "2024-11"))
    with pytest.raises(ReportWriteError):
        backfill("o", "r", mode="monthly", output_directory=tmp_path)
    assert not list(tmp_path.iterdir())


def test_invalid_optional_end_before_lookup(tmp_path, collector, monkeypatch):
    def unexpected(*args):
        pytest.fail("must not look up repository")

    monkeypatch.setattr(module, "_lookup_start", unexpected)
    for end in ("2025-02", "bad"):
        with pytest.raises(ValueError):
            backfill("o", "r", end=end, mode="monthly", output_directory=tmp_path)


def test_bad_directory_before_automatic_lookup(tmp_path, collector, monkeypatch):
    (tmp_path / "2024-01.json").write_text("bad")

    def unexpected(*args):
        pytest.fail("must not look up repository")

    monkeypatch.setattr(module, "_lookup_start", unexpected)
    with pytest.raises(ReportWriteError):
        backfill("o", "r", mode="monthly", output_directory=tmp_path)


def test_creation_lookup_utc_month_and_client_cleanup(client):
    client._session.responses = [
        Response(
            {
                "data": {
                    "repository": {
                        "id": "R_example",
                        "createdAt": "2024-03-01T00:30:00+01:00",
                    }
                }
            }
        )
    ]
    assert module._lookup_start("o", "r", "test") == ("R_example", "2024-02")
    assert client._session.closed
    payload = client._session.calls[0][1]["json"]
    assert payload["operationName"] == "BackfillRepository"
    assert payload["variables"] == {"owner": "o", "name": "r"}
    assert "createdAt" in payload["query"]
    assert "rateLimit { cost }" in payload["query"]


@pytest.mark.parametrize(
    "item",
    [
        {},
        {"id": "R_example"},
        {"id": "R_example", "createdAt": None},
        {"id": "R_example", "createdAt": "bad"},
        {"id": "R_example", "createdAt": "2024-01-01T00:00:00"},
    ],
)
def test_invalid_creation_metadata_fails_and_closes_client(client, item):
    client._session.responses = [Response({"data": {"repository": item}})]
    with pytest.raises(GraphQLError):
        module._lookup_start("o", "r", "test")
    assert client._session.closed


def test_missing_repository_creation_fails(client):
    client._session.responses = [Response({"data": {"repository": None}})]
    with pytest.raises(RepositoryUnavailableError):
        module._lookup_start("o", "r", "test")
    assert client._session.closed

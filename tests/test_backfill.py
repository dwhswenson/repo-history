"""Full-history backfill defaults, validation, and replacement."""

import importlib
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from repo_history import RepositoryHistory, backfill, report_from_json, report_to_json

module = importlib.import_module("repo_history.backfill")
NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def collector(monkeypatch):
    calls = []
    snapshot = RepositoryHistory(
        "R_example",
        "example/project",
        "https://github.com/example/project",
        datetime(2024, 11, 1, tzinfo=timezone.utc),
        None,
        None,
        NOW,
        NOW,
        (),
        (),
        (),
    )

    def collect(owner, repo, **kwargs):
        calls.append((owner, repo, kwargs))
        return snapshot

    monkeypatch.setattr(module, "_collect_history", collect)
    monkeypatch.setattr(module, "_utc_now", lambda: NOW)
    return calls


def test_inclusive_range_and_replacement(tmp_path, collector):
    paths = backfill(
        "o", "r", "2023-12", "2024-03", output_directory=tmp_path, token="secret"
    )
    assert [p.stem for p in paths] == ["2023-12", "2024-01", "2024-02", "2024-03"]
    assert len(collector) == 1
    assert collector[0][2] == {
        "token": "secret",
        "expected_repository_id": None,
        "started": NOW,
    }
    before = {p: p.read_bytes() for p in paths}
    backfill("o", "r", "2024-02", "2024-02", output_directory=tmp_path)
    assert len(collector) == 2
    assert collector[-1][2]["expected_repository_id"] == "R_example"
    assert all(p.read_bytes() == value for p, value in before.items())
    assert (
        report_from_json(paths[2].read_text()).metadata.month_end
        - report_from_json(paths[2].read_text()).metadata.month_start
    ).days == 29


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
def test_invalid_range_before_collection(tmp_path, collector, start, end):
    with pytest.raises(ValueError):
        backfill("o", "r", start, end, output_directory=tmp_path)
    assert not collector
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("owner,repo", [("", "r"), ("o/r", "r"), ("o", " ")])
def test_invalid_repository(tmp_path, collector, owner, repo):
    with pytest.raises(ValueError):
        backfill(owner, repo, output_directory=tmp_path)
    assert not collector


@pytest.mark.parametrize("problem", ["broken", "mismatch", "mixed"])
def test_bad_destination_before_collection(tmp_path, collector, empty, problem):
    path = tmp_path / "2024-02.json"
    path.write_text(report_to_json(empty))
    if problem == "broken":
        path.write_text("broken")
    elif problem == "mismatch":
        path.rename(tmp_path / "2024-01.json")
    else:
        other = replace(
            empty,
            metadata=replace(
                empty.metadata,
                repository_node_id="R_other",
                month="2024-01",
                month_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                month_end=datetime(2024, 2, 1, tzinfo=timezone.utc),
            ),
        )
        (tmp_path / "2024-01.json").write_text(report_to_json(other))
    with pytest.raises(ValueError):
        backfill("o", "r", output_directory=tmp_path)
    assert not collector


@pytest.mark.parametrize(
    "start,end,expected",
    [
        (None, None, ["2024-11", "2024-12", "2025-01"]),
        (None, "2024-12", ["2024-11", "2024-12"]),
        ("2024-12", None, ["2024-12", "2025-01"]),
    ],
)
def test_automatic_endpoints(tmp_path, collector, start, end, expected):
    paths = backfill("o", "r", start, end, output_directory=tmp_path)
    assert [p.stem for p in paths] == expected
    assert len(collector) == 1


@pytest.mark.parametrize("option", [{"mode": "monthly"}, {"discovery": "bounded"}])
def test_removed_python_backfill_options(tmp_path, collector, option):
    with pytest.raises(TypeError):
        backfill("o", "r", output_directory=tmp_path, **option)
    assert not collector

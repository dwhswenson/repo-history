"""Full-history backfill defaults, validation, and replacement."""

import importlib
import io
from datetime import datetime, timezone

import pytest

from repo_history import (
    RepositoryHistory,
    backfill,
    report_from_json,
    report_to_json,
    state,
)
from repo_history._terminal import BackfillProgress

from .helpers import replace

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
    assert all(
        p.read_bytes() == value for p, value in before.items() if p.stem != "2024-02"
    )
    after = report_from_json((tmp_path / "2024-02.json").read_text())
    original = report_from_json(before[tmp_path / "2024-02.json"].decode())
    assert after.activities == original.activities
    assert (
        after.metadata.provenance.created_by == original.metadata.provenance.created_by
    )
    assert (
        after.metadata.provenance.last_updated_by.run_id
        != original.metadata.provenance.last_updated_by.run_id
    )
    assert (
        report_from_json(paths[2].read_text()).metadata.month_end
        - report_from_json(paths[2].read_text()).metadata.month_start
    ).days == 29


@pytest.mark.parametrize("failure", [None, "inventory", "verification", "publishing"])
def test_monthly_writing_completes_before_collection_state(
    tmp_path, collector, monkeypatch, failure
):
    monkeypatch.chdir(tmp_path)
    display = BackfillProgress(io.StringIO())
    write_report = module._write_report
    read_report = state.report_from_json
    temporary_file = state.tempfile.NamedTemporaryFile
    publish = state.os.replace
    observed = []
    checks = []
    state_writes = []

    def monthly(report, *args, **kwargs):
        path = tmp_path / "reports" / f"{report.metadata.month}.json"
        assert display.active == "Writing"
        assert display.detail == str(path)
        assert display.stages["Writing"].status == "running"
        assert display.stages["Inventory"].status == "pending"
        assert display.stages["Verification"].status == "pending"
        observed.append(path)
        return write_report(report, *args, **kwargs)

    def check_report(raw):
        assert display.stages["Writing"].status == "completed"
        assert display.stages["Writing"].completed == 3
        assert display.progress.tasks[display.tasks["Writing"]].finished
        assert all(path.exists() for path in observed)
        if len(checks) < 3:
            assert display.active == "Inventory"
            assert not display.phase_running
            assert display.stages["Inventory"].status == "running"
            assert display.stages["Inventory"].completed == len(checks)
            assert display.stages["Inventory"].total == 3
            assert display.stages["Verification"].status == "pending"
        else:
            assert display.stages["Inventory"].status == "completed"
            assert display.stages["Inventory"].completed == 3
            assert display.progress.tasks[display.tasks["Inventory"]].finished
            assert display.active == "Verification"
            assert not display.phase_running
            assert display.stages["Verification"].status == "running"
            assert display.stages["Verification"].completed == len(checks) - 3
            assert display.stages["Verification"].total == 3
        checks.append(display.detail)
        if failure == "inventory" and len(checks) == 2:
            raise OSError("state inventory failed")
        if failure == "verification" and len(checks) == 5:
            raise OSError("state verification failed")
        return read_report(raw)

    def write_state_file(*args, **kwargs):
        if kwargs.get("prefix") == ".collection-state.":
            assert display.active == "Collection state"
            assert display.phase_running
            assert display.stages["Inventory"].status == "completed"
            assert display.stages["Verification"].status == "pending"
            assert display.stages["Verification"].completed == 0
            assert (
                display.detail == f"Writing {tmp_path / 'reports' / state.STATE_FILE}"
            )
            state_writes.append(display.detail)
        return temporary_file(*args, **kwargs)

    def publish_state(source, target):
        if target.name == state.STATE_FILE:
            assert display.active == "Collection state"
            assert display.phase_running
            assert display.stages["Verification"].status == "completed"
            assert display.stages["Verification"].completed == 3
            assert display.progress.tasks[display.tasks["Verification"]].finished
            if failure == "publishing":
                raise OSError("state publishing failed")
        return publish(source, target)

    monkeypatch.setattr(module, "_write_report", monthly)
    monkeypatch.setattr(state, "report_from_json", check_report)
    monkeypatch.setattr(state.tempfile, "NamedTemporaryFile", write_state_file)
    monkeypatch.setattr(state.os, "replace", publish_state)

    def run():
        with display:
            backfill("o", "r", output_directory="reports")

    if failure:
        with pytest.raises(OSError, match=f"state {failure} failed"):
            run()
    else:
        run()
    assert len(observed) == 3
    expected = [
        f"{operation}: report {index}/3: {path}"
        for operation in (
            "Building inventory",
            "Verifying saved state",
        )
        for index, path in enumerate(observed, 1)
    ]
    checked = {"inventory": 2, "verification": 5}.get(failure, 6)
    assert checks == expected[:checked]
    assert len(state_writes) == (0 if failure == "inventory" else 1)
    assert "Collection state" not in display.tasks
    assert display.stages["Writing"].status == "completed"
    assert display.stages["Inventory"].status == (
        "failed" if failure == "inventory" else "completed"
    )
    assert display.stages["Inventory"].completed == (1 if failure == "inventory" else 3)
    assert display.stages["Verification"].status == (
        "pending"
        if failure == "inventory"
        else "failed"
        if failure == "verification"
        else "completed"
    )
    assert display.stages["Verification"].completed == (
        0 if failure == "inventory" else 1 if failure == "verification" else 3
    )
    assert display.active == (
        "Inventory"
        if failure == "inventory"
        else "Verification"
        if failure == "verification"
        else "Collection state"
        if failure == "publishing"
        else "Completed"
    )
    assert not display.phase_running
    assert not list((tmp_path / "reports").glob("*.tmp"))


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

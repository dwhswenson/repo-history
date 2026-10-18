"""Provenance, multi-repository wire contracts, and durable baseline state."""

import copy
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

import repo_history as api
from repo_history.contracts import contract_validator
from repo_history.manifest import _sum_summaries, validate_manifest
from repo_history.provenance import make_run
from repo_history.serialization import to_data
from repo_history.state import (
    STATE_FILE,
    verify_collection_state,
    write_collection_state,
)

from .test_history import NOW
from .test_history import session as session


def read_state(directory):
    return json.loads((directory / STATE_FILE).read_text())


def test_full_backfill_establishes_boundary_and_baseline(session, tmp_path):
    paths = api.backfill("o", "r", output_directory=tmp_path, token="test")
    reports = [api.report_from_json(p.read_text()) for p in paths]
    state = read_state(tmp_path)
    api.validate_collection_state(state)
    verify_collection_state(state, tmp_path)
    assert state["checkpoint"] is not None
    assert state["checkpoint"]["report_start_month"] == "2023-12"
    assert state["checkpoint"]["report_end_month"] == "2024-04"
    assert reports[0].metadata.repository.history_start_month == "2023-12"
    assert reports[0].metadata.repository.created_at.strftime("%Y-%m") == "2024-01"
    assert len({r.metadata.provenance.created_by.run_id for r in reports}) == 1
    assert reports[-1].metadata.ongoing
    assert reports[-1].summaries.repository_counts == dict.fromkeys(
        api.ACTIVITY_TYPES, 0
    )
    run = reports[0].metadata.provenance.created_by
    assert run.command == "backfill" and run.requested_start_month is None
    assert run.collection_since is run.collection_until is None
    # No network access is needed to generate or validate the new manifest.
    count = len(session.calls)
    manifest = api.build_manifest(reports, display_name="Selected projects")
    validate_manifest(manifest)
    assert manifest["display_name"] == "Selected projects"
    assert len(session.calls) == count


@pytest.mark.parametrize("start,end", [("2024-02", "2024-03"), (None, "2024-03")])
def test_partial_backfill_stores_evidence_but_no_checkpoint(
    session, tmp_path, start, end
):
    paths = api.backfill("o", "r", start, end, output_directory=tmp_path, token="test")
    report = api.report_from_json(paths[0].read_text())
    assert report.metadata.repository.history_start_month == "2023-12"
    assert report.metadata.provenance.created_by.requested_start_month == start
    assert report.metadata.provenance.created_by.requested_end_month == end
    assert read_state(tmp_path)["checkpoint"] is None


@pytest.mark.parametrize("change", ["changed", "missing", "extra"])
def test_state_inventory_detects_tampering(session, tmp_path, change):
    paths = api.backfill("o", "r", output_directory=tmp_path, token="test")
    state = read_state(tmp_path)
    if change == "changed":
        paths[0].write_text(paths[0].read_text() + "\n")
    elif change == "missing":
        paths[0].unlink()
    else:
        report = api.report_from_json(paths[0].read_text())
        metadata = replace(
            report.metadata,
            month="2023-11",
            month_start=report.metadata.month_start.replace(month=11),
            month_end=report.metadata.month_start,
        )
        api.write_report(api.build_report(metadata, [], []), tmp_path)
    with pytest.raises(api.ReportValidationError, match="inventory"):
        verify_collection_state(state, tmp_path)


def test_rebuild_preserves_creator_and_changes_latest_run(session, tmp_path):
    paths = api.backfill("o", "r", output_directory=tmp_path, token="test")
    before = api.report_from_json(paths[0].read_text())
    api.backfill(
        "o", "r", "2023-12", "2023-12", output_directory=tmp_path, token="test"
    )
    after = api.report_from_json(paths[0].read_text())
    assert after.metadata.provenance.created_by == before.metadata.provenance.created_by
    assert (
        after.metadata.provenance.last_updated_by
        != before.metadata.provenance.last_updated_by
    )
    assert (
        after.metadata.provenance.last_updated_by
        == after.metadata.provenance.last_rebuilt_by
    )
    assert read_state(tmp_path)["checkpoint"] is None


def test_old_month_write_invalidates_checkpoint_and_retains_boundary(session, tmp_path):
    paths = api.backfill("o", "r", output_directory=tmp_path, token="test")
    before = api.report_from_json(paths[1].read_text())
    updated = make_run(
        "update",
        NOW + timedelta(days=1),
        NOW + timedelta(days=1),
        since=before.metadata.month_start,
        until=before.metadata.month_end,
        parents="bounded",
        commits="bounded",
    )
    metadata = replace(
        before.metadata,
        repository=replace(
            before.metadata.repository,
            history_start_month=None,
            history_start_established_by=None,
        ),
        provenance=api.Provenance(updated, updated, updated),
        coverage=api.Coverage(updated.started_at, ("bounded",)),
    )
    api.write_report(replace(before, metadata=metadata), tmp_path)
    saved = api.report_from_json(paths[1].read_text())
    assert saved.metadata.repository.history_start_month == "2023-12"
    assert saved.metadata.provenance.created_by == before.metadata.provenance.created_by
    state = read_state(tmp_path)
    assert state["checkpoint"] is None
    verify_collection_state(state, tmp_path)


def test_failure_does_not_advance_state(session, tmp_path, monkeypatch):
    import repo_history.state as module

    paths = api.backfill("o", "r", output_directory=tmp_path, token="test")
    original = (tmp_path / STATE_FILE).read_bytes()
    actual = module.os.replace

    def fail(source, target):
        if Path(target).name == STATE_FILE:
            raise OSError("state write failed")
        return actual(source, target)

    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError, match="state write failed"):
        api.backfill("o", "r", output_directory=tmp_path, token="test")
    assert (tmp_path / STATE_FILE).read_bytes() == original
    with pytest.raises(api.ReportValidationError, match="inventory"):
        verify_collection_state(json.loads(original), tmp_path)
    assert not list(tmp_path.glob("*.tmp"))
    assert all(p.exists() for p in paths)


def test_unmaterialized_future_activity_prevents_baseline(session, tmp_path):
    snapshot = api.collect_history("o", "r", token="test")
    run = make_run(
        "backfill",
        snapshot.collection_started_at,
        snapshot.collection_finished_at,
        parents="full",
        commits="full",
    )
    future = replace(snapshot.activities[0], occurred_at=NOW.replace(year=2025))
    snapshot = replace(snapshot, run=run, activities=(*snapshot.activities, future))
    for report in api.history_to_months(snapshot):
        api.write_report(report, tmp_path)
    write_collection_state(tmp_path, run, history=snapshot)
    assert read_state(tmp_path)["checkpoint"] is None


def test_deleted_first_report_cannot_erase_history_evidence(session, tmp_path):
    paths = api.backfill("o", "r", output_directory=tmp_path, token="test")
    paths[0].unlink()
    manifest = api.build_manifest(
        api.report_from_json(p.read_text()) for p in paths[1:]
    )
    assert manifest["repositories"][0]["history_start_month"] == "2023-12"


def multi_manifest(mixed):
    """A prepared aggregate fixture, not an exposed combining implementation."""
    value = api.build_manifest([mixed], display_name="Two repositories")
    second = copy.deepcopy(value["months"][0]["source_reports"][0])
    second["metadata"]["repository"]["node_id"] = "R_second"
    second["metadata"]["repository"]["full_name"] = "another/project"
    second["metadata"]["repository"]["url"] = "https://github.com/another/project"
    value["repositories"].append(second["metadata"]["repository"])
    value["months"][0]["source_reports"].append(second)
    value["months"][0]["summaries"] = _sum_summaries(
        [s["summaries"] for s in value["months"][0]["source_reports"]], value["actors"]
    )
    return value


def test_multi_repository_contract_sums_events_and_unions_people(mixed):
    value = multi_manifest(mixed)
    validate_manifest(value)
    summary = value["months"][0]["summaries"]
    assert (
        summary["repository_counts"]["commit"]
        == mixed.summaries.repository_counts["commit"] * 2
    )
    assert summary["distinct_actors"]["total"] == mixed.summaries.distinct_actors.total


@pytest.mark.parametrize(
    "problem",
    ["aggregate", "duplicate", "unknown", "missing", "creation", "run", "ongoing"],
)
def test_invalid_multi_repository_contracts(mixed, problem):
    value = multi_manifest(mixed)
    month = value["months"][0]
    if problem == "aggregate":
        month["summaries"]["repository_counts"]["commit"] += 1
    elif problem == "duplicate":
        month["source_reports"].append(copy.deepcopy(month["source_reports"][0]))
    elif problem == "unknown":
        month["source_reports"][0]["metadata"]["repository"]["node_id"] = "unknown"
    elif problem == "missing":
        month["source_reports"].pop()
    elif problem == "creation":
        month["source_reports"][0]["metadata"]["repository"]["created_at"] = (
            "2019-01-01T00:00:00Z"
        )
    elif problem == "run":
        month["source_reports"][1]["metadata"]["provenance"]["created_by"][
            "collector_version"
        ] = "conflict"
    else:
        month["metadata"]["ongoing"] = True
    with pytest.raises(api.ReportValidationError):
        validate_manifest(value)


def test_multi_repository_pre_history_zero_requires_evidence(mixed):
    value = api.build_manifest([mixed])
    run = copy.deepcopy(
        value["months"][0]["source_reports"][0]["metadata"]["provenance"]["created_by"]
    )
    run.update(
        collection_since=None,
        collection_until=None,
        parent_discovery="full",
        commit_history="full",
        run_id="a0000000-0000-4000-8000-000000000001",
    )
    value["repositories"].append(
        {
            "node_id": "R_later",
            "full_name": "o/later",
            "url": "https://github.com/o/later",
            "created_at": "2024-03-01T00:00:00Z",
            "history_start_month": "2024-03",
            "history_start_established_by": run,
        }
    )
    validate_manifest(value)
    value["repositories"][1].update(
        history_start_month=None, history_start_established_by=None
    )
    with pytest.raises(api.ReportValidationError, match="missing report"):
        validate_manifest(value)


def test_new_contract_rejects_old_metadata(mixed):
    value = to_data(mixed)
    value["metadata"]["collection_started_at"] = value["metadata"]["coverage"]["as_of"]
    with pytest.raises(api.ReportValidationError):
        api.report_from_json(json.dumps(value))
    for name in ("monthly-report", "viz-manifest", "collection-state"):
        assert contract_validator(name).schema["properties"]["schema_version"] == {
            "const": "1"
        }


def test_incremental_provenance_keeps_rebuild_and_closes_month(mixed):
    p = mixed.metadata.provenance
    run = replace(
        p.last_updated_by,
        run_id="b0000000-0000-4000-8000-000000000001",
        mode="incremental",
        command="update",
        started_at=NOW,
        finished_at=NOW,
    )
    meta = replace(
        mixed.metadata,
        provenance=replace(p, last_updated_by=run),
        coverage=api.Coverage(NOW),
        ongoing=False,
    )
    assert (
        api.report_from_json(
            api.report_to_json(replace(mixed, metadata=meta))
        ).metadata.provenance.last_rebuilt_by
        == p.last_rebuilt_by
    )


def test_shared_contract_examples(mixed):
    fixtures = Path(__file__).parent / "fixtures"
    multi = json.loads((fixtures / "multi-repo-manifest-v1.json").read_text())
    assert multi == multi_manifest(mixed)
    validate_manifest(multi)
    state = json.loads((fixtures / "collection-state-v1.json").read_text())
    api.validate_collection_state(state)


def test_state_contract_accepts_future_incremental_progress(session, tmp_path):
    api.backfill("o", "r", output_directory=tmp_path, token="test")
    state = read_state(tmp_path)
    applied = copy.deepcopy(state["last_applied_run"])
    applied.update(
        run_id="c0000000-0000-4000-8000-000000000001",
        mode="incremental",
        command="update",
        started_at="2024-04-16T00:00:00Z",
        finished_at="2024-04-16T00:05:00Z",
    )
    state["last_applied_run"] = applied
    for source in ("issues", "pull_requests", "commits"):
        state["checkpoint"][source]["checked_through"] = applied["started_at"]
    api.validate_collection_state(state)
    state["checkpoint"]["issues"]["checked_through"] = "2024-04-17T00:00:00Z"
    with pytest.raises(api.ReportValidationError, match="observation"):
        api.validate_collection_state(state)


def test_inactive_repository_baseline_uses_creation_month(session, tmp_path):
    from .github import connection
    from .helpers import repository

    session.repository = repository(
        createdAt="2024-03-01T00:00:00Z",
        hasIssuesEnabled=True,
        isEmpty=True,
        defaultBranchRef=None,
    )
    session.add("RepositoryIssues", "R_example", connection("issues"))
    session.add("RepositoryPullRequests", "R_example", connection("pullRequests"))
    paths = api.backfill("o", "r", output_directory=tmp_path, token="test")
    assert [p.stem for p in paths] == ["2024-03", "2024-04"]
    state = read_state(tmp_path)
    assert state["repository"]["history_start_month"] == "2024-03"
    assert state["checkpoint"]["commits"]["default_branch_head"] is None
    assert all(
        not any(
            api.report_from_json(p.read_text()).summaries.repository_counts.values()
        )
        for p in paths
    )


@pytest.mark.parametrize("problem", ["bounds", "history", "provenance", "evidence"])
def test_invalid_metadata_facts_rejected(mixed, problem):
    data = to_data(mixed)
    m = data["metadata"]
    if problem == "bounds":
        for run in m["provenance"].values():
            run["collection_until"] = run["collection_since"]
    elif problem == "history":
        m["repository"]["history_start_month"] = "2024-02"
    elif problem == "provenance":
        m["provenance"]["last_updated_by"]["mode"] = "incremental"
    else:
        run = copy.deepcopy(m["provenance"]["created_by"])
        m["repository"].update(
            history_start_month="2020-01", history_start_established_by=run
        )
    with pytest.raises(api.ReportValidationError):
        api.report_from_json(json.dumps(data))

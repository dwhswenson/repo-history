"""Repository state bound to the exact monthly bytes successfully published."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .contracts import (
    choose_repository,
    load_contract,
    month_bounds,
    require,
    validate_repository,
    validate_run,
    validate_structure,
)
from .serialization import parse_time, report_from_json, to_data

STATE_FILE = "collection-state.json"


def load_collection_state_schema():
    return load_contract("collection-state")


def validate_collection_state(data):
    validate_structure("collection-state", data)
    runs = {}
    validate_repository(data["repository"], "$.repository", runs)
    validate_run(data["last_applied_run"], "$.last_applied_run", runs)
    for month in data["reports"]:
        month_bounds(month)
    cp = data["checkpoint"]
    if cp is None:
        return
    baseline = cp["baseline_run"]
    validate_run(baseline, "$.checkpoint.baseline_run", runs)
    applied = data["last_applied_run"]
    require(
        baseline == applied
        or (
            applied["mode"] == "incremental"
            and parse_time(baseline["started_at"]) <= parse_time(applied["started_at"])
        ),
        "$.checkpoint",
        "checkpoint must derive from its baseline or a subsequent incremental run",
    )
    require(
        baseline["mode"] == "rebuild"
        and baseline["collection_since"] is None
        and baseline["parent_discovery"] == baseline["commit_history"] == "full",
        "$.checkpoint",
        "checkpoint requires full-history rebuild",
    )
    first, last = cp["report_start_month"], cp["report_end_month"]
    month_bounds(first)
    month_bounds(last)
    boundary = data["repository"]["history_start_month"]
    current = applied["started_at"][:7]
    require(
        boundary is not None and first <= boundary and last == current,
        "$.checkpoint",
        "checkpoint must cover full saved history through collection month",
    )
    expected = []
    month = first
    while month <= last:
        expected.append(month)
        month = month_bounds(month)[1].strftime("%Y-%m")
    require(
        sorted(data["reports"]) == expected,
        "$.checkpoint",
        "checkpoint inventory must cover every month",
    )
    for source in ("issues", "pull_requests", "commits"):
        item = cp[source]
        require(
            parse_time(baseline["started_at"])
            <= parse_time(item["checked_through"])
            <= parse_time(applied["finished_at"]),
            "$.checkpoint",
            "invalid checkpoint observation time",
        )
        if source != "commits":
            require(
                parse_time(baseline["started_at"])
                <= parse_time(item["last_full_scan_at"])
                <= parse_time(item["checked_through"]),
                "$.checkpoint",
                "invalid full scan observation time",
            )
        if applied == baseline:
            require(
                item["checked_through"] == baseline["started_at"],
                "$.checkpoint",
                "checkpoint cutoff disagrees with baseline",
            )
    commits = cp["commits"]
    require(
        (commits["default_branch"] is None) == (commits["default_branch_head"] is None),
        "$.checkpoint.commits",
        "branch and head must both be present or null",
    )


def _inventory(directory):
    from .writing import MONTHLY_FILE

    records, inventory = [], {}
    for path in sorted(directory.iterdir()):
        if not MONTHLY_FILE.fullmatch(path.name) or path.is_dir():
            continue
        raw = path.read_bytes()
        report = report_from_json(raw.decode("utf-8"))
        require(
            report.metadata.month == path.stem,
            str(path),
            "filename does not match report month",
        )
        inventory[path.stem] = hashlib.sha256(raw).hexdigest()
        records.append(report)
    return records, inventory


def verify_collection_state(data, directory):
    """Reject missing, added, or changed reports and mismatched baseline metadata."""
    validate_collection_state(data)
    records, inventory = _inventory(Path(directory))
    require(
        inventory == data["reports"],
        "$.reports",
        "collection state does not match report inventory",
    )
    repo = choose_repository(r.metadata.repository for r in records)
    require(
        to_data(repo) == data["repository"],
        "$.repository",
        "state repository disagrees with reports",
    )
    if data["checkpoint"] is not None:
        run = data["last_applied_run"]
        baseline = data["checkpoint"]["baseline_run"]
        require(
            all(
                parse_time(baseline["started_at"])
                <= r.metadata.provenance.last_rebuilt_by.started_at
                and r.metadata.provenance.last_updated_by.finished_at
                <= parse_time(data["last_applied_run"]["finished_at"])
                for r in records
            ),
            "$.checkpoint",
            "baseline not applied to every report",
        )
        if run == baseline:
            require(
                all(
                    to_data(r.metadata.provenance.last_updated_by) == run
                    for r in records
                ),
                "$.checkpoint",
                "baseline not applied to every report",
            )
            head = data["checkpoint"]["commits"]
            require(
                all(
                    r.metadata.default_branch == head["default_branch"]
                    and r.metadata.default_branch_head == head["default_branch_head"]
                    for r in records
                ),
                "$.checkpoint",
                "baseline head disagrees with reports",
            )


def write_collection_state(directory, run, *, history=None):
    directory = Path(directory)
    records, inventory = _inventory(directory)
    require(bool(records), "$.reports", "state requires monthly reports")
    repo = choose_repository(r.metadata.repository for r in records)
    raw_run = to_data(run)
    data = {
        "schema_version": "1",
        "repository": to_data(repo),
        "last_applied_run": raw_run,
        "reports": inventory,
        "checkpoint": None,
    }
    first, last = min(inventory), max(inventory)
    eligible = (
        history is not None
        and run.collection_since is None
        and run.parent_discovery == run.commit_history == "full"
        and repo.history_start_month is not None
        and first <= repo.history_start_month
        and last == run.started_at.strftime("%Y-%m")
        and all(r.metadata.provenance.last_updated_by == run for r in records)
        and not any(a.occurred_at >= month_bounds(last)[1] for a in history.activities)
    )
    if eligible:
        expected = []
        month = first
        while month <= last:
            expected.append(month)
            month = month_bounds(month)[1].strftime("%Y-%m")
        if sorted(inventory) == expected:
            stamp = raw_run["started_at"]
            data["checkpoint"] = {
                "baseline_run": raw_run,
                "report_start_month": first,
                "report_end_month": last,
                "issues": {"checked_through": stamp, "last_full_scan_at": stamp},
                "pull_requests": {"checked_through": stamp, "last_full_scan_at": stamp},
                "commits": {
                    "checked_through": stamp,
                    "default_branch": history.default_branch,
                    "default_branch_head": history.default_branch_head,
                },
            }
    verify_collection_state(data, directory)
    path = directory / STATE_FILE
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=".collection-state.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
            stream.flush()
        verify_collection_state(json.loads(temporary.read_text()), directory)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path

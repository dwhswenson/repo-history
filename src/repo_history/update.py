"""Checkpoint-backed historical reconciliation and explicitly bounded refreshes."""

import json
import logging
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from .collect import _parse_month, _run_collect
from .collection import _month_bounds, _Records
from .history import _collect_snapshot, _history_to_months
from .provenance import make_run
from .serialization import parse_time
from .state import (
    STATE_FILE,
    _inventory,
    verify_collection_state,
    write_collection_state,
)
from .writing import MONTHLY_FILE, _check_directory, _write_report, directory_repository

LOG = logging.getLogger(__name__)


def _prepare_update(
    directory: Path, start: str | None
) -> tuple[str | None, str | None]:
    """Validate the destination before inferring an explicitly bounded start."""
    repository_id = _check_directory(directory)
    if start is None and directory.exists():
        start = max(
            (
                path.stem
                for path in directory.iterdir()
                if MONTHLY_FILE.fullmatch(path.name) and not path.is_dir()
            ),
            default=None,
        )
    return start, repository_id


def _checkpoint(directory, now):
    """An unusable checkpoint requires a rebuild, never a guessed cutoff."""
    try:
        data = json.loads((directory / STATE_FILE).read_text())
        verify_collection_state(data, directory)
        if data["checkpoint"] is None:
            return None
        if parse_time(data["last_applied_run"]["started_at"]) > now:
            return None
        return data["checkpoint"]
    except (OSError, ValueError, UnicodeError) as exc:
        LOG.info("No verified checkpoint: %s", exc)
        return None


def _reconcile(history, previous, refreshed):
    records = _Records()
    old_commits = set()
    for report in previous.values():
        for actor in report.actors:
            records.add_actor(actor, "UpdateReconciliation")
        for activity in report.activities:
            if activity.type == "commit":
                old_commits.add(activity.id)
            elif activity.subject.node_id not in refreshed:
                records.add(activity, (), "UpdateReconciliation")
    for actor in history.actors:
        records.add_actor(actor, "UpdateReconciliation")
    for activity in history.activities:
        records.add(activity, (), "UpdateReconciliation")
    new_commits = {a.id for a in history.activities if a.type == "commit"}
    LOG.info(
        "Reconciled commits: added=%s removed=%s; refreshed parents=%s",
        len(new_commits - old_commits),
        len(old_commits - new_commits),
        len(refreshed),
    )
    return replace(
        history,
        activities=tuple(records.activities.values()),
        actors=tuple(records.actors.values()),
    )


def _run_update(
    owner,
    repo,
    start,
    end,
    *,
    output_directory,
    expected_repository_id,
    now,
    on_written=None,
    token=None,
    unrestricted=False,
):
    """Reconcile a verified baseline, or replace a strictly selected range."""
    directory = Path(output_directory)
    checkpoint = _checkpoint(directory, now) if unrestricted else None
    if (unrestricted and checkpoint is None) or start is None:
        LOG.info("Rebuilding full history to establish a verified baseline")
        return _run_collect(
            owner,
            repo,
            None,
            end,
            output_directory=directory,
            now=now,
            token=token,
            on_written=on_written,
            command="update",
        )

    previous = {}
    refreshed = set()
    boundary = None
    if unrestricted:
        reports, _ = _inventory(directory)
        previous = {r.metadata.month: r for r in reports}
        cutoff = min(
            parse_time(checkpoint[source]["checked_through"])
            for source in ("issues", "pull_requests")
        )
        # Retain the overlap locally as well as in the REST query. At the
        # representable minimum select full discovery before querying.
        try:
            first = cutoff - timedelta(seconds=1)
        except OverflowError:
            first = cutoff
        boundary = _month_bounds(
            *_parse_month(
                previous[min(previous)].metadata.repository.history_start_month
            )
        )[0]
        LOG.info(
            "Discovering parent changes since %s (with overlap)", first.isoformat()
        )
    else:
        first = _month_bounds(*_parse_month(start))[0]
        LOG.info("Updating %s/%s %s through %s", owner, repo, start, end)
    after = _month_bounds(*_parse_month(end))[1]
    history, parents, commits = _collect_snapshot(
        owner,
        repo,
        token=token,
        expected_repository_id=expected_repository_id,
        started=now,
        start=first,
        end=after,
        refresh_parents=True,
        refreshed_parents=refreshed,
        history_boundary=boundary,
    )
    if unrestricted and parents == "full":
        # The collector widened discovery for earlier history (or minimum dates).
        return _run_collect(
            owner,
            repo,
            None,
            end,
            output_directory=directory,
            now=now,
            token=token,
            on_written=on_written,
            command="update",
            snapshot=history,
        )
    run = make_run(
        "update",
        history.collection_started_at,
        history.collection_finished_at,
        parents=parents,
        commits=commits,
        requested_start=None if unrestricted else start,
        requested_end=None if unrestricted else end,
    )
    if unrestricted:
        run = replace(run, mode="incremental")
        history = _reconcile(history, previous, refreshed)
    history = replace(history, run=run)
    reports = _history_to_months(
        history,
        min(previous) if unrestricted else start,
        end,
        parent_discovery=parents,
        commit_history=commits,
        previous_reports=previous,
    )
    if unrestricted:
        reports = [
            report
            for report in reports
            if report.metadata.month >= checkpoint["report_end_month"]
            or report.activities != previous[report.metadata.month].activities
            or report.actors != previous[report.metadata.month].actors
        ]
        LOG.info(
            "Affected report months: %s", ", ".join(r.metadata.month for r in reports)
        )
    prior_repository = directory_repository(directory)
    paths = []
    for report in reports:
        path = _write_report(report, directory, previous_repository=prior_repository)
        paths.append(path)
        if on_written is not None:
            on_written(path)
    write_collection_state(
        directory,
        run,
        history=history if unrestricted else None,
        checkpoint=checkpoint if unrestricted else None,
    )
    return paths

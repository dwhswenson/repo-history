"""Internal update orchestration and filename-based start selection."""

import logging
from pathlib import Path

from .backfill import _parse_month, _run_backfill
from .collection import _month_bounds
from .history import _collect_snapshot, _history_to_months
from .writing import MONTHLY_FILE, _check_directory, _write_report

LOG = logging.getLogger(__name__)


def _prepare_update(
    directory: Path, start: str | None
) -> tuple[str | None, str | None]:
    """Validate the destination before using filenames to select a start."""
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


def _run_update(
    owner,
    repo,
    start,
    end,
    *,
    output_directory,
    expected_repository_id,
    now,
    mode="range",
    discovery="bounded",
    on_written=None,
    token=None,
):
    """Update a prevalidated destination and resolved inclusive range."""
    if start is None or mode == "monthly":
        fallback_mode = "monthly" if mode == "monthly" else "history"
        if start is None:
            LOG.info(
                "No monthly reports found; falling back to %s backfill", fallback_mode
            )
        return _run_backfill(
            owner,
            repo,
            start,
            end,
            output_directory=output_directory,
            mode=fallback_mode,
            discovery=discovery if fallback_mode == "monthly" else None,
            now=now,
            token=token,
            on_written=on_written,
        )
    first = _month_bounds(*_parse_month(start))[0]
    after = _month_bounds(*_parse_month(end))[1]
    LOG.info("Updating %s/%s %s through %s", owner, repo, start, end)
    history, parents, commits = _collect_snapshot(
        owner,
        repo,
        token=token,
        expected_repository_id=expected_repository_id,
        started=now,
        start=first,
        end=after,
        discovery=discovery,
    )
    reports = _history_to_months(
        history,
        start,
        end,
        parent_discovery=parents,
        commit_history=commits,
    )
    paths = []
    for report in reports:
        path = _write_report(report, output_directory)
        paths.append(path)
        if on_written is not None:
            on_written(path)
    return paths

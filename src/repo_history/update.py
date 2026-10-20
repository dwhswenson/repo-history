"""Internal update orchestration and filename-based start selection."""

import logging
from dataclasses import replace
from pathlib import Path

from .collect import _parse_month, _run_collect
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
    on_written=None,
    token=None,
):
    """Update a prevalidated destination and resolved inclusive range."""
    if start is None:
        LOG.info("No monthly reports found; collecting all history")
        return _run_collect(
            owner,
            repo,
            start,
            end,
            output_directory=output_directory,
            now=now,
            token=token,
            on_written=on_written,
            command="update",
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
    )
    from .provenance import make_run
    from .state import write_collection_state

    run = make_run(
        "update",
        history.collection_started_at,
        history.collection_finished_at,
        since=first,
        until=after,
        parents=parents,
        commits=commits,
        requested_start=start,
        requested_end=end,
    )
    history = replace(history, run=run)
    reports = _history_to_months(
        history,
        start,
        end,
        parent_discovery=parents,
        commit_history=commits,
    )
    from .writing import directory_repository

    prior_repository = directory_repository(Path(output_directory))
    paths = []
    for report in reports:
        path = _write_report(
            report, output_directory, previous_repository=prior_repository
        )
        paths.append(path)
        if on_written is not None:
            on_written(path)
    write_collection_state(output_directory, run)
    return paths

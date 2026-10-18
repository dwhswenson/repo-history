"""Collect full history once and write monthly reports."""

import logging
import os
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from ._progress import emit
from ._timing import backfill_timing, timed_phase
from .collection import _month_bounds
from .history import _collect_history, history_to_months
from .writing import _check_directory, _write_report

LOG = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_month(value: str) -> tuple[int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
        raise ValueError("month must use YYYY-MM")
    year, month = map(int, value.split("-"))
    _month_bounds(year, month)
    return year, month


def _validate_request(owner: str, repo: str) -> None:
    if any(
        not isinstance(value, str) or not value.strip() or "/" in value
        for value in (owner, repo)
    ):
        raise ValueError("owner and repo must be nonempty names without slashes")


def _validate_range(start: str, end: str, now: datetime) -> tuple[int, int]:
    first = _parse_month(start)
    last = _parse_month(end)
    if first > last:
        raise ValueError("start month must not follow end month")
    if _month_bounds(*last)[0] > now:
        raise ValueError("future month")
    return first


def _validate_optional_range(start: str | None, end: str | None, now: datetime) -> str:
    end = f"{now.year:04d}-{now.month:02d}" if end is None else end
    _validate_range(end if start is None else start, end, now)
    return end


def _run_backfill(
    owner: str,
    repo: str,
    start: str | None = None,
    end: str | None = None,
    *,
    output_directory: str | os.PathLike,
    token: str | None = None,
    on_written: Callable[[Path], None] | None = None,
    now: datetime | None = None,
    command: str = "backfill",
) -> list[Path]:
    with backfill_timing(LOG):
        with timed_phase(LOG, "Backfill preparation"):
            _validate_request(owner, repo)
            now = _utc_now() if now is None else now
            requested_end = end
            end = _validate_optional_range(start, end, now)
            emit("phase", stage="Preparing", detail="Validating destination")
            repository_id = _check_directory(Path(output_directory))
            emit("phase", stage="Preparing", detail="Looking up repository")
        history = _collect_history(
            owner, repo, token=token, expected_repository_id=repository_id, started=now
        )
        with timed_phase(LOG, "History grouping/writing monthly"):
            timer = perf_counter()
            from .provenance import make_run
            from .state import write_collection_state
            from .writing import directory_repository

            run = make_run(
                command,
                history.collection_started_at,
                history.collection_finished_at,
                parents="full",
                commits="full",
                requested_start=start,
                requested_end=requested_end,
            )
            history = replace(history, run=run)
            prior_repository = directory_repository(Path(output_directory))
            paths = []
            emit(
                "start",
                stage="Grouping",
                detail="Grouping and validating monthly reports",
            )
            effective_start = start
            if (
                start is None
                and prior_repository is not None
                and prior_repository.history_start_month is not None
            ):
                earliest = min(
                    [
                        history.repository_created_at,
                        *(a.occurred_at for a in history.activities),
                    ]
                )
                effective_start = min(
                    prior_repository.history_start_month,
                    f"{earliest.year:04d}-{earliest.month:02d}",
                )
            reports = history_to_months(history, effective_start, end)
            emit(
                "complete",
                stage="Grouping",
                detail=f"Validated {len(reports)} monthly reports",
            )
            # All selected reports have been validated before any replacement.
            LOG.debug(
                "History grouped months=%s elapsed_seconds=%.3f",
                len(reports),
                perf_counter() - timer,
            )
            emit(
                "start",
                stage="Writing",
                detail=f"Writing {len(reports)} monthly reports",
            )
            emit("total", stage="Writing", total=len(reports))
            destination = Path(output_directory).absolute()
            for report in reports:
                emit(
                    "detail", detail=str(destination / f"{report.metadata.month}.json")
                )
                path = _write_report(
                    report, output_directory, previous_repository=prior_repository
                )
                paths.append(path)
                emit("advance", stage="Writing")
                if on_written is not None:
                    on_written(path)
            emit(
                "complete",
                stage="Writing",
                detail=f"Saved {len(paths)} monthly reports",
            )
        with timed_phase(LOG, "History inventory/verification"):
            write_collection_state(output_directory, run, history=history)
            emit(
                "finish",
                stage="Completed",
                detail=f"Saved {len(paths)} monthly reports",
            )
        return paths


def backfill(
    owner: str,
    repo: str,
    start: str | None = None,
    end: str | None = None,
    *,
    output_directory: str | os.PathLike,
    token: str | None = None,
) -> list[Path]:
    """Collect and replace each month in an inclusive YYYY-MM range.

    Collect full history once, then group offline. Omitted start
    includes activity before repository creation; omitted end uses the current
    UTC month at invocation. All selected reports validate before writing.

    Return saved paths chronologically. The first error propagates; previously
    saved months remain usable. Existing monthly files must all be valid and
    belong to the same repository. Current UTC month is allowed; future months
    are rejected before any network request.
    """
    return _run_backfill(
        owner,
        repo,
        start,
        end,
        output_directory=output_directory,
        token=token,
    )

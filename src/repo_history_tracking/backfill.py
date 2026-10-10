"""Chronological monthly collection and writing without rollback or caching."""

import logging
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .collection import _month_bounds, collect_month
from .errors import GraphQLError, ReportWriteError, RepositoryUnavailableError
from .queries import BACKFILL_REPOSITORY_QUERY
from .transport import GraphQLClient
from .writing import _check_directory, write_report

LOG = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_month(value: str) -> tuple[int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
        raise ValueError("month must use YYYY-MM")
    year, month = map(int, value.split("-"))
    _month_bounds(year, month)
    return year, month


def _validate_request(owner: str, repo: str, discovery: str) -> None:
    if any(
        not isinstance(value, str) or not value.strip() or "/" in value
        for value in (owner, repo)
    ):
        raise ValueError("owner and repo must be nonempty names without slashes")
    if discovery not in ("full", "bounded"):
        raise ValueError("discovery must be full or bounded")


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


def _lookup_start(owner: str, repo: str, token: str | None) -> tuple[str, str]:
    operation = "BackfillRepository"
    with GraphQLClient(token) as client:
        data = client.execute(
            BACKFILL_REPOSITORY_QUERY,
            {"owner": owner, "name": repo},
            operation_name=operation,
        )
    if "repository" in data and data["repository"] is None:
        raise RepositoryUnavailableError(
            "repository unavailable to this credential", operation=operation
        )
    item = data.get("repository")
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("id"), str)
        or not item["id"]
    ):
        raise GraphQLError(
            "malformed backfill repository metadata", operation=operation
        )
    try:
        created = datetime.fromisoformat(item["createdAt"])
        if created.tzinfo is None:
            raise ValueError("timezone required")
        created = created.astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise GraphQLError(
            "malformed repository creation timestamp", operation=operation
        ) from exc
    return item["id"], f"{created.year:04d}-{created.month:02d}"


def _run_backfill(
    owner: str,
    repo: str,
    start: str | None = None,
    end: str | None = None,
    *,
    output_directory: str | os.PathLike,
    token: str | None = None,
    discovery: str = "bounded",
    on_written: Callable[[Path], None] | None = None,
    now: datetime | None = None,
) -> list[Path]:
    _validate_request(owner, repo, discovery)
    now = _utc_now() if now is None else now
    end = _validate_optional_range(start, end, now)
    repository_id = _check_directory(Path(output_directory))
    if start is None:
        observed, start = _lookup_start(owner, repo, token)
        if repository_id is not None and repository_id != observed:
            raise ReportWriteError(
                f"{output_directory}: report belongs to a different repository"
            )
        repository_id = observed
    year, month = _validate_range(start, end, now)
    last = _parse_month(end)
    LOG.info("Backfill range %s through %s", start, end)
    paths = []
    while (year, month) <= last:
        LOG.info("Collecting %s/%s %04d-%02d", owner, repo, year, month)
        report = collect_month(
            owner, repo, year, month, token=token, discovery=discovery
        )
        observed = report.metadata.repository_node_id
        if repository_id is not None and observed != repository_id:
            raise ReportWriteError(
                f"{Path(output_directory) / (report.metadata.month + '.json')}: "
                "report belongs to a different repository"
            )
        repository_id = observed
        path = write_report(report, output_directory)
        paths.append(path)
        if on_written is not None:
            on_written(path)
        year, month = year + (month == 12), month % 12 + 1
    return paths


def backfill(
    owner: str,
    repo: str,
    start: str | None = None,
    end: str | None = None,
    *,
    output_directory: str | os.PathLike,
    token: str | None = None,
    discovery: str = "bounded",
) -> list[Path]:
    """Collect and replace each month in an inclusive YYYY-MM range.

    Omitted start uses the repository's creation month; omitted end uses the
    current UTC month at invocation. Imported commits before creation require
    an explicit earlier start. Discovery defaults to bounded.

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
        discovery=discovery,
    )

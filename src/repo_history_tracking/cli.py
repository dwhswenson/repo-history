"""Command-line collection of monthly GitHub reports."""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .backfill import (
    _parse_month,
    _run_backfill,
    _validate_optional_range,
    _validate_range,
)
from .collection import collect_month
from .errors import GitHubAPIError, RateLimitError
from .writing import _check_directory, write_report

LOG = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _repository(value: str) -> tuple[str, str]:
    parts = value.split("/")
    if len(parts) != 2 or any(not p.strip() or p != p.strip() for p in parts):
        raise argparse.ArgumentTypeError("repository must use OWNER/REPO")
    return tuple(parts)


def _parser(backfilling: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-history-tracking" + (" backfill" if backfilling else ""),
        description=(
            "Collect an inclusive range of monthly reports."
            if backfilling
            else "Collect one UTC month. Use 'backfill --help' for inclusive ranges."
        ),
    )
    parser.add_argument("repository", type=_repository, metavar="OWNER/REPO")
    if backfilling:
        parser.add_argument(
            "--start", metavar="YYYY-MM", help="default: repository creation month"
        )
        parser.add_argument(
            "--end", metavar="YYYY-MM", help="default: current UTC month"
        )
    else:
        parser.add_argument(
            "--month", metavar="YYYY-MM", help="default: current UTC month"
        )
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument(
        "--discovery",
        choices=("full", "bounded"),
        default="bounded",
        help="parent discovery strategy; bounded is experimental (default: bounded)",
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "--quiet", action="store_true", help="errors only; suppress saved paths"
    )
    verbosity.add_argument(
        "--verbose", action="store_true", help="include debug diagnostics"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    backfilling = bool(argv and argv[0] == "backfill")
    parser = _parser(backfilling)
    args = parser.parse_args(argv[1:] if backfilling else argv)
    now = _utc_now()
    try:
        if backfilling:
            _validate_optional_range(args.start, args.end, now)
        else:
            if args.month is None:
                args.month = f"{now.year:04d}-{now.month:02d}"
            _validate_range(args.month, args.month, now)
            year, month = _parse_month(args.month)
    except ValueError as exc:
        parser.error(str(exc))

    level = (
        logging.ERROR if args.quiet else logging.DEBUG if args.verbose else logging.INFO
    )
    logger = logging.getLogger("repo_history_tracking")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    previous_level, previous_propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False

    def saved(path: Path) -> None:
        if not args.quiet:
            print(path, flush=True)

    try:
        owner, repo = args.repository
        if backfilling:
            _run_backfill(
                owner,
                repo,
                args.start,
                args.end,
                output_directory=args.output_directory,
                discovery=args.discovery,
                on_written=saved,
                now=now,
            )
        else:
            _check_directory(args.output_directory)
            LOG.info("Collecting %s/%s %s", owner, repo, args.month)
            report = collect_month(owner, repo, year, month, discovery=args.discovery)
            saved(write_report(report, args.output_directory))
        return 0
    except (GitHubAPIError, ValueError, OSError) as exc:
        message = str(exc)
        if isinstance(exc, RateLimitError):
            if exc.retry_after is not None:
                message += f"; retry after {exc.retry_after} seconds"
            if exc.reset_at is not None:
                message += f"; reset at {exc.reset_at.isoformat()}"
        LOG.error("%s", message)
        return 1
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate

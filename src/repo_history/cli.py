"""Command-line backfill, recent-month refreshes, and visualization summaries."""

import argparse
import logging
import sys
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from ._timing import backfill_timing
from .backfill import _run_backfill, _validate_optional_range, _validate_request
from .errors import GitHubAPIError, RateLimitError
from .manifest import _chop_partial_manifest, write_manifest
from .update import _prepare_update, _run_update

LOG = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _repository(value: str) -> tuple[str, str]:
    parts = value.split("/")
    if len(parts) != 2 or any(not p.strip() or p != p.strip() for p in parts):
        raise argparse.ArgumentTypeError("repository must use OWNER/REPO")
    return tuple(parts)


def _verbosity(parser):
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "--quiet", action="store_true", help="errors only; suppress saved paths"
    )
    verbosity.add_argument(
        "--verbose", action="store_true", help="include debug diagnostics"
    )


def _progress_option(parser):
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="disable added progress output; retain ordinary logs",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-history",
        description="Collect repository history and summarize reports.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("backfill", "update"):
        backfilling = name == "backfill"
        command = commands.add_parser(
            name,
            help="Backfill old history." if backfilling else "Refresh recent months.",
            description=(
                "Collect history and group monthly reports."
                if backfilling
                else "Refresh an inclusive range; infer start from the latest report, "
                "or backfill if none exist."
            ),
        )
        command.add_argument("repository", type=_repository, metavar="OWNER/REPO")
        command.add_argument(
            "--start",
            metavar="YYYY-MM",
            help=(
                "default: earliest history"
                if backfilling
                else "default: latest report month, recollected; backfill if none exist"
            ),
        )
        command.add_argument(
            "--end", metavar="YYYY-MM", help="default: current UTC month"
        )
        command.add_argument("--output-directory", required=True, type=Path)
        if backfilling:
            _progress_option(command)
        _verbosity(command)
    summary = commands.add_parser(
        "summarize",
        help="Generate the visualizer manifest offline.",
        description="Build a self-contained visualization manifest offline.",
    )
    summary.add_argument("--input-directory", required=True, type=Path)
    summary.add_argument(
        "--output", type=Path, help="default: INPUT_DIRECTORY/manifest.json"
    )
    summary.add_argument("--display-name", help="dataset title; default: owner/repo")
    _progress_option(summary)
    _verbosity(summary)
    chop = commands.add_parser(
        "chop-partial",
        help="Remove one trailing partial month from an existing manifest offline.",
        description="Remove a trailing ongoing month, leaving at least one "
        "month. All earlier months must be complete. Already complete manifests "
        "are left unchanged in place or saved to the explicit output.",
    )
    chop.add_argument(
        "manifest",
        type=Path,
        metavar="MANIFEST",
        help="manifest file or directory containing manifest.json",
    )
    chop.add_argument(
        "--output",
        "-o",
        type=Path,
        help="output manifest path; default: edit in place",
    )
    _verbosity(chop)
    return parser


def main(argv: list[str] | None = None) -> int:
    timer = perf_counter()
    parser = _parser()
    args = parser.parse_args(argv)
    now = _utc_now()
    if args.command in {"backfill", "update"}:
        try:
            if args.command == "backfill":
                _validate_request(*args.repository)
            args.end = _validate_optional_range(args.start, args.end, now)
        except ValueError as exc:
            parser.error(str(exc))

    level = (
        logging.ERROR if args.quiet else logging.DEBUG if args.verbose else logging.INFO
    )
    logger = logging.getLogger("repo_history")
    progress = None
    if (
        args.command in {"backfill", "summarize"}
        and not args.quiet
        and not args.no_progress
    ):
        from ._terminal import BackfillProgress, SummarizeProgress

        display = BackfillProgress if args.command == "backfill" else SummarizeProgress
        progress = display(sys.stderr, verbose=args.verbose)
    handler = (
        progress.log_handler() if progress is not None else None
    ) or logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    previous_level, previous_propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False

    def saved(path: Path) -> None:
        live_paths = (
            args.command == "backfill"
            and progress is not None
            and progress.interactive
            and sys.stdout.isatty()
        )
        if not args.quiet and not live_paths:
            print(path, flush=True)

    try:
        if args.command == "summarize":
            with progress if progress is not None else nullcontext():
                LOG.debug("Validating monthly reports in %s", args.input_directory)
                path = write_manifest(
                    args.input_directory,
                    output_path=args.output,
                    display_name=args.display_name,
                )
            saved(path)
        elif args.command == "chop-partial":
            LOG.debug("Chopping trailing partial month in %s", args.manifest)
            path = _chop_partial_manifest(args.manifest, output_path=args.output)
            if path is not None:
                saved(path)
        elif args.command == "backfill":
            with (
                backfill_timing(LOG, started=timer),
                progress if progress is not None else nullcontext(),
            ):
                _run_backfill(
                    *args.repository,
                    args.start,
                    args.end,
                    output_directory=args.output_directory,
                    on_written=saved,
                    now=now,
                )
        else:
            start, repository_id = _prepare_update(args.output_directory, args.start)
            try:
                _validate_optional_range(start, args.end, now)
            except ValueError as exc:
                parser.error(str(exc))
            _run_update(
                *args.repository,
                start,
                args.end,
                output_directory=args.output_directory,
                on_written=saved,
                now=now,
                expected_repository_id=repository_id,
            )
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

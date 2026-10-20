"""Command-line collection, recent-month refreshes, and history summaries."""

import argparse
import logging
import sys
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from ._timing import collect_timing
from .collect import _run_collect, _validate_optional_range, _validate_request
from .errors import GitHubAPIError, RateLimitError
from .summary import _combine_summaries, _trim_partial_summary, write_summary
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
    for name in ("collect", "update"):
        collecting = name == "collect"
        command = commands.add_parser(
            name,
            help="Collect repository history."
            if collecting
            else "Refresh recent months.",
            description=(
                "Collect history for an inclusive month range; default to all history."
                if collecting
                else "Refresh an inclusive range; infer start from the latest report, "
                "or collect all history if none exist."
            ),
        )
        command.add_argument("repository", type=_repository, metavar="OWNER/REPO")
        command.add_argument(
            "--start",
            metavar="YYYY-MM",
            help=(
                "default: earliest history"
                if collecting
                else "default: latest report month, recollected; all history if none exist"
            ),
        )
        command.add_argument(
            "--end", metavar="YYYY-MM", help="default: current UTC month"
        )
        command.add_argument("--output-directory", required=True, type=Path)
        if collecting:
            _progress_option(command)
        _verbosity(command)
    summary = commands.add_parser(
        "summarize",
        help="Generate the history summary offline.",
        description="Build a self-contained history summary for visualization offline.",
    )
    summary.add_argument("--input-directory", required=True, type=Path)
    summary.add_argument(
        "--output", type=Path, help="default: INPUT_DIRECTORY/summary.json"
    )
    summary.add_argument("--display-name", help="dataset title; default: owner/repo")
    _progress_option(summary)
    _verbosity(summary)
    trim = commands.add_parser(
        "trim-partial",
        help="Remove one trailing partial month from an existing summary offline.",
        description="Remove a trailing partial month, leaving at least one "
        "month. All earlier months must be complete. Already complete summaries "
        "are left unchanged in place or saved to the explicit output.",
    )
    trim.add_argument(
        "summary",
        type=Path,
        metavar="SUMMARY",
        help="summary file or directory containing summary.json",
    )
    trim.add_argument(
        "--output",
        "-o",
        type=Path,
        help="output summary path; default: edit in place",
    )
    _verbosity(trim)
    combine = commands.add_parser(
        "combine",
        help="Combine summaries from disjoint repositories offline.",
        description="Combine at least two summary files or directories containing "
        "summary.json. Repositories must not overlap and retained months must "
        "have complete coverage. Exclude an entire month if any source is partial "
        "unless --include-partial is supplied.",
    )
    combine.add_argument(
        "summaries",
        nargs="+",
        type=Path,
        metavar="SUMMARY",
        help="at least two summary files or directories containing summary.json",
    )
    combine.add_argument("--output", "-o", required=True, type=Path)
    combine.add_argument("--display-name", required=True, help="nonblank dataset title")
    combine.add_argument(
        "--include-partial", action="store_true", help="retain partial months"
    )
    _verbosity(combine)
    return parser


def main(argv: list[str] | None = None) -> int:
    timer = perf_counter()
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "combine":
        if len(args.summaries) < 2:
            parser.error("combine requires at least two input summaries")
        if not args.display_name.strip():
            parser.error("--display-name must not be blank")
    now = _utc_now()
    if args.command in {"collect", "update"}:
        try:
            if args.command == "collect":
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
        args.command in {"collect", "summarize"}
        and not args.quiet
        and not args.no_progress
    ):
        from ._terminal import CollectProgress, SummarizeProgress

        display = CollectProgress if args.command == "collect" else SummarizeProgress
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
            args.command == "collect"
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
                path = write_summary(
                    args.input_directory,
                    output_path=args.output,
                    display_name=args.display_name,
                )
            saved(path)
        elif args.command == "trim-partial":
            LOG.debug("Trimming trailing partial month in %s", args.summary)
            path = _trim_partial_summary(args.summary, output_path=args.output)
            if path is not None:
                saved(path)
        elif args.command == "combine":
            path = _combine_summaries(
                args.summaries,
                output_path=args.output,
                display_name=args.display_name,
                include_partial=args.include_partial,
            )
            saved(path)
        elif args.command == "collect":
            with (
                collect_timing(LOG, started=timer),
                progress if progress is not None else nullcontext(),
            ):
                _run_collect(
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

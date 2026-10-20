"""Validated, atomic monthly report file replacement (sequential writers)."""

import os
import re
import tempfile
from pathlib import Path

from .errors import ReportValidationError, ReportWriteError
from .models import MonthlyReport
from .serialization import report_from_json, report_to_json

MONTHLY_FILE = re.compile(r"[0-9]{4}-[0-9]{2}\.json")


def _check_directory(directory: Path, repository_id: str | None = None) -> str | None:
    """Validate existing monthly files and return their shared repository ID."""
    if not directory.exists():
        return repository_id
    for path in sorted(directory.iterdir()):
        if not MONTHLY_FILE.fullmatch(path.name) or path.is_dir():
            continue
        try:
            report = report_from_json(path.read_text(encoding="utf-8"))
        except (ReportValidationError, UnicodeError) as exc:
            raise ReportWriteError(f"{path}: invalid existing report: {exc}") from exc
        if report.metadata.month != path.stem:
            raise ReportWriteError(f"{path}: filename does not match report month")
        observed = report.metadata.repository.node_id
        if repository_id is not None and repository_id != observed:
            raise ReportWriteError(f"{path}: report belongs to a different repository")
        repository_id = observed
    return repository_id


def directory_repository(directory):
    """Read existing repository boundary evidence without relying on a summary."""
    from .contracts import choose_repository

    if not directory.exists():
        return None
    reports = [
        report_from_json(p.read_text())
        for p in sorted(directory.iterdir())
        if MONTHLY_FILE.fullmatch(p.name) and not p.is_dir()
    ]
    return (
        choose_repository(r.metadata.repository for r in reports) if reports else None
    )


def write_report(report: MonthlyReport, output_directory: str | os.PathLike) -> Path:
    """Atomically replace YYYY-MM.json after validating all destination reports.

    Creates missing directories. Invalid incoming reports raise
    ReportValidationError; inconsistent existing files raise ReportWriteError.
    Filesystem errors propagate. Concurrent writers require external coordination.
    """
    return _write_report(report, output_directory, check_directory=True)


def _write_report(
    report, output_directory, *, check_directory=False, previous_repository=None
):
    """Atomic writer for a destination already checked by a sequential collection run."""
    report_to_json(report)  # Validate before touching the destination.
    directory = Path(output_directory)
    if check_directory:
        _check_directory(directory, report.metadata.repository.node_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{report.metadata.month}.json"
    from .provenance import retain_provenance

    if check_directory:
        previous_repository = directory_repository(directory)
    previous = report_from_json(target.read_text()) if target.exists() else None
    if previous_repository is None and previous is not None:
        previous_repository = previous.metadata.repository
    if previous_repository is not None:
        report = retain_provenance(report, previous_repository, previous)
    text = report_to_json(report)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=directory,
            prefix=f".{report.metadata.month}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
        report_from_json(temporary.read_text(encoding="utf-8"))
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    if check_directory and (directory / "collection-state.json").exists():
        from .state import write_collection_state

        write_collection_state(directory, report.metadata.provenance.last_updated_by)
    return target

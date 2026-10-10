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
        observed = report.metadata.repository_node_id
        if repository_id is not None and repository_id != observed:
            raise ReportWriteError(f"{path}: report belongs to a different repository")
        repository_id = observed
    return repository_id


def write_report(report: MonthlyReport, output_directory: str | os.PathLike) -> Path:
    """Atomically replace YYYY-MM.json after validating all destination reports.

    Creates missing directories. Invalid incoming reports raise
    ReportValidationError; inconsistent existing files raise ReportWriteError.
    Filesystem errors propagate. Concurrent writers require external coordination.
    """
    text = report_to_json(report)
    directory = Path(output_directory)
    _check_directory(directory, report.metadata.repository_node_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{report.metadata.month}.json"
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
    return target

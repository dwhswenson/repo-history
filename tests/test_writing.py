"""Tests for validated atomic monthly file writing."""

from dataclasses import replace
from pathlib import Path

import pytest

from repo_history_tracking import (
    ReportValidationError,
    ReportWriteError,
    report_from_json,
    report_to_json,
    write_report,
    writing,
)


def test_write_empty_and_rerun_preserves_other_files(tmp_path, empty, mixed):
    directory = tmp_path / "nested" / "reports"
    target = write_report(empty, directory)
    assert target == directory / "2024-02.json"
    assert report_from_json(target.read_text()) == empty
    unrelated = directory / "notes.json"
    unrelated.write_text("not JSON")
    nested = directory / "2024-01.json"
    nested.mkdir()
    other = directory / "2024-03.json"
    metadata = replace(
        empty.metadata,
        month="2024-03",
        month_start=empty.metadata.month_end,
        month_end=empty.metadata.month_end.replace(month=4),
        collection_started_at=empty.metadata.month_end.replace(month=4),
        collection_finished_at=empty.metadata.month_end.replace(month=4),
    )
    other.write_text(report_to_json(replace(empty, metadata=metadata)))
    original = other.read_bytes()
    renamed = replace(
        mixed, metadata=replace(mixed.metadata, repository_full_name="new/name")
    )
    write_report(renamed, directory)
    assert report_from_json(target.read_text()) == renamed
    assert other.read_bytes() == original
    assert unrelated.read_text() == "not JSON"
    assert nested.is_dir()
    assert not list(directory.glob("*.tmp"))


def test_unicode(tmp_path, mixed):
    actors = (replace(mixed.actors[0], display_name="Zoë 雪"), *mixed.actors[1:])
    report = replace(mixed, actors=actors)
    target = write_report(report, tmp_path)
    assert "Zoë 雪" in target.read_text(encoding="utf-8")
    assert report_from_json(target.read_text(encoding="utf-8")) == report


def test_foreign_repository(tmp_path, empty):
    target = write_report(empty, tmp_path)
    original = target.read_bytes()
    foreign = replace(
        empty, metadata=replace(empty.metadata, repository_node_id="R_other")
    )
    with pytest.raises(ReportWriteError, match="2024-02.json.*different repository"):
        write_report(foreign, tmp_path)
    assert target.read_bytes() == original


@pytest.mark.parametrize(
    "filename,content",
    [
        ("2024-02.json", "broken"),
        ("2024-01.json", "broken"),
        ("2024-02.json", b"\xff"),
    ],
)
def test_malformed_existing(tmp_path, empty, filename, content):
    path = tmp_path / filename
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    original = path.read_bytes()
    with pytest.raises(ReportWriteError, match=filename):
        write_report(empty, tmp_path)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_filename_mismatch(tmp_path, empty):
    (tmp_path / "2024-01.json").write_text(report_to_json(empty))
    with pytest.raises(ReportWriteError, match="2024-01.json.*filename"):
        write_report(empty, tmp_path)


def test_invalid_incoming_does_not_create_directory(tmp_path, empty):
    directory = tmp_path / "new"
    with pytest.raises(ReportValidationError):
        write_report(replace(empty, schema_version="invalid"), directory)
    assert not directory.exists()


@pytest.mark.parametrize("stage", ["write", "flush", "validation", "replace"])
def test_failures_preserve_target_and_clean_temp(
    tmp_path, empty, mixed, monkeypatch, stage
):
    target = write_report(empty, tmp_path)
    original = target.read_bytes()
    if stage in ("write", "flush"):
        factory = writing.tempfile.NamedTemporaryFile

        class BrokenWriter:
            def __init__(self, *args, **kwargs):
                self.stream = factory(*args, **kwargs)
                self.name = self.stream.name

            def __enter__(self):
                return self

            def write(self, text):
                self.stream.write(text[:20])
                if stage == "write":
                    raise OSError("disk full")

            def flush(self):
                raise OSError("flush failed")

            def __exit__(self, *args):
                self.stream.close()

        monkeypatch.setattr(writing.tempfile, "NamedTemporaryFile", BrokenWriter)
        error = OSError
    elif stage == "validation":
        reader = Path.read_text

        def corrupt(path, *args, **kwargs):
            if path.suffix == ".tmp":
                return "broken"
            return reader(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", corrupt)
        error = ReportValidationError
    else:

        def fail(*args):
            raise OSError("replace failed")

        monkeypatch.setattr(writing.os, "replace", fail)
        error = OSError
    with pytest.raises(error):
        write_report(mixed, tmp_path)
    assert target.read_bytes() == original
    assert set(tmp_path.iterdir()) == {target}


def test_filesystem_errors_propagate(tmp_path, empty):
    directory = tmp_path / "file"
    directory.write_text("existing")
    with pytest.raises(OSError):
        write_report(empty, directory)

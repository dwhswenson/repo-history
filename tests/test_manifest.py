"""Offline producer, shared schema, and atomic publication tests."""

import json
from dataclasses import replace
from importlib.resources import files
from pathlib import Path

import pytest

from repo_history import (
    ReportValidationError,
    ReportWriteError,
    build_manifest,
    manifest,
    report_from_json,
    report_to_json,
    write_manifest,
)
from repo_history.cli import main


def later(report, month="2024-03"):
    text = (
        report_to_json(report).replace("2024-03", "2024-04").replace("2024-02", month)
    )
    return report_from_json(text)


def test_shared_fixture_and_projection(mixed, empty):
    value = build_manifest([mixed])
    expected = json.loads(
        files("repo_history").joinpath("schemas/viz-manifest-v1.json").read_text()
    )
    assert expected["$id"].endswith("viz-manifest:1")
    fixture = Path(__file__).parent / "fixtures" / "viz-manifest-v1.json"
    assert value == json.loads(fixture.read_text())
    assert set(value["actors"]) == set(mixed.summaries.actor_counts)
    assert "activities" not in value["months"][0]
    assert build_manifest([empty])["actors"] == {}


def test_sort_rename_and_determinism(tmp_path, mixed):
    second = later(mixed)
    second = replace(
        second,
        metadata=replace(
            second.metadata,
            repository_full_name="new/name",
            repository_url="https://github.com/new/name",
        ),
    )
    data = build_manifest([second, mixed])
    assert [m["metadata"]["month"] for m in data["months"]] == ["2024-02", "2024-03"]
    assert data["repository"]["full_name"] == "new/name"
    for report in (mixed, second):
        (tmp_path / f"{report.metadata.month}.json").write_text(report_to_json(report))
    original = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    path = write_manifest(tmp_path)
    text = path.read_bytes()
    assert b"\n" not in text[:-1] and text.endswith(b"\n")
    assert write_manifest(tmp_path).read_bytes() == text
    assert all(
        (tmp_path / name).read_bytes() == content for name, content in original.items()
    )
    (tmp_path / "2024-03.json").unlink()
    assert len(json.loads(write_manifest(tmp_path).read_text())["months"]) == 1
    (tmp_path / "2024-02.json").write_text(
        report_to_json(
            replace(
                mixed,
                metadata=replace(mixed.metadata, coverage_limitations=("new note",)),
            )
        )
    )
    assert json.loads(write_manifest(tmp_path).read_text())["months"][0]["metadata"][
        "coverage_limitations"
    ] == ["new note"]


def test_invalid_sources(tmp_path, mixed, empty):
    with pytest.raises(ReportWriteError, match="no monthly"):
        write_manifest(tmp_path)
    with pytest.raises(ReportWriteError, match="duplicate month"):
        build_manifest([mixed, mixed])
    with pytest.raises(ReportWriteError, match="same repository"):
        build_manifest(
            [
                mixed,
                replace(
                    later(empty),
                    metadata=replace(later(empty).metadata, repository_node_id="other"),
                ),
            ]
        )
    with pytest.raises(ReportValidationError):
        build_manifest(
            [replace(mixed, summaries=replace(mixed.summaries, unattributed_total=100))]
        )
    (tmp_path / "2024-01.json").write_text(report_to_json(mixed))
    with pytest.raises(ReportWriteError, match="filename"):
        write_manifest(tmp_path)
    (tmp_path / "2024-01.json").write_text("broken")
    with pytest.raises(ReportWriteError, match="invalid report"):
        write_manifest(tmp_path)


def test_classification_conflict(mixed):
    second = later(mixed)
    key = next(a.key for a in second.actors if a.classification == "human")
    actors = tuple(
        replace(a, classification="bot") if a.key == key else a for a in second.actors
    )
    from repo_history import build_report

    second = build_report(second.metadata, actors, second.activities)
    with pytest.raises(ReportWriteError, match="conflicting classifications"):
        build_manifest([mixed, second])


@pytest.mark.parametrize("stage", ["validation", "write", "flush", "replace"])
def test_atomic_failures(tmp_path, mixed, monkeypatch, stage):
    (tmp_path / "2024-02.json").write_text(report_to_json(mixed))
    target = write_manifest(tmp_path)
    original = target.read_bytes()
    if stage == "replace":

        def fail(*args):
            raise OSError("replace failed")

        monkeypatch.setattr(manifest.os, "replace", fail)
    elif stage == "validation":
        validator = manifest.validate_manifest
        calls = 0

        def fail(data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ReportValidationError("$", "temporary validation failed")
            validator(data)

        monkeypatch.setattr(manifest, "validate_manifest", fail)
    else:
        factory = manifest.tempfile.NamedTemporaryFile

        class BrokenWriter:
            def __init__(self, *args, **kwargs):
                self.stream = factory(*args, **kwargs)
                self.name = self.stream.name

            def __enter__(self):
                return self

            def write(self, text):
                self.stream.write(text[:20])
                if stage == "write":
                    raise OSError("write failed")

            def flush(self):
                raise OSError("flush failed")

            def __exit__(self, *args):
                self.stream.close()

        monkeypatch.setattr(manifest.tempfile, "NamedTemporaryFile", BrokenWriter)
    with pytest.raises((OSError, ReportValidationError)):
        write_manifest(tmp_path)
    assert target.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_cli_offline_and_output(tmp_path, mixed, capsys, monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    (tmp_path / "2024-02.json").write_text(report_to_json(mixed))
    (tmp_path / "notes.json").write_text("ignored")
    (tmp_path / "2024-01.json").mkdir()
    args = ["summarize", "--input-directory", str(tmp_path)]
    assert main(args) == 0
    assert str(tmp_path / "manifest.json") in capsys.readouterr().out
    assert main([*args, "--quiet"]) == 0
    assert capsys.readouterr().out == ""
    custom = tmp_path / "custom" / "summary.json"
    assert main([*args, "--output", str(custom), "--verbose"]) == 0
    assert custom.exists()
    assert main([*args, "--output", str(tmp_path / "2024-02.json")]) == 1
    assert "overwrite" in capsys.readouterr().err


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m.update(schema_version="2"),
        lambda m: m.update(extra=True),
        lambda m: m.update(months=[]),
        lambda m: m["months"].append(m["months"][0]),
        lambda m: m["months"][0]["metadata"].update(month_end="2024-04-01T00:00:00Z"),
        lambda m: m["months"][0]["metadata"].update(
            collection_finished_at="2024-01-01T00:00:00Z"
        ),
        lambda m: m["months"][0]["metadata"].update(ongoing=True),
        lambda m: m["actors"].pop(next(iter(m["actors"]))),
        lambda m: m["actors"].update(unused="human"),
        lambda m: m["actors"].update({next(iter(m["actors"])): "alien"}),
        lambda m: m["months"][0]["summaries"]["repository_counts"].update(commit=-1),
        lambda m: m["months"][0]["summaries"]["repository_counts"].update(
            commit=9007199254740992
        ),
        lambda m: m["months"][0]["summaries"]["repository_counts"].update(
            issue_comment=100
        ),
        lambda m: m["months"][0]["summaries"]["distinct_actors"].update(total=100),
        lambda m: m["months"][0]["summaries"]["distinct_actors_by_type"][
            "commit"
        ].update(human=100),
        lambda m: m["months"][0]["summaries"].update(unattributed_total=100),
    ],
)
def test_manifest_validation_rejects_inconsistent_summaries(mixed, mutation):
    value = build_manifest([mixed])
    mutation(value)
    with pytest.raises(ReportValidationError):
        manifest.validate_manifest(value)

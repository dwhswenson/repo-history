"""Offline producer, shared schema, and atomic publication tests."""

import json
from copy import deepcopy
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
from repo_history.manifest import resolve_manifest_path

from .helpers import replace


def later(report, month="2024-03"):
    text = (
        report_to_json(report).replace("2024-03", "2024-04").replace("2024-02", month)
    )
    import json
    from uuid import NAMESPACE_URL, uuid5

    data = json.loads(text)
    for run in data["metadata"]["provenance"].values():
        run["run_id"] = str(
            uuid5(
                NAMESPACE_URL,
                json.dumps(
                    {k: v for k, v in run.items() if k != "run_id"}, sort_keys=True
                ),
            )
        )
    return report_from_json(json.dumps(data))


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
    from repo_history._progress import listen

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
    assert data["repositories"][0]["full_name"] == "new/name"
    for report in (mixed, second):
        (tmp_path / f"{report.metadata.month}.json").write_text(report_to_json(report))
    original = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    path = write_manifest(tmp_path)
    text = path.read_bytes()
    assert b"\n" not in text[:-1] and text.endswith(b"\n")
    events = []
    with listen(events.append):
        assert write_manifest(tmp_path).read_bytes() == text
    assert [(e.stage, e.total) for e in events if e.kind == "total"] == [
        ("Reports", 2),
    ]
    assert [e.stage for e in events if e.kind == "advance"] == [
        "Reports",
        "Reports",
    ]
    assert events[-1].kind == "finish"
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
    assert json.loads(write_manifest(tmp_path).read_text())["months"][0][
        "source_reports"
    ][0]["metadata"]["coverage"]["limitations"] == ["new note"]


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
    from repo_history._progress import listen

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
    events = []
    with listen(events.append), pytest.raises((OSError, ReportValidationError)):
        write_manifest(tmp_path)
    assert target.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))
    assert [e.stage for e in events if e.kind == "advance"] == ["Reports"]
    assert not any(e.stage == "Writing" for e in events)
    assert not any(e.kind == "finish" for e in events)


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


@pytest.mark.parametrize(
    "flags,progress",
    [
        ([], True),
        (["--verbose"], True),
        (["--no-progress"], False),
        (["--quiet"], False),
    ],
)
def test_cli_summary_progress_modes(tmp_path, mixed, capsys, flags, progress):
    from repo_history._progress import enabled

    (tmp_path / "2024-02.json").write_text(report_to_json(mixed))
    assert main(["summarize", "--input-directory", str(tmp_path), *flags]) == 0
    out, err = capsys.readouterr()
    assert out == ("" if "--quiet" in flags else str(tmp_path / "manifest.json") + "\n")
    assert ("Summarize Reports" in err) == progress
    assert "Summarize Writing" not in err
    assert ("Validating monthly reports in" in err) == ("--verbose" in flags)
    assert "GraphQL" not in err
    assert "\x1b" not in err
    assert not enabled()


def test_cli_summary_live_display_preserves_final_output(tmp_path, mixed, monkeypatch):
    import io
    import logging
    import sys

    from repo_history._progress import enabled

    class TTY(io.StringIO):
        def isatty(self):
            return True

    stdout, stderr = TTY(), TTY()
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    (tmp_path / "2024-02.json").write_text(report_to_json(mixed))
    logger = logging.getLogger("repo_history")
    original = logger.level, logger.propagate, logger.handlers[:]
    assert main(["summarize", "--input-directory", str(tmp_path)]) == 0
    assert stdout.getvalue() == str(tmp_path / "manifest.json") + "\n"
    assert "Reports" in stderr.getvalue() and "Writing" not in stderr.getvalue()
    assert "Saved manifest.json" in stderr.getvalue()
    assert "GraphQL" not in stderr.getvalue()
    assert (logger.level, logger.propagate, logger.handlers) == original
    assert not enabled()


@pytest.mark.parametrize("broken", [False, True])
def test_summary_source_failure_does_not_complete_stage(tmp_path, mixed, broken):
    import io

    from repo_history._progress import enabled
    from repo_history._terminal import SummarizeProgress

    if broken:
        (tmp_path / "2024-02.json").write_text(report_to_json(mixed))
        (tmp_path / "2024-03.json").write_text("invalid")
    display = SummarizeProgress(io.StringIO())
    with pytest.raises(ReportWriteError), display:
        write_manifest(tmp_path)
    assert display.stages["Reports"].status == "failed"
    assert display.stages["Reports"].completed == int(broken)
    assert set(display.stages) == {"Reports"}
    assert not (tmp_path / "manifest.json").exists()
    assert not enabled()


def test_retired_collector_reports_still_summarize_offline(tmp_path, mixed):
    data = json.loads(report_to_json(mixed))
    for run in data["metadata"]["provenance"].values():
        run["command"] = "collect_month"
    (tmp_path / "2024-02.json").write_text(json.dumps(data))
    assert main(["summarize", "--input-directory", str(tmp_path), "--quiet"]) == 0
    value = json.loads((tmp_path / "manifest.json").read_text())
    assert value == build_manifest([report_from_json(json.dumps(data))])


def shifted_month(template, month, *, ongoing=False):
    from repo_history.contracts import month_bounds
    from repo_history.provenance import make_run
    from repo_history.serialization import to_data

    value = deepcopy(template)
    start, end = month_bounds(month)
    calendar = {
        "month": month,
        "month_start": to_data(start),
        "month_end": to_data(end),
        "ongoing": ongoing,
    }
    value["metadata"] = calendar
    for i, source in enumerate(value["source_reports"]):
        # One ongoing source makes the whole aggregate month partial.
        partial = ongoing and i == 0
        observed = start if partial else end
        run = to_data(make_run("manual", observed, observed))
        source["metadata"].update(calendar, ongoing=partial)
        source["metadata"]["coverage"]["as_of"] = to_data(observed)
        source["metadata"]["provenance"] = dict.fromkeys(
            ("created_by", "last_rebuilt_by", "last_updated_by"), run
        )
    return value


@pytest.fixture(params=["viz-manifest-v1.json", "multi-repo-manifest-v1.json"])
def choppable(request):
    value = json.loads((Path(__file__).parent / "fixtures" / request.param).read_text())
    partial = shifted_month(value["months"][0], "2024-03", ongoing=True)
    # The partial month has one actor that does not participate in older months.
    key = next(iter(value["actors"]))
    value["actors"]["partial-only"] = value["actors"][key]
    for summary in [
        partial["summaries"],
        *[s["summaries"] for s in partial["source_reports"]],
    ]:
        summary["actor_counts"]["partial-only"] = summary["actor_counts"].pop(key)
    value["months"].insert(0, partial)  # Latest month need not be the last entry.
    manifest.validate_manifest(value)
    return value


@pytest.mark.parametrize("directory_input", [False, True])
def test_chop_partial_offline_and_preserves_other_files(
    tmp_path, choppable, monkeypatch, capsys, directory_input
):
    import requests

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def no_network(*args, **kwargs):
        pytest.fail("chop-partial must not access the network")

    monkeypatch.setattr(requests, "Session", no_network)
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(choppable))
    untouched = {
        "2024-03.json": b"source report left untouched\n",
        "collection-state.json": b"collection state left untouched\n",
    }
    for name, content in untouched.items():
        (tmp_path / name).write_bytes(content)
    expected = deepcopy(choppable)
    expected["months"].pop(0)
    expected["actors"].pop("partial-only")
    input_path = tmp_path if directory_input else target
    assert main(["chop-partial", str(input_path)]) == 0
    out, err = capsys.readouterr()
    assert out == str(target) + "\n" and err == ""
    actual = json.loads(target.read_text())
    assert actual == expected
    manifest.validate_manifest(actual)
    text = target.read_bytes()
    assert text.endswith(b"\n") and b"\n" not in text[:-1]
    assert all(
        (tmp_path / name).read_bytes() == body for name, body in untouched.items()
    )
    assert main(["chop-partial", str(input_path)]) == 0
    out, err = capsys.readouterr()
    assert out == err == ""
    assert target.read_bytes() == text


def test_chop_prunes_all_actors_when_only_partial_has_activity(tmp_path, choppable):
    zero = manifest._sum_summaries([], {})
    for month in choppable["months"][1:]:
        month["summaries"] = deepcopy(zero)
        for source in month["source_reports"]:
            source["summaries"] = deepcopy(zero)
    used = set(choppable["months"][0]["summaries"]["actor_counts"])
    choppable["actors"] = {k: v for k, v in choppable["actors"].items() if k in used}
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(choppable))
    assert main(["chop-partial", str(target), "--quiet"]) == 0
    assert json.loads(target.read_text())["actors"] == {}


@pytest.mark.parametrize("flag", ["--quiet", "--verbose"])
def test_chop_symlink_and_verbosity(tmp_path, choppable, capsys, flag):
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(choppable))
    alias = tmp_path / "alias.json"
    alias.symlink_to(target)
    assert main(["chop-partial", str(alias), flag]) == 0
    out, err = capsys.readouterr()
    assert out == ("" if flag == "--quiet" else f"{alias}\n")
    assert ("Chopping trailing partial month" in err) == (flag == "--verbose")
    assert alias.is_symlink()
    assert len(json.loads(target.read_text())["months"]) == 1


@pytest.mark.parametrize(
    "problem,message",
    [
        ("multiple", "partial month precedes latest month"),
        ("sole", "leave no months"),
        ("json", "invalid manifest"),
        ("encoding", "invalid manifest"),
        ("structure", "invalid manifest"),
        ("schema", "invalid manifest"),
        ("summary", "invalid manifest"),
        ("missing", "No such file"),
    ],
)
def test_chop_invalid_input_preserves_file(
    tmp_path, choppable, capsys, problem, message
):
    target = tmp_path / "manifest.json"
    value = deepcopy(choppable)
    if problem == "multiple":
        value["months"][1] = shifted_month(value["months"][1], "2024-02", ongoing=True)
    elif problem == "sole":
        value["months"].pop()
        used = set(value["months"][0]["summaries"]["actor_counts"])
        value["actors"] = {k: v for k, v in value["actors"].items() if k in used}
    elif problem == "structure":
        value.pop("months")
    elif problem == "schema":
        value["schema_version"] = "2"
    elif problem == "summary":
        value["months"][0]["summaries"]["unattributed_total"] += 1
    content = (
        b"broken json"
        if problem == "json"
        else b"\xff"
        if problem == "encoding"
        else json.dumps(value).encode()
    )
    if problem != "missing":
        target.write_bytes(content)
    assert main(["chop-partial", str(target), "--quiet"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and str(target) in err and message in err
    assert "summarize" not in err
    if problem != "missing":
        assert target.read_bytes() == content
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("complete_latest", [False, True])
def test_chop_rejects_separated_earlier_partial(
    tmp_path, choppable, capsys, complete_latest
):
    earlier = shifted_month(choppable["months"][1], "2024-01", ongoing=True)
    choppable["months"].append(earlier)
    if complete_latest:
        choppable["months"][0] = shifted_month(choppable["months"][0], "2024-03")
    target = tmp_path / "manifest.json"
    content = json.dumps(choppable).encode()
    target.write_bytes(content)
    assert main(["chop-partial", str(target)]) == 1
    out, err = capsys.readouterr()
    assert out == "" and "partial month precedes latest month" in err
    assert str(target) in err
    assert target.read_bytes() == content


@pytest.mark.parametrize("sole", [False, True])
@pytest.mark.parametrize("output_flag", [None, "--output", "-o"])
@pytest.mark.parametrize("directory_input", [False, True])
def test_chop_complete_manifest_is_silent_noop(
    tmp_path, choppable, monkeypatch, capsys, sole, output_flag, directory_input
):
    choppable["months"][0] = shifted_month(choppable["months"][0], "2024-03")
    if sole:
        choppable["months"].pop(0)
        choppable["actors"].pop("partial-only")
    target = tmp_path / "manifest.json"
    original = json.dumps(choppable, indent=2).encode()
    target.write_bytes(original)

    def no_publish(*args):
        pytest.fail("complete manifests must not be rewritten")

    monkeypatch.setattr(manifest, "_publish_manifest", no_publish)
    options = [] if output_flag is None else [output_flag, str(target)]
    input_path = tmp_path if directory_input else target
    assert main(["chop-partial", str(input_path), *options]) == 0
    out, err = capsys.readouterr()
    assert out == err == ""
    assert target.read_bytes() == original


@pytest.mark.parametrize("output_flag", ["--output", "-o"])
@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("directory_input", [False, True])
def test_chop_explicit_output(
    tmp_path, choppable, capsys, output_flag, complete, directory_input
):
    if complete:
        choppable["months"][0] = shifted_month(choppable["months"][0], "2024-03")
    source = tmp_path / "manifest.json"
    original = json.dumps(choppable, indent=2).encode()
    source.write_bytes(original)
    target = tmp_path / "public" / "manifest.json"
    expected = deepcopy(choppable)
    if not complete:
        expected["months"].pop(0)
        expected["actors"].pop("partial-only")
    input_path = tmp_path if directory_input else source
    assert main(["chop-partial", str(input_path), output_flag, str(target)]) == 0
    out, err = capsys.readouterr()
    assert out == str(target) + "\n" and err == ""
    assert source.read_bytes() == original
    assert json.loads(target.read_text()) == expected
    manifest.validate_manifest(json.loads(target.read_text()))
    assert target.read_bytes().endswith(b"\n")


def test_chop_explicit_output_same_as_input(tmp_path, choppable, capsys):
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(choppable))
    assert main(["chop-partial", str(target), "-o", str(target)]) == 0
    out, err = capsys.readouterr()
    assert out == str(target) + "\n" and err == ""
    assert len(json.loads(target.read_text())["months"]) == 1


@pytest.mark.parametrize("invalid_input", [False, True])
def test_chop_explicit_output_validation_preserves_files(
    tmp_path, choppable, capsys, invalid_input
):
    source = tmp_path / "manifest.json"
    original = b"broken json" if invalid_input else json.dumps(choppable).encode()
    source.write_bytes(original)
    target = tmp_path / ("output.json" if invalid_input else "2024-03.json")
    existing = b"existing output\n"
    target.write_bytes(existing)
    assert main(["chop-partial", str(source), "-o", str(target)]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert (
        "invalid manifest" if invalid_input else "overwrite a monthly report"
    ) in err
    assert source.read_bytes() == original
    assert target.read_bytes() == existing


@pytest.mark.parametrize("stage", ["validation", "write", "flush", "replace"])
@pytest.mark.parametrize("separate_output", [False, True])
def test_chop_atomic_failures(
    tmp_path, choppable, monkeypatch, capsys, stage, separate_output
):
    source = tmp_path / "manifest.json"
    original = json.dumps(choppable).encode()
    source.write_bytes(original)
    target = tmp_path / "output.json" if separate_output else source
    existing = b"previous output\n" if separate_output else original
    target.write_bytes(existing)
    if stage == "validation":
        validate = manifest.validate_manifest
        calls = 0

        def fail(data):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise ReportValidationError("$", "temporary validation failed")
            validate(data)

        monkeypatch.setattr(manifest, "validate_manifest", fail)
    elif stage == "replace":

        def fail(*args):
            raise OSError("replace failed")

        monkeypatch.setattr(manifest.os, "replace", fail)
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
    options = ["-o", str(target)] if separate_output else []
    assert main(["chop-partial", str(source), *options]) == 1
    out, err = capsys.readouterr()
    assert out == "" and "failed" in err and "Traceback" not in err
    assert target.read_bytes() == existing
    assert source.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("output_flag", [False, True])
def test_chop_rejects_multiple_inputs_without_writing(tmp_path, choppable, output_flag):
    first, second = (tmp_path / name for name in ("first.json", "second.json"))
    original = json.dumps(choppable).encode()
    first.write_bytes(original)
    second.write_bytes(original)
    output = tmp_path / "output.json"
    options = ["-o", str(output)] if output_flag else []
    with pytest.raises(SystemExit) as exc:
        main(["chop-partial", str(first), str(second), *options])
    assert exc.value.code == 2
    assert first.read_bytes() == second.read_bytes() == original
    assert not output.exists()


def test_resolve_manifest_path_files_and_directories(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    directory = Path("reports")
    directory.mkdir()
    target = directory / "manifest.json"
    target.write_text("not read by the resolver")
    custom = Path("custom.json")
    custom.write_text("not read by the resolver")
    alias = Path("alias")
    alias.symlink_to(directory, target_is_directory=True)
    inputs = [target, directory, custom, alias]
    expected = [target, target, custom, alias / "manifest.json"]
    assert [resolve_manifest_path(p) for p in inputs] == expected
    assert [resolve_manifest_path(str(p)) for p in inputs] == expected
    assert resolve_manifest_path("missing.json") == Path("missing.json")


def test_chop_directory_without_manifest(tmp_path, capsys):
    assert resolve_manifest_path(tmp_path) == tmp_path / "manifest.json"
    assert main(["chop-partial", str(tmp_path)]) == 1
    out, err = capsys.readouterr()
    assert out == "" and str(tmp_path / "manifest.json") in err
    assert not list(tmp_path.iterdir())

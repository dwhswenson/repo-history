"""Offline combination, contributor unions, coverage, and safe publication."""

import json
from copy import deepcopy
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from repo_history import (
    ACTIVITY_TYPES,
    ReportValidationError,
    ReportWriteError,
    manifest,
)
from repo_history.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
BASE = json.loads((FIXTURES / "viz-manifest-v1.json").read_text())
GIT = next(key for key in BASE["actors"] if key.startswith("git:email:"))
ACTORS = {
    "U_shared": "human",
    "U_returning": "human",
    "U_new": "human",
    "B_bot": "bot",
    GIT: "unresolved_git",
}


def summary(participation, *, commits=None, missing_comments=0, actors=ACTORS):
    counts = {
        key: {kind: values.get(kind, 0) for kind in ACTIVITY_TYPES}
        for key, values in participation.items()
    }
    totals = {kind: sum(v[kind] for v in counts.values()) for kind in ACTIVITY_TYPES}
    if commits is not None:
        totals["commit"] = commits
    totals["issue_comment"] += missing_comments

    def population(keys):
        return {
            "total": len(keys),
            **{
                group: sum(actors[key] == group for key in keys)
                for group in ("human", "bot", "unresolved_git")
            },
        }

    return {
        "repository_counts": totals,
        "actor_counts": counts,
        "distinct_actors": population(list(counts)),
        "distinct_actors_by_type": {
            kind: population([k for k, v in counts.items() if v[kind]])
            for kind in ACTIVITY_TYPES
        },
        "unattributed_total": missing_comments,
        "unattributed_by_type": {
            kind: missing_comments if kind == "issue_comment" else 0
            for kind in ACTIVITY_TYPES
        },
    }


def single_manifest(repo, summaries, *, partial=(), actors=ACTORS):
    from repo_history.contracts import month_bounds
    from repo_history.serialization import to_data

    repository = deepcopy(BASE["repositories"][0])
    repository.update(
        node_id=repo,
        full_name=f"example/{repo}",
        url=f"https://github.com/example/{repo}",
    )
    months, used = [], set()
    for month, values in summaries.items():
        start, end = (to_data(date) for date in month_bounds(month))
        ongoing = month in partial
        observed = start if ongoing else end
        calendar = {
            "month": month,
            "month_start": start,
            "month_end": end,
            "ongoing": ongoing,
        }
        meta = deepcopy(BASE["months"][0]["source_reports"][0]["metadata"])
        meta.update(calendar, repository=repository)
        meta["coverage"]["as_of"] = observed
        run = deepcopy(meta["provenance"]["created_by"])
        run.update(
            started_at=observed,
            finished_at=observed,
            collection_since=start,
            collection_until=end,
            requested_start_month=month,
            requested_end_month=month,
        )
        run.pop("run_id")
        run["run_id"] = str(uuid5(NAMESPACE_URL, json.dumps(run, sort_keys=True)))
        meta["provenance"] = dict.fromkeys(
            ("created_by", "last_rebuilt_by", "last_updated_by"), run
        )
        used.update(values["actor_counts"])
        months.append(
            {
                "metadata": calendar,
                "summaries": deepcopy(values),
                "source_reports": [{"metadata": meta, "summaries": deepcopy(values)}],
            }
        )
    data = {
        "schema_version": "1",
        "display_name": repo,
        "repositories": [repository],
        "actors": {key: actors[key] for key in sorted(used)},
        "months": months,
    }
    manifest.validate_manifest(data)
    return data


def example_inputs():
    return [
        single_manifest(
            "R_a",
            {
                "2024-02": summary(
                    {
                        "U_shared": {"commit": 1, "issue_opened": 1},
                        GIT: {"commit": 1},
                        "B_bot": {"pr_review": 1},
                    },
                    commits=1,
                    missing_comments=1,
                ),
                "2024-03": summary(
                    {"U_shared": {"commit": 1}, "U_new": {"issue_opened": 1}}
                ),
                "2024-04": summary({}),
            },
        ),
        single_manifest(
            "R_b",
            {
                "2024-02": summary(
                    {"U_shared": {"commit": 1}, "U_returning": {"issue_comment": 1}}
                ),
                "2024-03": summary(
                    {"U_returning": {"commit": 1}, "B_bot": {"pr_review": 1}}
                ),
                "2024-04": summary(
                    {"U_new": {"commit": 1}, "U_shared": {"issue_comment": 1}}
                ),
            },
        ),
    ]


def save_inputs(directory, data):
    paths = []
    for i, value in enumerate(data):
        path = directory / f"repo-{i}" / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        paths.append(path)
    return paths


def command(paths, target, *options):
    return [
        "combine-manifests",
        *map(str, paths),
        "-o",
        str(target),
        "--display-name",
        "Project activity",
        *options,
    ]


def test_combines_offline_deterministically_and_matches_shared_fixture(
    tmp_path, monkeypatch, capsys
):
    import requests

    def no_network(*args, **kwargs):
        pytest.fail("combining must not access the network")

    monkeypatch.setattr(requests, "Session", no_network)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    inputs = example_inputs()
    paths = save_inputs(tmp_path, inputs)
    snapshots = {path: path.read_bytes() for path in paths}
    protected = tmp_path / "repo-0" / "collection-state.json"
    protected.write_text("private collection state")
    monthly = protected.with_name("2024-02.json")
    monthly.write_text("monthly report")
    monkeypatch.chdir(tmp_path)
    target = Path("public/manifest.json")
    args = command([Path("repo-0"), paths[1]], target)
    args[args.index("-o")] = "--output"
    assert main(args) == 0
    assert capsys.readouterr() == (str(target) + "\n", "")
    data = json.loads(target.read_text())
    assert data == json.loads((FIXTURES / "combined-manifest-v1.json").read_text())
    assert [m["summaries"]["repository_counts"]["commit"] for m in data["months"]] == [
        2,
        2,
        1,
    ]
    assert [m["summaries"]["distinct_actors"]["total"] for m in data["months"]] == [
        4,
        4,
        2,
    ]
    assert data["months"][0]["summaries"]["actor_counts"]["U_shared"]["commit"] == 2
    assert (
        sum(
            v["commit"] for v in data["months"][0]["summaries"]["actor_counts"].values()
        )
        == 3
    )
    assert data["months"][0]["summaries"]["unattributed_by_type"]["issue_comment"] == 1
    for i, original in enumerate(inputs):
        assert [m["source_reports"][i] for m in data["months"]] == [
            m["source_reports"][0] for m in original["months"]
        ]
    original_bytes = target.read_bytes()
    assert main(command(reversed(paths), target)) == 0
    assert target.read_bytes() == original_bytes
    assert original_bytes.endswith(b"\n") and b"\n" not in original_bytes[:-1]
    assert all(path.read_bytes() == content for path, content in snapshots.items())
    assert protected.read_text() == "private collection state"
    assert monthly.read_text() == "monthly report"


def test_three_inputs_and_precombined_inputs(tmp_path):
    paths = save_inputs(tmp_path, example_inputs())
    third = single_manifest(
        "R_c", {m: summary({}) for m in ("2024-02", "2024-03", "2024-04")}
    )
    third_path = tmp_path / "third.json"
    third_path.write_text(json.dumps(third))
    combined = tmp_path / "combined.json"
    manifest._combine_manifests(paths, output_path=combined, display_name="First two")
    direct, nested = (tmp_path / name for name in ("direct.json", "nested.json"))
    manifest._combine_manifests(
        [*paths, third_path], output_path=direct, display_name="Three"
    )
    manifest._combine_manifests(
        [third_path, combined], output_path=nested, display_name="Three"
    )
    assert direct.read_bytes() == nested.read_bytes()


@pytest.mark.parametrize("options", [[], ["--verbose"], ["--quiet"]])
def test_partial_policy_and_verbosity(tmp_path, capsys, options):
    inputs = example_inputs()
    last = inputs[0]["months"][-1]
    partial_only = "U_partial"
    actors = {**ACTORS, partial_only: "human"}
    inputs[0] = single_manifest(
        "R_a",
        {
            **{
                m["metadata"]["month"]: m["summaries"] for m in inputs[0]["months"][:-1]
            },
            last["metadata"]["month"]: summary(
                {partial_only: {"commit": 1}}, actors=actors
            ),
        },
        partial=["2024-04"],
        actors=actors,
    )
    paths = save_inputs(tmp_path, inputs)
    target = tmp_path / "out.json"
    assert main(command(paths, target, *options)) == 0
    out, err = capsys.readouterr()
    assert out == ("" if "--quiet" in options else f"{target}\n")
    if "--quiet" in options:
        assert err == ""
    else:
        assert "Excluding partial month 2024-04" in err and "example/R_a" in err
        assert "--include-partial" in err
        assert ("Reading and validating manifest" in err) == ("--verbose" in options)
    data = json.loads(target.read_text())
    assert [m["metadata"]["month"] for m in data["months"]] == ["2024-02", "2024-03"]
    assert partial_only not in data["actors"]
    assert len(data["repositories"]) == 2
    assert main(command(paths, target, "--include-partial", *options)) == 0
    out, err = capsys.readouterr()
    assert "Excluding" not in err
    included = json.loads(target.read_text())
    assert included["months"][-1]["metadata"]["ongoing"] is True
    assert (
        included["months"][-1]["source_reports"][0]
        == inputs[0]["months"][-1]["source_reports"][0]
    )
    assert partial_only in included["actors"]


@pytest.mark.parametrize("problem", ["empty", "internal", "leading"])
def test_partial_exclusions_and_contiguity(tmp_path, capsys, problem):
    months = ["2024-02"] if problem == "empty" else ["2024-02", "2024-03", "2024-04"]
    partial = "2024-03" if problem == "internal" else "2024-02"
    inputs = [
        single_manifest(
            repo,
            {m: summary({}) for m in months},
            partial=[partial] if repo == "R_a" else [],
        )
        for repo in ("R_a", "R_b")
    ]
    paths = save_inputs(tmp_path, inputs)
    target = tmp_path / "out.json"
    code = main(command(paths, target))
    out, err = capsys.readouterr()
    if problem == "leading":
        assert code == 0
        assert [
            m["metadata"]["month"] for m in json.loads(target.read_text())["months"]
        ] == ["2024-03", "2024-04"]
    else:
        assert code == 1 and out == "" and not target.exists()
        assert (
            "leave no months"
            if problem == "empty"
            else "missing aggregate month 2024-03"
        ) in err
    assert main(command(paths, target, "--include-partial")) == 0


@pytest.mark.parametrize(
    "problem", ["repeated", "rename", "disjoint-months", "combined"]
)
def test_rejects_repository_overlap(tmp_path, capsys, problem):
    a, b = example_inputs()
    other = deepcopy(a)
    if problem == "rename":
        other["repositories"][0].update(
            full_name="renamed/repo", url="https://github.com/renamed/repo"
        )
    if problem == "disjoint-months":
        a["months"] = a["months"][:1]
        other["months"] = other["months"][1:]
        for data in (a, other):
            used = {k for m in data["months"] for k in m["summaries"]["actor_counts"]}
            data["actors"] = {k: v for k, v in data["actors"].items() if k in used}
    paths = save_inputs(tmp_path, [a, other])
    if problem == "repeated":
        paths[1] = paths[0].parent
    elif problem == "combined":
        b_path = tmp_path / "b.json"
        b_path.write_text(json.dumps(b))
        combined = tmp_path / "combined.json"
        manifest._combine_manifests(
            [paths[0], b_path], output_path=combined, display_name="Both"
        )
        paths[1] = combined
    target = tmp_path / "out.json"
    assert main(command(paths, target)) == 1
    out, err = capsys.readouterr()
    assert out == "" and "R_a" in err and "appears in both" in err
    assert str(paths[0]) in err and str(paths[1]) in err
    assert not target.exists()


@pytest.mark.parametrize("partial_only", [False, True])
def test_actor_conflicts_checked_before_exclusion(tmp_path, capsys, partial_only):
    counts = {"2024-02": summary({}), "2024-03": summary({"U_shared": {"commit": 1}})}
    other_actors = {**ACTORS, "U_shared": "bot"}
    other_counts = {
        "2024-02": summary({}),
        "2024-03": summary({"U_shared": {"commit": 1}}, actors=other_actors),
    }
    partial = ["2024-03"] if partial_only else []
    inputs = [
        single_manifest("R_a", counts, partial=partial),
        single_manifest("R_b", other_counts, partial=partial, actors=other_actors),
    ]
    paths = save_inputs(tmp_path, inputs)
    target = tmp_path / "out.json"
    assert main(command(paths, target)) == 1
    assert "conflicting classifications for actor U_shared" in capsys.readouterr().err
    assert not target.exists()


@pytest.mark.parametrize("problem", ["start", "end", "internal", "all-missing"])
def test_rejects_missing_coverage(tmp_path, capsys, problem):
    months_a = ["2024-02", "2024-03", "2024-04"]
    months_b = months_a.copy()
    missing = {
        "start": "2024-02",
        "end": "2024-04",
        "internal": "2024-03",
        "all-missing": "2024-03",
    }[problem]
    months_b.remove(missing)
    if problem == "all-missing":
        months_a.remove(missing)
    inputs = [
        single_manifest(repo, {m: summary({}) for m in months})
        for repo, months in (("R_a", months_a), ("R_b", months_b))
    ]
    paths = save_inputs(tmp_path, inputs)
    target = tmp_path / "out.json"
    target.write_bytes(b"previous output")
    assert main(command(paths, target)) == 1
    out, err = capsys.readouterr()
    assert out == "" and missing in err
    assert (
        "missing aggregate month"
        if problem == "all-missing"
        else "missing report for R_b"
    ) in err
    assert target.read_bytes() == b"previous output"


def test_pre_history_requires_evidence_and_retains_zero_months(tmp_path, capsys):
    a = single_manifest("R_a", {m: summary({}) for m in ("2024-02", "2024-03")})
    b = single_manifest("R_b", {"2024-03": summary({})})
    repo = b["repositories"][0]
    proof = deepcopy(
        b["months"][0]["source_reports"][0]["metadata"]["provenance"]["created_by"]
    )
    proof.update(collection_since=None, parent_discovery="full", commit_history="full")
    proof["run_id"] = str(uuid5(NAMESPACE_URL, "history evidence"))
    repo.update(
        created_at="2024-03-01T00:00:00Z",
        history_start_month="2024-03",
        history_start_established_by=proof,
    )
    paths = save_inputs(tmp_path, [a, b])
    target = tmp_path / "out.json"
    assert main(command(paths, target)) == 0
    data = json.loads(target.read_text())
    assert [len(m["source_reports"]) for m in data["months"]] == [1, 2]
    assert all(
        not any(m["summaries"]["repository_counts"].values()) for m in data["months"]
    )
    assert data["actors"] == {}
    repo.update(history_start_month=None, history_start_established_by=None)
    paths = save_inputs(tmp_path, [a, b])
    original = target.read_bytes()
    assert main(command(paths, target)) == 1
    assert "missing report for R_b in 2024-02" in capsys.readouterr().err
    assert target.read_bytes() == original


@pytest.mark.parametrize(
    "options",
    [
        [],
        ["a", "b"],
        ["a", "-o", "out.json", "--display-name", "Title"],
        ["a", "b", "-o", "out.json"],
        ["a", "b", "--display-name", "Title"],
        ["a", "b", "-o", "out.json", "--display-name", " \t"],
        ["a", "b", "-o", "out.json", "--display-name", "Title", "--quiet", "--verbose"],
    ],
)
def test_argument_errors(tmp_path, monkeypatch, options):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["combine-manifests", *options])
    assert exc.value.code == 2
    assert not (tmp_path / "out.json").exists()


@pytest.mark.parametrize(
    "problem",
    [
        "json",
        "encoding",
        "schema",
        "summary",
        "partial-summary",
        "missing",
        "directory",
    ],
)
def test_invalid_inputs_preserve_output(tmp_path, capsys, problem):
    paths = save_inputs(tmp_path, example_inputs())
    if problem == "missing":
        paths[1].unlink()
    elif problem == "directory":
        paths[1] = tmp_path / "empty"
        paths[1].mkdir()
    elif problem in {"json", "encoding"}:
        paths[1].write_bytes(b"{" if problem == "json" else b"\xff")
    else:
        data = json.loads(paths[1].read_text())
        if problem == "schema":
            data["schema_version"] = "2"
        else:
            if problem == "partial-summary":
                data = single_manifest(
                    "R_b", {"2024-02": summary({})}, partial=["2024-02"]
                )
            data["months"][0]["summaries"]["repository_counts"]["commit"] += 1
        paths[1].write_text(json.dumps(data))
    target = tmp_path / "out.json"
    target.write_bytes(b"previous output")
    assert main(command(paths, target, "--quiet")) == 1
    out, err = capsys.readouterr()
    assert out == "" and str(paths[1]) in err and "Traceback" not in err
    assert target.read_bytes() == b"previous output"
    assert not list(tmp_path.glob(".manifest.*.tmp"))


@pytest.mark.parametrize("problem", ["overflow", "run-conflict"])
def test_rejects_inconsistency_only_visible_after_combination(
    tmp_path, capsys, problem
):
    if problem == "overflow":
        inputs = [
            single_manifest(
                repo, {"2024-02": summary({"U_shared": {"commit": 9007199254740991}})}
            )
            for repo in ("R_a", "R_b")
        ]
        message = "9007199254740991"
    else:
        inputs = example_inputs()
        meta = inputs[1]["months"][0]["source_reports"][0]["metadata"]
        # Each input is valid alone; the shared run ID disagrees across inputs.
        for run in meta["provenance"].values():
            run["collector_version"] = "99.0"
        message = "conflicting descriptors for run ID"
    paths = save_inputs(tmp_path, inputs)
    for path in paths:
        manifest.validate_manifest(json.loads(path.read_text()))
    target = tmp_path / "out.json"
    target.write_bytes(b"previous output")
    assert main(command(paths, target)) == 1
    out, err = capsys.readouterr()
    assert out == "" and message in err
    assert target.read_bytes() == b"previous output"
    assert not list(tmp_path.glob(".manifest.*.tmp"))


@pytest.mark.parametrize(
    "alias", ["same", "symlink", "symlink-parent", "hardlink", "monthly", "directory"]
)
def test_output_protection(tmp_path, capsys, alias):
    paths = save_inputs(tmp_path, example_inputs())
    target = tmp_path / "out.json"
    if alias == "same":
        target = paths[0]
    elif alias == "symlink":
        target.symlink_to(paths[0])
    elif alias == "symlink-parent":
        link = tmp_path / "linked-dir"
        link.symlink_to(paths[0].parent, target_is_directory=True)
        target = link / "manifest.json"
    elif alias == "hardlink":
        target.hardlink_to(paths[0])
    elif alias == "monthly":
        target = tmp_path / "2024-02.json"
        target.write_bytes(b"monthly report")
    else:
        target.mkdir()
    original = {path: path.read_bytes() for path in paths}
    old_target = target.read_bytes() if target.is_file() else None
    assert main(command(paths, target)) == 1
    out, err = capsys.readouterr()
    assert out == "" and "output" in err
    assert all(path.read_bytes() == content for path, content in original.items())
    if old_target is not None:
        assert target.read_bytes() == old_target
    assert not list(tmp_path.glob(".manifest.*.tmp"))


@pytest.mark.parametrize(
    "stage",
    ["assembled-validation", "temporary-validation", "write", "flush", "replace"],
)
def test_atomic_failures_preserve_inputs_and_output(
    tmp_path, monkeypatch, capsys, stage
):
    paths = save_inputs(tmp_path, example_inputs())
    originals = {path: path.read_bytes() for path in paths}
    target = tmp_path / "out.json"
    target.write_bytes(b"previous output")
    if stage.endswith("validation"):
        validate = manifest.validate_manifest
        calls = 0

        def fail(data):
            nonlocal calls
            calls += 1
            if calls == (3 if stage == "assembled-validation" else 4):
                raise ReportValidationError("$", "validation failed")
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

            def __exit__(self, *args):
                self.stream.close()

            def write(self, text):
                self.stream.write(text[:20])
                if stage == "write":
                    raise OSError("write failed")

            def flush(self):
                raise OSError("flush failed")

        monkeypatch.setattr(manifest.tempfile, "NamedTemporaryFile", BrokenWriter)
    assert main(command(paths, target)) == 1
    out, err = capsys.readouterr()
    assert out == "" and "failed" in err and "Traceback" not in err
    assert target.read_bytes() == b"previous output"
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert not list(tmp_path.glob(".manifest.*.tmp"))


def test_helper_rejects_invalid_requests(tmp_path):
    with pytest.raises(ReportWriteError, match="at least two"):
        manifest._combine_manifests(
            [], output_path=tmp_path / "out.json", display_name="Title"
        )
    with pytest.raises(ReportWriteError, match="must not be blank"):
        manifest._combine_manifests(
            ["a", "b"], output_path=tmp_path / "out.json", display_name=" "
        )

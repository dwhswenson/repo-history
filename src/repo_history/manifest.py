"""Offline, validated summary manifests for the browser visualizer."""

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from ._progress import emit
from .errors import ReportValidationError, ReportWriteError
from .models import ACTIVITY_TYPES, MonthlyReport
from .serialization import report_from_json, to_data
from .validation import validate_report
from .writing import MONTHLY_FILE

METADATA_KEYS = ("month", "month_start", "month_end", "ongoing")
CLASSES = ("human", "bot", "unresolved_git")


def _population(keys, actors):
    return {
        "total": len(keys),
        **{c: sum(actors[k] == c for k in keys) for c in CLASSES},
    }


def _validate_summary(summary, actors, path):
    from .contracts import require

    counts = summary["actor_counts"]
    for key, values in counts.items():
        require(key in actors, path, f"unknown actor {key}")
        require(any(values.values()), path, f"actor {key} has no participation")
    require(
        summary["distinct_actors"] == _population(set(counts), actors),
        path,
        "distinct actor population disagrees with actor counts",
    )
    require(
        summary["unattributed_total"] == sum(summary["unattributed_by_type"].values()),
        path,
        "unattributed total disagrees with type counts",
    )
    for kind in ACTIVITY_TYPES:
        keys = {k for k, v in counts.items() if v[kind] > 0}
        require(
            summary["distinct_actors_by_type"][kind] == _population(keys, actors),
            path,
            f"{kind} population disagrees with actor counts",
        )
        total, unattributed = (
            summary["repository_counts"][kind],
            summary["unattributed_by_type"][kind],
        )
        require(
            unattributed <= total and all(v[kind] <= total for v in counts.values()),
            path,
            f"{kind} attribution exceeds repository count",
        )
        attributed = sum(v[kind] for v in counts.values())
        require(
            (attributed >= total - unattributed)
            if kind == "commit"
            else attributed + unattributed == total,
            path,
            f"{kind} repository count disagrees with attribution",
        )


def _sum_summaries(summaries, actors):
    """Expected chart summary for validation, never a manifest-combining API."""
    counts = dict.fromkeys(ACTIVITY_TYPES, 0)
    unattributed = counts.copy()
    per_actor = {}
    for summary in summaries:
        for kind in ACTIVITY_TYPES:
            counts[kind] += summary["repository_counts"][kind]
            unattributed[kind] += summary["unattributed_by_type"][kind]
        for key, values in summary["actor_counts"].items():
            target = per_actor.setdefault(key, dict.fromkeys(ACTIVITY_TYPES, 0))
            for kind in ACTIVITY_TYPES:
                target[kind] += values[kind]
    return {
        "repository_counts": counts,
        "actor_counts": per_actor,
        "distinct_actors": _population(set(per_actor), actors),
        "distinct_actors_by_type": {
            kind: _population({k for k, v in per_actor.items() if v[kind]}, actors)
            for kind in ACTIVITY_TYPES
        },
        "unattributed_total": sum(unattributed.values()),
        "unattributed_by_type": unattributed,
    }


def validate_manifest(data: dict) -> None:
    """Validate sources, coverage, and the prepared aggregate chart summaries."""
    from .contracts import (
        require,
        validate_calendar,
        validate_metadata,
        validate_repository,
        validate_structure,
    )

    validate_structure("viz-manifest", data)
    runs, repositories, source_repositories = {}, {}, {}
    for repo in data["repositories"]:
        key = repo["node_id"]
        require(key not in repositories, "$.repositories", "duplicate repository")
        validate_repository(repo, "$.repositories", runs)
        repositories[key] = repo
        source_repositories[key] = []
    seen, used = set(), set()
    for i, entry in enumerate(data["months"]):
        path = f"$.months[{i}]"
        m = entry["metadata"]
        require(m["month"] not in seen, path, "duplicate month")
        seen.add(m["month"])
        validate_calendar(m, path)
        present = set()
        for source in entry["source_reports"]:
            meta = source["metadata"]
            key = meta["repository"]["node_id"]
            require(key in repositories, path, "unknown source repository")
            require(key not in present, path, "duplicate repository source report")
            present.add(key)
            validate_metadata(meta, path + ".source_reports", runs)
            source_repositories[key].append(meta["repository"])
            require(
                all(meta[k] == m[k] for k in ("month", "month_start", "month_end")),
                path,
                "source month disagrees with aggregate month",
            )
            require(
                meta["repository"]["created_at"] == repositories[key]["created_at"],
                path,
                "conflicting repository creation timestamp",
            )
            boundary = meta["repository"]["history_start_month"]
            require(
                boundary is None
                or meta["month"] >= boundary
                or not any(source["summaries"]["repository_counts"].values()),
                path,
                "activity precedes established history start",
            )
            _validate_summary(source["summaries"], data["actors"], path)
            used.update(source["summaries"]["actor_counts"])
        if len(repositories) > 1:
            for key, repo in repositories.items():
                if key not in present:
                    require(
                        repo["history_start_month"] is not None
                        and m["month"] < repo["history_start_month"],
                        path,
                        f"missing report for {key} in {m['month']}",
                    )
        require(
            m["ongoing"]
            == any(s["metadata"]["ongoing"] for s in entry["source_reports"]),
            path,
            "ongoing disagrees with sources",
        )
        _validate_summary(entry["summaries"], data["actors"], path)
        require(
            entry["summaries"]
            == _sum_summaries(
                [s["summaries"] for s in entry["source_reports"]], data["actors"]
            ),
            path,
            "aggregate summary disagrees with source reports",
        )
    if len(repositories) > 1:
        month = min(seen)
        while month <= max(seen):
            require(month in seen, "$.months", f"missing aggregate month {month}")
            from .contracts import month_bounds

            month = month_bounds(month)[1].strftime("%Y-%m")
    for key, sources in source_repositories.items():
        known = [r for r in sources if r["history_start_month"] is not None]
        if known:
            earliest = min(known, key=lambda r: r["history_start_month"])
            require(
                repositories[key]["history_start_month"]
                == earliest["history_start_month"],
                "$.repositories",
                "registry must retain earliest source history boundary",
            )
    require(
        used == set(data["actors"]),
        "$.actors",
        "classification lookup contains unused actors",
    )


def build_manifest(
    reports: Iterable[MonthlyReport], *, display_name: str | None = None
) -> dict:
    """Validate full reports sequentially and retain only visualization data."""
    return _build_manifest(reports, check_reports=True, display_name=display_name)


def _build_manifest(
    reports: Iterable[MonthlyReport],
    *,
    check_reports: bool,
    display_name: str | None = None,
) -> dict:
    actors, months, repository_id, latest = {}, [], None, None
    repository_metadata = []
    seen = set()
    for report in reports:
        if check_reports:
            validate_report(report)
        m = report.metadata
        if repository_id is not None and repository_id != m.repository.node_id:
            raise ReportWriteError("all reports must belong to the same repository")
        repository_id = m.repository.node_id
        if m.month in seen:
            raise ReportWriteError(f"duplicate month {m.month}")
        seen.add(m.month)
        registry = {actor.key: actor.classification for actor in report.actors}
        for key in report.summaries.actor_counts:
            classification = registry[key]
            if key in actors and actors[key] != classification:
                raise ReportWriteError(f"conflicting classifications for actor {key}")
            actors[key] = classification
        metadata = to_data(m)
        repository_metadata.append((m.month, m.repository))
        months.append(
            {
                "metadata": {key: metadata[key] for key in METADATA_KEYS},
                "summaries": to_data(report.summaries),
                "source_reports": [
                    {"metadata": metadata, "summaries": to_data(report.summaries)}
                ],
            }
        )
        if latest is None or m.month > latest.month:
            latest = m
    if latest is None:
        raise ReportWriteError("no monthly reports found")
    from .contracts import choose_repository

    repository = choose_repository(
        r for _, r in sorted(repository_metadata, key=lambda x: x[0])
    )
    data = {
        "schema_version": "1",
        "display_name": repository.full_name if display_name is None else display_name,
        "repositories": [to_data(repository)],
        "actors": dict(sorted(actors.items())),
        "months": sorted(months, key=lambda item: item["metadata"]["month"]),
    }
    validate_manifest(data)
    return data


def write_manifest(
    report_directory: str | os.PathLike,
    *,
    output_path: str | os.PathLike | None = None,
    display_name: str | None = None,
) -> Path:
    """Rebuild an atomic manifest; source reports must not change concurrently."""
    directory = Path(report_directory)
    target = directory / "manifest.json" if output_path is None else Path(output_path)
    if MONTHLY_FILE.fullmatch(target.name):
        raise ReportWriteError("manifest output must not overwrite a monthly report")
    paths = [
        path
        for path in sorted(directory.iterdir())
        if MONTHLY_FILE.fullmatch(path.name) and not path.is_dir()
    ]
    emit("start", stage="Reports", detail="Reading and validating monthly reports")
    emit("total", stage="Reports", total=len(paths))

    def reports():
        for path in paths:
            emit("detail", detail=f"Reading and validating {path.name}")
            if path.resolve() == target.resolve():
                raise ReportWriteError(
                    "manifest output must not overwrite a source report"
                )
            try:
                report = report_from_json(path.read_text(encoding="utf-8"))
                if report.metadata.month != path.stem:
                    raise ReportWriteError(
                        f"{path}: filename does not match report month"
                    )
            except (ReportValidationError, UnicodeError) as exc:
                raise ReportWriteError(f"{path}: invalid report: {exc}") from exc
            yield report
            emit("advance", stage="Reports")
        emit("detail", detail="Validating assembled manifest")

    # report_from_json has already performed full event and summary validation.
    data = _build_manifest(reports(), check_reports=False, display_name=display_name)
    emit("complete", stage="Reports", detail=f"Summarized {len(paths)} monthly reports")
    emit("phase", stage="Manifest", detail=f"Saving {target.name}")
    _publish_manifest(data, target)
    emit("finish", stage="Completed", detail=f"Saved {target.name}")
    return target


def resolve_manifest_path(path: str | os.PathLike) -> Path:
    """Locate a manifest from an explicit file or a directory containing it.

    Preserve relative paths and leave existence and content checks to the reader.
    """
    path = Path(path)
    return path / "manifest.json" if path.is_dir() else path


def _chop_partial_manifest(
    path: Path, *, output_path: Path | None = None
) -> Path | None:
    """Validate and save one manifest; return None for a complete in-place input."""
    if output_path is not None and MONTHLY_FILE.fullmatch(output_path.name):
        raise ReportWriteError("manifest output must not overwrite a monthly report")
    path = resolve_manifest_path(path)
    source = path.resolve()
    destination = path if output_path is None else output_path
    target = destination.resolve()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        validate_manifest(data)
    except ValueError as exc:
        raise ReportWriteError(f"{path}: invalid manifest: {exc}") from exc
    ordered = sorted(data["months"], key=lambda m: m["metadata"]["month"])
    latest = ordered[-1]
    if any(month["metadata"]["ongoing"] for month in ordered[:-1]):
        raise ReportWriteError(f"{path}: partial month precedes latest month")
    if not latest["metadata"]["ongoing"]:
        if target == source:
            return None
    else:
        if len(ordered) == 1:
            raise ReportWriteError(f"{path}: chopping would leave no months")
        data["months"] = [m for m in data["months"] if m is not latest]
        used = {
            key
            for month in data["months"]
            for source in month["source_reports"]
            for key in source["summaries"]["actor_counts"]
        }
        data["actors"] = {
            key: value for key, value in data["actors"].items() if key in used
        }
        try:
            validate_manifest(data)
        except ReportValidationError as exc:
            raise ReportWriteError(f"{path}: invalid chopped manifest: {exc}") from exc
    _publish_manifest(data, target)
    return destination


def _publish_manifest(data: dict, target: Path) -> None:
    """Validate a temporary manifest before atomically replacing its target."""
    text = (
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=".manifest.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
        emit("detail", detail=f"Verifying {target.name}")
        validate_manifest(json.loads(temporary.read_text(encoding="utf-8")))
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

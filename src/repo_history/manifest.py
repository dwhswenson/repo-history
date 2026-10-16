"""Offline, validated summary manifests for the browser visualizer."""

import json
import os
import tempfile
from collections.abc import Iterable
from datetime import datetime, timezone
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from .errors import ReportValidationError, ReportWriteError
from .models import ACTIVITY_TYPES, MonthlyReport
from .serialization import parse_time, report_from_json, to_data
from .validation import validate_report
from .writing import MONTHLY_FILE

METADATA_KEYS = (
    "month",
    "month_start",
    "month_end",
    "collection_started_at",
    "collection_finished_at",
    "ongoing",
    "coverage_limitations",
)
CLASSES = ("human", "bot", "unresolved_git")


@lru_cache(maxsize=1)
def _validator():
    schema = json.loads(
        files("repo_history").joinpath("schemas/viz-manifest-v1.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_manifest(data: dict) -> None:
    """Validate structure and every invariant recoverable from summaries."""

    def require(condition, path, message):
        if not condition:
            raise ReportValidationError(path, message)

    error = next(_validator().iter_errors(data), None)
    if error is not None:
        raise ReportValidationError(
            "$"
            + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.path),
            error.message,
        )
    seen = set()
    used = set()
    for i, entry in enumerate(data["months"]):
        path = f"$.months[{i}]"
        m, s = entry["metadata"], entry["summaries"]
        require(m["month"] not in seen, path, "duplicate month")
        seen.add(m["month"])
        year, month = map(int, m["month"].split("-"))
        try:
            start = datetime(year, month, 1, tzinfo=timezone.utc)
            end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=timezone.utc)
        except ValueError as exc:
            raise ReportValidationError(path, "unsupported calendar month") from exc
        require(
            parse_time(m["month_start"]) == start and parse_time(m["month_end"]) == end,
            path,
            "month boundaries must match reporting month in UTC",
        )
        collected, finished = (
            parse_time(m["collection_started_at"]),
            parse_time(m["collection_finished_at"]),
        )
        require(start <= collected <= finished, path, "invalid collection interval")
        require(
            m["ongoing"] == (start <= collected < end),
            path,
            "ongoing must reflect collection start",
        )
        counts = s["actor_counts"]
        for key, values in counts.items():
            require(key in data["actors"], path, f"unknown actor {key}")
            require(any(values.values()), path, f"actor {key} has no participation")
            used.add(key)

        def population(keys):
            return {
                "total": len(keys),
                **{c: sum(data["actors"][key] == c for key in keys) for c in CLASSES},
            }

        require(
            s["distinct_actors"] == population(set(counts)),
            path,
            "distinct actor population disagrees with actor counts",
        )
        require(
            s["unattributed_total"] == sum(s["unattributed_by_type"].values()),
            path,
            "unattributed total disagrees with type counts",
        )
        for kind in ACTIVITY_TYPES:
            keys = {key for key, values in counts.items() if values[kind] > 0}
            require(
                s["distinct_actors_by_type"][kind] == population(keys),
                path,
                f"{kind} population disagrees with actor counts",
            )
            total, unattributed = (
                s["repository_counts"][kind],
                s["unattributed_by_type"][kind],
            )
            require(
                unattributed <= total,
                path,
                f"{kind} unattributed exceeds repository count",
            )
            require(
                all(values[kind] <= total for values in counts.values()),
                path,
                f"{kind} actor count exceeds repository count",
            )
            attributed = sum(values[kind] for values in counts.values())
            require(
                (attributed >= total - unattributed)
                if kind == "commit"
                else (attributed + unattributed == total),
                path,
                f"{kind} repository count disagrees with attribution",
            )
    require(
        used == set(data["actors"]),
        "$.actors",
        "classification lookup contains unused actors",
    )


def build_manifest(reports: Iterable[MonthlyReport]) -> dict:
    """Validate full reports sequentially and retain only visualization data."""
    return _build_manifest(reports, check_reports=True)


def _build_manifest(reports: Iterable[MonthlyReport], *, check_reports: bool) -> dict:
    actors, months, repository_id, latest = {}, [], None, None
    seen = set()
    for report in reports:
        if check_reports:
            validate_report(report)
        m = report.metadata
        if repository_id is not None and repository_id != m.repository_node_id:
            raise ReportWriteError("all reports must belong to the same repository")
        repository_id = m.repository_node_id
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
        months.append(
            {
                "metadata": {key: metadata[key] for key in METADATA_KEYS},
                "summaries": to_data(report.summaries),
            }
        )
        if latest is None or m.month > latest.month:
            latest = m
    if latest is None:
        raise ReportWriteError("no monthly reports found")
    data = {
        "schema_version": "1",
        "repository": {
            "node_id": latest.repository_node_id,
            "full_name": latest.repository_full_name,
            "url": latest.repository_url,
        },
        "actors": dict(sorted(actors.items())),
        "months": sorted(months, key=lambda item: item["metadata"]["month"]),
    }
    validate_manifest(data)
    return data


def write_manifest(
    report_directory: str | os.PathLike, *, output_path: str | os.PathLike | None = None
) -> Path:
    """Rebuild an atomic manifest; source reports must not change concurrently."""
    directory = Path(report_directory)
    target = directory / "manifest.json" if output_path is None else Path(output_path)
    if MONTHLY_FILE.fullmatch(target.name):
        raise ReportWriteError("manifest output must not overwrite a monthly report")

    def reports():
        for path in sorted(directory.iterdir()):
            if not MONTHLY_FILE.fullmatch(path.name) or path.is_dir():
                continue
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

    # report_from_json has already performed full event and summary validation.
    data = _build_manifest(reports(), check_reports=False)
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
        validate_manifest(json.loads(temporary.read_text(encoding="utf-8")))
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target

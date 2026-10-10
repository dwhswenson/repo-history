"""Validated JSON conversion without filesystem writes."""

import json
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from typing import Any

from .errors import ReportValidationError
from .models import (
    METADATA_CLASSES,
    Activity,
    Actor,
    Credit,
    GitIdentity,
    MonthlyReport,
    MonthlySummaries,
    PopulationCounts,
    ReportMetadata,
    Subject,
)


def to_data(value: Any, path: str = "$") -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ReportValidationError(path, "timezone-aware datetime required")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: to_data(getattr(value, f.name), f"{path}.{f.name}")
            for f in fields(value)
            if not (
                isinstance(value, ReportMetadata)
                and f.name == "commit_history"
                and getattr(value, f.name) is None
            )
        }
    if isinstance(value, dict):
        return {key: to_data(item, f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_data(item, f"{path}[{i}]") for i, item in enumerate(value)]
    return value


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def from_data(data: dict) -> MonthlyReport:
    metadata = data["metadata"].copy()
    for key in (
        "month_start",
        "month_end",
        "collection_started_at",
        "collection_finished_at",
    ):
        metadata[key] = parse_time(metadata[key])
    metadata["coverage_limitations"] = tuple(metadata["coverage_limitations"])
    activities = []
    for item in data["activities"]:
        details = item["metadata"].copy()
        for key in ("authored_at", "created_at", "published_at", "submitted_at"):
            if key in details and details[key] is not None:
                details[key] = parse_time(details[key])
        if item["type"] == "commit":
            details["committer"] = GitIdentity(**details["committer"])
            details["parent_shas"] = tuple(details["parent_shas"])
        activities.append(
            Activity(
                item["id"],
                item["type"],
                parse_time(item["occurred_at"]),
                item["url"],
                Subject(**item["subject"]),
                tuple(Credit(**c) for c in item["credits"]),
                METADATA_CLASSES[item["type"]](**details),
            )
        )
    summary = data["summaries"].copy()
    summary["distinct_actors"] = PopulationCounts(**summary["distinct_actors"])
    summary["distinct_actors_by_type"] = {
        k: PopulationCounts(**v) for k, v in summary["distinct_actors_by_type"].items()
    }
    return MonthlyReport(
        ReportMetadata(**metadata),
        tuple(Actor(**a) for a in data["actors"]),
        tuple(activities),
        MonthlySummaries(**summary),
        data["schema_version"],
    )


def report_to_json(report: MonthlyReport) -> str:
    from .validation import validate_report

    validate_report(report)
    data = to_data(report)
    data["actors"].sort(key=lambda a: a["key"])
    # Sort by actual instants, including variable fractional precision.
    data["activities"].sort(key=lambda a: (parse_time(a["occurred_at"]), a["id"]))
    for activity in data["activities"]:
        activity["credits"].sort(key=lambda c: (c["actor_key"] or "", c["role"]))
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def report_from_json(text: str) -> MonthlyReport:
    from .validation import validate_data

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ReportValidationError("$", f"duplicate JSON member {key!r}")
            result[key] = value
        return result

    try:
        data = json.loads(text, object_pairs_hook=object_pairs)
    except (ValueError, TypeError) as exc:
        if isinstance(exc, ReportValidationError):
            raise
        raise ReportValidationError("$", f"invalid JSON: {exc}") from exc
    validate_data(data)
    return from_data(data)

"""Locally resolved schema contracts and shared metadata invariants."""

import json
from datetime import datetime, timezone
from functools import lru_cache
from importlib.resources import files

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from .errors import ReportValidationError


def load_contract(name):
    return json.loads(
        files("repo_history").joinpath(f"schemas/{name}-v1.json").read_text()
    )


def schema_validator(schema):
    """Resolve all shared definitions from the installed package, never HTTP."""
    common = load_contract("common")
    registry = Registry().with_resource(common["$id"], Resource.from_contents(common))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(
        schema, registry=registry, format_checker=FormatChecker()
    )


@lru_cache(maxsize=None)
def contract_validator(name):
    return schema_validator(load_contract(name))


def require(condition, path, message):
    if not condition:
        raise ReportValidationError(path, message)


def validate_structure(name, data):
    error = next(contract_validator(name).iter_errors(data), None)
    if error is not None:
        path = "$" + "".join(
            f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.path
        )
        raise ReportValidationError(path, error.message)


def month_bounds(month):
    try:
        y, m = map(int, month.split("-"))
        return datetime(y, m, 1, tzinfo=timezone.utc), datetime(
            y + (m == 12), m % 12 + 1, 1, tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ReportValidationError(
            "$.metadata.month", "unsupported calendar month"
        ) from exc


def validate_calendar(metadata, path):
    from .serialization import parse_time

    start, end = month_bounds(metadata["month"])
    require(
        parse_time(metadata["month_start"]) == start
        and parse_time(metadata["month_end"]) == end,
        path,
        "month boundaries must match reporting month in UTC",
    )
    return start, end


def validate_run(run, path, runs):
    from .serialization import parse_time

    previous = runs.setdefault(run["run_id"], run)
    require(previous == run, path, "conflicting descriptors for run ID")
    start, finish = parse_time(run["started_at"]), parse_time(run["finished_at"])
    require(start <= finish, path, "invalid collection interval")
    since, until = run["collection_since"], run["collection_until"]
    if since is not None and until is not None:
        require(
            parse_time(since) < parse_time(until), path, "invalid collection bounds"
        )
    first, last = run["requested_start_month"], run["requested_end_month"]
    for month in (first, last):
        if month is not None:
            month_bounds(month)
    if first is not None and last is not None:
        require(first <= last, path, "invalid requested month range")


def validate_repository(repo, path, runs):
    from .serialization import parse_time

    month, proof = repo["history_start_month"], repo["history_start_established_by"]
    require(
        (month is None) == (proof is None),
        path,
        "history start and evidence must both be present or null",
    )
    if proof is not None:
        month_bounds(month)
        validate_run(proof, path + ".history_start_established_by", runs)
        require(
            proof["mode"] == "rebuild"
            and proof["collection_since"] is None
            and proof["parent_discovery"] == proof["commit_history"] == "full",
            path,
            "history start requires full-history evidence",
        )
        require(
            month <= parse_time(repo["created_at"]).strftime("%Y-%m"),
            path,
            "history starts after repository creation month",
        )


def validate_metadata(metadata, path, runs):
    from .serialization import parse_time

    start, end = validate_calendar(metadata, path)
    validate_repository(metadata["repository"], path + ".repository", runs)
    p = metadata["provenance"]
    for k, run in p.items():
        validate_run(run, path + ".provenance." + k, runs)
    created, rebuilt, updated = (
        p[k] for k in ("created_by", "last_rebuilt_by", "last_updated_by")
    )
    require(
        parse_time(created["started_at"])
        <= parse_time(rebuilt["started_at"])
        <= parse_time(updated["started_at"]),
        path,
        "invalid provenance chronology",
    )
    require(
        created["mode"] == rebuilt["mode"] == "rebuild",
        path,
        "creation and last rebuild require rebuild runs",
    )
    if updated["mode"] == "rebuild":
        require(updated == rebuilt, path, "latest rebuild must identify latest update")
    observed = parse_time(metadata["coverage"]["as_of"])
    require(start <= observed, path, "future month")
    require(
        parse_time(updated["started_at"])
        <= observed
        <= parse_time(updated["finished_at"]),
        path,
        "coverage must fall within latest collection interval",
    )
    require(
        metadata["ongoing"] == (observed < end),
        path,
        "ongoing must reflect coverage as_of",
    )
    require(
        (metadata["default_branch"] is None)
        == (metadata["default_branch_head"] is None),
        path,
        "branch and head must both be present or null",
    )


def choose_repository(repositories):
    """Use latest display metadata and retain the earliest established boundary."""
    from dataclasses import replace

    values = list(repositories)
    latest = values[-1]
    require(
        all(
            r.node_id == latest.node_id and r.created_at == latest.created_at
            for r in values
        ),
        "$.repository",
        "conflicting repository identity or creation timestamp",
    )
    known = [r for r in values if r.history_start_month is not None]
    if known:
        first = min(known, key=lambda r: r.history_start_month)
        latest = replace(
            latest,
            history_start_month=first.history_start_month,
            history_start_established_by=first.history_start_established_by,
        )
    return latest

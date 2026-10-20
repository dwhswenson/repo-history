"""Authoritative schema and cross-record validation."""

import json
from dataclasses import asdict
from functools import lru_cache
from importlib.resources import files

from .contracts import contract_validator, schema_validator, validate_metadata
from .errors import ReportValidationError
from .identities import git_identity_key
from .models import METADATA_CLASSES, MonthlyReport
from .serialization import from_data, to_data
from .summaries import derive_summaries


@lru_cache(maxsize=1)
def _validator():
    return contract_validator("monthly-report")


def load_schema() -> dict:
    """Return a fresh schema dictionary from the installed package."""
    return json.loads(
        files("repo_history")
        .joinpath("schemas/monthly-report-v1.json")
        .read_text(encoding="utf-8")
    )


def _require(condition: bool, path: str, message: str) -> None:
    if not condition:
        raise ReportValidationError(path, message)


def validate_data(data: dict, *, check_summaries: bool = True) -> None:
    errors = sorted(_validator().iter_errors(data), key=lambda e: str(list(e.path)))
    if errors:
        error = errors[0]
        # oneOf errors often hide the useful failure in the matching activity branch.
        if error.validator == "oneOf" and isinstance(error.instance, dict):
            kind = error.instance.get("type")
            schema = load_schema()
            if isinstance(kind, str) and kind in METADATA_CLASSES:
                branch = {
                    "$schema": schema["$schema"],
                    "$defs": schema["$defs"],
                    "$ref": f"#/$defs/{kind}_activity",
                }
                detail = next(
                    schema_validator(branch).iter_errors(error.instance),
                    None,
                )
                if detail is not None:
                    path = [*error.absolute_path, *detail.absolute_path]
                    raise ReportValidationError(_path(path), detail.message)
        raise ReportValidationError(_path(error.absolute_path), error.message)
    report = from_data(data)
    _validate_semantics(report, check_summaries=check_summaries)


def _path(parts) -> str:
    return "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in parts)


def _validate_model_types(report: MonthlyReport) -> None:
    _require(isinstance(report, MonthlyReport), "$", "MonthlyReport required")
    for i, activity in enumerate(report.activities):
        expected = (
            METADATA_CLASSES.get(activity.type)
            if isinstance(activity.type, str)
            else None
        )
        _require(
            expected is not None and type(activity.metadata) is expected,
            f"$.activities[{i}].metadata",
            "metadata class does not match activity type",
        )


def validate_report(report: MonthlyReport) -> None:
    _validate_model_types(report)
    validate_data(to_data(report))


def _validate_semantics(report: MonthlyReport, *, check_summaries: bool) -> None:
    m = report.metadata
    path = "$.metadata"
    validate_metadata(to_data(m), path, {})
    start, end = m.month_start, m.month_end
    if (
        m.repository.history_start_month is not None
        and m.month < m.repository.history_start_month
    ):
        _require(
            not report.activities, path, "activity precedes established history start"
        )
    registry = {}
    for i, actor in enumerate(report.actors):
        p = f"$.actors[{i}]"
        _require(actor.key not in registry, p + ".key", "duplicate actor key")
        registry[actor.key] = actor
        if actor.classification == "unlinked_git":
            _require(
                actor.github_node_id is None
                and actor.database_id is None
                and actor.login is None
                and actor.url is None,
                p,
                "unlinked Git actor cannot carry linked account metadata",
            )
            _require(
                actor.key == git_identity_key(actor.git_email, actor.git_name),
                p + ".key",
                "does not match estimated Git identity",
            )
        else:
            _require(
                actor.github_node_id is not None and actor.key == actor.github_node_id,
                p + ".key",
                "linked identity must equal GitHub node ID",
            )

    def reference(key: str | None, p: str) -> None:
        _require(key is None or key in registry, p, "unknown actor reference")

    seen = set()
    for i, activity in enumerate(report.activities):
        p = f"$.activities[{i}]"
        _require(activity.id not in seen, p + ".id", "duplicate event ID")
        seen.add(activity.id)
        _require(
            start <= activity.occurred_at < end,
            p + ".occurred_at",
            "outside requested month",
        )
        kind = activity.type
        subject_kind = (
            "commit"
            if kind == "commit"
            else ("issue" if kind.startswith("issue_") else "pull_request")
        )
        _require(
            activity.subject.kind == subject_kind,
            p + ".subject.kind",
            "wrong subject kind",
        )
        _require(
            (activity.subject.number is None) == (subject_kind == "commit"),
            p + ".subject.number",
            "issue/PR number required; commit number must be null",
        )
        reference(activity.subject.author_key, p + ".subject.author_key")
        roles = (
            {"author", "co_author"}
            if kind == "commit"
            else {
                {
                    "pr_merged": "merger",
                    "issue_closed": "closer",
                    "issue_reopened": "reopener",
                }.get(kind, "author")
            }
        )
        for j, credit in enumerate(activity.credits):
            reference(credit.actor_key, f"{p}.credits[{j}].actor_key")
            _require(
                credit.role in roles,
                f"{p}.credits[{j}].role",
                "role incompatible with activity",
            )
        details = activity.metadata
        if kind == "commit":
            expected_id = f"commit:{m.repository.node_id}:{details.sha}"
            reference(details.committer.actor_key, p + ".metadata.committer.actor_key")
            _require(
                any(
                    c.role == "author" and c.actor_key == activity.subject.author_key
                    for c in activity.credits
                ),
                p + ".credits",
                "subject author credit required",
            )
            _require(
                m.default_branch_head is not None,
                p,
                "commit requires captured history head",
            )
            _require(
                len(set(details.parent_shas)) == len(details.parent_shas),
                p + ".metadata.parent_shas",
                "duplicate parent SHA",
            )
        else:
            expected_id = f"{kind}:{details.source_node_id}"
            _require(
                len(activity.credits) == 1,
                p + ".credits",
                "exactly one performer credit required",
            )
        _require(
            activity.id == expected_id,
            p + ".id",
            "does not match stable source identity",
        )
        if kind in ("pr_opened", "issue_opened"):
            _require(
                details.source_node_id == activity.subject.node_id,
                p + ".metadata.source_node_id",
                "opening must reference subject node",
            )
            _require(
                activity.credits[0].actor_key == activity.subject.author_key,
                p + ".credits",
                "opening credited actor must be subject author",
            )
        if kind == "pr_review":
            _require(
                details.state is None or details.state.upper() != "PENDING",
                p + ".metadata.state",
                "pending reviews are not submitted activity",
            )
            _require(
                details.submitted_at == activity.occurred_at,
                p + ".metadata.submitted_at",
                "must match occurrence timestamp",
            )
        if kind == "pr_review_comment":
            _require(
                details.publication_confirmed,
                p + ".metadata.publication_confirmed",
                "publication must be confirmed",
            )
            fallback = details.published_at is None
            _require(
                details.timestamp_fallback == fallback,
                p + ".metadata.timestamp_fallback",
                "fallback must match missing publication timestamp",
            )
            _require(
                activity.occurred_at
                == (details.created_at if fallback else details.published_at),
                p + ".occurred_at",
                "must use publication timestamp or recorded fallback",
            )
    if check_summaries:
        expected = derive_summaries(report.actors, report.activities)
        _require(
            asdict(report.summaries) == asdict(expected),
            "$.summaries",
            "does not agree with credited activities",
        )

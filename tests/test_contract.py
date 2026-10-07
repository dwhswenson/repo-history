import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from repo_history_tracking import (
    ACTIVITY_TYPES,
    Actor,
    Credit,
    IssueOpenedMetadata,
    ReportValidationError,
    build_report,
    git_identity_key,
    load_schema,
    report_from_json,
    report_to_json,
    unresolved_git_actor,
    validate_report,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("name", ["mixed-2024-02.json", "empty-2024-02.json"])
def test_fixtures_round_trip(name):
    text = (FIXTURES / name).read_text()
    Draft202012Validator(load_schema(), format_checker=FormatChecker()).validate(
        json.loads(text)
    )
    report = report_from_json(text)
    assert report_from_json(report_to_json(report)) == report
    assert report_to_json(report) == text


@pytest.mark.parametrize("kind", ACTIVITY_TYPES)
def test_every_metadata_branch(kind, mixed):
    activity = next(a for a in mixed.activities if a.type == kind)
    report = build_report(mixed.metadata, mixed.actors, [activity])
    assert report_from_json(report_to_json(report)) == report
    assert report.summaries.repository_counts[kind] == 1


@pytest.mark.parametrize("kind", ACTIVITY_TYPES)
def test_every_metadata_branch_rejects_wrong_fields(kind, mixed_data):
    activity = next(a for a in mixed_data["activities"] if a["type"] == kind)
    activity["metadata"]["body"] = "must not leak"
    with pytest.raises(ReportValidationError, match="body"):
        report_from_json(json.dumps(mixed_data))


def test_counts_and_context(mixed):
    s = mixed.summaries
    assert sum(s.repository_counts.values()) == 14
    assert s.repository_counts["commit"] == 2
    assert s.actor_counts["U_author"]["commit"] == 1
    assert s.actor_counts["U_author"]["pr_opened"] == 1
    assert s.actor_counts["U_author"]["pr_merged"] == 0
    assert s.actor_counts["U_performer"]["issue_closed"] == 2
    assert "U_context" not in s.actor_counts
    assert s.distinct_actors.total == 5
    assert (
        s.distinct_actors.human,
        s.distinct_actors.bot,
        s.distinct_actors.unresolved_git,
    ) == (2, 1, 2)
    assert s.unattributed_total == 1
    assert s.unattributed_by_type["commit"] == 0
    assert s.distinct_actors_by_type["issue_comment"].total == 0


def test_builder_normalizes_duplicates_and_order(mixed):
    activities = [replace(a, credits=a.credits + a.credits) for a in mixed.activities]
    report = build_report(mixed.metadata, reversed(mixed.actors), reversed(activities))
    assert report == mixed
    assert report_to_json(
        replace(
            mixed,
            actors=tuple(reversed(mixed.actors)),
            activities=tuple(reversed(mixed.activities)),
        )
    ) == report_to_json(mixed)


def test_missing_credit_never_merges_unknown_people(mixed):
    comments = [a for a in mixed.activities if a.type == "issue_comment"]
    original = comments[0]
    comments.append(
        replace(
            original,
            id="issue_comment:IC_2",
            metadata=replace(original.metadata, source_node_id="IC_2"),
        )
    )
    result = build_report(mixed.metadata, mixed.actors, comments)
    assert result.summaries.unattributed_total == 2
    assert result.summaries.distinct_actors.total == 0
    assert result.summaries.actor_counts == {}


@pytest.mark.parametrize(
    "email,name,kind,value",
    [
        (" Example@Email.org ", "ignored", "email", "example@email.org"),
        (None, " Name Only ", "name", "Name Only"),
        ("  ", "Name Only", "name", "Name Only"),
    ],
)
def test_identity_hash(email, name, kind, value):
    key = f"git:{kind}:{sha256(value.encode()).hexdigest()}"
    assert git_identity_key(email, name) == key
    actor = unresolved_git_actor(email, name)
    assert actor.key == key
    assert actor.git_email == email
    assert actor.git_name == name
    assert actor.classification == "unresolved_git"


def test_identity_namespaces_and_missing():
    assert git_identity_key("Same", None) != git_identity_key(None, "Same")
    assert git_identity_key(None, "Name") != git_identity_key(None, "name")
    assert git_identity_key("EMAIL", None) == git_identity_key("email", None)
    assert unresolved_git_actor(" ", None) is None
    assert git_identity_key(None, " ") is None
    assert unresolved_git_actor(None, "tool[bot]").classification == "unresolved_git"


def test_linked_identity_survives_login_change(mixed):
    actors = [
        replace(a, login="new-login") if a.key == "U_author" else a
        for a in mixed.actors
    ]
    report = build_report(mixed.metadata, actors, mixed.activities)
    assert report.summaries == mixed.summaries


@pytest.mark.parametrize("state", [None, "DISMISSED", "FUTURE_STATE"])
def test_open_review_states(state, mixed):
    review = next(a for a in mixed.activities if a.type == "pr_review")
    review = replace(review, metadata=replace(review.metadata, state=state))
    result = build_report(mixed.metadata, mixed.actors, [review])
    assert result.activities[0].metadata.state == state


@pytest.mark.parametrize("state", ["PENDING", "pending"])
def test_pending_review_rejected(state, mixed):
    review = next(a for a in mixed.activities if a.type == "pr_review")
    with pytest.raises(ReportValidationError, match="pending"):
        build_report(
            mixed.metadata,
            mixed.actors,
            [replace(review, metadata=replace(review.metadata, state=state))],
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("publication_confirmed", False),
        ("timestamp_fallback", False),
        ("published_at", datetime(2024, 2, 15, 12, tzinfo=timezone.utc)),
    ],
)
def test_inline_fallback_rejected(field, value, mixed):
    activity = next(
        a
        for a in mixed.activities
        if a.type == "pr_inline_review_comment" and a.metadata.timestamp_fallback
    )
    with pytest.raises(ReportValidationError):
        build_report(
            mixed.metadata,
            mixed.actors,
            [replace(activity, metadata=replace(activity.metadata, **{field: value}))],
        )


@pytest.mark.parametrize(
    "label,start,end",
    [
        ("2024-02", "2024-02-01", "2024-03-01"),
        ("2023-02", "2023-02-01", "2023-03-01"),
        ("2024-12", "2024-12-01", "2025-01-01"),
    ],
)
def test_calendar_boundaries(label, start, end, empty):
    start = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    metadata = replace(
        empty.metadata,
        month=label,
        month_start=start,
        month_end=end,
        collection_started_at=end,
        collection_finished_at=end,
    )
    report = build_report(metadata, [], [])
    assert report.metadata.month_end == end
    ongoing = replace(
        metadata, collection_started_at=start, collection_finished_at=end, ongoing=True
    )
    assert build_report(ongoing, [], []).metadata.ongoing
    with pytest.raises(ReportValidationError, match="future month"):
        build_report(
            replace(metadata, collection_started_at=start - timedelta(seconds=1)),
            [],
            [],
        )
    with pytest.raises(ReportValidationError, match="ongoing"):
        build_report(replace(metadata, ongoing=True), [], [])


def test_half_open_interval(mixed):
    event = next(a for a in mixed.activities if a.type == "issue_comment")
    build_report(
        mixed.metadata,
        mixed.actors,
        [replace(event, occurred_at=mixed.metadata.month_start)],
    )
    build_report(
        mixed.metadata,
        mixed.actors,
        [
            replace(
                event, occurred_at=mixed.metadata.month_end - timedelta(microseconds=1)
            )
        ],
    )
    for timestamp in [
        mixed.metadata.month_start - timedelta(microseconds=1),
        mixed.metadata.month_end,
    ]:
        with pytest.raises(ReportValidationError, match="outside"):
            build_report(
                mixed.metadata, mixed.actors, [replace(event, occurred_at=timestamp)]
            )


def test_timezone_normalization_and_precision_order(mixed):
    event = next(a for a in mixed.activities if a.type == "issue_comment")
    at = datetime(2024, 2, 10, 6, tzinfo=timezone(timedelta(hours=-6)))
    first = replace(event, occurred_at=at)
    second = replace(
        event,
        id="issue_comment:IC_2",
        occurred_at=at + timedelta(microseconds=1),
        metadata=replace(event.metadata, source_node_id="IC_2"),
    )
    report = build_report(mixed.metadata, mixed.actors, [second, first])
    data = json.loads(report_to_json(report))
    assert data["activities"][0]["occurred_at"] == "2024-02-10T12:00:00Z"
    assert data["activities"][1]["occurred_at"] == "2024-02-10T12:00:00.000001Z"
    with pytest.raises(ReportValidationError, match="timezone-aware"):
        build_report(
            mixed.metadata,
            mixed.actors,
            [replace(event, occurred_at=at.replace(tzinfo=None))],
        )


# Mutate a single field on an otherwise independently specified valid fixture.
@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), "2"),
        (("extra",), 1),
        (("metadata", "month"), "2024-13"),
        (("metadata", "month_start"), "2024-02-02T00:00:00Z"),
        (("metadata", "month_end"), "2024-02-29T00:00:00Z"),
        (("metadata", "month"), "0000-02"),
        (("metadata", "ongoing"), True),
        (("metadata", "collection_finished_at"), "2024-03-01T00:00:00Z"),
        (("metadata", "default_branch_head"), None),
        (("metadata", "repository_url"), "not-a-url"),
        (("actors", 0, "key"), "not-the-node-id"),
        (("actors", 0, "classification"), "unknown"),
        (("activities", 0, "id"), "wrong-id"),
        (("activities", 0, "type"), "new_type"),
        (("activities", 0, "type"), []),
        (("activities", 0, "type"), {}),
        (("activities", 0, "occurred_at"), "2024-02-31T00:00:00Z"),
        (("activities", 0, "occurred_at"), "2024-02-01T00:00:00"),
        (("activities", 0, "occurred_at"), "2024-02-01T00:00:00+00:00"),
        (("activities", 0, "subject", "kind"), "issue"),
        (("activities", 0, "subject", "number"), 1),
        (("activities", 0, "subject", "author_key"), "unknown"),
        (("activities", 0, "subject", "title"), "full\nmessage"),
        (("activities", 0, "credits", 0, "role"), "closer"),
        (("activities", 0, "credits", 0, "role"), "committer"),
        (("activities", 0, "credits", 0, "actor_key"), "unknown"),
        (("activities", 0, "credits"), []),
        (("activities", 0, "metadata", "sha"), "bad-sha"),
        (("activities", 0, "metadata", "committer", "actor_key"), "unknown"),
        (("activities", 0, "metadata", "parent_shas"), ["b" * 40, "b" * 40]),
        (("activities", 0, "body"), "secret"),
        (("summaries", "repository_counts", "commit"), -1),
        (("summaries", "repository_counts", "commit"), True),
        (("summaries", "repository_counts", "commit"), 3),
        (("summaries", "distinct_actors", "human"), 8),
        (("summaries", "unattributed_total"), 2),
        (("summaries", "actor_counts", "U_author", "pr_opened"), 0),
    ],
)
def test_malformed_input_rejected(path, value, mixed_data):
    target = mixed_data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ReportValidationError) as exc:
        report_from_json(json.dumps(mixed_data))
    assert exc.value.path.startswith("$")


@pytest.mark.parametrize("section", ["actors", "activities"])
def test_duplicate_records_rejected(section, mixed_data):
    mixed_data[section].append(mixed_data[section][0])
    with pytest.raises(ReportValidationError, match="duplicate"):
        report_from_json(json.dumps(mixed_data))


def test_duplicate_credits_rejected_on_read(mixed_data):
    credits = mixed_data["activities"][0]["credits"]
    credits.append(credits[0])
    with pytest.raises(ReportValidationError):
        report_from_json(json.dumps(mixed_data))


@pytest.mark.parametrize(
    "text", ["{", "[]", "null", '{"schema_version":"1","schema_version":"1"}']
)
def test_bad_json(text):
    with pytest.raises(ReportValidationError):
        report_from_json(text)


def test_wrong_dataclass_rejected(mixed):
    commit = mixed.activities[0]
    with pytest.raises(ReportValidationError, match="metadata class"):
        build_report(
            mixed.metadata,
            mixed.actors,
            [replace(commit, metadata=IssueOpenedMetadata("wrong"))],
        )


def test_unresolved_identity_mismatch(mixed):
    git_actor = next(a for a in mixed.actors if a.classification == "unresolved_git")
    actors = [
        replace(a, git_email="different@example.org") if a == git_actor else a
        for a in mixed.actors
    ]
    with pytest.raises(ReportValidationError, match="estimated Git identity"):
        build_report(mixed.metadata, actors, mixed.activities)
    with pytest.raises(ReportValidationError, match="linked account metadata"):
        build_report(
            mixed.metadata,
            [
                Actor(
                    git_actor.key,
                    "unresolved_git",
                    login="inferred",
                    git_email=git_actor.git_email,
                    git_name=git_actor.git_name,
                )
            ],
            [],
        )


def test_schema_load_is_not_mutable_shared_state():
    schema = load_schema()
    Draft202012Validator.check_schema(schema)
    schema.clear()
    assert load_schema()["$defs"]


def test_validation_reports_useful_nested_path(mixed_data):
    mixed_data["activities"][0]["metadata"]["sha"] = "bad"
    with pytest.raises(ReportValidationError) as exc:
        report_from_json(json.dumps(mixed_data))
    assert exc.value.path == "$.activities[0].metadata.sha"


def test_semantic_review_timestamp_and_opening_author(mixed):
    for kind, field, value in [
        ("pr_review", "submitted_at", mixed.metadata.month_start),
        ("pr_opened", "source_node_id", "different"),
    ]:
        event = next(a for a in mixed.activities if a.type == kind)
        with pytest.raises(ReportValidationError):
            build_report(
                mixed.metadata,
                mixed.actors,
                [replace(event, metadata=replace(event.metadata, **{field: value}))],
            )
    opening = next(a for a in mixed.activities if a.type == "pr_opened")
    with pytest.raises(ReportValidationError, match="subject author"):
        build_report(
            mixed.metadata,
            mixed.actors,
            [replace(opening, credits=(Credit("U_performer", "author"),))],
        )


def test_empty_and_context_only_month(empty):
    report = build_report(
        empty.metadata, [Actor("U_context", "human", github_node_id="U_context")], []
    )
    assert report.summaries == empty.summaries
    assert all(v == 0 for v in report.summaries.repository_counts.values())
    validate_report(report)

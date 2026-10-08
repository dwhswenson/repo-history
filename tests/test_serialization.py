"""Tests for repo_history_tracking.serialization."""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from repo_history_tracking import (
    ReportValidationError,
    build_report,
    load_schema,
    report_from_json,
    report_to_json,
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


@pytest.mark.parametrize(
    "text", ["{", "[]", "null", '{"schema_version":"1","schema_version":"1"}']
)
def test_bad_json(text):
    with pytest.raises(ReportValidationError):
        report_from_json(text)

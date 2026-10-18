"""Tests for repo_history.serialization."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from repo_history import (
    ReportValidationError,
    build_report,
    report_from_json,
    report_to_json,
)
from repo_history.contracts import contract_validator

from .helpers import replace

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("name", ["mixed-2024-02.json", "empty-2024-02.json"])
def test_fixtures_round_trip(name):
    text = (FIXTURES / name).read_text()
    contract_validator("monthly-report").validate(json.loads(text))
    report = report_from_json(text)
    assert report.metadata.provenance.last_updated_by.parent_discovery is None
    assert report_from_json(report_to_json(report)) == report
    assert report_to_json(report) == text


@pytest.mark.parametrize("strategy", ["bounded", "full"])
def test_parent_discovery_round_trip(mixed, strategy):
    report = replace(mixed, metadata=replace(mixed.metadata, parent_discovery=strategy))
    text = report_to_json(report)
    assert (
        json.loads(text)["metadata"]["provenance"]["last_updated_by"][
            "parent_discovery"
        ]
        == strategy
    )
    assert report_from_json(text) == report


@pytest.mark.parametrize("strategy", ["unknown", True, 0])
def test_invalid_parent_discovery_json_rejected(mixed, strategy):
    data = json.loads(report_to_json(mixed))
    data["metadata"]["provenance"]["last_updated_by"]["parent_discovery"] = strategy
    with pytest.raises(ReportValidationError, match="parent_discovery"):
        report_from_json(json.dumps(data))


@pytest.mark.parametrize("strategy", ["unknown", True, 0])
def test_invalid_parent_discovery_model_rejected(mixed, strategy):
    report = replace(mixed, metadata=replace(mixed.metadata, parent_discovery=strategy))
    with pytest.raises(ReportValidationError, match="parent_discovery"):
        report_to_json(report)


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

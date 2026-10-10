"""Tests for repo_history.models."""

from pathlib import Path

import pytest

from repo_history import (
    ACTIVITY_TYPES,
    build_report,
    report_from_json,
    report_to_json,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("kind", ACTIVITY_TYPES)
def test_every_metadata_branch(kind, mixed):
    activity = next(a for a in mixed.activities if a.type == kind)
    report = build_report(mixed.metadata, mixed.actors, [activity])
    assert report_from_json(report_to_json(report)) == report
    assert report.summaries.repository_counts[kind] == 1

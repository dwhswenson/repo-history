"""Tests for repo_history_tracking.reports."""

from dataclasses import replace
from pathlib import Path

from repo_history_tracking import (
    build_report,
    report_to_json,
)

FIXTURES = Path(__file__).parent / "fixtures"


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

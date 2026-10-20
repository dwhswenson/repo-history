"""Tests for repo_history.summaries."""

from pathlib import Path

from repo_history import (
    Actor,
    build_report,
    validate_report,
)

from .helpers import replace

FIXTURES = Path(__file__).parent / "fixtures"


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
        s.distinct_actors.unlinked_git,
    ) == (2, 1, 2)
    assert s.unattributed_total == 1
    assert s.unattributed_by_type["commit"] == 0
    assert s.distinct_actors_by_type["issue_comment"].total == 0


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


def test_empty_and_context_only_month(empty):
    report = build_report(
        empty.metadata, [Actor("U_context", "human", github_node_id="U_context")], []
    )
    assert report.summaries == empty.summaries
    assert all(v == 0 for v in report.summaries.repository_counts.values())
    validate_report(report)

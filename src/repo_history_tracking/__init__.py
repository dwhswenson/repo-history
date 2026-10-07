"""Monthly repository activity contract, independent of GitHub collection."""

from .errors import ReportValidationError
from .identities import git_identity_key, unresolved_git_actor
from .models import (
    ACTIVITY_TYPES,
    Activity,
    Actor,
    CommitMetadata,
    Credit,
    GitIdentity,
    IssueClosedMetadata,
    IssueCommentMetadata,
    IssueOpenedMetadata,
    IssueReopenedMetadata,
    MonthlyReport,
    MonthlySummaries,
    PopulationCounts,
    PRConversationCommentMetadata,
    PRInlineReviewCommentMetadata,
    PRMergedMetadata,
    PROpenedMetadata,
    PRReviewMetadata,
    ReportMetadata,
    Subject,
)
from .reports import build_report
from .serialization import report_from_json, report_to_json
from .summaries import derive_summaries
from .validation import load_schema, validate_report

__all__ = [
    "ACTIVITY_TYPES",
    "Activity",
    "Actor",
    "CommitMetadata",
    "Credit",
    "GitIdentity",
    "IssueClosedMetadata",
    "IssueCommentMetadata",
    "IssueOpenedMetadata",
    "IssueReopenedMetadata",
    "MonthlyReport",
    "MonthlySummaries",
    "PopulationCounts",
    "PRConversationCommentMetadata",
    "PRInlineReviewCommentMetadata",
    "PRMergedMetadata",
    "PROpenedMetadata",
    "PRReviewMetadata",
    "ReportMetadata",
    "ReportValidationError",
    "Subject",
    "build_report",
    "derive_summaries",
    "git_identity_key",
    "load_schema",
    "report_from_json",
    "report_to_json",
    "unresolved_git_actor",
    "validate_report",
]

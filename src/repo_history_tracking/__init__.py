"""Monthly repository activity contract, independent of GitHub collection."""

from .authentication import resolve_token
from .errors import (
    AuthenticationError,
    GitHubAPIError,
    GraphQLError,
    PaginationError,
    QueryResourceError,
    RateLimitError,
    ReportValidationError,
    RepositoryUnavailableError,
    TransportError,
)
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
from .repository import RepositorySnapshot, lookup_repository
from .serialization import report_from_json, report_to_json
from .summaries import derive_summaries
from .transport import GraphQLClient
from .validation import load_schema, validate_report

__all__ = [
    "AuthenticationError",
    "GitHubAPIError",
    "GraphQLClient",
    "GraphQLError",
    "PaginationError",
    "QueryResourceError",
    "RateLimitError",
    "RepositorySnapshot",
    "RepositoryUnavailableError",
    "TransportError",
    "lookup_repository",
    "resolve_token",
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

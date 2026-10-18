"""Monthly repository activity contract, independent of GitHub collection."""

from .authentication import resolve_token
from .backfill import backfill
from .collection import collect_month
from .errors import (
    AuthenticationError,
    GitHubAPIError,
    GraphQLError,
    PaginationError,
    QueryResourceError,
    RateLimitError,
    ReportValidationError,
    ReportWriteError,
    RepositoryUnavailableError,
    TransportError,
)
from .history import collect_history, history_to_months
from .identities import git_identity_key, unresolved_git_actor
from .manifest import build_manifest, write_manifest
from .models import (
    ACTIVITY_TYPES,
    Activity,
    Actor,
    CollectionRun,
    CommitMetadata,
    Coverage,
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
    Provenance,
    PRReviewMetadata,
    ReportMetadata,
    RepositoryHistory,
    RepositoryMetadata,
    Subject,
)
from .reports import build_report
from .repository import RepositorySnapshot, lookup_repository
from .serialization import report_from_json, report_to_json
from .state import load_collection_state_schema, validate_collection_state
from .summaries import derive_summaries
from .transport import GraphQLClient
from .validation import load_schema, validate_report
from .writing import write_report

__all__ = [
    "CollectionRun",
    "RepositoryMetadata",
    "Provenance",
    "Coverage",
    "load_collection_state_schema",
    "validate_collection_state",
    "build_manifest",
    "write_manifest",
    "collect_history",
    "history_to_months",
    "RepositoryHistory",
    "backfill",
    "write_report",
    "ReportWriteError",
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
    "collect_month",
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

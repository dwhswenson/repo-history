"""Transport-independent report records. Validation is explicit at report boundaries."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ActivityType = Literal[
    "commit",
    "pr_opened",
    "pr_merged",
    "issue_opened",
    "issue_closed",
    "issue_reopened",
    "issue_comment",
    "pr_discussion_comment",
    "pr_review_comment",
    "pr_review",
]
ACTIVITY_TYPES = tuple(ActivityType.__args__)
ActorClass = Literal["human", "bot", "unlinked_git"]


@dataclass(frozen=True)
class Actor:
    key: str
    classification: ActorClass
    github_node_id: str | None = None
    database_id: int | None = None
    login: str | None = None
    display_name: str | None = None
    url: str | None = None
    git_name: str | None = None
    git_email: str | None = None


@dataclass(frozen=True)
class GitIdentity:
    name: str | None = None
    email: str | None = None
    actor_key: str | None = None


@dataclass(frozen=True)
class CollectionRun:
    run_id: str
    command: str
    mode: str
    collector_version: str
    started_at: datetime
    finished_at: datetime
    collection_since: datetime | None = None
    collection_until: datetime | None = None
    requested_start_month: str | None = None
    requested_end_month: str | None = None
    parent_discovery: str | None = None
    commit_history: str | None = None


@dataclass(frozen=True)
class RepositoryMetadata:
    node_id: str
    full_name: str
    url: str
    created_at: datetime
    history_start_month: str | None = None
    history_start_established_by: CollectionRun | None = None


@dataclass(frozen=True)
class Provenance:
    created_by: CollectionRun
    last_updated_by: CollectionRun
    last_rebuilt_by: CollectionRun


@dataclass(frozen=True)
class Coverage:
    as_of: datetime
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportMetadata:
    repository: RepositoryMetadata
    month: str
    month_start: datetime
    month_end: datetime
    provenance: Provenance
    coverage: Coverage
    is_partial: bool
    default_branch: str | None
    default_branch_head: str | None


@dataclass(frozen=True)
class Subject:
    kind: Literal["commit", "issue", "pull_request"]
    node_id: str
    title: str
    url: str | None
    number: int | None = None
    author_key: str | None = None


@dataclass(frozen=True)
class Credit:
    actor_key: str | None
    role: Literal["author", "co_author", "merger", "closer", "reopener"]


@dataclass(frozen=True)
class CommitMetadata:
    sha: str
    authored_at: datetime
    committer: GitIdentity
    parent_shas: tuple[str, ...] = ()


@dataclass(frozen=True)
class PROpenedMetadata:
    source_node_id: str


@dataclass(frozen=True)
class IssueOpenedMetadata:
    source_node_id: str


@dataclass(frozen=True)
class PRMergedMetadata:
    source_node_id: str
    merge_commit_sha: str | None


@dataclass(frozen=True)
class IssueClosedMetadata:
    source_node_id: str
    reason: str | None


@dataclass(frozen=True)
class IssueReopenedMetadata:
    source_node_id: str


@dataclass(frozen=True)
class IssueCommentMetadata:
    source_node_id: str


@dataclass(frozen=True)
class PRDiscussionCommentMetadata:
    """A comment in a pull request's general discussion."""

    source_node_id: str


@dataclass(frozen=True)
class PRReviewCommentMetadata:
    """A published inline comment or reply in a pull request review thread."""

    source_node_id: str
    review_node_id: str | None
    thread_node_id: str | None
    created_at: datetime
    published_at: datetime | None
    publication_confirmed: bool
    resolved: bool
    outdated: bool
    timestamp_fallback: bool


@dataclass(frozen=True)
class PRReviewMetadata:
    source_node_id: str
    submitted_at: datetime
    state: str | None


METADATA_CLASSES = dict(
    zip(
        ACTIVITY_TYPES,
        (
            CommitMetadata,
            PROpenedMetadata,
            PRMergedMetadata,
            IssueOpenedMetadata,
            IssueClosedMetadata,
            IssueReopenedMetadata,
            IssueCommentMetadata,
            PRDiscussionCommentMetadata,
            PRReviewCommentMetadata,
            PRReviewMetadata,
        ),
        strict=True,
    )
)
ActivityMetadata = (
    CommitMetadata
    | PROpenedMetadata
    | PRMergedMetadata
    | IssueOpenedMetadata
    | IssueClosedMetadata
    | IssueReopenedMetadata
    | IssueCommentMetadata
    | PRDiscussionCommentMetadata
    | PRReviewCommentMetadata
    | PRReviewMetadata
)


@dataclass(frozen=True)
class Activity:
    id: str
    type: ActivityType
    occurred_at: datetime
    url: str | None
    subject: Subject
    credits: tuple[Credit, ...]
    metadata: ActivityMetadata


@dataclass(frozen=True)
class PopulationCounts:
    total: int
    human: int
    bot: int
    unlinked_git: int


@dataclass(frozen=True)
class MonthlySummaries:
    repository_counts: dict[str, int]
    actor_counts: dict[str, dict[str, int]]
    distinct_actors: PopulationCounts
    distinct_actors_by_type: dict[str, PopulationCounts]
    unattributed_total: int
    unattributed_by_type: dict[str, int]


@dataclass(frozen=True)
class MonthlyReport:
    metadata: ReportMetadata
    actors: tuple[Actor, ...]
    activities: tuple[Activity, ...]
    summaries: MonthlySummaries
    schema_version: str = "1"


@dataclass(frozen=True)
class RepositoryHistory:
    """In-memory normalized history from one collection, before month selection."""

    repository_node_id: str
    repository_full_name: str
    repository_url: str
    repository_created_at: datetime
    default_branch: str | None
    default_branch_head: str | None
    collection_started_at: datetime
    collection_finished_at: datetime
    coverage_limitations: tuple[str, ...]
    actors: tuple[Actor, ...]
    activities: tuple[Activity, ...]
    run: CollectionRun | None = None

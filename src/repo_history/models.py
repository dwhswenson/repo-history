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
    "pr_conversation_comment",
    "pr_inline_review_comment",
    "pr_review",
]
ACTIVITY_TYPES = tuple(ActivityType.__args__)
ActorClass = Literal["human", "bot", "unresolved_git"]


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
class ReportMetadata:
    repository_node_id: str
    repository_full_name: str
    repository_url: str
    month: str
    month_start: datetime
    month_end: datetime
    collection_started_at: datetime
    collection_finished_at: datetime
    ongoing: bool
    default_branch: str | None
    default_branch_head: str | None
    coverage_limitations: tuple[str, ...] = ()
    commit_history: Literal["bounded", "full"] | None = None


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
class PRConversationCommentMetadata:
    source_node_id: str


@dataclass(frozen=True)
class PRInlineReviewCommentMetadata:
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
            PRConversationCommentMetadata,
            PRInlineReviewCommentMetadata,
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
    | PRConversationCommentMetadata
    | PRInlineReviewCommentMetadata
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
    unresolved_git: int


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

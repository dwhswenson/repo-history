"""Named operations and reusable fragments. Values travel only as variables."""

PAGE_INFO_FRAGMENT = """
fragment ForwardPageInfo on PageInfo {
  hasNextPage
  endCursor
}
"""

GIT_ACTOR_FRAGMENT = """
fragment CommitIdentity on GitActor {
  name
  email
  user {
    __typename
    id
    databaseId
    login
    name
    url
  }
}
"""

COMMIT_HISTORY_QUERY = (
    GIT_ACTOR_FRAGMENT
    + PAGE_INFO_FRAGMENT
    + """
query CommitHistory($id: ID!, $head: GitObjectID!, $first: Int!,
                    $after: String, $since: GitTimestamp, $until: GitTimestamp) {
  rateLimit { cost }
  node(id: $id) {
    ... on Repository {
      object(oid: $head) {
        ... on Commit {
          history(first: $first, after: $after, since: $since, until: $until) {
            nodes {
              id
              oid
              url
              messageHeadline
              authoredDate
              committedDate
              author { ...CommitIdentity }
              committer { ...CommitIdentity }
              authors(first: 10) {
                nodes { ...CommitIdentity }
                pageInfo { ...ForwardPageInfo }
              }
              parents(first: 10) {
                nodes { oid }
                pageInfo { ...ForwardPageInfo }
              }
            }
            pageInfo { ...ForwardPageInfo }
          }
        }
      }
    }
  }
}
"""
)

COMMIT_AUTHORS_QUERY = (
    GIT_ACTOR_FRAGMENT
    + PAGE_INFO_FRAGMENT
    + """
query CommitAuthors($id: ID!, $first: Int!, $after: String) {
  rateLimit { cost }
  node(id: $id) {
    ... on Commit {
      authors(first: $first, after: $after) {
        nodes { ...CommitIdentity }
        pageInfo { ...ForwardPageInfo }
      }
    }
  }
}
"""
)

COMMIT_PARENTS_QUERY = (
    PAGE_INFO_FRAGMENT
    + """
query CommitParents($id: ID!, $first: Int!, $after: String) {
  rateLimit { cost }
  node(id: $id) {
    ... on Commit {
      parents(first: $first, after: $after) {
        nodes { oid }
        pageInfo { ...ForwardPageInfo }
      }
    }
  }
}
"""
)

REPOSITORY_FRAGMENT = """
fragment RepositoryIdentity on Repository {
  id
  createdAt
  databaseId
  nameWithOwner
  url
  hasIssuesEnabled
  isEmpty
  defaultBranchRef {
    name
    target {
      __typename
      ... on Commit { oid }
    }
  }
}
"""

REPOSITORY_QUERY = (
    REPOSITORY_FRAGMENT
    + """
query RepositoryLookup($owner: String!, $name: String!) {
  rateLimit { cost }
  repository(owner: $owner, name: $name) {
    ...RepositoryIdentity
  }
}
"""
)

BACKFILL_REPOSITORY_QUERY = """
query BackfillRepository($owner: String!, $name: String!) {
  rateLimit { cost }
  repository(owner: $owner, name: $name) { id createdAt }
}
"""

ACTOR_FRAGMENT = """
fragment LifecycleActor on Actor {
  __typename
  login
  url
  ... on User { id databaseId name }
  ... on Bot { id databaseId }
}
"""

COMMIT_ACTOR_QUERY = (
    ACTOR_FRAGMENT
    + """
query CommitActor($id: ID!) {
  rateLimit { cost }
  node(id: $id) {
    ...LifecycleActor
  }
}
"""
)

ISSUE_SUBJECT_FRAGMENT = """
fragment LifecycleIssue on Issue {
  id
  number
  title
  url
  createdAt
  author { ...LifecycleActor }
}
"""

PR_SUBJECT_FRAGMENT = """
fragment LifecyclePullRequest on PullRequest {
  id
  number
  title
  url
  createdAt
  author { ...LifecycleActor }
}
"""

ISSUES_QUERY = (
    ACTOR_FRAGMENT
    + ISSUE_SUBJECT_FRAGMENT
    + PAGE_INFO_FRAGMENT
    + """
query RepositoryIssues($id: ID!, $first: Int!, $after: String) {
  rateLimit { cost }
  node(id: $id) {
    ... on Repository {
      issues(first: $first, after: $after) {
        nodes { ...LifecycleIssue }
        pageInfo { ...ForwardPageInfo }
      }
    }
  }
}
"""
)

PULL_REQUESTS_QUERY = (
    ACTOR_FRAGMENT
    + PR_SUBJECT_FRAGMENT
    + PAGE_INFO_FRAGMENT
    + """
query RepositoryPullRequests($id: ID!, $first: Int!, $after: String) {
  rateLimit { cost }
  node(id: $id) {
    ... on Repository {
      pullRequests(first: $first, after: $after) {
        nodes { ...LifecyclePullRequest }
        pageInfo { ...ForwardPageInfo }
      }
    }
  }
}
"""
)

ISSUE_EVENTS_FRAGMENT = """
fragment LifecycleClosure on ClosedEvent {
  id
  createdAt
  url
  stateReason
  actor { ...LifecycleActor }
}
fragment LifecycleReopening on ReopenedEvent {
  id
  createdAt
  actor { ...LifecycleActor }
}
"""

ISSUE_TIMELINE_QUERY = (
    ACTOR_FRAGMENT
    + ISSUE_EVENTS_FRAGMENT
    + PAGE_INFO_FRAGMENT
    + """
query IssueLifecycle($id: ID!, $first: Int!, $after: String) {
  rateLimit { cost }
  node(id: $id) {
    ... on Issue {
      timelineItems(first: $first, after: $after,
                    itemTypes: [CLOSED_EVENT, REOPENED_EVENT]) {
        nodes {
          __typename
          ...LifecycleClosure
          ...LifecycleReopening
        }
        pageInfo { ...ForwardPageInfo }
      }
    }
  }
}
"""
)

MERGE_EVENT_FRAGMENT = """
fragment LifecycleMerge on MergedEvent {
  id
  createdAt
  url
  actor { ...LifecycleActor }
  commit { oid }
}
"""

PR_TIMELINE_QUERY = (
    ACTOR_FRAGMENT
    + MERGE_EVENT_FRAGMENT
    + PAGE_INFO_FRAGMENT
    + """
query PullRequestLifecycle($id: ID!, $first: Int!, $after: String) {
  rateLimit { cost }
  node(id: $id) {
    ... on PullRequest {
      timelineItems(first: $first, after: $after, itemTypes: [MERGED_EVENT]) {
        nodes { __typename ...LifecycleMerge }
        pageInfo { ...ForwardPageInfo }
      }
    }
  }
}
"""
)


COMMENT_FRAGMENT = """
fragment ConversationComment on IssueComment {
  id
  createdAt
  url
  author { ...LifecycleActor }
}
"""


def _connection_query(operation, parent_type, field, fields, fragments=""):
    return (
        fragments
        + PAGE_INFO_FRAGMENT
        + f"""
query {operation}($id: ID!, $first: Int!, $after: String) {{
  rateLimit {{ cost }}
  node(id: $id) {{
    ... on {parent_type} {{
      {field}(first: $first, after: $after) {{
        nodes {{ {fields} }}
        pageInfo {{ ...ForwardPageInfo }}
      }}
    }}
  }}
}}
"""
    )


ISSUE_COMMENTS_QUERY = _connection_query(
    "IssueComments",
    "Issue",
    "comments",
    "...ConversationComment",
    ACTOR_FRAGMENT + COMMENT_FRAGMENT,
)
PR_COMMENTS_QUERY = _connection_query(
    "PullRequestComments",
    "PullRequest",
    "comments",
    "...ConversationComment",
    ACTOR_FRAGMENT + COMMENT_FRAGMENT,
)
REVIEW_FIELDS = "id submittedAt state url author { ...LifecycleActor }"
INLINE_COMMENT_FIELDS = (
    "id createdAt publishedAt state url author { ...LifecycleActor } "
    "pullRequestReview { id }"
)

PR_REVIEWS_QUERY = _connection_query(
    "PullRequestReviews",
    "PullRequest",
    "reviews",
    REVIEW_FIELDS,
    ACTOR_FRAGMENT,
)
PR_THREADS_QUERY = _connection_query(
    "PullRequestReviewThreads",
    "PullRequest",
    "reviewThreads",
    "id isResolved isOutdated",
)
THREAD_COMMENTS_QUERY = _connection_query(
    "ReviewThreadComments",
    "PullRequestReviewThread",
    "comments",
    INLINE_COMMENT_FIELDS,
    ACTOR_FRAGMENT,
)


# Bound nested fan-out independently of the outer parent page. Continuations
# use the existing lean queries, except threads which also embed comment pages.
HISTORY_PARENT_PAGE_SIZE = 20
HISTORY_ACTIVITY_PAGE_SIZE = 20
HISTORY_THREAD_PAGE_SIZE = 10
HISTORY_THREAD_FIELDS = f"""
id isResolved isOutdated
comments(first: {HISTORY_ACTIVITY_PAGE_SIZE}) {{
  nodes {{ {INLINE_COMMENT_FIELDS} }}
  pageInfo {{ ...ForwardPageInfo }}
}}
"""

HISTORY_PR_THREADS_QUERY = _connection_query(
    "PullRequestReviewThreads",
    "PullRequest",
    "reviewThreads",
    HISTORY_THREAD_FIELDS,
    ACTOR_FRAGMENT,
)

_HISTORY_ISSUE_FRAGMENT = f"""
fragment HistoryIssue on Issue {{
  ...LifecycleIssue
  timelineItems(first: {HISTORY_ACTIVITY_PAGE_SIZE}, itemTypes: [CLOSED_EVENT, REOPENED_EVENT]) {{
    nodes {{ __typename ...LifecycleClosure ...LifecycleReopening }}
    pageInfo {{ ...ForwardPageInfo }}
  }}
  comments(first: {HISTORY_ACTIVITY_PAGE_SIZE}) {{
    nodes {{ ...ConversationComment }}
    pageInfo {{ ...ForwardPageInfo }}
  }}
}}
"""

_HISTORY_PR_FRAGMENT = f"""
fragment HistoryPullRequest on PullRequest {{
  ...LifecyclePullRequest
  timelineItems(first: {HISTORY_ACTIVITY_PAGE_SIZE}, itemTypes: [MERGED_EVENT]) {{
    nodes {{ __typename ...LifecycleMerge }}
    pageInfo {{ ...ForwardPageInfo }}
  }}
  comments(first: {HISTORY_ACTIVITY_PAGE_SIZE}) {{
    nodes {{ ...ConversationComment }}
    pageInfo {{ ...ForwardPageInfo }}
  }}
  reviews(first: {HISTORY_ACTIVITY_PAGE_SIZE}) {{
    nodes {{ {REVIEW_FIELDS} }}
    pageInfo {{ ...ForwardPageInfo }}
  }}
  reviewThreads(first: {HISTORY_THREAD_PAGE_SIZE}) {{
    nodes {{ {HISTORY_THREAD_FIELDS} }}
    pageInfo {{ ...ForwardPageInfo }}
  }}
}}
"""

HISTORY_ISSUES_QUERY = (
    ACTOR_FRAGMENT
    + ISSUE_SUBJECT_FRAGMENT
    + ISSUE_EVENTS_FRAGMENT
    + COMMENT_FRAGMENT
    + PAGE_INFO_FRAGMENT
    + _HISTORY_ISSUE_FRAGMENT
    + """
query RepositoryIssues($id: ID!, $first: Int!, $after: String) {
  rateLimit { cost }
  node(id: $id) {
    ... on Repository {
      issues(first: $first, after: $after) {
        nodes { ...HistoryIssue }
        pageInfo { ...ForwardPageInfo }
      }
    }
  }
}
"""
)

HISTORY_PULL_REQUESTS_QUERY = (
    ACTOR_FRAGMENT
    + PR_SUBJECT_FRAGMENT
    + MERGE_EVENT_FRAGMENT
    + COMMENT_FRAGMENT
    + PAGE_INFO_FRAGMENT
    + _HISTORY_PR_FRAGMENT
    + """
query RepositoryPullRequests($id: ID!, $first: Int!, $after: String) {
  rateLimit { cost }
  node(id: $id) {
    ... on Repository {
      pullRequests(first: $first, after: $after) {
        nodes { ...HistoryPullRequest }
        pageInfo { ...ForwardPageInfo }
      }
    }
  }
}
"""
)


def parent_hydration_query(count: int) -> str:
    """Build aliases from batch positions; repository and item values stay variables."""
    if type(count) is not int or not 1 <= count <= 50:
        raise ValueError("hydration batch size must be between 1 and 50")
    declarations = ", ".join(f"$number{i}: Int!" for i in range(count))
    fields = "\n".join(
        f"""parent{i}: issueOrPullRequest(number: $number{i}) {{
          __typename
          ...LifecycleIssue
          ...LifecyclePullRequest
        }}"""
        for i in range(count)
    )
    return (
        ACTOR_FRAGMENT
        + ISSUE_SUBJECT_FRAGMENT
        + PR_SUBJECT_FRAGMENT
        + f"""
query HydrateRepositoryParents($id: ID!, {declarations}) {{
  rateLimit {{ cost }}
  node(id: $id) {{
    ... on Repository {{
      {fields}
    }}
  }}
}}
"""
    )

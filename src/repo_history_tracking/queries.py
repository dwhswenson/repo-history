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
PR_REVIEWS_QUERY = _connection_query(
    "PullRequestReviews",
    "PullRequest",
    "reviews",
    "id submittedAt state url author { ...LifecycleActor }",
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
    "id createdAt publishedAt state url author { ...LifecycleActor } "
    "pullRequestReview { id }",
    ACTOR_FRAGMENT,
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

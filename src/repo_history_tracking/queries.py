"""Named operations and reusable fragments. Values travel only as variables."""

PAGE_INFO_FRAGMENT = """
fragment ForwardPageInfo on PageInfo {
  hasNextPage
  endCursor
}
"""

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
  repository(owner: $owner, name: $name) {
    ...RepositoryIdentity
  }
}
"""
)

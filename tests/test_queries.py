"""Tests for the reusable GraphQL query documents and fragments."""

from repo_history_tracking.queries import PAGE_INFO_FRAGMENT, REPOSITORY_QUERY


def test_repository_query_and_reusable_fragments():
    assert REPOSITORY_QUERY.count("fragment RepositoryIdentity on Repository") == 1
    assert "query RepositoryLookup($owner: String!, $name: String!)" in REPOSITORY_QUERY
    assert "repository(owner: $owner, name: $name)" in REPOSITORY_QUERY
    assert "...RepositoryIdentity" in REPOSITORY_QUERY
    assert "fragment ForwardPageInfo on PageInfo" in PAGE_INFO_FRAGMENT
    assert "hasNextPage" in PAGE_INFO_FRAGMENT and "endCursor" in PAGE_INFO_FRAGMENT

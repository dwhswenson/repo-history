"""Tests for the reusable GraphQL query documents and fragments."""

from repo_history_tracking.queries import PAGE_INFO_FRAGMENT, REPOSITORY_QUERY


def test_repository_query_and_reusable_fragments():
    assert REPOSITORY_QUERY.count("fragment RepositoryIdentity on Repository") == 1
    assert "query RepositoryLookup($owner: String!, $name: String!)" in REPOSITORY_QUERY
    assert "repository(owner: $owner, name: $name)" in REPOSITORY_QUERY
    assert "...RepositoryIdentity" in REPOSITORY_QUERY
    assert "fragment ForwardPageInfo on PageInfo" in PAGE_INFO_FRAGMENT
    assert "hasNextPage" in PAGE_INFO_FRAGMENT and "endCursor" in PAGE_INFO_FRAGMENT


def test_lifecycle_queries_use_node_variables_and_independent_connections():
    from repo_history_tracking.queries import (
        ISSUE_TIMELINE_QUERY,
        ISSUES_QUERY,
        PR_TIMELINE_QUERY,
        PULL_REQUESTS_QUERY,
    )

    documents = [
        ISSUES_QUERY,
        PULL_REQUESTS_QUERY,
        ISSUE_TIMELINE_QUERY,
        PR_TIMELINE_QUERY,
    ]
    for document in documents:
        assert "$id: ID!" in document
        assert "$first: Int!" in document and "$after: String" in document
        assert "node(id: $id)" in document
        assert "first: $first, after: $after" in document
        assert document.count("fragment ForwardPageInfo on PageInfo") == 1
        assert document.count("fragment LifecycleActor on Actor") == 1
        assert "__typename" in document
        # All date selection is local; parents must span all states and months.
        assert "since:" not in document and "states:" not in document
        assert "body" not in document
    assert "issues(first:" in ISSUES_QUERY
    assert "pullRequests(first:" in PULL_REQUESTS_QUERY
    assert "itemTypes: [CLOSED_EVENT, REOPENED_EVENT]" in ISSUE_TIMELINE_QUERY
    assert "stateReason" in ISSUE_TIMELINE_QUERY
    assert "itemTypes: [MERGED_EVENT]" in PR_TIMELINE_QUERY
    assert "commit { oid }" in PR_TIMELINE_QUERY


def test_comment_and_review_queries_paginate_one_connection_without_bodies():
    from repo_history_tracking.queries import (
        ISSUE_COMMENTS_QUERY,
        PR_COMMENTS_QUERY,
        PR_REVIEWS_QUERY,
        PR_THREADS_QUERY,
        THREAD_COMMENTS_QUERY,
    )

    for document in (
        ISSUE_COMMENTS_QUERY,
        PR_COMMENTS_QUERY,
        PR_REVIEWS_QUERY,
        PR_THREADS_QUERY,
        THREAD_COMMENTS_QUERY,
    ):
        assert "$id: ID!" in document
        assert "node(id: $id)" in document
        assert "first: $first, after: $after" in document
        assert document.count("fragment ForwardPageInfo on PageInfo") == 1
        assert "body" not in document
        assert "states:" not in document and "since:" not in document
    assert "... on Issue" in ISSUE_COMMENTS_QUERY
    assert "... on PullRequest" in PR_COMMENTS_QUERY
    assert "submittedAt state" in PR_REVIEWS_QUERY
    assert "isResolved isOutdated" in PR_THREADS_QUERY
    assert "... on PullRequestReviewThread" in THREAD_COMMENTS_QUERY
    assert "publishedAt state" in THREAD_COMMENTS_QUERY
    assert "pullRequestReview { id }" in THREAD_COMMENTS_QUERY
    assert "comments(first:" not in PR_REVIEWS_QUERY
    assert "comments(first:" not in PR_THREADS_QUERY

"""Tests for the reusable GraphQL query documents and fragments."""

import re

import pytest

from repo_history.queries import PAGE_INFO_FRAGMENT, REPOSITORY_QUERY


def test_repository_query_and_reusable_fragments():
    assert REPOSITORY_QUERY.count("fragment RepositoryIdentity on Repository") == 1
    assert "query RepositoryLookup($owner: String!, $name: String!)" in REPOSITORY_QUERY
    assert "repository(owner: $owner, name: $name)" in REPOSITORY_QUERY
    assert "...RepositoryIdentity" in REPOSITORY_QUERY
    assert "fragment ForwardPageInfo on PageInfo" in PAGE_INFO_FRAGMENT
    assert "hasNextPage" in PAGE_INFO_FRAGMENT and "endCursor" in PAGE_INFO_FRAGMENT


def test_history_queries_always_request_totals():
    from repo_history.queries import (
        COMMIT_HISTORY_QUERY,
        HISTORY_ISSUES_QUERY,
        HISTORY_PULL_REQUESTS_QUERY,
    )

    for document in (
        HISTORY_ISSUES_QUERY,
        HISTORY_PULL_REQUESTS_QUERY,
        COMMIT_HISTORY_QUERY,
    ):
        assert document.count("totalCount") == 1
        assert "totalCount\n" in document
        assert "includeProgressTotal" not in document


def test_lifecycle_queries_use_node_variables_and_independent_connections():
    from repo_history.queries import (
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
    from repo_history.queries import (
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


@pytest.mark.parametrize("count", [1, 20])
def test_activity_hydration_reuses_complete_history_fragments(count):
    from repo_history.queries import (
        _HISTORY_ISSUE_FRAGMENT,
        _HISTORY_PR_FRAGMENT,
        parent_hydration_query,
    )

    query = parent_hydration_query(count, include_activity=True)
    definitions = re.findall(r"fragment (\w+) on", query)
    spreads = set(re.findall(r"\.\.\.(\w+)", query))
    assert len(definitions) == len(set(definitions))
    assert spreads == set(definitions)
    assert _HISTORY_ISSUE_FRAGMENT in query
    assert _HISTORY_PR_FRAGMENT in query
    assert query.count("issueOrPullRequest(number: $number") == count
    assert f"$number{count - 1}: Int!" in query
    assert "reviews(first: 20)" in query
    assert "reviewThreads(first: 10)" in query
    assert "comments(first: 20)" in query
    assert "body" not in query and "since:" not in query

    lean = parent_hydration_query(count)
    assert lean == parent_hydration_query(count, include_activity=False)
    assert "HistoryIssue" not in lean and "HistoryPullRequest" not in lean
    assert "timelineItems" not in lean and "comments(first:" not in lean

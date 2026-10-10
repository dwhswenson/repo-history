"""Tests for repo_history.repository."""

import pytest

import repo_history as api
from repo_history.queries import REPOSITORY_QUERY

from .helpers import Response, repository


def test_invalid_repository_inputs(client):
    with pytest.raises(ValueError):
        api.lookup_repository(client, " ", "name")
    assert not client._session.calls


def test_repository_lookup(client):
    client._session.responses = [repository()]
    result = api.lookup_repository(client, "old-owner", "old-name")
    assert result == api.RepositorySnapshot(
        "R_example",
        "new-owner/new-name",
        "https://github.com/new-owner/new-name",
        False,
        "main",
        "a" * 40,
        123,
    )
    payload = client._session.calls[0][1]["json"]
    assert payload["query"] == REPOSITORY_QUERY
    assert payload["variables"] == {"owner": "old-owner", "name": "old-name"}
    assert payload["operationName"] == "RepositoryLookup"


def test_empty_repository(client):
    client._session.responses = [repository(isEmpty=True, defaultBranchRef=None)]
    result = api.lookup_repository(client, "owner", "name")
    assert result.default_branch is None and result.default_branch_head is None


@pytest.mark.parametrize(
    "response,error",
    [
        (Response({"data": {"repository": None}}), api.RepositoryUnavailableError),
        (Response({"data": {}}), api.GraphQLError),
        (repository(defaultBranchRef=None), api.GraphQLError),
        (
            repository(defaultBranchRef={"name": "main", "target": None}),
            api.GraphQLError,
        ),
        (
            repository(
                defaultBranchRef={
                    "name": "main",
                    "target": {"__typename": "Tag", "oid": "a" * 40},
                }
            ),
            api.GraphQLError,
        ),
        (
            repository(
                defaultBranchRef={
                    "name": "main",
                    "target": {"__typename": "Commit", "oid": "bad"},
                }
            ),
            api.GraphQLError,
        ),
        (repository(hasIssuesEnabled=None), api.GraphQLError),
        (repository(databaseId=True), api.GraphQLError),
        (repository(databaseId=-1), api.GraphQLError),
    ],
)
def test_bad_repository(client, response, error):
    client._session.responses = [response]
    with pytest.raises(error):
        api.lookup_repository(client, "owner", "name")

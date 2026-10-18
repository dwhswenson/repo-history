"""Per-run primary API usage, including retries and incomplete measurements."""

import logging

import pytest
import requests

import repo_history as api
from repo_history import history as collection
from repo_history.queries import parent_hydration_query

from .github import (
    NOW,
    collect_update_report,
    connection,
    hydrated,
    parent,
    rest_parent,
)
from .helpers import Response, Session, empty_commits, errors, repository


@pytest.fixture(autouse=True)
def isolate_commits(monkeypatch):
    monkeypatch.setattr("repo_history.history.collect_commits", empty_commits)


def cost(response, points):
    response.body.setdefault("data", {})["rateLimit"] = {"cost": points}
    # Cumulative account usage must not be attributed to this collection.
    response.headers.update({"X-RateLimit-Used": "4000"})
    return response


@pytest.fixture
def session(monkeypatch, caplog):
    result = Session()
    monkeypatch.setattr("requests.Session", lambda: result)
    monkeypatch.setattr(collection, "_utc_now", lambda: NOW)
    monkeypatch.setattr("repo_history.transport.time.sleep", lambda _: None)
    caplog.set_level(logging.INFO, logger="repo_history.history")
    return result


def summary(caplog):
    records = [r for r in caplog.records if r.name == "repo_history.history"]
    assert len(records) == 1
    message = records[0].getMessage()
    return message[message.index("REST requests=") :]


def test_full_run_logs_sum_of_query_costs_and_zero_rest(session, caplog):
    session.responses = [cost(repository(), 2), cost(connection("pullRequests"), 7)]
    report = api.collect_history("o", "r", token="test")
    assert (
        summary(caplog)
        == "REST requests=0 retries=0 points=0 unmeasured_attempts=0 GraphQL requests=2 retries=0 points=9 unmeasured_attempts=0"
    )
    assert report.activities == ()
    assert session.closed


def test_bounded_run_counts_rest_pages_and_graphql_hydration(session, caplog):
    next_url = "https://api.github.com/repos/new-owner/new-name/issues?page=2"
    session.responses = [
        cost(repository(), 2),
        Response([], headers={"Link": f'<{next_url}>; rel="next"'}),
        Response([rest_parent(pr=True)]),
        cost(hydrated(parent("P_real", __typename="PullRequest")), 7),
    ]
    collect_update_report()
    assert (
        summary(caplog)
        == "REST requests=2 retries=0 points=2 unmeasured_attempts=0 GraphQL requests=2 retries=0 points=9 unmeasured_attempts=0"
    )
    assert session.closed


def test_failed_run_logs_retry_and_error_costs(session, caplog):
    session.responses = [
        cost(repository(), 2),
        cost(errors("MAX_NODE_LIMIT_EXCEEDED"), 3),
        cost(
            connection(
                "pullRequests",
                [
                    parent(
                        timelineItems={
                            "nodes": [],
                            "pageInfo": {"hasNextPage": True, "endCursor": "next"},
                        }
                    )
                ],
            ),
            4,
        ),
        cost(errors("FORBIDDEN"), 5),
    ]
    with pytest.raises(api.GraphQLError):
        api.collect_history("o", "r", token="test")
    assert (
        summary(caplog)
        == "REST requests=0 retries=0 points=0 unmeasured_attempts=0 GraphQL requests=4 retries=1 points=14 unmeasured_attempts=0"
    )
    assert session.closed


def test_network_and_unreported_costs_are_explicitly_unmeasured(session, caplog):
    session.responses = [
        cost(repository(), 2),
        requests.Timeout(),
        Response(status=503),
        cost(connection("pullRequests"), 4),
    ]
    api.collect_history("o", "r", token="test")
    assert (
        summary(caplog)
        == "REST requests=0 retries=0 points=0 unmeasured_attempts=0 GraphQL requests=4 retries=2 points=6 unmeasured_attempts=2"
    )


def test_rest_failures_are_unmeasured_and_logged(session, caplog):
    session.responses = [
        cost(repository(), 2),
        Response(status=503),
        requests.Timeout(),
        Response(status=403),
    ]
    with pytest.raises(api.AuthenticationError):
        collect_update_report()
    assert (
        summary(caplog)
        == "REST requests=3 retries=2 points=0 unmeasured_attempts=3 GraphQL requests=1 retries=0 points=2 unmeasured_attempts=0"
    )
    assert session.closed


@pytest.mark.parametrize("value", [None, True, -1, 1.5, "5", {}, float("nan")])
def test_invalid_cost_is_not_reported_as_zero(client, value):
    client._session.responses = [cost(Response(), value)]
    client.execute("query Test { viewer { login } }", {}, operation_name="Test")
    usage = client._api_usage["graphql"]
    assert usage.points == 0
    assert usage.unmeasured_attempts == 1


def test_usage_starts_fresh_for_each_collection(session, caplog):
    for points in (3, 5):
        caplog.clear()
        session.responses = [
            cost(repository(), 1),
            cost(connection("pullRequests"), points),
        ]
        api.collect_history("o", "r", token="test")
        assert f"points={points + 1} unmeasured_attempts=0" in summary(caplog)


def test_collector_queries_all_request_primary_cost():
    from repo_history import queries

    documents = [
        getattr(queries, name) for name in dir(queries) if name.endswith("_QUERY")
    ]
    documents.append(parent_hydration_query(20))
    assert all(document.count("rateLimit { cost }") == 1 for document in documents)

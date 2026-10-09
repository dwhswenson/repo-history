"""Per-run primary API usage, including retries and incomplete measurements."""

import logging

import pytest
import requests

import repo_history_tracking as api
from repo_history_tracking import collection
from repo_history_tracking.queries import parent_hydration_query

from .helpers import Response, Session, empty_commits, errors, repository
from .test_collection import NOW, connection, parent
from .test_discovery import hydrated, rest_parent


@pytest.fixture(autouse=True)
def isolate_commits(monkeypatch):
    monkeypatch.setattr("repo_history_tracking.commits.collect_commits", empty_commits)


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
    monkeypatch.setattr("repo_history_tracking.transport.time.sleep", lambda _: None)
    caplog.set_level(logging.INFO, logger="repo_history_tracking.collection")
    return result


def summary(caplog):
    records = [
        r for r in caplog.records if r.name == "repo_history_tracking.collection"
    ]
    assert len(records) == 1
    return records[0].getMessage()


def test_full_run_logs_sum_of_query_costs_and_zero_rest(session, caplog):
    session.responses = [cost(repository(), 2), cost(connection("pullRequests"), 7)]
    report = api.collect_month("o", "r", 2024, 2, token="test")
    assert summary(caplog) == (
        "GitHub API usage status=completed REST points=0 unmeasured_attempts=0 "
        "GraphQL points=9 unmeasured_attempts=0"
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
        cost(connection("timelineItems"), 3),
        cost(connection("comments"), 4),
        cost(connection("reviews"), 5),
        cost(connection("reviewThreads"), 6),
    ]
    api.collect_month("o", "r", 2024, 2, token="test", discovery="bounded")
    assert summary(caplog) == (
        "GitHub API usage status=completed REST points=2 unmeasured_attempts=0 "
        "GraphQL points=27 unmeasured_attempts=0"
    )
    assert session.closed


def test_failed_run_logs_retry_and_error_costs(session, caplog):
    session.responses = [
        cost(repository(), 2),
        cost(errors("MAX_NODE_LIMIT_EXCEEDED"), 3),
        cost(connection("pullRequests", [parent()]), 4),
        cost(errors("FORBIDDEN"), 5),
    ]
    with pytest.raises(api.GraphQLError):
        api.collect_month("o", "r", 2024, 2, token="test")
    assert summary(caplog) == (
        "GitHub API usage status=failed REST points=0 unmeasured_attempts=0 "
        "GraphQL points=14 unmeasured_attempts=0"
    )
    assert session.closed


def test_network_and_unreported_costs_are_explicitly_unmeasured(session, caplog):
    session.responses = [
        cost(repository(), 2),
        requests.Timeout(),
        Response(status=503),
        cost(connection("pullRequests"), 4),
    ]
    api.collect_month("o", "r", 2024, 2, token="test")
    assert summary(caplog) == (
        "GitHub API usage status=completed REST points=0 unmeasured_attempts=0 "
        "GraphQL points=6 unmeasured_attempts=2"
    )


def test_rest_failures_are_unmeasured_and_logged(session, caplog):
    session.responses = [
        cost(repository(), 2),
        Response(status=503),
        requests.Timeout(),
        Response(status=403),
    ]
    with pytest.raises(api.AuthenticationError):
        api.collect_month("o", "r", 2024, 2, token="test", discovery="bounded")
    assert summary(caplog) == (
        "GitHub API usage status=failed REST points=0 unmeasured_attempts=3 "
        "GraphQL points=2 unmeasured_attempts=0"
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
        api.collect_month("o", "r", 2024, 2, token="test")
        assert f"GraphQL points={points + 1} unmeasured_attempts=0" in summary(caplog)


def test_collector_queries_all_request_primary_cost():
    from repo_history_tracking import queries

    documents = [
        getattr(queries, name) for name in dir(queries) if name.endswith("_QUERY")
    ]
    documents.append(parent_hydration_query(50))
    assert len(documents) == 15
    assert all(document.count("rateLimit { cost }") == 1 for document in documents)

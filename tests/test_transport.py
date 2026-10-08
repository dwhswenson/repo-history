"""Tests for repo_history_tracking.transport."""

from datetime import datetime, timezone
from email.utils import format_datetime

import pytest
import requests

import repo_history_tracking as api

from .helpers import Response, errors, execute


def test_session_payload_and_cleanup(client):
    session = client._session
    responses = [Response(), Response()]
    session.responses.extend(responses)
    assert execute(client) == {"ok": True}
    execute(client)
    assert session.headers["Authorization"] == "Bearer secret-token"
    assert session.adapters["https://"].max_retries.total == 0
    endpoint, call = session.calls[0]
    assert endpoint == "https://api.github.com/graphql"
    assert call["timeout"] == (10, 60)
    assert call["allow_redirects"] is False
    assert call["json"]["operationName"] == "Test"
    assert call["json"]["variables"] == {"name": "private-value"}
    assert "private-value" not in call["json"]["query"]
    assert all(r.closed for r in responses)
    with pytest.raises(RuntimeError):
        with client:
            raise RuntimeError("caller failure")
    assert session.closed
    client.close()
    with pytest.raises(api.TransportError, match="closed"):
        execute(client)
    with pytest.raises(api.TransportError, match="closed"):
        client.__enter__()


def test_netrc_cannot_override_explicit_bearer_auth(monkeypatch):
    monkeypatch.setattr(
        requests.sessions, "get_netrc_auth", lambda _: ("wrong", "netrc")
    )
    with api.GraphQLClient("explicit-token") as client:
        prepared = client._session.prepare_request(
            requests.Request("POST", "https://api.github.com/graphql")
        )
        assert prepared.headers["Authorization"] == "Bearer explicit-token"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": True},
        {"max_attempts": 0},
        {"timeout": (0, 1)},
        {"timeout": (1, float("nan"))},
        {"timeout": 10},
    ],
)
def test_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        api.GraphQLClient("token", **kwargs)


@pytest.mark.parametrize(
    "response,error",
    [
        (Response(ValueError("raw secret-token")), api.GraphQLError),
        (Response([]), api.GraphQLError),
        (Response({"data": None}), api.GraphQLError),
        (Response({"data": []}), api.GraphQLError),
        (Response({"data": {}, "errors": None}), api.GraphQLError),
        (Response({"data": {}, "errors": {}}), api.GraphQLError),
        (Response({"data": {}, "errors": [None]}), api.GraphQLError),
        (errors("NOT_FOUND"), api.GraphQLError),
        (errors("INTERNAL", "FORBIDDEN"), api.GraphQLError),
        (Response(status=401), api.AuthenticationError),
        (Response(status=403), api.AuthenticationError),
        (Response(status=302), api.TransportError),
        (Response(status=404), api.TransportError),
        (requests.exceptions.SSLError("raw secret-token"), api.TransportError),
        (requests.exceptions.InvalidURL("raw secret-token"), api.TransportError),
    ],
)
def test_nonretryable_errors(client, response, error, caplog):
    caplog.set_level("DEBUG", logger="repo_history_tracking.transport")
    client._session.responses = [response]
    with pytest.raises(error) as exc:
        execute(client)
    assert exc.value.operation == "Test"
    assert exc.value.attempts == 1
    assert len(client._session.calls) == 1
    assert "secret-token" not in str(exc.value) + caplog.text
    assert "private-value" not in str(exc.value) + caplog.text


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout(),
        requests.ConnectionError(),
        Response(status=500),
        Response(status=503),
        errors("INTERNAL"),
        errors("INTERNAL_SERVER_ERROR"),
    ],
)
def test_retries(client, failure):
    waits = []
    client._sleep = waits.append
    client._session.responses = [failure, failure, failure, Response()]
    assert execute(client) == {"ok": True}
    assert waits == [1, 2, 4]
    assert len(client._session.calls) == 4


def test_retry_exhaustion(client):
    waits = []
    client._sleep = waits.append
    client._session.responses = [Response(status=500)] * 4
    with pytest.raises(api.TransportError) as exc:
        execute(client)
    assert exc.value.attempts == 4
    assert exc.value.status == 500
    assert waits == [1, 2, 4]


@pytest.mark.parametrize(
    "response",
    [
        Response(status=429),
        Response(
            status=403, headers={"rEtRy-AfTeR": "60", "X-GitHub-Request-ID": "req"}
        ),
        Response(status=403, headers={"X-RateLimit-Remaining": "0"}),
        errors("RATE_LIMITED"),
        Response(
            {"errors": [{"message": "You have exceeded a secondary rate limit."}]}
        ),
        Response({"message": "API rate limit exceeded"}, status=403),
    ],
)
def test_limits_fail_without_retry(client, response):
    client._session.responses = [response]
    client._sleep = lambda _: pytest.fail("rate limits must not sleep")
    with pytest.raises(api.RateLimitError):
        execute(client)
    assert len(client._session.calls) == 1


@pytest.mark.parametrize(
    "retry,expected",
    [
        ("60", 60),
        ("garbage", None),
        ("nan", None),
        ("-1", None),
        (format_datetime(datetime.fromtimestamp(1060, timezone.utc)), 60),
    ],
)
def test_retry_details(client, retry, expected):
    client._session.responses = [
        Response(
            status=429,
            headers={
                "Retry-After": retry,
                "X-RateLimit-Reset": "1100",
                "X-GitHub-Request-ID": "req",
            },
        )
    ]
    with pytest.raises(api.RateLimitError) as exc:
        execute(client)
    assert exc.value.retry_after == expected
    assert exc.value.reset_at == datetime.fromtimestamp(1100, timezone.utc)
    assert exc.value.request_id == "req"


@pytest.mark.parametrize("reset", [None, "garbage", "nan", "1e300", "1100"])
def test_successful_exhaustion(client, reset):
    headers = {"X-RateLimit-Remaining": "0"}
    if reset is not None:
        headers["X-RateLimit-Reset"] = reset
    client._session.responses = [Response(headers=headers), Response()]
    assert execute(client) == {"ok": True}
    with pytest.raises(api.RateLimitError):
        execute(client)
    assert len(client._session.calls) == 1
    client._clock = lambda: 1100
    if reset == "1100":
        assert execute(client) == {"ok": True}
    else:
        with pytest.raises(api.RateLimitError):
            execute(client)


def test_graphql_budget_metadata(client):
    client._session.responses = [
        Response(
            {"data": {"rateLimit": {"remaining": 0, "resetAt": "1970-01-01T00:18:20Z"}}}
        )
    ]
    execute(client)
    with pytest.raises(api.RateLimitError) as exc:
        execute(client)
    assert exc.value.reset_at.timestamp() == 1100


def test_malformed_error_extension_is_not_retried(client):
    client._session.responses = [
        Response({"errors": [{"message": "error", "extensions": []}]})
    ]
    with pytest.raises(api.GraphQLError, match="malformed"):
        execute(client)


def test_error_extension_retry_and_limit(client):
    client._session.responses = [
        Response(
            {
                "errors": [
                    {
                        "message": "error",
                        "extensions": {"code": "INTERNAL_SERVER_ERROR"},
                    }
                ]
            }
        ),
        Response(
            {"errors": [{"message": "error", "extensions": {"type": "RATE_LIMITED"}}]}
        ),
    ]
    with pytest.raises(api.RateLimitError) as exc:
        execute(client)
    assert exc.value.attempts == 2


def test_unknown_primary_limit_error(client):
    client._session.responses = [
        errors("UNKNOWN", headers={"X-RateLimit-Remaining": "0"})
    ]
    with pytest.raises(api.RateLimitError):
        execute(client)


def test_secondary_retry_after_cannot_be_bypassed_by_primary_reset(client):
    client._session.responses = [
        Response(
            status=403,
            headers={
                "Retry-After": "300",
                "X-RateLimit-Reset": "1100",
            },
        ),
        Response(),
    ]
    with pytest.raises(api.RateLimitError):
        execute(client)
    client._clock = lambda: 1100
    with pytest.raises(api.RateLimitError) as exc:
        execute(client)
    assert exc.value.retry_after == 200
    assert len(client._session.calls) == 1
    client._clock = lambda: 1300
    assert execute(client) == {"ok": True}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"document": ""},
        {"variables": []},
        {"operation_name": " "},
    ],
)
def test_invalid_execution_inputs(client, kwargs):
    arguments = dict(
        document="query Test { viewer { login } }", variables={}, operation_name="Test"
    )
    arguments.update(kwargs)
    with pytest.raises(ValueError):
        client.execute(**arguments)
    assert not client._session.calls


def test_timeout_and_attempt_overrides(client):
    client.timeout = (2, 3)
    client.max_attempts = 1
    client._session.responses = [Response(status=500)]
    with pytest.raises(api.TransportError) as exc:
        execute(client)
    assert exc.value.attempts == 1
    assert client._session.calls[0][1]["timeout"] == (2, 3)

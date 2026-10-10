"""Offline REST request policy and safe Link pagination."""

from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

import repo_history as api
from repo_history.rest_pagination import iter_rest_list

from .helpers import Response, execute

URL = "https://api.github.com/repos/owner/repo/issues?state=all&per_page=100"
NEXT = URL + "&page=2"


def fetch(client, url=URL):
    return list(iter_rest_list(client, url, operation_name="TestREST"))


def test_pages_include_empty_page_and_close_responses(client):
    responses = [
        Response([], headers={"Link": f'<{NEXT}>; rel="next", <{NEXT}>; rel="last"'}),
        Response([{"number": 1}], headers={"Link": f'<{URL}>; rel="prev"'}),
    ]
    client._session.responses = responses.copy()
    assert fetch(client) == [{"number": 1}]
    assert all(r.closed for r in responses)
    calls = client._session.calls
    assert len(calls) == 2
    assert parse_qs(urlsplit(calls[1][0]).query)["page"] == ["2"]
    assert calls[0][1] == {
        "headers": {"X-GitHub-Api-Version": "2026-03-10"},
        "timeout": (10, 60),
        "allow_redirects": False,
    }
    assert client._session.headers["Accept"] == "application/vnd.github+json"
    assert client._session.headers["Authorization"] == "Bearer secret-token"


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout("secret-token"),
        requests.ConnectionError("secret-token"),
        *[Response(status=s) for s in (500, 502, 503, 504)],
    ],
)
def test_rest_retries(client, failure):
    waits = []
    client._sleep = waits.append
    client._session.responses = [failure] * 3 + [Response([])]
    assert fetch(client) == []
    assert waits == [1, 2, 4]
    assert len(client._session.calls) == 4


def test_rest_retry_exhaustion(client):
    response = Response(status=503, headers={"X-GitHub-Request-ID": "req"})
    client._session.responses = [response] * 4
    with pytest.raises(api.TransportError) as exc:
        fetch(client)
    assert exc.value.attempts == 4
    assert exc.value.status == 503
    assert exc.value.request_id == "req"
    assert response.closed


@pytest.mark.parametrize(
    "response,error",
    [
        (Response(ValueError("secret-token")), api.TransportError),
        (Response({"message": "secret-token"}), api.TransportError),
        (Response([None]), api.TransportError),
        (Response([1]), api.TransportError),
        *[
            (Response(status=s), api.TransportError)
            for s in (204, 301, 302, 304, 404, 410, 422)
        ],
        *[(Response(status=s), api.AuthenticationError) for s in (401, 403)],
        (requests.exceptions.SSLError("secret-token"), api.TransportError),
        (requests.exceptions.InvalidURL("secret-token"), api.TransportError),
    ],
)
def test_rest_permanent_failures(client, response, error, caplog):
    caplog.set_level("DEBUG", logger="repo_history.transport")
    client._session.responses = [response]
    with pytest.raises(error) as exc:
        fetch(client)
    assert exc.value.operation == "TestREST"
    assert exc.value.attempts == 1
    assert len(client._session.calls) == 1
    assert "secret-token" not in str(exc.value) + caplog.text
    if isinstance(response, Response):
        assert response.closed


@pytest.mark.parametrize(
    "response",
    [
        Response(status=429),
        Response(status=403, headers={"X-RateLimit-Remaining": "0"}),
        Response({"message": "API rate limit exceeded"}, status=403),
        Response({"message": "secondary rate limit"}, status=403),
        Response(status=403, headers={"Retry-After": "60"}),
    ],
)
def test_rest_limits_fail_immediately(client, response):
    client._session.responses = [response]
    client._sleep = lambda _: pytest.fail("rate limits must not sleep")
    with pytest.raises(api.RateLimitError):
        fetch(client)
    with pytest.raises(api.RateLimitError):
        fetch(client)
    assert len(client._session.calls) == 1
    assert response.closed


@pytest.mark.parametrize(
    "retry,expected",
    [
        ("60", 60),
        ("bad", None),
        ("nan", None),
        ("-1", None),
        (format_datetime(datetime.fromtimestamp(1060, timezone.utc)), 60),
    ],
)
def test_rest_limit_diagnostics(client, retry, expected):
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
        fetch(client)
    assert exc.value.retry_after == expected
    assert exc.value.reset_at.timestamp() == 1100
    assert exc.value.request_id == "req"


@pytest.mark.parametrize("first", ["rest", "graphql"])
@pytest.mark.parametrize("limited", [False, True])
def test_primary_budgets_are_independent(client, first, limited):
    def rest():
        return fetch(client)

    def graphql():
        return execute(client)

    first_call, second_call = (rest, graphql) if first == "rest" else (graphql, rest)
    first_body = [] if first == "rest" else {"data": {"ok": True}}
    second_body = {"data": {"ok": True}} if first == "rest" else []
    client._session.responses = [
        Response(
            first_body,
            status=403 if limited else 200,
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1100"},
        ),
        Response(second_body),
        Response(first_body),
    ]
    if limited:
        with pytest.raises(api.RateLimitError):
            first_call()
    else:
        first_call()
    second_call()
    with pytest.raises(api.RateLimitError):
        first_call()
    assert len(client._session.calls) == 2
    client._clock = lambda: 1100
    first_call()


@pytest.mark.parametrize("first", ["rest", "graphql"])
def test_secondary_throttling_is_shared_and_honors_both_dates(client, first):
    def rest():
        return fetch(client)

    def graphql():
        return execute(client)

    first_call, second_call = (rest, graphql) if first == "rest" else (graphql, rest)
    client._session.responses = [
        Response(
            {"message": "secondary rate limit"},
            status=403,
            headers={
                "Retry-After": "300",
                "X-RateLimit-Reset": "1100",
                "X-RateLimit-Remaining": "10",
            },
        ),
        Response({"data": {"ok": True}} if first == "rest" else []),
    ]
    with pytest.raises(api.RateLimitError):
        first_call()
    client._clock = lambda: 1100
    with pytest.raises(api.RateLimitError) as exc:
        second_call()
    assert exc.value.retry_after == 200
    assert len(client._session.calls) == 1
    client._clock = lambda: 1300
    second_call()


@pytest.mark.parametrize("reset", [None, "garbage", "1100"])
def test_rest_successful_exhaustion(client, reset):
    headers = {"X-RateLimit-Remaining": "0"}
    if reset is not None:
        headers["X-RateLimit-Reset"] = reset
    client._session.responses = [Response([], headers=headers), Response([])]
    assert fetch(client) == []
    with pytest.raises(api.RateLimitError):
        fetch(client)
    client._clock = lambda: 1100
    if reset == "1100":
        assert fetch(client) == []
    else:
        with pytest.raises(api.RateLimitError):
            fetch(client)


@pytest.mark.parametrize(
    "target",
    [
        "http://api.github.com/repos/owner/repo/issues?page=2",
        "https://evil.example/repos/owner/repo/issues?page=2",
        "https://api.github.com.evil.example/repos/owner/repo/issues?page=2",
        "https://api.github.com:444/repos/owner/repo/issues?page=2",
        "https://user:pass@api.github.com/repos/owner/repo/issues?page=2",
        "https://api.github.com/repos/owner/other/issues?page=2",
        "https://api.github.com/repos/owner/repo/issues?page=2#fragment",
        "https://api.github.com/repos/owner/repo/issues?page=2#",
        "https://api.github.com:bad/repos/owner/repo/issues?page=2",
        "/repos/owner/repo/issues?page=2",
        "https://api.github.com/repos/owner/repo/issues?page= 2",
        "https://api.github.com/repos/owner/repo/issues/../issues?page=2",
    ],
)
def test_unsafe_next_links_are_rejected_before_yield(client, target, caplog):
    response = Response([{"number": 1}], headers={"Link": f'<{target}>; rel="next"'})
    client._session.responses = [response]
    with pytest.raises(api.PaginationError) as exc:
        next(iter_rest_list(client, URL, operation_name="TestREST"))
    assert target not in str(exc.value) + caplog.text
    assert len(client._session.calls) == 1
    assert response.closed


@pytest.mark.parametrize(
    "header",
    [
        "",
        "nonsense",
        f"<{NEXT}>",
        f'<{NEXT}>; rel="next", invalid',
        f'<{NEXT}>; rel="next", <{NEXT}>; rel="next"',
        f'<{NEXT}>; rel="next"; rel="last"',
    ],
)
def test_malformed_links_fail(client, header):
    client._session.responses = [Response([], headers={"Link": header})]
    with pytest.raises(api.PaginationError):
        fetch(client)


def test_normalized_loop_is_detected(client):
    same = "https://API.GITHUB.COM:443/repos/owner/repo/issues?per_page=100&state=all"
    client._session.responses = [
        Response([], headers={"Link": f'<{same}>; rel="next"'})
    ]
    with pytest.raises(api.PaginationError, match="repeated"):
        fetch(client)
    assert len(client._session.calls) == 1


def test_late_loop_is_detected(client):
    client._session.responses = [
        Response([], headers={"Link": f'<{NEXT}>; rel="next"'}),
        Response([], headers={"Link": f'<{URL}>; rel="next"'}),
    ]
    with pytest.raises(api.PaginationError, match="repeated"):
        fetch(client)
    assert len(client._session.calls) == 2


def test_closed_client_cannot_fetch(client):
    client.close()
    with pytest.raises(api.TransportError, match="closed"):
        fetch(client)
    assert not client._session.calls


def test_secondary_retry_without_primary_reset_unblocks_both_apis(client):
    client._session.responses = [
        Response(
            {"message": "secondary rate limit"},
            status=403,
            headers={"Retry-After": "60"},
        ),
        Response(),
        Response([]),
    ]
    with pytest.raises(api.RateLimitError):
        fetch(client)
    with pytest.raises(api.RateLimitError):
        execute(client)
    client._clock = lambda: 1060
    execute(client)
    assert fetch(client) == []


def test_get_bearer_auth_is_not_overridden_by_netrc(monkeypatch):
    monkeypatch.setattr(
        requests.sessions, "get_netrc_auth", lambda _: ("wrong", "netrc")
    )
    with api.GraphQLClient("explicit-token") as client:
        prepared = client._session.prepare_request(requests.Request("GET", URL))
        assert prepared.headers["Authorization"] == "Bearer explicit-token"


def test_numeric_repository_pagination_alias(client):
    target = "https://api.github.com/repositories/123/issues?page=2"
    client._session.responses = [
        Response([{"number": 1}], headers={"Link": f'<{target}>; rel="next"'}),
        Response([{"number": 2}]),
    ]
    assert list(
        iter_rest_list(
            client, URL, operation_name="TestREST", repository_database_id=123
        )
    ) == [{"number": 1}, {"number": 2}]
    assert client._session.calls[1][0] == target


@pytest.mark.parametrize(
    "target",
    [
        "https://api.github.com/repositories/124/issues?page=2",
        "https://api.github.com/repositories/123/pulls?page=2",
        "https://evil.example/repositories/123/issues?page=2",
    ],
)
def test_numeric_repository_pagination_rejects_other_targets(client, target):
    client._session.responses = [
        Response([{"number": 1}], headers={"Link": f'<{target}>; rel="next"'})
    ]
    with pytest.raises(api.PaginationError):
        next(
            iter_rest_list(
                client, URL, operation_name="TestREST", repository_database_id=123
            )
        )
    assert len(client._session.calls) == 1

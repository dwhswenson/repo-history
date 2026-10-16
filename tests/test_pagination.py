"""Tests for repo_history.pagination."""

import pytest
import requests

import repo_history as api

from .helpers import Response, errors, iterate, page


def test_multiple_pages_and_nullable_nodes(client):
    variables = {"owner": "example"}
    client._session.responses = [
        page([{"id": "a"}, None], True, "c1"),
        page([], True, "c2"),
        page([{"id": "b"}]),
    ]
    iterator = iterate(client, variables)
    assert not client._session.calls
    assert list(iterator) == [{"id": "a"}, None, {"id": "b"}]
    assert [c[1]["json"]["variables"]["after"] for c in client._session.calls] == [
        None,
        "c1",
        "c2",
    ]
    assert variables == {"owner": "example"}


@pytest.mark.parametrize("size", [True, False, 0, 101, 2.5, "10"])
def test_invalid_page_size(client, size):
    with pytest.raises(ValueError):
        list(iterate(client, page_size=size))
    assert not client._session.calls


@pytest.mark.parametrize(
    "connection",
    [
        None,
        {},
        {"nodes": [1], "pageInfo": {"hasNextPage": False, "endCursor": None}},
        {"nodes": [], "pageInfo": {"hasNextPage": "false", "endCursor": None}},
        {
            "nodes": [{"id": "must not yield"}],
            "pageInfo": {"hasNextPage": True, "endCursor": None},
        },
        {"nodes": [], "pageInfo": {"hasNextPage": False}},
        {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": ""}},
    ],
)
def test_invalid_page_before_yield(client, connection):
    client._session.responses = [
        Response({"data": {"repository": {"items": connection}}})
    ]
    with pytest.raises(api.PaginationError):
        next(iterate(client))


def test_cursor_cycle_discards_bad_page(client):
    client._session.responses = [
        page([{"id": "a"}], True, "c1"),
        page([], True, "c2"),
        page([{"id": "bad"}], True, "c1"),
    ]
    iterator = iterate(client)
    assert next(iterator) == {"id": "a"}
    with pytest.raises(api.PaginationError, match="repeated"):
        next(iterator)


def test_independent_iterators(client):
    client._session.responses = [
        page([{"id": "parent"}], True, "p1"),
        page([{"id": "child"}], True, "c1"),
        page([{"id": "child2"}]),
        page([{"id": "parent2"}]),
    ]
    parent = iterate(client, {"owner": "parent"})
    assert next(parent) == {"id": "parent"}
    assert list(iterate(client, {"owner": "child"})) == [
        {"id": "child"},
        {"id": "child2"},
    ]
    assert list(parent) == [{"id": "parent2"}]
    assert [c[1]["json"]["variables"]["after"] for c in client._session.calls] == [
        None,
        None,
        "c1",
        "p1",
    ]


@pytest.mark.parametrize(
    "failure",
    [
        Response(status=502),
        Response(status=504),
        errors("RESOURCE_LIMITS_EXCEEDED"),
        errors("MAX_NODE_LIMIT_EXCEEDED"),
        Response(
            {"errors": [{"message": "We couldn't respond to your request in time"}]}
        ),
    ],
)
def test_reduce_and_retain_page_size(client, failure):
    client._session.responses = [
        failure,
        page([{"id": "a"}], True, "c1"),
        page([{"id": "b"}]),
    ]
    assert list(iterate(client)) == [{"id": "a"}, {"id": "b"}]
    values = [c[1]["json"]["variables"] for c in client._session.calls]
    assert [v["first"] for v in values] == [100, 50, 50]
    assert [v["after"] for v in values] == [None, None, "c1"]


def test_shared_retry_budget(client):
    client._session.responses = [
        requests.Timeout(),
        Response(status=504),
        Response(status=500),
        errors("RESOURCE_LIMITS_EXCEEDED"),
    ]
    with pytest.raises(api.QueryResourceError) as exc:
        list(iterate(client))
    assert exc.value.attempts == 4
    assert [c[1]["json"]["variables"]["first"] for c in client._session.calls] == [
        100,
        100,
        50,
        50,
    ]


def test_mixed_resource_and_permanent_error_is_not_retried(client):
    client._session.responses = [errors("RESOURCE_LIMITS_EXCEEDED", "FORBIDDEN")]
    with pytest.raises(api.GraphQLError):
        list(iterate(client))
    assert len(client._session.calls) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"connection_path": ()},
        {"connection_path": "repository.items"},
        {"cursor_variable": "first"},
        {"cursor_variable": ""},
        {"variables": []},
        {"variables": {"after": 42}},
    ],
)
def test_invalid_pagination_inputs(client, kwargs):
    arguments = dict(
        document="query Test { viewer { login } }",
        variables={},
        operation_name="Test",
        connection_path=("repository", "items"),
    )
    arguments.update(kwargs)
    with pytest.raises(ValueError):
        list(client.iter_connection(**arguments))
    assert not client._session.calls


def test_missing_connection_and_initial_cursor(client):
    client._session.responses = [Response({"data": {"repository": None}})]
    with pytest.raises(api.PaginationError):
        list(iterate(client))
    client._session.responses = [page([{"id": "bad"}], True, "initial")]
    with pytest.raises(api.PaginationError, match="repeated"):
        next(iterate(client, {"after": "initial"}))


def test_custom_cursor_and_page_variables(client):
    client._session.responses = [page([], True, "new"), page([])]
    assert (
        list(
            iterate(
                client,
                {"cursor": "initial"},
                cursor_variable="cursor",
                page_size_variable="size",
                page_size=3,
            )
        )
        == []
    )
    values = [c[1]["json"]["variables"] for c in client._session.calls]
    assert values == [{"cursor": "initial", "size": 3}, {"cursor": "new", "size": 3}]


def test_size_one_resource_failure(client):
    client._session.responses = [Response(status=504)]
    with pytest.raises(api.QueryResourceError):
        list(iterate(client, page_size=1))
    assert len(client._session.calls) == 1


def test_embedded_complete_page_requires_no_requests(client):
    from repo_history.pagination import iter_embedded_connection

    embedded = {
        "nodes": [{"id": "a"}],
        "pageInfo": {"hasNextPage": False, "endCursor": None},
    }
    assert list(
        iter_embedded_connection(client, embedded, "unused", "parent", "items", "Test")
    ) == [{"id": "a"}]
    assert not client._session.calls


def test_embedded_cursor_seeds_continuation_cycle_detection(client):
    from repo_history.pagination import iter_embedded_connection

    embedded = {
        "nodes": [{"id": "a"}],
        "pageInfo": {"hasNextPage": True, "endCursor": "initial"},
    }
    client._session.responses = [
        Response(
            {
                "data": {
                    "node": {
                        "items": {
                            "nodes": [{"id": "bad"}],
                            "pageInfo": {"hasNextPage": True, "endCursor": "initial"},
                        }
                    }
                }
            }
        )
    ]
    iterator = iter_embedded_connection(
        client, embedded, "query Test { node { id } }", "parent", "items", "Test"
    )
    assert next(iterator) == {"id": "a"}
    with pytest.raises(api.PaginationError, match="repeated"):
        next(iterator)
    assert client._session.calls[0][1]["json"]["variables"]["after"] == "initial"


def test_page_logs_advance_after_success_and_stay_fixed_on_retry(client, caplog):
    from repo_history.transport import _parent_log_context

    caplog.set_level("DEBUG", logger="repo_history.transport")
    client._session.responses = [
        page([{"id": "a"}], True, "c1"),
        Response(status=503),
        page([{"id": "b"}], True, "c2"),
        page([{"id": "c"}]),
    ]
    with _parent_log_context("pr", 87):
        assert list(iterate(client)) == [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    messages = [r.getMessage() for r in caplog.records]
    attempts = [
        m.split(" page=", 1)[1].split(" status=", 1)[0]
        for m in messages
        if " status=" in m
    ]
    assert attempts == ["1 attempt=1", "2 attempt=1", "2 attempt=2", "3 attempt=1"]
    assert all(" pr=87 " in m for m in messages)
    assert any("page=2 retry_after=1 attempt=1" in m for m in messages)
    assert all(
        set(kwargs["json"]["variables"]) == {"after", "first"}
        for _, kwargs in client._session.calls
    )


def test_embedded_first_page_and_continuation_logs_keep_parent_context(client, caplog):
    from repo_history.pagination import iter_embedded_connection
    from repo_history.transport import _parent_log_context

    caplog.set_level("DEBUG", logger="repo_history.transport")
    embedded = {
        "nodes": [{"id": "a"}],
        "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
    }
    client._session.responses = [
        Response(
            {
                "data": {
                    "node": {
                        "items": {
                            "nodes": [{"id": "b"}],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }
        )
    ]
    with _parent_log_context("issue", 42):
        assert list(
            iter_embedded_connection(
                client,
                embedded,
                "query Test { node { id } }",
                "parent",
                "items",
                "Test",
            )
        ) == [{"id": "a"}, {"id": "b"}]
    messages = [r.getMessage() for r in caplog.records]
    assert "issue=42 page=1 source=embedded nodes=1 has_next_page=True" in messages[0]
    assert "issue=42 page=2 attempt=1 status=200" in messages[1]
    assert len(client._session.calls) == 1


def test_interleaved_connection_page_numbers_do_not_leak(client, caplog):
    from repo_history.transport import _parent_log_context

    caplog.set_level("DEBUG", logger="repo_history.transport")
    client._session.responses = [page([{"id": "a"}], True, "outer"), page([]), page([])]
    outer = iterate(client)
    assert next(outer) == {"id": "a"}
    with _parent_log_context("issue", 42):
        assert list(iterate(client)) == []
    assert list(outer) == []
    messages = [r.getMessage() for r in caplog.records]
    assert "page=1 attempt=1" in messages[0] and "issue=" not in messages[0]
    assert "issue=42 page=1 attempt=1" in messages[1]
    assert "page=2 attempt=1" in messages[2] and "issue=" not in messages[2]


@pytest.mark.parametrize("start_page", [0, -1, True, 1.5, "2", None])
def test_invalid_start_page_before_network(client, start_page):
    with pytest.raises(ValueError, match="start_page"):
        list(iterate(client, start_page=start_page))
    assert not client._session.calls

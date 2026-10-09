"""Shared offline GitHub response builders; not a test suite."""

from copy import deepcopy


def empty_commits(client, repository, start, end, records):
    """Isolate lifecycle/discovery suites; commit integration has its own suite."""
    from repo_history_tracking.commits import history_bounds

    strategy, _ = history_bounds(start, end)
    return strategy


class Response:
    def __init__(self, body=None, status=200, headers=None):
        self.body = {"data": {"ok": True}} if body is None else body
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body

    def close(self):
        self.closed = True


class Session:
    def __init__(self):
        self.headers = {}
        self.responses = []
        self.calls = []
        self.closed = False
        self.adapters = {}

    def mount(self, prefix, adapter):
        self.adapters[prefix] = adapter

    def post(self, endpoint, **kwargs):
        self.calls.append((endpoint, deepcopy(kwargs)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, endpoint, **kwargs):
        return self.post(endpoint, **kwargs)

    def close(self):
        self.closed = True


def execute(client):
    return client.execute(
        "query Test($name: String!) { viewer { login } }",
        {"name": "private-value"},
        operation_name="Test",
    )


def page(nodes, next_page=False, cursor=None):
    return Response(
        {
            "data": {
                "repository": {
                    "items": {
                        "nodes": nodes,
                        "pageInfo": {"hasNextPage": next_page, "endCursor": cursor},
                    }
                }
            }
        }
    )


def iterate(client, variables=None, **kwargs):
    return client.iter_connection(
        "query Test { repository { id } }",
        variables or {},
        operation_name="Test",
        connection_path=("repository", "items"),
        **kwargs,
    )


def errors(*kinds, status=200, headers=None):
    return Response(
        {
            "data": {"partial": "must discard"},
            "errors": [
                {"type": kind, "message": "raw body secret-token private-value"}
                for kind in kinds
            ],
        },
        status=status,
        headers=headers,
    )


def repository(**updates):
    item = {
        "id": "R_example",
        "nameWithOwner": "new-owner/new-name",
        "url": "https://github.com/new-owner/new-name",
        "hasIssuesEnabled": False,
        "isEmpty": False,
        "defaultBranchRef": {
            "name": "main",
            "target": {"__typename": "Commit", "oid": "a" * 40},
        },
    }
    item.update(updates)
    return Response({"data": {"repository": item}})

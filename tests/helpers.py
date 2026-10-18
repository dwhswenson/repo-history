"""Shared offline GitHub response builders; not a test suite."""

from copy import deepcopy


def empty_commits(client, repository, start, end, records):
    """Isolate lifecycle/discovery suites; commit integration has its own suite."""
    from repo_history.commits import history_bounds

    strategy, _ = ("full", {}) if start is None else history_bounds(start, end)
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
        "databaseId": 123,
        "createdAt": "2020-01-01T00:00:00Z",
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


def replace(value, **changes):
    """Edit nested report fixture facts concisely; other dataclasses use normal replace."""
    from dataclasses import replace as dc_replace

    from repo_history import ReportMetadata

    if not isinstance(value, ReportMetadata):
        return dc_replace(value, **changes)
    repo = {}
    run = {}
    coverage = {}
    for old, new in (
        ("repository_node_id", "node_id"),
        ("repository_full_name", "full_name"),
        ("repository_url", "url"),
    ):
        if old in changes:
            repo[new] = changes.pop(old)
    for old, new in (
        ("collection_started_at", "started_at"),
        ("collection_finished_at", "finished_at"),
        ("parent_discovery", "parent_discovery"),
        ("commit_history", "commit_history"),
    ):
        if old in changes:
            run[new] = changes.pop(old)
    if "coverage_limitations" in changes:
        coverage["limitations"] = changes.pop("coverage_limitations")
    if repo:
        changes["repository"] = dc_replace(
            changes.pop("repository", value.repository), **repo
        )
    if run:
        if "started_at" in run:
            coverage["as_of"] = run["started_at"]
        changes["provenance"] = dc_replace(
            value.provenance,
            **{
                k: dc_replace(getattr(value.provenance, k), **run)
                for k in ("created_by", "last_updated_by", "last_rebuilt_by")
            },
        )
    if coverage:
        changes["coverage"] = dc_replace(value.coverage, **coverage)
    return dc_replace(value, **changes)


def without_run_ids(value):
    """Compare independent captures without treating their run UUIDs as content."""
    import json

    from repo_history.serialization import to_data

    if isinstance(value, (bytes, str)):
        value = json.loads(value)
    else:
        value = to_data(value)

    def clean(x):
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items() if k != "run_id"}
        if isinstance(x, list):
            return [clean(v) for v in x]
        return x

    return clean(value)

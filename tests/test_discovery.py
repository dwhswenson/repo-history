"""Offline discovery selection, hydration, and shared-source report comparisons."""

from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

import repo_history as api
from repo_history import history as collection
from repo_history.commits import BOUNDED_LIMITATION
from repo_history.discovery import BOUNDED_DISCOVERY_LIMITATION
from repo_history.queries import parent_hydration_query

from .github import (
    actor,
    collect_update_report,
    comment,
    connection,
    event,
    hydrated,
    inline,
    parent,
    rest_parent,
    review,
    thread,
)
from .helpers import (
    Response,
    Session,
    empty_commits,
    errors,
    repository,
)


@pytest.fixture(autouse=True)
def isolate_commits(monkeypatch):
    monkeypatch.setattr("repo_history.history.collect_commits", empty_commits)


NOW = datetime(2024, 4, 15, tzinfo=timezone.utc)


@pytest.fixture
def session(monkeypatch):
    result = Session()
    monkeypatch.setattr("requests.Session", lambda: result)
    monkeypatch.setattr(collection, "_utc_now", lambda: NOW)
    return result


def bounded(session, responses, **kwargs):
    session.responses = responses
    result = collect_update_report(
        "old-owner", "old-name", 2024, 2, token="test", **kwargs
    )
    assert not session.responses
    assert session.closed
    return result


def test_resolved_name_all_states_overlap_and_pr_identity(session):
    report = bounded(
        session,
        [
            repository(),
            Response([rest_parent(pr=True)]),
            hydrated(
                parent("P_real", __typename="PullRequest", date="2024-02-01T00:00:00Z")
            ),
        ],
    )
    url, request = session.calls[1]
    assert urlsplit(url).path == "/repos/new-owner/new-name/issues"
    assert parse_qs(urlsplit(url).query) == {
        "state": ["all"],
        "per_page": ["100"],
        "since": ["2024-01-31T23:59:59Z"],
    }
    assert request["allow_redirects"] is False
    payload = session.calls[2][1]["json"]
    assert payload["variables"] == {"id": "R_example", "number0": 1}
    assert "REST_ISSUE_ID" not in payload["query"]
    assert report.activities[0].subject.node_id == "P_real"
    assert all(
        call[1]["json"]["variables"]["id"] == "P_real" for call in session.calls[3:]
    )


def test_unordered_pages_bounds_and_duplicate_updated_times(session):
    records = [
        rest_parent(1, updated_at="2024-01-31T23:59:59Z"),  # overlap excluded
        rest_parent(2, updated_at="2024-02-01T00:00:00Z"),
        rest_parent(3, created_at="2024-03-01T00:00:00Z"),
        rest_parent(4, updated_at="2024-09-01T00:00:00Z"),
        rest_parent(5, created_at="2024-02-29T23:59:59Z"),
    ]
    following = "https://api.github.com/repos/new-owner/new-name/issues?page=2"
    report = bounded(
        session,
        [
            repository(hasIssuesEnabled=True),
            Response(records, headers={"Link": f'<{following}>; rel="next"'}),
            Response(
                [
                    deepcopy(records[1]),
                    rest_parent(1),
                    rest_parent(2, updated_at="2024-01-31T23:59:59Z"),
                ]
            ),
            hydrated(
                *[
                    parent(
                        f"I_{n}",
                        number=n,
                        __typename="Issue",
                        date="2024-02-10T00:00:00Z",
                    )
                    for n in (2, 4, 5, 1)
                ]
            ),
        ],
    )
    assert len(report.activities) == 4
    assert session.calls[3][1]["json"]["variables"] == {
        "id": "R_example",
        "number0": 2,
        "number1": 4,
        "number2": 5,
        "number3": 1,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"number": None},
        {"number": True},
        {"number": 0},
        {"number": 2**31},
        {"created_at": None},
        {"created_at": "2024-01-01"},
        {"created_at": "invalid"},
        {"updated_at": None},
        {"updated_at": "2024-02-01T00:00:00"},
        {"updated_at": "invalid"},
        {"pull_request": None},
        {"pull_request": {}},
        {"pull_request": {"url": 1}},
    ],
)
def test_invalid_rest_records_fail_and_close(session, changes):
    session.responses = [repository(), Response([rest_parent(**changes)])]
    with pytest.raises(api.TransportError):
        collect_update_report("o", "r", 2024, 2, token="test")
    assert session.closed
    assert len(session.calls) == 2


@pytest.mark.parametrize("field", ["number", "created_at", "updated_at"])
def test_missing_rest_field_fails(session, field):
    item = rest_parent()
    del item[field]
    session.responses = [repository(), Response([item])]
    with pytest.raises(api.TransportError):
        collect_update_report("o", "r", 2024, 2, token="test")
    assert session.closed


@pytest.mark.parametrize(
    "duplicate", [rest_parent(pr=True), rest_parent(created_at="2023-01-01T00:00:00Z")]
)
def test_conflicting_discovery_identity_fails(session, duplicate):
    session.responses = [repository(), Response([rest_parent(), duplicate])]
    with pytest.raises(api.TransportError, match="conflicting"):
        collect_update_report("o", "r", 2024, 2, token="test")
    assert session.closed


@pytest.mark.parametrize(
    "item",
    [
        None,
        {},
        parent(__typename="Issue"),
        parent(__typename="PullRequest", number=2),
        parent(__typename="PullRequest", number=True),
    ],
)
def test_missing_or_mismatched_hydration_fails(session, item):
    session.responses = [repository(), Response([rest_parent(pr=True)]), hydrated(item)]
    with pytest.raises(api.GraphQLError):
        collect_update_report("o", "r", 2024, 2, token="test")
    assert session.closed
    assert len(session.calls) == 3


@pytest.mark.parametrize("data", [{}, {"node": None}, {"node": {}}, {"node": []}])
def test_missing_hydration_repository_or_alias_fails(session, data):
    session.responses = [
        repository(),
        Response([rest_parent(pr=True)]),
        Response({"data": data}),
    ]
    with pytest.raises(api.GraphQLError):
        collect_update_report("o", "r", 2024, 2, token="test")
    assert session.closed


def test_subject_validation_is_shared(session):
    invalid = parent(__typename="PullRequest")
    del invalid["author"]
    session.responses = [
        repository(),
        Response([rest_parent(pr=True)]),
        hydrated(invalid),
    ]
    with pytest.raises(api.GraphQLError, match="author"):
        collect_update_report("o", "r", 2024, 2, token="test")
    assert session.closed


def test_hydration_batches_and_late_failure_never_switch_strategy(session):
    parents = [
        parent(f"P_{n}", number=n, __typename="PullRequest") for n in range(1, 22)
    ]
    session.responses = [
        repository(),
        Response([rest_parent(n, pr=True) for n in range(1, 22)]),
        hydrated(*parents[:20]),
        errors("FORBIDDEN"),
    ]
    with pytest.raises(api.GraphQLError):
        collect_update_report()
    payloads = [kwargs["json"] for _, kwargs in session.calls if "json" in kwargs]
    hydration = [
        p for p in payloads if p["operationName"] == "HydrateRepositoryParents"
    ]
    assert [len(p["variables"]) for p in hydration] == [21, 2]
    assert hydration[-1]["variables"] == {"id": "R_example", "number0": 21}
    assert not any(
        p["operationName"] in ("RepositoryIssues", "RepositoryPullRequests")
        for p in payloads
    )
    assert session.closed


@pytest.mark.parametrize("failure", [Response(status=410), errors("FORBIDDEN")])
def test_disabled_issue_endpoint_or_hydration_failure_is_not_empty(session, failure):
    session.responses = (
        [repository(), failure]
        if failure.status_code == 410
        else [repository(), Response([rest_parent(pr=True)]), failure]
    )
    with pytest.raises(api.GitHubAPIError):
        collect_update_report("o", "r", 2024, 2, token="test")
    assert session.closed


def test_late_rest_failure_does_not_hydrate_or_fallback(session):
    next_url = "https://api.github.com/repos/new-owner/new-name/issues?page=2"
    session.responses = [
        repository(),
        Response([rest_parent(pr=True)], headers={"Link": f'<{next_url}>; rel="next"'}),
        Response(status=404),
    ]
    with pytest.raises(api.TransportError):
        collect_update_report("o", "r", 2024, 2, token="test")
    assert session.closed
    assert len(session.calls) == 3


def test_earliest_month_selects_full_before_discovery(session, caplog):
    caplog.set_level("INFO", logger="repo_history.discovery")
    session.responses = [repository(), connection("pullRequests")]
    report = collect_update_report("o", "r", 1, 1, token="test")
    assert report.metadata.month == "0001-01"
    assert report.metadata.provenance.last_updated_by.parent_discovery == "full"
    assert BOUNDED_DISCOVERY_LIMITATION not in report.metadata.coverage.limitations
    assert api.report_from_json(api.report_to_json(report)) == report
    assert "overlap underflow" in caplog.text
    assert "strategy=full" in caplog.text
    assert all("json" in kwargs for _, kwargs in session.calls)
    assert session.closed


def test_early_year_overlap_is_zero_padded(session):
    session.responses = [repository(), Response([])]
    collect_update_report("o", "r", 1, 2, token="test")
    assert parse_qs(urlsplit(session.calls[1][0]).query)["since"] == [
        "0001-01-31T23:59:59Z"
    ]


@pytest.mark.parametrize("count", [1, 20])
def test_hydration_queries_reuse_fragments_and_variables(count):
    query = parent_hydration_query(count)
    assert query.count("fragment LifecycleActor") == 1
    assert query.count("fragment LifecycleIssue") == 1
    assert query.count("fragment LifecyclePullRequest") == 1
    assert query.count("issueOrPullRequest(number: $number") == count
    assert f"$number{count - 1}: Int!" in query
    assert "$id: ID!" in query
    assert "__typename" in query
    assert "body" not in query


@pytest.mark.parametrize("count", [0, 21, True, "1"])
def test_invalid_hydration_batch_size(count):
    with pytest.raises(ValueError):
        parent_hydration_query(count)


@pytest.mark.parametrize("kind", ["issues", "prs", "mixed"])
def test_discovery_hydration_batch_limits(session, kind):
    from repo_history.discovery import discover_parents
    from repo_history.repository import lookup_repository

    count, sizes = 41, [20, 20, 1]
    parents, rest = [], []
    for number in range(1, count + 1):
        pr = kind == "prs" or (kind == "mixed" and number % 2 == 0)
        item = parent(
            f"P_{number}", number=number, __typename="PullRequest" if pr else "Issue"
        )
        parents.append(item)
        rest.append(rest_parent(number, pr=pr))
    session.responses = [repository(hasIssuesEnabled=True), Response(rest)]
    offset = 0
    for size in sizes:
        session.responses.append(hydrated(*parents[offset : offset + size]))
        offset += size
    with api.GraphQLClient("test") as client:
        repo = lookup_repository(client, "o", "r")
        collected = list(
            discover_parents(
                client,
                repo,
                datetime(2024, 2, 1, tzinfo=timezone.utc),
                datetime(2024, 3, 1, tzinfo=timezone.utc),
                strategy="bounded",
            )
        )
    assert [item for _, item, _ in collected] == parents
    assert [issues for issues, _, _ in collected] == [
        item["__typename"] == "Issue" for item in parents
    ]
    payloads = [kw["json"] for _, kw in session.calls if "json" in kw]
    hydration = [
        p for p in payloads if p["operationName"] == "HydrateRepositoryParents"
    ]
    assert [len(p["variables"]) - 1 for p in hydration] == sizes
    assert all("...HistoryIssue" in p["query"] for p in hydration)
    assert not session.responses
    assert session.closed


@pytest.mark.parametrize("batch", [0, 1])
@pytest.mark.parametrize("failure", ["missing", "number", "type", "resource"])
def test_enriched_hydration_validates_whole_batch_and_never_falls_back(
    session, monkeypatch, batch, failure
):
    from repo_history.discovery import discover_parents
    from repo_history.repository import lookup_repository

    monkeypatch.setattr("repo_history.transport.time.sleep", lambda _: None)
    parents = [
        parent(f"P_{n}", number=n, __typename="PullRequest") for n in range(1, 23)
    ]
    batches = [hydrated(*parents[:20]), hydrated(*parents[20:])]
    # Corrupt the last alias: no preceding parent in that batch may be yielded.
    node = batches[batch].body["data"]["node"]
    alias = f"parent{len(node) - 1}"
    if failure == "missing":
        del node[alias]
    elif failure == "number":
        node[alias]["number"] = 100
    elif failure == "type":
        node[alias]["__typename"] = "Issue"
    else:
        batches[batch] = errors("MAX_NODE_LIMIT_EXCEEDED")
    session.responses = [
        repository(),
        Response([rest_parent(n, pr=True) for n in range(1, 23)]),
        *batches[:batch],
        *([batches[batch]] * (4 if failure == "resource" else 1)),
    ]
    collected = []
    with api.GraphQLClient("test") as client:
        repo = lookup_repository(client, "o", "r")
        with pytest.raises(api.GraphQLError):
            for item in discover_parents(
                client,
                repo,
                datetime(2024, 2, 1, tzinfo=timezone.utc),
                datetime(2024, 3, 1, tzinfo=timezone.utc),
                strategy="bounded",
            ):
                collected.append(item)
    assert len(collected) == batch * 20
    payloads = [kw["json"] for _, kw in session.calls if "json" in kw]
    assert {p["operationName"] for p in payloads} == {
        "RepositoryLookup",
        "HydrateRepositoryParents",
    }
    hydration = [
        p for p in payloads if p["operationName"] == "HydrateRepositoryParents"
    ]
    if failure == "resource":
        assert all(p == hydration[-1] for p in hydration[-4:])
    assert session.closed


class SourceSession(Session):
    """Serve both APIs from one history, including the REST endpoint's actual filter."""

    def __init__(self, *, issues_enabled=True, counterexample=False):
        super().__init__()
        self.issues_enabled = issues_enabled
        self.source = [
            (
                rest_parent(1, updated_at="2024-09-01T00:00:00Z"),
                parent("I_old", number=1, __typename="Issue", state="CLOSED"),
            ),
            (
                rest_parent(2, pr=True, updated_at="2024-09-01T00:00:00Z"),
                parent(
                    "P_old",
                    number=2,
                    __typename="PullRequest",
                    state="MERGED",
                    isDraft=True,
                    author=None,
                ),
            ),
            (
                rest_parent(3, created_at="2024-02-01T00:00:00Z"),
                parent(
                    "I_new", number=3, __typename="Issue", date="2024-02-01T00:00:00Z"
                ),
            ),
            (
                rest_parent(
                    4,
                    pr=True,
                    created_at="2024-03-01T00:00:00Z",
                    updated_at="2024-03-01T00:00:00Z",
                ),
                parent(
                    "P_new",
                    number=4,
                    __typename="PullRequest",
                    date="2024-03-01T00:00:00Z",
                ),
            ),
        ]
        if counterexample:
            self.source[1][0]["updated_at"] = "2024-01-15T00:00:00Z"
        self.nested = {
            ("IssueLifecycle", "I_old"): [
                event(),
                event("E_reopen", kind="ReopenedEvent"),
                event("E_again"),
            ],
            ("IssueComments", "I_old"): [
                comment("C_null", author=None),
                comment("C_boundary", createdAt="2024-02-01T01:00:00+01:00"),
            ],
            ("PullRequestLifecycle", "P_old"): [event("E_merge", kind="MergedEvent")],
            ("PullRequestComments", "P_old"): [
                comment("C_bot", author=actor("B_one", kind="Bot")),
                comment("C_end", createdAt="2024-03-01T00:00:00Z"),
            ],
            ("PullRequestReviews", "P_old"): [
                review(state="DISMISSED"),
                review("V_pending", state="PENDING", submittedAt=None),
            ],
            ("PullRequestReviewThreads", "P_old"): [
                thread(),
                thread("T_two", isResolved=False, isOutdated=False),
            ],
            ("ReviewThreadComments", "T_one"): [
                inline(createdAt="2024-01-31T00:00:00Z"),
                inline("C_reply", pullRequestReview=None),
            ],
            ("ReviewThreadComments", "T_two"): [
                inline(
                    "C_fallback", publishedAt=None, createdAt="2024-02-29T23:59:59Z"
                ),
                inline("C_pending", publishedAt=None, state="PENDING"),
            ],
        }

    def post(self, endpoint, **kwargs):
        self.calls.append((endpoint, deepcopy(kwargs)))
        payload = kwargs["json"]
        operation, values = payload["operationName"], payload["variables"]
        if operation == "RepositoryLookup":
            return repository(hasIssuesEnabled=self.issues_enabled)
        if operation == "HydrateRepositoryParents":
            by_number = {p["number"]: p for _, p in self.source}
            items = [
                deepcopy(by_number[values[f"number{i}"]])
                for i in range(len(values) - 1)
            ]
            for item in items:
                self.embed_parent(item)
            return hydrated(*items)
        if operation in ("RepositoryIssues", "RepositoryPullRequests"):
            field = "issues" if operation == "RepositoryIssues" else "pullRequests"
            kind = "Issue" if field == "issues" else "PullRequest"
            items = [p for _, p in self.source if p["__typename"] == kind]
        else:
            field = {
                "IssueLifecycle": "timelineItems",
                "PullRequestLifecycle": "timelineItems",
                "IssueComments": "comments",
                "PullRequestComments": "comments",
                "PullRequestReviews": "reviews",
                "PullRequestReviewThreads": "reviewThreads",
                "ReviewThreadComments": "comments",
            }[operation]
            items = self.nested.get((operation, values["id"]), [])
        offset = int(values.get("after") or 0)
        more = offset + 1 < len(items)
        nodes = deepcopy(items[offset : offset + 1])
        for item in nodes:
            if operation in ("RepositoryIssues", "RepositoryPullRequests"):
                self.embed_parent(item)
            elif operation == "PullRequestReviewThreads":
                item["comments"] = self.initial("ReviewThreadComments", item["id"])
        return connection(
            field, nodes, more=more, cursor=str(offset + 1) if more else None
        )

    def initial(self, operation, node_id):
        items = deepcopy(self.nested.get((operation, node_id), []))
        from .github import page

        result = page(
            items[:1], more=len(items) > 1, cursor="1" if len(items) > 1 else None
        )
        if operation == "PullRequestReviewThreads":
            for thread in result["nodes"]:
                thread["comments"] = self.initial("ReviewThreadComments", thread["id"])
        return result

    def embed_parent(self, item):
        issues = item["__typename"] == "Issue"
        fields = {
            "timelineItems": "IssueLifecycle" if issues else "PullRequestLifecycle",
            "comments": "IssueComments" if issues else "PullRequestComments",
        }
        if not issues:
            fields.update(
                reviews="PullRequestReviews", reviewThreads="PullRequestReviewThreads"
            )
        for field, operation in fields.items():
            item[field] = self.initial(operation, item["id"])

    def get(self, endpoint, **kwargs):
        self.calls.append((endpoint, deepcopy(kwargs)))
        parsed = urlsplit(endpoint)
        params = parse_qs(parsed.query)
        assert params["state"] == ["all"]
        since = datetime.fromisoformat(params["since"][0])
        # Do not encode the completeness assumption into the source: REST filtering
        # depends only on REST updated_at, independently of the nested activities.
        items = [
            deepcopy(r)
            for r, _ in reversed(self.source)
            if datetime.fromisoformat(r["updated_at"]) > since
        ]
        page = int(params.get("page", ["1"])[0])
        offset = (page - 1) * 2
        headers = {}
        if offset + 2 < len(items):
            from urllib.parse import urlencode

            params["page"] = [str(page + 1)]
            following = (
                "https://api.github.com"
                + parsed.path
                + "?"
                + urlencode(params, doseq=True)
            )
            headers["Link"] = f'<{following}>; rel="next"'
        return Response(items[offset : offset + 2], headers=headers)


def run_source(monkeypatch, source, strategy, *, month=2):
    source.closed = False
    monkeypatch.setattr("requests.Session", lambda: source)
    monkeypatch.setattr(collection, "_utc_now", lambda: NOW)
    if strategy == "full":
        key = f"2024-{month:02d}"
        result = api.history_to_months(
            api.collect_history("o", "r", token="test"), key, key
        )[0]
    else:
        result = collect_update_report(month=month)
    assert source.closed
    api.validate_report(result)
    assert api.report_from_json(api.report_to_json(result)) == result
    return result


@pytest.mark.parametrize("issues_enabled", [True, False])
@pytest.mark.parametrize("month", [1, 2, 3, 4])
def test_shared_histories_produce_identical_complete_reports(
    monkeypatch, issues_enabled, month
):
    full = run_source(
        monkeypatch, SourceSession(issues_enabled=issues_enabled), "full", month=month
    )
    bounded_report = run_source(
        monkeypatch,
        SourceSession(issues_enabled=issues_enabled),
        "bounded",
        month=month,
    )
    assert full.metadata.provenance.last_updated_by.parent_discovery == "full"
    assert (
        bounded_report.metadata.provenance.last_updated_by.parent_discovery == "bounded"
    )
    assert BOUNDED_DISCOVERY_LIMITATION not in full.metadata.coverage.limitations
    assert (
        bounded_report.metadata.coverage.limitations.count(BOUNDED_DISCOVERY_LIMITATION)
        == 1
    )
    assert (
        tuple(
            item
            for item in bounded_report.metadata.coverage.limitations
            if item not in (BOUNDED_DISCOVERY_LIMITATION, BOUNDED_LIMITATION)
        )
        == full.metadata.coverage.limitations
    )
    assert full.activities == bounded_report.activities
    assert full.actors == bounded_report.actors
    assert full.summaries == bounded_report.summaries
    assert bounded_report.metadata.is_partial == (month == 4)
    if month == 2:
        kinds = {a.type for a in full.activities}
        assert {
            "pr_review",
            "pr_review_comment",
            "pr_merged",
            "pr_discussion_comment",
        } <= kinds
        if issues_enabled:
            assert {
                "issue_opened",
                "issue_closed",
                "issue_reopened",
                "issue_comment",
            } <= kinds
        else:
            assert not any(a.subject.kind == "issue" for a in full.activities)


def test_counterexample_explicitly_demonstrates_unproven_completeness(monkeypatch):
    full = run_source(monkeypatch, SourceSession(counterexample=True), "full")
    optimized = run_source(monkeypatch, SourceSession(counterexample=True), "bounded")
    missing = {a.id for a in full.activities} - {a.id for a in optimized.activities}
    assert "pr_review:V_one" in missing
    assert "pr_review_comment:C_inline" in missing
    assert all(a.subject.node_id != "P_old" for a in optimized.activities)
    assert full.activities != optimized.activities


def test_excluded_malformed_history_changes_validation_scope(monkeypatch):
    source = SourceSession()
    source.source[1][0]["updated_at"] = "2024-01-15T00:00:00Z"
    source.nested[("PullRequestReviews", "P_old")] = [review(submittedAt=None)]
    with pytest.raises(api.GraphQLError, match="submission"):
        run_source(monkeypatch, source, "full")
    assert source.closed
    report = run_source(monkeypatch, source, "bounded")
    assert all(a.subject.node_id != "P_old" for a in report.activities)


@pytest.mark.parametrize(
    "created,updated,kept",
    [
        ("2024-03-01T01:00:00+01:00", "2024-03-02T00:00:00Z", False),
        ("2024-02-01T01:00:00+01:00", "2024-02-01T01:00:00+01:00", True),
        ("2024-01-01T00:00:00Z", "2024-02-01T00:59:59+01:00", False),
    ],
)
def test_rest_bounds_normalize_timezones(session, created, updated, kept):
    responses = [
        repository(),
        Response([rest_parent(pr=True, created_at=created, updated_at=updated)]),
    ]
    if kept:
        responses += [
            hydrated(parent("P_kept", __typename="PullRequest", date=created)),
        ]
    result = bounded(session, responses)
    assert len(result.activities) == int(kept)


def test_multiple_hydration_batches_succeed(session):
    responses = [
        repository(),
        Response([rest_parent(n, pr=True) for n in range(1, 42)]),
    ]
    for numbers in (range(1, 21), range(21, 41), range(41, 42)):
        responses.append(
            hydrated(
                *[
                    parent(
                        f"P_{n}",
                        number=n,
                        __typename="PullRequest",
                        date="2024-02-01T00:00:00Z",
                    )
                    for n in numbers
                ]
            )
        )
    report = bounded(session, responses)
    assert len(report.activities) == 41
    api.validate_report(report)


def test_live_style_numeric_pagination_path(session):
    target = "https://api.github.com/repositories/123/issues?page=2"
    bounded(
        session,
        [
            repository(),
            Response([], headers={"Link": f'<{target}>; rel="next"'}),
            Response([]),
        ],
    )
    assert session.calls[2][0] == target

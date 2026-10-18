"""Source builders shared by offline collection, discovery, and update tests."""

from datetime import datetime, timezone

from .helpers import Response

NOW = datetime(2024, 4, 15, tzinfo=timezone.utc)


def actor(key="U_author", *, kind="User", login=None, **updates):
    result = {
        "__typename": kind,
        "id": key,
        "databaseId": 1,
        "login": login or key,
        "url": f"https://github.com/{key}",
    }
    if kind == "User":
        result["name"] = key
    result.update(updates)
    return result


def parent(key="I_old", *, date="2024-01-01T00:00:00Z", **updates):
    result = {
        "id": key,
        "number": 1,
        "title": "Current title",
        "url": f"https://github.com/owner/repo/issues/{key}",
        "createdAt": date,
        "author": actor(),
        **{
            field: page()
            for field in ("timelineItems", "comments", "reviews", "reviewThreads")
        },
    }
    result.update(updates)
    return result


def event(key="E_close", *, kind="ClosedEvent", date="2024-02-10T00:00:00Z", **updates):
    result = {
        "__typename": kind,
        "id": key,
        "createdAt": date,
        "actor": actor("U_closer"),
    }
    if kind == "ClosedEvent":
        result.update(
            stateReason="COMPLETED", url=f"https://github.com/owner/repo/event/{key}"
        )
    if kind == "MergedEvent":
        result.update(
            commit={"oid": "b" * 40}, url=f"https://github.com/owner/repo/event/{key}"
        )
    result.update(updates)
    return result


def connection(field, nodes=(), *, cursor=None, more=False):
    return Response(
        {
            "data": {
                "node": {
                    field: {
                        "nodes": list(nodes),
                        "pageInfo": {"endCursor": cursor, "hasNextPage": more},
                    }
                }
            }
        }
    )


def comment(key="C_one", **updates):
    result = {
        "id": key,
        "createdAt": "2024-02-10T00:00:00Z",
        "url": f"https://github.com/owner/repo/comment/{key}",
        "author": actor("U_commenter"),
    }
    result.update(updates)
    return result


def review(key="V_one", **updates):
    result = comment(key)
    result.pop("createdAt")
    result.update(submittedAt="2024-02-11T00:00:00Z", state="COMMENTED")
    result.update(updates)
    return result


def inline(key="C_inline", **updates):
    result = comment(key)
    result.update(
        publishedAt="2024-02-12T00:00:00Z",
        state="SUBMITTED",
        pullRequestReview={"id": "V_one"},
    )
    result.update(updates)
    return result


def thread(key="T_one", **updates):
    return {
        "id": key,
        "isResolved": True,
        "isOutdated": True,
        "comments": page(),
        **updates,
    }


def page(nodes=(), *, more=False, cursor=None):
    return {
        "nodes": list(nodes),
        "pageInfo": {"hasNextPage": more, "endCursor": cursor},
    }


def identity(name="Author", email="author@example.org", user=None):
    return {"name": name, "email": email, "user": user}


def commit(sha="b", *, date="2024-02-10T00:00:00Z", **updates):
    author = identity(user=actor())
    value = {
        "id": f"C_{sha}",
        "oid": sha * 40,
        "url": f"https://github.com/o/r/commit/{sha * 40}",
        "messageHeadline": "A commit headline",
        "authoredDate": "2024-01-15T00:00:00Z",
        "committedDate": date,
        "author": author,
        "committer": identity(
            "Committer", "committer@example.org", actor("U_committer")
        ),
        "authors": page([author]),
        "parents": page(),
    }
    value.update(updates)
    return value


def history(nodes=(), **kwargs):
    return Response({"data": {"node": {"object": {"history": page(nodes, **kwargs)}}}})


def rest_parent(number=1, *, pr=False, **updates):
    result = {
        "number": number,
        "node_id": "REST_ISSUE_ID_NOT_PR_ID",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-02-10T00:00:00Z",
    }
    if pr:
        result["pull_request"] = {
            "url": f"https://api.github.com/repos/o/r/pulls/{number}"
        }
    result.update(updates)
    return result


def hydrated(*parents):
    return Response(
        {"data": {"node": {f"parent{i}": p for i, p in enumerate(parents)}}}
    )


def collect_update_report(owner="o", repo="r", year=2024, month=2, *, token="test"):
    """Exercise the retained interval engine without writing files in unit tests."""
    from repo_history.collection import _month_bounds
    from repo_history.history import _collect_snapshot, _history_to_months

    start, end = _month_bounds(year, month)
    snapshot, parents, commits = _collect_snapshot(
        owner, repo, token=token, start=start, end=end
    )
    key = f"{year:04d}-{month:02d}"
    return _history_to_months(
        snapshot, key, key, parent_discovery=parents, commit_history=commits
    )[0]

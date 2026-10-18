"""One full repository traversal and network-free monthly projection."""

import logging
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from time import perf_counter

from ._progress import emit
from .collection import COVERAGE_LIMITATIONS, _collect_parent, _month_bounds, _Records
from .commits import BOUNDED_LIMITATION, collect_commits
from .discovery import BOUNDED_DISCOVERY_LIMITATION, discover_parents, select_strategy
from .errors import GraphQLError, ReportWriteError
from .models import CommitMetadata, MonthlyReport, RepositoryHistory
from .provenance import make_run, report_metadata
from .reports import build_report
from .repository import lookup_repository
from .transport import GraphQLClient

LOG = logging.getLogger(__name__)


def _utc_now():
    return datetime.now(timezone.utc)


def collect_history(
    owner: str, repo: str, *, token: str | None = None
) -> RepositoryHistory:
    """Collect all accessible lifecycle history and captured default-head commits.

    No files are written. Date filtering and month selection happen offline in
    history_to_months. Failures raise without returning a partial snapshot.
    The entire normalized history is held in memory; no resume cache is saved.
    """
    return _collect_history(owner, repo, token=token)


def _collect_history(
    owner, repo, *, token=None, expected_repository_id=None, started=None
):
    history, _, _ = _collect_snapshot(
        owner,
        repo,
        token=token,
        expected_repository_id=expected_repository_id,
        started=started,
    )
    return history


def _collect_snapshot(
    owner,
    repo,
    *,
    token=None,
    expected_repository_id=None,
    started=None,
    start=None,
    end=None,
    discovery="full",
):
    """Collect one interval (or full history) with actual strategy provenance."""
    if any(
        not isinstance(value, str) or not value.strip() or "/" in value
        for value in (owner, repo)
    ):
        raise ValueError("owner and repo must be nonempty names without slashes")
    started = _utc_now() if started is None else started
    timer = perf_counter()
    with GraphQLClient(token) as client:
        completed = False
        try:
            repository = lookup_repository(client, owner, repo)
            if (
                expected_repository_id is not None
                and repository.repository_node_id != expected_repository_id
            ):
                raise ReportWriteError("report belongs to a different repository")
            if repository.created_at is None:
                raise GraphQLError(
                    "missing repository creation timestamp",
                    operation="RepositoryLookup",
                )
            discovery = select_strategy(start, discovery)
            records = _Records()
            for issues, parent, operation in discover_parents(
                client,
                repository,
                start,
                end,
                strategy=discovery,
                include_activity=True,
            ):
                _collect_parent(
                    client,
                    parent,
                    operation,
                    start,
                    end,
                    records,
                    issues=issues,
                    initial_pages=True,
                )
            commit_strategy = collect_commits(client, repository, start, end, records)
            limitations = COVERAGE_LIMITATIONS
            if discovery == "bounded":
                limitations += (BOUNDED_DISCOVERY_LIMITATION,)
            if commit_strategy == "bounded":
                limitations += (BOUNDED_LIMITATION,)
            if not repository.issues_enabled:
                limitations += (
                    "Issues are disabled; the issue connection was not collected.",
                )
            result = RepositoryHistory(
                repository.repository_node_id,
                repository.repository_full_name,
                repository.repository_url,
                repository.created_at,
                repository.default_branch,
                repository.default_branch_head,
                started,
                _utc_now(),
                limitations,
                tuple(sorted(records.actors.values(), key=lambda actor: actor.key)),
                tuple(
                    sorted(
                        records.activities.values(),
                        key=lambda activity: (activity.occurred_at, activity.id),
                    )
                ),
            )
            completed = True
            result = replace(
                result,
                run=make_run(
                    "collect_history",
                    started,
                    result.collection_finished_at,
                    since=start,
                    until=end,
                    parents=discovery,
                    commits=commit_strategy,
                ),
            )
            return result, discovery, commit_strategy
        finally:
            rest, graphql = client._api_usage["rest"], client._api_usage["graphql"]
            LOG.info(
                "History collection status=%s elapsed_seconds=%.3f "
                "REST requests=%s retries=%s points=%s unmeasured_attempts=%s "
                "GraphQL requests=%s retries=%s points=%s unmeasured_attempts=%s",
                "completed" if completed else "failed",
                perf_counter() - timer,
                rest.attempts,
                rest.retries,
                rest.points,
                rest.unmeasured_attempts,
                graphql.attempts,
                graphql.retries,
                graphql.points,
                graphql.unmeasured_attempts,
            )


def history_to_months(
    history: RepositoryHistory, start: str | None = None, end: str | None = None
) -> list[MonthlyReport]:
    """Return validated reports for an inclusive range without any API requests.

    Default start includes imported commits older than repository creation;
    default end is the UTC month at collection start. Future output months are
    rejected relative to that snapshot. Empty months are included.
    """
    return _history_to_months(history, start, end)


def _history_to_months(
    history, start=None, end=None, *, parent_discovery="full", commit_history="full"
):
    """Shared projection with explicit collection provenance."""
    # Imported lazily because backfill integrates this module's collector.
    from .backfill import _parse_month, _validate_range

    if not isinstance(history, RepositoryHistory):
        raise ValueError("history must be a RepositoryHistory")
    if history.run is None:
        history = replace(
            history,
            run=make_run(
                "manual",
                history.collection_started_at,
                history.collection_finished_at,
                parents=parent_discovery,
                commits=commit_history,
            ),
        )
    earliest = min(
        (activity.occurred_at for activity in history.activities),
        default=history.repository_created_at,
    )
    earliest = min(earliest, history.repository_created_at).astimezone(timezone.utc)
    started = history.collection_started_at.astimezone(timezone.utc)
    start = f"{earliest.year:04d}-{earliest.month:02d}" if start is None else start
    end = f"{started.year:04d}-{started.month:02d}" if end is None else end
    year, month = _validate_range(start, end, started)
    last = _parse_month(end)
    emit("total", stage="Grouping", total=(last[0] - year) * 12 + last[1] - month + 1)
    emit("detail", detail=f"Indexing {len(history.activities)} activities into months")
    buckets = defaultdict(list)
    for activity in history.activities:
        occurred = activity.occurred_at.astimezone(timezone.utc)
        buckets[(occurred.year, occurred.month)].append(activity)
    actors = {actor.key: actor for actor in history.actors}
    reports = []
    while (year, month) <= last:
        emit("detail", detail=f"Building and validating {year:04d}-{month:02d}.json")
        first, after = _month_bounds(year, month)
        activities = buckets.get((year, month), ())
        referenced = set()
        for activity in activities:
            referenced.add(activity.subject.author_key)
            referenced.update(credit.actor_key for credit in activity.credits)
            if isinstance(activity.metadata, CommitMetadata):
                referenced.add(activity.metadata.committer.actor_key)
        metadata = report_metadata(
            history,
            f"{year:04d}-{month:02d}",
            first,
            after,
            history_start=f"{earliest.year:04d}-{earliest.month:02d}"
            if parent_discovery == commit_history == "full"
            else None,
            parents=parent_discovery,
            commits=commit_history,
        )
        reports.append(
            build_report(
                metadata,
                (
                    actors[key]
                    for key in referenced
                    if key is not None and key in actors
                ),
                activities,
            )
        )
        emit("advance", stage="Grouping")
        year, month = year + (month == 12), month % 12 + 1
    return reports

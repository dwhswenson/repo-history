"""Collection facts carried from capture through monthly projection."""

from dataclasses import replace
from importlib.metadata import version
from uuid import uuid4

from .models import (
    CollectionRun,
    Coverage,
    Provenance,
    ReportMetadata,
    RepositoryMetadata,
)


def make_run(
    command,
    started,
    finished,
    *,
    since=None,
    until=None,
    parents=None,
    commits=None,
    requested_start=None,
    requested_end=None,
):
    return CollectionRun(
        str(uuid4()),
        command,
        "rebuild",
        version("repo-history"),
        started,
        finished,
        since,
        until,
        requested_start,
        requested_end,
        parents,
        commits,
    )


def report_metadata(
    history,
    month,
    first,
    after,
    *,
    history_start=None,
    parents="bounded",
    commits="bounded",
):
    run = history.run or make_run(
        "manual",
        history.collection_started_at,
        history.collection_finished_at,
        parents=parents,
        commits=commits,
    )
    # A single-month collector cannot establish a repository's earliest history.
    if history_start is not None and not (
        run.collection_since is None
        and run.parent_discovery == run.commit_history == "full"
    ):
        history_start = None
    repository = RepositoryMetadata(
        history.repository_node_id,
        history.repository_full_name,
        history.repository_url,
        history.repository_created_at,
        history_start,
        run if history_start is not None else None,
    )
    return ReportMetadata(
        repository,
        month,
        first,
        after,
        Provenance(run, run, run),
        Coverage(run.started_at, history.coverage_limitations),
        first <= run.started_at < after,
        history.default_branch,
        history.default_branch_head,
    )


def retain_provenance(report, previous_repository, previous_report=None):
    from .contracts import choose_repository

    metadata = report.metadata
    repository = choose_repository([previous_repository, metadata.repository])
    provenance = metadata.provenance
    if previous_report is not None:
        provenance = replace(
            provenance, created_by=previous_report.metadata.provenance.created_by
        )
    return replace(
        report, metadata=replace(metadata, repository=repository, provenance=provenance)
    )

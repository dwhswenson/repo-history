# Repository history tracking

Collect GitHub repository activity with fast full-history backfills and bounded
updates, then build monthly reports and visualization manifests offline.
See [SPEC.md](SPEC.md) for the collection and report contracts.

Requires Python 3.12+. Packaging and Pixi configuration are maintained together in
`pyproject.toml`. Install with `pip install .`, or use the existing Pixi workspace:

```text
pixi run -e dev python-test
pixi run -e dev python-coverage
pixi run -e dev python-lint
pixi run -e dev python-format-check
pixi run -e dev python-build
```

CI runs on pushes and pull requests with separate Python (3.12–3.15) and
Node (22, 24, 26) jobs. Each uses a locked Pixi environment on Linux. Python
checks include lint, formatting, coverage, and source/wheel builds; the test suite
also installs the built wheel in isolation. Node checks include formatting, unit
tests, package builds, an npm package preview, and Chromium browser tests.

Run the same checks locally from the repository root:

```text
pixi run -e py312 python-ci
pixi run -e node22 node-install
pixi run -e node22 node-ci
pixi run -e node22 node-browser-install
pixi run -e node22 node-test-browser
```

Use `py313`, `py314`, `py315`, `node24`, or `node26` to test another runtime.
`node-browser-install` installs Chromium and its system dependencies on Linux.

## Writing reports and using the CLI

Set `GH_TOKEN` or `GITHUB_TOKEN` before collecting. After installation, use:

```text
repo-history backfill example/project --output-directory reports
repo-history backfill example/project --start 2024-01 --end 2024-12 --output-directory reports
repo-history update example/project --output-directory reports
repo-history update example/project --start 2024-02 --end 2024-06 --output-directory reports
repo-history summarize --input-directory reports
repo-history chop-partial reports
```

The output directory is required and is created if missing. All ranges include
both endpoints; omitted `--end` selects the current **UTC** month at invocation.
Future months are rejected. Backfill collects all history once,
then grouping it into monthly reports offline. Omit `--start` to begin at the
earlier of repository creation and the earliest observed activity, including
imported commits. Empty months are included. Explicit backfill ranges limit
output, not the history traversal.

Update collects the requested interval once using experimental
bounded parent discovery and buffered commit history. Candidate issues and PRs
are hydrated in batches of up to 20 with their first activity pages, reusing
backfill's embedded-page queries and collectors. Only overflowing activity
connections require follow-up requests; later review-thread pages also include
their first inline-comment pages. Without `--start`, it
selects the latest direct-child `YYYY-MM.json` report filename and **recollects
that month** through the end month. It replaces every selected report, leaves
earlier files untouched, and does not repair earlier gaps. An end before the
selected start is an argument error. Existing reports are validated before
filename inference or network requests; unrelated files and subdirectories are
ignored. Invalid reports fail rather than triggering a backfill.

With no explicit start and no monthly reports (including a missing directory),
update falls back to history-first backfill with its automatic start. This
fallback uses full history. An explicit start uses bounded range collection even
when the directory has no reports. Update always collects its requested interval
once, then groups activities into monthly files; it does not repeat collection
independently for each month. Backfill always collects full history once.

The CLI requires one of `backfill`, `update`, `summarize`, or `chop-partial`.
The former implicit single-month command and `manifest` command have been removed.
For one month, use update with equal `--start` and `--end` values. Summarization remains an
explicit offline step after collection.

`python -m repo_history` supports the same commands.
With Pixi, prefix commands with `pixi run`, for example
`pixi run repo-history --help`.

Each successful month prints its saved path to stdout immediately, except when
backfill uses live progress and stdout is a terminal. Progress and
API usage go to stderr. `--verbose` includes debug diagnostics with `issue=N` or
`pr=N` on relevant nested requests and retries. Paginated requests include a local
`page=N` counter; retries keep the same number. Embedded first pages log
`page=1 source=embedded` with item count and `has_next_page`; their continuation
requests begin at page 2. Counters restart for each connection, including each
thread's comments. `--quiet` suppresses
paths and all diagnostics except errors. Exit codes are 0 for success, 2 for
invalid arguments, and 1 for collection, validation, or filesystem failures.
Rate-limit errors include available retry/reset guidance and stop the run.

Backfill shows a live Rich display on interactive stderr: persistent Issues, PRs,
Commits, Grouping, Writing, Inventory, and Verification rows include processed/total items,
percentage, elapsed time, and approximate per-stage ETA. The current item and operation show
nested pagination and retries. Items advance only after their history has been processed; totals
are advisory and unavailable totals use an indeterminate display. Issue, PR, and
commit history queries always request totals on each page, without extra count
requests; progress uses only the first page's total. A footer updates measured
GraphQL points spent by this run, requests, retries, unmeasured attempts, and the
last observed remaining budget and reset time. Budget readings can become stale
between responses or when other clients use the same credentials; unknown values
are shown explicitly.

Redirected stderr and `--verbose` use plain progress messages instead of bars.
Stage transitions, retries, and completion/failure appear immediately; other
updates appear at most every 30 seconds when work advances. `backfill --no-progress`
disables the added progress output, retaining ordinary logs. Total-count fields
remain in the queries even when progress is disabled.
`--quiet` suppresses progress as well as saved paths. Grouping advances after each
monthly report has been built and validated; writing advances after each successful
file save and shows the full path of the current monthly JSON file. Writing completes
after the last monthly file is saved. Inventory has its own progress bar, advancing
after each report is read, validated, and hashed. Collection-state writing and
publishing use a separate status line with a spinner. Verification
has its own progress bar checking each report against the temporary state file,
with the current operation, report number, and full path shown below it.
The live display stays active through collection-state processing
and replaces the scrolling saved-path list when stdout is a terminal.
Redirected stdout still receives each saved path immediately, including with live
progress on stderr; plain/verbose mode and `--no-progress` retain path output.
These collection stages are specific to the backfill CLI; Python calls and
`update` (including its full-history fallback) keep their existing output behavior.

Python callers can collect without writing, write an existing report, or backfill:

```python
from repo_history import backfill, collect_history, history_to_months, write_report

history = collect_history("example", "project")
report = history_to_months(history, "2024-02", "2024-02")[0]
path = write_report(report, "reports")  # pathlib.Path: reports/2024-02.json
paths = backfill(
    "example",
    "project",
    "2024-01",
    "2024-12",
    output_directory="reports",
    # One complete history traversal, then offline grouping.
)
```

`backfill("example", "project", output_directory="reports")` uses the same
automatic endpoints. `backfill` also accepts `token=None` and returns a
chronological list of saved paths on success. Backfill finishes collection
and validates all selected reports before writing: collection or grouping failure
leaves existing files untouched. Writing failures preserve already saved months.
There is no automatic skipping, rollback, or resume.
Reruns replace every requested month, including during backfill, without changing
other months.

An output directory holds monthly reports for **one repository node ID**.
Before writing, every direct child named `YYYY-MM.json` must be a valid report,
match its filename's month, and share that repository identity. Repository renames
are allowed. Damaged files are rejected even when being replaced; move or repair
them before retrying. Unrelated filenames and nested directories are ignored.
Backfill and update check existing files before making network requests; repository
identity is compared immediately after repository lookup in history and range
collection, before traversing parents or commits. Backfill and update validate the destination before collection and retain its
history evidence while replacing reports. State publication subsequently verifies
the saved inventory; standalone writes validate their destination too.

Writing validates the report, writes and validates a temporary UTF-8 file in the
destination directory, and atomically replaces the target. Failure before
replacement preserves the old file and cleans up the temporary file. Invalid
incoming reports raise `ReportValidationError`; invalid or conflicting existing
files raise `ReportWriteError` with the filename; filesystem errors propagate.
Writers must run sequentially or use external coordination. Atomic replacement
does not provide power-loss durability or a transactional multi-month backfill.

Test suites mirror source module names with a `test_` prefix: for example,
`tests/test_queries.py` tests
`src/repo_history/queries.py`. Shared fixtures and response builders live
in `tests/conftest.py` and `tests/helpers.py`; `tests/test_packaging.py` covers
distribution integration. Pytest uses standard test filename discovery under
`tests`, with importlib mode.

## Collect once, group offline

```python
from repo_history import collect_history, history_to_months, write_report

history = collect_history("example", "project")
reports = history_to_months(history)  # Includes imported history and empty months.
february = history_to_months(history, "2024-02", "2024-02")[0]
for report in reports:
    write_report(report, "reports")
```

`collect_history(owner, repo, *, token=None)` returns a frozen `RepositoryHistory`
snapshot with repository identity, `repository_created_at`, captured default
branch/head, collection timestamps, coverage limitations, actors, and activities.
It uses one client and one repository lookup, fully traverses accessible issue/PR
connections and their nested history, and walks commits reachable from the
captured default head **without date filters**. Parent queries include the first
pages of lifecycle events and conversation comments, plus reviews and review
threads for PRs. Threads include their first comment page. Each nested connection
has its own `pageInfo`; additional requests start at its returned cursor only
when `hasNextPage` is true, including threads found on later thread pages.

History queries request 20 parents per page, 20 items per activity connection,
and 10 review threads per parent, bounding nested response sizes. Lean activity
continuation queries request up to 100 items; thread continuation queries request
10 threads with embedded comment pages. Existing resource-limit retries can
reduce the outer page size. Update uses the same embedded-page traversal.

Collection writes no files and raises without returning a partial snapshot if
collection fails.

`history_to_months(history, start=None, end=None)` makes no network requests and
returns validated `MonthlyReport` objects in chronological order. It groups by the
existing activity reporting timestamps in UTC and includes only actors referenced
by each month's activities, including subject authors and committers. Every report
shares the snapshot's collection interval and head, records both strategies as
`full`, and omits bounded-collection warnings. Its default end and future-month
validation use the snapshot's collection-start month, even if grouping happens
later. Future-dated source activities remain in the snapshot but are excluded
from supported output months until a later collection.

History collection holds the complete normalized history in memory; there is no
disk cache or interruption resume. Use backfill for initial builds and update for
recent intervals.
Collection logs include elapsed time, request attempts, retries, measured REST and
GraphQL points, and unmeasured attempts. Backfill also logs grouping/writing time.
See [PERFORMANCE.md](PERFORMANCE.md) for deterministic measurements and limitations.

## Collection strategies

Backfill and update share one collector and activity normalization implementation.
Backfill scans full history; update collects one requested interval. Both include
`issue_opened`, `issue_closed`, `issue_reopened`, `pr_opened`, `pr_merged`,
`issue_comment`, `pr_conversation_comment`, `pr_review`,
`pr_inline_review_comment`, and `commit` activities. Coverage limitations are
recorded in `metadata.coverage.limitations`.

Months use exact UTC half-open boundaries. Future months and invalid CLI inputs
are rejected before network requests. The ongoing flag uses collection start,
even if the run finishes in a later month. Repository identity and the
default-branch head are captured once, including a null head for an empty
repository. The collector owns and closes its client, including on failure.
Issue activity is skipped and recorded as a limitation when issues are disabled;
PR collection still runs.

The former independent `collect_month()` API and selectable discovery modes
have been removed. For a single month, use:

```text
repo-history update example/project --start 2024-02 --end 2024-02 --output-directory reports
```

Python callers can collect full history with `collect_history()` and select
months offline with `history_to_months()`. The report, serialization, validation,
writing, and manifest APIs remain available.
Existing reports with `collect_month` provenance can still be read and summarized.

### Buffered commit collection in update

Update collects commits from the captured default-branch SHA with GraphQL history
bounded by 24 hours before interval start and 24 hours after interval end. It
traverses every returned history page and filters committed dates locally to the
exact interval before grouping into months. Backfill walks that captured head's
history without date filters.

Authored date and committer metadata are preserved. Merge commits count, and each
distinct author/co-author receives credit without increasing the repository
commit count. Committers receive no separate credit. Headlines are included;
full messages are omitted. GitHub-linked accounts use node IDs; unlinked Git
identities use estimated email/name keys without inferred account matches or
bot status. Commit-linked accounts reuse canonical issue/PR identities or are
resolved by node ID once per run: GitHub can project a bot as a User inside
commit authorship.

The buffer accommodates small clock errors but cannot guarantee completeness:
GitHub's lower date bound may prune traversal before reaching an in-period
ancestor when intermediate timestamps are unusually old. Counts support trend
reporting and annual summaries rather than exhaustive historical audits.

Reports record the actual strategy in
`metadata.provenance.last_updated_by.commit_history`. If buffered dates overflow
or exceed the supported filter range (1970-01-01 through 2099-12-13), update
selects unbounded history (`full`) before querying and applies the exact local
interval filter. Empty repositories produce zero commits without a history request.

### Experimental bounded parent discovery in update

Update uses the REST repository issues endpoint to discover issues and PRs in
all states, using the resolved current repository name. It requests updates since
one second before interval start, follows every next-page link, deduplicates item
numbers, and retains parents with `updated_at >= start` and `created_at < end`.
Updates after interval end remain eligible.

GraphQL hydrates up to 20 parents per batch with their first activity pages: up
to 20 lifecycle events, conversation comments, and reviews, plus 10 review threads
with up to 20 inline comments each. Complete connections need no additional
request. Overflow pages retain independent cursors; ordinary activity and inline
comment continuations request up to 100 items, while thread pages request up to
10 and keep embedding inline-comment pages. Candidate histories are exhausted;
activity date filtering remains local. Missing or malformed embedded pages fail
the run. REST PR issue IDs are never used as GraphQL PullRequest IDs. REST uses
API version `2026-03-10`.

**Bounded discovery is experimental.** It has not been established that every
counted activity advances its parent's REST update timestamp. A stale timestamp
can therefore omit activity. Full-history backfill remains available when a full
traversal is needed. Only offline fixtures have been verified, including the
disabled-issues case; live REST behavior when issues are disabled remains
unverified. Update still requests REST parents and collects PRs when issues are
disabled. Endpoint errors fail the run rather than yielding an empty report.
Fine-grained credentials need the REST endpoint's **Issues: read** permission for
private repositories in addition to GraphQL permissions. See
[repository issues endpoint permissions](https://docs.github.com/en/rest/issues/issues#list-repository-issues).

Bounded discovery validates retrieved discovery records and retained parents'
histories; it cannot detect malformed history on excluded parents. Failures never
switch strategies or return partial snapshots. For January of year 1, update
selects full parent traversal before discovery to avoid overlap underflow.
Reports and INFO logs record the actual parent and commit strategies separately.
Bounded reports include explicit completeness warnings. See
[PERFORMANCE.md](PERFORMANCE.md) for measurements and remaining verification work.

### Activity normalization

Openings credit the subject author. Closures, reopenings, and merges credit the
event performer, retaining the subject author only as context. Every distinct
closure/reopening counts separately; closure reasons come from the individual
event, never the issue's current reason. Missing performers and reasons remain
null. Reopening activities have null event URLs because their source event does
not expose a URL. A merge's commit SHA is context and does not count as commit
activity. Draft PR openings and published activity on draft PRs are included.

Conversation comments use creation time. Submitted reviews use submission time,
including reviews currently dismissed; pending reviews are excluded. A review
counts once even when it has no body, and its body never counts as another comment.
Inline comments and replies are collected through every review thread, including
resolved and outdated threads. Each published comment counts separately, using
its publication timestamp, even if created in an earlier month. Thread flags and
review states reflect collection-time metadata. GitHub may report publication
slightly before comment creation; both source timestamps are preserved, and
publication time still determines the reporting month.

Inline comments with `PENDING` state are excluded. A publication timestamp confirms
publication; without one, `SUBMITTED` state confirms publication and creation time
is used with an explicit timestamp-fallback flag. An unknown state with no
publication timestamp fails collection. Contradictory pending/publication evidence
also fails. Non-pending reviews without submission timestamps fail. Review IDs
may be null; thread IDs and parent subject context are retained. These independent
nested requests can be expensive on repositories with long review histories.

Actor identities use GitHub node IDs, with `User` classified as human and `Bot`
as bot. Unsupported actor types fail explicitly. Only actors referenced by
retained activities enter the registry; repeated observations refresh available
display metadata. Identical duplicate activities count once; conflicting records
sharing an event ID fail. Malformed source objects, null parent/event nodes,
pagination failures, and API failures raise without returning a partial report.
Report contract failures raise `ReportValidationError`.

The offline acceptance history is in `tests/test_collection.py`, particularly
`test_multi_month_history` and `test_comments_reviews_multi_month_history`.
They check explicit event IDs, attribution, counts, independent cursors for all
connections, reruns, and collection of a different month from the same source
history. Run it with:

```text
pixi run -e dev python-test tests/test_collection.py
```

## GitHub GraphQL foundation

Set `GH_TOKEN` (or `GITHUB_TOKEN` as a fallback), or pass an explicit token.
Blank environment values are ignored; an explicit blank token is an error.
The client supports repositories on GitHub.com accessible to that credential.

```python
from repo_history import GraphQLClient, lookup_repository

with GraphQLClient() as client:
    repository = lookup_repository(client, "example", "project")
    print(repository.repository_node_id, repository.default_branch_head)
```

The immutable `RepositorySnapshot` contains `repository_node_id`, current
`repository_full_name` and `repository_url`, `database_id`, `issues_enabled`, `default_branch`,
and `default_branch_head`. Empty repositories have null branch/head. This lookup
captures the head for later history traversal; it does not collect activities.
The client owns its session: use a context manager or call `close()`. It is intended
for sequential queries and must not be shared between concurrent workers.

`execute(document, variables, *, operation_name)` returns the checked GraphQL
`data` dictionary. Supply named **query** operations, not mutations. Values remain
in the variables payload. HTTP errors, malformed responses, and all nonempty
GraphQL error lists prevent success, including responses carrying partial data.

`iter_connection` traverses one forward `nodes` connection:

```python
query = """
query RepositoryIssues($owner: String!, $name: String!, $first: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    issues(first: $first, after: $after) {
      nodes { id }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""
with GraphQLClient() as client:
    nodes = list(
        client.iter_connection(
            query,
            {"owner": "example", "name": "project"},
            operation_name="RepositoryIssues",
            connection_path=("repository", "issues"),
            page_size=100,
        )
    )
```

Each iterator owns its cursor and effective page size, including nested traversals.
`cursor_variable` and `page_size_variable` default to `after` and `first`; provided
variables can contain an initial cursor. Null nodes are preserved. Every page is
validated before yielding, but a later page can still fail: discard the entire
incomplete collection, including previously yielded nodes. Pagination performs
no date filtering or activity normalization.

Defaults are four attempts per operation/page, 1/2/4-second retry backoff, and
10-second connect/60-second read timeouts. Override `max_attempts` and `timeout`
on the client. Timeouts are Requests connect/read limits, not a total run deadline.
Connection failures and transient server errors are retried. Server query timeouts
and recognized resource errors halve paginated page sizes at the same cursor,
retaining the reduced size after success. Reductions share the attempt budget;
an unrecoverable size-1 failure stops immediately.

Rate limits always raise `RateLimitError` immediately without waiting or retrying.
Its `retry_after` (seconds) and `reset_at` (aware UTC datetime) are nullable when
GitHub provides no usable guidance. Callers must honor both before arranging a
later run. A successful response with zero remaining budget is returned, but the
client refuses further requests against that API budget until its known reset;
without one, use a fresh client for a later run. REST and GraphQL primary budgets
are tracked separately. Secondary throttling blocks both APIs until all available
reset/retry guidance has elapsed; without either, use a fresh client for a later
run. Rate-limit errors never trigger an automatic strategy fallback.

All API failures inherit `GitHubAPIError`: `AuthenticationError`, `TransportError`,
`GraphQLError`, `RateLimitError`, `PaginationError`, `RepositoryUnavailableError`,
and `QueryResourceError` (also a `GraphQLError`). Exceptions expose applicable
`operation`, `status`, `request_id`, `attempts`, `retry_after`, and `reset_at` fields.
Unavailable repositories are not distinguished as missing versus inaccessible.
Each collection logs a final INFO usage summary on success or failure,
with separate REST and GraphQL primary-budget point totals. REST counts successful
HTTP 200 requests (one primary-budget request each); GraphQL sums the reported
`rateLimit.cost` from every response, including retries and hydration. Attempts
without a measurable cost (such as network failures or GraphQL errors without
cost data) are reported as `unmeasured_attempts`; when nonzero, the point total is
incomplete. Account-wide cumulative usage and secondary-limit points are not
included. Usage is logged only and does not change the report schema.

Standard Python logging records retry/page-reduction information at INFO and
response status/request-ID/budget metadata at DEBUG. The package configures no
handlers and excludes credentials, query variables, and response bodies.

## Python API

```python
from datetime import datetime, timezone
from uuid import uuid4
from repo_history import (
    CollectionRun, Coverage, Provenance, RepositoryMetadata, ReportMetadata,
    build_report, report_to_json,
)

now = datetime(2024, 3, 2, tzinfo=timezone.utc)
run = CollectionRun(
    run_id=str(uuid4()), command="manual", mode="rebuild",
    collector_version="0.1.0", started_at=now, finished_at=now,
)
metadata = ReportMetadata(
    repository=RepositoryMetadata(
        node_id="R_example", full_name="example/project",
        url="https://github.com/example/project",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    ),
    month="2024-02",
    month_start=datetime(2024, 2, 1, tzinfo=timezone.utc),
    month_end=datetime(2024, 3, 1, tzinfo=timezone.utc),
    provenance=Provenance(run, run, run), coverage=Coverage(now),
    ongoing=False, default_branch=None, default_branch_head=None,
)
report = build_report(metadata, actors=[], activities=[])
text = report_to_json(report)
```

The package exports all report dataclasses, `build_report`, `validate_report`,
`report_to_json`, `report_from_json`, `load_schema`, `derive_summaries`,
`git_identity_key`, and `unresolved_git_actor`. Contract violations raise
`ReportValidationError` with a field/record `path` and explanatory message.
Dataclasses are frozen records; construction alone does not validate them.
Build/validate/serialization operations enforce the contract. Nested summary
mappings are ordinary dictionaries; revalidate a report after modifying them.

## JSON contract version 1

The authoritative Draft 2020-12 schema is packaged at
`repo_history/schemas/monthly-report-v1.json` and available via
`load_schema()`. JSON has five required sections: `schema_version` (`"1"`),
`metadata`, `actors`, `activities`, and `summaries`. Unknown structural fields,
activity types, roles, and versions are rejected. GitHub reason/state strings and
nulls remain representable. Schema versioning is independent of package versioning.

Metadata nests repository identity and creation time in `repository`, collection
provenance in `provenance`, and observation time and limitations in `coverage`.
It also records UTC month boundaries, `ongoing`, and captured default branch/head.
`ongoing` reflects `coverage.as_of < month_end`, not the original report's creation
time or the reader's clock. Current collectors conservatively use collection start
as the observation time. Null branch/head denotes an empty repository. Event
occurrence must lie in `[month_start, month_end)`; source timestamps can be
future-dated. Aware datetimes normalize to UTC `Z`; naive datetimes are invalid.

`repository.history_start_month` and `history_start_established_by` are either both
null (unknown) or both present. Full-history collection establishes the earlier of
repository creation and earliest observed activity, including imported commits.
Backfill retains this evidence even when an explicit output range omits early
months. Bounded collection cannot establish the boundary; updates preserve prior
evidence and never move the known boundary later. Explicit reports preceding a
known boundary must be zero activity.

The three provenance descriptors are `created_by`, `last_updated_by`, and
`last_rebuilt_by`. Replacements retain the original creator. Current updates
reconstruct their selected months and advance both latest descriptors; a future
incremental update can retain the rebuild descriptor while advancing the latest
update. There is no growing update-history array; version control can retain it.
A successful refresh with no activity still updates provenance and coverage.

Every run records a UUID, entry point (`command`), `mode` (`rebuild` or
`incremental`), collector version, collection timestamps, nullable historical
collection bounds, nullable requested output month bounds, and actual
`parent_discovery`/`commit_history` strategies. Null bounds mean unbounded; null
strategies mean unknown. Backfill always traverses full history, so requested
output start and collection start are independent. A run ID always identifies
one immutable descriptor. Coverage limitations describe the resulting data;
incremental refreshes must not erase unresolved limitations.

The shared definitions live in packaged `schemas/common-v1.json`; Python and
browser validation resolve them locally without fetching schemas. These
unreleased v1 contracts were revised in place. Regenerate older reports and
manifests: obsolete layouts are rejected rather than migrated.

Each activity has `id`, `type`, `occurred_at`, nullable `url`, a `subject`,
`credits`, and type-specific `metadata`. Types are `commit`, `pr_opened`,
`pr_merged`, `issue_opened`, `issue_closed`, `issue_reopened`, `issue_comment`,
`pr_conversation_comment`, `pr_inline_review_comment`, and `pr_review`.
Node-backed IDs are `<type>:<source_node_id>`; commit IDs are
`commit:<repository_node_id>:<sha>`. Commits use committed time as occurrence and
retain authored time, parents, and committer metadata. Their title is the first
line of the commit message. Subjects retain current titles, URLs, node IDs,
issue/PR numbers, and author references. Bodies and full messages are excluded.

Credit entries contain nullable `actor_key` and `role`. Commits accept `author`
and `co_author`; merges, closures, and reopenings use `merger`, `closer`, and
`reopener`; other events use `author`. Missing performers remain null rather
than becoming a shared actor. The builder deduplicates actor/role pairs and sorts
records; deserialization rejects duplicates and inconsistent supplied summaries.
Serialization sorts actors by key, activities by occurrence/ID, credits by
actor/role, and object keys, using two-space indentation and a final newline.

Submitted reviews retain fetched state (including dismissal); pending reviews
are invalid. Inline comments record creation/publication time, review/thread
references, resolved/outdated flags, publication confirmation, and fallback flag.
Without publication time they must confirm publication and explicitly use
creation time as fallback. Closure reasons are nullable source values, never the
issue's current reason. Every closure/reopening has a separate event ID.

## Identity and counting

Linked humans and explicitly identified bots use GitHub node IDs as keys; logins
are mutable metadata. Supply bot classification only with explicit source
evidence: names do not establish bot status. Unresolved Git identities use
`git:email:<sha256>` of trimmed casefolded email, falling back to
`git:name:<sha256>` of trimmed case-preserved name. Original names/emails are
retained. No usable email/name yields null. These estimates may merge distinct
people or split one person's participation; no GitHub matches are inferred.

Repository counts count events once. Per-actor counts count each distinct
credited actor once per event, even with multiple roles. Committers and subject
authors are context unless independently credited. Counts contain all ten types,
including zeros. `distinct_actors` and `distinct_actors_by_type` count only credited
actors, divided into `total`, `human`, `bot`, and `unresolved_git`.
`actor_counts` contains only credited actors. `unattributed_total` and
`unattributed_by_type` count events with no known credited actor; a known author
plus a null co-author remains attributed. Actors referenced only as context may
appear in the registry but never inflate participation counts. No cumulative
counts are stored.

## Reconstruction limitations

Deleted comments, inaccessible items, removed commits, and transferred issues
can limit reconstruction. Repository/actor node IDs survive renames, but historical
display names are not reconstructed. Titles, resolved/outdated thread flags, and
review states reflect collection-time metadata. GitHub may report publication
slightly before comment creation; both source timestamps are preserved, and
publication time still determines the reporting month. Captured-head history does not
establish when a commit reached the branch. Force-pushes, rebases, changed default
branches, and newly available data can revise rerun results. Collection is not a
transactional snapshot; concurrent changes may appear on a later rerun.
Disabled issues do not prevent later PR collection. Record applicable limitations
in `metadata.coverage.limitations`.

## License

MIT. See [LICENSE](LICENSE).

## Visualization manifests

After collecting monthly reports, generate a self-contained summary manifest offline:

```sh
repo-history summarize --input-directory reports
repo-history summarize --input-directory reports --output public/reports/manifest.json
```

On interactive stderr, `summarize` shows a Rich progress bar for reading and
validating the monthly reports, with status text while saving the manifest. The display
includes report totals, elapsed time, approximate ETA, and the current filename.
The output manifest path is printed to stdout after the display finishes.
Redirected stderr and `--verbose` use throttled plain progress messages;
`--no-progress` disables added progress, and `--quiet` suppresses progress and the
saved path while retaining errors. Python manifest APIs remain silent by default.

Publish the resulting `manifest.json` and the `repo-history-viz` bundle. The browser loads only the manifest. It contains a display name, repository registry, monthly aggregate and source summaries, stable actor keys with classifications, and coverage metadata; full event records and actor profile fields remain in the monthly files. The generator validates every source report and atomically replaces the manifest. Rebuild explicitly after adding, replacing, or deleting reports; backfill and update remain independent. Do not modify source reports concurrently with generation.

The Python APIs are `build_manifest(reports, *, display_name=None) -> dict` and `write_manifest(report_directory, *, output_path=None, display_name=None) -> Path`. The packaged contract is `schemas/viz-manifest-v1.json`, independently versioned from monthly reports. The generator rejects empty directories, wrong month filenames, mixed repositories, and conflicting classifications for participating actors. Only immediate monthly report files are read; unrelated files and subdirectories are ignored.

See [the visualizer documentation](repo-history-viz/README.md) for the `loadManifestSource()` API and embedding examples.

### Removing a trailing partial month

Use `repo-history chop-partial reports` or
`repo-history chop-partial reports/manifest.json` to edit an existing manifest
in place, offline and without GitHub credentials. The command accepts exactly one
input: either a manifest file or a directory containing `manifest.json`.
It removes the chronologically latest month when that month has
`metadata.ongoing: true`, even for an older snapshot. Actors used only in the
removed month are pruned. Monthly report files and `collection-state.json` remain
untouched. Running `summarize` again rebuilds from all reports and may restore
the removed month.

To save to a separate file, use `--output` or `-o`:

```sh
repo-history chop-partial reports -o public/manifest.json
```

The input is preserved when the output is a different path. The output's parent
directories are created as needed. If the input is already complete, its validated
contents are still saved to the explicit output. The output must be a file path
and cannot use a monthly report filename (`YYYY-MM.json`). Omitting it
keeps the default in-place behavior.

All months before the latest must be complete; any earlier ongoing month is an
error, even when the latest month is complete. Removal also errors if it would
leave no months. If all months are complete, an in-place run succeeds silently
without rewriting the manifest.

The input and edited result are validated before writing. The manifest is saved
atomically, printing its destination path after saving.
Use sequential writers or external coordination. `--quiet` suppresses saved paths
and retains errors; `--verbose` adds diagnostics. Exit codes follow the other CLI
commands: 0 for success, 2 for argument errors, and 1 for validation or filesystem
failures. Repeated in-place invocations succeed silently after the partial month
is removed.

CLI commands can reuse `repo_history.manifest.resolve_manifest_path(path)` to
resolve a file or directory to a manifest path. It accepts strings and path-like
objects, preserves relative paths, and leaves existence and content validation
to the reader. Commands accepting multiple inputs can apply it to each input.

### Combining manifests

Combine at least two manifests into one dataset, offline and without GitHub
credentials:

```sh
repo-history combine-manifests reports/repo-a reports/repo-b/manifest.json \
  --output public/manifest.json --display-name "Project activity"
```

Each positional input accepts a filename or directory containing `manifest.json`,
just like `chop-partial`. Inputs may already contain multiple repositories, but
their repository sets must be disjoint. A repeated repository node ID is an error,
even across different month ranges, renamed repositories, or identical files.
Conflicting classifications for a shared actor key are also errors, including
actors appearing only in excluded months.

Both `--output` (or `-o`) and a nonblank `--display-name` are required. The output
must be a separate file, cannot alias an input through a symlink or hard link,
and cannot use a monthly report filename (`YYYY-MM.json`). Parent directories
are created as needed. An existing separate output is replaced atomically only
after all inputs and the assembled manifest validate. Inputs, monthly reports,
and collection-state files are left unchanged. Keep inputs stable while combining.

The candidate range spans the earliest through latest input month. By default,
an entire calendar month is excluded if any source marks it ongoing, with a
warning identifying the month and responsible repositories. This uses the stored
coverage flag, regardless of today's date. Add `--include-partial` to retain those
months and their source-specific coverage warnings. At least one month must
remain, and retained months must be contiguous; excluding an internal partial
month is an error.

Every retained month must have a source for every included repository unless
the repository's history evidence proves that month predates its history start.
Unknown boundaries cannot justify implicit zeros. Unequal ranges and missing
reports fail instead of automatically trimming the range or filling gaps.
Collected zero-activity months remain present.

Event counts and per-actor counts are summed across sources; distinct contributor
populations are recomputed from actor-key unions. The viewer unions the same keys
across months for cumulative contributors. Linked accounts deduplicate by stable
GitHub node ID; unresolved Git identities remain estimates and are not reconciled
with linked accounts. Shared commits count once per repository. Source summaries,
provenance, and coverage metadata are preserved, and actors used only in excluded
months are pruned. Reordering inputs produces identical output bytes.

On success the command prints the destination path to stdout. Warnings and
diagnostics go to stderr; `--quiet` suppresses saved paths and warnings while
retaining errors, and `--verbose` adds diagnostics. There is no progress display.
Exit codes are 0 for success, 2 for argument errors, and 1 for validation or
filesystem failures. The resulting manifest loads directly in `repo-history-viz`.

## Repository collection state

Successful `backfill` and `update` operations also write `collection-state.json`
in their output directory. This is one current state file per repository dataset,
replaced after successful report writes; it is neither one file per month nor an
archive of runs. It is not needed by the visualizer and need not be published.

The packaged `schemas/collection-state-v1.json` contract has `schema_version`,
`repository`, `last_applied_run`, `reports`, and `checkpoint`. `reports` maps each
stored month to the SHA-256 of its exact file bytes. A populated checkpoint records
its full-history `baseline_run`, materialized month range, issue and PR
`checked_through`/`last_full_scan_at`, and commit observation time and branch/head.
These are observation facts under the recorded limitations, not a guarantee that
an exact timestamp filter discovers every later change. Pagination cursors are
not durable checkpoints.

A resumable baseline is emitted only when a full-history backfill saves every
month from the established history start through its current UTC month, refreshes
every inventoried report from that run, and leaves no observed future-month
activity unmaterialized. Partial ranges, historical end dates, mixed refreshes,
and today's bounded updates write an accurate inventory with `checkpoint: null`.
Standalone report writes invalidate an existing checkpoint. Creation provenance
and repository history evidence survive those replacements.

If a run fails after writing some reports, state does not advance. The previous
state may remain, but its inventory no longer matches and must not be used to
resume. Report and state publication are individually atomic, not a multi-file
transaction; writers must remain sequential. `validate_collection_state(data)`
and `load_collection_state_schema()` are exported; disk inventory verification is
available as `repo_history.state.verify_collection_state(data, directory)`.
No incremental collection or automatic resume is implemented yet.

## Prepared multi-repository manifests

The visualization contract now supports `display_name`, `repositories`, `actors`,
and `months`. `summarize --display-name "Project activity"` sets the title; its
default is the latest `owner/repo` name. Summarize still rejects mixed repositories;
use `combine-manifests` to combine existing manifests from disjoint repositories.

Each month has calendar `metadata`, chart-facing `summaries`, and `source_reports`.
Each source retains the revised monthly metadata and its own summaries, including
repository identity, history evidence, provenance, and coverage. Events and actor
profiles are omitted. Single-repository chart summaries equal the source summary;
prepared multi-repository summaries sum events and per-actor counts, then union
identities for distinct contributor populations. Shared commits count once per
repository; equivalent changes and fork ancestry are not deduplicated.

Both validators check source/aggregate consistency. Multi-repository months must
be contiguous, have at most one source per repository, and include every
repository unless that month precedes its known history start. Unknown boundaries
cannot justify implicit zeros. Single-repository manifests may retain gaps.
Ongoing sources remain representable and visible. `combine-manifests` excludes an
entire calendar month by default when any source is ongoing, warning instead of
silently undercounting; `--include-partial` retains those months explicitly.

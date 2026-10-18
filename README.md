# Repository history tracking

Phases 1–6 provide a monthly report contract, a synchronous GitHub GraphQL
foundation, monthly activity collection, atomic report writing, a CLI, and backfill.
See [SPEC.md](SPEC.md) for the implementation phases.

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
```

The output directory is required and is created if missing. All ranges include
both endpoints; omitted `--end` selects the current **UTC** month at invocation.
Future months are rejected. Backfill collects all history once,
then grouping it into monthly reports offline. Omit `--start` to begin at the
earlier of repository creation and the earliest observed activity, including
imported commits. Empty months are included. Explicit backfill ranges limit
output, not the history traversal.

Update collects the requested interval once using experimental
bounded parent discovery and buffered commit history. Without `--start`, it
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

The CLI requires one of `backfill`, `update`, or `summarize`. The former implicit
single-month command and `manifest` command have been removed. For one month,
use update with equal `--start` and `--end` values. Summarization remains an
explicit offline step after collection.

`python -m repo_history` supports the same commands.
With Pixi, prefix commands with `pixi run`, for example
`pixi run repo-history --help`.

Each successful month prints its saved path to stdout immediately. Progress and
API usage go to stderr. `--verbose` includes debug diagnostics with `issue=N` or
`pr=N` on relevant nested requests and retries. Paginated requests include a local
`page=N` counter; retries keep the same number. Embedded first pages log
`page=1 source=embedded` with item count and `has_next_page`; their continuation
requests begin at page 2. Counters restart for each connection, including each
thread's comments. `--quiet` suppresses
paths and all diagnostics except errors. Exit codes are 0 for success, 2 for
invalid arguments, and 1 for collection, validation, or filesystem failures.
Rate-limit errors include available retry/reset guidance and stop the run.

Python callers can collect without writing, write an existing report, or backfill:

```python
from repo_history import backfill, collect_month, write_report

report = collect_month("example", "project", 2024, 2)
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
collection, before traversing parents or commits. Backfill and updates scan
destination reports once; standalone writes still scan on every call.

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
reduce the outer page size. Standalone single-month Python collection retains its independent queries.

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

History collection retains the same accessible-source and default-branch coverage
limits as monthly collection. It holds the complete normalized history in memory;
there is no disk cache or interruption resume. History mode benefits multi-month
initial builds; independent monthly collection may be cheaper for a narrow range.
Collection logs include elapsed time, request attempts, retries, measured REST and
GraphQL points, and unmeasured attempts. Backfill also logs grouping/writing time.
See [PERFORMANCE.md](PERFORMANCE.md) for deterministic measurements and limitations.

## Monthly activity collection

```python
from repo_history import collect_month, report_to_json, validate_report

# Uses GH_TOKEN, falling back to GITHUB_TOKEN; token="..." is also supported.
report = collect_month("example", "project", 2024, 2)
validate_report(report)
print(report.summaries.repository_counts)
for activity in report.activities:
    print(activity.id, activity.occurred_at, activity.credits)
text = report_to_json(report)
```

`collect_month(owner, repo, year, month, *, token=None, discovery="bounded")` returns
a validated `MonthlyReport` containing `issue_opened`, `issue_closed`, `issue_reopened`,
`pr_opened`, `pr_merged`, `issue_comment`, `pr_conversation_comment`,
`pr_review`, `pr_inline_review_comment`, and `commit` activities.
Coverage limitations are recorded in `metadata.coverage_limitations`. No files
are written.

Months use exact UTC half-open boundaries. Future months and invalid inputs are
rejected before network requests. The ongoing flag uses collection start, even
if the run finishes in a later month. Repository identity and the default-branch
head are captured once before traversal, including a null head for an empty
repository. The function owns and closes its client, including on failure.

With `discovery="full"`, all issue/PR parent pages, lifecycle timelines,
conversation comments, reviews, review threads, and thread comments are traversed independently, without
creation-date or current-state filters. This finds events on older parents but
can be expensive for large repositories. Requests begin
with 100-item pages and reuse the client's page reductions and retry policy.
Issue collection is skipped and recorded as a limitation when issues are
disabled; PR collection still runs. The collector performs no persistent caching.

### Buffered commit collection

Both issue/PR discovery modes collect commits from the captured default-branch
SHA using GraphQL history bounded by 24 hours before month start and 24 hours
after the next month starts. Every returned history page is traversed, then
commits are filtered to the exact UTC month using committed date. Authored date
and committer metadata are preserved. Merge commits count, and each distinct
author/co-author receives credit without increasing the repository commit count.
Committers receive no separate credit. Headlines are included; full messages are
omitted. GitHub-linked accounts use node IDs; unlinked Git identities use
estimated email/name keys without inferred account matches or bot status.
Commit-linked accounts reuse canonical issue/PR identities or are resolved by
node ID once per run: GitHub can project a bot as a User inside commit authorship.

The buffer accommodates small clock errors but cannot guarantee completeness:
GitHub's lower date bound may prune traversal before reaching an in-month
ancestor when intermediate timestamps are unusually old. Full issue/PR discovery
still uses this buffered commit query. Counts support trend reporting and annual
summaries rather than exhaustive historical audits.

Reports record the actual strategy in `metadata.commit_history` (`bounded`).
If buffered dates overflow or exceed GraphQL's documented supported range
(1970-01-01 through 2099-12-13), the collector internally selects unbounded
history (`full`) and applies the same local month filter. When the optional
provenance field is absent, the strategy is unknown. Empty repositories produce zero commits without a history request.

### Experimental bounded parent discovery

```python
report = collect_month("example", "project", 2024, 2, discovery="bounded")
```

`discovery="bounded"` uses the REST repository issues endpoint to discover both
issues and PRs in all states, using the resolved current repository name. It
requests updates since one second before month start, follows every next-page
link, deduplicates item numbers, and retains parents with `updated_at >= start`
and `created_at < end`. Updates after month end remain eligible. Candidate
subjects and authors are hydrated through GraphQL in batches of up to 50 before
the same nested collectors retrieve their activities. REST PR issue IDs are
never used as GraphQL PullRequest IDs. REST uses API version `2026-03-10`.

**Bounded discovery is experimental.** It has not been established that every
counted activity advances its parent's REST update timestamp. A stale parent
update timestamp can therefore omit activity. Keep full traversal for reference
and audits; selecting bounded discovery does not establish report completeness.
Only offline fixtures have been verified, including the disabled-issues case;
live REST behavior when issues are disabled remains unverified. When issues are
disabled, bounded discovery still requests REST parents and collects PRs, while
skipping issue activity. Endpoint errors fail the run rather than yielding an
empty report. Fine-grained credentials need the REST endpoint's **Issues: read**
permission for private repositories in addition to the permissions required by
GraphQL collection. See [repository issues endpoint permissions](https://docs.github.com/en/rest/issues/issues#list-repository-issues).

Bounded mode validates the retrieved discovery records and retained parents'
histories; it cannot detect malformed unrelated history on excluded parents.
The activity rules are unchanged. Unknown discovery modes
raise `ValueError` before requests. A failure never switches strategies or
returns a partial report. For January of year 1 only, bounded mode selects full
traversal before discovery to avoid overlap underflow and logs that selection.
Reports record the actual selected strategy in `metadata.parent_discovery`
(`bounded` or `full`), independently of `metadata.commit_history`. The selected
strategy is also logged at INFO. Bounded reports include an explicit completeness
warning in `metadata.coverage_limitations`. An absent optional
`parent_discovery` field means the strategy is unknown (Python `None`)
and is omitted on serialization. The January year-1 fallback records `full`.
Bounded discovery is now the default by user preference; its completeness
assumptions remain unverified. Instrumentation, comparison tooling, and live
verification remain follow-on work described in `PERFORMANCE.md`.

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
Each `collect_month` run logs a final INFO usage summary on success or failure,
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
from repo_history import ReportMetadata, build_report, report_to_json

metadata = ReportMetadata(
    repository_node_id="R_example",
    repository_full_name="example/project",
    repository_url="https://github.com/example/project",
    month="2024-02",
    month_start=datetime(2024, 2, 1, tzinfo=timezone.utc),
    month_end=datetime(2024, 3, 1, tzinfo=timezone.utc),
    collection_started_at=datetime(2024, 3, 2, tzinfo=timezone.utc),
    collection_finished_at=datetime(2024, 3, 2, tzinfo=timezone.utc),
    ongoing=False,
    default_branch=None,
    default_branch_head=None,
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

Metadata records repository node ID/current name/URL, UTC month boundaries,
collection interval, ongoing flag, captured branch/head, and coverage limitations.
Optional `parent_discovery` and `commit_history` fields record the actual strategies
used for issue/PR parent discovery and commit traversal, respectively. Missing
strategy fields mean unknown, not a presumed default.
The ongoing flag and future-month rejection use collection start rather than the
reader's clock. Null branch/head denotes an empty repository. Event occurrence
must lie in `[month_start, month_end)`; contextual timestamps may be outside it.
Aware Python datetimes normalize to UTC `Z`; naive datetimes are invalid.

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
in `metadata.coverage_limitations`.

## License

MIT. See [LICENSE](LICENSE).

## Visualization manifests

After collecting monthly reports, generate a self-contained summary manifest offline:

```sh
repo-history summarize --input-directory reports
repo-history summarize --input-directory reports --output public/reports/manifest.json
```

Publish the resulting `manifest.json` and the `repo-history-viz` bundle. The browser loads only the manifest. It contains monthly summaries, stable actor keys with classifications, and coverage metadata; full event records and actor profile fields remain in the monthly files. The generator validates every source report and atomically replaces the manifest. Rebuild explicitly after adding, replacing, or deleting reports; backfill and update remain independent. Do not modify source reports concurrently with generation.

The Python APIs are `build_manifest(reports) -> dict` and `write_manifest(report_directory, *, output_path=None) -> Path`. The packaged contract is `schemas/viz-manifest-v1.json`, independently versioned from monthly reports. The generator rejects empty directories, wrong month filenames, mixed repositories, and conflicting classifications for participating actors. Only immediate monthly report files are read; unrelated files and subdirectories are ignored.

See [the visualizer documentation](repo-history-viz/README.md) for the `loadManifestSource()` API and embedding examples.

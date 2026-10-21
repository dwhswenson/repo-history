# Monthly GitHub Repository Activity

## Goal and interfaces

Build a Python library and CLI that collects repository history once for initial collection, groups activities into monthly reports offline, and writes `YYYY-MM.json`. Expose collect, update, and summarize CLI commands; use one embedded-page collector for both workflows.

Reports support casual activity reporting, year-over-year growth analysis, and annual reports to funders. Counts should be useful and consistent, but exhaustive historical reconstruction is not required. Document known coverage limitations and favor practical collection costs where the tradeoff is explicit. Exact month assignment, attribution rules, deduplication, and internally consistent summaries still apply to collected events.

```python
collect_history(owner, repo, *, token=None) -> RepositoryHistory
history_to_months(history, start=None, end=None) -> list[MonthlyReport]
collect(owner, repo, start=None, end=None, *, output_directory, token=None) -> list[Path]
```

```text
repo-history collect OWNER/REPO [--start YYYY-MM] [--end YYYY-MM] --output-directory PATH
repo-history update OWNER/REPO [--start YYYY-MM] [--end YYYY-MM] --output-directory PATH
repo-history summarize --input-directory PATH [--output PATH]
```

Collect always performs one complete history traversal followed by offline
monthly grouping. Optional start/end months are inclusive and limit output only.
Omitted start uses the earlier of repository creation and the earliest observed
activity, including imported commits. Omitted end uses the invocation's UTC month.
Include empty months.

Unrestricted update verifies the saved report inventory and full-history baseline
checkpoint. Missing, malformed, null, or mismatched checkpoint state triggers a
full-history rebuild through the current UTC month. Invalid monthly reports and
mixed repository identities still fail. With a valid checkpoint, discover parents
updated since the prior successful run with a one-second local overlap, collect
their complete histories, and replace their saved activities. Reconcile all saved
commits against an unfiltered scan from the captured default-branch head. Rewrite
affected historical reports and refresh the prior final month through the current
month, including empty months. Preserve unrelated parent history.

Either explicit month flag selects strict inclusive output limits. With end alone,
infer start from the latest direct-child monthly filename, ignoring unrelated
files/directories; without reports use full-history collection. Explicit updates
invalidate the unrestricted checkpoint. Reject future/reversed ranges before
requests. Neither command accepts strategy flags.

Summarize generates a self-contained history summary offline, with `summary.json`
as its default filename. Collection does not regenerate it. Use update with equal
endpoints for one month. The independent `collect_month` API is removed. Report parsing,
validation, writing, offline grouping, and summary generation remain available.

Update uses bounded parent discovery; collect uses full GraphQL parent discovery.
Both commands use unfiltered commit history.
Both workflows embed first activity pages and independently paginate overflow.

Each month is independently collectable and replaceable. Earlier reports are not required. **Monthly files contain no cumulative counts**; a later visualizer can compute these from stable actor identities, individual events, and per-actor monthly counts.

Use [ghcontribs](https://github.com/dwhswenson/ghcontribs) as an architectural reference, separating GraphQL transport, query fragments, collection, normalized models, and serialization. Use Python and the existing Pixi workspace.

## Activity and attribution semantics

| Activity | Reporting timestamp | Credited actor |
|---|---|---|
| Commit | `committedDate` | Author and co-authors |
| PR opened | `createdAt` | PR author |
| PR merged | Merge event timestamp | Actor performing the merge |
| Issue opened | `createdAt` | Issue author |
| Issue closed | Closure event timestamp | Actor performing the closure |
| Issue reopened | Reopening event timestamp | Actor performing the reopening |
| Issue comment | `createdAt` | Comment author |
| PR discussion comment | `createdAt` | Comment author |
| PR review comment | `publishedAt` | Comment author |
| PR review | `submittedAt` | Review author |

- Use UTC calendar months and half-open intervals: `[month start, next month start)`. Return a valid report even when the month has no activity. Mark the current month as partial; reject future months.
- Collect unique commits reachable from a captured default-branch head, including merge commits. Preserve authored date and committer metadata, but use committed date for monthly grouping. This does not establish when a commit reached the branch. [Commit reference](https://docs.github.com/en/graphql/reference/commits)
- Count every issue closure and reopening independently. Preserve each closure event's reason, including completed, not planned, duplicate, or unknown; never substitute the issue's current reason. [Issue reference](https://docs.github.com/en/graphql/reference/issues)
- Distinguish PR discussion comments from PR review comments (published inline comments and replies in code review threads). Include published replies on resolved and outdated threads. Count submitted PR reviews once, excluding pending reviews; a review body is not an additional comment. Preserve fetched review state as collection-time metadata. [PR reference](https://docs.github.com/en/graphql/reference/pulls)
- Credit merges and closures to their performers; retain the subject's author as context. Commit co-authors receive attribution without increasing the repository's commit count. Committers receive no separate contribution credit.

## Monthly JSON contract

Define a versioned Draft 2020-12 JSON Schema, distributed with the Python package. Validate every report before writing it.

Each file contains:

- **Metadata:** schema version, repository node ID, current name and URL, month boundaries, collection interval, partial-month flag, captured default-branch head, parent-discovery and commit-history strategies, and coverage limitations. Strategy fields are optional; absence means unknown. Collections record the actual selected strategies, including pre-traversal fallbacks, and bounded parent discovery includes an experimental completeness warning.
- **Actors:** stable identity keys, available GitHub IDs and logins, display metadata, and actor classification.
- **Activities:** stable event ID, type, occurrence timestamp, URL where available, subject reference, credited actors and roles, and activity-specific metadata.
- **Monthly summaries:** repository counts by activity type, per-actor counts by activity type, distinct actor counts by population, and unattributed-event counts.

Identity and counting rules:

- Use GitHub node IDs as identity keys; logins are mutable metadata.
- Separate linked human accounts, explicitly identified bots, and unlinked Git identities.
- For unlinked Git authors, derive a deterministic identity key from available email, falling back to name. Document that these identities are estimates; do not infer GitHub account matches.
- Preserve missing actors as null. Do not count all deleted or unknown users as one contributor.
- Deduplicate repeated actor roles within an event. A co-authored commit counts once for the repository and once for each distinct credited actor.
- Include subject titles and URLs, but omit comment bodies, review bodies, and full commit messages in v1.
- Preserve source status/reason strings and null values so new GitHub values remain representable.

The format must let a downstream consumer compute monthly and cumulative actor participation, including per-actor PR openings, by combining files without further GitHub requests.

Supplement schema validation with checks that actor references resolve, event IDs are unique, timestamps belong to the month, and summaries agree with activities.

## Collection behavior and edge cases

- An update with equal start/end months writes only that month. It may inspect older parent objects to find that month's activity; it must not generate earlier reports or require earlier outputs.
- Do not select parent issues or PRs solely by creation month or current state. An old, closed PR can receive a new comment or review; an old issue can close or reopen in the requested month.
- Collect uses full issue/PR parent traversal. Use repository connections directly, GraphQL variables, and reusable fragments. Independently paginate parents and nested timelines, comments, reviews, review threads, and commit authors. Detect missing or repeated cursors. [Pagination documentation](https://docs.github.com/en/graphql/guides/using-pagination-in-the-graphql-api)
- Update uses REST repository issues with all states and an overlapping update-time `since`, local `updated_at >= start` / `created_at < end` selection, and GraphQL hydration by repository identity and item number. Preserve all nested activity rules and the report schema. Completeness of REST parent update timestamps, including live discovery with issues disabled, remains unverified. The verification work in PERFORMANCE.md remains outstanding. Excluded parent histories are outside bounded mode's validation scope. Failures never silently switch strategies; January of year 1 selects full traversal before discovery because the overlap would underflow.
- Separate discovery observation cutoffs from activity occurrence dates and output month limits. Collect complete selected-parent histories; project explicit output limits offline.
- Capture the default-branch head before traversing commits. Use that SHA as the unfiltered history root in both commands.
- Every update fully paginates unfiltered commit history and all nested commit connections. Never stop traversal at an old timestamp. Reconcile removed commits as well as additions; default-branch changes and timestamp inversions require no date-based special cases.
- If observed activity predates the established history boundary, rebuild all parent history before writing, reusing the captured repository and collected commits to establish new full-history boundary evidence.
- Incremental reports retain creation and last-rebuild provenance. Newly created months receive a separate initial rebuild descriptor plus the incremental update descriptor. Full/bounded strategies and unbounded occurrence-time collection bounds reflect the actual traversal.
- Advance checkpoint observation times to the successful run's start; retain the baseline and full-parent-scan times. Publish exact report hashes and captured commit head only after all report writes succeed. Partial-write inventory mismatches trigger a rebuild next time.
- Read authentication from `GH_TOKEN`, falling back to `GITHUB_TOKEN`. Support GitHub.com repositories accessible to the credential.
- Use request timeouts, bounded retries, rate-limit handling, and smaller pages for oversized queries. HTTP or GraphQL errors that prevent completion of the selected collection strategy fail the run; accepted coverage limitations do not excuse request failures or incomplete pagination.
- Write to a temporary file, validate, then atomically replace the requested month. Leave months outside explicitly selected ranges untouched. Refuse an output directory containing reports for a different repository node ID.
- History-first collection uses one client and repository lookup, checks destination repository identity before traversal, then collects every accessible parent/nested connection and unfiltered default-head commit history once. Normalize and deduplicate stable activities using the existing attribution and publication rules. Capture repository creation time in the lookup.
- History-mode parent queries embed first lifecycle/comment pages and PR review/thread pages; threads embed first inline-comment pages. Use independent `pageInfo` and resume cursors for every overflowing connection, including comments on later thread pages. Bound nested fan-out with 20 parents, 20 activity items, and 10 threads per page; use lean continuation queries except for thread pages, which retain embedded comment pages. Metadata-only parent queries and separate first-page collection are removed.
- `RepositoryHistory` is a frozen in-memory snapshot containing repository identity/creation time, captured head, collection interval, limitations, actors, and activities. Offline grouping uses UTC occurrence months, includes all referenced actors (including contextual authors and committers), and builds validated reports with shared snapshot metadata and both strategies `full`. Future output months are rejected relative to collection start; future source timestamps can remain in the snapshot.
- Validate the destination once per collect and all selected history-mode reports before replacing any file. Collection/grouping failures leave existing files untouched; writing failures preserve completed months. Keep per-file atomic replacement. Standalone writes retain full destination checks.
- Log collection duration, request attempts/retries, measured REST/GraphQL cost and unmeasured attempts, plus history grouping/writing time, including failures. Graph-delta optimization, interruption resume, and concurrency remain deferred.
- Sort activities by timestamp and stable ID, and actors by identity key. Identical source data produces identical content except collection metadata.

Document these limitations:

- Deleted comments, inaccessible items, removed commits, and transferred issues can limit historical reconstruction.
- Renames preserve repository and actor identity through node IDs; historical display names are not reconstructed.
- Titles and review states reflect collection-time metadata unless supported by a historical event.
- Force-pushes, rebases, changed default branches, and newly available data may revise a month when it is rerun.
- Bounded parent discovery can miss timestamp-invisible state changes and entirely deleted parents. Published tests support comments/review submissions advancing timestamps, but thread resolution is an exception. Manual full collect provides reconciliation; automatic periodic scans and live mutation experiments remain deferred.
- Empty repositories have zero commit activity; disabled issues do not prevent PR collection.
- Preserve null actors and reasons. For inline comments missing publication timestamps, fall back to creation time only when publication is otherwise confirmed, recording the fallback.
- Collection is not a transactional GitHub snapshot; concurrent changes may appear on a later rerun.

Persistent caching, graph-based commit deltas, and scheduling are deferred. The visualizer computes cumulative aggregation from independently versioned, offline-generated history summaries; full monthly reports remain the collection contract.

## PR-sized implementation phases

1. **Models and monthly contract.** Establish packaging and Pixi test tasks; implement normalized activities, actor identities, JSON Schema, and representative monthly fixtures. Validate every activity type and reject malformed records.

2. **GraphQL foundation.** Implement authentication, transport, query variables/fragments, cursor pagination, timeouts, retries, and rate-limit handling. Test multiple pages, invalid cursors, partial GraphQL errors, and retry exhaustion.

3. **Monthly lifecycle collection.** Collect issue/PR openings, merges, and issue closure/reopening events for a requested month. Test different opener/closer/merger identities, repeated transitions, missing reasons, and events on older parents.

4. **Monthly comments and reviews.** Collect conversation comments, submitted reviews, inline comments, and replies. Test nested pagination, old closed parents, draft exclusion, dismissed reviews, publication timestamps, and double-count prevention.

5. **Monthly commit collection.** Collect unfiltered captured default-branch history for both update and full collection, attributing commits to authors/co-authors. Record the strategy and coverage limitations. Test merge commits, unlinked identities, duplicate authors, exact output-month filtering, non-monotonic timestamps across pages, late-merged older commits, removed commits, changed default branches, and empty repositories.

6. **Reports and CLI.** Implement monthly summaries, schema validation, atomic writing, explicit collect/update commands, and offline summarization. Test UTC boundaries, leap years, empty months, partial months, isolated monthly runs, reruns that leave other files untouched, and collect failures.

7. **End-to-end validation and documentation.** Verify packaged schema availability and documented library/CLI usage. Use a multi-month mocked history to prove bounded updates match full-history projections for equivalent retrieved data and provide enough information to derive cumulative actor statistics. Add an opt-in live smoke check.


## Unreleased v1 contract preparation

The implemented contracts in `src/repo_history/schemas/` are authoritative.
Monthly reports now nest repository creation/history evidence, three bounded
provenance descriptors, and coverage observation metadata. Visualization summaries
retain source summaries alongside prepared aggregates and support a display name
and repository registry. Repository-wide `collection-state.json` binds checkpoints
to exact monthly report hashes; fully materialized full-history collection runs establish baseline checkpoints,
and unrestricted updates consume and advance verified checkpoints. See README sections “JSON contract
version 1”, “Repository collection state”, and “Prepared multi-repository summaries”
for field semantics. This revision retains schema version 1 and the existing public API; it adds
checkpoint-backed updates without a selectable collection strategy.

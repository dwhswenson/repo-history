# Monthly GitHub Repository Activity

## Goal and interfaces

Build a Python library and CLI that collects repository history once for initial backfills, groups activities into monthly reports offline, and writes `YYYY-MM.json`. Expose backfill, update, and summarize CLI commands; use one embedded-page collector for both workflows.

Reports support casual activity reporting, year-over-year growth analysis, and annual reports to funders. Counts should be useful and consistent, but exhaustive historical reconstruction is not required. Document known coverage limitations and favor practical collection costs where the tradeoff is explicit. Exact month assignment, attribution rules, deduplication, and internally consistent summaries still apply to collected events.

```python
collect_history(owner, repo, *, token=None) -> RepositoryHistory
history_to_months(history, start=None, end=None) -> list[MonthlyReport]
backfill(owner, repo, start=None, end=None, *, output_directory, token=None) -> list[Path]
```

```text
repo-history backfill OWNER/REPO [--start YYYY-MM] [--end YYYY-MM] --output-directory PATH
repo-history update OWNER/REPO [--start YYYY-MM] [--end YYYY-MM] --output-directory PATH
repo-history summarize --input-directory PATH [--output PATH]
```

Backfill always performs one complete history traversal followed by offline
monthly grouping. Optional start/end months are inclusive and limit output only.
Omitted start uses the earlier of repository creation and the earliest observed
activity, including imported commits. Omitted end uses the invocation's UTC month.
Include empty months.

Update always collects one interval using bounded parent discovery and buffered
commit history, then groups activities into monthly reports offline. With no
explicit start, validate destination reports and use the latest direct-child
`YYYY-MM.json` filename as the inclusive start, recollecting that month. Ignore
unrelated files and directories; do not repair earlier gaps. No monthly reports
or a missing directory triggers full-history backfill with automatic start.
Omitted end uses invocation's UTC month; reject future/reversed ranges before
requests. Verify repository identity before traversal and validate all projected
reports before replacing files. Record actual discovery/commit strategies and
coverage limitations, including internal boundary fallbacks. Neither command
accepts `--mode` or `--discovery`; Python backfill also removes these options.

Summarize replaces the former manifest command and generates the existing
visualizer manifest offline. Collection does not regenerate it. The implicit
single-month CLI and manifest command are removed; use update with equal endpoints
for one month. The independent `collect_month` API is removed. Report parsing,
validation, writing, offline grouping, and manifest generation remain available.

Update uses bounded parent discovery and buffered commit history for its interval.
Backfill always uses unfiltered commit history and full GraphQL parent discovery.
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
| PR conversation comment | `createdAt` | Comment author |
| PR inline review comment | `publishedAt` | Comment author |
| PR review | `submittedAt` | Review author |

- Use UTC calendar months and half-open intervals: `[month start, next month start)`. Return a valid report even when the month has no activity. Mark the current month as ongoing; reject future months.
- Collect unique commits reachable from a captured default-branch head, including merge commits. Preserve authored date and committer metadata, but use committed date for monthly grouping. This does not establish when a commit reached the branch. [Commit reference](https://docs.github.com/en/graphql/reference/commits)
- Count every issue closure and reopening independently. Preserve each closure event's reason, including completed, not planned, duplicate, or unknown; never substitute the issue's current reason. [Issue reference](https://docs.github.com/en/graphql/reference/issues)
- Distinguish PR conversation comments from inline comments. Include published replies on resolved and outdated threads. Count submitted reviews once, excluding pending reviews; a review body is not an additional comment. Preserve fetched review state as collection-time metadata. [PR reference](https://docs.github.com/en/graphql/reference/pulls)
- Credit merges and closures to their performers; retain the subject's author as context. Commit co-authors receive attribution without increasing the repository's commit count. Committers receive no separate contribution credit.

## Monthly JSON contract

Define a versioned Draft 2020-12 JSON Schema, distributed with the Python package. Validate every report before writing it.

Each file contains:

- **Metadata:** schema version, repository node ID, current name and URL, month boundaries, collection interval, ongoing-month flag, captured default-branch head, parent-discovery and commit-history strategies, and coverage limitations. Strategy fields are optional; absence means unknown. Collections record the actual selected strategies, including pre-traversal fallbacks, and bounded parent discovery includes an experimental completeness warning.
- **Actors:** stable identity keys, available GitHub IDs and logins, display metadata, and actor classification.
- **Activities:** stable event ID, type, occurrence timestamp, URL where available, subject reference, credited actors and roles, and activity-specific metadata.
- **Monthly summaries:** repository counts by activity type, per-actor counts by activity type, distinct actor counts by population, and unattributed-event counts.

Identity and counting rules:

- Use GitHub node IDs as identity keys; logins are mutable metadata.
- Separate linked human accounts, explicitly identified bots, and unresolved Git identities.
- For unlinked Git authors, derive a deterministic identity key from available email, falling back to name. Document that these identities are estimates; do not infer GitHub account matches.
- Preserve missing actors as null. Do not count all deleted or unknown users as one contributor.
- Deduplicate repeated actor roles within an event. A co-authored commit counts once for the repository and once for each distinct credited actor.
- Include subject titles and URLs, but omit comment bodies, review bodies, and full commit messages in v1.
- Preserve source status/reason strings and null values so new GitHub values remain representable.

The format must let a downstream consumer compute monthly and cumulative actor participation, including per-actor PR openings, by combining files without further GitHub requests.

Supplement schema validation with checks that actor references resolve, event IDs are unique, timestamps belong to the month, and summaries agree with activities.

## Collection behavior and edge cases

- An update with equal start/end months collects and writes only that month. It may inspect older parent objects to find that month's activity; it must not generate earlier reports or require earlier outputs.
- Do not select parent issues or PRs solely by creation month or current state. An old, closed PR can receive a new comment or review; an old issue can close or reopen in the requested month.
- Backfill uses full issue/PR parent traversal. Use repository connections directly, GraphQL variables, and reusable fragments. Independently paginate parents and nested timelines, comments, reviews, review threads, and commit authors. Detect missing or repeated cursors. [Pagination documentation](https://docs.github.com/en/graphql/guides/using-pagination-in-the-graphql-api)
- Update uses REST repository issues with all states and an overlapping update-time `since`, local `updated_at >= start` / `created_at < end` selection, and GraphQL hydration by repository identity and item number. Preserve all nested activity rules and the report schema. Completeness of REST parent update timestamps, including live discovery with issues disabled, remains unverified. The verification work in PERFORMANCE.md remains outstanding. Excluded parent histories are outside bounded mode's validation scope. Failures never silently switch strategies; January of year 1 selects full traversal before discovery because the overlap would underflow.
- Enforce exact month boundaries locally after retrieval. Apply API date filters with understood semantics and explicit coverage limitations.
- Capture the default-branch head before traversing commits. Use that SHA as the history root in both commit-history strategies.
- Update requests commit history with `since = interval start - 24 hours` and `until = interval end + 24 hours`. Fully paginate the API results, then retain only commits whose committed dates fall in the exact requested interval. This buffer accommodates small timestamp inversions but does not guarantee exhaustive retrieval: the API's lower bound can prune traversal before reaching matching ancestors. Record the strategy and this limitation in report metadata. If buffered bounds fall outside the API's documented supported date range, select full history traversal before querying and record the strategy actually used.
- Never stop commit pagination merely because an encountered commit or page has an old date. Update uses full history as a fallback for unsupported filter dates; `collect_history` always requests unfiltered history.
- Read authentication from `GH_TOKEN`, falling back to `GITHUB_TOKEN`. Support GitHub.com repositories accessible to the credential.
- Use request timeouts, bounded retries, rate-limit handling, and smaller pages for oversized queries. HTTP or GraphQL errors that prevent completion of the selected collection strategy fail the run; accepted coverage limitations do not excuse request failures or incomplete pagination.
- Write to a temporary file, validate, then atomically replace the requested month. Leave other monthly files untouched. Refuse an output directory containing reports for a different repository node ID.
- History-first backfill uses one client and repository lookup, checks destination repository identity before traversal, then collects every accessible parent/nested connection and unfiltered default-head commit history once. Normalize and deduplicate stable activities using the existing attribution and publication rules. Capture repository creation time in the lookup.
- History-mode parent queries embed first lifecycle/comment pages and PR review/thread pages; threads embed first inline-comment pages. Use independent `pageInfo` and resume cursors for every overflowing connection, including comments on later thread pages. Bound nested fan-out with 20 parents, 20 activity items, and 10 threads per page; use lean continuation queries except for thread pages, which retain embedded comment pages. Metadata-only parent queries and separate first-page collection are removed.
- `RepositoryHistory` is a frozen in-memory snapshot containing repository identity/creation time, captured head, collection interval, limitations, actors, and activities. Offline grouping uses UTC occurrence months, includes all referenced actors (including contextual authors and committers), and builds validated reports with shared snapshot metadata and both strategies `full`. Future output months are rejected relative to collection start; future source timestamps can remain in the snapshot.
- Validate the destination once per backfill and all selected history-mode reports before replacing any file. Collection/grouping failures leave existing files untouched; writing failures preserve completed months. Keep per-file atomic replacement. Standalone writes retain full destination checks.
- Log collection duration, request attempts/retries, measured REST/GraphQL cost and unmeasured attempts, plus history grouping/writing time, including failures. Persistent storage, resume, batching, and concurrency remain deferred.
- Sort activities by timestamp and stable ID, and actors by identity key. Identical source data produces identical content except collection metadata.

Document these limitations:

- Deleted comments, inaccessible items, removed commits, and transferred issues can limit historical reconstruction.
- Renames preserve repository and actor identity through node IDs; historical display names are not reconstructed.
- Titles and review states reflect collection-time metadata unless supported by a historical event.
- Force-pushes, rebases, changed default branches, and newly available data may revise a month when it is rerun.
- Buffered commit collection may miss in-period ancestors hidden behind commits dated before the buffered lower bound. Local clock errors and unusual or rewritten timestamps can exceed the one-day buffer. Counts are suitable for trend reporting, not an exhaustive audit. History-first collection has no date filters and does not carry the bounded-history warning.
- Empty repositories have zero commit activity; disabled issues do not prevent PR collection.
- Preserve null actors and reasons. For inline comments missing publication timestamps, fall back to creation time only when publication is otherwise confirmed, recording the fallback.
- Collection is not a transactional GitHub snapshot; concurrent changes may appear on a later rerun.

Persistent caching, incremental synchronization, and scheduling are deferred. The visualizer computes cumulative aggregation from independently versioned, offline-generated summary manifests; full monthly reports remain the collection contract.

## PR-sized implementation phases

1. **Models and monthly contract.** Establish packaging and Pixi test tasks; implement normalized activities, actor identities, JSON Schema, and representative monthly fixtures. Validate every activity type and reject malformed records.

2. **GraphQL foundation.** Implement authentication, transport, query variables/fragments, cursor pagination, timeouts, retries, and rate-limit handling. Test multiple pages, invalid cursors, partial GraphQL errors, and retry exhaustion.

3. **Monthly lifecycle collection.** Collect issue/PR openings, merges, and issue closure/reopening events for a requested month. Test different opener/closer/merger identities, repeated transitions, missing reasons, and events on older parents.

4. **Monthly comments and reviews.** Collect conversation comments, submitted reviews, inline comments, and replies. Test nested pagination, old closed parents, draft exclusion, dismissed reviews, publication timestamps, and double-count prevention.

5. **Monthly commit collection.** Collect captured default-branch history with buffered date bounds for updates and unfiltered history for backfills, attributing commits to authors/co-authors. Record the strategy and coverage limitations. Test merge commits, unresolved identities, duplicate authors, exact month filtering, small timestamp inversions within the buffer, documented omissions beyond the buffer, unbounded fallback across non-monotonic dates, unsupported filter dates, and empty repositories.

6. **Reports and CLI.** Implement monthly summaries, schema validation, atomic writing, explicit backfill/update commands, and offline summarization. Test UTC boundaries, leap years, empty months, ongoing months, isolated monthly runs, reruns that leave other files untouched, and backfill failures.

7. **End-to-end validation and documentation.** Verify packaged schema availability and documented library/CLI usage. Use a multi-month mocked history to prove bounded updates match full-history projections for equivalent retrieved data and provide enough information to derive cumulative actor statistics. Add an opt-in live smoke check.


## Unreleased v1 contract preparation

The implemented contracts in `src/repo_history/schemas/` are authoritative.
Monthly reports now nest repository creation/history evidence, three bounded
provenance descriptors, and coverage observation metadata. Visualization manifests
retain source summaries alongside prepared aggregates and support a display name
and repository registry. Repository-wide `collection-state.json` binds checkpoints
to exact monthly report hashes; only fully materialized full-history backfills
currently establish resumable checkpoints. See README sections “JSON contract
version 1”, “Repository collection state”, and “Prepared multi-repository manifests”
for field semantics. There is no backward compatibility, schema version bump,
manifest-combining command, or incremental updater in this revision.

# Monthly GitHub Repository Activity

## Goal and interfaces

Build a Python library and CLI whose primary operation collects **one month of activity for one repository** and writes `YYYY-MM.json`.

```python
collect_month(owner, repo, year, month, *, token) -> MonthlyReport
```

```text
repo-history-tracking OWNER/REPO --month YYYY-MM --output-directory PATH
```

Omitting `--month` selects the current UTC month. A separate `backfill` command accepts inclusive starting and ending months and invokes the same monthly operation.

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

- **Metadata:** schema version, repository node ID, current name and URL, month boundaries, collection interval, ongoing-month flag, captured default-branch head, and coverage limitations.
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

- A monthly run collects and writes only the requested month. It may inspect older parent objects to find that month's activity; it must not generate earlier reports or require earlier outputs.
- Do not select parent issues or PRs solely by creation month or current state. An old, closed PR can receive a new comment or review; an old issue can close or reopen in the requested month.
- Use repository connections directly, GraphQL variables, and reusable fragments. Independently paginate parents and nested timelines, comments, reviews, review threads, and commit authors. Detect missing or repeated cursors. [Pagination documentation](https://docs.github.com/en/graphql/guides/using-pagination-in-the-graphql-api)
- Enforce exact month boundaries locally after retrieval. Apply API date filters only where their semantics are verified; do not stop commit traversal simply because one encountered commit has an old date.
- Capture the default-branch head before traversing commits. Use that SHA as the history root. Broad parent or history traversal may be necessary for correctness even though the result is monthly.
- Read authentication from `GH_TOKEN`, falling back to `GITHUB_TOKEN`. Support GitHub.com repositories accessible to the credential.
- Use request timeouts, bounded retries, rate-limit handling, and smaller pages for oversized queries. HTTP or GraphQL errors that prevent complete collection fail the run.
- Write to a temporary file, validate, then atomically replace the requested month. Leave other monthly files untouched. Refuse an output directory containing reports for a different repository node ID.
- Backfill collects months chronologically and reuses the monthly collector. A failure stops backfill with a nonzero exit; previously completed months remain usable.
- Sort activities by timestamp and stable ID, and actors by identity key. Identical source data produces identical content except collection metadata.

Document these limitations:

- Deleted comments, inaccessible items, removed commits, and transferred issues can limit historical reconstruction.
- Renames preserve repository and actor identity through node IDs; historical display names are not reconstructed.
- Titles and review states reflect collection-time metadata unless supported by a historical event.
- Force-pushes, rebases, changed default branches, and newly available data may revise a month when it is rerun.
- Empty repositories have zero commit activity; disabled issues do not prevent PR collection.
- Preserve null actors and reasons. For inline comments missing publication timestamps, fall back to creation time only when publication is otherwise confirmed, recording the fallback.
- Collection is not a transactional GitHub snapshot; concurrent changes may appear on a later rerun.

Persistent caching, incremental synchronization, cumulative aggregation, visualization, and scheduling are deferred.

## PR-sized implementation phases

1. **Models and monthly contract.** Establish packaging and Pixi test tasks; implement normalized activities, actor identities, JSON Schema, and representative monthly fixtures. Validate every activity type and reject malformed records.

2. **GraphQL foundation.** Implement authentication, transport, query variables/fragments, cursor pagination, timeouts, retries, and rate-limit handling. Test multiple pages, invalid cursors, partial GraphQL errors, and retry exhaustion.

3. **Monthly lifecycle collection.** Collect issue/PR openings, merges, and issue closure/reopening events for a requested month. Test different opener/closer/merger identities, repeated transitions, missing reasons, and events on older parents.

4. **Monthly comments and reviews.** Collect conversation comments, submitted reviews, inline comments, and replies. Test nested pagination, old closed parents, draft exclusion, dismissed reviews, publication timestamps, and double-count prevention.

5. **Monthly commit collection.** Traverse captured default-branch history and attribute commits to authors/co-authors. Test merge commits, unresolved identities, duplicate authors, non-monotonic dates, and empty repositories.

6. **Reports and CLI.** Implement monthly summaries, schema validation, atomic writing, the single-month CLI default, and explicit backfill. Test UTC boundaries, leap years, empty months, ongoing months, isolated monthly runs, reruns that leave other files untouched, and backfill failures.

7. **End-to-end validation and documentation.** Verify packaged schema availability and documented library/CLI usage. Use a multi-month mocked history to prove independent monthly runs match backfill results and provide enough information to derive cumulative actor statistics. Add an opt-in live smoke check.

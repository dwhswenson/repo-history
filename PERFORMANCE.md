# Collection performance

The repository has two collection workflows, both using the same embedded-page
activity collectors:

- `collect`: collect full history once, then group selected months offline.
- `update`: reconcile a verified saved baseline using bounded parent discovery
  and a complete, unfiltered commit scan.

Without month flags, update uses the previous successful checkpoint as the parent
discovery cutoff and can rewrite older months. An unusable checkpoint triggers a
full-history rebuild. Explicit start/end options restrict which months are written
and invalidate the unrestricted checkpoint. Use equal endpoints for a single month.
`summarize` remains a separate offline step.

## Full-history collection

Collect uses one client and repository lookup, exhausts accessible issue and PR
connections and their nested activity histories, and walks commits reachable from
the captured default-branch head without date filters. It retains the normalized
snapshot in memory and groups it into monthly reports without more API requests.
The published inventory/checkpoint supports subsequent updates; partially written
runs are recovered by a full rebuild rather than interruption resume.

Parent queries embed first activity pages. Parent pages contain up to 20 items;
activity first pages contain up to 20 items; review-thread pages contain up to 10
threads, each embedding up to 20 inline comments. Only overflowing connections
require follow-up requests. Later thread pages also embed their first comment
pages. Lean activity and inline-comment continuation queries request up to 100
items, while thread continuations request up to 10.

The deterministic history fixture covers all activity types and independent
parent/activity/commit pagination. Its January–March 2024 collect uses **16
GraphQL requests**, with one synthetic point per response. The test asserts one
repository lookup and no separate activity first-page requests:

```text
pixi run -e dev python -m pytest tests/test_history.py::test_collect_collects_once_and_groups_offline -q
```

## Checkpoint-backed update

Update verifies the checkpoint against report hashes before trusting a parent
cutoff. It discovers candidate issues/PRs with REST repository issues, all states,
and an overlap before the previous successful run's start. The local timestamp
filter retains the overlap too. REST adds its existing one-second query overlap;
normal incremental calls therefore request two seconds before the checkpoint and
locally retain items updated at least one second before it. It exhausts pagination
and deduplicates item numbers. Explicit ranges use their month start instead.

Hydration batches contain at most 20 parents and use collect's embedded activity
fragments. Every selected parent's complete supported history is fetched. Commit
history is fully paginated once from the captured head, including nested author
and parent connections even for commits outside explicitly selected output months.
This avoids date-pruning omissions, at a cost proportional to complete commit
history on every update. There is no graph-delta optimization or local Git cache.

Unrestricted updates replace all saved commits and refreshed parent histories,
then compare activities and referenced actors to decide which older reports need
replacement. The previous final month and subsequent months are always refreshed.
If earlier history requires a new boundary, full parent discovery reuses the same
repository lookup and commit scan before publishing a rebuilt baseline.

The deterministic February 2024 update fixture contains one issue, one PR, and one
review thread, plus commit collection and actor resolution:

| Activity pagination | Requests across REST and GraphQL |
|---|---:|
| Activity connections fit in first pages | 9 |
| Independent activity overflow pages | 14 |

Tests assert these request budgets directly, including one REST discovery request
and one hydration request. Additional tests cover every activity connection's
overflow, comments on later thread pages, mixed hydration batches of 20/20/1,
actual strategy fallbacks, and report/state preservation on failed collection.

```text
pixi run -e dev python -m pytest tests/test_update.py::test_embedded_update_request_budget -q
```

These are offline fixture counts. They do not establish live runtime or API cost
savings. Embedded queries can cost more than one GraphQL point, and real page
sizes, response sizes, retries, and throttling affect performance.

## Completeness and validation

Bounded parent discovery remains experimental. Its lower bound assumes that each
counted activity advances the parent's REST update timestamp. This has not been
established for all activity types. Tests explicitly model a counterexample with
new reviews and inline comments on a parent whose REST timestamp remains old;
update omits those activities while full-history collection finds them.

The retained comparison fixtures exercise both workflows against a shared source,
including old closed parents, repeated transitions, dismissed reviews, resolved
and outdated threads, published replies, publication-time fallbacks, disabled
issues, UTC boundaries, and later parent updates. They compare normalized
activities, actors, and summaries, and check strategy/coverage differences
separately. Excluded parents are outside bounded collection's validation scope.

Both public collection commands now use unfiltered commit history, so neither
has the former 24-hour date-buffer limitation. Published timestamp experiments
support ordinary comments and submitted reviews advancing parent timestamps;
thread resolution is a known exception. Periodic full parent scans are not enabled:
manual `collect` reconciles timestamp-invisible changes and entirely deleted parents.
Request failures never silently switch strategies or remove inaccessible history.

## Remaining live verification

Live performance comparisons and completeness verification have not been run.
Future read-only comparisons should use equivalent credentials and intervals,
record captured heads, and distinguish changing source data from algorithmic
differences. Metric:

- Elapsed time and variability across repeated runs.
- REST and GraphQL requests, retries, throttling, and primary-budget points
  separately; response bytes where practical.
- Parents discovered, hydrated, and traversed, and nested activity requests.
- Differences in activity IDs, timestamps, credits, context, actors, and summaries.

Check REST parent timestamps for lifecycle events, conversation comments,
submitted reviews, inline comments, replies, and publication of previously
drafted comments. Include old closed/merged parents and disabled issues. Matching
samples increase confidence without proving a universal API guarantee.

Collect INFO timing records cover four non-overlapping phases, on success or
failure:

- Preparation: request validation and checking destination reports.
- History collection: client initialization, repository lookup, collection and
  retries, normalization, and client cleanup. This record also includes API usage.
- Grouping/writing monthly: provenance setup, reading prior repository metadata,
  monthly projection and validation, file writes, and saved-path callbacks.
- Inventory/verification: building the inventory and checkpoint, writing the
  temporary collection state, final verification, publishing, and temporary-file
  cleanup.

Add these records' `elapsed_seconds` values to compare against the final
`Collect summary`. That summary reports `total_elapsed_seconds`,
`phase_elapsed_seconds`, and their difference, `unaccounted_seconds`; its total
is not another additive phase. The CLI total includes argument parsing, display
setup, and display cleanup; library calls measure the collection operation itself.
Interpreter startup and imports before CLI entry are outside the total. The
overlapping grouping-only timing is DEBUG rather than INFO.

Persistent caching, concurrency, graph-based commit deltas, and automatic periodic
parent reconciliation remain separate future work.

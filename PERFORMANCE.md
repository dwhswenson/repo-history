# Collection performance

The repository has two collection workflows, both using the same embedded-page
activity collectors:

- `backfill`: collect full history once, then group selected months offline.
- `update`: collect the selected interval once with bounded parent discovery and
  buffered commit history, then group selected months offline.

Without an explicit start, update recollects the latest monthly report and every
subsequent month through its end. No reports triggers full-history backfill.
For a single month, use update with equal start and end values. There is no
independent monthly collector or selectable collection mode. `summarize` builds
a visualization manifest offline and remains a separate step.

## Full-history backfill

Backfill uses one client and repository lookup, exhausts accessible issue and PR
connections and their nested activity histories, and walks commits reachable from
the captured default-branch head without date filters. It retains the normalized
snapshot in memory and groups it into monthly reports without more API requests.
There is no persistent cache or interruption resume.

Parent queries embed first activity pages. Parent pages contain up to 20 items;
activity first pages contain up to 20 items; review-thread pages contain up to 10
threads, each embedding up to 20 inline comments. Only overflowing connections
require follow-up requests. Later thread pages also embed their first comment
pages. Lean activity and inline-comment continuation queries request up to 100
items, while thread continuations request up to 10.

The deterministic history fixture covers all activity types and independent
parent/activity/commit pagination. Its January–March 2024 backfill uses **16
GraphQL requests**, with one synthetic point per response. The test asserts one
repository lookup and no separate activity first-page requests:

```text
pixi run -e dev python -m pytest tests/test_history.py::test_backfill_collects_once_and_groups_offline -q
```

## Bounded update

Update discovers candidate issues and PRs with the REST repository issues
endpoint, all states, and `since` one second before interval start. It exhausts
pagination, deduplicates item numbers, and locally retains parents satisfying
`updated_at >= start` and `created_at < end`. A later update time does not exclude
an older parent's in-period activity.

GraphQL hydration batches contain at most 20 parents and use the same embedded
activity fragments as backfill. Each retained parent's activity connections are
exhausted, with exact interval filtering applied locally. Commit history is
queried once from the captured head, with a 24-hour buffer around the interval.
The collector does not repeat traversal for each output month.

The deterministic February 2024 update fixture contains one issue, one PR, and one
review thread, plus commit collection and actor resolution:

| Activity pagination | Requests across REST and GraphQL |
|---|---:|
| Activity connections fit in first pages | 6 |
| Independent activity overflow pages | 11 |

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

Buffered commit history can also miss matching ancestors behind unusually old
intermediate timestamps. Full-history backfill has no API date filter and avoids
that particular limitation. Internal date-boundary fallbacks are selected before
traversal and recorded in provenance. Request failures never silently switch
strategies.

## Remaining live verification

Live performance comparisons and completeness verification have not been run.
Future read-only comparisons should use equivalent credentials and intervals,
record captured heads, and distinguish changing source data from algorithmic
differences. Measure:

- Elapsed time and variability across repeated runs.
- REST and GraphQL requests, retries, throttling, and primary-budget points
  separately; response bytes where practical.
- Parents discovered, hydrated, and traversed, and nested activity requests.
- Differences in activity IDs, timestamps, credits, context, actors, and summaries.

Check REST parent timestamps for lifecycle events, conversation comments,
submitted reviews, inline comments, replies, and publication of previously
drafted comments. Include old closed/merged parents and disabled issues. Matching
samples increase confidence without proving a universal API guarantee.

Backfill INFO timing records cover four non-overlapping phases, on success or
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
`Backfill summary`. That summary reports `total_elapsed_seconds`,
`phase_elapsed_seconds`, and their difference, `unaccounted_seconds`; its total
is not another additive phase. The CLI total includes argument parsing, display
setup, and display cleanup; library calls measure the backfill operation itself.
Interpreter startup and imports before CLI entry are outside the total. The
overlapping grouping-only timing is DEBUG rather than INFO.

Persistent caching, concurrency, and incremental checkpoint consumption remain
separate future work.

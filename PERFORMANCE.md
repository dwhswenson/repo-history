# CLI workflow performance

`backfill` always uses a complete history traversal followed by offline monthly
grouping. `update` always uses one traversal of its selected interval: one
repository lookup, bounded parent discovery, each retained parent's nested
histories, and buffered captured-head commit history. Grouping happens offline
and all selected reports validate before writing. Actual strategies and existing
completeness limitations are recorded per report.

Without an explicit start, update recollects the latest monthly report filename
and every subsequent month through its end. No reports triggers history-first
backfill. There is no independent monthly CLI workflow. `summarize` is offline and
must be invoked explicitly after reports change.

# Collector performance

For single-month Python collection and CLI updates, bounded discovery is the
default by user preference. The validation work below remains outstanding; this
default change does not establish completeness.
Full traversal remains available through `discovery="full"`.

## History-first backfill

Backfill collects a `RepositoryHistory` snapshot once, followed
by `history_to_months` offline projection. Parent connections and each nested
connection are exhausted once rather than revisited per month. Commit history is
walked once from a captured default-branch SHA without `since`/`until`. A single
client also shares canonical commit actor resolution across the complete run.
Destination reports are scanned once per backfill, avoiding repeated directory
validation as months are written.

History-mode parent queries now include the first activity pages, with inline
comment pages embedded in review threads. Complete connections need no follow-up
request. Overflowing connections retain independent cursors and pagination; later
thread pages also embed comment pages. Parent pages are capped at 20, activity
first pages at 20, and thread pages at 10 to bound nested fan-out.

The deterministic `tests/test_history.py` fixture covers all activity types and
parent/nested pagination, with one synthetic GraphQL point per response. For the
same three-month output (January–March 2024):

| Path | Requests | Synthetic GraphQL points |
|---|---:|---:|
| History-first backfill, embedded first pages | 16 | 16 |
| Previous history-first backfill, separate first pages | 23 | 23 |
| Monthly backfill, full parent discovery | 58 | 58 |

Embedding first pages reduces fixture requests by 30% versus the previous
history collector and 72% versus independent monthly collection. These synthetic
costs do not predict GitHub's actual query costs: bundled queries may cost more
than one point each. A sample local run took 0.084 s versus 0.085 s for monthly
collection; mocked latency is negligible and these timings establish no live
speedup. Measure live points per returned activity as well as request counts.
Reproduce the comparison with:

```text
pixi run -e dev python -m pytest tests/test_history.py::test_monthly_equivalence_and_request_measurement -s
```

The comparison asserts identical activities, actors, and summaries, while
provenance differs intentionally. A separate fixture verifies recovery of a
commit hidden by bounded history pruning. Collection logs report actual request
attempts/retries, measured API points and unmeasured attempts, including failed
runs; backfill reports grouping/writing time.

Live cost/runtime comparisons remain optional and have not been run. For live
evaluation, use equivalent credentials and output ranges, run both modes close
together, compare normalized activities and captured heads, and report actual
GraphQL costs, throttling, and elapsed-time variability. A full history walk may
be more expensive than monthly collection for a narrow range. The complete
snapshot stays in memory; interruption requires a fresh traversal.

## Monthly collector optimization plan

### Implemented: embedded activity pages in CLI update

CLI updates reuse the history collector's issue/PR activity fragments and
embedded-page traversal after bounded REST discovery. GraphQL hydration batches
contain at most 20 parents, with first pages of 20 activity items and 10 review
threads; each thread embeds up to 20 inline comments. Only overflowing
connections need follow-up requests. Later thread pages keep embedding inline
comments. Ordinary activity and inline-comment continuations request up to 100
items, while thread continuations request up to 10.

Standalone `collect_month` retains metadata-only hydration in batches of 50 and
separate activity requests. This provides an independent bounded reference for
output comparisons. Full-history backfill continues using its existing queries.

The deterministic `tests/test_update.py` comparison uses identical source data
for February 2024, with one issue, one PR, and one review thread. Both paths
include repository lookup, one REST discovery request, one hydration request,
and the same buffered commit collection and actor resolution:

| Activity pagination | Metadata-only reference requests | Embedded update requests | Reduction |
|---|---:|---:|---:|
| All activity connections fit in first pages | 13 | 6 | 54% |
| Independent activity overflow pages | 18 | 11 | 39% |

Both fixtures save seven requests: two issue activity first pages, four PR
activity first pages, and one thread-comment first page. The comparisons assert
identical activities, actors, summaries, and coverage limitations. Separate
tests cover every activity connection's overflow, inline-comment overflow on
later thread pages, enriched batches of 20/20/1, and report/state preservation
after malformed embedded pages or pagination failures. Reproduce counts with:

```text
pixi run -e dev python -m pytest tests/test_update.py::test_embedded_update_request_measurement -q -s
```

These are offline fixture request counts, not measured live runtime or API cost
savings. The simulated pages do not model every effect of different page sizes:
20-item embedded first pages can require extra continuations compared with
standalone 100-item first pages, and 20-parent hydration batches can require more
requests than 50-parent batches. Live measurements should include those costs,
actual GraphQL points, retries, throttling, and response sizes.

Activity date filtering remains local, and retained parents' histories are still
exhausted. This optimization adds no persistent cache, date pruning, concurrency,
or changes to discovery/commit completeness assumptions. Missing embedded pages
fail collection without falling back to separate first-page requests. Hydration
uses existing transport retries without adaptive alias-batch splitting.

The remaining plan below concerns bounded collection through `collect_month` and
CLI updates.
It is independent of history-first backfill and its full parent discovery.

## Objective and current behavior

Reduce the API work needed to collect one UTC month without changing activity,
attribution, or report semantics. This document proposes implementation and
validation work; it does not establish that parent update timestamps are already
safe to use as a completeness filter.

For standalone Python collection with full discovery, `discovery.py::discover_parents` paginates every accessible
issue and PR through unfiltered repository connections. For each parent,
`collection.py` independently traverses lifecycle events, conversation comments,
and, for PRs, reviews, review threads, and thread comments. Activities are retained
only when their reporting timestamp is in the requested month. Each independent
monthly run repeats nested traversal; there is no persistent cache. Default
bounded discovery selects candidate parents before this nested traversal.

Keep this full traversal as the reference implementation while evaluating the
optimization. Parent creation month and current state alone cannot select the
relevant parents: an old, closed issue or PR may have new activity.

## Proposed parent discovery

Use the REST repository issues endpoint to discover candidate parents, then use
the existing GraphQL collectors to retrieve their activities. The REST endpoint
returns both issues and PRs and supports an update-time `since` filter. GraphQL's
repository issue connection supports `filterBy.since`, but its PR connection has
no equivalent filter.

For month boundaries `start` and `end`, use these candidate rules:

| Bound | Candidate rule | Where applied |
|---|---|---|
| Lower | `updatedAt >= start` | REST `since`, with an overlap, then local filtering |
| Upper | `createdAt < end` | Local filtering of discovered parents |
| Activity interval | `start <= occurredAt < end` | Existing activity normalization |

The upper bound is on **creation**, not last update. Do not exclude a parent
because it was updated after the requested month. A February contribution on an
issue updated again in September must remain discoverable for February.

Example for February 2024:

```http
GET /repos/OWNER/REPO/issues?state=all&since=2024-01-31T23:59:59Z&per_page=100
```

1. Resolve repository identity and capture report metadata once, as today. Use
   the resolved current repository name for REST requests.
2. Request all states, including closed issues and closed/merged PRs. Set `since`
   one second before `start` because REST documents the boundary as strictly
   "after". Handle datetime underflow for the earliest supported month by
   falling back to full traversal.
3. Follow REST pagination links until exhaustion. Do not stop based on result
   ordering or a timestamp encountered on a page. Detect repeated pagination
   links and fail rather than returning partial results.
4. Validate and deduplicate discovery records. Apply the exact candidate rules
   locally; discard overlap results and parents created at or after `end`.
5. Distinguish PRs by the REST `pull_request` field. Hydrate the existing
   GraphQL subject and author fields using repository identity and item number.
   Do not assume the REST issue ID/node ID for a PR is the GraphQL PullRequest
   ID. Batch hydration where practical, with explicit missing-node handling.
6. Run the existing nested collectors for retained parents. Keep their event
   timestamps, publication fallback rules, actor identities, deduplication,
   validation, and final half-open month filtering.

This still fetches parents updated any time after month start. Consequently it
should help recent months more than old historical months. Later metadata-only
updates can produce extra candidates; that affects efficiency, not correctness.

## Implementation outline

1. **Separate discovery from collection.** Extract the current parent connection
   traversal behind a discovery interface and let both strategies feed the same
   subject hydration and nested collection logic. Preserve an explicit full
   traversal mode for comparison and fallback. Keep optimized mode experimental
   until the acceptance criteria below pass.
2. **Add REST transport and pagination.** Reuse the existing authentication
   precedence and session lifecycle. Provide timeouts, bounded retries,
   primary/secondary rate-limit handling, response validation, and safe
   pagination restricted to the expected GitHub API origin. Surface failures
   without returning a partial report; never interpret an error as an empty
   candidate list.
3. **Implement bounded discovery and hydration.** Add UTC bounds, overlap,
   all-state selection, PR identification, deduplication, and GraphQL hydration.
   Preserve disabled-issues behavior: skip issue activity while still discovering
   and collecting PRs. Verify this case against the REST endpoint.
4. **Instrument both strategies.** Collect request/page counts, parent counts,
   timings, retry counts, and available rate-limit measurements outside the
   monthly report contract. Include hydration work in optimized totals.
5. **Add comparison tooling and fixtures.** Run both strategies on equivalent
   source histories and produce actionable differences, including parents and
   activity IDs omitted by discovery.
6. **Evaluate and document rollout.** If validated, make bounded discovery the
   default while retaining full traversal for audits. Update README.md and the
   repository-connection requirement in SPEC.md to describe the approved REST
   discovery approach and its assumptions.

Do not silently switch strategies midway through a failed run. If fallback is
needed, restart collection explicitly and record which strategy completed.

## Completeness assumption to investigate

The lower bound is safe only if each counted activity implies that its parent's
discovery update timestamp is at least the activity's reporting timestamp.
Verify the REST discovery timestamp specifically; agreement with a GraphQL
timestamp should not be assumed.

Existing firsthand observations suggest conversation comments and submitted
reviews advance parent timestamps. They do not establish a current guarantee
for every activity. In particular, verify:

- Issue closure and reopening, including repeated transitions; PR merge.
- New conversation comments on old open and closed parents.
- Submitted reviews with no body, on draft PRs, and on closed/merged PRs where
  GitHub permits them; later dismissal must not remove historical submission.
- Inline comments and standalone replies, including resolved/outdated threads.
- Comments drafted before month start and published during the month, using
  publication rather than draft creation time.
- Missing publication timestamps with confirmed publication and the current
  creation-time fallback.

Thread resolution and review-state changes are collection-time metadata, not
additional contributions. A future persistent cache would need a separate
strategy for refreshing these fields even when parent timestamps do not move.

If a counted activity fails the assumption, do not enable parent filtering as
the sole discovery method. Either retain full traversal for that path or add
independent discovery that covers it. REST has repository-wide conversation
comment and review-comment endpoints with their own update-time `since`
filters. These are possible supplements, not proven replacements: inline
publication semantics and required review/thread context must still be checked.

## Comparison with the current collector

### Deterministic correctness checks

Use shared source histories to exercise both discovery strategies. Fixtures
must model actual filter behavior, including a counterexample where an activity
does not advance its parent timestamp; do not bake the assumption into every
fixture and then treat matching outputs as proof of GitHub behavior.

Cover multiple parent and nested pages, duplicate discovery records, exact UTC
month boundaries, leap February, empty/ongoing months, disabled issues, old
closed parents, parents created after month end, and parents updated again long
after the requested month. Exercise malformed responses, pagination loops,
rate limits, missing hydration results, and transport failures.

For valid source data, compare the complete normalized reports after excluding
collection start/finish timestamps and the expected differences in parent-discovery
provenance and its bounded-discovery coverage warning. Check those provenance
differences separately. Hold repository identity, captured
head, and collection-start time constant in fixtures. Require equality of:

- Activity IDs, kinds, occurrence times, credits, subject context, and metadata.
- Actor records and references, including null/unattributed actors and bots.
- Repository and per-actor summaries and coverage limitations.

Both reports must pass existing validation. Compare failure behavior separately:
optimized collection will intentionally not inspect excluded parents, so it
cannot detect malformed unrelated history on those parents. Document this
change in validation scope rather than claiming identical failure behavior.

### Live verification

Use read-only comparison runs on selected repositories and completed months.
Run controlled mutation experiments only in an explicitly authorized disposable
repository; this plan does not authorize posting test issues or comments.
Record parent timestamps and discovery inclusion before and after each activity
listed above, allowing for and measuring indexing delay.

Run full and optimized collection close together, and bracket optimized runs
with full runs when investigating differences. GitHub is not a transactional
snapshot: flag changing source data, metadata, or captured heads and rerun those
cases before attributing differences to the algorithm. Preserve raw comparison
evidence without credentials or comment/review bodies.

Matching live samples increase confidence but are not a universal API guarantee.
Document the tested cases, dates, API versions, and remaining uncertainty.

### Performance measurements

Compare both strategies on the same repositories and months, with equivalent
credentials, page sizes, and retry settings. Include small repositories, large
mostly inactive repositories, repositories with extensive review histories,
recent completed months, and old historical months.

Measure total elapsed time; REST and GraphQL request/page counts; parents listed,
hydrated, and deeply traversed; nested requests by activity family; response
bytes where practical; retries and throttling; GraphQL rate-limit cost; and REST
rate-limit consumption. Report the two rate-limit budgets separately. Repeat
runs and report variability rather than relying on one elapsed-time sample.

Accept the optimization only when:

1. Deterministic reports match and all required edge cases pass.
2. Every counted activity has supporting discovery evidence, or an independent
   discovery/fallback path covers the unresolved cases.
3. Live comparisons have no unexplained missing or additional activities.
4. Measurements show a material reduction in requests or runtime on the target
   large repositories, including REST discovery and GraphQL hydration overhead.
   Record regressions and the limited benefit for old months.

## Follow-on work, evaluated separately

After isolating parent-discovery gains, consider verified `timelineItems.since`
filters to reduce lifecycle event retrieval. Keep exact local month boundaries;
do not apply timeline creation-time filtering blindly to comments counted by
publication time. Persistent caching and further commit-history optimization remain separate
workstreams. Collecting history once for multiple months is implemented above.

## Sources and evidence

- [GraphQL repository connections](https://docs.github.com/en/graphql/reference/repos#repository)
  and [issue filters](https://docs.github.com/en/graphql/reference/issues#issuefilters).
- [REST repository issues](https://docs.github.com/en/rest/issues/issues#list-repository-issues):
  combined issue/PR discovery and update-time `since` semantics.
- [Firsthand integration test notes from 2019](https://pkg.go.dev/github.com/pinpt/agent@v3.1.12%2Bincompatible/integrations/github#readme-does-updating-node-children-update-updated-at-field-on-parent):
  observed PR timestamp changes for comments/reviews, but not thread resolution.
- [November 2024 GraphQL reproduction](https://github.com/orgs/community/discussions/144806):
  a comment edit advances its parent issue timestamp; timeline filtering uses
  different timestamp semantics.
- [April 2026 REST PR sorting bug report](https://github.com/orgs/community/discussions/192025):
  reported disagreement between sort order and returned update timestamps.
  This is a user report, not confirmation of a GraphQL sorting defect.
- [Repository conversation comments](https://docs.github.com/en/rest/issues/comments#list-issue-comments-for-a-repository)
  and [repository review comments](https://docs.github.com/en/rest/pulls/comments#list-review-comments-in-a-repository):
  potential independent discovery paths.

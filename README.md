# repo-history

## Table of contents

- [Introduction](#introduction)
- [Installing](#installing)
- [Quick start](#quick-start)
- [Collection and Summary Manipulation](#collection-and-summary-manipulation)
  - [`collect`](#collect)
  - [`update`](#update)
  - [`summarize`](#summarize)
  - [`trim-partial`](#trim-partial)
  - [`combine`](#combine)
- [Visualization](#visualization)
- [Understanding the data](#understanding-the-data)
  - [Big ideas](#big-ideas)
  - [Activity-based metrics](#activity-based-metrics)
  - [Contributor-based metrics](#contributor-based-metrics)
  - [Other metrics](#other-metrics)
- [Schemas](#schemas)
- [Development](#development)
- [License](#license)

## Introduction

This repository contains code to collect and visualize the history of a repository on GitHub.

Find various live examples of the visualizer in action at [https://repo-history.dwhswenson.net](https://repo-history.dwhswenson.net).

## Installing

Install the Python collector and command line tools from [PyPI](https://pypi.org/project/repo-history/) in your Python environment:

```sh
python -m pip install repo-history
```

Install the visualization component from [npm](https://www.npmjs.com/package/@dwhswenson/repo-history-viz) in your web project:

```sh
npm install @dwhswenson/repo-history-viz
```

For a static site, copy `node_modules/@dwhswenson/repo-history-viz/dist/repo-history-viz.js` and the package's `dist/THIRD_PARTY_LICENSES.md` into your web assets. See the [visualizer installation guide](repo-history-viz/README.md#use-in-any-static-site) for embedding instructions and framework examples.

For local development, use the Pixi environment described under [Development](#development).

## Quick start

Once you've installed `repo-history`, you'll need to store authentication credentials in an environment variable, then run the `collect` command to collect the history of a repository, and then run the `summarize` command to generate a summary for visualization.

```text
$ export GH_TOKEN=$(gh auth token)  # assuming `gh` is installed/authenticated
$ repo-history collect OWNER/REPO --output-directory history-dir
✓ Issues       ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 404/404 100%   0:00:27  completed
✓ PRs          ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 437/437 100%   0:01:34  completed
✓ Commits      ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 1150/1150 100% 0:00:20  completed
✓ Grouping     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 56/56 100%     0:00:22  completed
✓ Writing      ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 56/56 100%     0:00:57  completed
✓ Inventory    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 56/56 100%     0:00:15  completed
✓ Verification ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 56/56 100%     0:00:12  completed
   Completed: Saved 56 monthly reports
GraphQL 161 points spent · budget (last observed): 4828 remaining, reset 2026-10-19
14:12:40 CDT · 105 requests, 0 retries, 0 unmeasured attempts

$ repo-history summarize --input-directory history-dir
✓ Reports ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 56/56 100% 0:00:11  completed
   Completed: Saved summary.json
history-dir/summary.json

$ tree history-dir
history-dir
├── 2022-03.json
├── 2022-04.json
├── ...
├── ...
├── 2026-09.json
├── 2026-10.json
├── collection-state.json
└── summary.json
```

Now put that `summary.json` file somewhere that your web server can find it, and add the visualizer script and web component to your HTML, pointing the web component at that summary file:

```html
 <script type="module" src="./repo-history-viz.js"></script>
 <repo-history-viz
   summary="./path/to/summary.json"
   metrics="commits github-activities total-contributors"
   hide-loader
  ></repo-history-viz>
```

The visualization will show up on your web page, allowing you to explore the history of the repository!

## Collection and Summary Manipulation

The collection and analysis code is written in Python, and is located in the `src/repo_history` directory.
Installing it provides a command line tool called `repo-history`, which has subcommands for collecting and manipulating history summaries.

```text
$ repo-history --help
usage: repo-history [-h] {collect,update,summarize,trim-partial,combine} ...

Collect repository history and summarize reports.

positional arguments:
  {collect,update,summarize,trim-partial,combine}
    collect             Collect repository history.
    update              Refresh history and backfill older activity.
    summarize           Generate the history summary offline.
    trim-partial        Remove one trailing partial month from an existing
                        summary offline.
    combine             Combine summaries from disjoint repositories offline.

options:
  -h, --help            show this help message and exit
```

The `collect` and `update` commands are the main commands for collecting history, and they produce monthly JSON files named in the format YYYY-MM.json, which contain the collected activity history of a repository.
To run them, you need to provide a GitHub token with read access to the repository you want to collect history for, and store that in either the `GH_TOKEN` or `GITHUB_TOKEN` environment variable.

Both collection commands will also produce a `collection-state.json` file, which binds the saved report inventory to the last successful collection checkpoint.

### `collect`

```text
$ repo-history collect --help
usage: repo-history collect [-h] [--start YYYY-MM] [--end YYYY-MM]
                            --output-directory OUTPUT_DIRECTORY
                            [--no-progress] [--quiet | --verbose]
                            OWNER/REPO

Collect history for an inclusive month range; default to all history.

positional arguments:
  OWNER/REPO

options:
  -h, --help            show this help message and exit
  --start YYYY-MM       default: earliest history
  --end YYYY-MM         default: current UTC month
  --output-directory OUTPUT_DIRECTORY
  --no-progress         disable added progress output; retain ordinary logs
  --quiet               errors only; suppress saved paths
  --verbose             include debug diagnostics
```

The `collect` subcommand collects the history of a repository, usually from the beginning of the repository's history until the present.
This will usually be the first command you run on a repository.
The build goes through several stages:

1. Collecting history from GitHub, in the order of issues, pull requests, and commits. Comments and reviews are collected during this pass.
2. Grouping all activity into monthly buckets, and then writing a monthly output file named YYYY-MM.json for each month of history.

### `update`

```text
$ repo-history update --help
usage: repo-history update [-h] [--start YYYY-MM] [--end YYYY-MM]
                           --output-directory OUTPUT_DIRECTORY
                           [--quiet | --verbose]
                           OWNER/REPO

Reconcile saved history; explicit month limits restrict output. Rebuild
automatically when no verified checkpoint exists.

positional arguments:
  OWNER/REPO

options:
  -h, --help            show this help message and exit
  --start YYYY-MM       restrict output; default with --end: latest report month
  --end YYYY-MM         default: current UTC month
  --output-directory OUTPUT_DIRECTORY
  --quiet               errors only; suppress saved paths
  --verbose             include debug diagnostics
```

The `update` subcommand refreshes previously collected repository history, including older months affected by newly discovered activity.
The update goes through several stages:

1. Checking the saved collection state, falling back to full collection if no verified checkpoint exists.
2. Collecting complete histories for issues and pull requests updated since the last successful collection, and all commits reachable from the default branch.
3. Reconciling the collected activity with saved history, then rewriting affected monthly reports and saving the new collection state.

Explicit start/end months restrict which reports are written.

### `summarize`

```text
$ repo-history summarize --help
usage: repo-history summarize [-h] --input-directory INPUT_DIRECTORY
                              [--output OUTPUT] [--display-name DISPLAY_NAME]
                              [--no-progress] [--quiet | --verbose]

Build a self-contained history summary for visualization offline.

options:
  -h, --help            show this help message and exit
  --input-directory INPUT_DIRECTORY
  --output OUTPUT       default: INPUT_DIRECTORY/summary.json
  --display-name DISPLAY_NAME
                        dataset title; default: owner/repo
  --no-progress         disable added progress output; retain ordinary logs
  --quiet               errors only; suppress saved paths
  --verbose             include debug diagnostics
```

The `summarize` subcommand generates a summary that can be used by the visualizer to display the history of a repository.
It uses the monthly JSON files from either the `collect` or `update` commands, and produces a single JSON file that contains all of the data needed for visualization (which is much less than the full history data).

### `trim-partial`

```text
$ repo-history trim-partial --help
usage: repo-history trim-partial [-h] [--output OUTPUT] [--quiet | --verbose]
                                 SUMMARY

Remove a trailing partial month, leaving at least one month. All earlier
months must be complete. Already complete summaries are left unchanged in
place or saved to the explicit output.

positional arguments:
  SUMMARY               summary file or directory containing summary.json

options:
  -h, --help            show this help message and exit
  --output OUTPUT, -o OUTPUT
                        output summary path; default: edit in place
  --quiet               errors only; suppress saved paths
  --verbose             include debug diagnostics
```

When collecting history, the current month's report has `metadata.is_partial: true` because it contains data only through the collection snapshot, before month-end.
If you want to remove that partial month from a history summary, you can use the `trim-partial` subcommand to remove it.

### `combine`

```text
$ repo-history combine --help
usage: repo-history combine [-h] --output OUTPUT --display-name DISPLAY_NAME
                            [--include-partial] [--quiet | --verbose]
                            SUMMARY [SUMMARY ...]

Combine at least two summary files or directories containing summary.json.
Repositories must not overlap and retained months must have complete coverage.
Exclude an entire month if any source is partial unless --include-partial is
supplied.

positional arguments:
  SUMMARY               at least two summary files or directories containing
                        summary.json

options:
  -h, --help            show this help message and exit
  --output OUTPUT, -o OUTPUT
  --display-name DISPLAY_NAME
                        nonblank dataset title
  --include-partial     retain partial months
  --quiet               errors only; suppress saved paths
  --verbose             include debug diagnostics
```

Although the visualizer is mainly designed to visualize the history of a single repository, it is possible to combine the summaries of multiple repositories into a single summary that can be visualized.
This deduplicates contributors with matching identity keys across repositories, and produces a single summary of the combined history of all of the repositories.
You can use the `combine` subcommand to do this, and it will take a list of summaries and produce a single combined summary.

## Visualization

Visualization code is written in TypeScript, and is located in the `repo-history-viz` directory.
The visualizer is provided as a web component, so adding it to your HTML is as simple as:

```html
 <script type="module" src="./repo-history-viz.js"></script>
 <repo-history-viz
   summary="./path/to/summary.json"
   metrics="commits github-activities total-contributors"
   hide-loader
  ></repo-history-viz>
```

The `metrics` attribute is a required, whitespace-separated list of metrics to offer in the visualizer:

* `commits` - monthly/cumulative commits
* `github-activities` - monthly/cumulative GitHub activities (issues, PRs, comments, reviews)
* `total-contributors` - monthly/cumulative unique contributors across all activity types, deduplicated across types
* `contributors-by-type` - monthly/cumulative unique contributors, broken down by activity type (e.g., to see how many people per month are making commits vs. reviewing PRs)
* `balance` - cumulative issue and PR balances over the selected month range: issues opened plus reopened minus closed, and PRs opened minus merged. These show net change, rather than the repository's actual open totals.

Every metric you want to offer must be listed explicitly; there is no default selection of metrics or wildcard to enable them all.
Only listed metrics appear in the dropdown, in the order given, with the first initially selected.
If `metrics` is missing, empty, or contains an unknown name, the visualizer shows a configuration error and no chart.

## Understanding the data

### Big ideas

The collector records events for issues, pull requests, commits, comments, and reviews.
PR discussion comments are general comments on a pull request; PR review comments are published inline comments and replies in code review threads.
Submitted PR reviews are counted separately from those comments.
It then groups these activities into monthly buckets (based on UTC), allowing us to watch the evolution of repository activity over time.
In default builds, the last (current) month is considered partial, and will be marked as such in the visualization.
A missing monthly report represents a gap in the data, not zero activity; cumulative values include only the loaded months.

Deleted or inaccessible activity may be missing from the reconstructed history, and later collection runs can revise historical results.

The Aggregation control offers a Monthly view (showing the activity in each month) or a Cumulative view (showing the total activity from the selected start month through that month).
The time window of the visualization can be adjusted to show a subset of the months in the summary.
When this happens, the cumulative view will start from the first month in the selected range, rather than the first month in the summary.

### Activity-based metrics

We'll refer to metrics as "activity-based" if they count a number of activities, such as commits, issues, or pull requests.
Activity-based metrics are useful for answering questions like "how many commits were made in a given month" or "how many issues were closed over a period of time."
The current activity-based metrics are Commits and GitHub activity (which gives separate counts for various types of GitHub activities, including opening/closing issues, making issue/PR comments, and reviewing PRs).
Each recorded event belongs to one month, so activity counts can be added across months.
This means that you can find, for example, the total number of commits in 2025 in the cumulative view either by setting the month range to Jan 2025 through Dec 2025 and looking at the cumulative value for Dec 2025, or by keeping the range over all months, looking at the cumulative value for Dec 2025, and subtracting the cumulative value for Dec 2024.

### Contributor-based metrics

We'll refer to metrics as "contributor-based" if they count a number of unique contributors, rather than a number of activities.
Contributor-based metrics are useful for answering questions like "how many people are making commits in a given month" or "how many people have interacted with the repository over its history."
The current contributor-based metrics are Contributors by type and Total contributors.

Contributor identity filters let you include or exclude human GitHub accounts, known bots, and unlinked Git identities.
GitHub activity offers human accounts, known bots, and Unknown actors (events with missing performers).
Humans and unlinked identities or unknown actors are included by default, while known bots are excluded; commit counts are unaffected by these filters.
Unlinked Git identities are commit authors or co-authors that could not be linked to a GitHub account, identified using their recorded email address or, when unavailable, their name.
These are estimates of distinct contributors: one person may appear under multiple identities, or different people may share an identity.

The "Contributors by type" metric shows the number of unique contributors that performed any activity in a given month, broken down by activity type.
This is useful for answering questions like "how many people are making pull requests in a given month" or "how many people have raised issues over the history of the repository".

Adding up the contributors for each activity type will double-count contributors that perform multiple types of activities in a month, so the "Total contributors" metric deduplicates contributors across activity types, so that each contributor is counted only once per month, regardless of how many types of activities they performed.
This becomes a metric of all the contributors to the repository in a given month, regardless of whether they opened an issue, commented on a PR, or made a commit.

Comparing with our activity-based metrics, contributor-based metrics are not additive across months.
For Total contributors, selecting only Jan–Dec 2025 and reading the final cumulative value answers a different question from selecting all months and subtracting the Dec 2024 cumulative value from the Dec 2025 value.
In the first case, the question you're asking is, "How many people contributed to the repository in 2025?"
In the second case, the question you're asking is, "How many people contributed to the repository in 2025 who hadn't contributed before?"
Both are valuable metrics, but the first is more about how active your repo is, while the second is more about how your community is growing.

### Other metrics

The other metric is Issue / PR balance, which starts from zero at the selected start month.
Issues use opened + reopened − closed; pull requests use opened − merged.
These are balances of recorded events, not the repository's actual open-item counts.
PR closes without merging and PR reopens are not recorded, so the PR series is not a complete change in the number of open PRs.


## Schemas

JSON schemas in [`src/repo_history/schemas`](src/repo_history/schemas) define the formats of the collected data, collection state, and visualization summaries:

* [`monthly-report-v1.json`](src/repo_history/schemas/monthly-report-v1.json) - the detailed `YYYY-MM.json` reports written by `collect` and `update`, including repository metadata, contributors, individual activities, and monthly summaries.
* [`viz-summary-v1.json`](src/repo_history/schemas/viz-summary-v1.json) - the compact summaries consumed by the visualizer, including those generated by `summarize` or combined with `combine`.
* [`collection-state-v1.json`](src/repo_history/schemas/collection-state-v1.json) - the `collection-state.json` file used to track collection progress and checkpoints for incremental updates.
* [`common-v1.json`](src/repo_history/schemas/common-v1.json) - shared definitions referenced by the other schemas, such as repository metadata, month boundaries, collection runs, and summaries.

## Development

Use the Pixi `dev` environment to work on both the Python collector and the TypeScript visualizer.
From the repository root, install the development environment and the visualizer's npm dependencies:

```sh
pixi install -e dev
pixi run -e dev node-install
```

The Python package is installed in editable mode, so source changes are available immediately when running commands with `pixi run -e dev repo-history`.
Python code and tests are in `src/repo_history` and `tests`; visualizer code and tests are in `repo-history-viz/src` and `repo-history-viz/tests`.

Run the unit tests and apply formatting with:

```sh
pixi run -e dev python-test
pixi run -e dev node-test
pixi run -e dev python-format
pixi run -e dev node-format
```

For the broader checks used in CI, including formatting checks, Python linting and coverage, and package builds, run:

```sh
pixi run -e dev python-ci
pixi run -e dev node-ci
```

The visualizer also has Playwright browser tests. Install Chromium once, then run them with:

```sh
pixi run -e dev node-browser-install
pixi run -e dev node-test-browser
```

To work on the visualizer interactively, start its development server and open the local URL it prints:

```sh
pixi run -e dev npm --prefix repo-history-viz run dev
```

See the [visualizer README](repo-history-viz/README.md#development-builds-and-distribution) for build outputs and additional development commands.

## License

[MIT](LICENSE).

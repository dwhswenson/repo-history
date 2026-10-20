# Repository history visualization

`<repo-history-viz>` is a framework-independent Web Component for self-contained history summaries produced offline by `repo-history`. It includes activity comparisons, monthly bars, cumulative lines, unique contributor counts, month-range controls, and CSV data downloads. Chart.js, summary validation schema, and styles are bundled. No backend or GitHub credentials are needed.

## Use in any static site

Build from this repository with `npm ci && npm run build` inside `repo-history-viz`. Copy **`dist/repo-history-viz.js`** to your site's static assets. This is a single self-contained ES module, with no separate stylesheet or runtime dependencies to host.

```html
<script type="module" src="/assets/repo-history-viz.js"></script>
<repo-history-viz
  summary="/reports/summary.json"
  metrics="github-activities commits contributors-by-type total-contributors balance"
  hide-loader
></repo-history-viz>
```

Generate a summary from validated monthly reports, then publish only the summary and visualizer bundle:

```sh
repo-history summarize --input-directory reports
# Optional destination:
repo-history summarize --input-directory reports --output public/reports/summary.json
```

The summary contains a required `display_name`, a `repositories` registry, one shared actor-classification lookup, and prepared monthly summaries with per-repository source summaries and coverage metadata. It omits individual events and actor profile fields. Hosted loading makes one credential-free HTTP(S) request; cross-origin summaries require CORS. Relative summary URLs resolve against the host document's base URL. Local file selection reads one summary in browser memory without uploading it.

Regenerate the summary explicitly after adding, replacing, or deleting monthly reports. Collect and update do not regenerate it automatically. Source reports must remain unchanged during generation. The summary is atomically replaced only after every source report and the completed summary validate.

See `examples/embed.html` for a complete host page.

## npm and Astro

The npm entry point exports the class and an explicit registration function. The registration entry point registers the standard tag automatically:

```js
import "repo-history-viz/register";
```

For Astro, install the package (or a local packed tarball), then use a normal processed client script. No `client:*` directive or framework integration is required:

```astro
---
const summary = `${import.meta.env.BASE_URL}reports/summary.json`;
---
<repo-history-viz summary={summary} metrics="commits github-activities" hide-loader></repo-history-viz>
<script>
  import "repo-history-viz/register";
</script>
```

A reusable wrapper is provided in `examples/astro/RepositoryHistory.astro`. The package can be imported during server-side rendering without accessing the DOM; registration is a no-op without `customElements`. The interactive viewer is created only in the browser.

## Hugo

Copy `dist/repo-history-viz.js` to `static/js/repo-history-viz.js`. Put your generated summary in `static/reports/`. Copy `examples/hugo/layouts/shortcodes/repo-history.html` into your site's `layouts/shortcodes/`, then embed it in content:

```go-html-template
{{< repo-history summary="reports/summary.json" metrics="commits github-activities" >}}
```

The shortcode uses Hugo's `relURL` for deployment subpaths. Hugo only needs the prebuilt bundle, so its build does not require Node.

## Component API

| Attribute     | Behavior                                                                                                                                                                                                               |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `metrics`     | Required whitespace-separated list: `commits`, `github-activities`, `contributors-by-type`, `total-contributors`, `balance`. Only listed metrics are offered, in the specified order; the first is initially selected. |
| `summary`     | Automatically loads the given summary on connection and when changed. Removing or emptying it cancels a pending request and retains the displayed data.                                                                |
| `hide-loader` | Boolean attribute hiding the file picker and URL controls; status and errors remain visible.                                                                                                                           |

Missing, empty, or unknown `metrics` values show a configuration error and no chart, and emit `repo-history-error`. There is no implicit default or wildcard. Duplicate names are included once. Changing the attribute updates the available metrics without reloading reports or resetting the month range; the current selection is retained if still enabled, otherwise the first listed metric is selected. Invalid configuration hides the chart while retaining reports, so correcting the attribute restores the viewer. Configuration errors remain visible with `hide-loader`.

Each instance has an open shadow root, independent controls, data, and chart. Host-page CSS does not style its internals. Responsive layout follows the component's container width. Disconnecting aborts pending loads and destroys the chart; reconnecting redraws retained data and reloads a nonempty `summary` attribute. Multiple instances can coexist on one page.

For explicit registration or programmatic loading:

```ts
import {
  defineRepoHistoryViz,
  type HistorySummarySource,
} from "repo-history-viz";

defineRepoHistoryViz(); // Idempotent.
await customElements.whenDefined("repo-history-viz");
const viewer = document.querySelector("repo-history-viz")!;
viewer.setAttribute("metrics", "commits total-contributors");
await viewer.loadSummary("/reports/summary.json");

const source: HistorySummarySource = {
  name: "summary.json",
  text: JSON.stringify(historySummary),
};
await viewer.loadSummarySource(source);
```

Call methods after the element is connected. Both loading methods return `Promise<void>`, reject on failure, and retain the previous dataset on failure. The latest load supersedes earlier loads; superseded or disconnected loads are cancelled without an error event and their promises resolve without changing data. Successful replacement resets the range to all loaded months and preserves other controls. `loadSummary()` does not change the attribute. `HistorySummary`, `MonthSummary`, and `HistorySummarySource` are exported TypeScript types.

Events bubble and are composed, so the host document can listen:

| Event                | `event.detail`                                                                                                                                   |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `repo-history-load`  | `{ display_name: string, repositories: { node_id: string, full_name: string, url: string }[], months: string[] }`, with chronological month keys |
| `repo-history-error` | `{ message: string }`                                                                                                                            |

Theme variables can be set on the element:

```css
repo-history-viz {
  --repo-history-accent: #0f766e;
  --repo-history-background: #f4f6fa;
  --repo-history-surface: white;
  --repo-history-color: #22304b;
  --repo-history-font: system-ui, sans-serif;
  --repo-history-radius: 8px;
}
```

The accent controls primary buttons and selection indicators. Activity series use selected [IBM Carbon colors](https://v10.carbondesignsystem.com/data-visualization/color-palettes/), shared by solid bars, legend keys, and control swatches. PR opens use Purple 80 and PR reviews use Purple 50. Cumulative lines have no visible point markers by default; points appear on hover. Balance uses solid blue for issues and solid orange for PRs. Total contributors also uses a solid line. PNG exports preserve these styles. The default theme uses light surfaces and dark text.

Use **Download PNG** above the chart to save its current plot, axes, and legend at the displayed resolution. The filename includes the dataset display name, metric, chart mode, and month range. PNGs have a transparent background. **Download Data (CSV)** below it exports the same filtered chart data: one row per month with report status and one column per displayed series. Missing reports are marked “No report”; collected months with no activity remain zero.

## Counting and validation

- Activities count each recorded event once, including co-authored commits, bots, and unattributed events.
- Contributors are unique non-null credited actor keys. Contextual subject authors are excluded. Humans and unlinked Git identities are selected by default; classification controls affect contributor counts and GitHub activity counts.
- The Total contributors metric shows one deduplicated series across all activity types, independent of activity type selections. Monthly bars count identities active that month; cumulative lines union identities since the selected start month. Contributor identity filters apply to both.
- Combined activity counts sum events. Combined contributor counts union identities across selected types. Contributor series overlap and must not be added together.
- Metric is a dropdown: Commits, GitHub activity (all selected event types except commits), Contributors by type, Total contributors, and Issue / PR balance. Only controls relevant to the selected metric are shown.
- Monthly GitHub activity bars stack selected activity types; the optional combined count appears as a separate line. Monthly contributors by type use side-by-side bars because identities overlap across types. Cumulative lines accumulate events or union identities from the selected start month. Month endpoints are inclusive, in UTC.
- Issue / PR balance uses cumulative mode only, with separate Issues and Pull requests toggles. Issues add opens and reopens and subtract closes; PRs add opens and subtract merges. Both start at zero at the selected start month, preserve negative values, and cover loaded months only. They show net change rather than the repository’s actual open totals. The current report schema does not record PR closes without merging or PR reopens, so the PR series is an opened-minus-merged balance.
- The month range uses a double-ended slider with one-month steps and live chart updates. Drag either handle, click the track to move the nearest endpoint, or focus a handle and use the arrow keys. Handles cannot cross; “All available months” resets both endpoints. A single-report range disables the handles.
- Empty reports are zero activity; absent reports are gaps. Cumulative totals cover loaded months only and resume from the last observed total. Partial months contain data only through their collection snapshot.
- Unlinked Git identities are estimates; the viewer does not infer identity merges. Stable keys survive account renames.
- Summaries must pass the shared visualization-summary v1 schema, have unique months and valid summary actor references, and contain internally consistent counts and populations. The offline generator validates full source reports, rejects mixed repositories and conflicting participating actor classifications, and uses the latest reporting month’s repository name. The browser validates the entire summary before replacing the dataset.

## Development, builds, and distribution

Requires Node 22+. From the repository root, use `pixi run -e node22 node-install`
then `pixi run -e node22 node-ci` for formatting, unit tests, builds, and an npm
package preview. Run `pixi run -e node22 node-browser-install` once, then
`pixi run -e node22 node-test-browser` for Chromium tests. The `node24` and
`node26` environments run the same tasks on those versions. CI runs all three.

The equivalent npm commands inside `repo-history-viz` are:

```sh
npm ci
npm run dev           # Standalone demo using the component
npm test
npm run test:browser  # Requires Chromium; run npx playwright install chromium once
npm run format:check
npm run build         # Library, standalone bundle, and TypeScript declarations
npm run build:demo    # Optional standalone website in demo-dist/
npm run preview      # Preview demo-dist/
npm pack             # Builds and creates repo-history-viz-0.1.0.tgz
```

Build outputs:

- `dist/index.js`: bundled npm ES module, explicit registration API.
- `dist/repo-history-viz.js`: bundled auto-registering ES module for static hosts or the `/register` export.
- `dist/types/`: TypeScript declarations, including the custom element's DOM typing.

`npm pack` includes only `dist/`, documentation, examples, and npm metadata. Install the tarball into a consuming project with `npm install /path/to/repo-history-viz-0.1.0.tgz`. Consumers do not need this repository or the Python package. Source builds use the canonical schema from the sibling Python package.

The package is prepared for npm publication but is not published automatically. Before a registry release, confirm the package name/version with the project owner, run the checks above, inspect `npm pack --dry-run`, then publish through the project's release process.

Browser tests cover local and hosted summary loading, chart controls, range validation, mobile layout, isolated instances, disconnect/reconnect, changing summaries, events, and the built standalone bundle. Browser tests build the package automatically before exercising packaged assets. Set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to reuse an existing Chromium executable. Browser output is ignored under `test-results/`.

GitHub activity supports the same identity filter defaults as contributor counts: humans and unknown actors included, known bots excluded. Events are classified by their credited performer (including the closer or merger), with missing actors counted as Unknown actors. The filter applies to monthly and cumulative charts and their downloads. Commit counts and issue / PR balance are unaffected.

## License

MIT. See [LICENSE](LICENSE).

## Summary contract

Python and browser validators resolve the packaged shared schema locally.

`display_name` supplies the heading and download filename. For multiple repositories,
an expandable **Included repositories** list shows their names and links. The
viewer consumes precomputed `months[*].summaries`; it does not merge input files.
`source_reports` retains repository-specific metadata and summaries for validation
and coverage warnings. Contributor counts union actor keys across repositories;
event counts sum source counts. Coverage messages identify their source repository.

The contract accepts prepared multi-repository summaries, including partial sources.
Missing sources require known pre-history evidence; all other gaps fail validation
for multiple repositories. Single-repository summaries retain gap visualization.
The Python `summarize` command accepts one repository, with optional `--display-name`.
Use `repo-history combine repo-a repo-b/summary.json -o combined.json --display-name "Project activity"`
to prepare one dataset from disjoint repository sets. Inputs accept summary files
or directories containing `summary.json`. Retained months require continuous,
complete source coverage except proven pre-history omissions. The command excludes
an entire calendar month when any source is partial unless `--include-partial` is
supplied. Collection state files contain private-to-the-dataset resume metadata and
are not part of browser input.

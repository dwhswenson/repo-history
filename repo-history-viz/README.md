# Repository history visualization

`<repo-history-viz>` is a framework-independent Web Component for monthly v1 JSON reports produced by `repo-history`. It includes activity comparisons, monthly bars, cumulative lines, unique contributor counts, month-range controls, and CSV data downloads. Chart.js, validation schema, and styles are bundled. No backend or GitHub credentials are needed.

## Use in any static site

Build from this repository with `npm ci && npm run build` inside `repo-history-viz`. Copy **`dist/repo-history-viz.js`** to your site's static assets. This is a single self-contained ES module, with no separate stylesheet or runtime dependencies to host.

```html
<script type="module" src="/assets/repo-history-viz.js"></script>
<repo-history-viz
  manifest="/reports/manifest.json"
  hide-loader
></repo-history-viz>
```

Serve a manifest alongside your reports:

```json
{ "files": ["2024-01.json", "2024-02.json", "2024-03.json"] }
```

Report paths resolve relative to the manifest's final URL; relative manifest URLs resolve against the host document's base URL. Use paths appropriate to your site's deployment subpath. Cross-origin manifests and reports require CORS; fetches omit credentials. Local file selection processes files in browser memory without uploading them. Published full reports can include contributor Git names and emails.

See `examples/embed.html` for a complete host page.

## npm and Astro

The npm entry point exports the class and an explicit registration function. The registration entry point registers the standard tag automatically:

```js
import "repo-history-viz/register";
```

For Astro, install the package (or a local packed tarball), then use a normal processed client script. No `client:*` directive or framework integration is required:

```astro
---
const manifest = `${import.meta.env.BASE_URL}reports/manifest.json`;
---
<repo-history-viz manifest={manifest} hide-loader></repo-history-viz>
<script>
  import "repo-history-viz/register";
</script>
```

A reusable wrapper is provided in `examples/astro/RepositoryHistory.astro`. The package can be imported during server-side rendering without accessing the DOM; registration is a no-op without `customElements`. The interactive viewer is created only in the browser.

## Hugo

Copy `dist/repo-history-viz.js` to `static/js/repo-history-viz.js`. Put your manifest and reports in `static/reports/`. Copy `examples/hugo/layouts/shortcodes/repo-history.html` into your site's `layouts/shortcodes/`, then embed it in content:

```go-html-template
{{< repo-history manifest="reports/manifest.json" >}}
```

The shortcode uses Hugo's `relURL` for deployment subpaths. Hugo only needs the prebuilt bundle, so its build does not require Node.

## Component API

| Attribute     | Behavior                                                                                                                                                 |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `manifest`    | Automatically loads the given manifest on connection and when changed. Removing or emptying it cancels a pending request and retains the displayed data. |
| `hide-loader` | Boolean attribute hiding the file picker and URL controls; status and errors remain visible.                                                             |

Each instance has an open shadow root, independent controls, data, and chart. Host-page CSS does not style its internals. Responsive layout follows the component's container width. Disconnecting aborts pending loads and destroys the chart; reconnecting redraws retained data and reloads a nonempty `manifest` attribute. Multiple instances can coexist on one page.

For explicit registration or programmatic loading:

```ts
import { defineRepoHistoryViz, type ReportSource } from "repo-history-viz";

defineRepoHistoryViz(); // Idempotent.
await customElements.whenDefined("repo-history-viz");
const viewer = document.querySelector("repo-history-viz")!;
await viewer.loadManifest("/reports/manifest.json");

const sources: ReportSource[] = [
  { name: "2024-02.json", text: JSON.stringify(monthlyReport) },
];
await viewer.loadReports(sources);
```

Call methods after the element is connected. Both return `Promise<void>`, reject on failure, and retain the previous dataset on failure. The latest load supersedes earlier loads; superseded or disconnected loads are cancelled without an error event and their promises resolve without changing data. Successful replacement resets the range to all loaded months and preserves other controls. `loadManifest()` does not change the attribute.

Events bubble and are composed, so the host document can listen:

| Event                | `event.detail`                                                            |
| -------------------- | ------------------------------------------------------------------------- |
| `repo-history-load`  | `{ repository: string, months: string[] }`, with chronological month keys |
| `repo-history-error` | `{ message: string }`                                                     |

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

Use **Download PNG** above the chart to save its current plot, axes, and legend at the displayed resolution. The filename includes the repository, measure, chart mode, and month range. PNGs have a transparent background. **Download Data (CSV)** below it exports the same filtered chart data: one row per month with report status and one column per displayed series. Missing reports are marked “No report”; collected months with no activity remain zero.

## Counting and validation

- Activities count each recorded event once, including co-authored commits, bots, and unattributed events.
- Contributors are unique non-null credited actor keys. Contextual subject authors are excluded. Humans and unresolved Git identities are selected by default; classification controls affect contributor counts only.
- The Total contributors measure shows one deduplicated series across all contribution types, independent of contribution type selections. Monthly bars count identities active that month; cumulative lines union identities since the selected start month. Contributor identity filters apply to both.
- Combined activity counts sum events. Combined contributor counts union identities across selected types. Contributor series overlap and must not be added together.
- Measure is a dropdown: Commits, GitHub Activities (all selected event types except commits), Contributors by type, Total contributors, and Issue / PR balance. Only controls relevant to the selected measure are shown.
- Monthly GitHub activity bars stack selected contribution types; the optional combined count appears as a separate line. Monthly contributors by type use side-by-side bars because identities overlap across types. Cumulative lines accumulate events or union identities from the selected start month. Month endpoints are inclusive, in UTC.
- Issue / PR balance uses cumulative mode only, with separate Issues and Pull requests toggles. Issues add opens and reopens and subtract closes; PRs add opens and subtract merges. Both start at zero at the selected start month, preserve negative values, and cover loaded months only. They show net change rather than the repository’s actual open totals. The current report schema does not record PR closes without merging or PR reopens, so the PR series is an opened-minus-merged balance.
- The month range uses a double-ended slider with one-month steps and live chart updates. Drag either handle, click the track to move the nearest endpoint, or focus a handle and use the arrow keys. Handles cannot cross; “All available months” resets both endpoints. A single-report range disables the handles.
- Empty reports are zero activity; absent reports are gaps. Cumulative totals cover loaded months only and resume from the last observed total. Ongoing months are partial snapshots.
- Unresolved Git identities are estimates; the viewer does not infer identity merges. Stable keys survive account renames.
- Reports must pass the shared v1 schema, share one repository node ID, have unique months, and reference valid credited actors. Repository renames are accepted. Conflicting actor classifications are rejected. A load succeeds only if all reports validate.

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

The package is prepared for npm publication but is not published automatically. Before a registry release, confirm the package name/version and license with the project owner, run the checks above, inspect `npm pack --dry-run`, then publish through the project's release process.

Browser tests cover file and manifest loading, chart controls, range validation, mobile layout, isolated instances, disconnect/reconnect, changing manifests, events, and the built standalone bundle. Browser tests build the package automatically before exercising packaged assets. Set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to reuse an existing Chromium executable. Browser output is ignored under `test-results/`.

GitHub Activities supports the same identity filter defaults as contributor counts: humans and undetermined actors included, known bots excluded. Events are classified by their credited performer (including the closer or merger), with missing actors counted as Undetermined. The filter applies to monthly and cumulative charts and their downloads. Commit counts and issue / PR balance are unaffected.

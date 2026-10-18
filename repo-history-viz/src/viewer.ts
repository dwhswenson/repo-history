import styles from "./style.css?inline";
import {
  ACTIVITY_LABELS,
  ACTIVITY_TYPES,
  loadManifest,
  nextMonth,
  parseManifest,
  type ActivityType,
  type Classification,
  type Manifest,
  type MonthSummary,
  type ManifestSource,
} from "./report.js";
import { aggregate, type ViewState, type PlotData } from "./series.js";
import { chartCsv } from "./csv.js";
import { COLORS, renderChart } from "./chart.js";
import type { Chart } from "chart.js";

const MEASURES = {
  commits: "Commits",
  "github-activities": "GitHub Activities",
  contributors: "Contributors by type",
  "total-contributors": "Total contributors",
  balance: "Issue / PR balance",
} as const;
type Measure = keyof typeof MEASURES;

export function createViewer(
  root: ShadowRoot,
  notify: (type: string, detail: unknown) => void,
) {
  const sheet = document.createElement("style");
  sheet.textContent = styles;
  root.append(sheet);
  const app = document.createElement("div");
  app.className = "viewer";
  root.append(app);
  app.innerHTML = `
  <main>
    <p id="configuration-error" class="error card" role="alert" hidden></p>
    <section class="loader card" aria-labelledby="load-heading">
      <div><h2 id="load-heading">Load your manifest</h2><p>Explore monthly GitHub activity across a selected set of repositories.</p></div>
      <div class="load-options">
        <label class="file-button">Choose manifest JSON<input id="files" type="file" accept=".json,application/json" aria-label="Choose manifest JSON"></label>
        <span class="or">or</span>
        <form id="manifest-form"><label for="manifest">Hosted manifest URL</label><div class="url-row"><input id="manifest" type="text" placeholder="https://example.org/reports/manifest.json" required><button id="load-url" type="submit">Load URL</button></div></form>
      </div>
      <p id="load-status" role="status" aria-live="polite">Local files are processed in your browser.</p>
      <p id="load-error" class="error" role="alert" hidden></p>
    </section>
    <section id="empty" class="empty card"><div class="empty-icon" aria-hidden="true">▥</div><h2>A clearer view of repository activity</h2><p>Choose a manifest file or load a hosted manifest to compare contributions and discover how your contributor community grows.</p><div class="empty-tags"><span>Monthly activity</span><span>Cumulative trends</span><span>Unique contributors</span></div></section>
    <section id="dashboard" hidden>
      <div class="repository-heading"><div><p class="eyebrow">REPOSITORY HISTORY</p><h2 id="repository-name"></h2></div><p id="report-summary"></p></div>
      <div class="workspace">
        <aside class="controls card" aria-label="Chart controls">
          <fieldset id="month-controls"><legend>Month range</legend><div class="month-range-values"><label for="start">From<output id="start-label" for="start"></output></label><label for="end">Through<output id="end-label" for="end"></output></label></div><div id="month-range" class="month-range"><div class="month-range-track" aria-hidden="true"><span id="month-selection"></span></div><input id="start" type="range" min="0" max="0" step="1" value="0"><input id="end" type="range" min="0" max="0" step="1" value="0"></div><button id="reset-range" class="text-button" type="button">All available months</button></fieldset>
          <fieldset><legend><label for="metric">Measure</label></legend><select id="metric"></select></fieldset>
          <fieldset><legend>Chart</legend><div class="segmented"><label><input type="radio" name="mode" value="monthly" checked><span>Monthly</span></label><label><input type="radio" name="mode" value="cumulative"><span>Cumulative</span></label></div></fieldset>
          <fieldset id="contribution-types"><legend>Contribution types</legend><div class="selection-actions"><button id="select-all" class="text-button" type="button">Select all</button><button id="select-none" class="text-button" type="button">Clear</button></div><div class="type-list">${ACTIVITY_TYPES.map((type, index) => `<label><input type="checkbox" name="activity" value="${type}" checked><span class="swatch" style="background:${COLORS[index]}"></span>${ACTIVITY_LABELS[type]}</label>`).join("")}</div><label class="combined"><input id="combined" type="checkbox">Show selected types combined</label></fieldset>
          <fieldset id="balance-types" hidden><legend>Show balance for</legend><label><input type="checkbox" name="balance-type" value="issues" checked>Issues</label><label><input type="checkbox" name="balance-type" value="prs" checked>Pull requests</label></fieldset>
          <fieldset id="classifications" hidden><legend id="classification-legend">Contributor identities</legend><label><input type="checkbox" name="classification" value="human" checked>Human GitHub accounts</label><label><input type="checkbox" name="classification" value="unresolved_git" checked><span id="undetermined-label">Unresolved Git identities</span></label><label><input type="checkbox" name="classification" value="bot">Known bots</label></fieldset>
        </aside>
        <div class="results">
          <section class="chart-card card" aria-labelledby="chart-title"><div class="chart-heading"><div><p class="eyebrow" id="chart-subtitle"></p><h2 id="chart-title"></h2></div><div class="chart-downloads"><button id="download-chart" class="text-button" type="button">Download PNG</button><button id="download-data" class="text-button" type="button">Download Data (CSV)</button></div></div><p id="chart-note" class="chart-note"></p><p id="warnings" class="warning" role="status" hidden></p><p id="no-series" class="warning" hidden>Select at least one contribution type to draw a chart.</p><div class="chart-wrap"><canvas id="chart" role="img" aria-label="Repository activity chart. Exact values are available through Download Data (CSV)."></canvas></div></section>
          <details id="repository-details" class="card coverage-card" hidden><summary>Included repositories</summary><ul id="repository-list"></ul></details><details class="card coverage-card"><summary>About these counts and report coverage</summary><p>GitHub activity counts follow the actor filters, excluding known bots by default. Undetermined includes events with missing actors. Commit counts include all recorded commits. Contributor counts include only credited identities matching your filters. Contextual subject authors do not receive credit.</p><p>Unresolved Git identities are estimates and may represent the same person as another identity. A known account is tracked by its stable actor key, even if its login changes. Cumulative contributors are a union of identities, not a sum of monthly totals.</p><p>Missing reports are gaps, not zero activity. Cumulative charts resume from the last observed total and cover loaded months only. Ongoing months are partial snapshots.</p><ul id="coverage-list"></ul></details>
        </div>
      </div>
    </section>
  </main>
`;

  function element<T extends HTMLElement>(id: string): T {
    return root.getElementById(id) as T;
  }
  const files = element<HTMLInputElement>("files");
  const start = element<HTMLInputElement>("start");
  const end = element<HTMLInputElement>("end");
  const state: ViewState = {
    metric: "github-activities",
    mode: "monthly",
    types: [...ACTIVITY_TYPES],
    classifications: ["human", "unresolved_git"],
    combined: false,
    start: "",
    end: "",
  };
  let measures: Measure[] = [];
  let reports: MonthSummary[] = [];
  let manifest: Manifest | undefined;
  let months: string[] = [];
  let generation = 0;
  let request: AbortController | undefined;
  let plot: PlotData | undefined;
  let chart: Chart<"line" | "bar", (number | null)[], string> | undefined;

  function setMessage(id: string, message: string) {
    const target = element(id);
    target.textContent = message;
    target.hidden = !message;
  }

  function setMeasures(value: string | null) {
    const requested = value?.trim().split(/\s+/).filter(Boolean) ?? [];
    const unknown = requested.filter((key) => !Object.hasOwn(MEASURES, key));
    const message = !requested.length
      ? 'Set the "measures" attribute to explicitly enable at least one measure.'
      : unknown.length
        ? `Unknown measures: ${unknown.join(", ")}. Supported measures: ${Object.keys(MEASURES).join(", ")}.`
        : "";
    measures = message ? [] : ([...new Set(requested)] as Measure[]);
    const select = element<HTMLSelectElement>("metric");
    const previous = select.value;
    select.replaceChildren(
      ...measures.map((key) => new Option(MEASURES[key], key)),
    );
    if (measures.includes(previous as Measure)) select.value = previous;
    setMessage("configuration-error", message);
    render();
    if (message) notify("repo-history-error", { message });
  }

  function showDataset(incoming: Manifest) {
    manifest = incoming;
    reports = incoming.months;
    const first = reports[0]!.metadata.month;
    const last = reports.at(-1)!.metadata.month;
    months = [];
    for (let month = first; month <= last; month = nextMonth(month)) {
      months.push(month);
      if (month === last) break;
    }
    for (const input of [start, end]) {
      input.max = String(months.length - 1);
      input.disabled = months.length === 1;
    }
    start.value = "0";
    end.value = String(months.length - 1);
    element("empty").hidden = true;
    element("dashboard").hidden = false;
    element("repository-name").textContent = manifest!.display_name;
    const details = element("repository-details");
    details.hidden = manifest!.repositories.length < 2;
    element("repository-list").replaceChildren(
      ...manifest!.repositories.map((repo) => {
        const li = document.createElement("li"),
          link = document.createElement("a");
        link.textContent = repo.full_name;
        link.href = repo.url;
        li.append(link);
        return li;
      }),
    );
    element("report-summary").textContent =
      `${reports.length} monthly report${reports.length === 1 ? "" : "s"} · ${first} — ${last}`;
    render();
  }

  function cancel() {
    generation++;
    if (request) {
      request.abort();
      setMessage(
        "load-status",
        reports.length
          ? "The previous dataset is still available."
          : "Loading cancelled.",
      );
    }
    request = undefined;
    files.disabled = false;
    element<HTMLButtonElement>("load-url").disabled = false;
  }

  async function load(action: (signal: AbortSignal) => Promise<Manifest>) {
    cancel();
    const current = generation;
    request = new AbortController();
    files.disabled = true;
    element<HTMLButtonElement>("load-url").disabled = true;
    setMessage("load-error", "");
    setMessage("load-status", "Loading and validating manifest…");
    try {
      const incoming = await action(request.signal);
      if (current !== generation) return;
      showDataset(incoming);
      setMessage("load-status", "");
      notify("repo-history-load", {
        display_name: manifest!.display_name,
        repositories: manifest!.repositories.map(
          ({ node_id, full_name, url }) => ({ node_id, full_name, url }),
        ),
        months: reports.map((report) => report.metadata.month),
      });
    } catch (error) {
      if (current !== generation) return;
      const message =
        error instanceof Error ? error.message : "Unable to load manifest.";
      setMessage("load-error", message);
      setMessage(
        "load-status",
        reports.length
          ? "The previous dataset is still available."
          : "Unable to load manifest.",
      );
      notify("repo-history-error", { message });
      throw error;
    } finally {
      if (current === generation) {
        request = undefined;
        files.disabled = false;
        files.value = "";
        element<HTMLButtonElement>("load-url").disabled = false;
      }
    }
  }

  function loadUrl(url: string) {
    element<HTMLInputElement>("manifest").value = url;
    return load((signal) =>
      loadManifest(url, root.host.baseURI, fetch, signal),
    );
  }

  files.addEventListener("change", () => {
    const selected = files.files?.[0];
    if (selected)
      void load(async () =>
        parseManifest({ name: selected.name, text: await selected.text() }),
      ).catch(() => {});
  });
  element("manifest-form").addEventListener("submit", (event) => {
    event.preventDefault();
    void loadUrl(element<HTMLInputElement>("manifest").value.trim()).catch(
      () => {},
    );
  });

  function checked<T extends string>(name: string): T[] {
    return [
      ...root.querySelectorAll<HTMLInputElement>(
        `input[name="${name}"]:checked`,
      ),
    ].map((input) => input.value as T);
  }

  function render() {
    element("empty").hidden = !measures.length || reports.length > 0;
    element("dashboard").hidden = !measures.length || !reports.length;
    if (!measures.length || !reports.length) {
      chart?.destroy();
      chart = undefined;
      plot = undefined;
      return;
    }
    state.metric = element<HTMLSelectElement>("metric")
      .value as ViewState["metric"];
    state.mode = checked<ViewState["mode"]>("mode")[0]!;
    state.types = checked<ActivityType>("activity");
    state.classifications = checked<Classification>("classification");
    state.combined = element<HTMLInputElement>("combined").checked;
    const totalContributors = state.metric === "total-contributors";
    const balance = state.metric === "balance";
    const contributors = state.metric === "contributors" || totalContributors;
    state.balanceTypes = checked<"issues" | "prs">("balance-type");
    const monthly = root.querySelector<HTMLInputElement>(
      'input[name="mode"][value="monthly"]',
    )!;
    monthly.disabled = balance;
    if (balance) {
      root.querySelector<HTMLInputElement>(
        'input[name="mode"][value="cumulative"]',
      )!.checked = true;
      state.mode = "cumulative";
    }
    const githubActivities = state.metric === "github-activities";
    element("classifications").hidden = !(contributors || githubActivities);
    element("classification-legend").textContent = githubActivities
      ? "Activity actors"
      : "Contributor identities";
    element("undetermined-label").textContent = githubActivities
      ? "Undetermined"
      : "Unresolved Git identities";
    element("balance-types").hidden = !balance;
    element("contribution-types").hidden =
      totalContributors || balance || state.metric === "commits";
    const commit = root.querySelector<HTMLInputElement>(
      'input[name="activity"][value="commit"]',
    )!;
    commit.closest("label")!.hidden = state.metric === "github-activities";
    state.start = months[start.valueAsNumber]!;
    state.end = months[end.valueAsNumber]!;
    for (const [input, month, id] of [
      [start, state.start, "start-label"],
      [end, state.end, "end-label"],
    ] as const) {
      const [year, number] = month.split("-");
      const label = new Intl.DateTimeFormat("en", {
        month: "short",
        year: "numeric",
        timeZone: "UTC",
      }).format(new Date(Date.UTC(Number(year), Number(number) - 1)));
      element(id).textContent = label;
      input.setAttribute("aria-valuetext", label);
    }
    const lastIndex = months.length - 1;
    const selection = element("month-selection");
    selection.style.left = `${lastIndex ? (start.valueAsNumber / lastIndex) * 100 : 0}%`;
    selection.style.right = `${lastIndex ? 100 - (end.valueAsNumber / lastIndex) * 100 : 0}%`;
    start.style.zIndex = start.valueAsNumber === lastIndex ? "4" : "2";
    const data = aggregate(manifest!, state);
    plot = data;
    const cumulative = state.mode === "cumulative";
    const measureLabel = {
      activities: "activity",
      commits: "commits",
      "github-activities": "GitHub activity",
      contributors: "contributors by type",
      "total-contributors": "contributors",
      balance: "issue / PR balance",
    }[state.metric];
    element("chart-title").textContent =
      `${cumulative ? "Cumulative" : "Monthly"} ${measureLabel}`;
    element("chart-subtitle").textContent = balance
      ? "OPENING AND CLOSING"
      : contributors
        ? "COMMUNITY PARTICIPATION"
        : "CONTRIBUTION VOLUME";
    element("chart-note").textContent = balance
      ? "Net change from zero at the selected start month, not the repository’s total open items. Issues: opened + reopened − closed. PRs: opened − merged; reports omit PR closes without merging and PR reopens. Negative values are possible."
      : totalContributors
        ? `${cumulative ? "Unique credited identities across all contribution types since the selected start month." : "Unique credited identities active across all contribution types in each month."} This includes everyone from commits to comments. Each identity counts once, even when contributing in multiple types.`
        : contributors
          ? `${cumulative ? "Unique credited identities since the selected start month." : "Side-by-side bars show unique credited identities per contribution type in each month."} Combined counts deduplicate across selected types.`
          : state.metric === "commits"
            ? `${cumulative ? "Running commit totals starting at the selected month." : "Recorded commits in each month."} Each commit counts once, including co-authored commits.`
            : `${cumulative ? "Running event totals starting at the selected month." : "Stacked bars show recorded events in each month across selected types."} GitHub activity excludes commits and follows the actor filters.`;
    element("no-series").textContent = balance
      ? "Select Issues, Pull requests, or both to draw a chart."
      : "Select at least one contribution type to draw a chart.";
    const warnings: string[] = [];
    if (data.missing.length)
      warnings.push(
        `Missing reports: ${data.missing.join(", ")}. ${cumulative ? "Cumulative totals cover loaded months only." : "Gaps indicate unknown activity."}`,
      );
    for (const month of data.partial) {
      const sources = reports.find(
        (report) => report.metadata.month === month,
      )!.source_reports;
      for (const { metadata } of sources.filter(
        (source) => source.metadata.ongoing,
      )) {
        const through = metadata.coverage.as_of.slice(0, 10);
        const name =
          manifest!.repositories.length > 1
            ? `${metadata.repository.full_name}: `
            : "";
        warnings.push(
          `${name}Partial month: ${metadata.month_start.slice(0, 10)} — ${through} (UTC).`,
        );
      }
    }

    if ((contributors || githubActivities) && !state.classifications.length)
      warnings.push(
        "No identity categories selected; counts are zero for loaded months.",
      );
    setMessage("warnings", warnings.join(" "));
    element("no-series").hidden = data.series.length > 0;
    chart?.destroy();
    chart = renderChart(element<HTMLCanvasElement>("chart"), data, state);
    for (const id of ["download-chart", "download-data"]) {
      element<HTMLButtonElement>(id).disabled = data.series.length === 0;
    }

    const limitations = [
      ...new Set(
        reports
          .filter(
            (report) =>
              report.metadata.month >= state.start &&
              report.metadata.month <= state.end,
          )
          .flatMap((report) =>
            report.source_reports.flatMap(({ metadata }) =>
              metadata.coverage.limitations.map((message) =>
                manifest!.repositories.length > 1
                  ? `${metadata.repository.full_name}: ${message}`
                  : message,
              ),
            ),
          ),
      ),
    ];
    element("coverage-list").replaceChildren(
      ...limitations.map((message) => {
        const item = document.createElement("li");
        item.textContent = message;
        return item;
      }),
    );
  }

  function download(url: string, extension: string) {
    const repository = manifest!.display_name.replace(/[^a-zA-Z0-9_-]/g, "-");
    const link = document.createElement("a");
    link.download = `${repository}-${state.metric}-${state.mode}-${state.start}-to-${state.end}.${extension}`;
    link.href = url;
    link.click();
  }

  element("download-chart").addEventListener("click", () => {
    if (chart && reports.length)
      download(chart.toBase64Image("image/png"), "png");
  });
  element("download-data").addEventListener("click", () => {
    if (!plot || !reports.length) return;
    const url = URL.createObjectURL(
      new Blob([chartCsv(plot)], { type: "text/csv;charset=utf-8" }),
    );
    download(url, "csv");
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  function changeRange(input: HTMLInputElement) {
    if (start.valueAsNumber > end.valueAsNumber) {
      input.value = input === start ? end.value : start.value;
    }
    render();
  }
  for (const input of [start, end]) {
    input.addEventListener("input", () => changeRange(input));
    input.addEventListener("change", () => changeRange(input));
  }
  element("month-range").addEventListener("click", (event) => {
    if (event.target === start || event.target === end || months.length < 2)
      return;
    const track = root
      .querySelector(".month-range-track")!
      .getBoundingClientRect();
    const offset = Math.round(
      Math.max(0, Math.min(1, (event.clientX - track.left) / track.width)) *
        (months.length - 1),
    );
    const input =
      Math.abs(offset - start.valueAsNumber) <=
      Math.abs(offset - end.valueAsNumber)
        ? start
        : end;
    input.value = String(offset);
    input.focus();
    changeRange(input);
  });
  root.querySelector(".controls")!.addEventListener("change", (event) => {
    if (event.target !== start && event.target !== end) render();
  });
  element("reset-range").addEventListener("click", () => {
    start.value = "0";
    end.value = String(months.length - 1);
    render();
  });
  for (const [id, selected] of [
    ["select-all", true],
    ["select-none", false],
  ] as const) {
    element(id).addEventListener("click", () => {
      root
        .querySelectorAll<HTMLInputElement>('input[name="activity"]')
        .forEach((input) => {
          input.checked = selected;
        });
      render();
    });
  }

  return {
    loadUrl,
    loadSource: (source: ManifestSource) =>
      load(async () => parseManifest(source)),
    cancel,
    setMeasures,
    disconnect() {
      cancel();
      chart?.destroy();
      chart = undefined;
    },
  };
}

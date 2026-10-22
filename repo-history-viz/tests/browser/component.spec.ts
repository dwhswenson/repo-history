import { expect, test } from "@playwright/test";
import { summarySource as source, combinedSource, fixture } from "../fixtures";

test("instances isolate controls, charts, and CSS and survive reconnection", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.evaluate(() => {
    const style = document.createElement("style");
    style.textContent = "button { background: rgb(255, 0, 0) !important; }";
    document.head.append(style);
    const button = document.createElement("button");
    button.id = "host-button";
    button.textContent = "Host button";
    document.body.append(button);
    const second = document.createElement("repo-history-viz");
    second.setAttribute(
      "metrics",
      "github-activities commits contributors-by-type total-contributors balance",
    );
    second.id = "second";
    second.style.width = "360px";
    document.body.append(second);
  });
  const first = page.locator("repo-history-viz").first();
  const second = page.locator("#second");
  await first.getByLabel("Choose summary JSON").setInputFiles({
    name: "2024-02.json",
    mimeType: "application/json",
    buffer: Buffer.from(source().text),
  });
  await second.getByLabel("Choose summary JSON").setInputFiles({
    name: "2024-03.json",
    mimeType: "application/json",
    buffer: Buffer.from(source("2024-03", true).text),
  });
  await first
    .getByLabel("Metric", { exact: true })
    .selectOption("contributors-by-type");
  await expect(first.locator("#chart-title")).toHaveText(
    "Monthly contributors by type",
  );
  await expect(second.locator("#chart-title")).toHaveText(
    "Monthly GitHub activity",
  );
  await expect(first.locator("#start")).toHaveAttribute(
    "aria-valuetext",
    "Feb 2024",
  );
  await expect(second.locator("#start")).toHaveAttribute(
    "aria-valuetext",
    "Mar 2024",
  );
  await expect(second.locator("#start")).toBeDisabled();
  await expect(second.locator("#end")).toBeDisabled();
  await expect(page.locator("#host-button")).toHaveCSS(
    "background-color",
    "rgb(255, 0, 0)",
  );
  await expect(second.getByRole("button", { name: "Load URL" })).not.toHaveCSS(
    "background-color",
    "rgb(255, 0, 0)",
  );
  await expect
    .poll(() => second.evaluate((host) => host.scrollWidth <= host.clientWidth))
    .toBe(true);
  await page.evaluate(() => {
    const host = document.querySelector("#second")!;
    host.remove();
    document.body.append(host);
  });
  await expect(second.locator("#chart-title")).toHaveText(
    "Monthly GitHub activity",
  );
  await second.getByLabel("Cumulative", { exact: true }).check();
  await expect(second.locator("#chart-title")).toHaveText(
    "Cumulative GitHub activity",
  );
  await expect(first.locator("#chart-title")).toHaveText(
    "Monthly contributors by type",
  );
  expect(errors).toEqual([]);
});

test("summary attributes load automatically, supersede pending loads, and emit events", async ({
  page,
}) => {
  await page.route("**/reports/slow.json", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 400));
    await route.fulfill({ body: source().text });
  });
  await page.route("**/reports/fast.json", (route) =>
    route.fulfill({ body: source("2024-03").text }),
  );
  await page.goto("/");
  await page.evaluate(() => {
    const host = document.querySelector("repo-history-viz")!;
    const events: unknown[] = [];
    (window as unknown as { viewerEvents: unknown[] }).viewerEvents = events;
    document.addEventListener("repo-history-load", (event) =>
      events.push((event as CustomEvent).detail),
    );
    document.addEventListener("repo-history-error", (event) =>
      events.push((event as CustomEvent).detail),
    );
    host.setAttribute("hide-loader", "");
    host.setAttribute("summary", "/reports/slow.json");
    host.setAttribute("summary", "/reports/fast.json");
  });
  await expect(page.locator("#start")).toHaveAttribute(
    "aria-valuetext",
    "Mar 2024",
  );
  await expect(page.locator("#load-status")).toBeHidden();
  await expect(page.locator(".loader")).toBeHidden();
  await expect(page.locator("#start")).toHaveAttribute(
    "aria-valuetext",
    "Mar 2024",
  );
  await expect(page.getByLabel("Choose summary JSON")).toBeHidden();
  await expect
    .poll(() =>
      page.evaluate(
        () => (window as unknown as { viewerEvents: unknown[] }).viewerEvents,
      ),
    )
    .toEqual([
      {
        display_name: "example/project",
        repositories: [
          {
            node_id: "R_example",
            full_name: "example/project",
            url: "https://github.com/example/project",
          },
        ],
        months: ["2024-03"],
      },
    ]);
  await page.evaluate(() =>
    document
      .querySelector("repo-history-viz")!
      .setAttribute("summary", "file:///invalid.json"),
  );
  await expect(page.locator("#load-error")).toContainText("HTTP or HTTPS");
  await expect(page.locator("#start")).toHaveAttribute(
    "aria-valuetext",
    "Mar 2024",
  );
});

test("built standalone bundle works without source files and duplicate registration is safe", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/embed.html", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html><body><p id="host">A host page</p><repo-history-viz metrics="github-activities commits contributors-by-type total-contributors balance"></repo-history-viz><script type="module" src="/dist/repo-history-viz.js"></script><script type="module" src="/dist/repo-history-viz.js?second"></script></body></html>`,
    }),
  );
  await page.goto("/embed.html");
  await expect(page.getByLabel("Choose summary JSON")).toBeVisible();
  const events = await page.evaluate(async (text) => {
    const host = document.querySelector("repo-history-viz")!;
    const { defineRepoHistoryViz } = await import(
      /* @vite-ignore */ "/dist/index.js"
    );
    defineRepoHistoryViz();
    const loaded = new Promise((resolve) =>
      host.addEventListener(
        "repo-history-load",
        (event) => resolve((event as CustomEvent).detail),
        { once: true },
      ),
    );
    await host.loadSummarySource({ name: "summary.json", text });
    return loaded;
  }, source().text);
  expect(events).toEqual({
    display_name: "example/project",
    repositories: [
      {
        node_id: "R_example",
        full_name: "example/project",
        url: "https://github.com/example/project",
      },
    ],
    months: ["2024-02"],
  });
  await expect(page.locator("#repository-name")).toHaveText("example/project");
  await expect(page.locator("#host")).toHaveText("A host page");
  const failure = await page.evaluate(async () => {
    try {
      await document
        .querySelector("repo-history-viz")!
        .loadSummarySource({ name: "bad.json", text: "{" });
    } catch (error) {
      return (error as Error).message;
    }
  });
  expect(failure).toContain("bad.json: invalid JSON");
  await expect(page.locator("#repository-name")).toHaveText("example/project");
  expect(errors).toEqual([]);
});

test("production demo registers the component after bundling", async ({
  page,
}) => {
  await page.goto("/demo-dist/index.html");
  await expect(page.getByLabel("Choose summary JSON")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Activity over time" }),
  ).toHaveCount(0);
  await expect(page.locator(".page-header, footer, #range-label")).toHaveCount(
    0,
  );
});

test("metrics are required, validated, ordered, and updated without losing reports", async ({
  page,
}) => {
  await page.goto("/");
  const host = page.locator("repo-history-viz");
  const metric = host.getByLabel("Metric", { exact: true });
  await host.evaluate((element) => {
    element.removeAttribute("metrics");
    element.setAttribute("hide-loader", "");
  });
  await expect(host.locator("#configuration-error")).toBeVisible();
  await expect(host.locator("#configuration-error")).toContainText(
    'Set the "metrics" attribute',
  );
  await host.evaluate(async (element, text) => {
    await element.loadSummarySource({ name: "summary.json", text });
  }, source().text);
  await expect(host.locator("#dashboard")).toBeHidden();
  await expect(metric.locator("option")).toHaveCount(0);
  await host.evaluate((element) => {
    element.setAttribute("metrics", "  commits\n total-contributors commits  ");
  });
  await expect(host.locator("#configuration-error")).toBeHidden();
  await expect(metric.locator("option")).toHaveText([
    "Commits",
    "Total contributors",
  ]);
  await expect(metric).toHaveValue("commits");
  await expect(host.locator("#chart-title")).toHaveText("Monthly commits");
  await metric.selectOption("total-contributors");
  await host.evaluate((element) =>
    element.setAttribute("metrics", "balance total-contributors"),
  );
  await expect(metric).toHaveValue("total-contributors");
  await host.evaluate((element) => element.setAttribute("metrics", "balance"));
  await expect(metric).toHaveValue("balance");
  await expect(host.locator("#chart-title")).toHaveText(
    "Cumulative issue / PR balance",
  );
  const error = await host.evaluate((element) => {
    let detail: unknown;
    element.addEventListener(
      "repo-history-error",
      (event) => {
        detail = (event as CustomEvent).detail;
      },
      { once: true },
    );
    element.setAttribute("metrics", "commits typo");
    return detail;
  });
  expect(error).toEqual({
    message:
      "Unknown metrics: typo. Supported metrics: commits, github-activities, contributors-by-type, total-contributors, balance.",
  });
  await expect(host.locator("#configuration-error")).toContainText("typo");
  await expect(host.locator("#dashboard")).toBeHidden();
  await expect(metric.locator("option")).toHaveCount(0);
  await host.evaluate((element) => element.setAttribute("metrics", "  "));
  await expect(host.locator("#configuration-error")).toContainText(
    "at least one metric",
  );
  await host.evaluate((element) => {
    element.remove();
    element.setAttribute("metrics", "contributors-by-type");
    document.body.append(element);
  });
  await expect(host.locator("#dashboard")).toBeVisible();
  await expect(metric).toHaveValue("contributors-by-type");
  await expect(host.locator("#repository-name")).toHaveText("example/project");
  await expect(host.locator("#start")).toHaveAttribute(
    "aria-valuetext",
    "Feb 2024",
  );
});

test("changing metrics preserves the selected month range", async ({
  page,
}) => {
  await page.goto("/");
  const host = page.locator("repo-history-viz");
  await host.evaluate(
    async (element, sources) => {
      await element.loadSummarySource(sources);
    },
    combinedSource([fixture(), fixture("2024-03")]),
  );
  await host.locator("#start").focus();
  await page.keyboard.press("ArrowRight");
  await host.evaluate((element) => element.setAttribute("metrics", "commits"));
  await expect(host.locator("#start")).toHaveAttribute(
    "aria-valuetext",
    "Mar 2024",
  );
  await expect(host.locator("#chart-title")).toHaveText("Monthly commits");
});

test("hosted datasets load with one summary request and no monthly requests", async ({
  page,
}) => {
  const requested: string[] = [];
  await page.route("**/reports/**", async (route) => {
    requested.push(new URL(route.request().url()).pathname);
    if (requested.at(-1) === "/reports/summary.json") {
      await route.fulfill({
        body: combinedSource([fixture(), fixture("2024-03")]).text,
      });
    } else {
      await route.abort();
    }
  });
  await page.goto("/");
  await page.locator("repo-history-viz").evaluate(async (element) => {
    await element.loadSummary("/reports/summary.json");
  });
  await expect(page.locator("#repository-name")).toHaveText("example/project");
  expect(requested).toEqual(["/reports/summary.json"]);
});

test("prepared multi-repository data uses its display name, repository details, and chart exports", async ({
  page,
}) => {
  const { default: fixture } = await import(
    "../../../tests/fixtures/multi-repo-summary-v1.json",
    { with: { type: "json" } }
  );
  await page.goto("/");
  await page.evaluate(async (text) => {
    const host = document.querySelector("repo-history-viz")!;
    await host.loadSummarySource({ name: "multi.json", text });
  }, JSON.stringify(fixture));
  await expect(page.locator("#repository-name")).toHaveText("Two repositories");
  const details = page.locator("#repository-details");
  await expect(details).toBeVisible();
  await details.locator("summary").click();
  await expect(page.locator("#repository-list a")).toHaveText([
    "example/project",
    "another/project",
  ]);
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download PNG" }).click();
  expect((await download).suggestedFilename()).toContain("Two-repositories");
});

test("npm registration and main entry share the built component class", async ({
  page,
}) => {
  await page.route("**/package-consumer.html", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: '<repo-history-viz metrics="commits"></repo-history-viz><script type="module" src="/dist/register.js"></script>',
    }),
  );
  await page.goto("/package-consumer.html");
  await expect(page.getByLabel("Choose summary JSON")).toBeVisible();
  expect(
    await page.evaluate(async () => {
      const entry = "/dist/index.js";
      const { RepoHistoryViz } = await import(entry);
      return (
        document.querySelector("repo-history-viz") instanceof RepoHistoryViz
      );
    }),
  ).toBe(true);
  await page.getByLabel("Choose summary JSON").setInputFiles({
    name: "summary.json",
    mimeType: "application/json",
    buffer: Buffer.from(source().text),
  });
  await expect(page.locator("#dashboard")).toBeVisible();
});

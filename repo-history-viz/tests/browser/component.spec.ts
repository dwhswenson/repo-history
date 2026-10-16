import { expect, test } from "@playwright/test";
import { manifestSource as source, combinedSource, fixture } from "../fixtures";

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
      "measures",
      "github-activities commits contributors total-contributors balance",
    );
    second.id = "second";
    second.style.width = "360px";
    document.body.append(second);
  });
  const first = page.locator("repo-history-viz").first();
  const second = page.locator("#second");
  await first.getByLabel("Choose manifest JSON").setInputFiles({
    name: "2024-02.json",
    mimeType: "application/json",
    buffer: Buffer.from(source().text),
  });
  await second.getByLabel("Choose manifest JSON").setInputFiles({
    name: "2024-03.json",
    mimeType: "application/json",
    buffer: Buffer.from(source("2024-03", true).text),
  });
  await first
    .getByLabel("Measure", { exact: true })
    .selectOption("contributors");
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

test("manifest attributes load automatically, supersede pending loads, and emit events", async ({
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
    host.setAttribute("manifest", "/reports/slow.json");
    host.setAttribute("manifest", "/reports/fast.json");
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
  await expect(page.getByLabel("Choose manifest JSON")).toBeHidden();
  await expect
    .poll(() =>
      page.evaluate(
        () => (window as unknown as { viewerEvents: unknown[] }).viewerEvents,
      ),
    )
    .toEqual([{ repository: "example/project", months: ["2024-03"] }]);
  await page.evaluate(() =>
    document
      .querySelector("repo-history-viz")!
      .setAttribute("manifest", "file:///invalid.json"),
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
      body: `<!doctype html><html><body><p id="host">A host page</p><repo-history-viz measures="github-activities commits contributors total-contributors balance"></repo-history-viz><script type="module" src="/dist/repo-history-viz.js"></script><script type="module" src="/dist/repo-history-viz.js?second"></script></body></html>`,
    }),
  );
  await page.goto("/embed.html");
  await expect(page.getByLabel("Choose manifest JSON")).toBeVisible();
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
    await host.loadManifestSource({ name: "manifest.json", text });
    return loaded;
  }, source().text);
  expect(events).toEqual({
    repository: "example/project",
    months: ["2024-02"],
  });
  await expect(page.locator("#repository-name")).toHaveText("example/project");
  await expect(page.locator("#host")).toHaveText("A host page");
  const failure = await page.evaluate(async () => {
    try {
      await document
        .querySelector("repo-history-viz")!
        .loadManifestSource({ name: "bad.json", text: "{" });
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
  await expect(page.getByLabel("Choose manifest JSON")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Activity over time" }),
  ).toHaveCount(0);
  await expect(page.locator(".page-header, footer, #range-label")).toHaveCount(
    0,
  );
});

test("measures are required, validated, ordered, and updated without losing reports", async ({
  page,
}) => {
  await page.goto("/");
  const host = page.locator("repo-history-viz");
  const measure = host.getByLabel("Measure", { exact: true });
  await host.evaluate((element) => {
    element.removeAttribute("measures");
    element.setAttribute("hide-loader", "");
  });
  await expect(host.locator("#configuration-error")).toBeVisible();
  await expect(host.locator("#configuration-error")).toContainText(
    'Set the "measures" attribute',
  );
  await host.evaluate(async (element, text) => {
    await element.loadManifestSource({ name: "manifest.json", text });
  }, source().text);
  await expect(host.locator("#dashboard")).toBeHidden();
  await expect(measure.locator("option")).toHaveCount(0);
  await host.evaluate((element) => {
    element.setAttribute(
      "measures",
      "  commits\n total-contributors commits  ",
    );
  });
  await expect(host.locator("#configuration-error")).toBeHidden();
  await expect(measure.locator("option")).toHaveText([
    "Commits",
    "Total contributors",
  ]);
  await expect(measure).toHaveValue("commits");
  await expect(host.locator("#chart-title")).toHaveText("Monthly commits");
  await measure.selectOption("total-contributors");
  await host.evaluate((element) =>
    element.setAttribute("measures", "balance total-contributors"),
  );
  await expect(measure).toHaveValue("total-contributors");
  await host.evaluate((element) => element.setAttribute("measures", "balance"));
  await expect(measure).toHaveValue("balance");
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
    element.setAttribute("measures", "commits typo");
    return detail;
  });
  expect(error).toEqual({
    message:
      "Unknown measures: typo. Supported measures: commits, github-activities, contributors, total-contributors, balance.",
  });
  await expect(host.locator("#configuration-error")).toContainText("typo");
  await expect(host.locator("#dashboard")).toBeHidden();
  await expect(measure.locator("option")).toHaveCount(0);
  await host.evaluate((element) => element.setAttribute("measures", "  "));
  await expect(host.locator("#configuration-error")).toContainText(
    "at least one measure",
  );
  await host.evaluate((element) => {
    element.remove();
    element.setAttribute("measures", "contributors");
    document.body.append(element);
  });
  await expect(host.locator("#dashboard")).toBeVisible();
  await expect(measure).toHaveValue("contributors");
  await expect(host.locator("#repository-name")).toHaveText("example/project");
  await expect(host.locator("#start")).toHaveAttribute(
    "aria-valuetext",
    "Feb 2024",
  );
});

test("changing measures preserves the selected month range", async ({
  page,
}) => {
  await page.goto("/");
  const host = page.locator("repo-history-viz");
  await host.evaluate(
    async (element, sources) => {
      await element.loadManifestSource(sources);
    },
    combinedSource([fixture(), fixture("2024-03")]),
  );
  await host.locator("#start").focus();
  await page.keyboard.press("ArrowRight");
  await host.evaluate((element) => element.setAttribute("measures", "commits"));
  await expect(host.locator("#start")).toHaveAttribute(
    "aria-valuetext",
    "Mar 2024",
  );
  await expect(host.locator("#chart-title")).toHaveText("Monthly commits");
});

test("hosted datasets load with one manifest request and no monthly requests", async ({
  page,
}) => {
  const requested: string[] = [];
  await page.route("**/reports/**", async (route) => {
    requested.push(new URL(route.request().url()).pathname);
    if (requested.at(-1) === "/reports/manifest.json") {
      await route.fulfill({
        body: combinedSource([fixture(), fixture("2024-03")]).text,
      });
    } else {
      await route.abort();
    }
  });
  await page.goto("/");
  await page.locator("repo-history-viz").evaluate(async (element) => {
    await element.loadManifest("/reports/manifest.json");
  });
  await expect(page.locator("#repository-name")).toHaveText("example/project");
  expect(requested).toEqual(["/reports/manifest.json"]);
});

import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { source } from "../fixtures";

const payload = (month: string, isEmpty = false) => ({
  name: `${month}.json`,
  mimeType: "application/json",
  buffer: Buffer.from(source(month, isEmpty).text),
});

async function csvLines(page: Page): Promise<string[]> {
  const ready = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download Data (CSV)" }).click();
  const download = await ready;
  expect(download.suggestedFilename()).toMatch(/\.csv$/);
  return (await readFile((await download.path())!, "utf8"))
    .trimEnd()
    .split("\r\n");
}

test("local files, controls, gaps, range sliders, and failed replacement", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", {
      name: "A clearer view of repository activity",
    }),
  ).toBeVisible();
  await page
    .getByLabel("Choose monthly JSON reports")
    .setInputFiles([payload("2024-02"), payload("2024-04", true)]);
  await expect(page.locator("#repository-name")).toHaveText("example/project");
  await expect(page.locator("#warnings")).toContainText(
    "Missing reports: 2024-03",
  );
  expect(await csvLines(page)).toHaveLength(4);
  expect((await csvLines(page))[2]).toContain('"2024-03","No report"');
  await page
    .getByLabel("Measure", { exact: true })
    .selectOption("contributors");
  await page.getByRole("button", { name: "Clear", exact: true }).click();
  await page.getByLabel("Commits", { exact: true }).check();
  await page
    .getByLabel("Measure", { exact: true })
    .selectOption("contributors");
  await page.getByLabel("Cumulative", { exact: true }).check();
  await expect(page.locator("#chart-title")).toHaveText(
    "Cumulative contributors by type",
  );
  expect(await csvLines(page)).toEqual([
    '"Month (UTC)","Report status","Commits"',
    '"2024-02","Collected","3"',
    '"2024-03","No report","No report"',
    '"2024-04","Collected","3"',
  ]);
  await page.getByLabel("Unresolved Git identities", { exact: true }).uncheck();
  expect((await csvLines(page))[1]).toBe('"2024-02","Collected","1"');
  await page.getByRole("slider", { name: "From", exact: true }).focus();
  await page.getByRole("slider", { name: "From", exact: true }).press("End");
  expect(await csvLines(page)).toEqual([
    '"Month (UTC)","Report status","Commits"',
    '"2024-04","Collected","0"',
  ]);
  await page.getByRole("slider", { name: "Through", exact: true }).focus();
  await page
    .getByRole("slider", { name: "Through", exact: true })
    .press("Home");
  await expect(page.locator("#end")).toHaveValue("2");
  await expect(page.locator("#start-label")).toHaveText("Apr 2024");
  await expect(page.locator("#end-label")).toHaveText("Apr 2024");
  await page.getByRole("button", { name: "All available months" }).click();
  await expect(page.locator("#start")).toHaveValue("0");
  await expect(page.locator("#end")).toHaveValue("2");
  await page
    .getByRole("slider", { name: "From", exact: true })
    .press("ArrowRight");
  await expect(page.locator("#start-label")).toHaveText("Mar 2024");
  await expect(page.locator("#end-label")).toHaveText("Apr 2024");
  await expect(page.locator("#start")).toHaveAttribute(
    "aria-valuetext",
    "Mar 2024",
  );
  await page.getByRole("button", { name: "All available months" }).click();
  expect(await csvLines(page)).toHaveLength(4);
  await page.getByLabel("Choose monthly JSON reports").setInputFiles({
    name: "broken.json",
    mimeType: "application/json",
    buffer: Buffer.from("{"),
  });
  await expect(page.locator("#load-error")).toContainText(
    "broken.json: invalid JSON",
  );
  await expect(page.locator("#repository-name")).toHaveText("example/project");
  expect(await csvLines(page)).toHaveLength(4);
  await page.screenshot({
    path: "test-results/dashboard-desktop.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("hosted reports, partial months, combined contributors, and mobile layout", async ({
  page,
}) => {
  const partial = JSON.parse(source("2024-03").text);
  partial.metadata.ongoing = true;
  partial.metadata.collection_started_at = "2024-03-15T12:00:00Z";
  partial.metadata.collection_finished_at = "2024-03-15T12:05:00Z";
  await page.route("**/reports/manifest.json", (route) =>
    route.fulfill({ json: { files: ["2024-03.json", "2024-02.json"] } }),
  );
  await page.route("**/reports/2024-02.json", (route) =>
    route.fulfill({ contentType: "application/json", body: source().text }),
  );
  await page.route("**/reports/2024-03.json", (route) =>
    route.fulfill({ json: partial }),
  );
  await page.goto("/");
  await page.getByLabel("Hosted manifest URL").fill("/reports/manifest.json");
  await page.getByRole("button", { name: "Load URL" }).click();
  await expect(page.locator("#repository-name")).toHaveText("example/project");
  await expect(page.locator("#load-status")).toBeHidden();
  await expect(page.locator("#warnings")).toContainText(
    "Partial month: 2024-03-01 — 2024-03-15 (UTC).",
  );
  await page
    .getByLabel("Measure", { exact: true })
    .selectOption("contributors");
  await page.getByRole("button", { name: "Clear", exact: true }).click();
  await page.getByLabel("Commits", { exact: true }).check();
  await page.getByLabel("Pull requests opened", { exact: true }).check();
  await page.getByLabel("Show selected types combined").check();
  await page
    .getByLabel("Measure", { exact: true })
    .selectOption("contributors");
  await page.getByLabel("Cumulative", { exact: true }).check();
  expect((await csvLines(page)).at(-1)).toBe('"2024-03","Partial","3","1","3"');
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator("#chart")).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
  await page.screenshot({
    path: "test-results/dashboard-mobile.png",
    fullPage: true,
  });
  await page.route("**/reports/manifest.json", (route) =>
    route.fulfill({ status: 404, body: "Not found" }),
  );
  await page.getByRole("button", { name: "Load URL" }).click();
  await expect(page.locator("#load-error")).toContainText("Manifest: HTTP 404");
  await expect(page.locator("#repository-name")).toHaveText("example/project");
});

test("measure selection separates commits, GitHub events, contributors, and cumulative balances", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByLabel("Choose monthly JSON reports")
    .setInputFiles([payload("2024-02"), payload("2024-03")]);
  const measure = page.getByLabel("Measure", { exact: true });
  await expect(measure).toHaveValue("github-activities");
  await expect(page.getByLabel("Commits", { exact: true })).toBeHidden();
  await measure.selectOption("commits");
  await expect(page.locator("#contribution-types")).toBeHidden();
  expect((await csvLines(page))[1]).toBe('"2024-02","Collected","2"');
  await measure.selectOption("contributors");
  await expect(page.locator("#chart-note")).toContainText("Side-by-side bars");
  await expect(page.getByLabel("Commits", { exact: true })).toBeVisible();
  await measure.selectOption("balance");
  await expect(page.getByLabel("Cumulative", { exact: true })).toBeChecked();
  await expect(page.getByLabel("Monthly", { exact: true })).toBeDisabled();
  await expect(page.locator("#classifications")).toBeHidden();
  await expect(page.locator("#chart-note")).toContainText(
    "reports omit PR closes",
  );
  expect((await csvLines(page)).at(-1)).toBe('"2024-03","Collected","2","0"');
  await page.getByLabel("Pull requests", { exact: true }).uncheck();
  expect((await csvLines(page))[0]).toBe(
    '"Month (UTC)","Report status","Issue balance"',
  );
  await page.getByLabel("Issues", { exact: true }).uncheck();
  await expect(page.locator("#no-series")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Download PNG" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Download Data (CSV)" }),
  ).toBeDisabled();
  await measure.selectOption("total-contributors");
  await expect(page.locator("#chart-title")).toHaveText(
    "Cumulative contributors",
  );
  await expect(page.getByLabel("Monthly", { exact: true })).toBeEnabled();
  await page.getByLabel("Monthly", { exact: true }).check();
  await expect(page.locator("#chart-title")).toHaveText("Monthly contributors");
  expect((await csvLines(page))[1]).toBe('"2024-02","Collected","4"');
});

test("downloads the current chart as a PNG with its repository and range in the filename", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByLabel("Choose monthly JSON reports")
    .setInputFiles([payload("2024-02"), payload("2024-03")]);
  await page.getByLabel("Measure", { exact: true }).selectOption("commits");
  const downloadReady = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download PNG" }).click();
  const download = await downloadReady;
  expect(download.suggestedFilename()).toBe(
    "example-project-commits-monthly-2024-02-to-2024-03.png",
  );
  const png = await readFile((await download.path())!);
  expect(png.subarray(0, 8)).toEqual(
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
  );
  expect(png.readUInt32BE(16)).toBeGreaterThan(0);
  expect(png.readUInt32BE(20)).toBeGreaterThan(0);
});

test("GitHub activity actor filters update chart downloads with bots excluded by default", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByLabel("Choose monthly JSON reports")
    .setInputFiles([payload("2024-02"), payload("2024-03")]);
  await expect(
    page.getByLabel("Known bots", { exact: true }),
  ).not.toBeChecked();
  await expect(page.getByLabel("Undetermined", { exact: true })).toBeChecked();
  await page.getByRole("button", { name: "Clear", exact: true }).click();
  await page.getByLabel("Inline review comments", { exact: true }).check();
  await page.getByLabel("Issue comments", { exact: true }).check();
  expect((await csvLines(page))[1]).toBe('"2024-02","Collected","1","1"');
  await page.getByLabel("Known bots", { exact: true }).check();
  expect((await csvLines(page))[1]).toBe('"2024-02","Collected","1","2"');
  await page.getByLabel("Undetermined", { exact: true }).uncheck();
  await page.getByLabel("Cumulative", { exact: true }).check();
  expect((await csvLines(page)).at(-1)).toBe('"2024-03","Collected","0","4"');
});

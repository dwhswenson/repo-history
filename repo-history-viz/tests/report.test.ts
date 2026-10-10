import { describe, expect, it, vi } from "vitest";
import { loadManifest, loadReports, parseReport } from "../src/report";
import { fixture, source } from "./fixtures";

describe("report loading", () => {
  it("accepts the existing mixed and empty fixtures and sorts chronologically", () => {
    const reports = loadReports([source("2024-03", true), source()]);
    expect(reports.map((report) => report.metadata.month)).toEqual([
      "2024-02",
      "2024-03",
    ]);
    expect(reports[0]!.activities).toHaveLength(14);
    expect(reports[1]!.activities).toHaveLength(0);
  });
  it("rejects empty selections", () =>
    expect(() => loadReports([])).toThrow("at least one"));
  it("identifies malformed JSON by filename", () =>
    expect(() => parseReport({ name: "broken.json", text: "{" })).toThrow(
      "broken.json: invalid JSON",
    ));
  it("rejects unsupported schema versions", () => {
    expect(() =>
      parseReport({
        name: "future.json",
        text: JSON.stringify({ ...fixture(), schema_version: "2" }),
      }),
    ).toThrow("future.json: invalid v1 report");
  });
  it("rejects mixed repositories", () => {
    const second = fixture("2024-03");
    second.metadata.repository_node_id = "R_other";
    expect(() =>
      loadReports([
        source(),
        { name: "other.json", text: JSON.stringify(second) },
      ]),
    ).toThrow("other.json: all reports must belong");
  });
  it("accepts repository renames with stable node IDs", () => {
    const second = fixture("2024-03");
    second.metadata.repository_full_name = "renamed/project";
    expect(
      loadReports([
        source(),
        { name: "renamed.json", text: JSON.stringify(second) },
      ]),
    ).toHaveLength(2);
  });
  it("rejects duplicate months", () =>
    expect(() => loadReports([source(), source()])).toThrow(
      "duplicate report for 2024-02",
    ));
  it("rejects unknown credited actors", () => {
    const report = fixture();
    report.activities[0]!.credits[0]!.actor_key = "unknown";
    expect(() =>
      parseReport({ name: "unknown.json", text: JSON.stringify(report) }),
    ).toThrow("references unknown actor unknown");
  });
  it("rejects duplicate actor keys and activity IDs", () => {
    const report = fixture();
    report.actors.push(report.actors[0]!);
    expect(() =>
      parseReport({ name: "duplicate.json", text: JSON.stringify(report) }),
    ).toThrow("duplicate actor key");
    report.actors.pop();
    report.activities.push(report.activities[0]!);
    expect(() =>
      parseReport({ name: "duplicate.json", text: JSON.stringify(report) }),
    ).toThrow("duplicate activity ID");
  });
  it("rejects conflicting classifications across months", () => {
    const report = fixture("2024-03");
    report.actors[0]!.classification = "human";
    expect(() =>
      loadReports([
        source(),
        { name: "conflict.json", text: JSON.stringify(report) },
      ]),
    ).toThrow("conflicting classifications");
  });
  it("rejects incorrect month boundaries and out-of-month events", () => {
    const report = fixture();
    report.metadata.month_end = "2024-04-01T00:00:00Z";
    expect(() =>
      parseReport({ name: "boundaries.json", text: JSON.stringify(report) }),
    ).toThrow("month boundaries");
    report.metadata.month_end = "2024-03-01T00:00:00Z";
    report.activities[0]!.occurred_at = "2024-03-01T00:00:00Z";
    expect(() =>
      parseReport({ name: "event.json", text: JSON.stringify(report) }),
    ).toThrow("outside its reporting month");
  });
  it("accepts a timestamp with fractional seconds at month start", () => {
    const report = fixture();
    report.activities[0]!.occurred_at = "2024-02-01T00:00:00.000Z";
    expect(
      parseReport({ name: "fractional.json", text: JSON.stringify(report) })
        .activities,
    ).toHaveLength(14);
  });
});

describe("hosted manifests", () => {
  it("resolves relative report paths and loads a sorted dataset without credentials", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ files: ["2024-03.json", "2024-02.json"] }),
        ),
      )
      .mockResolvedValueOnce(new Response(source("2024-03").text))
      .mockResolvedValueOnce(new Response(source().text));
    const reports = await loadManifest(
      "reports/index.json",
      "https://example.org/viewer/",
      fetcher,
    );
    expect(reports.map((report) => report.metadata.month)).toEqual([
      "2024-02",
      "2024-03",
    ]);
    expect(fetcher).toHaveBeenNthCalledWith(
      2,
      "https://example.org/viewer/reports/2024-03.json",
      { credentials: "omit" },
    );
  });
  it("reports manifest and report HTTP failures", async () => {
    await expect(
      loadManifest(
        "/manifest.json",
        "https://example.org",
        vi.fn().mockResolvedValue(new Response("", { status: 404 })),
      ),
    ).rejects.toThrow("Manifest: HTTP 404");
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response('{"files":["missing.json"]}'))
      .mockResolvedValueOnce(new Response("", { status: 403 }));
    await expect(
      loadManifest("/manifest.json", "https://example.org", fetcher),
    ).rejects.toThrow("missing.json: HTTP 403");
  });
  it("reports malformed manifests, empty files, and disallowed protocols", async () => {
    for (const text of [
      "{}",
      '{"files":[]}',
      '{"files":[42]}',
      '{"files":[""]}',
    ]) {
      await expect(
        loadManifest(
          "/index.json",
          "https://example.org",
          vi.fn().mockResolvedValue(new Response(text)),
        ),
      ).rejects.toThrow('nonempty "files" array');
    }
    await expect(
      loadManifest(
        "/index.json",
        "https://example.org",
        vi.fn().mockResolvedValue(new Response("{")),
      ),
    ).rejects.toThrow("invalid JSON");
    await expect(
      loadManifest("file:///tmp/manifest.json", "https://example.org"),
    ).rejects.toThrow("HTTP or HTTPS");
    await expect(
      loadManifest(
        "/index.json",
        "https://example.org",
        vi
          .fn()
          .mockResolvedValue(
            new Response('{"files":["file:///tmp/report.json"]}'),
          ),
      ),
    ).rejects.toThrow("HTTP or HTTPS");
  });
  it("propagates network failures", async () => {
    await expect(
      loadManifest(
        "/index.json",
        "https://example.org",
        vi.fn().mockRejectedValue(new Error("Network unavailable")),
      ),
    ).rejects.toThrow("Network unavailable");
  });
});

import { describe, expect, it, vi } from "vitest";
import { loadSummary, parseSummary } from "../src/report";
import { fixture, summarySource, toSummary } from "./fixtures";
import generated from "../../tests/fixtures/viz-summary-v1.json" with { type: "json" };
import combined from "../../tests/fixtures/combined-summary-v1.json" with { type: "json" };

describe("history summaries", () => {
  it("accepts Python-generated summaries and sorts months", () => {
    expect(
      parseSummary({ name: "python.json", text: JSON.stringify(generated) }),
    ).toEqual(generated);
    const value = toSummary([fixture("2024-03", true), fixture()]);
    expect(
      parseSummary({ name: "m", text: JSON.stringify(value) }).months.map(
        (m) => m.metadata.month,
      ),
    ).toEqual(["2024-02", "2024-03"]);
  });
  it("identifies malformed JSON", () =>
    expect(() => parseSummary({ name: "bad.json", text: "{" })).toThrow(
      "bad.json: invalid JSON",
    ));
  it.each([{ files: ["2024-02.json"] }, fixture()])(
    "rejects unsupported input formats with summary generation guidance",
    (value) => {
      expect(() =>
        parseSummary({
          name: "unsupported.json",
          text: JSON.stringify(value),
        }),
      ).toThrow("repo-history summarize --input-directory REPORTS");
    },
  );
  it.each([
    (m: any) => {
      m.schema_version = "2";
    },
    (m: any) => {
      m.extra = true;
    },
    (m: any) => {
      m.months = [];
    },
    (m: any) => {
      m.months.push(m.months[0]);
    },
    (m: any) => {
      m.months[0].metadata.month_end = "2024-04-01T00:00:00Z";
    },
    (m: any) => {
      m.months[0].source_reports[0].metadata.provenance.last_updated_by.finished_at =
        "2024-01-01T00:00:00Z";
    },
    (m: any) => {
      m.months[0].metadata.is_partial = true;
    },
    (m: any) => {
      delete m.actors[Object.keys(m.actors)[0]];
    },
    (m: any) => {
      m.actors.unused = "human";
    },
    (m: any) => {
      m.actors[Object.keys(m.actors)[0]] = "alien";
    },
    (m: any) => {
      m.months[0].summaries.repository_counts.commit = -1;
    },
    (m: any) => {
      m.months[0].summaries.repository_counts.commit =
        Number.MAX_SAFE_INTEGER + 1;
    },
    (m: any) => {
      m.months[0].summaries.repository_counts.issue_comment++;
    },
    (m: any) => {
      m.months[0].summaries.distinct_actors.total++;
    },
    (m: any) => {
      m.months[0].summaries.distinct_actors_by_type.commit.human++;
    },
    (m: any) => {
      m.months[0].summaries.unattributed_total++;
    },
    (m: any) => {
      m.months[0].summaries.actor_counts[Object.keys(m.actors)[0]].commit = 100;
    },
  ])("rejects inconsistent summaries %#", (mutate) => {
    const value = toSummary([fixture()]);
    mutate(value);
    expect(() =>
      parseSummary({ name: "bad", text: JSON.stringify(value) }),
    ).toThrow();
  });
});
describe("hosted summary", () => {
  it("fetches the complete dataset once without credentials", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(summarySource().text));
    const value = await loadSummary(
      "reports/summary.json",
      "https://example.org/viewer/",
      fetcher,
    );
    expect(value.months).toHaveLength(1);
    expect(fetcher).toHaveBeenCalledExactlyOnceWith(
      "https://example.org/viewer/reports/summary.json",
      { credentials: "omit" },
    );
  });
  it("propagates HTTP, JSON, protocol and network errors", async () => {
    await expect(
      loadSummary(
        "/m",
        "https://example.org",
        vi.fn().mockResolvedValue(new Response("", { status: 404 })),
      ),
    ).rejects.toThrow("History summary: HTTP 404");
    await expect(
      loadSummary(
        "/m",
        "https://example.org",
        vi.fn().mockResolvedValue(new Response("{")),
      ),
    ).rejects.toThrow("invalid JSON");
    await expect(
      loadSummary("file:///tmp/m", "https://example.org"),
    ).rejects.toThrow("HTTP or HTTPS");
    await expect(
      loadSummary(
        "/m",
        "https://example.org",
        vi.fn().mockRejectedValue(new Error("offline")),
      ),
    ).rejects.toThrow("offline");
  });
});

describe("prepared multi-repository summaries", () => {
  it("accepts the CLI-generated fixture spanning repositories and months", () => {
    expect(
      parseSummary({ name: "combined.json", text: JSON.stringify(combined) }),
    ).toEqual(combined);
  });
  it("accepts the Python contract fixture with deduplicated populations", async () => {
    const { default: fixture } = await import(
      "../../tests/fixtures/multi-repo-summary-v1.json",
      { with: { type: "json" } }
    );
    const value = parseSummary({
      name: "multi.json",
      text: JSON.stringify(fixture),
    });
    expect(value.display_name).toBe("Two repositories");
    expect(value.repositories).toHaveLength(2);
    expect(value.months[0]!.summaries.repository_counts.commit).toBe(4);
    expect(value.months[0]!.summaries.distinct_actors.total).toBe(5);
  });
  it.each(["aggregate", "missing", "duplicate", "unknown", "creation", "run"])(
    "rejects invalid %s source data",
    async (problem) => {
      const { default: fixture } = await import(
        "../../tests/fixtures/multi-repo-summary-v1.json",
        { with: { type: "json" } }
      );
      const value = structuredClone(fixture),
        month = value.months[0]!;
      if (problem === "aggregate") month.summaries.repository_counts.commit++;
      else if (problem === "missing") month.source_reports.pop();
      else if (problem === "duplicate")
        month.source_reports.push(structuredClone(month.source_reports[0]!));
      else if (problem === "unknown")
        month.source_reports[0]!.metadata.repository.node_id = "unknown";
      else if (problem === "creation")
        month.source_reports[0]!.metadata.repository.created_at =
          "2019-01-01T00:00:00Z";
      else
        month.source_reports[1]!.metadata.provenance.created_by.collector_version =
          "conflict";
      expect(() =>
        parseSummary({ name: "bad.json", text: JSON.stringify(value) }),
      ).toThrow();
    },
  );
});

import { describe, expect, it, vi } from "vitest";
import { loadManifest, parseManifest } from "../src/report";
import { fixture, manifestSource, toManifest } from "./fixtures";
import generated from "../../tests/fixtures/viz-manifest-v1.json" with { type: "json" };

describe("summary manifests", () => {
  it("accepts Python-generated manifests and sorts months", () => {
    expect(
      parseManifest({ name: "python.json", text: JSON.stringify(generated) }),
    ).toEqual(generated);
    const value = toManifest([fixture("2024-03", true), fixture()]);
    expect(
      parseManifest({ name: "m", text: JSON.stringify(value) }).months.map(
        (m) => m.metadata.month,
      ),
    ).toEqual(["2024-02", "2024-03"]);
  });
  it("identifies malformed JSON", () =>
    expect(() => parseManifest({ name: "bad.json", text: "{" })).toThrow(
      "bad.json: invalid JSON",
    ));
  it.each([{ files: ["2024-02.json"] }, fixture()])(
    "rejects unsupported input formats with manifest generation guidance",
    (value) => {
      expect(() =>
        parseManifest({
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
      m.months[0].metadata.collection_finished_at = "2024-01-01T00:00:00Z";
    },
    (m: any) => {
      m.months[0].metadata.ongoing = true;
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
  ])("rejects inconsistent manifests %#", (mutate) => {
    const value = toManifest([fixture()]);
    mutate(value);
    expect(() =>
      parseManifest({ name: "bad", text: JSON.stringify(value) }),
    ).toThrow();
  });
});
describe("hosted manifest", () => {
  it("fetches the complete dataset once without credentials", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(manifestSource().text));
    const value = await loadManifest(
      "reports/manifest.json",
      "https://example.org/viewer/",
      fetcher,
    );
    expect(value.months).toHaveLength(1);
    expect(fetcher).toHaveBeenCalledExactlyOnceWith(
      "https://example.org/viewer/reports/manifest.json",
      { credentials: "omit" },
    );
  });
  it("propagates HTTP, JSON, protocol and network errors", async () => {
    await expect(
      loadManifest(
        "/m",
        "https://example.org",
        vi.fn().mockResolvedValue(new Response("", { status: 404 })),
      ),
    ).rejects.toThrow("Manifest: HTTP 404");
    await expect(
      loadManifest(
        "/m",
        "https://example.org",
        vi.fn().mockResolvedValue(new Response("{")),
      ),
    ).rejects.toThrow("invalid JSON");
    await expect(
      loadManifest("file:///tmp/m", "https://example.org"),
    ).rejects.toThrow("HTTP or HTTPS");
    await expect(
      loadManifest(
        "/m",
        "https://example.org",
        vi.fn().mockRejectedValue(new Error("offline")),
      ),
    ).rejects.toThrow("offline");
  });
});

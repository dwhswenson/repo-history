import { describe, expect, it } from "vitest";
import { eventAggregate } from "./event-reference";
import { aggregate, type ViewState } from "../src/series";
import { ACTIVITY_TYPES } from "../src/report";
import {
  fixture,
  source,
  fromReports as loadReports,
  toManifest,
} from "./fixtures";

const defaults: ViewState = {
  metric: "activities",
  mode: "monthly",
  types: ["commit"],
  classifications: ["human", "unresolved_git"],
  combined: false,
  start: "2024-02",
  end: "2024-03",
};
const reports = loadReports([source(), source("2024-03")]);
const plot = (overrides: Partial<ViewState> = {}) =>
  aggregate(reports, { ...defaults, ...overrides });

describe("monthly and cumulative aggregation", () => {
  it("separates commits from GitHub activity and keeps commit counting independent of type filters", () => {
    expect(
      plot({ metric: "commits", types: [], combined: true }).series,
    ).toEqual([{ key: "commit", label: "Commits", values: [2, 2] }]);
    expect(
      plot({ metric: "commits", mode: "cumulative" }).series[0]!.values,
    ).toEqual([2, 4]);
    const github = plot({
      metric: "github-activities",
      types: ["commit", "pr_opened"],
      combined: true,
    });
    expect(github.series.map((item) => item.key)).toEqual([
      "pr_opened",
      "combined",
    ]);
    expect(github.series.map((item) => item.values)).toEqual([
      [1, 1],
      [1, 1],
    ]);
  });
  it("filters GitHub events by credited actor, including missing actors as undetermined", () => {
    const selection: Partial<ViewState> = {
      metric: "github-activities",
      types: [
        "pr_inline_review_comment",
        "pr_conversation_comment",
        "issue_comment",
        "pr_merged",
      ],
      combined: true,
    };
    // Mixed human/bot inline comments, bot conversation, missing comment author,
    // and a human merger: defaults retain the human and missing-actor events.
    expect(plot(selection).series.map((item) => item.values)).toEqual([
      [1, 1],
      [0, 0],
      [1, 1],
      [1, 1],
      [3, 3],
    ]);
    expect(
      plot({
        ...selection,
        classifications: ["bot"],
        mode: "cumulative",
      }).series.map((item) => item.values),
    ).toEqual([
      [1, 2],
      [1, 2],
      [0, 0],
      [0, 0],
      [2, 4],
    ]);
    expect(
      plot({ ...selection, classifications: ["human"] }).series.map(
        (item) => item.values,
      ),
    ).toEqual([
      [1, 1],
      [0, 0],
      [0, 0],
      [1, 1],
      [2, 2],
    ]);
    expect(
      plot({ ...selection, classifications: [] }).series.every((item) =>
        item.values.every((value) => value === 0),
      ),
    ).toBe(true);
    expect(
      plot({
        ...selection,
        classifications: ["human", "bot", "unresolved_git"],
      }).series.at(-1)!.values,
    ).toEqual([5, 5]);
  });
  it("accumulates separate issue and PR balances, including reopens", () => {
    const state: Partial<ViewState> = { metric: "balance", mode: "cumulative" };
    expect(plot(state).series.map((item) => item.values)).toEqual([
      [1, 2],
      [0, 0],
    ]);
    expect(
      plot({ ...state, start: "2024-03", balanceTypes: ["issues"] }).series.map(
        (item) => item.values,
      ),
    ).toEqual([[1]]);
    expect(
      plot({ ...state, balanceTypes: ["prs"] }).series.map((item) => item.key),
    ).toEqual(["prs"]);
    expect(plot({ ...state, balanceTypes: [] }).series).toEqual([]);
    expect(() => plot({ metric: "balance" })).toThrow(
      "requires cumulative mode",
    );
  });
  it("preserves negative balances and gaps and marks partial months", () => {
    const closing = fixture("2024-04");
    closing.activities = closing.activities.filter(
      (activity) =>
        activity.type === "issue_closed" || activity.type === "pr_merged",
    );
    closing.metadata.ongoing = true;
    const result = aggregate(
      loadReports([
        source(),
        { name: "closing.json", text: JSON.stringify(closing) },
      ]),
      { ...defaults, metric: "balance", mode: "cumulative", end: "2024-04" },
    );
    expect(result.series.map((item) => item.values)).toEqual([
      [1, null, -1],
      [0, null, -1],
    ]);
    expect(result.missing).toEqual(["2024-03"]);
    expect(result.partial).toEqual(["2024-04"]);
  });
  it("counts total contributors across all types regardless of type selection", () => {
    const result = plot({
      metric: "total-contributors",
      types: [],
      combined: false,
    });
    expect(result.series).toEqual([
      { key: "combined", label: "Total contributors", values: [4, 4] },
    ]);
    expect(
      plot({ metric: "total-contributors", classifications: ["human"] })
        .series[0]!.values,
    ).toEqual([2, 2]);
    expect(
      plot({ metric: "total-contributors", classifications: [] }).series[0]!
        .values,
    ).toEqual([0, 0]);
  });
  it("deduplicates total contributors across months, preserves gaps, and resets the range", () => {
    const later = fixture("2024-05");
    later.actors.find((actor) => actor.key === "U_author")!.key = "U_new";
    later.activities.forEach((activity) =>
      activity.credits.forEach((credit) => {
        if (credit.actor_key === "U_author") credit.actor_key = "U_new";
      }),
    );
    const totalReports = loadReports([
      source(),
      source("2024-04", true),
      { name: "later.json", text: JSON.stringify(later) },
    ]);
    const state: ViewState = {
      ...defaults,
      metric: "total-contributors",
      mode: "cumulative",
      end: "2024-05",
    };
    expect(aggregate(totalReports, state).series[0]!.values).toEqual([
      4,
      null,
      4,
      5,
    ]);
    expect(
      aggregate(totalReports, { ...state, mode: "monthly" }).series[0]!.values,
    ).toEqual([4, null, 0, 4]);
    expect(
      aggregate(totalReports, { ...state, start: "2024-05" }).series[0]!.values,
    ).toEqual([4]);
  });
  it("counts co-authored commits once per event", () =>
    expect(plot().series[0]!.values).toEqual([2, 2]));
  it("accumulates activity and resets at the selected start month", () => {
    expect(plot({ mode: "cumulative" }).series[0]!.values).toEqual([2, 4]);
    expect(
      plot({ mode: "cumulative", start: "2024-03" }).series[0]!.values,
    ).toEqual([2]);
  });
  it("includes both range endpoints", () =>
    expect(plot().months).toEqual(["2024-02", "2024-03"]));
  it("distinguishes empty reports from missing months and resumes cumulative totals", () => {
    const result = aggregate(
      loadReports([source(), source("2024-04", true), source("2024-05")]),
      { ...defaults, mode: "cumulative", end: "2024-05" },
    );
    expect(result.series[0]!.values).toEqual([2, null, 2, 4]);
    expect(result.missing).toEqual(["2024-03"]);
  });
  it("counts unique contributors each month and unions across months", () => {
    expect(plot({ metric: "contributors" }).series[0]!.values).toEqual([3, 3]);
    expect(
      plot({ metric: "contributors", mode: "cumulative" }).series[0]!.values,
    ).toEqual([3, 3]);
  });
  it("includes newly observed contributors cumulatively", () => {
    const later = fixture("2024-03");
    later.actors.find((actor) => actor.key === "U_author")!.key = "U_new";
    later.activities.forEach((activity) =>
      activity.credits.forEach((credit) => {
        if (credit.actor_key === "U_author") credit.actor_key = "U_new";
      }),
    );
    const result = aggregate(
      loadReports([
        source(),
        { name: "later.json", text: JSON.stringify(later) },
      ]),
      { ...defaults, metric: "contributors", mode: "cumulative" },
    );
    expect(result.series[0]!.values).toEqual([3, 4]);
  });
  it("resets cumulative contributor membership at the selected start", () => {
    expect(
      plot({ metric: "contributors", mode: "cumulative", start: "2024-03" })
        .series[0]!.values,
    ).toEqual([3]);
  });
  it("deduplicates a combined contributor series across activity types", () => {
    const result = plot({
      metric: "contributors",
      types: ["commit", "pr_opened"],
      combined: true,
    });
    expect(result.series.map((item) => item.values[0])).toEqual([3, 1, 3]);
  });
  it("sums events in the combined activity series", () => {
    const result = plot({ types: ["commit", "pr_opened"], combined: true });
    expect(result.series.map((item) => item.values[0])).toEqual([2, 1, 3]);
  });
  it("excludes context-only authors and null credits", () => {
    const result = plot({
      metric: "contributors",
      types: ACTIVITY_TYPES,
      combined: true,
    });
    expect(result.series.at(-1)!.values).toEqual([4, 4]);
    expect(
      plot({ metric: "contributors", types: ["issue_comment"] }).series[0]!
        .values,
    ).toEqual([0, 0]);
    expect(plot({ types: ["issue_comment"] }).series[0]!.values).toEqual([
      1, 1,
    ]);
  });
  it("applies contributor classification filters without changing event counts", () => {
    expect(
      plot({ metric: "contributors", classifications: ["human"] }).series[0]!
        .values,
    ).toEqual([1, 1]);
    expect(
      plot({
        metric: "contributors",
        types: ACTIVITY_TYPES,
        classifications: ["bot"],
        combined: true,
      }).series.at(-1)!.values,
    ).toEqual([1, 1]);
    expect(plot({ classifications: [] }).series[0]!.values).toEqual([2, 2]);
    expect(
      plot({ metric: "contributors", classifications: [] }).series[0]!.values,
    ).toEqual([0, 0]);
  });
  it("identifies partial reports and supports no selected types", () => {
    const report = fixture();
    report.metadata.ongoing = true;
    const result = aggregate(
      loadReports([{ name: "partial.json", text: JSON.stringify(report) }]),
      { ...defaults, end: "2024-02", types: [], combined: true },
    );
    expect(result.partial).toEqual(["2024-02"]);
    expect(result.series).toEqual([]);
  });
  it("crosses year boundaries and handles the maximum four-digit year", () => {
    expect(
      aggregate(
        {
          schema_version: "1",
          repository: {
            node_id: "r",
            full_name: "o/r",
            url: "https://example.org",
          },
          actors: {},
          months: [],
        },
        { ...defaults, start: "2023-12", end: "2024-01" },
      ).months,
    ).toEqual(["2023-12", "2024-01"]);
    expect(
      aggregate(
        {
          schema_version: "1",
          repository: {
            node_id: "r",
            full_name: "o/r",
            url: "https://example.org",
          },
          actors: {},
          months: [],
        },
        { ...defaults, start: "9999-12", end: "9999-12" },
      ).months,
    ).toEqual(["9999-12"]);
  });
  it("rejects invalid and reversed ranges", () => {
    expect(() => plot({ start: "2024-04" })).toThrow("valid start month");
    expect(() => plot({ start: "" })).toThrow("valid start month");
  });
});

it("matches the event reference for every measure, identity filter, mode and selected ranges", () => {
  const first = fixture();
  const empty = fixture("2024-04", true);
  const last = fixture("2024-05");
  last.metadata.ongoing = true;
  last.actors.find((a) => a.key === "U_author")!.key = "U_new";
  for (const activity of last.activities)
    for (const credit of activity.credits) {
      if (credit.actor_key === "U_author") credit.actor_key = "U_new";
    }
  const full = [first, empty, last];
  const manifest = toManifest(full);
  const selections = [
    [],
    ...ACTIVITY_TYPES.map((t) => [t]),
    ["commit", "pr_opened"],
    ACTIVITY_TYPES,
  ] as ViewState["types"][];
  for (const metric of [
    "activities",
    "commits",
    "github-activities",
    "contributors",
    "total-contributors",
    "balance",
  ] as const) {
    for (const mode of ["monthly", "cumulative"] as const) {
      if (metric === "balance" && mode === "monthly") continue;
      for (let mask = 0; mask < 8; mask++) {
        const classifications = (
          ["human", "bot", "unresolved_git"] as const
        ).filter((_, i) => mask & (1 << i));
        for (const types of selections)
          for (const combined of [false, true]) {
            for (const [start, end] of [
              ["2024-02", "2024-05"],
              ["2024-03", "2024-05"],
              ["2024-05", "2024-05"],
            ]) {
              const state: ViewState = {
                ...defaults,
                metric,
                mode,
                classifications,
                types,
                combined,
                start: start!,
                end: end!,
              };
              expect(aggregate(manifest, state)).toEqual(
                eventAggregate(full, state),
              );
            }
          }
      }
    }
  }
});

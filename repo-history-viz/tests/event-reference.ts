import {
  ACTIVITY_LABELS,
  ACTIVITY_TYPES,
  type ActivityType,
  type Classification,
  nextMonth,
} from "../src/report.js";

export interface ViewState {
  metric:
    | "activities"
    | "commits"
    | "github-activities"
    | "contributors-by-type"
    | "total-contributors"
    | "balance";
  mode: "monthly" | "cumulative";
  types: ActivityType[];
  classifications: Classification[];
  combined: boolean;
  balanceTypes?: ("issues" | "prs")[];
  start: string;
  end: string;
}
export interface Series<
  K extends ActivityType | "combined" | "issues" | "prs" =
    ActivityType | "combined" | "issues" | "prs",
> {
  key: K;
  label: string;
  values: (number | null)[];
}
export interface PlotData {
  months: string[];
  series: Series[];
  missing: string[];
  partial: string[];
}

export function eventAggregate(
  reports: import("./fixtures").FullReport[],
  state: ViewState,
): PlotData {
  if (
    !/^\d{4}-(0[1-9]|1[0-2])$/.test(state.start) ||
    !/^\d{4}-(0[1-9]|1[0-2])$/.test(state.end) ||
    state.start > state.end
  ) {
    throw new Error("Choose a valid start month on or before the end month.");
  }
  const months: string[] = [];
  for (let month = state.start; ; month = nextMonth(month)) {
    months.push(month);
    if (month === state.end) break;
  }
  const byMonth = new Map(
    reports.map((report) => [report.metadata.month, report]),
  );
  if (state.metric === "balance") {
    if (state.mode !== "cumulative")
      throw new Error("Issue / PR balance requires cumulative mode.");
    const series: Series<"issues" | "prs">[] = (
      state.balanceTypes ?? ["issues", "prs"]
    ).map((key) => ({
      key,
      label: key === "issues" ? "Issue balance" : "PR balance",
      values: [],
    }));
    const totals = { issues: 0, prs: 0 };
    const missing: string[] = [];
    const partial: string[] = [];
    for (const month of months) {
      const report = byMonth.get(month);
      if (!report) {
        missing.push(month);
        series.forEach((item) => item.values.push(null));
        continue;
      }
      if (report.metadata.is_partial) partial.push(month);
      for (const activity of report.activities) {
        if (
          activity.type === "issue_opened" ||
          activity.type === "issue_reopened"
        )
          totals.issues++;
        if (activity.type === "issue_closed") totals.issues--;
        if (activity.type === "pr_opened") totals.prs++;
        if (activity.type === "pr_merged") totals.prs--;
      }
      series.forEach((item) => item.values.push(totals[item.key]));
    }
    return { months, series, missing, partial };
  }
  const totalContributors = state.metric === "total-contributors";
  const types: ActivityType[] = totalContributors
    ? ACTIVITY_TYPES
    : state.metric === "commits"
      ? ["commit"]
      : state.metric === "github-activities"
        ? state.types.filter((type) => type !== "commit")
        : state.types;
  const keys: (ActivityType | "combined")[] = totalContributors
    ? ["combined"]
    : [
        ...types,
        ...(state.combined && state.metric !== "commits" && types.length
          ? ["combined" as const]
          : []),
      ];
  const series: Series<ActivityType | "combined">[] = keys.map((key) => ({
    key,
    label:
      key === "combined"
        ? totalContributors
          ? "Total contributors"
          : "Combined total"
        : ACTIVITY_LABELS[key],
    values: [],
  }));
  const runningCounts = new Map(keys.map((key) => [key, 0]));
  const runningActors = new Map(keys.map((key) => [key, new Set<string>()]));
  const missing: string[] = [];
  const partial: string[] = [];
  for (const month of months) {
    const report = byMonth.get(month);
    if (!report) {
      missing.push(month);
      for (const item of series) item.values.push(null);
      continue;
    }
    if (report.metadata.is_partial) partial.push(month);
    const classes = new Map(
      report.actors.map((actor) => [actor.key, actor.classification]),
    );
    const counts = new Map(keys.map((key) => [key, 0]));
    const actors = new Map(keys.map((key) => [key, new Set<string>()]));
    for (const activity of report.activities) {
      if (!types.includes(activity.type)) continue;
      if (state.metric === "github-activities") {
        // Missing performers belong to Unknown actors; count each event once.
        const matches = activity.credits.length
          ? activity.credits.some((credit) =>
              state.classifications.includes(
                credit.actor_key === null
                  ? "unlinked_git"
                  : (classes.get(credit.actor_key) ?? "unlinked_git"),
              ),
            )
          : state.classifications.includes("unlinked_git");
        if (!matches) continue;
      }
      const targets: (ActivityType | "combined")[] = totalContributors
        ? ["combined"]
        : state.combined && state.metric !== "commits"
          ? [activity.type, "combined"]
          : [activity.type];
      for (const key of targets) {
        counts.set(key, counts.get(key)! + 1);
        for (const credit of activity.credits) {
          if (
            credit.actor_key !== null &&
            state.classifications.includes(classes.get(credit.actor_key)!)
          ) {
            actors.get(key)!.add(credit.actor_key);
          }
        }
      }
    }
    for (const item of series) {
      const key = item.key;
      runningCounts.set(key, runningCounts.get(key)! + counts.get(key)!);
      for (const actor of actors.get(key)!) runningActors.get(key)!.add(actor);
      item.values.push(
        state.metric === "activities" ||
          state.metric === "commits" ||
          state.metric === "github-activities"
          ? state.mode === "monthly"
            ? counts.get(key)!
            : runningCounts.get(key)!
          : state.mode === "monthly"
            ? actors.get(key)!.size
            : runningActors.get(key)!.size,
      );
    }
  }
  return { months, series, missing, partial };
}

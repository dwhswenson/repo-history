import mixed from "../../tests/fixtures/mixed-2024-02.json" with { type: "json" };
import empty from "../../tests/fixtures/empty-2024-02.json" with { type: "json" };
import {
  nextMonth,
  ACTIVITY_TYPES,
  parseManifest,
  type Manifest,
  type Counts,
  type Population,
  type ReportMetadata,
  type Summaries,
} from "../src/report";

export function fixture(month = "2024-02", isEmpty = false) {
  const report = structuredClone(isEmpty ? empty : mixed);
  report.metadata.month = month;
  report.metadata.month_start = `${month}-01T00:00:00Z`;
  report.metadata.month_end = `${nextMonth(month)}-01T00:00:00Z`;
  report.metadata.coverage.as_of = `${nextMonth(month)}-02T12:00:00Z`;
  for (const run of Object.values(report.metadata.provenance)) {
    run.run_id = `${run.run_id.slice(0, -4)}${month.replace("-", "").slice(-4)}`;
    run.started_at = `${nextMonth(month)}-02T12:00:00Z`;
    run.finished_at = `${nextMonth(month)}-02T12:05:00Z`;
  }
  for (const activity of report.activities) {
    activity.occurred_at = activity.occurred_at.replace("2024-02", month);
    activity.id += `:${month}`;
  }
  return report;
}
export function source(month = "2024-02", isEmpty = false) {
  return {
    name: `${month}.json`,
    text: JSON.stringify(fixture(month, isEmpty)),
  };
}

export type FullReport = ReturnType<typeof fixture>;
export function toManifest(reports: FullReport[]): Manifest {
  const actors: Manifest["actors"] = Object.create(null);
  const months = reports.map((report) => {
    const perActor: Record<string, Counts> = Object.create(null);
    const counts = Object.fromEntries(
      ACTIVITY_TYPES.map((k) => [k, 0]),
    ) as Counts;
    const unattributed = { ...counts };
    const registry = new Map(
      report.actors.map((a) => [a.key, a.classification]),
    );
    for (const activity of report.activities) {
      counts[activity.type]++;
      const keys = new Set(
        activity.credits.flatMap((c) =>
          c.actor_key === null ? [] : [c.actor_key],
        ),
      );
      if (!keys.size) unattributed[activity.type]++;
      for (const key of keys) {
        perActor[key] ??= Object.fromEntries(
          ACTIVITY_TYPES.map((k) => [k, 0]),
        ) as Counts;
        perActor[key][activity.type]++;
        const classification = registry.get(key)!;
        if (Object.hasOwn(actors, key) && actors[key] !== classification)
          throw new Error("conflicting classifications");
        actors[key] = classification;
      }
    }
    const population = (keys: string[]): Population => ({
      total: keys.length,
      human: keys.filter((k) => actors[k] === "human").length,
      bot: keys.filter((k) => actors[k] === "bot").length,
      unresolved_git: keys.filter((k) => actors[k] === "unresolved_git").length,
    });
    const metadata = structuredClone(report.metadata) as ReportMetadata;
    // Tests may mark snapshots ongoing after creating their activity fixtures.
    if (metadata.ongoing) {
      metadata.coverage.as_of = `${metadata.month}-15T12:00:00Z`;
      for (const run of Object.values(metadata.provenance)) {
        run.started_at = metadata.coverage.as_of;
        run.finished_at = `${metadata.month}-15T12:05:00Z`;
      }
    }
    const summaries: Summaries = {
      repository_counts: counts,
      actor_counts: perActor,
      distinct_actors: population(Object.keys(perActor)),
      distinct_actors_by_type: Object.fromEntries(
        ACTIVITY_TYPES.map((k) => [
          k,
          population(
            Object.keys(perActor).filter((key) => perActor[key]![k] > 0),
          ),
        ]),
      ) as Record<(typeof ACTIVITY_TYPES)[number], Population>,
      unattributed_total: Object.values(unattributed).reduce(
        (a, b) => a + b,
        0,
      ),
      unattributed_by_type: unattributed,
    };
    return {
      metadata: {
        month: metadata.month,
        month_start: metadata.month_start,
        month_end: metadata.month_end,
        ongoing: metadata.ongoing,
      },
      summaries,
      source_reports: [{ metadata, summaries: structuredClone(summaries) }],
    };
  });
  const latest = [...reports]
    .sort((a, b) => a.metadata.month.localeCompare(b.metadata.month))
    .at(-1)!;
  return {
    schema_version: "1",
    display_name: latest.metadata.repository.full_name,
    repositories: [
      latest.metadata.repository as Manifest["repositories"][number],
    ],
    actors,
    months,
  };
}
export function fromReports(
  sources: { name: string; text: string }[],
): Manifest {
  return parseManifest({
    name: "manifest.json",
    text: JSON.stringify(toManifest(sources.map((s) => JSON.parse(s.text)))),
  });
}
export function manifestSource(month = "2024-02", isEmpty = false) {
  return {
    name: "manifest.json",
    text: JSON.stringify(toManifest([fixture(month, isEmpty)])),
  };
}
export function combinedSource(reports: FullReport[]) {
  return { name: "manifest.json", text: JSON.stringify(toManifest(reports)) };
}

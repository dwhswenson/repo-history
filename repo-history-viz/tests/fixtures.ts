import mixed from "../../tests/fixtures/mixed-2024-02.json" with { type: "json" };
import empty from "../../tests/fixtures/empty-2024-02.json" with { type: "json" };
import {
  nextMonth,
  ACTIVITY_TYPES,
  parseManifest,
  type Manifest,
  type Counts,
  type Population,
} from "../src/report";

export function fixture(month = "2024-02", isEmpty = false) {
  const report = structuredClone(isEmpty ? empty : mixed);
  report.metadata.month = month;
  report.metadata.month_start = `${month}-01T00:00:00Z`;
  report.metadata.month_end = `${nextMonth(month)}-01T00:00:00Z`;
  report.metadata.collection_started_at = `${nextMonth(month)}-02T12:00:00Z`;
  report.metadata.collection_finished_at = `${nextMonth(month)}-02T12:05:00Z`;
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
    const metadata = { ...report.metadata };
    // Match the source contract when tests mark a snapshot as ongoing.
    if (
      metadata.ongoing &&
      (Date.parse(metadata.collection_started_at) <
        Date.parse(metadata.month_start) ||
        Date.parse(metadata.collection_started_at) >=
          Date.parse(metadata.month_end))
    ) {
      metadata.collection_started_at = `${metadata.month}-15T12:00:00Z`;
      metadata.collection_finished_at = `${metadata.month}-15T12:05:00Z`;
    }
    return {
      metadata: {
        month: metadata.month,
        month_start: metadata.month_start,
        month_end: metadata.month_end,
        collection_started_at: metadata.collection_started_at,
        collection_finished_at: metadata.collection_finished_at,
        ongoing: metadata.ongoing,
        coverage_limitations: metadata.coverage_limitations,
      },
      summaries: {
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
      },
    };
  });
  const latest = [...reports]
    .sort((a, b) => a.metadata.month.localeCompare(b.metadata.month))
    .at(-1)!;
  return {
    schema_version: "1",
    repository: {
      node_id: latest.metadata.repository_node_id,
      full_name: latest.metadata.repository_full_name,
      url: latest.metadata.repository_url,
    },
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

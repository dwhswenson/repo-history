import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import common from "../../src/repo_history/schemas/common-v1.json" with { type: "json" };
import schema from "../../src/repo_history/schemas/viz-manifest-v1.json" with { type: "json" };

export const ACTIVITY_LABELS = {
  commit: "Commits",
  issue_opened: "Issues opened",
  issue_closed: "Issues closed",
  issue_reopened: "Issues reopened",
  pr_opened: "Pull requests opened",
  pr_merged: "Pull requests merged",
  issue_comment: "Issue comments",
  pr_conversation_comment: "PR conversation comments",
  pr_review: "PR reviews",
  pr_inline_review_comment: "Inline review comments",
} as const;
export type ActivityType = keyof typeof ACTIVITY_LABELS;
export const ACTIVITY_TYPES = Object.keys(ACTIVITY_LABELS) as ActivityType[];
export type Classification = "human" | "bot" | "unresolved_git";

export type Counts = Record<ActivityType, number>;
export interface Population {
  total: number;
  human: number;
  bot: number;
  unresolved_git: number;
}
export interface CollectionRun {
  run_id: string;
  command:
    "backfill" | "update" | "collect_history" | "collect_month" | "manual";
  mode: "rebuild" | "incremental";
  collector_version: string;
  started_at: string;
  finished_at: string;
  collection_since: string | null;
  collection_until: string | null;
  requested_start_month: string | null;
  requested_end_month: string | null;
  parent_discovery: "full" | "bounded" | "incremental" | null;
  commit_history: "full" | "bounded" | "incremental" | null;
}
export interface RepositoryMetadata {
  node_id: string;
  full_name: string;
  url: string;
  created_at: string;
  history_start_month: string | null;
  history_start_established_by: CollectionRun | null;
}
export interface CalendarMonth {
  month: string;
  month_start: string;
  month_end: string;
  ongoing: boolean;
}
export interface ReportMetadata extends CalendarMonth {
  repository: RepositoryMetadata;
  provenance: Record<
    "created_by" | "last_updated_by" | "last_rebuilt_by",
    CollectionRun
  >;
  coverage: { as_of: string; limitations: string[] };
  default_branch: string | null;
  default_branch_head: string | null;
}
export interface Summaries {
  repository_counts: Counts;
  actor_counts: Record<string, Counts>;
  distinct_actors: Population;
  distinct_actors_by_type: Record<ActivityType, Population>;
  unattributed_total: number;
  unattributed_by_type: Counts;
}
export interface MonthSummary {
  metadata: CalendarMonth;
  summaries: Summaries;
  source_reports: { metadata: ReportMetadata; summaries: Summaries }[];
}
export interface Manifest {
  schema_version: "1";
  display_name: string;
  repositories: RepositoryMetadata[];
  actors: Record<string, Classification>;
  months: MonthSummary[];
}
export interface ManifestSource {
  name: string;
  text: string;
}
const ajv = new Ajv2020({
  allErrors: true,
  strict: false,
  ownProperties: true,
});
addFormats(ajv);
ajv.addSchema(common);
const validate = ajv.compile<Manifest>(schema);
export function nextMonth(month: string): string {
  const [year, number] = month.split("-").map(Number);
  return number === 12
    ? `${String(year! + 1).padStart(4, "0")}-01`
    : `${String(year).padStart(4, "0")}-${String(number! + 1).padStart(2, "0")}`;
}

export function parseManifest(source: ManifestSource): Manifest {
  const fail = (message: string): never => {
    throw new Error(`${source.name}: ${message}`);
  };
  let value: unknown;
  try {
    value = JSON.parse(source.text);
  } catch {
    return fail("invalid JSON.");
  }
  if (
    typeof value === "object" &&
    value !== null &&
    ("files" in value || "activities" in value)
  ) {
    return fail(
      "Load a summary manifest generated with repo-history summarize --input-directory REPORTS.",
    );
  }
  if (!validate(value)) {
    return fail(
      `invalid v1 visualization manifest — ${validate.errors
        ?.slice(0, 3)
        .map((e) => `${e.instancePath || "/"} ${e.message}`)
        .join("; ")}`,
    );
  }
  const seen = new Set<string>();
  const used = new Set<string>();
  const runs = new Map<string, CollectionRun>();
  const same = (a: unknown, b: unknown): boolean => {
    if (a === b) return true;
    if (!a || !b || typeof a !== "object" || typeof b !== "object")
      return false;
    const x = a as Record<string, unknown>,
      y = b as Record<string, unknown>;
    return (
      Object.keys(x).length === Object.keys(y).length &&
      Object.keys(x).every((k) => Object.hasOwn(y, k) && same(x[k], y[k]))
    );
  };
  const validMonth = (month: string) => {
    if (month.startsWith("0000-") || month === "9999-12")
      fail("unsupported calendar month.");
  };
  const calendar = (m: CalendarMonth) => {
    validMonth(m.month);
    if (
      Date.parse(m.month_start) !== Date.parse(`${m.month}-01T00:00:00Z`) ||
      Date.parse(m.month_end) !==
        Date.parse(`${nextMonth(m.month)}-01T00:00:00Z`)
    )
      fail("month boundaries must match reporting month in UTC.");
  };
  const checkRun = (run: CollectionRun) => {
    const previous = runs.get(run.run_id);
    if (previous && !same(previous, run))
      fail("conflicting descriptors for run ID.");
    runs.set(run.run_id, run);
    if (Date.parse(run.started_at) > Date.parse(run.finished_at))
      fail("invalid collection interval.");
    if (
      run.collection_since &&
      run.collection_until &&
      Date.parse(run.collection_since) >= Date.parse(run.collection_until)
    )
      fail("invalid collection bounds.");
    for (const m of [run.requested_start_month, run.requested_end_month])
      if (m) validMonth(m);
    if (
      run.requested_start_month &&
      run.requested_end_month &&
      run.requested_start_month > run.requested_end_month
    )
      fail("invalid requested month range.");
  };
  const checkRepository = (repo: RepositoryMetadata) => {
    const proof = repo.history_start_established_by,
      start = repo.history_start_month;
    if ((start === null) !== (proof === null))
      fail("history start and evidence must both be present or null.");
    if (proof && start) {
      checkRun(proof);
      validMonth(start);
      if (
        proof.mode !== "rebuild" ||
        proof.collection_since !== null ||
        proof.parent_discovery !== "full" ||
        proof.commit_history !== "full"
      )
        fail("history start requires full-history evidence.");
      if (start > repo.created_at.slice(0, 7))
        fail("history starts after repository creation month.");
    }
  };
  const repositories = new Map<string, RepositoryMetadata>();
  const sourceRepositories = new Map<string, RepositoryMetadata[]>();
  for (const repo of value.repositories) {
    if (repositories.has(repo.node_id)) fail("duplicate repository.");
    checkRepository(repo);
    repositories.set(repo.node_id, repo);
    sourceRepositories.set(repo.node_id, []);
  }
  const population = (keys: string[]): Population => ({
    total: keys.length,
    human: keys.filter((k) => value.actors[k] === "human").length,
    bot: keys.filter((k) => value.actors[k] === "bot").length,
    unresolved_git: keys.filter((k) => value.actors[k] === "unresolved_git")
      .length,
  });
  const samePopulation = (actual: Population, expected: Population) =>
    (Object.keys(expected) as (keyof Population)[]).every(
      (k) => actual[k] === expected[k],
    );
  const checkSummary = (s: Summaries) => {
    const entries = Object.entries(s.actor_counts);
    for (const [key, counts] of entries) {
      if (!Object.hasOwn(value.actors, key)) fail(`unknown actor ${key}.`);
      if (!Object.values(counts).some((n) => n > 0))
        fail(`actor ${key} has no participation.`);
      used.add(key);
    }
    if (!samePopulation(s.distinct_actors, population(entries.map(([k]) => k))))
      fail("distinct actor population disagrees with actor counts.");
    if (
      s.unattributed_total !==
      Object.values(s.unattributed_by_type).reduce((a, b) => a + b, 0)
    )
      fail("unattributed total disagrees with type counts.");
    for (const type of ACTIVITY_TYPES) {
      const keys = entries
        .filter(([, counts]) => counts[type] > 0)
        .map(([k]) => k);
      if (!samePopulation(s.distinct_actors_by_type[type], population(keys)))
        fail(`${type} population disagrees with actor counts.`);
      const total = s.repository_counts[type],
        unattributed = s.unattributed_by_type[type];
      if (unattributed > total)
        fail(`${type} unattributed exceeds repository count.`);
      if (entries.some(([, counts]) => counts[type] > total))
        fail(`${type} actor count exceeds repository count.`);
      const attributed = entries.reduce(
        (sum, [, counts]) => sum + counts[type],
        0,
      );
      if (
        type === "commit"
          ? attributed < total - unattributed
          : attributed + unattributed !== total
      )
        fail(`${type} repository count disagrees with attribution.`);
    }
  };
  for (const entry of value.months) {
    const m = entry.metadata;
    if (seen.has(m.month)) fail(`duplicate month ${m.month}.`);
    seen.add(m.month);
    calendar(m);
    const present = new Set<string>();
    const totals = Object.fromEntries(
      ACTIVITY_TYPES.map((t) => [t, 0]),
    ) as Counts;
    const unattributed = { ...totals };
    const actorTotals = new Map<string, Counts>();
    for (const source of entry.source_reports) {
      const meta = source.metadata,
        repo = repositories.get(meta.repository.node_id);
      if (!repo) fail("unknown source repository.");
      const registered = repo!;
      if (present.has(registered.node_id))
        fail("duplicate repository source report.");
      present.add(registered.node_id);
      calendar(meta);
      checkRepository(meta.repository);
      sourceRepositories.get(registered.node_id)!.push(meta.repository);
      if (
        ["month", "month_start", "month_end"].some(
          (k) => meta[k as keyof CalendarMonth] !== m[k as keyof CalendarMonth],
        )
      )
        fail("source month disagrees with aggregate month.");
      if (meta.repository.created_at !== registered.created_at)
        fail("conflicting repository creation timestamp.");
      const p = meta.provenance;
      for (const r of Object.values(p)) checkRun(r);
      if (
        Date.parse(p.created_by.started_at) >
          Date.parse(p.last_rebuilt_by.started_at) ||
        Date.parse(p.last_rebuilt_by.started_at) >
          Date.parse(p.last_updated_by.started_at)
      )
        fail("invalid provenance chronology.");
      if (
        p.created_by.mode !== "rebuild" ||
        p.last_rebuilt_by.mode !== "rebuild" ||
        (p.last_updated_by.mode === "rebuild" &&
          !same(p.last_updated_by, p.last_rebuilt_by))
      )
        fail("invalid rebuild provenance.");
      const asOf = Date.parse(meta.coverage.as_of);
      if (
        asOf < Date.parse(meta.month_start) ||
        asOf < Date.parse(p.last_updated_by.started_at) ||
        asOf > Date.parse(p.last_updated_by.finished_at)
      )
        fail("invalid coverage observation time.");
      if (meta.ongoing !== asOf < Date.parse(meta.month_end))
        fail("ongoing must reflect coverage as_of.");
      if (
        (meta.default_branch === null) !==
        (meta.default_branch_head === null)
      )
        fail("branch and head must both be present or null.");
      if (
        meta.repository.history_start_month &&
        meta.month < meta.repository.history_start_month &&
        Object.values(source.summaries.repository_counts).some((n) => n > 0)
      )
        fail("activity precedes established history start.");
      checkSummary(source.summaries);
      for (const type of ACTIVITY_TYPES) {
        totals[type] += source.summaries.repository_counts[type];
        unattributed[type] += source.summaries.unattributed_by_type[type];
      }
      for (const [key, counts] of Object.entries(
        source.summaries.actor_counts,
      )) {
        if (!actorTotals.has(key))
          actorTotals.set(
            key,
            Object.fromEntries(ACTIVITY_TYPES.map((t) => [t, 0])) as Counts,
          );
        for (const type of ACTIVITY_TYPES)
          actorTotals.get(key)![type] += counts[type];
      }
    }
    if (repositories.size > 1)
      for (const repo of repositories.values()) {
        if (
          !present.has(repo.node_id) &&
          (!repo.history_start_month || m.month >= repo.history_start_month)
        )
          fail(`missing report for ${repo.node_id} in ${m.month}.`);
      }
    if (m.ongoing !== entry.source_reports.some((s) => s.metadata.ongoing))
      fail("ongoing disagrees with sources.");
    checkSummary(entry.summaries);
    if (
      !same(entry.summaries.repository_counts, totals) ||
      !same(entry.summaries.unattributed_by_type, unattributed) ||
      !same(entry.summaries.actor_counts, Object.fromEntries(actorTotals))
    )
      fail("aggregate summary disagrees with source reports.");
  }
  if (repositories.size > 1) {
    const months = [...seen].sort();
    for (
      let month = months[0]!;
      month <= months.at(-1)!;
      month = nextMonth(month)
    ) {
      if (!seen.has(month)) fail(`missing aggregate month ${month}.`);
    }
  }
  for (const [key, sources] of sourceRepositories) {
    const known = sources
      .flatMap((r) => (r.history_start_month ? [r.history_start_month] : []))
      .sort();
    if (known.length && repositories.get(key)!.history_start_month !== known[0])
      fail("registry must retain earliest source history boundary.");
  }
  if (used.size !== Object.keys(value.actors).length)
    fail("classification lookup contains unused actors.");
  value.months.sort((a, b) => a.metadata.month.localeCompare(b.metadata.month));
  return value;
}

export async function loadManifest(
  input: string,
  baseUrl: string,
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<Manifest> {
  const url = new URL(input, baseUrl);
  if (!["http:", "https:"].includes(url.protocol))
    throw new Error("Use an HTTP or HTTPS manifest URL.");
  const response = await fetcher(url.href, {
    credentials: "omit",
    ...(signal ? { signal } : {}),
  });
  if (!response.ok) throw new Error(`Manifest: HTTP ${response.status}.`);
  return parseManifest({ name: "Manifest", text: await response.text() });
}

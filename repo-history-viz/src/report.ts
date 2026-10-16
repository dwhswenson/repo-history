import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
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
export interface MonthSummary {
  metadata: {
    month: string;
    month_start: string;
    month_end: string;
    collection_started_at: string;
    collection_finished_at: string;
    ongoing: boolean;
    coverage_limitations: string[];
  };
  summaries: {
    repository_counts: Counts;
    actor_counts: Record<string, Counts>;
    distinct_actors: Population;
    distinct_actors_by_type: Record<ActivityType, Population>;
    unattributed_total: number;
    unattributed_by_type: Counts;
  };
}
export interface Manifest {
  schema_version: "1";
  repository: { node_id: string; full_name: string; url: string };
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
  for (const { metadata: m, summaries: s } of value.months) {
    if (seen.has(m.month)) fail(`duplicate month ${m.month}.`);
    seen.add(m.month);
    if (
      m.month.startsWith("0000-") ||
      m.month === "9999-12" ||
      Date.parse(m.month_start) !== Date.parse(`${m.month}-01T00:00:00Z`) ||
      Date.parse(m.month_end) !==
        Date.parse(`${nextMonth(m.month)}-01T00:00:00Z`)
    )
      fail("month boundaries must match reporting month in UTC.");
    const start = Date.parse(m.month_start),
      end = Date.parse(m.month_end);
    const collected = Date.parse(m.collection_started_at),
      finished = Date.parse(m.collection_finished_at);
    if (!(start <= collected && collected <= finished))
      fail("invalid collection interval.");
    if (m.ongoing !== (start <= collected && collected < end))
      fail("ongoing must reflect collection start.");
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

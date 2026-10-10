import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import schema from "../../src/repo_history/schemas/monthly-report-v1.json" with { type: "json" };

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

// The viewer consumes this subset; the shared JSON schema validates the full report.
export interface MonthlyReport {
  schema_version: "1";
  metadata: {
    repository_node_id: string;
    repository_full_name: string;
    repository_url: string;
    month: string;
    month_start: string;
    month_end: string;
    collection_started_at: string;
    collection_finished_at: string;
    ongoing: boolean;
    coverage_limitations: string[];
  };
  actors: { key: string; classification: Classification }[];
  activities: {
    id: string;
    type: ActivityType;
    occurred_at: string;
    credits: { actor_key: string | null; role: string }[];
    subject: { author_key: string | null } | null;
  }[];
}
export interface ReportSource {
  name: string;
  text: string;
}

const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats(ajv);
const validate = ajv.compile<MonthlyReport>(schema);

export function nextMonth(month: string): string {
  const [year, number] = month.split("-").map(Number);
  return number === 12
    ? `${String(year! + 1).padStart(4, "0")}-01`
    : `${String(year).padStart(4, "0")}-${String(number! + 1).padStart(2, "0")}`;
}

export function parseReport(source: ReportSource): MonthlyReport {
  let value: unknown;
  try {
    value = JSON.parse(source.text);
  } catch {
    throw new Error(`${source.name}: invalid JSON.`);
  }
  if (!validate(value)) {
    const errors = validate.errors
      ?.slice(0, 3)
      .map((error) => `${error.instancePath || "/"} ${error.message}`)
      .join("; ");
    throw new Error(`${source.name}: invalid v1 report — ${errors}`);
  }
  const fail = (message: string): never => {
    throw new Error(`${source.name}: ${message}`);
  };
  const month = value.metadata.month;
  if (month.startsWith("0000-")) fail("month must have a positive year.");
  if (
    Date.parse(value.metadata.month_start) !==
      Date.parse(`${month}-01T00:00:00Z`) ||
    Date.parse(value.metadata.month_end) !==
      Date.parse(`${nextMonth(month)}-01T00:00:00Z`)
  ) {
    fail("month boundaries must match the reporting month in UTC.");
  }
  const actors = new Set<string>();
  for (const actor of value.actors) {
    if (actors.has(actor.key)) fail(`duplicate actor key ${actor.key}.`);
    actors.add(actor.key);
  }
  const ids = new Set<string>();
  for (const activity of value.activities) {
    if (ids.has(activity.id)) fail(`duplicate activity ID ${activity.id}.`);
    ids.add(activity.id);
    if (
      Date.parse(activity.occurred_at) <
        Date.parse(value.metadata.month_start) ||
      Date.parse(activity.occurred_at) >= Date.parse(value.metadata.month_end)
    ) {
      fail(`activity ${activity.id} falls outside its reporting month.`);
    }
    for (const credit of activity.credits) {
      if (credit.actor_key !== null && !actors.has(credit.actor_key)) {
        fail(
          `activity ${activity.id} references unknown actor ${credit.actor_key}.`,
        );
      }
    }
  }
  return value;
}

export function loadReports(sources: ReportSource[]): MonthlyReport[] {
  if (!sources.length) throw new Error("Choose at least one monthly report.");
  const reports = sources.map(parseReport);
  const repository = reports[0]!.metadata.repository_node_id;
  const months = new Set<string>();
  const classifications = new Map<string, Classification>();
  for (const [index, report] of reports.entries()) {
    const fail = (message: string): never => {
      throw new Error(`${sources[index]!.name}: ${message}`);
    };
    if (report.metadata.repository_node_id !== repository)
      fail("all reports must belong to the same repository node ID.");
    if (months.has(report.metadata.month))
      fail(`duplicate report for ${report.metadata.month}.`);
    months.add(report.metadata.month);
    for (const actor of report.actors) {
      const previous = classifications.get(actor.key);
      if (previous && previous !== actor.classification)
        fail(`conflicting classifications for actor ${actor.key}.`);
      classifications.set(actor.key, actor.classification);
    }
  }
  return reports.sort((a, b) =>
    a.metadata.month.localeCompare(b.metadata.month),
  );
}

export async function loadManifest(
  input: string,
  baseUrl: string,
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<MonthlyReport[]> {
  const manifestUrl = new URL(input, baseUrl);
  if (!["http:", "https:"].includes(manifestUrl.protocol))
    throw new Error("Use an HTTP or HTTPS manifest URL.");
  const response = await fetcher(manifestUrl.href, {
    credentials: "omit",
    ...(signal ? { signal } : {}),
  });
  if (!response.ok) throw new Error(`Manifest: HTTP ${response.status}.`);
  let manifest: unknown;
  try {
    manifest = await response.json();
  } catch {
    throw new Error("Manifest: invalid JSON.");
  }
  if (
    typeof manifest !== "object" ||
    manifest === null ||
    !("files" in manifest) ||
    !Array.isArray(manifest.files) ||
    !manifest.files.length ||
    !manifest.files.every((file) => typeof file === "string" && file.trim())
  ) {
    throw new Error(
      'Manifest must contain a nonempty "files" array of report paths.',
    );
  }
  // Resolve against the final URL so redirects to directory URLs work as expected.
  const files = manifest.files as string[];
  const sources = await Promise.all(
    files.map(async (file) => {
      const url = new URL(file, response.url || manifestUrl.href);
      if (!["http:", "https:"].includes(url.protocol))
        throw new Error(`${file}: use an HTTP or HTTPS report URL.`);
      const reportResponse = await fetcher(url.href, {
        credentials: "omit",
        ...(signal ? { signal } : {}),
      });
      if (!reportResponse.ok)
        throw new Error(`${file}: HTTP ${reportResponse.status}.`);
      return { name: file, text: await reportResponse.text() };
    }),
  );
  return loadReports(sources);
}

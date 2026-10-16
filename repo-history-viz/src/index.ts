export { RepoHistoryViz, defineRepoHistoryViz } from "./component.js";
export type { ManifestSource, Manifest, MonthSummary } from "./report.js";
export interface RepoHistoryLoadDetail {
  repository: string;
  months: string[];
}
export interface RepoHistoryErrorDetail {
  message: string;
}

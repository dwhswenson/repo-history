export { RepoHistoryViz, defineRepoHistoryViz } from "./component.js";
export type { ReportSource } from "./report.js";
export interface RepoHistoryLoadDetail {
  repository: string;
  months: string[];
}
export interface RepoHistoryErrorDetail {
  message: string;
}

export { RepoHistoryViz, defineRepoHistoryViz } from "./component.js";
export type {
  HistorySummarySource,
  HistorySummary,
  MonthSummary,
  CollectionRun,
  RepositoryMetadata,
  ReportMetadata,
  Summaries,
} from "./report.js";
export interface RepoHistoryLoadDetail {
  display_name: string;
  repositories: { node_id: string; full_name: string; url: string }[];
  months: string[];
}
export interface RepoHistoryErrorDetail {
  message: string;
}

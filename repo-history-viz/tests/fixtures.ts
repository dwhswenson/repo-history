import mixed from "../../tests/fixtures/mixed-2024-02.json" with { type: "json" };
import empty from "../../tests/fixtures/empty-2024-02.json" with { type: "json" };
import { nextMonth } from "../src/report";

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

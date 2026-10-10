import type { PlotData } from "./series.js";

export function chartCsv(data: PlotData): string {
  const rows = [
    ["Month (UTC)", "Report status", ...data.series.map((item) => item.label)],
    ...data.months.map((month, index) => [
      month,
      data.missing.includes(month)
        ? "No report"
        : data.partial.includes(month)
          ? "Partial"
          : "Collected",
      ...data.series.map((item) =>
        item.values[index] === null ? "No report" : String(item.values[index]),
      ),
    ]),
  ];
  return (
    rows
      .map((row) =>
        row.map((cell) => `"${cell.replace(/"/g, '""')}"`).join(","),
      )
      .join("\r\n") + "\r\n"
  );
}

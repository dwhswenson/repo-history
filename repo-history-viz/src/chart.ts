import {
  Chart,
  BarController,
  BarElement,
  LineController,
  LineElement,
  PointElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
} from "chart.js";
import { ACTIVITY_TYPES } from "./report.js";
import type { PlotData, ViewState } from "./series.js";

Chart.register(
  BarController,
  BarElement,
  LineController,
  LineElement,
  PointElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
);
// Selected IBM Carbon colors, kept stable by activity type.
// PR opens and reviews use Purple 80 and Purple 50 respectively.
export const COLORS = [
  "#002d9c", // Commits: Blue 80
  "#fa4d56", // Issues opened: Red 50
  "#005d5d", // Issues closed: Teal 70
  "#9f1853", // Issues reopened: Magenta 70
  "#491d8b", // PRs opened: Purple 80
  "#8a3800", // PRs merged: Orange 70
  "#198038", // Issue comments: Green 60
  "#b28600", // PR discussion comments: Yellow 50
  "#a56eff", // PR reviews: Purple 50
  "#1192e8", // PR review comments: Cyan 50
];

export function renderChart(
  canvas: HTMLCanvasElement,
  data: PlotData,
  state: ViewState,
): Chart<"line" | "bar", (number | null)[], string> {
  const partial = new Set(data.partial);
  const stacked =
    state.mode === "monthly" &&
    (state.metric === "activities" ||
      state.metric === "commits" ||
      state.metric === "github-activities");
  return new Chart<"line" | "bar", (number | null)[], string>(canvas, {
    type: state.mode === "monthly" ? "bar" : "line",
    data: {
      labels: data.months,
      datasets: data.series.map((item) => {
        const color =
          item.key === "combined"
            ? "#172554"
            : item.key === "issues"
              ? "#0072b2"
              : item.key === "prs"
                ? "#d55e00"
                : COLORS[ACTIVITY_TYPES.indexOf(item.key)]!;
        return {
          type: stacked && item.key === "combined" ? "line" : undefined,
          stack: stacked
            ? item.key === "combined"
              ? "total"
              : "activity-types"
            : undefined,
          label: item.label,
          data: item.values,
          borderColor: color,
          backgroundColor: color,
          borderWidth: item.key === "combined" ? 3 : 2,
          borderDash:
            item.key === "combined" && state.metric !== "total-contributors"
              ? [5, 4]
              : [],
          pointRadius: 0,
          pointHoverRadius: 5,
          spanGaps: false,
          tension: 0,
        };
      }),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          position: "bottom",
          labels: {
            boxWidth: 16,
            boxHeight: 16,
            padding: 16,
          },
          onClick: () => {},
        },
        tooltip: {
          callbacks: {
            title: (items) => {
              const month = items[0]?.label || "";
              return month + (partial.has(month) ? " · partial month" : "");
            },
          },
        },
      },
      scales: {
        x: {
          stacked,
          title: { display: true, text: "Month (UTC)" },
          grid: { display: false },
          ticks: { maxRotation: 45, autoSkip: true, maxTicksLimit: 18 },
        },
        y: {
          stacked,
          beginAtZero: true,
          title: {
            display: true,
            text:
              state.metric === "balance"
                ? "Net change since start"
                : state.metric === "commits"
                  ? "Commits"
                  : state.metric === "activities" ||
                      state.metric === "github-activities"
                    ? "Activities"
                    : "Unique contributors",
          },
          ticks: { precision: 0 },
        },
      },
    },
  });
}

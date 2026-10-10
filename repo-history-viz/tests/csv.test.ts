import { expect, it } from "vitest";
import { chartCsv } from "../src/csv";

it("exports zeros, partial months, and missing reports with CSV escaping", () => {
  expect(
    chartCsv({
      months: ["2024-01", "2024-02", "2024-03"],
      series: [
        { key: "commit", label: 'Commits, "all"\ntypes', values: [0, 2, null] },
      ],
      missing: ["2024-03"],
      partial: ["2024-02"],
    }),
  ).toBe(
    '"Month (UTC)","Report status","Commits, ""all""\ntypes"\r\n' +
      '"2024-01","Collected","0"\r\n' +
      '"2024-02","Partial","2"\r\n' +
      '"2024-03","No report","No report"\r\n',
  );
});

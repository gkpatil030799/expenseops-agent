import { describe, expect, it } from "vitest";

import { comparisonPercent, customGranularity, dateRangeForPreset } from "@/insightsLogic";

const now = new Date(2026, 7, 12, 10, 30);

describe("insights date ranges", () => {
  it("builds rolling ranges including today", () => {
    expect(dateRangeForPreset("7d", now)).toMatchObject({ start: "2026-08-06", end: "2026-08-12" });
    expect(dateRangeForPreset("30d", now)).toMatchObject({ start: "2026-07-14", end: "2026-08-12" });
    expect(dateRangeForPreset("90d", now)).toMatchObject({ start: "2026-05-15", end: "2026-08-12", granularity: "week" });
  });

  it("builds calendar month and quarter ranges", () => {
    expect(dateRangeForPreset("this_month", now)).toMatchObject({ start: "2026-08-01", end: "2026-08-12" });
    expect(dateRangeForPreset("last_month", now)).toMatchObject({ start: "2026-07-01", end: "2026-07-31" });
    expect(dateRangeForPreset("this_quarter", now)).toMatchObject({ start: "2026-07-01", end: "2026-08-12" });
    expect(dateRangeForPreset("last_quarter", now)).toMatchObject({ start: "2026-04-01", end: "2026-06-30" });
    expect(dateRangeForPreset("ytd", now)).toMatchObject({ start: "2026-01-01", end: "2026-08-12" });
  });

  it("selects deterministic custom granularity", () => {
    expect(customGranularity("2026-08-01", "2026-08-12")).toBe("day");
    expect(customGranularity("2026-01-01", "2026-03-31")).toBe("week");
    expect(customGranularity("2025-01-01", "2026-01-01")).toBe("month");
  });

  it("keeps unavailable comparisons neutral", () => {
    expect(comparisonPercent(100, 0)).toBeNull();
    expect(comparisonPercent(90, 100)).toBe(-10);
  });
});

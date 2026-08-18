import { describe, expect, it } from "vitest";

import {
  comparisonPercent,
  createLatestPresetRequestGate,
  customGranularity,
  dateRangeForPreset,
  insightsContextForPresetResolution,
  presetDateRangePath,
  type PresetDateRangeResponse,
} from "@/insightsLogic";

describe("insights date ranges", () => {
  it("consumes the backend-owned Phoenix range near UTC midnight", () => {
    const response: PresetDateRangeResponse = {
      preset: "30d",
      start_date: "2026-07-19",
      end_date: "2026-08-17",
      granularity: "day",
      timezone: "America/Phoenix",
    };

    expect(dateRangeForPreset(response, "30d")).toEqual({
      start: "2026-07-19",
      end: "2026-08-17",
      granularity: "day",
    });
  });

  it("consumes the backend-owned DST-zone calendar range without browser recomputation", () => {
    const response: PresetDateRangeResponse = {
      preset: "this_month",
      start_date: "2026-03-01",
      end_date: "2026-03-08",
      granularity: "day",
      timezone: "America/New_York",
    };

    expect(dateRangeForPreset(response, "this_month")).toEqual({
      start: "2026-03-01",
      end: "2026-03-08",
      granularity: "day",
    });
  });

  it("fails visibly for missing, mismatched, or malformed server resolution", () => {
    expect(() => dateRangeForPreset("30d")).toThrow(
      "Invalid Insights preset date range response",
    );
    expect(() => dateRangeForPreset({
      preset: "7d",
      start_date: "2026-08-11",
      end_date: "2026-08-17",
      granularity: "day",
      timezone: "America/Phoenix",
    }, "30d")).toThrow("Invalid Insights preset date range response");
    expect(() => dateRangeForPreset({
      preset: "30d",
      start_date: "2026-08-18",
      end_date: "2026-08-17",
      granularity: "day",
      timezone: "America/Phoenix",
    })).toThrow("Invalid Insights preset date range response");
    expect(() => dateRangeForPreset({
      preset: "30d",
      start_date: "2026-02-31",
      end_date: "2026-03-01",
      granularity: "day",
      timezone: "America/Phoenix",
    })).toThrow("Invalid Insights preset date range response");
    expect(() => dateRangeForPreset({
      preset: "30d",
      start_date: "2026-07-19",
      end_date: "2026-08-17",
      granularity: "day",
      timezone: "Mars/Phobos",
    })).toThrow("Invalid Insights preset date range response");
    expect(() => dateRangeForPreset({
      preset: "30d",
      start_date: "2026-07-19",
      end_date: "2026-08-17",
      granularity: "day",
      timezone: "America/Phoenix",
      extra: "must fail closed",
    })).toThrow("Invalid Insights preset date range response");
  });

  it("cancels superseded preset reads and rejects their stale completions", () => {
    const gate = createLatestPresetRequestGate();
    const first = gate.begin();
    const second = gate.begin();

    expect(first.signal.aborted).toBe(true);
    expect(first.isCurrent()).toBe(false);
    expect(second.signal.aborted).toBe(false);
    expect(second.isCurrent()).toBe(true);

    gate.cancel();
    expect(second.signal.aborted).toBe(true);
    expect(second.isCurrent()).toBe(false);
    const staleContext = {
      pageContext: {
        schema_version: "1.0" as const,
        surface: "expense_insights" as const,
        filters: { start_date: "2026-07-19", end_date: "2026-08-17" },
      },
      label: "Insights · Last 30 Days",
    };
    expect(insightsContextForPresetResolution(false, () => staleContext)).toEqual({
      pageContext: null,
      label: "Insights",
    });
    expect(insightsContextForPresetResolution(true, () => staleContext)).toBe(staleContext);
  });

  it("builds the bounded authenticated preset endpoint path", () => {
    expect(presetDateRangePath("last_quarter")).toBe(
      "/api/insights/date-range?preset=last_quarter",
    );
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

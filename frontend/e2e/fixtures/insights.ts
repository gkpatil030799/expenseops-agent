import type { Page } from "@playwright/test";

import type {
  PresetDateRangeResponse,
  ServerDatePreset,
} from "../../src/insightsLogic";

const ranges: Record<ServerDatePreset, Omit<PresetDateRangeResponse, "preset">> = {
  "7d": {
    start_date: "2026-08-11",
    end_date: "2026-08-17",
    granularity: "day",
    timezone: "America/Phoenix",
  },
  "30d": {
    start_date: "2026-07-19",
    end_date: "2026-08-17",
    granularity: "day",
    timezone: "America/Phoenix",
  },
  "90d": {
    start_date: "2026-05-20",
    end_date: "2026-08-17",
    granularity: "week",
    timezone: "America/Phoenix",
  },
  this_month: {
    start_date: "2026-08-01",
    end_date: "2026-08-17",
    granularity: "day",
    timezone: "America/Phoenix",
  },
  last_month: {
    start_date: "2026-07-01",
    end_date: "2026-07-31",
    granularity: "day",
    timezone: "America/Phoenix",
  },
  this_quarter: {
    start_date: "2026-07-01",
    end_date: "2026-08-17",
    granularity: "week",
    timezone: "America/Phoenix",
  },
  last_quarter: {
    start_date: "2026-04-01",
    end_date: "2026-06-30",
    granularity: "week",
    timezone: "America/Phoenix",
  },
  ytd: {
    start_date: "2026-01-01",
    end_date: "2026-08-17",
    granularity: "month",
    timezone: "America/Phoenix",
  },
};

export function insightsDateRangeForPreset(
  preset: ServerDatePreset,
): PresetDateRangeResponse {
  return { preset, ...ranges[preset] };
}

export async function mockInsightsDateRanges(page: Page): Promise<void> {
  await page.route("**/api/insights/date-range?**", (route) => {
    const preset = new URL(route.request().url()).searchParams.get("preset") as
      | ServerDatePreset
      | null;
    if (!preset || !(preset in ranges)) {
      return route.fulfill({ status: 422, json: { detail: "Unsupported date preset" } });
    }
    return route.fulfill({ json: insightsDateRangeForPreset(preset) });
  });
}

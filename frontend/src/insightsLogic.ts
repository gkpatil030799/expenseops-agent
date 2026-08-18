import type { AgentContextDescriptor } from "@/agent/pageContext";

export type DatePreset = "7d" | "30d" | "this_month" | "last_month" | "90d" | "this_quarter" | "last_quarter" | "ytd" | "custom";
export type ServerDatePreset = Exclude<DatePreset, "custom">;
export type Granularity = "day" | "week" | "month";

export type DateRange = { start: string; end: string; granularity: Granularity };

export type PresetDateRangeResponse = {
  preset: ServerDatePreset;
  start_date: string;
  end_date: string;
  granularity: Granularity;
  timezone: string;
};

export type LatestPresetRequest = {
  signal: AbortSignal;
  isCurrent: () => boolean;
};

export type LatestPresetRequestGate = {
  begin: () => LatestPresetRequest;
  cancel: () => void;
};

const serverPresets = new Set<ServerDatePreset>([
  "7d",
  "30d",
  "this_month",
  "last_month",
  "90d",
  "this_quarter",
  "last_quarter",
  "ytd",
]);

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const PRESET_DATE_RANGE_KEYS = new Set([
  "preset",
  "start_date",
  "end_date",
  "granularity",
  "timezone",
]);

function isRealIsoDate(value: unknown): value is string {
  if (typeof value !== "string" || !ISO_DATE.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  if (year < 1 || year > 9999 || month < 1 || month > 12 || day < 1) return false;
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day <= daysInMonth[month - 1];
}

function isSupportedTimezone(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    value !== value.trim() ||
    value.length > 64 ||
    value.startsWith("/") ||
    value.startsWith(".") ||
    value.includes("\0")
  ) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: value }).format(0);
    return true;
  } catch {
    return false;
  }
}

export const UNRESOLVED_INSIGHTS_CONTEXT: AgentContextDescriptor = {
  pageContext: null,
  label: "Insights",
};

export function insightsContextForPresetResolution(
  ready: boolean,
  resolved: () => AgentContextDescriptor,
): AgentContextDescriptor {
  return ready ? resolved() : UNRESOLVED_INSIGHTS_CONTEXT;
}

export function isServerDatePreset(preset: DatePreset): preset is ServerDatePreset {
  return preset !== "custom";
}

export function presetDateRangePath(preset: ServerDatePreset): string {
  return `/api/insights/date-range?preset=${encodeURIComponent(preset)}`;
}

export function dateRangeForPreset(
  response: unknown,
  expectedPreset?: ServerDatePreset,
): DateRange {
  const candidate = response as Partial<PresetDateRangeResponse> | null;
  if (
    !candidate ||
    typeof candidate !== "object" ||
    Array.isArray(candidate) ||
    Object.keys(candidate).length !== PRESET_DATE_RANGE_KEYS.size ||
    Object.keys(candidate).some((key) => !PRESET_DATE_RANGE_KEYS.has(key)) ||
    typeof candidate.preset !== "string" ||
    !serverPresets.has(candidate.preset as ServerDatePreset) ||
    (expectedPreset !== undefined && candidate.preset !== expectedPreset) ||
    !isRealIsoDate(candidate.start_date) ||
    !isRealIsoDate(candidate.end_date) ||
    candidate.start_date > candidate.end_date ||
    typeof candidate.granularity !== "string" ||
    !["day", "week", "month"].includes(candidate.granularity) ||
    !isSupportedTimezone(candidate.timezone)
  ) {
    throw new Error("Invalid Insights preset date range response");
  }
  return {
    start: candidate.start_date,
    end: candidate.end_date,
    granularity: candidate.granularity as Granularity,
  };
}

export function createLatestPresetRequestGate(): LatestPresetRequestGate {
  let version = 0;
  let active: AbortController | null = null;
  return {
    begin() {
      version += 1;
      active?.abort();
      const requestVersion = version;
      const controller = new AbortController();
      active = controller;
      return {
        signal: controller.signal,
        isCurrent: () => requestVersion === version && !controller.signal.aborted,
      };
    },
    cancel() {
      version += 1;
      active?.abort();
      active = null;
    },
  };
}

export function customGranularity(start: string, end: string): Granularity {
  const days = Math.round((new Date(`${end}T12:00:00`).getTime() - new Date(`${start}T12:00:00`).getTime()) / 86_400_000) + 1;
  return days <= 45 ? "day" : days <= 180 ? "week" : "month";
}

export function comparisonPercent(current: number, previous: number): number | null {
  if (!previous) return null;
  return Math.round(((current - previous) / Math.abs(previous)) * 100);
}

export type DatePreset = "7d" | "30d" | "this_month" | "last_month" | "90d" | "this_quarter" | "last_quarter" | "ytd" | "custom";
export type Granularity = "day" | "week" | "month";

export type DateRange = { start: string; end: string; granularity: Granularity };

const iso = (value: Date) => {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
};

const addDays = (value: Date, days: number) => {
  const output = new Date(value.getFullYear(), value.getMonth(), value.getDate());
  output.setDate(output.getDate() + days);
  return output;
};

export function dateRangeForPreset(preset: DatePreset, now = new Date()): DateRange {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);
  const quarterStartMonth = Math.floor(today.getMonth() / 3) * 3;
  const quarterStart = new Date(today.getFullYear(), quarterStartMonth, 1);
  if (preset === "7d") return { start: iso(addDays(today, -6)), end: iso(today), granularity: "day" };
  if (preset === "30d") return { start: iso(addDays(today, -29)), end: iso(today), granularity: "day" };
  if (preset === "90d") return { start: iso(addDays(today, -89)), end: iso(today), granularity: "week" };
  if (preset === "this_month") return { start: iso(monthStart), end: iso(today), granularity: "day" };
  if (preset === "last_month") {
    const start = new Date(today.getFullYear(), today.getMonth() - 1, 1);
    return { start: iso(start), end: iso(addDays(monthStart, -1)), granularity: "day" };
  }
  if (preset === "this_quarter") return { start: iso(quarterStart), end: iso(today), granularity: "week" };
  if (preset === "last_quarter") {
    const start = new Date(today.getFullYear(), quarterStartMonth - 3, 1);
    return { start: iso(start), end: iso(addDays(quarterStart, -1)), granularity: "week" };
  }
  return { start: iso(new Date(today.getFullYear(), 0, 1)), end: iso(today), granularity: "month" };
}

export function customGranularity(start: string, end: string): Granularity {
  const days = Math.round((new Date(`${end}T12:00:00`).getTime() - new Date(`${start}T12:00:00`).getTime()) / 86_400_000) + 1;
  return days <= 45 ? "day" : days <= 180 ? "week" : "month";
}

export function comparisonPercent(current: number, previous: number): number | null {
  if (!previous) return null;
  return Math.round(((current - previous) / Math.abs(previous)) * 100);
}

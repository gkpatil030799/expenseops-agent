export const CATEGORY_COLORS: Record<string, string> = {
  "Food & Dining": "#4f46e5",
  Lifestyle: "#7c3aed",
  "Home & Bills": "#0f766e",
  Transportation: "#0369a1",
  Travel: "#b45309",
  Health: "#be123c",
  Subscriptions: "#475569",
  Other: "#64748b",
  Uncategorized: "#94a3b8",
};

export const NEAR_ZERO_COMPARISON_CENTS = 5_000;
export const MATERIAL_CHANGE_CENTS = 2_500;
export const MATERIAL_CHANGE_PERCENT = 15;
export const SMALL_CATEGORY_PERCENT = 3;

export type CategoryAmount = { name: string; amount_cents: number; [key: string]: unknown };

export function categoryColor(name: string): string {
  return CATEGORY_COLORS[name] || CATEGORY_COLORS.Other;
}

export function money(cents: number, currency = "USD"): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(cents / 100);
}

export function signedMoney(cents: number, currency = "USD"): string {
  if (!cents) return money(0, currency);
  return `${cents > 0 ? "+" : "−"}${money(Math.abs(cents), currency)}`;
}

export function comparisonText(current: number, previous: number, currency = "USD"): {
  primary: string;
  secondary: string | null;
} {
  const delta = current - previous;
  if (!delta) return { primary: "No change", secondary: null };
  const primary = `${signedMoney(delta, currency)} vs previous period`;
  if (Math.abs(previous) < NEAR_ZERO_COMPARISON_CENTS) {
    return { primary, secondary: null };
  }
  const percentage = Math.round((delta / Math.abs(previous)) * 100);
  return { primary, secondary: `(${percentage > 0 ? "+" : "−"}${Math.abs(percentage)}%)` };
}

export function meaningfulChange(current: number, previous: number, total: number): boolean {
  const delta = Math.abs(current - previous);
  if (delta < MATERIAL_CHANGE_CENTS) return false;
  const percent = previous ? (delta / Math.abs(previous)) * 100 : 100;
  const contribution = total ? delta / Math.abs(total) * 100 : 0;
  return percent >= MATERIAL_CHANGE_PERCENT || contribution >= 5;
}

export function groupSmallCategories<T extends CategoryAmount>(items: T[], maxCategories = 7): T[] {
  const total = items.reduce((sum, item) => sum + item.amount_cents, 0);
  const candidates = items.filter((item) => item.name !== "Uncategorized");
  const uncategorized = items.filter((item) => item.name === "Uncategorized");
  const major = candidates.filter((item, index) =>
    item.name !== "Other" && index < maxCategories &&
    (!total || item.amount_cents / total * 100 >= SMALL_CATEGORY_PERCENT));
  const grouped = candidates.filter((item) => !major.includes(item));
  if (!grouped.length) return [...major, ...uncategorized];
  const other = grouped.reduce((sum, item) => sum + item.amount_cents, 0);
  return [
    ...major,
    ...uncategorized,
    { ...grouped[0], name: "Other", amount_cents: other } as T,
  ];
}

export function dateLabel(value: string, granularity: "day" | "week" | "month", long = false): string {
  const date = new Date(`${value}T12:00:00`);
  if (granularity === "month") return date.toLocaleDateString("en-US", { month: long ? "long" : "short", year: "numeric" });
  const formatted = date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  return granularity === "week" && long ? `Week of ${formatted}` : formatted;
}

export function axisTicks(maxCents: number, count = 4): number[] {
  if (maxCents <= 0) return [0];
  const rawStep = maxCents / count;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const normalized = rawStep / magnitude;
  const nice = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  const step = nice * magnitude;
  const ceiling = Math.ceil(maxCents / step) * step;
  return Array.from({ length: Math.round(ceiling / step) + 1 }, (_, index) => index * step);
}

export function xTickIndexes(length: number, maximum = 6): number[] {
  if (length <= maximum) return Array.from({ length }, (_, index) => index);
  const indexes = new Set<number>([0, length - 1]);
  const step = (length - 1) / (maximum - 1);
  for (let index = 1; index < maximum - 1; index += 1) indexes.add(Math.round(index * step));
  return [...indexes].sort((a, b) => a - b);
}

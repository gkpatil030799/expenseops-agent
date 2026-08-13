import { describe, expect, it } from "vitest";

import { CATEGORY_COLORS, axisTicks, categoryColor, comparisonText, dateLabel, groupSmallCategories, money, xTickIndexes } from "@/insightsVisualization";

describe("insights visualization rules", () => {
  it("formats dollar axes and spend tooltip values", () => {
    expect(money(123_400)).toBe("$1,234");
    expect(money(123_400, "EUR")).toBe("€1,234");
    expect(axisTicks(27_500)).toEqual([0, 10_000, 20_000, 30_000]);
    expect(dateLabel("2026-08-03", "week", true)).toBe("Week of Aug 3");
  });

  it("shows multiple deterministic date ticks", () => {
    expect(xTickIndexes(30)).toHaveLength(6);
    expect(xTickIndexes(30)).toEqual([0, 6, 12, 17, 23, 29]);
  });

  it("handles KPI comparisons without misleading percentages", () => {
    expect(comparisonText(10_000, 10_000)).toEqual({ primary: "No change", secondary: null });
    expect(comparisonText(28_300, 3_100)).toEqual({ primary: "+$252 vs previous period", secondary: null });
    expect(comparisonText(28_300, 100)).toEqual({ primary: "+$282 vs previous period", secondary: null });
    expect(comparisonText(18_100, 10_000)).toEqual({ primary: "+$81 vs previous period", secondary: "(+81%)" });
  });

  it("uses one canonical category color mapping", () => {
    expect(categoryColor("Food & Dining")).toBe(CATEGORY_COLORS["Food & Dining"]);
    expect(categoryColor("Unmapped")).toBe(CATEGORY_COLORS.Other);
  });

  it("groups very small categories into Other", () => {
    const grouped = groupSmallCategories([
      { name: "Food & Dining", amount_cents: 9_700 },
      { name: "Health", amount_cents: 200 },
      { name: "Travel", amount_cents: 100 },
    ]);
    expect(grouped).toEqual([
      { name: "Food & Dining", amount_cents: 9_700 },
      { name: "Other", amount_cents: 300 },
    ]);
  });

  it("uses magnitude when refunds make a category negative", () => {
    const grouped = groupSmallCategories([
      { name: "Food & Dining", amount_cents: 10_000 },
      { name: "Health", amount_cents: -4_000 },
      { name: "Travel", amount_cents: 100 },
    ]);
    expect(grouped.map((item) => item.name)).toContain("Health");
  });
});

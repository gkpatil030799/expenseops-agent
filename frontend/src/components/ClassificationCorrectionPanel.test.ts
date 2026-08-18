import { describe, expect, it } from "vitest";

import { classificationCorrectionPayload } from "@/classificationActivity";

describe("classification correction payload", () => {
  it("keeps closed semantic values and turns cleared optional labels into null", () => {
    expect(classificationCorrectionPayload({
      spending_parent_category: "food_dining",
      item_activity_type: "restaurant_meal",
      replenishment_eligibility: "not_replenishable",
      subcategory_name: "   ",
      canonical_concept: " Restaurant meal ",
    })).toEqual({
      spending_parent_category: "food_dining",
      item_activity_type: "restaurant_meal",
      replenishment_eligibility: "not_replenishable",
      subcategory_name: null,
      canonical_concept: "Restaurant meal",
    });
  });

  it("defensively bounds optional labels before sending them", () => {
    const payload = classificationCorrectionPayload({
      spending_parent_category: "household_home",
      item_activity_type: "household_consumable",
      replenishment_eligibility: "replenishable",
      subcategory_name: "s".repeat(140),
      canonical_concept: "c".repeat(300),
    });
    expect(payload.subcategory_name).toHaveLength(128);
    expect(payload.canonical_concept).toHaveLength(255);
  });
});

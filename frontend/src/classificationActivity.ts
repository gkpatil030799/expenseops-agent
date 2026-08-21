export type ClassificationActivityView =
  | "summary"
  | "categories"
  | "new_categories"
  | "matches"
  | "staples"
  | "cadence"
  | "uncertain";

export type ClassificationActivitySection =
  | "transactions"
  | "receipt_items"
  | "categories"
  | "new_categories"
  | "receipt_matches"
  | "new_household_items"
  | "cadence_updates"
  | "uncertain";

export type ClassificationActivityRangeView =
  | ClassificationActivityView
  | "staple_candidates"
  | "aliases";

export type ClassificationActivityRangeSection =
  | ClassificationActivitySection
  | "staple_candidates"
  | "aliases";

export type SpendingParentCategory =
  | "food_dining"
  | "household_home"
  | "lifestyle_shopping"
  | "personal_care"
  | "health"
  | "transportation"
  | "travel"
  | "entertainment"
  | "subscriptions"
  | "pets"
  | "education_office"
  | "services"
  | "fees_taxes_discounts"
  | "other_uncertain";

export type ClassificationActivityType =
  | "grocery"
  | "household_consumable"
  | "routine_consumption"
  | "one_time_purchase"
  | "restaurant_meal"
  | "coffee_beverage"
  | "food_delivery"
  | "nightlife"
  | "apparel"
  | "electronics"
  | "pharmacy"
  | "personal_care"
  | "beauty"
  | "pet_supply"
  | "automotive"
  | "transportation"
  | "travel"
  | "entertainment"
  | "subscription"
  | "education_office"
  | "service"
  | "tax"
  | "tip"
  | "discount"
  | "fee"
  | "refund"
  | "non_product"
  | "uncertain";

export type ReplenishmentEligibility =
  | "replenishable"
  | "potentially_replenishable"
  | "not_replenishable"
  | "uncertain";

export type ClassificationCorrectionDraft = {
  spending_parent_category: SpendingParentCategory;
  item_activity_type: ClassificationActivityType;
  replenishment_eligibility: ReplenishmentEligibility;
  subcategory_name: string;
  canonical_concept: string;
};

export type ClassificationCorrectionPayload = Omit<
  ClassificationCorrectionDraft,
  "subcategory_name" | "canonical_concept"
> & {
  subcategory_name: string | null;
  canonical_concept: string | null;
};

export function classificationCorrectionPayload(
  draft: ClassificationCorrectionDraft,
): ClassificationCorrectionPayload {
  return {
    spending_parent_category: draft.spending_parent_category,
    item_activity_type: draft.item_activity_type,
    replenishment_eligibility: draft.replenishment_eligibility,
    subcategory_name: nullableBoundedLabel(draft.subcategory_name, 128),
    canonical_concept: nullableBoundedLabel(draft.canonical_concept, 255),
  };
}

function nullableBoundedLabel(value: string, maximum: number): string | null {
  const normalized = value.trim();
  return normalized ? normalized.slice(0, maximum) : null;
}

export type ClassificationConfidenceBand = "low" | "medium" | "high";
export type ClassificationDecisionState = "provisional" | "final" | "corrected";
export type ClassificationAuthority =
  | "fallback"
  | "model_evidence"
  | "provider_evidence"
  | "receipt_evidence"
  | "deterministic_exact"
  | "confirmed_alias"
  | "user_correction";

export type HouseholdCadenceSource =
  | "configured"
  | "learning"
  | "category_prior"
  | "model_prior"
  | "observed"
  | "quantity_adjusted"
  | "adaptive";

export type ClassificationDecisionActivity = {
  decision_public_id: string;
  public_id: string;
  source_available: boolean;
  version: number;
  parent_category: SpendingParentCategory;
  subcategory: string | null;
  concept: string | null;
  activity_type: ClassificationActivityType;
  replenishment_eligibility: ReplenishmentEligibility;
  confidence: number;
  confidence_band: ClassificationConfidenceBand;
  authority: ClassificationAuthority;
  decision_state: ClassificationDecisionState;
  provenance_codes: string[];
  auto_finalize_at: string | null;
  finalized_at: string | null;
  corrects_decision_public_id: string | null;
  created_subcategory: boolean;
  created_concept: boolean;
  created_household_item: boolean;
  applied_at: string;
};

export type ClassificationTransactionActivity = ClassificationDecisionActivity & {
  merchant: string;
  occurred_on: string | null;
};

export type ClassificationReceiptItemActivity = ClassificationDecisionActivity & {
  receipt_public_id: string;
  merchant: string | null;
  name: string;
  household_item_public_id: string | null;
  household_item_name: string | null;
};

export type ClassificationCategoryActivity = {
  parent_category: SpendingParentCategory;
  transaction_count: number;
  receipt_item_count: number;
  total_count: number;
};

export type ClassificationNewCategoryActivity = {
  decision_public_id: string;
  parent_category: SpendingParentCategory;
  subcategory: string;
  source_type: "transaction" | "receipt_line";
  authority: ClassificationAuthority;
  created_at: string;
};

export type ClassificationReceiptMatchActivity = {
  receipt_public_id: string;
  merchant: string | null;
  status: "auto_matched" | "ambiguous" | "no_match";
  confidence: number;
  transaction_public_id: string | null;
  reason_code:
    | "matched_by_receipt_evidence"
    | "multiple_possible_transactions"
    | "no_eligible_transaction"
    | "linked_transaction_unavailable";
  attempted_at: string;
  matched_at: string | null;
};

export type ClassificationHouseholdItemActivity = {
  created_by_decision_public_id: string | null;
  public_id: string;
  name: string;
  parent_category: SpendingParentCategory;
  replenishment_eligibility: ReplenishmentEligibility;
  classification_confidence: number;
  cadence_source: HouseholdCadenceSource;
  cadence_days: number | null;
  cadence_min_days: number | null;
  cadence_max_days: number | null;
  cadence_confidence: number;
  activity_at: string;
};

export type ClassificationUncertaintyReason =
  | "low_confidence"
  | "provisional"
  | "other_uncertain"
  | "replenishment_uncertain"
  | "ambiguous_receipt_match"
  | "no_receipt_match";

export type ClassificationUncertainActivity = {
  kind: "transaction" | "receipt_item" | "receipt_match";
  public_id: string;
  receipt_public_id: string | null;
  label: string;
  reasons: ClassificationUncertaintyReason[];
  confidence_band: ClassificationConfidenceBand | null;
  decision_state: ClassificationDecisionState | null;
  observed_at: string;
};

export type ClassificationStapleCandidateActivity = {
  decision_public_id: string;
  receipt_item_public_id: string;
  receipt_public_id: string;
  source_available: boolean;
  merchant: string | null;
  name: string;
  parent_category: SpendingParentCategory;
  subcategory: string | null;
  concept: string | null;
  activity_type: ClassificationActivityType;
  replenishment_eligibility: "replenishable" | "potentially_replenishable";
  confidence: number;
  confidence_band: ClassificationConfidenceBand;
  decision_state: ClassificationDecisionState;
  created_household_item: boolean;
  household_item_public_id: string | null;
  household_item_name: string | null;
  learning_state: "candidate" | "learning" | "tracked";
  applied_at: string;
};

export type ClassificationAliasActivity = {
  public_id: string;
  concept: string;
  parent_category: SpendingParentCategory;
  raw_pattern: string;
  merchant: string | null;
  confidence: number;
  authority: ClassificationAuthority;
  active: boolean;
  created_at: string;
};

export type ClassificationActivityCounts = {
  transactions: number;
  receipt_items: number;
  categories: number;
  new_categories: number;
  receipt_matches: number;
  new_household_items: number;
  cadence_updates: number;
  uncertain: number;
};

export type ClassificationActivityRangeCounts = ClassificationActivityCounts & {
  staple_candidates: number;
  aliases: number;
};

export type ClassificationActivityRows = {
  transactions: ClassificationTransactionActivity[];
  receipt_items: ClassificationReceiptItemActivity[];
  categories: ClassificationCategoryActivity[];
  new_categories: ClassificationNewCategoryActivity[];
  receipt_matches: ClassificationReceiptMatchActivity[];
  new_household_items: ClassificationHouseholdItemActivity[];
  cadence_updates: ClassificationHouseholdItemActivity[];
  uncertain: ClassificationUncertainActivity[];
  truncated_sections: ClassificationActivitySection[];
};

export type ClassificationActivityRangeRows = Omit<ClassificationActivityRows, "truncated_sections"> & {
  staple_candidates: ClassificationStapleCandidateActivity[];
  aliases: ClassificationAliasActivity[];
  truncated_sections: ClassificationActivityRangeSection[];
};

export type ClassificationActivityOut = ClassificationActivityRows & {
  schema_version: "1.0";
  view: ClassificationActivityView;
  activity_date: string;
  timezone: "UTC";
  as_of: string;
  counts: ClassificationActivityCounts;
};

export type ClassificationConceptSummary = {
  id: number;
  name: string;
  parent_category: SpendingParentCategory;
  subcategory_id: number | null;
  subcategory_name: string | null;
  item_activity_type: ClassificationActivityType;
  replenishment_eligibility: ReplenishmentEligibility;
  linked_household_item_count: number;
};

export type ClassificationConceptList = {
  concepts: ClassificationConceptSummary[];
  has_more: boolean;
};

export type ClassificationConceptMutation = {
  applied: boolean;
  source_concept_id: number;
  target_concept_id: number;
  target_name: string;
  aliases_moved: number;
  receipt_items_updated: number;
  transactions_updated: number;
};

export type ClassificationSubcategorySummary = {
  id: number;
  name: string;
  parent_category: SpendingParentCategory;
  concept_count: number;
};

export type ClassificationSubcategoryList = {
  subcategories: ClassificationSubcategorySummary[];
  has_more: boolean;
};

export type ClassificationSubcategoryMutation = {
  applied: boolean;
  source_subcategory_id: number;
  target_subcategory_id: number;
  target_name: string;
  concepts_updated: number;
  receipt_items_updated: number;
  transactions_updated: number;
};

export type HouseholdItemMergeMutation = {
  applied: boolean;
  source_household_item_id: number;
  target_household_item_id: number;
  target_name: string;
  merge_event_id: number;
  aliases_moved: number;
  receipt_items_updated: number;
  acquisitions_moved: number;
  errand_links_updated: number;
  plan_links_updated: number;
  predictions_invalidated: number;
  reverted: boolean;
};

export function classificationCategoryLabel(value: SpendingParentCategory): string {
  const labels: Record<SpendingParentCategory, string> = {
    food_dining: "Food & Dining",
    household_home: "Household & Home",
    lifestyle_shopping: "Lifestyle & Shopping",
    personal_care: "Personal Care",
    health: "Health",
    transportation: "Transportation",
    travel: "Travel",
    entertainment: "Entertainment",
    subscriptions: "Subscriptions",
    pets: "Pets",
    education_office: "Education / Office",
    services: "Services",
    fees_taxes_discounts: "Fees / Taxes / Discounts",
    other_uncertain: "Other / Uncertain",
  };
  return labels[value];
}

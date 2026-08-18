import type { ClassificationActivityOut } from "../../src/classificationActivity";

export const emptyClassificationActivity: ClassificationActivityOut = {
  schema_version: "1.0",
  view: "summary",
  activity_date: "2026-08-17",
  timezone: "UTC",
  as_of: "2026-08-17T17:00:00Z",
  counts: {
    transactions: 0,
    receipt_items: 0,
    categories: 0,
    new_categories: 0,
    receipt_matches: 0,
    new_household_items: 0,
    cadence_updates: 0,
    uncertain: 0,
  },
  transactions: [],
  receipt_items: [],
  categories: [],
  new_categories: [],
  receipt_matches: [],
  new_household_items: [],
  cadence_updates: [],
  uncertain: [],
  truncated_sections: [],
};

export type Transaction = {
  id: number;
  plaid_transaction_id: string;
  merchant_name: string | null;
  name: string;
  amount_cents: number;
  amount: string;
  iso_currency_code: string;
  institution_name?: string | null;
  category?: string | null;
  payment_channel?: string | null;
  date: string | null;
  authorized_date: string | null;
  pending: boolean;
  status: string;
  agent_question: string | null;
  review_notification_queued_at?: string | null;
  splitwise_expense_id: string | null;
  splitwise_payload_json: string | null;
  splitwise_amount_cents?: number | null;
  replaces_transaction_id?: number | null;
  replaced_by_transaction_id?: number | null;
  last_error: string | null;
  classification_suggestion: "likely_personal" | "likely_shared" | "unsure" | null;
  classification_reason: string | null;
  can_undo_transaction: boolean;
  created_at: string;
  updated_at: string;
};

export type Friend = {
  id: number;
  first_name: string | null;
  last_name: string | null;
  email: string | null;
  display_name: string;
  registration_status?: string | null;
};

export type Group = {
  id: number;
  name: string;
  invite_link?: string | null;
};

export type SplitwiseUser = {
  id: number;
  first_name: string | null;
  last_name: string | null;
  email: string | null;
};

export type CustomSplitMode = "equal" | "exact_amounts" | "percentages" | "shares";

export type DashboardEventType =
  | "transaction_detected"
  | "telegram_sent"
  | "recommendation_generated"
  | "split_confirmed"
  | "split_posted"
  | "undo_completed";

export type DashboardEvent = {
  id: string;
  transaction_id: number;
  type: DashboardEventType;
  merchant: string;
  amount: string;
  currency: string;
  participants: string[];
  group_name: string | null;
  status: string;
  timestamp: string;
  details: Record<string, unknown>;
};

export type DashboardFilters = {
  merchant: string;
  group: string;
  status: string;
  dateFrom: string;
  dateTo: string;
};

export type MemoryEntry = {
  id: string;
  name: string;
  count: number;
};

export type AIMemory = {
  id: number;
  original_message: string;
  failure_reason: string;
  final_action: string;
  final_group_name: string | null;
  final_participants: string[];
  final_split_mode: string | null;
  payer_included: boolean;
  custom_values: Array<{
    display_name?: string | null;
    amount_cents?: number | null;
    percentage?: string | number | null;
    shares?: string | number | null;
  }> | null;
  correction_type: string;
  merchant: string | null;
  amount_cents: number | null;
  currency: string | null;
  usage_count: number;
  last_used_at: string | null;
  created_at: string;
};

export type HouseholdItem = {
  id: number;
  name: string;
  quantity: string | null;
  unit: string | null;
  preferred_place_name: string | null;
  preferred_place_address: string | null;
  replenishment_mode: "errand" | "delivery" | "either";
  cadence_days: number;
  last_acquired_at: string | null;
  snoozed_until: string | null;
  enabled: boolean;
  notes: string | null;
  due_score: number;
  due_state: "likely_due" | "probably_due" | "not_due" | "unknown";
  should_surface: boolean;
  linked_errand_id: number | null;
  created_at: string;
  updated_at: string;
};

export type LinkedHouseholdItem = Pick<HouseholdItem, "id" | "name" | "quantity" | "unit">;

export type Errand = {
  id: number;
  title: string;
  errand_type: "purchase" | "return" | "pickup" | "service" | "other";
  place_name: string | null;
  place_address: string | null;
  place_resolution_status: "unresolved" | "needs_user_choice" | "resolved" | "failed";
  resolved_place_name: string | null;
  resolved_place_address: string | null;
  resolved_latitude: number | null;
  resolved_longitude: number | null;
  resolved_provider_place_id: string | null;
  resolved_open_now: boolean | null;
  resolved_opening_hours: string[] | null;
  place_resolution_method: string | null;
  due_at: string | null;
  estimated_duration_minutes: number | null;
  priority: "low" | "normal" | "high";
  status: "open" | "planned" | "completed" | "skipped";
  source: "manual" | "replenishment";
  notes: string | null;
  included_in_next_plan: boolean;
  linked_household_items: LinkedHouseholdItem[];
  created_at: string;
  updated_at: string;
};

export type PlaceCandidate = {
  canonical_name: string;
  full_address: string;
  latitude: number | null;
  longitude: number | null;
  provider_place_id: string | null;
  is_preferred: boolean;
  open_now: boolean | null;
  opening_hours: string[] | null;
};

export type PlaceSearchResult = {
  errand_id: number;
  query: string;
  provider: string;
  candidates: PlaceCandidate[];
  message: string | null;
};

export type ReplenishmentSuggestion = {
  household_item_id: number;
  household_item_name: string;
  action: "created_errand" | "added_to_existing_stop" | "delivery_only" | "due_no_trip";
  errand_id: number | null;
  message: string;
};

export type ReplenishmentRefresh = {
  due_items: HouseholdItem[];
  suggestions: ReplenishmentSuggestion[];
  created_errand_count: number;
};

export type ReceiptLine = {
  id: number;
  raw_name: string;
  normalized_name: string;
  quantity: number | null;
  unit: string | null;
  line_total_cents: number | null;
  household_item_id: number | null;
  household_item_name: string | null;
  acquisition_id: number | null;
  match_status: string;
  match_confidence: number | null;
};

export type PurchaseReceipt = {
  id: number;
  source: string;
  merchant: string | null;
  purchased_at: string | null;
  total_cents: number | null;
  currency: string;
  parse_status: string;
  parse_confidence: number | null;
  failure_code: string | null;
  transaction_id: number | null;
  created_at: string;
  items: ReceiptLine[];
};

export type ReplenishmentPrediction = {
  id: number;
  household_item_id: number;
  household_item_name: string;
  predicted_need_at: string;
  predicted_days_remaining: number;
  due_score: number;
  confidence: number;
  confidence_level: "insufficient" | "low" | "medium" | "high";
  method: string;
  explanation: string;
  evidence_label: string | null;
};

export type ReplenishmentLearningSummary = {
  this_week: ReplenishmentPrediction[];
  learning: {
    confirmed_acquisitions: number;
    items_with_history: number;
    active_model: string | null;
  };
  recent_receipts: PurchaseReceipt[];
  accuracy: {
    evaluated_predictions: number;
    mae_days: number | null;
    current_prediction_method: string | null;
    training_observations: number;
    validation_observations: number;
    validation_method: string | null;
    baseline_mae_days: number | null;
    active_model_mae_days: number | null;
    improvement_pct: number | null;
    confidence_level: "insufficient" | "low" | "medium" | "high";
    latest_model_status: string | null;
    decision_reason: string | null;
  };
};

export type ErrandPlanStop = {
  id: number;
  stop_order: number;
  place_name: string;
  place_address: string | null;
  errands: Errand[];
  household_items: Array<{
    id: number;
    name: string;
    quantity: string | null;
    unit: string | null;
    due_score: number;
    reason: string | null;
  }>;
};

export type ErrandPlan = {
  id: number;
  planned_for: string | null;
  base_location: string | null;
  status: "planned" | "started" | "completed";
  routing_provider: string;
  routing_is_optimized: boolean;
  route_url: string | null;
  is_stale: boolean;
  stale_reason: string | null;
  estimated_stop_minutes: number;
  travel_duration_minutes: number | null;
  distance_meters: number | null;
  baseline_travel_duration_minutes: number | null;
  incremental_travel_duration_minutes: number | null;
  available_minutes: number | null;
  planning_mode: string;
  primary_destination: string | null;
  final_destination: string | null;
  stop_count: number;
  stops: ErrandPlanStop[];
  created_at: string;
  updated_at: string;
};

export type SavedLocation = {
  id: number;
  label: string;
  address: string;
  latitude: number | null;
  longitude: number | null;
  location_type: "home" | "work" | "custom";
  created_at: string;
  updated_at: string;
};

export type PlanningLocation = {
  saved_location_id?: number;
  label?: string;
  address?: string;
  latitude?: number;
  longitude?: number;
};

export type PromotionOffer = {
  id: number;
  merchant: string;
  category: string;
  headline: string;
  description: string | null;
  offer_type: string;
  percent_off: number | null;
  amount_off: number | null;
  minimum_spend: number | null;
  promo_code: string | null;
  expires_at: string | null;
  expiry_precision: string;
  destination_url: string | null;
  destination_domain: string | null;
  terms_summary: string | null;
  trust_status: string;
  trust_reason: string;
  status: string;
  score: number;
  saved: boolean;
  why: string[];
  source_count: number;
};

export type WhileOutPlan = {
  plan: ErrandPlan;
  recommendation_reasons: string[];
  excluded: Array<{
    kind: "errand" | "household_item";
    id: number;
    title: string;
    reason: string;
  }>;
  estimates_are_routed: boolean;
  time_budget_applied: boolean;
};

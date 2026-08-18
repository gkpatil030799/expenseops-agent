/**
 * Platform-neutral contracts for the unified ExpenseOps Agent.
 *
 * These JSON shapes mirror `app/agent/contracts.py`. Keep this module free of
 * React, DOM types, CSS concerns, and executable callbacks. The backend remains
 * the canonical runtime validator and authorization boundary.
 */

import type {
  ClassificationActivityCounts,
  ClassificationActivityRows,
  ClassificationActivityView,
  ClassificationActivityType,
  ReplenishmentEligibility,
  SpendingParentCategory,
} from "../classificationActivity";

export const AGENT_SCHEMA_VERSION = "1.0" as const;

export type AgentSchemaVersion = typeof AGENT_SCHEMA_VERSION;

export type JsonPrimitive = boolean | number | string | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export type AgentCapabilities = {
  schema_version: AgentSchemaVersion;
  enabled: boolean;
  read_tools_enabled: boolean;
  write_actions_enabled: boolean;
  proactive_enabled: boolean;
  purchasing_enabled: boolean;
};

export type AgentConversationCreate = {
  title?: string | null;
};

export type AgentMessageCreate = {
  text: string;
  client_message_id?: string | null;
};

export type AgentSurface =
  | "home"
  | "expense_review"
  | "expense_insights"
  | "expense_activity"
  | "household_today"
  | "household_errands"
  | "household_receipts"
  | "household_staples"
  | "household_history"
  | "deals"
  | "settings"
  | "integrations";

export type AgentPageFilters = {
  start_date?: string | null;
  end_date?: string | null;
  date_preset?: string | null;
  account_id?: string | null;
  category?: string | null;
  merchant?: string | null;
  status?: string | null;
  currency_code?: string | null;
  spend_basis?: "card" | "actual_share" | null;
  query?: string | null;
};

export type AgentEntityKind =
  | "transaction"
  | "deal"
  | "receipt"
  | "errand"
  | "household_item"
  | "integration";

export type AgentPageEntity = {
  kind: AgentEntityKind;
  public_id: string;
};

/**
 * Page context is descriptive only. Workspace and user identity must always
 * come from the authenticated server context, never from this payload.
 */
export type AgentPageContext = {
  schema_version: AgentSchemaVersion;
  surface: AgentSurface;
  filters?: AgentPageFilters;
  entity?: AgentPageEntity | null;
};

export type AgentTurnCreate = {
  text: string;
  client_message_id: string;
  page_context?: AgentPageContext | null;
};

export type AgentResponseBlockBase = {
  block_id?: string | null;
};

export type AgentTextBlock = AgentResponseBlockBase & {
  type: "text";
  text: string;
};

export type AgentTransactionSummary = {
  public_id: string;
  merchant: string;
  amount_cents: number;
  currency_code: string;
  occurred_on: string | null;
  category?: string | null;
  status: string;
  pending?: boolean;
};

export type AgentTransactionListBlock = AgentResponseBlockBase & {
  type: "transaction_list";
  title: string;
  transactions: AgentTransactionSummary[];
  total_count: number;
};

export type AgentSpendingBreakdownItem = {
  name: string;
  amount_cents: number;
  transaction_count: number;
  percentage: number;
  previous_amount_cents: number | null;
};

export type AgentSpendingSummaryBlock = AgentResponseBlockBase & {
  type: "spending_summary";
  title: string;
  start_date: string;
  end_date: string;
  currency_code: string;
  spend_basis: "card" | "actual_share";
  total_cents: number;
  previous_total_cents: number | null;
  credits_cents: number;
  previous_credits_cents: number;
  unknown_share_transactions: number;
  previous_unknown_share_transactions: number;
  unknown_credit_share_transactions: number;
  previous_unknown_credit_share_transactions: number;
  change_percent: number | null;
  highlights: string[];
  top_categories: AgentSpendingBreakdownItem[];
  top_merchants: AgentSpendingBreakdownItem[];
};

export type AgentLifestyleBreakdownItem = {
  name: string;
  amount_cents: number;
  transaction_count: number;
  percentage: number;
};

export type AgentLifestyleSummaryBlock = AgentResponseBlockBase & {
  type: "lifestyle_summary";
  block_version: "1.0";
  title: string;
  start_date: string;
  end_date: string;
  previous_start_date: string | null;
  previous_end_date: string | null;
  activity_type: "all" | "coffee" | "restaurants" | "delivery" | "nightlife";
  currency_code: string;
  spend_basis: "card" | "actual_share";
  total_cents: number;
  credits_cents: number;
  transaction_count: number;
  average_cents: number;
  personal_cents: number;
  shared_cents: number;
  unreviewed_cents: number;
  previous_total_cents: number | null;
  previous_transaction_count: number | null;
  unknown_share_transactions: number;
  previous_unknown_share_transactions: number;
  unknown_credit_share_transactions: number;
  previous_unknown_credit_share_transactions: number;
  weekday_cents: number;
  weekday_count: number;
  weekend_cents: number;
  weekend_count: number;
  uncertain_transaction_count: number;
  observations: string[];
  activities: AgentLifestyleBreakdownItem[];
  top_merchants: AgentLifestyleBreakdownItem[];
};

export type AgentReplenishmentItem = {
  public_id: string;
  name: string;
  predicted_due_on?: string | null;
  confidence?: number | null;
  confidence_level: "insufficient" | "low" | "medium" | "high";
  evidence_basis:
    | "configured_cadence"
    | "purchase_pattern"
    | "validated_model"
    | "insufficient_history";
  due_state: "likely_due" | "probably_due" | "not_due" | "learning";
  reason?: string | null;
  quantity?: string | null;
  unit?: string | null;
  last_acquired_on?: string | null;
  confirmed_acquisition_count: number;
};

export type AgentAcquisitionSummary = {
  acquired_on: string;
  merchant?: string | null;
  quantity?: number | null;
  unit?: string | null;
  evidence_type: "manual" | "receipt" | "transaction" | "imported" | "correction";
};

export type AgentReplenishmentSummaryBlock = AgentResponseBlockBase & {
  type: "replenishment_summary";
  title: string;
  items: AgentReplenishmentItem[];
  acquisition_history: AgentAcquisitionSummary[];
  acquisition_history_truncated: boolean;
  total_count: number;
  items_truncated: boolean;
};

export type AgentDealSummary = {
  public_id: string;
  merchant: string;
  headline: string;
  expires_at?: string | null;
  score?: number | null;
  category?: string | null;
  offer_type?: string | null;
  percent_off?: number | null;
  amount_off_cents?: number | null;
  currency_code?: string | null;
  minimum_spend_cents?: number | null;
  promo_code?: string | null;
  trust_status: "trusted" | "review";
  saved: boolean;
  relevant_to_need: boolean;
  relevance_reasons: string[];
};

export type AgentDealListBlock = AgentResponseBlockBase & {
  type: "deal_list";
  title: string;
  deals: AgentDealSummary[];
  total_count: number;
};

export type AgentReceiptLineSummary = {
  name: string;
  quantity?: number | null;
  unit?: string | null;
  line_total_cents?: number | null;
  match_status: "matched" | "possible" | "unmatched" | "ignored";
  household_item_name?: string | null;
  parent_category?: SpendingParentCategory | null;
  subcategory?: string | null;
  concept?: string | null;
  activity_type?: ClassificationActivityType | null;
  replenishment_eligibility?: ReplenishmentEligibility | null;
  classification_confidence?: number | null;
  confirmed_acquisition: boolean;
};

export type AgentReceiptSummaryBlock = AgentResponseBlockBase & {
  type: "receipt_summary";
  public_id: string;
  merchant?: string | null;
  purchased_at?: string | null;
  ingested_at?: string | null;
  total_cents?: number | null;
  currency_code: string;
  status: string;
  transaction_linked: boolean;
  matched_line_count: number;
  ignored_line_count: number;
  unmatched_line_count: number;
  total_line_count: number;
  items: AgentReceiptLineSummary[];
  items_truncated: boolean;
};

export type AgentErrandItem = {
  public_id: string;
  title: string;
  status: string;
  priority: string;
  errand_type: string;
  due_on?: string | null;
  place_name?: string | null;
  place_resolution_status: string;
  included_in_next_plan: boolean;
  household_items: string[];
};

export type AgentErrandPlanStop = {
  order: number;
  place_name: string;
  errands: string[];
  errands_truncated: boolean;
  household_items: string[];
  household_items_truncated: boolean;
};

export type AgentErrandPlanSummary = {
  public_id: string;
  status: string;
  planned_for?: string | null;
  is_stale: boolean;
  stale_reason?: string | null;
  estimated_stop_minutes: number;
  travel_duration_minutes?: number | null;
  distance_meters?: number | null;
  stops: AgentErrandPlanStop[];
  total_stop_count: number;
  stops_truncated: boolean;
};

export type AgentErrandSummaryBlock = AgentResponseBlockBase & {
  type: "errand_summary";
  title: string;
  errands: AgentErrandItem[];
  total_count: number;
  errands_truncated: boolean;
  plan?: AgentErrandPlanSummary | null;
};

export type AgentIntegrationState =
  | "connected"
  | "ready"
  | "attention_required"
  | "disconnected"
  | "disabled"
  | "unavailable";

export type AgentIntegrationStatusItem = {
  provider: string;
  scope?: "personal" | "workspace" | "application" | null;
  status: AgentIntegrationState;
  message?: string | null;
  last_successful_sync_at?: string | null;
};

export type AgentIntegrationStatusBlock = AgentResponseBlockBase & {
  type: "integration_status";
  title: string;
  integrations: AgentIntegrationStatusItem[];
};

export type AgentClassificationActivityBlock =
  AgentResponseBlockBase &
  ClassificationActivityRows & {
    type: "classification_activity_summary";
    block_version: "1.0";
    title: string;
    view: ClassificationActivityView;
    activity_date: string;
    timezone: "UTC";
    counts: ClassificationActivityCounts;
  };

export type AgentAttentionDomain =
  | "spending"
  | "lifestyle"
  | "transactions"
  | "replenishment"
  | "receipts"
  | "deals"
  | "errands"
  | "integrations"
  | "classification";

export type AgentAttentionPriority =
  | "action_required"
  | "time_sensitive"
  | "useful_to_know";

export type AgentNavigationBlock = AgentResponseBlockBase & {
  type: "navigation";
  label: string;
  target_surface: AgentSurface;
  entity?: AgentPageEntity | null;
};

export type AgentAttentionItem = {
  priority: AgentAttentionPriority;
  domain: AgentAttentionDomain;
  title: string;
  detail?: string | null;
  count: number;
  navigation?: AgentNavigationBlock | null;
};

/**
 * A compact, deterministic cross-domain view. Text is plain account-facing
 * copy produced by ExpenseOps composition code; navigation remains semantic.
 */
export type AgentAttentionSummaryBlock = AgentResponseBlockBase & {
  type: "attention_summary";
  block_version: "1.0";
  title: string;
  status: "complete" | "partial";
  checked_domains: AgentAttentionDomain[];
  unavailable_domains: AgentAttentionDomain[];
  items: AgentAttentionItem[];
  items_truncated: boolean;
};

export type AgentLabelValue = {
  label: string;
  value: string;
};

export type AgentActionPreview = {
  title: string;
  summary: string;
  details: AgentLabelValue[];
  confirm_label: string;
  cancel_label: string;
};

export type AgentProposalState =
  | "awaiting_confirmation"
  | "confirmed"
  | "executing"
  | "completed"
  | "cancelled"
  | "expired"
  | "failed"
  | "ambiguous";

/**
 * Confirmation clients send only proposal_id and proposal_version back to the
 * server. Exact normalized action parameters remain immutable server-side.
 */
export type AgentActionConfirmationBlock = AgentResponseBlockBase & AgentActionPreview & {
  type: "action_confirmation";
  action:
    | "mark_transaction_personal"
    | "post_splitwise_expense"
    | "apply_receipt_learning_batch"
    | "post_itemized_receipt_split";
  proposal_id: string;
  proposal_version: number;
  status: AgentProposalState;
  expires_at: string;
};

export type AgentActionDecision = {
  proposal_version: number;
};

export type AgentErrorBlock = AgentResponseBlockBase & {
  type: "error";
  code: string;
  title: string;
  message: string;
  retryable: boolean;
};

export type AgentEmptyStateBlock = AgentResponseBlockBase & {
  type: "empty";
  title: string;
  message: string;
  suggested_navigation: AgentNavigationBlock | null;
};

export type AgentResponseBlock =
  | AgentTextBlock
  | AgentTransactionListBlock
  | AgentSpendingSummaryBlock
  | AgentLifestyleSummaryBlock
  | AgentReplenishmentSummaryBlock
  | AgentDealListBlock
  | AgentReceiptSummaryBlock
  | AgentErrandSummaryBlock
  | AgentIntegrationStatusBlock
  | AgentClassificationActivityBlock
  | AgentAttentionSummaryBlock
  | AgentNavigationBlock
  | AgentActionConfirmationBlock
  | AgentErrorBlock
  | AgentEmptyStateBlock;

export type AgentStructuredResponse = {
  schema_version: AgentSchemaVersion;
  blocks: AgentResponseBlock[];
};

export type AgentConversation = {
  public_id: string;
  title: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
};

export type AgentConversationOut = AgentConversation;

export type AgentFeedbackRating = "helpful" | "not_helpful";

export type AgentFeedbackReason =
  | "wrong_data"
  | "didnt_understand"
  | "too_slow"
  | "other";

export type AgentFeedbackCreate = {
  rating: AgentFeedbackRating;
  reason?: AgentFeedbackReason | null;
};

export type AgentFeedback = {
  schema_version: AgentSchemaVersion;
  public_id: string;
  message_public_id: string;
  conversation_public_id: string;
  run_public_id: string;
  rating: AgentFeedbackRating;
  reason: AgentFeedbackReason | null;
  created_at: string;
  updated_at: string;
};

export type AgentFeedbackOut = AgentFeedback;

export type AgentMessage = {
  public_id: string;
  conversation_public_id: string;
  role: "user" | "assistant";
  text: string | null;
  structured_response: AgentStructuredResponse | null;
  client_message_id: string | null;
  feedback_eligible: boolean;
  feedback: AgentFeedbackOut | null;
  created_at: string;
};

export type AgentMessageOut = AgentMessage;

export type AgentRunOut = {
  public_id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  model_name: string | null;
  prompt_version: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  error_code: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type AgentTurnOut = {
  schema_version: AgentSchemaVersion;
  run: AgentRunOut;
  user_message: AgentMessageOut;
  assistant_message: AgentMessageOut;
};

export type AgentStreamEventBase = {
  schema_version: AgentSchemaVersion;
  sequence: number;
  run_public_id: string | null;
};

export type AgentRunStartedEvent = AgentStreamEventBase & {
  type: "run_started";
  resumed: boolean;
};

export type AgentAssistantDeltaEvent = AgentStreamEventBase & {
  type: "assistant_delta";
  delta: string;
};

export type AgentToolActivity =
  | "spending"
  | "transactions"
  | "replenishment"
  | "receipts"
  | "deals"
  | "errands"
  | "integrations"
  | "classification";

export type AgentToolStartedEvent = AgentStreamEventBase & {
  type: "tool_started";
  activity: AgentToolActivity;
  message: string;
};

export type AgentToolCompletedEvent = AgentStreamEventBase & {
  type: "tool_completed";
  activity: AgentToolActivity;
  message: string;
};

export type AgentStructuredResponseEvent = AgentStreamEventBase & {
  type: "structured_response";
  response: AgentStructuredResponse;
};

export type AgentAssistantCompletedEvent = AgentStreamEventBase & {
  type: "assistant_completed";
  message: AgentMessageOut;
};

export type AgentRunCompletedEvent = AgentStreamEventBase & {
  type: "run_completed";
  run: AgentRunOut;
};

export type AgentRunFailedEvent = AgentStreamEventBase & {
  type: "run_failed";
  run: AgentRunOut | null;
  code: string;
  message: string;
  retryable: boolean;
};

export type AgentStreamEvent =
  | AgentRunStartedEvent
  | AgentAssistantDeltaEvent
  | AgentToolStartedEvent
  | AgentToolCompletedEvent
  | AgentStructuredResponseEvent
  | AgentAssistantCompletedEvent
  | AgentRunCompletedEvent
  | AgentRunFailedEvent;

export type AgentConversationDetail = {
  conversation: AgentConversation;
  messages: AgentMessage[];
  messages_total: number;
  messages_offset: number;
  messages_has_more: boolean;
};

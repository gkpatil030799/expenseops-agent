/**
 * Platform-neutral contracts for the unified ExpenseOps Agent.
 *
 * These JSON shapes mirror `app/agent/contracts.py`. Keep this module free of
 * React, DOM types, CSS concerns, and executable callbacks. The backend remains
 * the canonical runtime validator and authorization boundary.
 */

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
  status: string;
};

export type AgentTransactionListBlock = AgentResponseBlockBase & {
  type: "transaction_list";
  title: string;
  transactions: AgentTransactionSummary[];
  total_count: number;
};

export type AgentSpendingSummaryBlock = AgentResponseBlockBase & {
  type: "spending_summary";
  title: string;
  start_date: string;
  end_date: string;
  currency_code: string;
  total_cents: number;
  previous_total_cents: number | null;
  change_percent: number | null;
  highlights: string[];
};

export type AgentReplenishmentItem = {
  public_id: string;
  name: string;
  predicted_due_on: string | null;
  confidence: number | null;
  reason: string | null;
};

export type AgentReplenishmentSummaryBlock = AgentResponseBlockBase & {
  type: "replenishment_summary";
  title: string;
  items: AgentReplenishmentItem[];
};

export type AgentDealSummary = {
  public_id: string;
  merchant: string;
  headline: string;
  expires_at: string | null;
  score: number | null;
};

export type AgentDealListBlock = AgentResponseBlockBase & {
  type: "deal_list";
  title: string;
  deals: AgentDealSummary[];
  total_count: number;
};

export type AgentReceiptLineSummary = {
  name: string;
  quantity: number | null;
  line_total_cents: number | null;
};

export type AgentReceiptSummaryBlock = AgentResponseBlockBase & {
  type: "receipt_summary";
  public_id: string;
  merchant: string | null;
  purchased_at: string | null;
  total_cents: number | null;
  currency_code: string;
  status: string;
  items: AgentReceiptLineSummary[];
};

export type AgentErrandItem = {
  public_id: string;
  title: string;
  status: string;
  due_on: string | null;
  place_name: string | null;
};

export type AgentErrandSummaryBlock = AgentResponseBlockBase & {
  type: "errand_summary";
  title: string;
  errands: AgentErrandItem[];
};

export type AgentIntegrationState =
  | "connected"
  | "attention_required"
  | "disconnected"
  | "unavailable";

export type AgentIntegrationStatusItem = {
  provider: string;
  status: AgentIntegrationState;
  message: string | null;
};

export type AgentIntegrationStatusBlock = AgentResponseBlockBase & {
  type: "integration_status";
  title: string;
  integrations: AgentIntegrationStatusItem[];
};

export type AgentNavigationBlock = AgentResponseBlockBase & {
  type: "navigation";
  label: string;
  target_surface: AgentSurface;
  entity: AgentPageEntity | null;
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
  proposal_id: string;
  proposal_version: number;
  status: AgentProposalState;
  expires_at: string;
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
  | AgentReplenishmentSummaryBlock
  | AgentDealListBlock
  | AgentReceiptSummaryBlock
  | AgentErrandSummaryBlock
  | AgentIntegrationStatusBlock
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

export type AgentMessage = {
  public_id: string;
  conversation_public_id: string;
  role: "user" | "assistant";
  text: string | null;
  structured_response: AgentStructuredResponse | null;
  client_message_id: string | null;
  created_at: string;
};

export type AgentMessageOut = AgentMessage;

export type AgentConversationDetail = {
  conversation: AgentConversation;
  messages: AgentMessage[];
  messages_total: number;
  messages_offset: number;
  messages_has_more: boolean;
};

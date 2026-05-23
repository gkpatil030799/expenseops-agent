export type Transaction = {
  id: number;
  plaid_transaction_id: string;
  merchant_name: string | null;
  name: string;
  amount_cents: number;
  amount: string;
  iso_currency_code: string;
  date: string | null;
  authorized_date: string | null;
  pending: boolean;
  status: string;
  agent_question: string | null;
  splitwise_expense_id: string | null;
  splitwise_payload_json: string | null;
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
};

export type Group = {
  id: number;
  name: string;
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

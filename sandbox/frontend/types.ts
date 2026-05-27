export type SandboxStepStatus = {
  name: string;
  status: "success" | "fallback" | "unknown" | "failed";
  detail?: string | null;
};

export type SandboxStatus = {
  enabled: boolean;
  plaid_env: string;
  webhook_url_configured: boolean;
  webhook_url?: string | null;
  state_exists: boolean;
  item_id?: string | null;
  item_db_id?: number | null;
  access_token_exists: boolean;
  access_token_redacted?: string | null;
  transactions_cursor_exists: boolean;
  latest_event_timestamp?: string | null;
  latest_known_trace_id?: string | null;
};

export type SandboxTransactionRequest = {
  description: string;
  amount: string;
  iso_currency_code: string;
  date_transacted: string;
  date_posted: string;
  auto_fire_webhook: boolean;
  auto_sync_after: boolean;
};

export type SandboxCreateTransactionResponse = {
  trace_id: string;
  transaction: Record<string, unknown>;
  plaid_request_id?: string | null;
  created: boolean;
  steps: SandboxStepStatus[];
};

export type SandboxWebhookResponse = {
  trace_id: string;
  webhook_type: string;
  webhook_code: string;
  webhook_fired: boolean;
  plaid_request_id?: string | null;
};

export type SandboxSyncedTransaction = {
  id?: number;
  name?: string;
  merchant_name?: string | null;
  amount_cents?: number;
  pending?: boolean;
  status?: string;
  date?: string | null;
  created_at?: string | null;
};

export type SandboxSyncResponse = {
  trace_id: string;
  added_count: number;
  modified_count: number;
  removed_count: number;
  cursor_present: boolean;
  cursor_updated: boolean;
  next_cursor_present: boolean;
  added_transactions: SandboxSyncedTransaction[];
};

export type SandboxRunResponse = {
  trace_id: string;
  status: "completed" | "partial" | "failed";
  steps: SandboxStepStatus[];
  details: {
    transaction?: SandboxCreateTransactionResponse;
    webhook?: SandboxWebhookResponse;
    fallback_sync_attempts?: Array<SandboxSyncResponse & { attempt?: number }>;
    [key: string]: unknown;
  };
};

export type SandboxEvent = {
  id: string;
  trace_id: string;
  event_type: string;
  status: string;
  message?: string;
  payload?: Record<string, unknown>;
  plaid_request_id?: string | null;
  plaid_item_id?: string | null;
  created_at: string;
};

export type SandboxEventsResponse = {
  events: SandboxEvent[];
};

export type SandboxScenarioFlow = "create_only" | "manual_sync" | "webhook" | "e2e";

export type SandboxScenarioDefinition = {
  id: string;
  name: string;
  description: string;
  flow: SandboxScenarioFlow;
  transaction?: {
    description: string;
    amount: string | number;
    iso_currency_code: string;
    date_transacted?: string | null;
    date_posted?: string | null;
  } | null;
  expectations: Record<string, unknown>;
  timeout_seconds: number;
  tags: string[];
  enabled: boolean;
};

export type SandboxScenarioAssertion = {
  name: string;
  status: "passed" | "failed" | "skipped";
  expected?: unknown;
  actual?: unknown;
  message: string;
};

export type SandboxScenarioResult = {
  scenario_id: string;
  scenario_name: string;
  scenario_run_id: string;
  trace_id: string;
  status: "passed" | "failed" | "partial" | "error";
  started_at: string;
  completed_at: string;
  duration_ms: number;
  flow: SandboxScenarioFlow;
  transaction_summary: Record<string, unknown>;
  assertions: SandboxScenarioAssertion[];
  events_summary: Record<string, unknown>;
  raw_events: SandboxEvent[];
  error_message?: string | null;
  error_details: Record<string, unknown>;
};

export type SandboxScenarioRunAggregate = {
  status: "passed" | "failed" | "partial" | "error";
  total: number;
  passed: number;
  failed: number;
  partial: number;
  errors: number;
  rate_limit_errors: number;
  passed_count: number;
  failed_count: number;
  error_count: number;
  rate_limit_error_count: number;
  results: SandboxScenarioResult[];
};

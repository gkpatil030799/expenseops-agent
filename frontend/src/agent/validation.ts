import {
  AGENT_SCHEMA_VERSION,
  type AgentConversation,
  type AgentConversationDetail,
  type AgentActionConfirmationBlock,
  type AgentFeedbackOut,
  type AgentMessage,
  type AgentRunOut,
  type AgentStreamEvent,
  type AgentStructuredResponse,
} from "./contracts";
import { isAgentNavigationRequest } from "./pageContext";

export class AgentProtocolError extends Error {
  constructor(message = "ExpenseOps received an unsupported Agent response.") {
    super(message);
    this.name = "AgentProtocolError";
  }
}

export function parseAgentStreamEvent(value: unknown): AgentStreamEvent {
  const record = requireRecord(value);
  requireSchema(record);
  requireNonNegativeInteger(record.sequence);
  requireNullableString(record.run_public_id, 128);
  const type = requireString(record.type, 64);
  switch (type) {
    case "run_started":
      requireBoolean(record.resumed);
      break;
    case "assistant_delta":
      requireString(record.delta, 1_000);
      break;
    case "tool_started":
    case "tool_completed":
      requireToolActivity(record.activity);
      requireString(record.message, 160);
      break;
    case "structured_response":
      parseAgentStructuredResponse(record.response);
      break;
    case "assistant_completed":
      {
        const message = parseAgentMessage(record.message);
        if (
          message.feedback !== null &&
          message.feedback.run_public_id !== record.run_public_id
        ) {
          throw new AgentProtocolError();
        }
      }
      break;
    case "run_completed":
      parseAgentRun(record.run);
      break;
    case "run_failed":
      if (record.run !== null && record.run !== undefined) parseAgentRun(record.run);
      requireString(record.code, 100);
      requireString(record.message, 1_000);
      requireBoolean(record.retryable);
      break;
    default:
      throw new AgentProtocolError("ExpenseOps received an unknown Agent stream event.");
  }
  return value as AgentStreamEvent;
}

export function parseAgentStructuredResponse(value: unknown): AgentStructuredResponse {
  const record = requireRecord(value);
  requireSchema(record);
  if (!Array.isArray(record.blocks) || record.blocks.length < 1 || record.blocks.length > 50) {
    throw new AgentProtocolError();
  }
  record.blocks.forEach(parseSupportedBlock);
  return value as AgentStructuredResponse;
}

export function parseAgentConversation(value: unknown): AgentConversation {
  const record = requireRecord(value);
  requireString(record.public_id, 128);
  requireNullableString(record.title, 120);
  requireNullableString(record.archived_at, 128);
  requireString(record.created_at, 128);
  requireString(record.updated_at, 128);
  return value as AgentConversation;
}

export function parseAgentConversationList(value: unknown): AgentConversation[] {
  if (!Array.isArray(value) || value.length > 100) throw new AgentProtocolError();
  return value.map(parseAgentConversation);
}

export function parseAgentConversationDetail(value: unknown): AgentConversationDetail {
  const record = requireRecord(value);
  parseAgentConversation(record.conversation);
  if (!Array.isArray(record.messages) || record.messages.length > 500) {
    throw new AgentProtocolError();
  }
  record.messages.forEach(parseAgentMessage);
  requireNonNegativeInteger(record.messages_total);
  requireNonNegativeInteger(record.messages_offset);
  requireBoolean(record.messages_has_more);
  return value as AgentConversationDetail;
}

export function parseAgentFeedback(value: unknown): AgentFeedbackOut {
  const record = requireRecord(value);
  requireAllowedKeys(record, [
    "schema_version",
    "public_id",
    "message_public_id",
    "conversation_public_id",
    "run_public_id",
    "rating",
    "reason",
    "created_at",
    "updated_at",
  ]);
  requireSchema(record);
  requireString(record.public_id, 128);
  requireString(record.message_public_id, 128);
  requireString(record.conversation_public_id, 128);
  requireString(record.run_public_id, 128);
  requireOneOf(record.rating, ["helpful", "not_helpful"]);
  if (record.reason === undefined) throw new AgentProtocolError();
  if (record.reason !== null) {
    requireOneOf(record.reason, ["wrong_data", "didnt_understand", "too_slow", "other"]);
  }
  if (record.rating === "helpful" && record.reason != null) throw new AgentProtocolError();
  requireString(record.created_at, 128);
  requireString(record.updated_at, 128);
  return value as AgentFeedbackOut;
}

function parseAgentMessage(value: unknown): AgentMessage {
  const record = requireRecord(value);
  requireString(record.public_id, 128);
  requireString(record.conversation_public_id, 128);
  if (record.role !== "user" && record.role !== "assistant") throw new AgentProtocolError();
  requireNullableString(record.text, 8_000);
  if (record.structured_response !== null && record.structured_response !== undefined) {
    parseAgentStructuredResponse(record.structured_response);
  }
  requireNullableString(record.client_message_id, 64);
  requireBoolean(record.feedback_eligible);
  if (record.feedback === undefined) throw new AgentProtocolError();
  if (record.role === "user" && record.feedback_eligible) throw new AgentProtocolError();
  if (record.feedback !== null && record.feedback !== undefined) {
    const feedback = parseAgentFeedback(record.feedback);
    if (
      record.role !== "assistant" ||
      record.feedback_eligible !== true ||
      feedback.message_public_id !== record.public_id ||
      feedback.conversation_public_id !== record.conversation_public_id
    ) {
      throw new AgentProtocolError();
    }
  }
  requireString(record.created_at, 128);
  if (record.text == null && record.structured_response == null) throw new AgentProtocolError();
  return value as AgentMessage;
}

function parseAgentRun(value: unknown): AgentRunOut {
  const record = requireRecord(value);
  requireString(record.public_id, 128);
  if (!["queued", "running", "completed", "failed", "cancelled"].includes(String(record.status))) {
    throw new AgentProtocolError();
  }
  requireNullableString(record.model_name, 128);
  requireNullableString(record.prompt_version, 64);
  requireNullableInteger(record.input_tokens);
  requireNullableInteger(record.output_tokens);
  requireNullableInteger(record.total_tokens);
  requireNullableString(record.error_code, 100);
  requireString(record.created_at, 128);
  requireNullableString(record.started_at, 128);
  requireNullableString(record.completed_at, 128);
  return value as AgentRunOut;
}

function parseSupportedBlock(value: unknown): void {
  const block = requireRecord(value);
  const type = requireString(block.type, 64);
  requireNullableString(block.block_id, 100);
  switch (type) {
    case "text":
      requireString(block.text, 8_000);
      return;
    case "spending_summary":
      requireString(block.title, 160);
      requireString(block.start_date, 32);
      requireString(block.end_date, 32);
      requireString(block.currency_code, 8);
      requireOneOf(block.spend_basis, ["card", "actual_share"]);
      requireNonNegativeInteger(block.total_cents);
      requireNullableInteger(block.previous_total_cents);
      requireNonNegativeInteger(block.credits_cents);
      requireNonNegativeInteger(block.previous_credits_cents);
      requireNonNegativeInteger(block.unknown_share_transactions);
      requireNonNegativeInteger(block.previous_unknown_share_transactions);
      requireNonNegativeInteger(block.unknown_credit_share_transactions);
      requireNonNegativeInteger(block.previous_unknown_credit_share_transactions);
      if (
        block.spend_basis === "card" &&
        (block.unknown_share_transactions !== 0 ||
          block.previous_unknown_share_transactions !== 0 ||
          block.unknown_credit_share_transactions !== 0 ||
          block.previous_unknown_credit_share_transactions !== 0)
      ) {
        throw new AgentProtocolError();
      }
      requireNullableFiniteNumber(block.change_percent);
      if (
        (block.unknown_share_transactions !== 0 ||
          block.previous_unknown_share_transactions !== 0) &&
        block.change_percent !== null
      ) {
        throw new AgentProtocolError();
      }
      requireStringArray(block.highlights, 10, 1_000);
      parseBreakdowns(block.top_categories);
      parseBreakdowns(block.top_merchants);
      return;
    case "transaction_list":
      requireString(block.title, 160);
      requireNonNegativeInteger(block.total_count);
      if (!Array.isArray(block.transactions) || block.transactions.length > 50) {
        throw new AgentProtocolError();
      }
      block.transactions.forEach((item) => {
        const transaction = requireRecord(item);
        requireString(transaction.public_id, 128);
        requireString(transaction.merchant, 255);
        requireInteger(transaction.amount_cents);
        requireString(transaction.currency_code, 8);
        requireNullableString(transaction.occurred_on, 32);
        requireNullableString(transaction.category, 255);
        requireString(transaction.status, 64);
        requireBoolean(transaction.pending);
      });
      return;
    case "replenishment_summary":
      requireString(block.title, 160);
      requireNonNegativeInteger(block.total_count);
      requireBoolean(block.items_truncated);
      if (!Array.isArray(block.items) || block.items.length > 50) throw new AgentProtocolError();
      block.items.forEach((item) => {
        const row = requireRecord(item);
        requireString(row.public_id, 128);
        requireString(row.name, 255);
        requireNullableString(row.predicted_due_on, 32);
        requireNullableNumberRange(row.confidence, 0, 1);
        requireOneOf(row.confidence_level, ["insufficient", "low", "medium", "high"]);
        requireOneOf(row.evidence_basis, [
          "configured_cadence",
          "purchase_pattern",
          "validated_model",
          "insufficient_history",
        ]);
        requireOneOf(row.due_state, ["likely_due", "probably_due", "not_due", "learning"]);
        requireNullableString(row.reason, 500);
        requireNullableString(row.quantity, 64);
        requireNullableString(row.unit, 64);
        requireNullableString(row.last_acquired_on, 32);
        requireNonNegativeInteger(row.confirmed_acquisition_count);
      });
      if (!Array.isArray(block.acquisition_history) || block.acquisition_history.length > 20) {
        throw new AgentProtocolError();
      }
      requireBoolean(block.acquisition_history_truncated);
      block.acquisition_history.forEach((item) => {
        const row = requireRecord(item);
        requireString(row.acquired_on, 32);
        requireNullableString(row.merchant, 255);
        requireNullableFiniteNumber(row.quantity);
        requireNullableString(row.unit, 64);
        requireOneOf(row.evidence_type, ["manual", "receipt", "transaction", "imported", "correction"]);
      });
      return;
    case "receipt_summary":
      requireString(block.public_id, 128);
      requireNullableString(block.merchant, 255);
      requireNullableString(block.purchased_at, 128);
      requireNullableString(block.ingested_at, 128);
      requireNullableIntegerValue(block.total_cents);
      requireString(block.currency_code, 8);
      requireString(block.status, 64);
      requireBoolean(block.transaction_linked);
      requireNonNegativeInteger(block.matched_line_count);
      requireNonNegativeInteger(block.ignored_line_count);
      requireNonNegativeInteger(block.unmatched_line_count);
      requireNonNegativeInteger(block.total_line_count);
      requireBoolean(block.items_truncated);
      if (!Array.isArray(block.items) || block.items.length > 100) throw new AgentProtocolError();
      block.items.forEach((item) => {
        const row = requireRecord(item);
        requireString(row.name, 500);
        requireNullableFiniteNumber(row.quantity);
        requireNullableString(row.unit, 64);
        requireNullableIntegerValue(row.line_total_cents);
        requireOneOf(row.match_status, ["matched", "possible", "unmatched", "ignored"]);
        requireNullableString(row.household_item_name, 255);
        requireBoolean(row.confirmed_acquisition);
      });
      return;
    case "deal_list":
      requireString(block.title, 160);
      requireNonNegativeInteger(block.total_count);
      if (!Array.isArray(block.deals) || block.deals.length > 50) throw new AgentProtocolError();
      block.deals.forEach((item) => {
        const row = requireRecord(item);
        requireString(row.public_id, 128);
        requireString(row.merchant, 255);
        requireString(row.headline, 500);
        requireNullableString(row.expires_at, 128);
        requireNullableNumberRange(row.score, 0, 100);
        requireNullableString(row.category, 64);
        requireNullableString(row.offer_type, 32);
        requireNullableNumberRange(row.percent_off, 0, 100);
        requireNullableNonNegativeInteger(row.amount_off_cents);
        requireNullableString(row.currency_code, 8);
        requireNullableNonNegativeInteger(row.minimum_spend_cents);
        requireNullableString(row.promo_code, 128);
        requireOneOf(row.trust_status, ["trusted", "review"]);
        requireBoolean(row.saved);
        requireBoolean(row.relevant_to_need);
        requireStringArray(row.relevance_reasons, 5, 160);
      });
      return;
    case "errand_summary":
      requireString(block.title, 160);
      requireNonNegativeInteger(block.total_count);
      requireBoolean(block.errands_truncated);
      if (!Array.isArray(block.errands) || block.errands.length > 50) throw new AgentProtocolError();
      block.errands.forEach(parseErrand);
      if (block.plan !== null && block.plan !== undefined) parseErrandPlan(block.plan);
      return;
    case "integration_status":
      requireString(block.title, 160);
      if (!Array.isArray(block.integrations) || block.integrations.length > 25) {
        throw new AgentProtocolError();
      }
      block.integrations.forEach((item) => {
        const row = requireRecord(item);
        requireString(row.provider, 64);
        if (row.scope !== null && row.scope !== undefined) {
          requireOneOf(row.scope, ["personal", "workspace", "application"]);
        }
        requireOneOf(row.status, [
          "connected",
          "ready",
          "attention_required",
          "disconnected",
          "disabled",
          "unavailable",
        ]);
        requireNullableString(row.message, 500);
        requireNullableString(row.last_successful_sync_at, 128);
      });
      return;
    case "attention_summary":
      parseAttentionSummaryBlock(block);
      return;
    case "navigation":
      parseNavigationBlock(block);
      return;
    case "action_confirmation":
      parseAgentActionConfirmation(block);
      return;
    case "error":
      requireString(block.code, 100);
      requireString(block.title, 160);
      requireString(block.message, 1_000);
      requireBoolean(block.retryable);
      return;
    case "empty":
      requireString(block.title, 160);
      requireString(block.message, 1_000);
      if (block.suggested_navigation !== null && block.suggested_navigation !== undefined) {
        parseNavigationBlock(block.suggested_navigation);
      }
      return;
    default:
      throw new AgentProtocolError("ExpenseOps cannot safely display this response yet.");
  }
}

export function parseAgentActionConfirmation(
  value: unknown,
): AgentActionConfirmationBlock {
  const block = requireRecord(value);
  requireAllowedKeys(block, [
    "type",
    "block_id",
    "action",
    "title",
    "summary",
    "details",
    "confirm_label",
    "cancel_label",
    "proposal_id",
    "proposal_version",
    "status",
    "expires_at",
  ]);
  if (block.type !== "action_confirmation") throw new AgentProtocolError();
  requireNullableString(block.block_id, 100);
  requireOneOf(block.action, [
    "mark_transaction_personal",
    "post_splitwise_expense",
    "apply_receipt_learning_batch",
  ]);
  requireString(block.title, 160);
  requireString(block.summary, 1_000);
  if (!Array.isArray(block.details) || block.details.length > 25) {
    throw new AgentProtocolError();
  }
  block.details.forEach((value) => {
    const detail = requireRecord(value);
    requireAllowedKeys(detail, ["label", "value"]);
    requireString(detail.label, 100);
    requireString(detail.value, 500);
  });
  requireString(block.confirm_label, 80);
  requireString(block.cancel_label, 80);
  requireString(block.proposal_id, 128);
  requirePositiveInteger(block.proposal_version);
  requireOneOf(block.status, [
    "awaiting_confirmation",
    "confirmed",
    "executing",
    "completed",
    "cancelled",
    "expired",
    "failed",
    "ambiguous",
  ]);
  requireString(block.expires_at, 128);
  return value as AgentActionConfirmationBlock;
}

const ATTENTION_DOMAINS = [
  "spending",
  "transactions",
  "replenishment",
  "receipts",
  "deals",
  "errands",
  "integrations",
] as const;

const ATTENTION_PRIORITIES = [
  "action_required",
  "time_sensitive",
  "useful_to_know",
] as const;

function parseAttentionSummaryBlock(block: Record<string, unknown>): void {
  requireAllowedKeys(block, [
    "type",
    "block_id",
    "block_version",
    "title",
    "status",
    "checked_domains",
    "unavailable_domains",
    "items",
    "items_truncated",
  ]);
  if (block.block_version !== "1.0") {
    throw new AgentProtocolError("ExpenseOps cannot display this attention-summary version yet.");
  }
  requireString(block.title, 160);
  requireOneOf(block.status, ["complete", "partial"]);
  const checkedDomains = parseAttentionDomains(block.checked_domains, 1);
  const unavailableDomains = parseAttentionDomains(block.unavailable_domains, 0);
  if (unavailableDomains.some((domain) => checkedDomains.includes(domain))) {
    throw new AgentProtocolError();
  }
  if (
    (block.status === "complete" && unavailableDomains.length !== 0) ||
    (block.status === "partial" && unavailableDomains.length === 0)
  ) {
    throw new AgentProtocolError();
  }
  if (!Array.isArray(block.items) || block.items.length > 12) {
    throw new AgentProtocolError();
  }
  const seenItems = new Set<string>();
  let previousOrder = -1;
  block.items.forEach((value) => {
    const item = requireRecord(value);
    requireAllowedKeys(item, ["priority", "domain", "title", "detail", "count", "navigation"]);
    requireOneOf(item.priority, ATTENTION_PRIORITIES);
    requireOneOf(item.domain, ATTENTION_DOMAINS);
    requireString(item.title, 160);
    requireNullableString(item.detail, 500);
    requirePositiveInteger(item.count);
    if (!checkedDomains.includes(item.domain as (typeof ATTENTION_DOMAINS)[number])) {
      throw new AgentProtocolError();
    }
    if (item.navigation !== null && item.navigation !== undefined) {
      parseNavigationBlock(item.navigation);
    }
    const identity = `${String(item.priority)}:${String(item.domain)}`;
    if (seenItems.has(identity)) throw new AgentProtocolError();
    seenItems.add(identity);
    const order =
      ATTENTION_PRIORITIES.indexOf(item.priority as (typeof ATTENTION_PRIORITIES)[number]) *
        ATTENTION_DOMAINS.length +
      ATTENTION_DOMAINS.indexOf(item.domain as (typeof ATTENTION_DOMAINS)[number]);
    if (order <= previousOrder) throw new AgentProtocolError();
    previousOrder = order;
  });
  requireBoolean(block.items_truncated);
}

function parseAttentionDomains(
  value: unknown,
  minimum: number,
): (typeof ATTENTION_DOMAINS)[number][] {
  if (!Array.isArray(value) || value.length < minimum || value.length > ATTENTION_DOMAINS.length) {
    throw new AgentProtocolError();
  }
  const result = value.map((domain) => {
    requireOneOf(domain, ATTENTION_DOMAINS);
    return domain as (typeof ATTENTION_DOMAINS)[number];
  });
  if (new Set(result).size !== result.length) throw new AgentProtocolError();
  if (result.some((domain, index) => index > 0 && ATTENTION_DOMAINS.indexOf(domain) <= ATTENTION_DOMAINS.indexOf(result[index - 1]))) {
    throw new AgentProtocolError();
  }
  return result;
}

function parseNavigationBlock(value: unknown): void {
  const navigation = requireRecord(value);
  requireAllowedKeys(navigation, ["type", "block_id", "label", "target_surface", "entity"]);
  if (navigation.type !== "navigation") {
    throw new AgentProtocolError();
  }
  requireNullableString(navigation.block_id, 128);
  requireString(navigation.label, 120);
  requireString(navigation.target_surface, 64);
  if (!isAgentNavigationRequest({
    target_surface: navigation.target_surface,
    entity: navigation.entity ?? null,
  })) {
    throw new AgentProtocolError("ExpenseOps received an unsafe navigation target.");
  }
}

function requireAllowedKeys(record: Record<string, unknown>, allowed: string[]): void {
  if (Object.keys(record).some((key) => !allowed.includes(key))) {
    throw new AgentProtocolError();
  }
}

function parseBreakdowns(value: unknown): void {
  if (!Array.isArray(value) || value.length > 10) throw new AgentProtocolError();
  value.forEach((item) => {
    const row = requireRecord(item);
    requireString(row.name, 255);
    requireNonNegativeInteger(row.amount_cents);
    requireNonNegativeInteger(row.transaction_count);
    requireFiniteNumber(row.percentage);
    if ((row.percentage as number) < 0 || (row.percentage as number) > 100) {
      throw new AgentProtocolError();
    }
    if (row.previous_amount_cents !== null && row.previous_amount_cents !== undefined) {
      requireNonNegativeInteger(row.previous_amount_cents);
    }
  });
}

function parseErrand(value: unknown): void {
  const row = requireRecord(value);
  requireString(row.public_id, 128);
  requireString(row.title, 255);
  requireString(row.status, 64);
  requireString(row.priority, 32);
  requireString(row.errand_type, 32);
  requireNullableString(row.due_on, 32);
  requireNullableString(row.place_name, 255);
  requireString(row.place_resolution_status, 32);
  requireBoolean(row.included_in_next_plan);
  requireStringArray(row.household_items, 20, 255);
}

function parseErrandPlan(value: unknown): void {
  const plan = requireRecord(value);
  requireString(plan.public_id, 128);
  requireString(plan.status, 32);
  requireNullableString(plan.planned_for, 128);
  requireBoolean(plan.is_stale);
  requireNullableString(plan.stale_reason, 255);
  requireNonNegativeInteger(plan.estimated_stop_minutes);
  requireNullableNonNegativeInteger(plan.travel_duration_minutes);
  requireNullableNonNegativeInteger(plan.distance_meters);
  requireNonNegativeInteger(plan.total_stop_count);
  requireBoolean(plan.stops_truncated);
  if (!Array.isArray(plan.stops) || plan.stops.length > 12) throw new AgentProtocolError();
  plan.stops.forEach((item) => {
    const stop = requireRecord(item);
    requireNonNegativeInteger(stop.order);
    if (stop.order === 0) throw new AgentProtocolError();
    requireString(stop.place_name, 255);
    requireStringArray(stop.errands, 20, 255);
    requireBoolean(stop.errands_truncated);
    requireStringArray(stop.household_items, 20, 255);
    requireBoolean(stop.household_items_truncated);
  });
}

function requireRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new AgentProtocolError();
  }
  return value as Record<string, unknown>;
}

function requireSchema(value: Record<string, unknown>): void {
  if (value.schema_version !== AGENT_SCHEMA_VERSION) {
    throw new AgentProtocolError("ExpenseOps cannot display this Agent response version yet.");
  }
}

function requireString(value: unknown, max: number): string {
  if (typeof value !== "string" || value.length < 1 || value.length > max) {
    throw new AgentProtocolError();
  }
  return value;
}

function requireNullableString(value: unknown, max: number): void {
  if (value === null || value === undefined) return;
  requireString(value, max);
}

function requireBoolean(value: unknown): void {
  if (typeof value !== "boolean") throw new AgentProtocolError();
}

function requireInteger(value: unknown): void {
  if (!Number.isSafeInteger(value)) throw new AgentProtocolError();
}

function requireNonNegativeInteger(value: unknown): void {
  requireInteger(value);
  if ((value as number) < 0) throw new AgentProtocolError();
}

function requirePositiveInteger(value: unknown): void {
  requireInteger(value);
  if ((value as number) < 1) throw new AgentProtocolError();
}

function requireNullableInteger(value: unknown): void {
  if (value === null || value === undefined) return;
  requireNonNegativeInteger(value);
}

function requireNullableIntegerValue(value: unknown): void {
  if (value === null || value === undefined) return;
  requireInteger(value);
}

function requireNullableNonNegativeInteger(value: unknown): void {
  if (value === null || value === undefined) return;
  requireNonNegativeInteger(value);
}

function requireFiniteNumber(value: unknown): void {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new AgentProtocolError();
}

function requireNullableFiniteNumber(value: unknown): void {
  if (value === null || value === undefined) return;
  requireFiniteNumber(value);
}

function requireNullableNumberRange(value: unknown, minimum: number, maximum: number): void {
  if (value === null || value === undefined) return;
  requireFiniteNumber(value);
  if ((value as number) < minimum || (value as number) > maximum) {
    throw new AgentProtocolError();
  }
}

function requireStringArray(value: unknown, maxItems: number, maxLength: number): void {
  if (!Array.isArray(value) || value.length > maxItems) throw new AgentProtocolError();
  value.forEach((item) => requireString(item, maxLength));
}

function requireToolActivity(value: unknown): void {
  requireOneOf(value, [
    "spending",
    "transactions",
    "replenishment",
    "receipts",
    "deals",
    "errands",
    "integrations",
  ]);
}

function requireOneOf(value: unknown, allowed: readonly string[]): void {
  if (typeof value !== "string" || !allowed.includes(value)) throw new AgentProtocolError();
}

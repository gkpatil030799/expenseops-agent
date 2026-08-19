import {
  AGENT_SCHEMA_VERSION,
  type AgentConversation,
  type AgentConversationDetail,
  type AgentActionConfirmationBlock,
  type AgentFeedbackOut,
  type AgentMessage,
  type AgentReviewSessionOut,
  type AgentRunOut,
  type AgentStreamEvent,
  type AgentStructuredResponse,
} from "./contracts";
import type { ClassificationActivityOut } from "../classificationActivity";
import { isAgentNavigationRequest } from "./pageContext";

export const AGENT_RESPONSE_UNAVAILABLE_MESSAGE =
  "I couldn't display that answer. Reload the conversation or try again.";

export class AgentProtocolError extends Error {
  constructor(message = AGENT_RESPONSE_UNAVAILABLE_MESSAGE) {
    super(message);
    this.name = "AgentProtocolError";
  }
}

const STREAM_EVENT_BASE_KEYS = ["schema_version", "sequence", "run_public_id", "type"];

export function parseAgentStreamEvent(value: unknown): AgentStreamEvent {
  const record = requireRecord(value);
  requireSchema(record);
  requireNonNegativeInteger(record.sequence);
  requireNullableString(record.run_public_id, 128);
  const type = requireString(record.type, 64);
  switch (type) {
    case "run_started":
      requireAllowedKeys(record, [...STREAM_EVENT_BASE_KEYS, "resumed"]);
      requireBoolean(record.resumed);
      break;
    case "assistant_delta":
      requireAllowedKeys(record, [...STREAM_EVENT_BASE_KEYS, "delta"]);
      if (!requireString(record.delta, 1_000).trim()) throw new AgentProtocolError();
      break;
    case "tool_started":
    case "tool_completed":
      requireAllowedKeys(record, [...STREAM_EVENT_BASE_KEYS, "activity", "message"]);
      requireToolActivity(record.activity);
      requireString(record.message, 160);
      break;
    case "structured_response":
      requireAllowedKeys(record, [...STREAM_EVENT_BASE_KEYS, "response"]);
      parseAgentStructuredResponse(record.response);
      break;
    case "assistant_completed":
      {
        requireAllowedKeys(record, [...STREAM_EVENT_BASE_KEYS, "message"]);
        const message = parseAgentMessage(record.message);
        if (message.role !== "assistant") throw new AgentProtocolError();
        if (
          message.feedback !== null &&
          message.feedback.run_public_id !== record.run_public_id
        ) {
          throw new AgentProtocolError();
        }
      }
      break;
    case "run_completed":
      {
        requireAllowedKeys(record, [...STREAM_EVENT_BASE_KEYS, "run"]);
        const run = parseAgentRun(record.run);
        requireTerminalRunIdentity(record, run);
        if (run.status !== "completed") throw new AgentProtocolError();
      }
      break;
    case "run_failed":
      requireAllowedKeys(record, [...STREAM_EVENT_BASE_KEYS, "run", "code", "message", "retryable"]);
      if (record.run !== null && record.run !== undefined) {
        const run = parseAgentRun(record.run);
        requireTerminalRunIdentity(record, run);
        if (run.status !== "failed" && run.status !== "cancelled") throw new AgentProtocolError();
      }
      requireCode(record.code, 100);
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
  requireAllowedKeys(record, ["schema_version", "blocks"]);
  requireKeys(record, ["schema_version", "blocks"]);
  requireSchema(record);
  if (!Array.isArray(record.blocks) || record.blocks.length < 1 || record.blocks.length > 50) {
    throw new AgentProtocolError();
  }
  record.blocks.forEach(parseSupportedBlock);
  return value as AgentStructuredResponse;
}

export function parseClassificationActivityOut(value: unknown): ClassificationActivityOut {
  const record = requireRecord(value);
  const keys = [
    "schema_version", "view", "activity_date", "timezone", "as_of", "counts",
    "transactions", "receipt_items", "categories", "new_categories", "receipt_matches",
    "new_household_items", "cadence_updates", "uncertain", "truncated_sections",
  ];
  requireAllowedKeys(record, keys);
  requireKeys(record, keys);
  requireSchema(record);
  requireString(record.as_of, 128);
  const { schema_version: _schemaVersion, as_of: _asOf, ...activity } = record;
  parseClassificationActivityBlock({
    ...activity,
    type: "classification_activity_summary",
    block_version: "1.0",
    title: "Classification activity",
  });
  return value as ClassificationActivityOut;
}

export function parseAgentConversation(value: unknown): AgentConversation {
  const record = requireRecord(value);
  requireAllowedKeys(record, ["public_id", "title", "archived_at", "created_at", "updated_at"]);
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
  requireAllowedKeys(record, [
    "conversation", "messages", "messages_total", "messages_offset", "messages_has_more",
  ]);
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
  requireAllowedKeys(record, [
    "public_id", "conversation_public_id", "role", "text", "structured_response",
    "client_message_id", "feedback_eligible", "feedback", "created_at",
  ]);
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
  requireAllowedKeys(record, [
    "public_id", "status", "model_name", "prompt_version", "input_tokens", "output_tokens",
    "total_tokens", "error_code", "created_at", "started_at", "completed_at",
  ]);
  requireString(record.public_id, 128);
  if (!["queued", "running", "completed", "failed", "cancelled"].includes(String(record.status))) {
    throw new AgentProtocolError();
  }
  requireNullableString(record.model_name, 128);
  requireNullableString(record.prompt_version, 64);
  requireNullableInteger(record.input_tokens);
  requireNullableInteger(record.output_tokens);
  requireNullableInteger(record.total_tokens);
  if (record.error_code !== null && record.error_code !== undefined) requireCode(record.error_code, 100);
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
      requireBlockKeys(block, ["text"]);
      requireString(block.text, 8_000);
      return;
    case "spending_summary":
      requireBlockKeys(block, [
        "focus", "requested_limit", "title", "start_date", "end_date", "currency_code",
        "spend_basis", "total_cents", "previous_total_cents", "credits_cents",
        "previous_credits_cents", "unknown_share_transactions",
        "previous_unknown_share_transactions", "unknown_credit_share_transactions",
        "previous_unknown_credit_share_transactions", "change_percent", "highlights",
        "top_categories", "top_merchants",
      ]);
      if (block.focus !== undefined) {
        requireOneOf(block.focus, [
          "summary", "comparison", "top_categories", "top_merchants", "change_explanation",
        ]);
      }
      if (block.requested_limit !== undefined && block.requested_limit !== null) {
        requirePositiveInteger(block.requested_limit);
        if ((block.requested_limit as number) > 10) throw new AgentProtocolError();
      }
      requireString(block.title, 160);
      const startDate = requireDate(block.start_date);
      const endDate = requireDate(block.end_date);
      if (startDate > endDate) throw new AgentProtocolError();
      requireCurrencyCode(block.currency_code);
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
      {
        const focus = block.focus ?? "summary";
        const requestedLimit = typeof block.requested_limit === "number"
          ? block.requested_limit
          : null;
        if (focus === "top_categories") {
          if (
            requestedLimit === null ||
            (block.top_categories as unknown[]).length > requestedLimit ||
            (block.top_merchants as unknown[]).length > 0
          ) throw new AgentProtocolError();
        } else if (focus === "top_merchants") {
          if (
            requestedLimit === null ||
            (block.top_merchants as unknown[]).length > requestedLimit ||
            (block.top_categories as unknown[]).length > 0
          ) throw new AgentProtocolError();
        } else if (requestedLimit !== null) {
          throw new AgentProtocolError();
        }
      }
      return;
    case "lifestyle_summary":
      parseLifestyleSummaryBlock(block);
      return;
    case "transaction_list":
      requireBlockKeys(block, ["title", "transactions", "total_count"]);
      requireString(block.title, 160);
      requireNonNegativeInteger(block.total_count);
      if (!Array.isArray(block.transactions) || block.transactions.length > 50) {
        throw new AgentProtocolError();
      }
      block.transactions.forEach((item) => {
        const transaction = requireRecord(item);
        requireAllowedKeys(transaction, [
          "public_id", "merchant", "amount_cents", "currency_code", "occurred_on",
          "category", "status", "pending",
        ]);
        requireString(transaction.public_id, 128);
        requireString(transaction.merchant, 255);
        requireInteger(transaction.amount_cents);
        requireCurrencyCode(transaction.currency_code);
        requireNullableDate(transaction.occurred_on);
        requireNullableString(transaction.category, 255);
        requireString(transaction.status, 64);
        requireBoolean(transaction.pending);
      });
      return;
    case "replenishment_summary":
      requireBlockKeys(block, [
        "title", "items", "acquisition_history", "acquisition_history_truncated",
        "total_count", "items_truncated",
      ]);
      requireString(block.title, 160);
      requireNonNegativeInteger(block.total_count);
      requireBoolean(block.items_truncated);
      if (!Array.isArray(block.items) || block.items.length > 50) throw new AgentProtocolError();
      block.items.forEach((item) => {
        const row = requireRecord(item);
        requireAllowedKeys(row, [
          "public_id", "name", "predicted_due_on", "confidence", "confidence_level",
          "evidence_basis", "due_state", "reason", "quantity", "unit", "last_acquired_on",
          "confirmed_acquisition_count",
        ]);
        requireString(row.public_id, 128);
        requireString(row.name, 255);
        requireNullableDate(row.predicted_due_on);
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
        requireNullableDate(row.last_acquired_on);
        requireNonNegativeInteger(row.confirmed_acquisition_count);
      });
      if (!Array.isArray(block.acquisition_history) || block.acquisition_history.length > 20) {
        throw new AgentProtocolError();
      }
      requireBoolean(block.acquisition_history_truncated);
      block.acquisition_history.forEach((item) => {
        const row = requireRecord(item);
        requireAllowedKeys(row, ["acquired_on", "merchant", "quantity", "unit", "evidence_type"]);
        requireDate(row.acquired_on);
        requireNullableString(row.merchant, 255);
        requireNullableNumberRange(row.quantity, 0, Number.MAX_VALUE);
        requireNullableString(row.unit, 64);
        requireOneOf(row.evidence_type, ["manual", "receipt", "transaction", "imported", "correction"]);
      });
      return;
    case "receipt_summary":
      requireBlockKeys(block, [
        "public_id", "merchant", "purchased_at", "ingested_at", "total_cents",
        "currency_code", "status", "transaction_linked", "matched_line_count",
        "ignored_line_count", "unmatched_line_count", "total_line_count", "items",
        "items_truncated",
      ]);
      requireString(block.public_id, 128);
      requireNullableString(block.merchant, 255);
      requireNullableString(block.purchased_at, 128);
      requireNullableString(block.ingested_at, 128);
      requireNullableIntegerValue(block.total_cents);
      requireCurrencyCode(block.currency_code);
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
        requireAllowedKeys(row, [
          "name", "quantity", "unit", "line_total_cents", "match_status",
          "household_item_name", "parent_category", "subcategory", "concept",
          "activity_type", "replenishment_eligibility", "classification_confidence",
          "confirmed_acquisition",
        ]);
        requireString(row.name, 500);
        requireNullableNumberRange(row.quantity, 0, Number.MAX_VALUE);
        requireNullableString(row.unit, 64);
        requireNullableIntegerValue(row.line_total_cents);
        requireOneOf(row.match_status, ["matched", "possible", "unmatched", "ignored"]);
        requireNullableString(row.household_item_name, 255);
        requireNullableString(row.subcategory, 128);
        requireNullableString(row.concept, 255);
        const taxonomyValues = [
          row.parent_category,
          row.activity_type,
          row.replenishment_eligibility,
          row.classification_confidence,
        ];
        const hasTaxonomy = taxonomyValues.some((value) => value !== null && value !== undefined);
        if (hasTaxonomy) {
          if (taxonomyValues.some((value) => value === null || value === undefined)) {
            throw new AgentProtocolError();
          }
          requireOneOf(row.parent_category, CLASSIFICATION_PARENT_CATEGORIES);
          requireOneOf(row.activity_type, CLASSIFICATION_ACTIVITY_TYPES);
          requireOneOf(row.replenishment_eligibility, CLASSIFICATION_REPLENISHMENT);
          requireNullableNumberRange(row.classification_confidence, 0, 1);
        } else if (row.subcategory != null || row.concept != null) {
          throw new AgentProtocolError();
        }
        requireBoolean(row.confirmed_acquisition);
      });
      return;
    case "deal_list":
      requireBlockKeys(block, ["title", "deals", "total_count"]);
      requireString(block.title, 160);
      requireNonNegativeInteger(block.total_count);
      if (!Array.isArray(block.deals) || block.deals.length > 50) throw new AgentProtocolError();
      block.deals.forEach((item) => {
        const row = requireRecord(item);
        requireAllowedKeys(row, [
          "public_id", "merchant", "headline", "expires_at", "score", "category",
          "offer_type", "percent_off", "amount_off_cents", "currency_code",
          "minimum_spend_cents", "promo_code", "trust_status", "saved",
          "relevant_to_need", "relevance_reasons",
        ]);
        requireString(row.public_id, 128);
        requireString(row.merchant, 255);
        requireString(row.headline, 500);
        requireNullableString(row.expires_at, 128);
        requireNullableNumberRange(row.score, 0, 100);
        requireNullableString(row.category, 64);
        requireNullableString(row.offer_type, 32);
        requireNullableNumberRange(row.percent_off, 0, 100);
        requireNullableNonNegativeInteger(row.amount_off_cents);
        if (row.currency_code !== null && row.currency_code !== undefined) {
          requireCurrencyCode(row.currency_code);
        }
        requireNullableNonNegativeInteger(row.minimum_spend_cents);
        requireNullableString(row.promo_code, 128);
        requireOneOf(row.trust_status, ["trusted", "review"]);
        requireBoolean(row.saved);
        requireBoolean(row.relevant_to_need);
        requireStringArray(row.relevance_reasons, 5, 160);
      });
      return;
    case "errand_summary":
      requireBlockKeys(block, ["title", "errands", "total_count", "errands_truncated", "plan"]);
      requireString(block.title, 160);
      requireNonNegativeInteger(block.total_count);
      requireBoolean(block.errands_truncated);
      if (!Array.isArray(block.errands) || block.errands.length > 50) throw new AgentProtocolError();
      block.errands.forEach(parseErrand);
      if (block.plan !== null && block.plan !== undefined) parseErrandPlan(block.plan);
      return;
    case "integration_status":
      requireBlockKeys(block, ["title", "integrations"]);
      requireString(block.title, 160);
      if (!Array.isArray(block.integrations) || block.integrations.length > 25) {
        throw new AgentProtocolError();
      }
      block.integrations.forEach((item) => {
        const row = requireRecord(item);
        requireAllowedKeys(row, ["provider", "scope", "status", "message", "last_successful_sync_at"]);
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
    case "classification_activity_summary":
      parseClassificationActivityBlock(block);
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
      requireBlockKeys(block, ["code", "title", "message", "retryable"]);
      requireCode(block.code, 100);
      requireString(block.title, 160);
      requireString(block.message, 1_000);
      requireBoolean(block.retryable);
      return;
    case "empty":
      requireBlockKeys(block, ["title", "message", "suggested_navigation"]);
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
    "post_itemized_receipt_split",
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

export function parseAgentReviewSession(value: unknown): AgentReviewSessionOut {
  const session = requireRecord(value);
  requireAllowedKeys(session, [
    "public_id",
    "conversation_public_id",
    "progress",
    "current",
  ]);
  requireString(session.public_id, 64);
  requireString(session.conversation_public_id, 64);

  const progress = requireRecord(session.progress);
  requireAllowedKeys(progress, [
    "status",
    "reviewed",
    "personal",
    "split",
    "skipped",
    "stale",
    "remaining",
    "total_candidates",
  ]);
  requireOneOf(progress.status, ["active", "completed", "cancelled"]);
  for (const key of ["reviewed", "personal", "split", "skipped", "stale", "remaining", "total_candidates"]) {
    requireNonNegativeInteger(progress[key]);
  }

  if (session.current !== null) {
    const current = requireRecord(session.current);
    requireAllowedKeys(current, [
      "review_item_public_id",
      "kind",
      "transaction",
      "receipt",
      "recommended_participant_names",
      "recommended_group_name",
      "recommendation_label",
      "available_actions",
    ]);
    requireString(current.review_item_public_id, 64);
    requireString(current.kind, 40);
    if (current.transaction !== null) {
      const transaction = requireRecord(current.transaction);
      requireAllowedKeys(transaction, [
        "id",
        "merchant",
        "amount_cents",
        "currency",
        "occurred_on",
        "pending",
        "institution_name",
      ]);
      requirePositiveInteger(transaction.id);
      requireString(transaction.merchant, 255);
      requireInteger(transaction.amount_cents);
      if (transaction.currency !== null && transaction.currency !== undefined) {
        requireCurrencyCode(transaction.currency);
      }
      requireNullableDate(transaction.occurred_on);
      requireBoolean(transaction.pending);
      requireNullableString(transaction.institution_name, 255);
    }
    if (current.receipt !== null) {
      const receipt = requireRecord(current.receipt);
      requireAllowedKeys(receipt, ["id", "merchant", "total_cents", "currency", "line_count"]);
      requirePositiveInteger(receipt.id);
      requireString(receipt.merchant, 255);
      requireNullableIntegerValue(receipt.total_cents);
      if (receipt.currency !== null && receipt.currency !== undefined) {
        requireCurrencyCode(receipt.currency);
      }
      requireNonNegativeInteger(receipt.line_count);
    }
    requireStringArray(current.recommended_participant_names, 8, 120);
    requireNullableString(current.recommended_group_name, 255);
    requireNullableString(current.recommendation_label, 255);
    requireStringArray(current.available_actions, 8, 64);
  }
  return value as AgentReviewSessionOut;
}

const CLASSIFICATION_VIEWS = [
  "summary",
  "categories",
  "new_categories",
  "matches",
  "staples",
  "cadence",
  "uncertain",
] as const;

const CLASSIFICATION_SECTIONS = [
  "transactions",
  "receipt_items",
  "categories",
  "new_categories",
  "receipt_matches",
  "new_household_items",
  "cadence_updates",
  "uncertain",
] as const;

const CLASSIFICATION_RANGE_VIEWS = [
  ...CLASSIFICATION_VIEWS,
  "staple_candidates",
  "aliases",
] as const;

const CLASSIFICATION_RANGE_SECTIONS = [
  "transactions",
  "receipt_items",
  "categories",
  "new_categories",
  "receipt_matches",
  "new_household_items",
  "staple_candidates",
  "aliases",
  "cadence_updates",
  "uncertain",
] as const;

const CLASSIFICATION_PARENT_CATEGORIES = [
  "food_dining",
  "household_home",
  "lifestyle_shopping",
  "personal_care",
  "health",
  "transportation",
  "travel",
  "entertainment",
  "subscriptions",
  "pets",
  "education_office",
  "services",
  "fees_taxes_discounts",
  "other_uncertain",
] as const;

const CLASSIFICATION_ACTIVITY_TYPES = [
  "grocery", "household_consumable", "routine_consumption", "one_time_purchase",
  "restaurant_meal", "coffee_beverage", "food_delivery", "nightlife", "apparel",
  "electronics", "pharmacy", "personal_care", "beauty", "pet_supply", "automotive",
  "transportation", "travel", "entertainment", "subscription", "education_office",
  "service", "tax", "tip", "discount", "fee", "refund", "non_product", "uncertain",
] as const;

const CLASSIFICATION_REPLENISHMENT = [
  "replenishable",
  "potentially_replenishable",
  "not_replenishable",
  "uncertain",
] as const;

const CLASSIFICATION_CONFIDENCE = ["low", "medium", "high"] as const;
const CLASSIFICATION_STATES = ["provisional", "final", "corrected"] as const;
const CLASSIFICATION_AUTHORITIES = [
  "fallback",
  "model_evidence",
  "provider_evidence",
  "receipt_evidence",
  "deterministic_exact",
  "confirmed_alias",
  "user_correction",
] as const;
const CADENCE_SOURCES = [
  "configured",
  "learning",
  "category_prior",
  "model_prior",
  "observed",
  "quantity_adjusted",
  "adaptive",
] as const;

function parseClassificationActivityBlock(block: Record<string, unknown>): void {
  if (block.block_version === "1.0") {
    parseClassificationActivityV1Block(block);
    return;
  }
  if (block.block_version === "1.1") {
    parseClassificationActivityV11Block(block);
    return;
  }
  throw new AgentProtocolError();
}

function parseClassificationActivityV1Block(block: Record<string, unknown>): void {
  const keys = [
    "type", "block_id", "block_version", "title", "view", "activity_date", "start_date",
    "end_date", "timezone",
    "counts", "transactions", "receipt_items", "categories", "new_categories", "receipt_matches",
    "new_household_items", "staple_candidates", "aliases", "cadence_updates", "uncertain",
    "truncated_sections",
  ];
  requireAllowedKeys(block, keys);
  requireKeys(block, [
    "type", "block_version", "title", "view", "activity_date", "timezone", "counts",
    "transactions", "receipt_items", "categories", "new_categories", "receipt_matches",
    "new_household_items", "cadence_updates", "uncertain", "truncated_sections",
  ]);
  if (block.start_date !== null && block.start_date !== undefined) throw new AgentProtocolError();
  if (block.end_date !== null && block.end_date !== undefined) throw new AgentProtocolError();
  if (block.staple_candidates !== undefined && (
    !Array.isArray(block.staple_candidates) || block.staple_candidates.length > 0
  )) throw new AgentProtocolError();
  if (block.aliases !== undefined && (!Array.isArray(block.aliases) || block.aliases.length > 0)) {
    throw new AgentProtocolError();
  }
  requireString(block.title, 160);
  requireOneOf(block.view, CLASSIFICATION_VIEWS);
  requireDate(block.activity_date);
  if (block.timezone !== "UTC") throw new AgentProtocolError();

  const counts = requireRecord(block.counts);
  requireAllowedKeys(counts, CLASSIFICATION_SECTIONS.slice());
  requireKeys(counts, CLASSIFICATION_SECTIONS.slice());
  CLASSIFICATION_SECTIONS.forEach((section) => requireNonNegativeInteger(counts[section]));

  parseClassificationRows(block.transactions, 20, parseClassificationTransaction);
  parseClassificationRows(block.receipt_items, 20, parseClassificationReceiptItem);
  parseClassificationRows(block.categories, 20, parseClassificationCategory);
  parseClassificationRows(block.new_categories, 20, parseClassificationNewCategory);
  parseClassificationRows(block.receipt_matches, 20, parseClassificationReceiptMatch);
  parseClassificationRows(block.new_household_items, 20, parseClassificationHouseholdItem);
  parseClassificationRows(block.cadence_updates, 20, parseClassificationHouseholdItem);
  parseClassificationRows(block.uncertain, 20, parseClassificationUncertain);

  const rows: Record<(typeof CLASSIFICATION_SECTIONS)[number], unknown[]> = {
    transactions: block.transactions as unknown[],
    receipt_items: block.receipt_items as unknown[],
    categories: block.categories as unknown[],
    new_categories: block.new_categories as unknown[],
    receipt_matches: block.receipt_matches as unknown[],
    new_household_items: block.new_household_items as unknown[],
    cadence_updates: block.cadence_updates as unknown[],
    uncertain: block.uncertain as unknown[],
  };
  const view = block.view as (typeof CLASSIFICATION_VIEWS)[number];
  const allowed = view === "summary"
    ? new Set<string>(CLASSIFICATION_SECTIONS)
    : new Set<string>({
      categories: ["categories"],
      new_categories: ["new_categories"],
      matches: ["receipt_matches"],
      staples: ["new_household_items"],
      cadence: ["cadence_updates"],
      uncertain: ["uncertain"],
    }[view]);
  for (const section of CLASSIFICATION_SECTIONS) {
    if (!allowed.has(section) && rows[section].length) throw new AgentProtocolError();
    if (rows[section].length > (counts[section] as number)) throw new AgentProtocolError();
  }

  if (!Array.isArray(block.truncated_sections) || block.truncated_sections.length > 8) {
    throw new AgentProtocolError();
  }
  const truncated = block.truncated_sections.map((section) => {
    requireOneOf(section, CLASSIFICATION_SECTIONS);
    return section as (typeof CLASSIFICATION_SECTIONS)[number];
  });
  if (new Set(truncated).size !== truncated.length) throw new AgentProtocolError();
  const expected = CLASSIFICATION_SECTIONS.filter(
    (section) => allowed.has(section) && (counts[section] as number) > rows[section].length,
  );
  if (expected.length !== truncated.length || expected.some((section) => !truncated.includes(section))) {
    throw new AgentProtocolError();
  }
}

function parseClassificationActivityV11Block(block: Record<string, unknown>): void {
  const keys = [
    "type", "block_id", "block_version", "title", "view", "activity_date", "start_date",
    "end_date", "timezone", "counts", "transactions", "receipt_items", "categories",
    "new_categories", "receipt_matches", "new_household_items", "staple_candidates",
    "aliases", "cadence_updates", "uncertain", "truncated_sections",
  ];
  requireAllowedKeys(block, keys);
  requireKeys(block, keys.filter((key) => key !== "block_id" && key !== "activity_date"));
  if (block.activity_date !== null && block.activity_date !== undefined) throw new AgentProtocolError();
  requireString(block.title, 160);
  requireOneOf(block.view, CLASSIFICATION_RANGE_VIEWS);
  const startDate = requireDate(block.start_date);
  const endDate = requireDate(block.end_date);
  if (startDate > endDate || dateSpanDays(startDate, endDate) >= 90) {
    throw new AgentProtocolError();
  }
  requireIanaTimezone(block.timezone);

  const counts = requireRecord(block.counts);
  requireAllowedKeys(counts, CLASSIFICATION_RANGE_SECTIONS.slice());
  requireKeys(counts, CLASSIFICATION_RANGE_SECTIONS.slice());
  CLASSIFICATION_RANGE_SECTIONS.forEach((section) => requireNonNegativeInteger(counts[section]));

  parseClassificationRows(block.transactions, 20, parseClassificationTransaction);
  parseClassificationRows(block.receipt_items, 20, parseClassificationReceiptItem);
  parseClassificationRows(block.categories, 20, parseClassificationCategory);
  parseClassificationRows(block.new_categories, 20, parseClassificationNewCategory);
  parseClassificationRows(block.receipt_matches, 20, parseClassificationReceiptMatch);
  parseClassificationRows(block.new_household_items, 20, parseClassificationHouseholdItem);
  parseClassificationRows(block.staple_candidates, 20, parseClassificationStapleCandidate);
  parseClassificationRows(block.aliases, 20, parseClassificationAlias);
  parseClassificationRows(block.cadence_updates, 20, parseClassificationHouseholdItem);
  parseClassificationRows(block.uncertain, 20, parseClassificationUncertain);

  const rows: Record<(typeof CLASSIFICATION_RANGE_SECTIONS)[number], unknown[]> = {
    transactions: block.transactions as unknown[],
    receipt_items: block.receipt_items as unknown[],
    categories: block.categories as unknown[],
    new_categories: block.new_categories as unknown[],
    receipt_matches: block.receipt_matches as unknown[],
    new_household_items: block.new_household_items as unknown[],
    staple_candidates: block.staple_candidates as unknown[],
    aliases: block.aliases as unknown[],
    cadence_updates: block.cadence_updates as unknown[],
    uncertain: block.uncertain as unknown[],
  };
  const view = block.view as (typeof CLASSIFICATION_RANGE_VIEWS)[number];
  const allowed = view === "summary"
    ? new Set<string>(CLASSIFICATION_RANGE_SECTIONS)
    : new Set<string>({
      categories: ["categories"],
      new_categories: ["new_categories"],
      matches: ["receipt_matches"],
      staples: ["new_household_items"],
      staple_candidates: ["staple_candidates"],
      aliases: ["aliases"],
      cadence: ["cadence_updates"],
      uncertain: ["uncertain"],
    }[view]);
  for (const section of CLASSIFICATION_RANGE_SECTIONS) {
    if (!allowed.has(section) && rows[section].length) throw new AgentProtocolError();
    if (rows[section].length > (counts[section] as number)) throw new AgentProtocolError();
  }

  if (!Array.isArray(block.truncated_sections) || block.truncated_sections.length > 10) {
    throw new AgentProtocolError();
  }
  const truncated = block.truncated_sections.map((section) => {
    requireOneOf(section, CLASSIFICATION_RANGE_SECTIONS);
    return section as (typeof CLASSIFICATION_RANGE_SECTIONS)[number];
  });
  if (new Set(truncated).size !== truncated.length) throw new AgentProtocolError();
  const expected = CLASSIFICATION_RANGE_SECTIONS.filter(
    (section) => allowed.has(section) && (counts[section] as number) > rows[section].length,
  );
  if (expected.length !== truncated.length || expected.some((section) => !truncated.includes(section))) {
    throw new AgentProtocolError();
  }
}

function parseClassificationRows(
  value: unknown,
  maximum: number,
  parser: (record: Record<string, unknown>) => void,
): void {
  if (!Array.isArray(value) || value.length > maximum) throw new AgentProtocolError();
  value.forEach((row) => parser(requireRecord(row)));
}

const CLASSIFICATION_DECISION_KEYS = [
  "decision_public_id", "public_id", "source_available", "version", "parent_category",
  "subcategory", "concept", "activity_type", "replenishment_eligibility", "confidence",
  "confidence_band", "authority", "decision_state", "provenance_codes", "auto_finalize_at",
  "finalized_at", "corrects_decision_public_id", "created_subcategory", "created_concept",
  "created_household_item", "applied_at",
] as const;

function parseClassificationDecision(
  row: Record<string, unknown>,
  extraKeys: string[],
): void {
  const keys = [...CLASSIFICATION_DECISION_KEYS, ...extraKeys];
  requireAllowedKeys(row, keys);
  requireKeys(row, keys);
  requireString(row.decision_public_id, 128);
  requireString(row.public_id, 128);
  requireBoolean(row.source_available);
  requirePositiveInteger(row.version);
  requireOneOf(row.parent_category, CLASSIFICATION_PARENT_CATEGORIES);
  requireNullableString(row.subcategory, 128);
  requireNullableString(row.concept, 255);
  requireOneOf(row.activity_type, CLASSIFICATION_ACTIVITY_TYPES);
  requireOneOf(row.replenishment_eligibility, CLASSIFICATION_REPLENISHMENT);
  requireNullableNumberRange(row.confidence, 0, 1);
  if (row.confidence === null || row.confidence === undefined) throw new AgentProtocolError();
  requireOneOf(row.confidence_band, CLASSIFICATION_CONFIDENCE);
  requireOneOf(row.authority, CLASSIFICATION_AUTHORITIES);
  requireOneOf(row.decision_state, CLASSIFICATION_STATES);
  if (!Array.isArray(row.provenance_codes) || row.provenance_codes.length < 1 || row.provenance_codes.length > 16) {
    throw new AgentProtocolError();
  }
  row.provenance_codes.forEach((code) => {
    const value = requireString(code, 64);
    if (!/^[a-z0-9_]+$/.test(value)) throw new AgentProtocolError();
  });
  if (new Set(row.provenance_codes).size !== row.provenance_codes.length) throw new AgentProtocolError();
  requireNullableString(row.auto_finalize_at, 128);
  requireNullableString(row.finalized_at, 128);
  requireNullableString(row.corrects_decision_public_id, 128);
  requireBoolean(row.created_subcategory);
  requireBoolean(row.created_concept);
  requireBoolean(row.created_household_item);
  requireString(row.applied_at, 128);
}

function parseClassificationTransaction(row: Record<string, unknown>): void {
  parseClassificationDecision(row, ["merchant", "occurred_on"]);
  requireString(row.merchant, 255);
  requireNullableString(row.occurred_on, 32);
}

function parseClassificationReceiptItem(row: Record<string, unknown>): void {
  parseClassificationDecision(row, [
    "receipt_public_id", "merchant", "name", "household_item_public_id", "household_item_name",
  ]);
  requireString(row.receipt_public_id, 128);
  requireNullableString(row.merchant, 255);
  requireString(row.name, 500);
  requireNullableString(row.household_item_public_id, 128);
  requireNullableString(row.household_item_name, 255);
  if ((row.household_item_public_id === null) !== (row.household_item_name === null)) {
    throw new AgentProtocolError();
  }
}

function parseClassificationCategory(row: Record<string, unknown>): void {
  requireAllowedKeys(row, ["parent_category", "transaction_count", "receipt_item_count", "total_count"]);
  requireKeys(row, ["parent_category", "transaction_count", "receipt_item_count", "total_count"]);
  requireOneOf(row.parent_category, CLASSIFICATION_PARENT_CATEGORIES);
  requireNonNegativeInteger(row.transaction_count);
  requireNonNegativeInteger(row.receipt_item_count);
  requirePositiveInteger(row.total_count);
  if (row.total_count !== (row.transaction_count as number) + (row.receipt_item_count as number)) {
    throw new AgentProtocolError();
  }
}

function parseClassificationNewCategory(row: Record<string, unknown>): void {
  const keys = [
    "decision_public_id", "parent_category", "subcategory", "source_type", "authority",
    "created_at",
  ];
  requireAllowedKeys(row, keys);
  requireKeys(row, keys);
  requireString(row.decision_public_id, 128);
  requireOneOf(row.parent_category, CLASSIFICATION_PARENT_CATEGORIES);
  requireString(row.subcategory, 128);
  requireOneOf(row.source_type, ["transaction", "receipt_line"]);
  requireOneOf(row.authority, CLASSIFICATION_AUTHORITIES);
  requireString(row.created_at, 128);
}

function parseClassificationReceiptMatch(row: Record<string, unknown>): void {
  const keys = [
    "receipt_public_id", "merchant", "status", "confidence", "transaction_public_id",
    "reason_code", "attempted_at", "matched_at",
  ];
  requireAllowedKeys(row, keys);
  requireKeys(row, keys);
  requireString(row.receipt_public_id, 128);
  requireNullableString(row.merchant, 255);
  requireOneOf(row.status, ["auto_matched", "ambiguous", "no_match"]);
  requireNullableNumberRange(row.confidence, 0, 1);
  if (row.confidence === null || row.confidence === undefined) throw new AgentProtocolError();
  requireNullableString(row.transaction_public_id, 128);
  requireOneOf(row.reason_code, [
    "matched_by_receipt_evidence", "multiple_possible_transactions",
    "no_eligible_transaction", "linked_transaction_unavailable",
  ]);
  requireString(row.attempted_at, 128);
  requireNullableString(row.matched_at, 128);
  if (
    row.status !== "auto_matched" &&
    (row.transaction_public_id !== null || row.matched_at !== null)
  ) throw new AgentProtocolError();
}

function parseClassificationHouseholdItem(row: Record<string, unknown>): void {
  const keys = [
    "created_by_decision_public_id", "public_id", "name", "parent_category",
    "replenishment_eligibility", "classification_confidence", "cadence_source",
    "cadence_days", "cadence_min_days", "cadence_max_days", "cadence_confidence", "activity_at",
  ];
  requireAllowedKeys(row, keys);
  requireKeys(row, keys);
  requireNullableString(row.created_by_decision_public_id, 128);
  requireString(row.public_id, 128);
  requireString(row.name, 255);
  requireOneOf(row.parent_category, CLASSIFICATION_PARENT_CATEGORIES);
  requireOneOf(row.replenishment_eligibility, CLASSIFICATION_REPLENISHMENT);
  requireNullableNumberRange(row.classification_confidence, 0, 1);
  requireOneOf(row.cadence_source, CADENCE_SOURCES);
  requireNullablePositiveInteger(row.cadence_days);
  requireNullablePositiveInteger(row.cadence_min_days);
  requireNullablePositiveInteger(row.cadence_max_days);
  requireNullableNumberRange(row.cadence_confidence, 0, 1);
  if (row.classification_confidence == null || row.cadence_confidence == null) {
    throw new AgentProtocolError();
  }
  if (
    row.cadence_min_days !== null && row.cadence_max_days !== null &&
    (row.cadence_min_days as number) > (row.cadence_max_days as number)
  ) throw new AgentProtocolError();
  requireString(row.activity_at, 128);
}

function parseClassificationStapleCandidate(row: Record<string, unknown>): void {
  const keys = [
    "decision_public_id", "receipt_item_public_id", "receipt_public_id", "source_available",
    "merchant", "name", "parent_category", "subcategory", "concept", "activity_type",
    "replenishment_eligibility", "confidence", "confidence_band", "decision_state",
    "created_household_item", "household_item_public_id", "household_item_name",
    "learning_state", "applied_at",
  ];
  requireAllowedKeys(row, keys);
  requireKeys(row, keys);
  requireString(row.decision_public_id, 128);
  requireString(row.receipt_item_public_id, 128);
  requireString(row.receipt_public_id, 128);
  requireBoolean(row.source_available);
  requireNullableString(row.merchant, 255);
  requireString(row.name, 500);
  requireOneOf(row.parent_category, CLASSIFICATION_PARENT_CATEGORIES);
  requireNullableString(row.subcategory, 128);
  requireNullableString(row.concept, 255);
  requireOneOf(row.activity_type, CLASSIFICATION_ACTIVITY_TYPES);
  requireOneOf(row.replenishment_eligibility, ["replenishable", "potentially_replenishable"]);
  requireNullableNumberRange(row.confidence, 0, 1);
  if (row.confidence === null || row.confidence === undefined) throw new AgentProtocolError();
  requireOneOf(row.confidence_band, CLASSIFICATION_CONFIDENCE);
  requireOneOf(row.decision_state, CLASSIFICATION_STATES);
  requireBoolean(row.created_household_item);
  requireNullableString(row.household_item_public_id, 128);
  requireNullableString(row.household_item_name, 255);
  const linked = row.household_item_public_id !== null && row.household_item_public_id !== undefined;
  const named = row.household_item_name !== null && row.household_item_name !== undefined;
  if (linked !== named) throw new AgentProtocolError();
  requireOneOf(row.learning_state, ["candidate", "learning", "tracked"]);
  if ((row.learning_state === "candidate") === linked) throw new AgentProtocolError();
  requireString(row.applied_at, 128);
}

function parseClassificationAlias(row: Record<string, unknown>): void {
  const keys = [
    "public_id", "concept", "parent_category", "raw_pattern", "merchant", "confidence",
    "authority", "active", "created_at",
  ];
  requireAllowedKeys(row, keys);
  requireKeys(row, keys);
  requireString(row.public_id, 128);
  requireString(row.concept, 255);
  requireOneOf(row.parent_category, CLASSIFICATION_PARENT_CATEGORIES);
  requireString(row.raw_pattern, 500);
  requireNullableString(row.merchant, 255);
  requireNullableNumberRange(row.confidence, 0, 1);
  if (row.confidence === null || row.confidence === undefined) throw new AgentProtocolError();
  requireOneOf(row.authority, CLASSIFICATION_AUTHORITIES);
  requireBoolean(row.active);
  requireString(row.created_at, 128);
}

function parseClassificationUncertain(row: Record<string, unknown>): void {
  const keys = [
    "kind", "public_id", "receipt_public_id", "label", "reasons", "confidence_band",
    "decision_state", "observed_at",
  ];
  requireAllowedKeys(row, keys);
  requireKeys(row, keys);
  requireOneOf(row.kind, ["transaction", "receipt_item", "receipt_match"]);
  requireString(row.public_id, 128);
  requireNullableString(row.receipt_public_id, 128);
  requireString(row.label, 500);
  if (!Array.isArray(row.reasons) || row.reasons.length < 1 || row.reasons.length > 6) {
    throw new AgentProtocolError();
  }
  row.reasons.forEach((reason) => requireOneOf(reason, [
    "low_confidence", "provisional", "other_uncertain", "replenishment_uncertain",
    "ambiguous_receipt_match", "no_receipt_match",
  ]));
  if (new Set(row.reasons).size !== row.reasons.length) throw new AgentProtocolError();
  if (row.confidence_band !== null) requireOneOf(row.confidence_band, CLASSIFICATION_CONFIDENCE);
  if (row.decision_state !== null) requireOneOf(row.decision_state, CLASSIFICATION_STATES);
  requireString(row.observed_at, 128);
  if (
    (row.kind === "receipt_item") !== (row.receipt_public_id !== null) ||
    (row.kind === "receipt_match") !== (row.confidence_band === null && row.decision_state === null)
  ) throw new AgentProtocolError();
}

const ATTENTION_DOMAINS = [
  "spending",
  "lifestyle",
  "transactions",
  "replenishment",
  "receipts",
  "deals",
  "errands",
  "integrations",
  "classification",
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

function requireBlockKeys(record: Record<string, unknown>, payloadKeys: string[]): void {
  requireAllowedKeys(record, ["type", "block_id", ...payloadKeys]);
}

function requireKeys(record: Record<string, unknown>, required: string[]): void {
  if (required.some((key) => !Object.prototype.hasOwnProperty.call(record, key))) {
    throw new AgentProtocolError();
  }
}

function parseLifestyleSummaryBlock(block: Record<string, unknown>): void {
  const keys = [
    "type", "block_id", "block_version", "title", "start_date", "end_date",
    "previous_start_date", "previous_end_date", "activity_type", "currency_code",
    "spend_basis", "total_cents", "credits_cents", "transaction_count", "average_cents",
    "personal_cents", "shared_cents", "unreviewed_cents", "previous_total_cents",
    "previous_transaction_count", "unknown_share_transactions",
    "previous_unknown_share_transactions", "unknown_credit_share_transactions",
    "previous_unknown_credit_share_transactions", "weekday_cents", "weekday_count",
    "weekend_cents", "weekend_count", "uncertain_transaction_count", "observations",
    "activities", "top_merchants",
  ];
  requireAllowedKeys(block, keys);
  requireKeys(block, keys.filter((key) => key !== "block_id"));
  if (block.block_version !== "1.0") throw new AgentProtocolError();
  requireString(block.title, 160);
  const startDate = requireDate(block.start_date);
  const endDate = requireDate(block.end_date);
  if (startDate > endDate) throw new AgentProtocolError();
  const previousStartDate = requireNullableDate(block.previous_start_date);
  const previousEndDate = requireNullableDate(block.previous_end_date);
  if (previousStartDate && previousEndDate && previousStartDate > previousEndDate) {
    throw new AgentProtocolError();
  }
  requireOneOf(block.activity_type, ["all", "coffee", "restaurants", "delivery", "nightlife"]);
  requireCurrencyCode(block.currency_code);
  requireOneOf(block.spend_basis, ["card", "actual_share"]);
  [
    "total_cents", "credits_cents", "transaction_count", "average_cents", "personal_cents",
    "shared_cents", "unreviewed_cents", "unknown_share_transactions",
    "previous_unknown_share_transactions", "unknown_credit_share_transactions",
    "previous_unknown_credit_share_transactions", "weekday_cents", "weekday_count",
    "weekend_cents", "weekend_count", "uncertain_transaction_count",
  ].forEach((key) => requireNonNegativeInteger(block[key]));
  requireNullableInteger(block.previous_total_cents);
  requireNullableInteger(block.previous_transaction_count);
  const comparisonPresent = block.previous_total_cents !== null;
  if (
    comparisonPresent !== (block.previous_transaction_count !== null) ||
    comparisonPresent !== (block.previous_start_date !== null) ||
    comparisonPresent !== (block.previous_end_date !== null)
  ) throw new AgentProtocolError();
  if (block.total_cents !== (block.personal_cents as number) + (block.shared_cents as number) + (block.unreviewed_cents as number)) throw new AgentProtocolError();
  if (block.total_cents !== (block.weekday_cents as number) + (block.weekend_cents as number)) throw new AgentProtocolError();
  if (block.transaction_count !== (block.weekday_count as number) + (block.weekend_count as number)) throw new AgentProtocolError();
  if (block.spend_basis === "card" && (
    block.unknown_share_transactions !== 0 || block.previous_unknown_share_transactions !== 0 ||
    block.unknown_credit_share_transactions !== 0 || block.previous_unknown_credit_share_transactions !== 0
  )) throw new AgentProtocolError();
  requireStringArray(block.observations, 6, 500);
  parseLifestyleBreakdowns(block.activities, 4);
  parseLifestyleBreakdowns(block.top_merchants, 8);
}

function parseLifestyleBreakdowns(value: unknown, maximum: number): void {
  if (!Array.isArray(value) || value.length > maximum) throw new AgentProtocolError();
  value.forEach((item) => {
    const row = requireRecord(item);
    requireAllowedKeys(row, ["name", "amount_cents", "transaction_count", "percentage"]);
    requireString(row.name, 255);
    requireNonNegativeInteger(row.amount_cents);
    requireNonNegativeInteger(row.transaction_count);
    requireFiniteNumber(row.percentage);
    if ((row.percentage as number) < 0 || (row.percentage as number) > 100) throw new AgentProtocolError();
  });
}

function parseBreakdowns(value: unknown): void {
  if (!Array.isArray(value) || value.length > 10) throw new AgentProtocolError();
  value.forEach((item) => {
    const row = requireRecord(item);
    requireAllowedKeys(row, [
      "name", "amount_cents", "transaction_count", "percentage", "previous_amount_cents",
    ]);
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
  requireAllowedKeys(row, [
    "public_id", "title", "status", "priority", "errand_type", "due_on", "place_name",
    "place_resolution_status", "included_in_next_plan", "household_items",
  ]);
  requireString(row.public_id, 128);
  requireString(row.title, 255);
  requireString(row.status, 64);
  requireString(row.priority, 32);
  requireString(row.errand_type, 32);
  requireNullableDate(row.due_on);
  requireNullableString(row.place_name, 255);
  requireString(row.place_resolution_status, 32);
  requireBoolean(row.included_in_next_plan);
  requireStringArray(row.household_items, 20, 255);
}

function parseErrandPlan(value: unknown): void {
  const plan = requireRecord(value);
  requireAllowedKeys(plan, [
    "public_id", "status", "planned_for", "is_stale", "stale_reason",
    "estimated_stop_minutes", "travel_duration_minutes", "distance_meters", "stops",
    "total_stop_count", "stops_truncated",
  ]);
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
    requireAllowedKeys(stop, [
      "order", "place_name", "errands", "errands_truncated", "household_items",
      "household_items_truncated",
    ]);
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
  if (
    typeof value !== "string" ||
    value.length < 1 ||
    value.length > max ||
    value.trim().length < 1
  ) {
    throw new AgentProtocolError();
  }
  return value;
}

function requireCode(value: unknown, max: number): string {
  const code = requireString(value, max);
  if (!/^[a-z][a-z0-9_]*$/.test(code)) throw new AgentProtocolError();
  return code;
}

function requireCurrencyCode(value: unknown): string {
  const code = requireString(value, 8);
  if (!/^[A-Za-z]{3,8}$/.test(code)) throw new AgentProtocolError();
  return code;
}

function requireDate(value: unknown): string {
  const date = requireString(value, 32);
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!match) throw new AgentProtocolError();
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (year < 1) throw new AgentProtocolError();
  const parsed = new Date(0);
  parsed.setUTCHours(0, 0, 0, 0);
  parsed.setUTCFullYear(year, month - 1, day);
  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day
  ) {
    throw new AgentProtocolError();
  }
  return date;
}

function requireNullableDate(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  return requireDate(value);
}

function dateSpanDays(startDate: string, endDate: string): number {
  return (
    Date.parse(`${endDate}T00:00:00Z`) - Date.parse(`${startDate}T00:00:00Z`)
  ) / 86_400_000;
}

function requireIanaTimezone(value: unknown): string {
  const timezone = requireString(value, 64);
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: timezone }).format(0);
  } catch {
    throw new AgentProtocolError();
  }
  return timezone;
}

function requireTerminalRunIdentity(
  event: Record<string, unknown>,
  run: AgentRunOut,
): void {
  if (event.run_public_id !== null && event.run_public_id !== run.public_id) {
    throw new AgentProtocolError();
  }
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

function requireNullablePositiveInteger(value: unknown): void {
  if (value === null || value === undefined) return;
  requirePositiveInteger(value);
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
    "lifestyle",
    "transactions",
    "replenishment",
    "receipts",
    "deals",
    "errands",
    "integrations",
    "classification",
  ]);
}

function requireOneOf(value: unknown, allowed: readonly string[]): void {
  if (typeof value !== "string" || !allowed.includes(value)) throw new AgentProtocolError();
}

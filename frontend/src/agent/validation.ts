import {
  AGENT_SCHEMA_VERSION,
  type AgentConversation,
  type AgentConversationDetail,
  type AgentMessage,
  type AgentRunOut,
  type AgentStreamEvent,
  type AgentStructuredResponse,
} from "./contracts";

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
      parseAgentMessage(record.message);
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
      requireInteger(block.total_cents);
      requireNullableInteger(block.previous_total_cents);
      requireNullableFiniteNumber(block.change_percent);
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
        const navigation = requireRecord(block.suggested_navigation);
        if (navigation.type !== "navigation") throw new AgentProtocolError();
        requireString(navigation.label, 120);
        requireString(navigation.target_surface, 64);
      }
      return;
    default:
      throw new AgentProtocolError("ExpenseOps cannot safely display this response yet.");
  }
}

function parseBreakdowns(value: unknown): void {
  if (!Array.isArray(value) || value.length > 10) throw new AgentProtocolError();
  value.forEach((item) => {
    const row = requireRecord(item);
    requireString(row.name, 255);
    requireInteger(row.amount_cents);
    requireNonNegativeInteger(row.transaction_count);
    requireFiniteNumber(row.percentage);
    if ((row.percentage as number) < 0 || (row.percentage as number) > 100) {
      throw new AgentProtocolError();
    }
    if (row.previous_amount_cents !== null && row.previous_amount_cents !== undefined) {
      requireInteger(row.previous_amount_cents);
    }
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

function requireNullableInteger(value: unknown): void {
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

function requireStringArray(value: unknown, maxItems: number, maxLength: number): void {
  if (!Array.isArray(value) || value.length > maxItems) throw new AgentProtocolError();
  value.forEach((item) => requireString(item, maxLength));
}

function requireToolActivity(value: unknown): void {
  if (value !== "spending" && value !== "transactions") throw new AgentProtocolError();
}

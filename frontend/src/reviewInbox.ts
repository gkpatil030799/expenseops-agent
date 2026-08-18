import type { ReviewInboxPage, ReviewItem } from "./types";

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[], label: string) {
  const expected = new Set(keys);
  if (Object.keys(value).some((key) => !expected.has(key)) || keys.some((key) => !(key in value))) {
    throw new Error(`${label} has an invalid shape`);
  }
}

function stringValue(value: unknown, label: string, max = 500): string {
  if (typeof value !== "string" || !value || value.length > max) throw new Error(`${label} is invalid`);
  return value;
}

function nullableString(value: unknown, label: string, max = 500): string | null {
  return value === null ? null : stringValue(value, label, max);
}

function integer(value: unknown, label: string, minimum = 0): number {
  if (!Number.isInteger(value) || (value as number) < minimum) throw new Error(`${label} is invalid`);
  return value as number;
}

function signedInteger(value: unknown, label: string): number {
  if (!Number.isInteger(value)) throw new Error(`${label} is invalid`);
  return value as number;
}

function booleanValue(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") throw new Error(`${label} is invalid`);
  return value;
}

function enumValue<T extends string>(value: unknown, allowed: readonly T[], label: string): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) throw new Error(`${label} is invalid`);
  return value as T;
}

function nullableIso(value: unknown, label: string): string | null {
  if (value === null) return null;
  const result = stringValue(value, label, 64);
  if (!Number.isFinite(Date.parse(result))) throw new Error(`${label} is invalid`);
  return result;
}

function nullableDate(value: unknown, label: string): string | null {
  if (value === null) return null;
  const result = stringValue(value, label, 10);
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(result);
  if (!match) throw new Error(`${label} is invalid`);
  const parsed = new Date(`${result}T00:00:00Z`);
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== result) {
    throw new Error(`${label} is invalid`);
  }
  return result;
}

function parseItem(value: unknown, index: number): ReviewItem {
  const item = record(value, `items[${index}]`);
  exactKeys(item, [
    "public_id", "kind", "state", "unread", "seen_at", "created_at", "updated_at",
    "available_actions", "transaction", "receipt", "recommendation",
  ], `items[${index}]`);
  const publicId = stringValue(item.public_id, "public_id", 36);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(publicId)) {
    throw new Error("public_id is invalid");
  }
  const actions = item.available_actions;
  if (!Array.isArray(actions) || actions.length > 6) throw new Error("available_actions is invalid");
  const availableActions = actions.map((action) => enumValue(action, [
    "personal", "recommended_split", "customize", "itemized_split", "open_receipt",
    "open_receipt_match",
  ] as const, "available_action"));

  let transaction: ReviewItem["transaction"] = null;
  if (item.transaction !== null) {
    const tx = record(item.transaction, "transaction");
    exactKeys(tx, ["id", "merchant_name", "name", "amount_cents", "currency", "date", "pending", "status", "institution_name"], "transaction");
    transaction = {
      id: integer(tx.id, "transaction.id", 1),
      merchant_name: nullableString(tx.merchant_name, "transaction.merchant_name", 255),
      name: stringValue(tx.name, "transaction.name", 255),
      amount_cents: signedInteger(tx.amount_cents, "transaction.amount_cents"),
      currency: stringValue(tx.currency, "transaction.currency", 8),
      date: nullableDate(tx.date, "transaction.date"),
      pending: booleanValue(tx.pending, "transaction.pending"),
      status: stringValue(tx.status, "transaction.status", 32),
      institution_name: nullableString(tx.institution_name, "transaction.institution_name", 255),
    };
  }

  let receipt: ReviewItem["receipt"] = null;
  if (item.receipt !== null) {
    const row = record(item.receipt, "receipt");
    exactKeys(row, ["id", "merchant_name", "total_cents", "currency", "purchased_at", "parse_status", "transaction_match_status", "transaction_id", "line_count"], "receipt");
    receipt = {
      id: integer(row.id, "receipt.id", 1),
      merchant_name: nullableString(row.merchant_name, "receipt.merchant_name", 255),
      total_cents: row.total_cents === null ? null : integer(row.total_cents, "receipt.total_cents", 0),
      currency: stringValue(row.currency, "receipt.currency", 8),
      purchased_at: nullableIso(row.purchased_at, "receipt.purchased_at"),
      parse_status: stringValue(row.parse_status, "receipt.parse_status", 32),
      transaction_match_status: stringValue(row.transaction_match_status, "receipt.transaction_match_status", 32),
      transaction_id: row.transaction_id === null ? null : integer(row.transaction_id, "receipt.transaction_id", 1),
      line_count: integer(row.line_count, "receipt.line_count", 0),
    };
  }
  if ((transaction === null) === (receipt === null)) throw new Error("review item must have exactly one source summary");

  let recommendation: ReviewItem["recommendation"] = null;
  if (item.recommendation !== null) {
    const row = record(item.recommendation, "recommendation");
    exactKeys(row, ["suggestion", "reason", "memory_id", "participant_names", "group_name", "split_mode"], "recommendation");
    if (!Array.isArray(row.participant_names) || row.participant_names.length > 8) throw new Error("participant_names is invalid");
    recommendation = {
      suggestion: enumValue(row.suggestion, ["likely_personal", "likely_shared"] as const, "suggestion"),
      reason: stringValue(row.reason, "reason", 500),
      memory_id: integer(row.memory_id, "memory_id", 1),
      participant_names: row.participant_names.map((name) => stringValue(name, "participant_name", 120)),
      group_name: nullableString(row.group_name, "group_name", 255),
      split_mode: nullableString(row.split_mode, "split_mode", 64),
    };
  }
  if (recommendation && !transaction) throw new Error("receipt item cannot include a transaction recommendation");

  return {
    public_id: publicId,
    kind: enumValue(item.kind, ["transaction_review", "itemized_split_ready", "receipt_match_needed", "financial_reconciliation"] as const, "kind"),
    state: enumValue(item.state, ["open", "resolved", "stale"] as const, "state"),
    unread: booleanValue(item.unread, "unread"),
    seen_at: nullableIso(item.seen_at, "seen_at"),
    created_at: nullableIso(item.created_at, "created_at")!,
    updated_at: nullableIso(item.updated_at, "updated_at")!,
    available_actions: availableActions,
    transaction,
    receipt,
    recommendation,
  };
}

export function parseReviewInbox(value: unknown): ReviewInboxPage {
  const page = record(value, "review inbox");
  exactKeys(page, ["items", "total_open", "unread_count", "limit", "offset"], "review inbox");
  if (!Array.isArray(page.items) || page.items.length > 100) throw new Error("review items are invalid");
  const items = page.items.map(parseItem);
  const totalOpen = integer(page.total_open, "total_open", 0);
  const unreadCount = integer(page.unread_count, "unread_count", 0);
  if (unreadCount > totalOpen || items.some((item) => item.state !== "open")) {
    throw new Error("review inbox counts are inconsistent");
  }
  return {
    items,
    total_open: totalOpen,
    unread_count: unreadCount,
    limit: integer(page.limit, "limit", 1),
    offset: integer(page.offset, "offset", 0),
  };
}

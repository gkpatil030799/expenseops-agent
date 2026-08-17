import type { AgentStructuredResponse } from "@/agent/contracts";
import { parseAgentStructuredResponse } from "@/agent/validation";

export const ATTENTION_CATEGORIES = [
  "transactions",
  "receipts",
  "integrations",
  "replenishment",
  "deals",
  "errands",
] as const;

export type AttentionCategory = (typeof ATTENTION_CATEGORIES)[number];

export type AttentionPreference = {
  enabled: boolean;
  categories: AttentionCategory[];
  in_app_enabled: boolean;
  telegram_enabled: boolean;
  delivery_mode: "immediate" | "digest";
  quiet_start_hour: number;
  quiet_end_hour: number;
  timezone: string;
  max_alerts_per_day: number;
  cooldown_minutes: number;
};

export type AttentionCenter = {
  enabled: boolean;
  generated_at: string | null;
  response: AgentStructuredResponse | null;
  preferences: AttentionPreference;
};

const PREFERENCE_KEYS = new Set([
  "enabled",
  "categories",
  "in_app_enabled",
  "telegram_enabled",
  "delivery_mode",
  "quiet_start_hour",
  "quiet_end_hour",
  "timezone",
  "max_alerts_per_day",
  "cooldown_minutes",
]);

export function parseAttentionPreference(value: unknown): AttentionPreference {
  const record = requireRecord(value);
  requireExactKeys(record, PREFERENCE_KEYS);
  requireBoolean(record.enabled);
  requireBoolean(record.in_app_enabled);
  requireBoolean(record.telegram_enabled);
  if (record.delivery_mode !== "immediate" && record.delivery_mode !== "digest") fail();
  requireInteger(record.quiet_start_hour, 0, 23);
  requireInteger(record.quiet_end_hour, 0, 23);
  requireString(record.timezone, 1, 64);
  requireInteger(record.max_alerts_per_day, 1, 10);
  requireInteger(record.cooldown_minutes, 15, 1_440);
  if (!Array.isArray(record.categories) || record.categories.length < 1) fail();
  const categories = record.categories as unknown[];
  if (
    categories.some(
      (category) =>
        typeof category !== "string" ||
        !ATTENTION_CATEGORIES.includes(category as AttentionCategory),
    ) ||
    new Set(categories).size !== categories.length
  ) {
    fail();
  }
  return value as AttentionPreference;
}

export function parseAttentionCenter(value: unknown): AttentionCenter {
  const record = requireRecord(value);
  requireExactKeys(record, new Set(["enabled", "generated_at", "response", "preferences"]));
  requireBoolean(record.enabled);
  if (record.generated_at !== null) requireString(record.generated_at, 1, 128);
  parseAttentionPreference(record.preferences);
  if (record.response !== null) parseAgentStructuredResponse(record.response);
  if (record.enabled !== (record.response !== null)) fail();
  return value as AttentionCenter;
}

function requireRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail();
  return value as Record<string, unknown>;
}

function requireExactKeys(record: Record<string, unknown>, keys: Set<string>): void {
  if (Object.keys(record).length !== keys.size || Object.keys(record).some((key) => !keys.has(key))) {
    fail();
  }
}

function requireBoolean(value: unknown): asserts value is boolean {
  if (typeof value !== "boolean") fail();
}

function requireInteger(value: unknown, minimum: number, maximum: number): void {
  if (!Number.isInteger(value) || (value as number) < minimum || (value as number) > maximum) fail();
}

function requireString(value: unknown, minimum: number, maximum: number): asserts value is string {
  if (typeof value !== "string" || value.length < minimum || value.length > maximum) fail();
}

function fail(): never {
  throw new Error("ExpenseOps received an invalid Attention Center response.");
}

import { describe, expect, it, vi } from "vitest";

import { parseAttentionCenter, parseAttentionPreference } from "./attention";

const preferences = {
  enabled: true,
  categories: ["transactions", "receipts", "replenishment"],
  in_app_enabled: true,
  telegram_enabled: false,
  delivery_mode: "digest",
  quiet_start_hour: 22,
  quiet_end_hour: 7,
  timezone: "America/Phoenix",
  max_alerts_per_day: 3,
  cooldown_minutes: 240,
};

describe("Attention Center contracts", () => {
  it("accepts one strict canonical response", () => {
    const value = {
      enabled: true,
      generated_at: "2026-08-17T12:00:00Z",
      preferences,
      response: {
        schema_version: "1.0",
        blocks: [
          {
            type: "attention_summary",
            block_version: "1.0",
            title: "Needs attention",
            status: "complete",
            checked_domains: ["transactions", "replenishment", "receipts"],
            unavailable_domains: [],
            items: [],
            items_truncated: false,
          },
        ],
      },
    };

    expect(parseAttentionCenter(value)).toBe(value);
  });

  it("rejects unknown fields, duplicate categories, and mismatched enabled state", () => {
    expect(() => parseAttentionPreference({ ...preferences, secret: "no" })).toThrow();
    expect(() =>
      parseAttentionPreference({ ...preferences, categories: ["receipts", "receipts"] }),
    ).toThrow();
    expect(() =>
      parseAttentionCenter({
        enabled: true,
        generated_at: null,
        response: null,
        preferences,
      }),
    ).toThrow();
  });

  it("has no browser persistence side effects", () => {
    const localSpy = vi.spyOn(Storage.prototype, "setItem");
    parseAttentionPreference(preferences);
    expect(localSpy).not.toHaveBeenCalled();
    localSpy.mockRestore();
  });
});

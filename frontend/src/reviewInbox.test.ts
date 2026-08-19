import { describe, expect, it } from "vitest";

import { parseReviewInbox } from "./reviewInbox";

const valid = {
  items: [{
    public_id: "11111111-1111-4111-a111-111111111111",
    kind: "transaction_review",
    state: "open",
    unread: true,
    seen_at: null,
    created_at: "2026-08-18T12:00:00Z",
    updated_at: "2026-08-18T12:00:00Z",
    available_actions: ["personal", "recommended_split", "customize"],
    transaction: {
      id: 1,
      merchant_name: "Cafe",
      name: "CAFE",
      amount_cents: 1200,
      currency: "USD",
      date: "2026-08-18",
      pending: false,
      status: "ask_user",
      institution_name: "Bank",
    },
    receipt: null,
    recommendation: null,
  }],
  total_open: 1,
  unread_count: 1,
  limit: 100,
  offset: 0,
};

describe("parseReviewInbox", () => {
  it("accepts the strict canonical page", () => {
    expect(parseReviewInbox(valid).items[0].transaction?.merchant_name).toBe("Cafe");
  });

  it.each([
    { ...valid, workspace_id: 9 },
    { ...valid, unread_count: 2 },
    { ...valid, items: [{ ...valid.items[0], public_id: "not-a-uuid" }] },
    { ...valid, items: [{ ...valid.items[0], state: "resolved" }] },
    { ...valid, items: [{ ...valid.items[0], transaction: null }] },
    { ...valid, items: [{ ...valid.items[0], action_codes_json: [] }] },
  ])("rejects malformed or expanded payload %#", (payload) => {
    expect(() => parseReviewInbox(payload)).toThrow();
  });
});

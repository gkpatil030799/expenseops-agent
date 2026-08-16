import { describe, expect, it } from "vitest";

import {
  AgentProtocolError,
  parseAgentStreamEvent,
  parseAgentStructuredResponse,
} from "./validation";

const streamBase = {
  schema_version: "1.0",
  sequence: 0,
  run_public_id: "run-public-1",
} as const;

function supportedResponse() {
  return {
    schema_version: "1.0",
    blocks: [
      {
        type: "text",
        text: "Here is your grounded answer.",
      },
      {
        type: "spending_summary",
        title: "Spending summary",
        start_date: "2026-07-01",
        end_date: "2026-07-31",
        currency_code: "USD",
        total_cents: 41_200,
        previous_total_cents: 32_600,
        change_percent: 26.4,
        highlights: ["Personal: $250.00", "Shared: $162.00"],
        top_categories: [
          {
            name: "Food & Dining",
            amount_cents: 41_200,
            transaction_count: 8,
            percentage: 100,
            previous_amount_cents: 32_600,
          },
        ],
        top_merchants: [],
      },
      {
        type: "transaction_list",
        title: "Starbucks transactions",
        transactions: [
          {
            public_id: "transaction-public-1",
            merchant: "Starbucks",
            amount_cents: 875,
            currency_code: "USD",
            occurred_on: "2026-07-18",
            category: "Coffee",
            status: "personal",
            pending: false,
          },
        ],
        total_count: 1,
      },
      {
        type: "replenishment_summary",
        title: "Likely due",
        items: [
          {
            public_id: "household-1",
            name: "Laundry detergent",
            predicted_due_on: "2026-08-17",
            confidence: null,
            confidence_level: "medium",
            evidence_basis: "purchase_pattern",
            due_state: "probably_due",
            reason: "Based on confirmed purchase history.",
            quantity: "1",
            unit: "bottle",
            last_acquired_on: "2026-07-12",
            confirmed_acquisition_count: 3,
          },
        ],
        acquisition_history: [],
        acquisition_history_truncated: false,
        total_count: 1,
        items_truncated: false,
      },
      {
        type: "receipt_summary",
        public_id: "receipt-1",
        merchant: "Costco",
        purchased_at: "2026-08-12T15:00:00Z",
        ingested_at: "2026-08-12T16:00:00Z",
        total_cents: 9_438,
        currency_code: "USD",
        status: "needs_review",
        transaction_linked: true,
        matched_line_count: 1,
        ignored_line_count: 0,
        unmatched_line_count: 1,
        total_line_count: 2,
        items: [
          {
            name: "Tide Pods",
            quantity: 1,
            unit: "pack",
            line_total_cents: 2_499,
            match_status: "matched",
            household_item_name: "Laundry detergent",
            confirmed_acquisition: true,
          },
        ],
        items_truncated: true,
      },
      {
        type: "deal_list",
        title: "Current deals",
        deals: [
          {
            public_id: "deal-1",
            merchant: "Target",
            headline: "Save 15% on detergent",
            expires_at: "2026-08-20T23:59:00Z",
            score: 82,
            category: "Household",
            offer_type: "percent_off",
            percent_off: 15,
            amount_off_cents: null,
            currency_code: "USD",
            minimum_spend_cents: null,
            promo_code: "CLEAN15",
            trust_status: "trusted",
            saved: false,
            relevant_to_need: true,
            relevance_reasons: ["Relevant to a household item due soon."],
          },
        ],
        total_count: 1,
      },
      {
        type: "errand_summary",
        title: "Errands and stored plan",
        errands: [
          {
            public_id: "errand-1",
            title: "Buy detergent",
            status: "open",
            priority: "normal",
            errand_type: "purchase",
            due_on: "2026-08-17",
            place_name: "Target",
            place_resolution_status: "resolved",
            included_in_next_plan: true,
            household_items: ["Laundry detergent"],
          },
        ],
        total_count: 1,
        errands_truncated: false,
        plan: {
          public_id: "plan-1",
          status: "planned",
          planned_for: "2026-08-16T17:00:00Z",
          is_stale: false,
          stale_reason: null,
          estimated_stop_minutes: 15,
          travel_duration_minutes: 10,
          distance_meters: 2400,
          stops: [{
            order: 1,
            place_name: "Target",
            errands: ["Buy detergent"],
            errands_truncated: false,
            household_items: ["Laundry detergent"],
            household_items_truncated: false,
          }],
          total_stop_count: 1,
          stops_truncated: false,
        },
      },
      {
        type: "integration_status",
        title: "Integration status",
        integrations: [
          {
            provider: "gmail",
            scope: "workspace",
            status: "connected",
            message: "The workspace Gmail connection is connected.",
            last_successful_sync_at: "2026-08-15T12:00:00Z",
          },
        ],
      },
      {
        type: "error",
        code: "agent_provider_failed",
        title: "ExpenseOps could not retrieve that data",
        message: "Please retry.",
        retryable: true,
      },
      {
        type: "empty",
        title: "No transactions found",
        message: "Try a different date range.",
        suggested_navigation: null,
      },
    ],
  };
}

describe("Agent structured-response validation", () => {
  it("accepts the bounded read-only renderer block types", () => {
    const response = supportedResponse();

    expect(parseAgentStructuredResponse(response)).toBe(response);
    expect(response.blocks.map((block) => block.type)).toEqual([
      "text",
      "spending_summary",
      "transaction_list",
      "replenishment_summary",
      "receipt_summary",
      "deal_list",
      "errand_summary",
      "integration_status",
      "error",
      "empty",
    ]);
  });

  it("accepts original v1 domain bounds and a legacy integration without scope", () => {
    const replenishmentItem = {
      public_id: "legacy-item",
      name: "Legacy item",
      predicted_due_on: null,
      confidence: null,
      confidence_level: "insufficient",
      evidence_basis: "insufficient_history",
      due_state: "learning",
      reason: null,
      quantity: null,
      unit: null,
      last_acquired_on: null,
      confirmed_acquisition_count: 0,
    };
    const receiptLine = {
      name: "Legacy line",
      quantity: null,
      unit: null,
      line_total_cents: null,
      match_status: "unmatched",
      household_item_name: null,
      confirmed_acquisition: false,
    };
    const errand = {
      public_id: "legacy-errand",
      title: "Legacy errand",
      status: "open",
      priority: "normal",
      errand_type: "other",
      due_on: null,
      place_name: null,
      place_resolution_status: "unresolved",
      included_in_next_plan: false,
      household_items: [],
    };
    const response = {
      schema_version: "1.0",
      blocks: [
        {
          type: "replenishment_summary",
          title: "Legacy replenishment",
          items: Array.from({ length: 21 }, () => replenishmentItem),
          acquisition_history: [],
          acquisition_history_truncated: false,
          total_count: 21,
          items_truncated: false,
        },
        {
          type: "receipt_summary",
          public_id: "legacy-receipt",
          merchant: null,
          purchased_at: null,
          ingested_at: null,
          total_cents: null,
          currency_code: "USD",
          status: "confirmed",
          transaction_linked: false,
          matched_line_count: 0,
          ignored_line_count: 0,
          unmatched_line_count: 26,
          total_line_count: 26,
          items: Array.from({ length: 26 }, () => receiptLine),
          items_truncated: false,
        },
        {
          type: "errand_summary",
          title: "Legacy errands",
          errands: Array.from({ length: 26 }, () => errand),
          total_count: 26,
          errands_truncated: false,
          plan: null,
        },
        {
          type: "integration_status",
          title: "Legacy integration",
          integrations: [
            {
              provider: "legacy_provider",
              status: "connected",
              message: null,
              last_successful_sync_at: null,
            },
          ],
        },
      ],
    };

    expect(parseAgentStructuredResponse(response)).toBe(response);
  });

  it.each(["navigation", "action_confirmation", "unexpected_block"])(
    "fails closed for unsupported %s blocks",
    (type) => {
      expect(() =>
        parseAgentStructuredResponse({
          schema_version: "1.0",
          blocks: [{ type, label: "Do not execute this" }],
        }),
      ).toThrow(AgentProtocolError);
    },
  );

  it("rejects unknown schema versions and unsafe collection bounds", () => {
    expect(() =>
      parseAgentStructuredResponse({ ...supportedResponse(), schema_version: "2.0" }),
    ).toThrow(/version/i);

    expect(() =>
      parseAgentStructuredResponse({ schema_version: "1.0", blocks: [] }),
    ).toThrow(AgentProtocolError);

    expect(() =>
      parseAgentStructuredResponse({
        schema_version: "1.0",
        blocks: [
          {
            type: "transaction_list",
            title: "Too many rows",
            total_count: 51,
            transactions: Array.from({ length: 51 }, (_, index) => ({
              public_id: `transaction-${index}`,
              merchant: "Merchant",
              amount_cents: 100,
              currency_code: "USD",
              occurred_on: "2026-07-18",
              category: null,
              status: "personal",
              pending: false,
            })),
          },
        ],
      }),
    ).toThrow(AgentProtocolError);
  });
});

describe("Agent semantic-event validation", () => {
  it("accepts a structured event only after validating its nested renderer payload", () => {
    const event = {
      ...streamBase,
      type: "structured_response",
      response: supportedResponse(),
    };

    expect(parseAgentStreamEvent(event)).toBe(event);
  });

  it("rejects unknown event types and schema versions", () => {
    expect(() =>
      parseAgentStreamEvent({ ...streamBase, type: "provider_raw_delta", data: "secret" }),
    ).toThrow(/unknown Agent stream event/i);

    expect(() =>
      parseAgentStreamEvent({
        ...streamBase,
        schema_version: "9.9",
        type: "run_started",
        resumed: false,
      }),
    ).toThrow(/version/i);
  });

  it("rejects malformed event fields before they reach UI state", () => {
    expect(() =>
      parseAgentStreamEvent({
        ...streamBase,
        type: "assistant_delta",
        sequence: -1,
        delta: "hello",
      }),
    ).toThrow(AgentProtocolError);

    expect(() =>
      parseAgentStreamEvent({
        ...streamBase,
        type: "tool_started",
        activity: "execute_sql",
        message: "Leaking an internal tool",
      }),
    ).toThrow(AgentProtocolError);

    expect(() =>
      parseAgentStreamEvent({
        ...streamBase,
        type: "assistant_delta",
        delta: "x".repeat(1_001),
      }),
    ).toThrow(AgentProtocolError);
  });

  it.each(["replenishment", "receipts", "deals", "errands", "integrations"])(
    "accepts the safe %s progress activity",
    (activity) => {
      const event = {
        ...streamBase,
        type: "tool_started",
        activity,
        message: "Checking ExpenseOps data…",
      };
      expect(parseAgentStreamEvent(event)).toBe(event);
    },
  );
});

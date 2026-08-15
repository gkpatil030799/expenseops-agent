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
  it("accepts only the five read-only renderer block types", () => {
    const response = supportedResponse();

    expect(parseAgentStructuredResponse(response)).toBe(response);
    expect(response.blocks.map((block) => block.type)).toEqual([
      "text",
      "spending_summary",
      "transaction_list",
      "error",
      "empty",
    ]);
  });

  it.each(["navigation", "action_confirmation", "deal_list", "unexpected_block"])(
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
});

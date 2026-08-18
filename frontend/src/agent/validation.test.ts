import { describe, expect, it } from "vitest";

import {
  AgentProtocolError,
  parseAgentActionConfirmation,
  parseClassificationActivityOut,
  parseAgentConversationDetail,
  parseAgentFeedback,
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
        spend_basis: "card",
        total_cents: 41_200,
        previous_total_cents: 32_600,
        credits_cents: 8_000,
        previous_credits_cents: 1_000,
        unknown_share_transactions: 0,
        previous_unknown_share_transactions: 0,
        unknown_credit_share_transactions: 0,
        previous_unknown_credit_share_transactions: 0,
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
        type: "attention_summary",
        block_version: "1.0",
        title: "Today needs attention",
        status: "partial",
        checked_domains: ["transactions", "replenishment", "receipts"],
        unavailable_domains: ["deals"],
        items: [
          {
            priority: "action_required",
            domain: "transactions",
            title: "Expense reviews",
            detail: "Two transactions still need review.",
            count: 2,
            navigation: {
              type: "navigation",
              label: "View expenses",
              target_surface: "expense_review",
            },
          },
          {
            priority: "action_required",
            domain: "receipts",
            title: "Receipt review",
            detail: null,
            count: 1,
            navigation: null,
          },
          {
            priority: "time_sensitive",
            domain: "replenishment",
            title: "Household item likely due",
            count: 1,
          },
        ],
        items_truncated: false,
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

function attentionSummary(overrides: Record<string, unknown> = {}) {
  return {
    type: "attention_summary",
    block_version: "1.0",
    title: "Today needs attention",
    status: "partial",
    checked_domains: ["transactions", "replenishment"],
    unavailable_domains: ["deals"],
    items: [
      {
        priority: "action_required",
        domain: "transactions",
        title: "Expense reviews",
        detail: "Two transactions still need review.",
        count: 2,
        navigation: {
          type: "navigation",
          label: "View expenses",
          target_surface: "expense_review",
        },
      },
      {
        priority: "time_sensitive",
        domain: "replenishment",
        title: "Household item likely due",
        detail: null,
        count: 1,
        navigation: null,
      },
    ],
    items_truncated: false,
    ...overrides,
  };
}

function classificationActivity(overrides: Record<string, unknown> = {}) {
  return {
    type: "classification_activity_summary",
    block_version: "1.0",
    title: "Categories used",
    view: "categories",
    activity_date: "2026-08-17",
    timezone: "UTC",
    counts: {
      transactions: 1,
      receipt_items: 1,
      categories: 1,
      new_categories: 0,
      receipt_matches: 0,
      new_household_items: 0,
      cadence_updates: 0,
      uncertain: 0,
    },
    transactions: [],
    receipt_items: [],
    categories: [{
      parent_category: "food_dining",
      transaction_count: 1,
      receipt_item_count: 1,
      total_count: 2,
    }],
    new_categories: [],
    receipt_matches: [],
    new_household_items: [],
    cadence_updates: [],
    uncertain: [],
    truncated_sections: [],
    ...overrides,
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
      "attention_summary",
      "error",
      "empty",
    ]);
  });

  it.each([
    "total_cents",
    "previous_total_cents",
    "credits_cents",
    "previous_credits_cents",
    "unknown_share_transactions",
    "previous_unknown_share_transactions",
    "unknown_credit_share_transactions",
    "previous_unknown_credit_share_transactions",
  ])(
    "rejects a negative spending-summary %s",
    (field) => {
      const response = structuredClone(supportedResponse()) as {
        blocks: Record<string, unknown>[];
      };
      response.blocks[1][field] = -1;

      expect(() => parseAgentStructuredResponse(response)).toThrow(AgentProtocolError);
    },
  );

  it.each(["amount_cents", "previous_amount_cents"])(
    "rejects a negative spending-breakdown %s",
    (field) => {
      const response = structuredClone(supportedResponse()) as {
        blocks: Record<string, unknown>[];
      };
      const categories = response.blocks[1].top_categories;
      if (!Array.isArray(categories)) throw new Error("spending categories fixture is missing");
      (categories[0] as Record<string, unknown>)[field] = -1;

      expect(() => parseAgentStructuredResponse(response)).toThrow(AgentProtocolError);
    },
  );

  it("requires the explicit positive-magnitude credits field on new spending summaries", () => {
    const response = structuredClone(supportedResponse()) as {
      blocks: Record<string, unknown>[];
    };
    delete response.blocks[1].credits_cents;

    expect(() => parseAgentStructuredResponse(response)).toThrow(AgentProtocolError);
  });

  it("accepts actual-share spending with explicit current and prior unknown-credit counts", () => {
    const response = structuredClone(supportedResponse()) as {
      blocks: Record<string, unknown>[];
    };
    response.blocks[1].spend_basis = "actual_share";
    response.blocks[1].unknown_share_transactions = 0;
    response.blocks[1].previous_unknown_share_transactions = 3;
    response.blocks[1].unknown_credit_share_transactions = 1;
    response.blocks[1].previous_unknown_credit_share_transactions = 2;
    response.blocks[1].change_percent = null;

    expect(parseAgentStructuredResponse(response)).toBe(response);
  });

  it.each([undefined, "net"])("rejects spending summaries with spend_basis %s", (spendBasis) => {
    const response = structuredClone(supportedResponse()) as {
      blocks: Record<string, unknown>[];
    };
    response.blocks[1].spend_basis = spendBasis;

    expect(() => parseAgentStructuredResponse(response)).toThrow(AgentProtocolError);
  });

  it("rejects unknown shared-credit counts on card-basis summaries", () => {
    const response = structuredClone(supportedResponse()) as {
      blocks: Record<string, unknown>[];
    };
    response.blocks[1].unknown_credit_share_transactions = 1;

    expect(() => parseAgentStructuredResponse(response)).toThrow(AgentProtocolError);
  });

  it("rejects an exact percentage when an actual-share comparison omits purchases", () => {
    const response = structuredClone(supportedResponse()) as {
      blocks: Record<string, unknown>[];
    };
    response.blocks[1].spend_basis = "actual_share";
    response.blocks[1].previous_unknown_share_transactions = 1;

    expect(() => parseAgentStructuredResponse(response)).toThrow(AgentProtocolError);
  });

  it("accepts the server-adapted historical spending response without old financial values", () => {
    const response = {
      schema_version: "1.0",
      blocks: [
        {
          type: "text",
          text: "This saved spending answer used retired net-spend semantics and is not shown as current financial truth.",
        },
        {
          type: "empty",
          title: "Recalculate this spending answer",
          message: "Ask the question again to calculate eligible purchase spending with credits reported separately.",
          suggested_navigation: null,
        },
      ],
    };

    expect(parseAgentStructuredResponse(response)).toBe(response);
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

  it("requires a complete canonical taxonomy when a receipt line carries Day 16 fields", () => {
    const response = structuredClone(supportedResponse()) as {
      blocks: Record<string, unknown>[];
    };
    const receipt = response.blocks.find((block) => block.type === "receipt_summary");
    if (!receipt || !Array.isArray(receipt.items)) throw new Error("receipt fixture is missing");
    const line = receipt.items[0] as Record<string, unknown>;
    Object.assign(line, {
      parent_category: "household_home",
      subcategory: "Paper goods",
      concept: "Paper towels",
      activity_type: "household_consumable",
      replenishment_eligibility: "replenishable",
      classification_confidence: 0.94,
    });

    expect(parseAgentStructuredResponse(response)).toBe(response);
    delete line.activity_type;
    expect(() => parseAgentStructuredResponse(response)).toThrow(AgentProtocolError);
  });

  it("accepts allowlisted navigation with omitted or compatible entity fields", () => {
    const response = {
      schema_version: "1.0",
      blocks: [
        {
          type: "navigation",
          label: "Open insights",
          target_surface: "expense_insights",
        },
        {
          type: "empty",
          title: "No matching deals",
          message: "Open the active deal list instead.",
          suggested_navigation: {
            type: "navigation",
            label: "Open Target deal",
            target_surface: "deals",
            entity: { kind: "deal", public_id: "71" },
          },
        },
      ],
    };

    expect(parseAgentStructuredResponse(response)).toBe(response);
  });

  it("accepts a bounded partial attention summary even when completed areas are empty", () => {
    const block = attentionSummary({ items: [] });
    const response = { schema_version: "1.0", blocks: [block] };

    expect(parseAgentStructuredResponse(response)).toBe(response);
  });

  it("accepts a strict bounded classification retrospective", () => {
    const block = classificationActivity();
    const response = { schema_version: "1.0", blocks: [block] };

    expect(parseAgentStructuredResponse(response)).toBe(response);
  });

  it("strictly validates the direct Classification Activity API projection", () => {
    const block = classificationActivity();
    const { type: _type, block_version: _blockVersion, title: _title, ...activity } = block;
    const response = {
      schema_version: "1.0",
      as_of: "2026-08-17T18:00:00Z",
      ...activity,
    };

    expect(parseClassificationActivityOut(response)).toBe(response);
    expect(() => parseClassificationActivityOut({ ...response, raw_evidence_json: {} }))
      .toThrow(AgentProtocolError);
  });

  it.each([
    classificationActivity({ block_version: "2.0" }),
    classificationActivity({ timezone: "America/Phoenix" }),
    classificationActivity({ secret_provider_payload: { account_id: "forbidden" } }),
    classificationActivity({
      categories: [{
        parent_category: "food_dining",
        transaction_count: 1,
        receipt_item_count: 1,
        total_count: 3,
      }],
    }),
    classificationActivity({ truncated_sections: ["categories"] }),
    classificationActivity({
      transactions: [{ merchant: "Must not appear in the categories view" }],
    }),
  ])("rejects malformed or inconsistent classification activity", (block) => {
    expect(() => parseAgentStructuredResponse({ schema_version: "1.0", blocks: [block] }))
      .toThrow(AgentProtocolError);
  });

  it.each([
    attentionSummary({ block_version: "2.0" }),
    attentionSummary({ status: "complete" }),
    attentionSummary({ checked_domains: ["transactions", "transactions"] }),
    attentionSummary({ unavailable_domains: ["replenishment", "deals"] }),
    attentionSummary({
      items: [
        {
          priority: "action_required",
          domain: "transactions",
          title: "Expense reviews",
          count: 0,
        },
      ],
    }),
    attentionSummary({
      items: [
        {
          priority: "time_sensitive",
          domain: "replenishment",
          title: "Household item likely due",
          count: 1,
        },
        {
          priority: "action_required",
          domain: "transactions",
          title: "Expense reviews",
          count: 2,
        },
      ],
    }),
    attentionSummary({
      items: [
        {
          priority: "action_required",
          domain: "transactions",
          title: "Expense reviews",
          count: 2,
          href: "https://evil.example",
        },
      ],
    }),
    attentionSummary({
      items: [
        {
          priority: "action_required",
          domain: "transactions",
          title: "Expense reviews",
          count: 2,
          navigation: {
            type: "navigation",
            label: "Leave ExpenseOps",
            target_surface: "expense_review",
            href: "https://evil.example",
          },
        },
      ],
    }),
    attentionSummary({
      items: [
        {
          priority: "action_required",
          domain: "receipts",
          title: "Receipt review",
          count: 1,
        },
      ],
    }),
  ])("rejects malformed, inconsistent, or unsafe attention-summary payloads", (block) => {
    expect(() =>
      parseAgentStructuredResponse({ schema_version: "1.0", blocks: [block] }),
    ).toThrow(AgentProtocolError);
  });

  it.each([
    {
      type: "navigation",
      label: "External URL",
      target_surface: "deals",
      href: "https://evil.example",
    },
    {
      type: "navigation",
      label: "Contradictory entity",
      target_surface: "deals",
      entity: { kind: "receipt", public_id: "51" },
    },
    {
      type: "empty",
      title: "Unsafe suggestion",
      message: "This must fail closed.",
      suggested_navigation: {
        type: "navigation",
        label: "Unknown target",
        target_surface: "https://evil.example",
      },
    },
  ])("rejects unsafe top-level or nested navigation", (block) => {
    expect(() =>
      parseAgentStructuredResponse({ schema_version: "1.0", blocks: [block] }),
    ).toThrow(AgentProtocolError);
  });

  it.each(["navigation", "action_confirmation", "unexpected_block"])(
    "fails closed for malformed or unsupported %s blocks",
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

describe("Agent feedback validation", () => {
  const feedback = {
    schema_version: "1.0",
    public_id: "feedback-public-1",
    message_public_id: "message-assistant-1",
    conversation_public_id: "conversation-public-1",
    run_public_id: "run-public-1",
    rating: "not_helpful",
    reason: "didnt_understand",
    created_at: "2026-08-16T16:00:00Z",
    updated_at: "2026-08-16T16:00:01Z",
  };

  it("accepts the bounded feedback projection", () => {
    expect(parseAgentFeedback(feedback)).toBe(feedback);
    expect(
      parseAgentFeedback({ ...feedback, rating: "helpful", reason: null }),
    ).toEqual({ ...feedback, rating: "helpful", reason: null });
  });

  it.each([
    { rating: "mixed" },
    { reason: "full_answer_copy" },
    { rating: "helpful", reason: "other" },
    { reason: undefined },
    { run_public_id: "" },
    { schema_version: "2.0" },
    { answer_text: "This must never enter the feedback contract." },
  ])("rejects malformed or unbounded feedback fields: %o", (overrides) => {
    expect(() => parseAgentFeedback({ ...feedback, ...overrides })).toThrow(
      AgentProtocolError,
    );
  });

  it("keeps loaded feedback bound to its exact assistant message and conversation", () => {
    const detail = {
      conversation: {
        public_id: "conversation-public-1",
        title: "Feedback",
        archived_at: null,
        created_at: "2026-08-16T15:59:00Z",
        updated_at: "2026-08-16T16:00:01Z",
      },
      messages: [
        {
          public_id: "message-assistant-1",
          conversation_public_id: "conversation-public-1",
          role: "assistant",
          text: null,
          structured_response: {
            schema_version: "1.0",
            blocks: [{ type: "text", text: "Grounded answer." }],
          },
          client_message_id: null,
          feedback_eligible: true,
          feedback,
          created_at: "2026-08-16T16:00:00Z",
        },
      ],
      messages_total: 1,
      messages_offset: 0,
      messages_has_more: false,
    };

    expect(parseAgentConversationDetail(detail)).toBe(detail);
    expect(() =>
      parseAgentConversationDetail({
        ...detail,
        messages: [
          {
            ...detail.messages[0],
            feedback: { ...feedback, message_public_id: "different-message" },
          },
        ],
      }),
    ).toThrow(AgentProtocolError);
    expect(() =>
      parseAgentConversationDetail({
        ...detail,
        messages: [{ ...detail.messages[0], feedback_eligible: false }],
      }),
    ).toThrow(AgentProtocolError);
    expect(() =>
      parseAgentConversationDetail({
        ...detail,
        messages: [
          {
            ...detail.messages[0],
            role: "user",
            feedback_eligible: true,
            feedback: null,
          },
        ],
      }),
    ).toThrow(AgentProtocolError);
  });

  it("checks feedback run identity on an enclosing assistant-completed event", () => {
    const message = {
      public_id: "message-assistant-1",
      conversation_public_id: "conversation-public-1",
      role: "assistant",
      text: "Grounded answer.",
      structured_response: null,
      client_message_id: null,
      feedback_eligible: true,
      feedback,
      created_at: "2026-08-16T16:00:00Z",
    };
    const event = {
      ...streamBase,
      type: "assistant_completed",
      message,
    };

    expect(parseAgentStreamEvent(event)).toBe(event);
    expect(() =>
      parseAgentStreamEvent({
        ...event,
        run_public_id: "different-run",
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

  it.each(["replenishment", "receipts", "deals", "errands", "integrations", "classification"])(
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

describe("Agent controlled-action validation", () => {
  const block = {
    type: "action_confirmation",
    block_id: null,
    action: "mark_transaction_personal",
    title: "Mark transaction personal",
    summary: "This transaction will be marked personal.",
    details: [
      { label: "Merchant", value: "Costco" },
      { label: "Amount", value: "USD 84.20" },
    ],
    confirm_label: "Mark personal",
    cancel_label: "Cancel",
    proposal_id: "proposal-public-1",
    proposal_version: 1,
    status: "awaiting_confirmation",
    expires_at: "2026-08-16T16:15:00Z",
  } as const;

  it("accepts the strict code-owned confirmation contract", () => {
    expect(parseAgentActionConfirmation(block)).toBe(block);
    expect(
      parseAgentStructuredResponse({ schema_version: "1.0", blocks: [block] }).blocks[0],
    ).toBe(block);
    const receiptLearning = {
      ...block,
      action: "apply_receipt_learning_batch",
      title: "Learn household items from this receipt",
      confirm_label: "Confirm selected",
    } as const;
    expect(parseAgentActionConfirmation(receiptLearning)).toBe(receiptLearning);
    const itemizedReceiptSplit = {
      ...block,
      action: "post_itemized_receipt_split",
      title: "Split restaurant receipt by item",
      confirm_label: "Confirm itemized split",
    } as const;
    expect(parseAgentActionConfirmation(itemizedReceiptSplit)).toBe(itemizedReceiptSplit);
  });

  it("rejects editable action parameters, missing action types, and invalid versions", () => {
    expect(() =>
      parseAgentActionConfirmation({ ...block, transaction_id: 42 }),
    ).toThrow(AgentProtocolError);
    expect(() =>
      parseAgentActionConfirmation({ ...block, action: undefined }),
    ).toThrow(AgentProtocolError);
    expect(() =>
      parseAgentActionConfirmation({ ...block, proposal_version: 0 }),
    ).toThrow(AgentProtocolError);
    expect(() =>
      parseAgentActionConfirmation({ ...block, action: "create_household_item_directly" }),
    ).toThrow(AgentProtocolError);
  });
});

describe("Day 10 lifestyle summary validation", () => {
  const block = {
    type: "lifestyle_summary",
    block_version: "1.0",
    title: "Restaurant summary",
    start_date: "2026-08-01",
    end_date: "2026-08-16",
    previous_start_date: "2026-07-16",
    previous_end_date: "2026-07-31",
    activity_type: "restaurants",
    currency_code: "USD",
    spend_basis: "card",
    total_cents: 12_000,
    credits_cents: 500,
    transaction_count: 4,
    average_cents: 3_000,
    personal_cents: 4_000,
    shared_cents: 8_000,
    unreviewed_cents: 0,
    previous_total_cents: 9_000,
    previous_transaction_count: 3,
    unknown_share_transactions: 0,
    previous_unknown_share_transactions: 0,
    unknown_credit_share_transactions: 0,
    previous_unknown_credit_share_transactions: 0,
    weekday_cents: 8_000,
    weekday_count: 3,
    weekend_cents: 4_000,
    weekend_count: 1,
    uncertain_transaction_count: 1,
    observations: ["Restaurant purchases increased from 3 to 4."],
    activities: [{ name: "restaurants", amount_cents: 12_000, transaction_count: 4, percentage: 100 }],
    top_merchants: [{ name: "Local Bistro", amount_cents: 8_000, transaction_count: 2, percentage: 66.7 }],
  } as const;

  it("accepts the strict reconciled code-owned lifestyle card", () => {
    expect(parseAgentStructuredResponse({ schema_version: "1.0", blocks: [block] }).blocks[0]).toBe(block);
  });

  it("fails closed on negative, unreconciled, unknown card-basis, or extra data", () => {
    expect(() => parseAgentStructuredResponse({ schema_version: "1.0", blocks: [{ ...block, total_cents: -1 }] })).toThrow(AgentProtocolError);
    expect(() => parseAgentStructuredResponse({ schema_version: "1.0", blocks: [{ ...block, personal_cents: 3_999 }] })).toThrow(AgentProtocolError);
    expect(() => parseAgentStructuredResponse({ schema_version: "1.0", blocks: [{ ...block, unknown_share_transactions: 1 }] })).toThrow(AgentProtocolError);
    expect(() => parseAgentStructuredResponse({ schema_version: "1.0", blocks: [{ ...block, model_commentary: "buy more" }] })).toThrow(AgentProtocolError);
  });
});

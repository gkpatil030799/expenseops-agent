import { describe, expect, it } from "vitest";

import { buildDashboardEvents, filterTransactions, memoryForTransactions } from "@/dashboardLogic";
import type { Transaction } from "@/types";

function tx(overrides: Partial<Transaction>): Transaction {
  return {
    id: 1,
    plaid_transaction_id: "plaid-1",
    merchant_name: "Costco",
    name: "Costco",
    amount_cents: 4200,
    amount: "42.00",
    iso_currency_code: "USD",
    date: "2026-05-20",
    authorized_date: null,
    pending: false,
    status: "posted",
    agent_question: null,
    splitwise_expense_id: "expense-1",
    splitwise_payload_json: JSON.stringify({
      group_id: 44,
      users: [{ first_name: "Rahul", last_name: "Shah", user_id: 7 }],
    }),
    last_error: null,
    classification_suggestion: "likely_shared",
    classification_reason: "test",
    can_undo_transaction: true,
    created_at: "2026-05-20T10:00:00Z",
    updated_at: "2026-05-20T11:00:00Z",
    ...overrides,
  };
}

describe("dashboard logic", () => {
  it("builds timeline events for rendering", () => {
    const events = buildDashboardEvents([tx({})]);

    expect(events.some((event) => event.type === "transaction_detected")).toBe(true);
    expect(events.some((event) => event.type === "split_posted")).toBe(true);
    expect(events[0].merchant).toBe("Costco");
  });

  it("filters transactions by merchant and status", () => {
    const filtered = filterTransactions(
      [
        tx({ merchant_name: "Costco", status: "posted" }),
        tx({ id: 2, merchant_name: "Uber", status: "personal" }),
      ],
      { merchant: "cost", group: "", status: "posted", dateFrom: "", dateTo: "" },
    );

    expect(filtered).toHaveLength(1);
    expect(filtered[0].merchant_name).toBe("Costco");
  });

  it("builds memory entries for quick selection", () => {
    const memory = memoryForTransactions([tx({}), tx({ id: 2 })]);

    expect(memory.friends[0]).toMatchObject({ name: "Rahul Shah", count: 2 });
    expect(memory.groups[0]).toMatchObject({ name: "Group 44", count: 2 });
  });
});

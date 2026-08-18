import { expect, test, type Page, type TestInfo } from "@playwright/test";

import type { AgentRunOut, AgentStreamEvent, AgentStructuredResponse } from "../src/agent/contracts";
import {
  agentStreamCallCount,
  agentStreamCalls,
  canonicalMessages,
  installAgentStream,
  mockAgentApp,
  type RecordedAgentStreamCall,
} from "./fixtures/agent";

const RESPONSE: AgentStructuredResponse = {
  schema_version: "1.0",
  blocks: [
    {
      type: "text",
      text: "Coffee purchases totaled USD 42.00 across 6 transactions; the average was USD 7.00. The comparable prior period had 4 purchases totaling USD 28.00. Some Food & Dining transactions remained unclassified by lifestyle subtype.",
    },
    {
      type: "lifestyle_summary",
      block_version: "1.0",
      title: "Coffee summary",
      start_date: "2026-08-01",
      end_date: "2026-08-16",
      previous_start_date: "2026-07-16",
      previous_end_date: "2026-07-31",
      activity_type: "coffee",
      currency_code: "USD",
      spend_basis: "card",
      total_cents: 4_200,
      credits_cents: 500,
      transaction_count: 6,
      average_cents: 700,
      personal_cents: 2_800,
      shared_cents: 1_400,
      unreviewed_cents: 0,
      previous_total_cents: 2_800,
      previous_transaction_count: 4,
      unknown_share_transactions: 0,
      previous_unknown_share_transactions: 0,
      unknown_credit_share_transactions: 0,
      previous_unknown_credit_share_transactions: 0,
      weekday_cents: 3_500,
      weekday_count: 5,
      weekend_cents: 700,
      weekend_count: 1,
      uncertain_transaction_count: 1,
      observations: [
        "Coffee purchases: 6 totaling USD 42.00.",
        "Purchase frequency changed from 4 to 6 (+2); purchase spend changed by +USD 14.00.",
      ],
      activities: [{ name: "coffee", amount_cents: 4_200, transaction_count: 6, percentage: 100 }],
      top_merchants: [{ name: "Local Coffee", amount_cents: 2_800, transaction_count: 4, percentage: 66.7 }],
    },
  ],
};

function messages(calls: RecordedAgentStreamCall[]) {
  const call = calls.at(-1);
  return call?.body?.text && call.body.client_message_id
    ? canonicalMessages(RESPONSE, call.body.text, call.body.client_message_id)
    : [];
}

function events(): AgentStreamEvent[] {
  const run: AgentRunOut = {
    public_id: "run-day10",
    status: "completed",
    model_name: "gpt-4.1-mini",
    prompt_version: "expenseops-readonly-v1.5",
    input_tokens: 300,
    output_tokens: 80,
    total_tokens: 380,
    error_code: null,
    created_at: "2026-08-17T12:00:00Z",
    started_at: "2026-08-17T12:00:00Z",
    completed_at: "2026-08-17T12:00:03Z",
  };
  const assistant = canonicalMessages(RESPONSE, "How much have I spent on coffee lately?", "day10-message")[1];
  return [
    { schema_version: "1.0", sequence: 0, run_public_id: run.public_id, type: "run_started", resumed: false },
    { schema_version: "1.0", sequence: 1, run_public_id: run.public_id, type: "tool_started", activity: "spending", message: "Checking lifestyle and dining activity…" },
    { schema_version: "1.0", sequence: 2, run_public_id: run.public_id, type: "tool_completed", activity: "spending", message: "Lifestyle and dining data is ready." },
    { schema_version: "1.0", sequence: 3, run_public_id: run.public_id, type: "structured_response", response: RESPONSE },
    { schema_version: "1.0", sequence: 4, run_public_id: run.public_id, type: "assistant_completed", message: assistant },
    { schema_version: "1.0", sequence: 5, run_public_id: run.public_id, type: "run_completed", run },
  ];
}

async function send(page: Page, testId: "agent-panel" | "agent-page") {
  const container = page.getByTestId(testId);
  const count = await agentStreamCallCount(page);
  const composer = container.getByLabel("Ask ExpenseOps Agent");
  await composer.fill("How much have I spent on coffee lately?");
  await composer.press("Enter");
  await expect.poll(() => agentStreamCallCount(page)).toBe(count + 1);
  await expect(container.locator('article[aria-label="ExpenseOps Agent response in progress"]')).toHaveCount(0);
  return container;
}

test("desktop renders the bounded lifestyle card without action controls", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "Detailed Day 10 flow is covered in Chromium");
  const fixture = await mockAgentApp(page, { messages });
  await installAgentStream(page, { events: events() });
  await page.goto("/");
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  const panel = await send(page, "agent-panel");

  await expect(panel.getByText("Coffee summary")).toBeVisible();
  await expect(panel.getByText("$42.00", { exact: true }).first()).toBeVisible();
  await expect(panel.getByText("Average check", { exact: true })).toBeVisible();
  await expect(panel.getByText("Typical check", { exact: true })).toHaveCount(0);
  await expect(panel.getByText(/left unclassified rather than guessed/)).toBeVisible();
  await expect(panel.getByRole("button", { name: /confirm|buy|order|save/i })).toHaveCount(0);
  expect((await agentStreamCalls(page))[0].body?.text).toBe("How much have I spent on coffee lately?");
  expect(
    fixture.requests.filter(
      (request) => request.method !== "GET" && !request.pathname.startsWith("/api/agent/conversations"),
    ),
  ).toEqual([]);
});

for (const width of [320, 375, 390]) {
  test(`mobile lifestyle card is readable without horizontal overflow at ${width}px`, async ({ page }, testInfo: TestInfo) => {
    test.skip(testInfo.project.name !== "mobile-chromium", "Mobile Day 10 coverage");
    await page.setViewportSize({ width, height: 844 });
    await mockAgentApp(page, { messages });
    await installAgentStream(page, { events: events() });
    await page.goto("/");
    await page.getByRole("navigation", { name: "Primary mobile navigation" }).getByRole("button", { name: "Agent", exact: true }).click();
    const agent = await send(page, "agent-page");
    await expect(agent.getByText("Coffee summary")).toBeVisible();
    await expect(agent.getByText("Average check", { exact: true })).toBeVisible();
    const average = agent.getByText("$7.00", { exact: true });
    await expect(average).toBeVisible();
    const averageMetrics = await average.evaluate((element) => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
      textOverflow: getComputedStyle(element).textOverflow,
    }));
    expect(averageMetrics.scrollWidth).toBeLessThanOrEqual(averageMetrics.clientWidth);
    expect(averageMetrics.textOverflow).not.toBe("ellipsis");
    const sizes = await page.evaluate(() => ({ width: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth }));
    expect(sizes.scrollWidth).toBeLessThanOrEqual(sizes.width);
  });
}

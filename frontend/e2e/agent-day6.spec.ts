import { expect, test, type Locator, type Page, type Request, type TestInfo } from "@playwright/test";

import type {
  AgentRunOut,
  AgentStreamEvent,
  AgentStructuredResponse,
  AgentToolActivity,
} from "../src/agent/contracts";
import {
  agentStreamCallCount,
  agentStreamCalls,
  canonicalMessages,
  installAgentStream,
  mockAgentApp,
  releaseAgentStream,
  waitForAgentStreamPause,
  type RecordedAgentStreamCall,
} from "./fixtures/agent";

const ATTENTION_RESPONSE: AgentStructuredResponse = {
  schema_version: "1.0",
  blocks: [
    {
      type: "text",
      text: "Four things need attention across the ExpenseOps areas checked.",
    },
    {
      type: "attention_summary",
      block_version: "1.0",
      title: "Today needs attention",
      status: "complete",
      checked_domains: ["transactions", "replenishment", "errands"],
      unavailable_domains: [],
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
          detail: "Laundry detergent is likely due from confirmed purchase evidence.",
          count: 1,
          navigation: {
            type: "navigation",
            label: "View household needs",
            target_surface: "household_staples",
          },
        },
        {
          priority: "time_sensitive",
          domain: "errands",
          title: "Errand due today",
          detail: "One open high-priority errand is due today.",
          count: 1,
          navigation: {
            type: "navigation",
            label: "View errands",
            target_surface: "household_errands",
          },
        },
      ],
      items_truncated: false,
    },
  ],
};

const NEED_AND_DEAL_RESPONSE: AgentStructuredResponse = {
  schema_version: "1.0",
  blocks: [
    {
      type: "text",
      text: "Laundry detergent is likely due based on confirmed purchase evidence. One current Target offer is relevant to that need and expires Aug 20, 2026.",
    },
    {
      type: "replenishment_summary",
      title: "Laundry detergent outlook",
      items: [
        {
          public_id: "71",
          name: "Laundry detergent",
          predicted_due_on: "2026-08-17",
          confidence: null,
          confidence_level: "medium",
          evidence_basis: "purchase_pattern",
          due_state: "likely_due",
          reason: "Likely due from three confirmed purchases; timing is not certain.",
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
      type: "deal_list",
      title: "Relevant current deal",
      deals: [
        {
          public_id: "71",
          merchant: "Target",
          headline: "15% off laundry care",
          expires_at: "2026-08-20T23:59:00Z",
          score: 82,
          category: "Household",
          offer_type: "percent_off",
          percent_off: 15,
          amount_off_cents: null,
          currency_code: "USD",
          minimum_spend_cents: null,
          promo_code: null,
          trust_status: "trusted",
          saved: false,
          relevant_to_need: true,
          relevance_reasons: ["Relevant to Laundry detergent, which is likely due."],
        },
      ],
      total_count: 1,
    },
  ],
};

const SPENDING_AND_TRANSACTIONS_RESPONSE: AgentStructuredResponse = {
  schema_version: "1.0",
  blocks: [
    {
      type: "text",
      text: "Food & Dining increased by USD 86.00. The aggregate remains USD 412.00; the transactions below are supporting detail and are not re-totaled.",
    },
    {
      type: "spending_summary",
      title: "Food & Dining spending",
      start_date: "2026-07-18",
      end_date: "2026-08-16",
      currency_code: "USD",
      spend_basis: "card",
      total_cents: 41_200,
      previous_total_cents: 32_600,
      credits_cents: 0,
      previous_credits_cents: 0,
      unknown_share_transactions: 0,
      previous_unknown_share_transactions: 0,
      unknown_credit_share_transactions: 0,
      previous_unknown_credit_share_transactions: 0,
      change_percent: 26.4,
      highlights: ["Food & Dining: +USD 86.00 versus the prior period."],
      top_categories: [
        {
          name: "Food & Dining",
          amount_cents: 41_200,
          transaction_count: 8,
          percentage: 100,
          previous_amount_cents: 32_600,
        },
      ],
      top_merchants: [
        {
          name: "Local Bistro",
          amount_cents: 16_800,
          transaction_count: 3,
          percentage: 40.8,
          previous_amount_cents: 12_000,
        },
      ],
    },
    {
      type: "transaction_list",
      title: "Supporting Food & Dining transactions",
      transactions: [
        {
          public_id: "81",
          merchant: "Local Bistro",
          amount_cents: 16_800,
          currency_code: "USD",
          occurred_on: "2026-08-12",
          category: "Food & Dining",
          status: "personal",
          pending: false,
        },
        {
          public_id: "82",
          merchant: "Corner Cafe",
          amount_cents: 11_200,
          currency_code: "USD",
          occurred_on: "2026-08-09",
          category: "Food & Dining",
          status: "shared",
          pending: false,
        },
      ],
      total_count: 2,
    },
  ],
};

const PARTIAL_RESPONSE: AgentStructuredResponse = {
  schema_version: "1.0",
  blocks: [
    {
      type: "text",
      text: "I found two items from the areas that completed. Current deals were unavailable.",
    },
    {
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
          title: "Expense review",
          detail: "One transaction still needs review.",
          count: 1,
          navigation: {
            type: "navigation",
            label: "View expense review",
            target_surface: "expense_review",
          },
        },
        {
          priority: "time_sensitive",
          domain: "replenishment",
          title: "Household item likely due",
          detail: "Laundry detergent is likely due.",
          count: 1,
          navigation: {
            type: "navigation",
            label: "View household needs",
            target_surface: "household_staples",
          },
        },
      ],
      items_truncated: false,
    },
  ],
};

const MOBILE_ATTENTION_RESPONSE: AgentStructuredResponse = {
  schema_version: "1.0",
  blocks: [
    {
      type: "text",
      text: "Here is a concise summary from the selected ExpenseOps checks.",
    },
    {
      type: "attention_summary",
      block_version: "1.0",
      title: "Before you go out today",
      status: "complete",
      checked_domains: ["transactions", "replenishment", "errands"],
      unavailable_domains: [],
      items: [
        {
          priority: "action_required",
          domain: "transactions",
          title: "Expense reviews",
          detail: "Two transactions still need review.",
          count: 2,
          navigation: { type: "navigation", label: "View expenses", target_surface: "expense_review" },
        },
        {
          priority: "time_sensitive",
          domain: "replenishment",
          title: "Household item likely due",
          detail: "unbroken".repeat(45),
          count: 1,
          navigation: { type: "navigation", label: "View household needs", target_surface: "household_staples" },
        },
        {
          priority: "time_sensitive",
          domain: "errands",
          title: "Errand due today",
          detail: "One open high-priority errand is due today.",
          count: 1,
          navigation: { type: "navigation", label: "View errands", target_surface: "household_errands" },
        },
        {
          priority: "useful_to_know",
          domain: "replenishment",
          title: "Household item may be due soon",
          detail: "One additional item is probably due based on current replenishment evidence.",
          count: 1,
          navigation: { type: "navigation", label: "View household needs", target_surface: "household_staples" },
        },
      ],
      items_truncated: false,
    },
  ],
};

function skipUnlessChromium(testInfo: TestInfo): void {
  test.skip(testInfo.project.name !== "chromium", "Detailed Day 6 behavior is covered in Chromium");
}

function skipMobileProject(testInfo: TestInfo): void {
  test.skip(testInfo.project.name === "mobile-chromium", "Desktop companion coverage");
}

async function openDesktopAgent(page: Page): Promise<Locator> {
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  const panel = page.getByTestId("agent-panel");
  await expect(panel).toBeVisible();
  return panel;
}

async function sendFrom(page: Page, containerTestId: "agent-panel" | "agent-page", text: string) {
  const container = page.getByTestId(containerTestId);
  const composer = container.getByLabel("Ask ExpenseOps Agent");
  const callsBefore = await agentStreamCallCount(page);
  await expect(composer).toBeEnabled();
  await composer.fill(text);
  await composer.press("Enter");
  await expect.poll(() => agentStreamCallCount(page)).toBe(callsBefore + 1);
  await expect(container.locator('article[aria-label="ExpenseOps Agent response in progress"]')).toHaveCount(0);
}

function messagesForResponse(response: AgentStructuredResponse) {
  return (calls: RecordedAgentStreamCall[]) => {
    const call = calls.at(-1);
    if (!call?.body?.text || !call.body.client_message_id) return [];
    return canonicalMessages(response, call.body.text, call.body.client_message_id);
  };
}

const PROGRESS_COPY: Record<AgentToolActivity, [string, string]> = {
  spending: ["Checking your spending…", "Spending data is ready."],
  transactions: ["Looking through your transactions…", "Transactions are ready."],
  replenishment: ["Checking household and replenishment evidence…", "Household evidence is ready."],
  receipts: ["Checking your receipts…", "Receipt details are ready."],
  deals: ["Checking current deals…", "Deal results are ready."],
  errands: ["Checking errands and stored plans…", "Errand details are ready."],
  integrations: ["Checking integration status…", "Integration status is ready."],
};

function semanticEvents(
  response: AgentStructuredResponse,
  activities: AgentToolActivity[],
  question: string,
  failedActivities: AgentToolActivity[] = [],
): AgentStreamEvent[] {
  const events: AgentStreamEvent[] = [];
  let sequence = 0;
  events.push({
    schema_version: "1.0",
    sequence: sequence++,
    run_public_id: "run-day6",
    type: "run_started",
    resumed: false,
  });
  activities.forEach((activity) => {
    events.push({
      schema_version: "1.0",
      sequence: sequence++,
      run_public_id: "run-day6",
      type: "tool_started",
      activity,
      message: PROGRESS_COPY[activity][0],
    });
    if (!failedActivities.includes(activity)) {
      events.push({
        schema_version: "1.0",
        sequence: sequence++,
        run_public_id: "run-day6",
        type: "tool_completed",
        activity,
        message: PROGRESS_COPY[activity][1],
      });
    }
  });
  const text = response.blocks.find((block) => block.type === "text");
  if (text?.type === "text") {
    events.push({
      schema_version: "1.0",
      sequence: sequence++,
      run_public_id: "run-day6",
      type: "assistant_delta",
      delta: text.text,
    });
  }
  const assistant = canonicalMessages(response, question, "day6-message")[1];
  events.push(
    {
      schema_version: "1.0",
      sequence: sequence++,
      run_public_id: "run-day6",
      type: "structured_response",
      response,
    },
    {
      schema_version: "1.0",
      sequence: sequence++,
      run_public_id: "run-day6",
      type: "assistant_completed",
      message: assistant,
    },
    {
      schema_version: "1.0",
      sequence: sequence++,
      run_public_id: "run-day6",
      type: "run_completed",
      run: completedRun(),
    },
  );
  return events;
}

function completedRun(): AgentRunOut {
  return {
    public_id: "run-day6",
    status: "completed",
    model_name: "gpt-5-mini",
    prompt_version: "expenseops-readonly-v1.3",
    input_tokens: 320,
    output_tokens: 180,
    total_tokens: 500,
    error_code: null,
    created_at: "2026-08-16T13:00:00Z",
    started_at: "2026-08-16T13:00:01Z",
    completed_at: "2026-08-16T13:00:03Z",
  };
}

async function mockHouseholdItem(page: Page): Promise<void> {
  const item = {
    id: 71,
    name: "Laundry detergent",
    quantity: "1",
    unit: "bottle",
    preferred_place_name: "Target",
    preferred_place_address: null,
    replenishment_mode: "either",
    cadence_days: 30,
    last_acquired_at: "2026-07-12T12:00:00Z",
    snoozed_until: null,
    enabled: true,
    notes: null,
    due_score: 0.9,
    due_state: "likely_due",
    should_surface: true,
    linked_errand_id: null,
    created_at: "2026-01-01T12:00:00Z",
    updated_at: "2026-08-16T12:00:00Z",
  };
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    const emptyPage = { items: [], total: 0, limit: 25, offset: 0, has_more: false };
    const responses: Record<string, unknown> = {
      "/api/household/errands": [],
      "/api/household/items": [item],
      "/api/household/errand-plans/latest": null,
      "/api/household/locations": [],
      "/api/replenishment/summary": {
        this_week: [],
        learning: { confirmed_acquisitions: 3, items_with_history: 1, active_model: null },
        recent_receipts: [],
        accuracy: {
          evaluated_predictions: 0,
          mae_days: null,
          current_prediction_method: null,
          training_observations: 0,
          validation_observations: 0,
          validation_method: null,
          baseline_mae_days: null,
          active_model_mae_days: null,
          improvement_pct: null,
          confidence_level: "insufficient",
          latest_model_status: null,
          decision_reason: null,
        },
      },
      "/api/replenishment/gmail/status": { configured: true, last_successful_sync_at: null, latest_receipt_at: null },
    };
    if (url.pathname === "/api/replenishment/receipts") return route.fulfill({ json: emptyPage });
    if (url.pathname in responses) return route.fulfill({ json: responses[url.pathname] });
    return route.fallback();
  });
}

async function mockSpendingInsights(page: Page): Promise<void> {
  await page.route("**/api/insights/spending?**", (route) =>
    route.fulfill({
      json: {
        range: {
          start_date: "2026-07-18",
          end_date: "2026-08-16",
          previous_start_date: "2026-06-18",
          previous_end_date: "2026-07-17",
          granularity: "day",
        },
        scope: {
          currency: "USD",
          available_currencies: ["USD"],
          excluded_other_currency_transactions: 0,
          spend_basis: "card",
          viewer_share_identity_connected: true,
          pending_transactions_excluded: true,
        },
        summary: {
          total_cents: 41_200,
          personal_cents: 25_000,
          shared_cents: 16_200,
          classified_cents: 41_200,
          unreviewed_cents: 0,
          credits_cents: 0,
          unknown_share_transactions: 0,
          unknown_credit_share_transactions: 0,
          transaction_count: 8,
          average_cents: 5_150,
        },
        comparison: {
          total_cents: 32_600,
          personal_cents: 20_000,
          shared_cents: 12_600,
          classified_cents: 32_600,
          unreviewed_cents: 0,
          credits_cents: 0,
          unknown_share_transactions: 0,
          unknown_credit_share_transactions: 0,
          transaction_count: 7,
          average_cents: 4_657,
        },
        trend: [],
        category_breakdown: [{ name: "Food & Dining", amount_cents: 41_200, transaction_count: 8, percentage: 100, previous_amount_cents: 32_600 }],
        subcategory_breakdown: [],
        merchant_breakdown: [{ name: "Local Bistro", amount_cents: 16_800, transaction_count: 3 }],
        personal_shared: { personal: 25_000, shared: 16_200 },
        shared_people: [],
        shared_groups: [],
        category_trend: [],
        notable_changes: [{ kind: "category", direction: "up", label: "Food & Dining", amount_cents: 8_600, detail: "+$86 vs previous period" }],
        accounts: ["Chase card"],
        categories: ["Food & Dining"],
        merchants: ["Local Bistro"],
        data_quality: {
          unknown_share_transactions: 0,
          unknown_credit_share_transactions: 0,
          unreviewed_cents: 0,
          pending_review_cents: 0,
          uncategorized_cents: 0,
          pending_transactions_excluded: true,
        },
      },
    }),
  );
}

function installForbiddenWriteRecorder(page: Page): string[] {
  const writes: string[] = [];
  page.on("request", (request) => {
    if (isForbiddenWrite(request)) writes.push(`${request.method()} ${request.url()}`);
  });
  return writes;
}

function isForbiddenWrite(request: Request): boolean {
  if (["GET", "HEAD", "OPTIONS"].includes(request.method())) return false;
  const url = new URL(request.url());
  if (/(^|\.)(googleapis\.com|plaid\.com|splitwise\.com|openai\.com|telegram\.org)$/.test(url.hostname)) return true;
  return !url.pathname.startsWith("/api/agent/");
}

async function assertNoHorizontalOverflow(page: Page, agent: Locator): Promise<void> {
  const conversationRegion = agent
    .getByRole("list", { name: "Agent conversation messages" })
    .locator("xpath=..");
  const metrics = await conversationRegion.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
    documentClientWidth: document.documentElement.clientWidth,
    documentScrollWidth: document.documentElement.scrollWidth,
  }));
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth);
  expect(metrics.documentScrollWidth).toBeLessThanOrEqual(metrics.documentClientWidth);
  expect(metrics.scrollHeight).toBeGreaterThan(metrics.clientHeight);
}

test("attention response shows multiple safe progress states and one concise semantic summary", async ({ page }, testInfo) => {
  skipMobileProject(testInfo);
  const writes = installForbiddenWriteRecorder(page);
  await mockAgentApp(page, { messages: messagesForResponse(ATTENTION_RESPONSE) });
  await installAgentStream(page, {
    events: semanticEvents(
      ATTENTION_RESPONSE,
      ["transactions", "replenishment", "errands"],
      "What needs my attention today?",
    ),
    pauseAfterIndexes: [1, 3, 5],
  });

  await page.goto("/");
  const panel = await openDesktopAgent(page);
  const composer = panel.getByLabel("Ask ExpenseOps Agent");
  await composer.fill("What needs my attention today?");
  await composer.press("Enter");

  await waitForAgentStreamPause(page, 1);
  await expect(panel).toContainText("Looking through your transactions…");
  await releaseAgentStream(page);
  await waitForAgentStreamPause(page, 3);
  await expect(panel).toContainText("Checking household and replenishment evidence…");
  await releaseAgentStream(page);
  await waitForAgentStreamPause(page, 5);
  await expect(panel).toContainText("Checking errands and stored plans…");
  await releaseAgentStream(page);
  await expect(panel.locator('article[aria-label="ExpenseOps Agent response in progress"]')).toHaveCount(0);

  const summary = panel.getByTestId("agent-attention-summary");
  await expect(summary).toBeVisible();
  await expect(summary.getByRole("heading", { name: "Today needs attention" })).toBeVisible();
  await expect(summary.getByRole("region", { name: "Action required" })).toContainText("Expense reviews");
  await expect(summary.getByRole("region", { name: "Time sensitive" })).toContainText("Errand due today");
  await expect(summary.getByLabel("Attention coverage")).toContainText("All selected checks completed");
  await expect(summary.getByRole("button", { name: /complete|save|buy|order/i })).toHaveCount(0);

  await summary.getByRole("button", { name: "View expenses", exact: true }).click();
  await expect(page).toHaveURL(/workspace=expenses.*tab=review/);
  await expect(page.getByRole("heading", { name: "Needs your attention" })).toBeVisible();
  expect(writes).toEqual([]);
});

test("detergent page context renders coherent replenishment and deal evidence", async ({ page }, testInfo) => {
  skipUnlessChromium(testInfo);
  const writes = installForbiddenWriteRecorder(page);
  await mockAgentApp(page, { messages: messagesForResponse(NEED_AND_DEAL_RESPONSE) });
  await mockHouseholdItem(page);
  await installAgentStream(page, {
    events: semanticEvents(
      NEED_AND_DEAL_RESPONSE,
      ["replenishment", "deals"],
      "Any useful deal for this?",
    ),
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Household", exact: true }).click();
  await page.getByRole("button", { name: "Staples", exact: true }).click();
  await page.getByTestId("agent-context-household-item-71").click();
  const panel = await openDesktopAgent(page);
  await expect(panel).toContainText("Using context: Household Staples · Laundry detergent");
  await sendFrom(page, "agent-panel", "Any useful deal for this?");

  await expect(panel).toContainText("likely due based on confirmed purchase evidence");
  await expect(panel).toContainText("15% off laundry care");
  await expect(panel).toContainText("Relevant to a need");
  const [call] = await agentStreamCalls(page);
  expect(call.body?.page_context).toEqual({
    schema_version: "1.0",
    surface: "household_staples",
    entity: { kind: "household_item", public_id: "71" },
  });
  await expect(panel.getByRole("button", { name: /save|buy|order/i })).toHaveCount(0);
  expect(writes).toEqual([]);
});

test("Food & Dining context keeps canonical aggregate separate from supporting transactions", async ({ page }, testInfo) => {
  skipUnlessChromium(testInfo);
  const writes = installForbiddenWriteRecorder(page);
  await mockAgentApp(page, { messages: messagesForResponse(SPENDING_AND_TRANSACTIONS_RESPONSE) });
  await mockSpendingInsights(page);
  await installAgentStream(page, {
    events: semanticEvents(
      SPENDING_AND_TRANSACTIONS_RESPONSE,
      ["spending", "transactions"],
      "Why did this increase and which transactions drove it?",
    ),
  });

  await page.goto("/");
  await page.getByRole("button", { name: "insights", exact: true }).click();
  await page.locator("summary").filter({ hasText: "Filters" }).click();
  await page.getByRole("combobox", { name: "Category", exact: true }).selectOption("Food & Dining");
  const panel = await openDesktopAgent(page);
  await expect(panel).toContainText(/Using context: Insights · Food & Dining/);
  await sendFrom(page, "agent-panel", "Why did this increase and which transactions drove it?");

  await expect(panel).toContainText("The aggregate remains USD 412.00");
  await expect(panel).toContainText("$412.00");
  await expect(panel).toContainText("Prior period $326.00");
  await expect(panel).toContainText("Local Bistro");
  await expect(panel).toContainText("$168.00");
  await expect(panel).toContainText("Corner Cafe");
  await expect(panel).toContainText("$112.00");
  const [call] = await agentStreamCalls(page);
  expect(call.body?.page_context).toEqual(expect.objectContaining({
    schema_version: "1.0",
    surface: "expense_insights",
    filters: expect.objectContaining({ category: "Food & Dining", spend_basis: "card" }),
  }));
  expect(writes).toEqual([]);
});

test("one failed domain remains visibly partial without fabricating its evidence", async ({ page }, testInfo) => {
  skipUnlessChromium(testInfo);
  const writes = installForbiddenWriteRecorder(page);
  await mockAgentApp(page, { messages: messagesForResponse(PARTIAL_RESPONSE) });
  const events = semanticEvents(
    PARTIAL_RESPONSE,
    ["transactions", "replenishment", "deals"],
    "What needs my attention today?",
    ["deals"],
  );
  expect(events).not.toEqual(expect.arrayContaining([
    expect.objectContaining({ type: "tool_completed", activity: "deals" }),
  ]));
  await installAgentStream(page, {
    events,
  });

  await page.goto("/");
  const panel = await openDesktopAgent(page);
  await sendFrom(page, "agent-panel", "What needs my attention today?");

  const summary = panel.getByTestId("agent-attention-summary");
  await expect(summary).toContainText("Expense review");
  await expect(summary).toContainText("Household item likely due");
  await expect(summary).toContainText("Partial");
  await expect(summary.getByLabel("Attention coverage")).toContainText("Couldn't check deals right now");
  await expect(panel).not.toContainText("Deal results are ready");
  await expect(panel).not.toContainText("15% off laundry care");
  await expect(panel.getByRole("alert")).toHaveCount(0);
  expect(writes).toEqual([]);
});

test("broad attention hierarchy is readable and internally scrollable at 320px", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chromium", "Mobile Day 6 attention coverage");
  await page.setViewportSize({ width: 320, height: 700 });
  const writes = installForbiddenWriteRecorder(page);
  await mockAgentApp(page, { messages: messagesForResponse(MOBILE_ATTENTION_RESPONSE) });
  await installAgentStream(page, {
    events: semanticEvents(
      MOBILE_ATTENTION_RESPONSE,
      ["transactions", "replenishment", "errands"],
      "What should I know before I go out today?",
    ),
  });

  await page.goto("/");
  const mobileNavigation = page.getByRole("navigation", { name: "Primary mobile navigation" });
  await mobileNavigation.getByRole("button", { name: "Agent", exact: true }).click();
  const agentPage = page.getByTestId("agent-page");
  await sendFrom(page, "agent-page", "What should I know before I go out today?");

  const summary = agentPage.getByTestId("agent-attention-summary");
  await expect(summary.getByRole("region", { name: "Action required" })).toBeVisible();
  await expect(summary.getByRole("region", { name: "Time sensitive" })).toBeVisible();
  await expect(summary.getByRole("region", { name: "Useful to know" })).toBeVisible();
  await expect(summary.getByLabel("Attention coverage")).toContainText("Checked 3 ExpenseOps areas");
  const helpful = agentPage.getByRole("button", { name: "Helpful", exact: true });
  await expect(helpful).toBeVisible();
  expect((await helpful.boundingBox())?.height).toBeGreaterThanOrEqual(44);
  await assertNoHorizontalOverflow(page, agentPage);
  await mobileNavigation.getByRole("button", { name: "Expenses", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Expense Review" })).toBeVisible();
  expect(writes).toEqual([]);
});

test("paired need and deal cards keep badges and semantic Open controls inside 320px", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chromium", "Mobile Day 6 paired-card coverage");
  await page.setViewportSize({ width: 320, height: 700 });
  await mockAgentApp(page, { messages: messagesForResponse(NEED_AND_DEAL_RESPONSE) });
  await installAgentStream(page, {
    events: semanticEvents(
      NEED_AND_DEAL_RESPONSE,
      ["replenishment", "deals"],
      "Do I need detergent, and is there a useful deal?",
    ),
  });

  await page.goto("/");
  await page.getByRole("navigation", { name: "Primary mobile navigation" })
    .getByRole("button", { name: "Agent", exact: true })
    .click();
  const agentPage = page.getByTestId("agent-page");
  await sendFrom(page, "agent-page", "Do I need detergent, and is there a useful deal?");

  await expect(agentPage.getByText("Likely due", { exact: true })).toBeVisible();
  await expect(agentPage.getByText("Relevant to a need", { exact: true })).toBeVisible();
  await expect(agentPage.getByRole("button", { name: "Open Laundry detergent" })).toBeVisible();
  await expect(agentPage.getByRole("button", { name: "Open Target deal" })).toBeVisible();
  await expect(agentPage.getByRole("button", { name: "Not helpful", exact: true })).toBeVisible();
  await assertNoHorizontalOverflow(page, agentPage);
});

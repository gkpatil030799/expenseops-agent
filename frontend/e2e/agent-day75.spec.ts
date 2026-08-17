import { expect, test, type Page, type TestInfo } from "@playwright/test";

import type { AgentStructuredResponse } from "../src/agent/contracts";
import type { SpendingBasis } from "../src/components/InsightsDashboard";
import {
  agentStreamCalls,
  canonicalMessages,
  installAgentStream,
  mockAgentApp,
  successfulEvents,
} from "./fixtures/agent";

const ORIGINAL_QUERY = "are my spendings increased compared to last week ?";

const CORRECTED_SPENDING_RESPONSE: AgentStructuredResponse = {
  schema_version: "1.0",
  blocks: [
    {
      type: "text",
      text: "Eligible purchase spending increased from $400.00 last week to $500.00 this week.",
    },
    {
      type: "spending_summary",
      title: "This week compared with last week",
      start_date: "2026-08-10",
      end_date: "2026-08-16",
      currency_code: "USD",
      spend_basis: "card",
      total_cents: 50_000,
      previous_total_cents: 40_000,
      credits_cents: 10_000,
      previous_credits_cents: 0,
      unknown_credit_share_transactions: 0,
      previous_unknown_credit_share_transactions: 0,
      unknown_share_transactions: 0,
      previous_unknown_share_transactions: 0,
      change_percent: 25,
      highlights: ["Credits are reported separately from purchase spending."],
      top_categories: [
        {
          name: "Food & Dining",
          amount_cents: 30_000,
          transaction_count: 3,
          percentage: 60,
          previous_amount_cents: 20_000,
        },
      ],
      top_merchants: [
        {
          name: "Synthetic Cafe",
          amount_cents: 20_000,
          transaction_count: 2,
          percentage: 40,
          previous_amount_cents: 10_000,
        },
      ],
    },
  ],
};

const ACTUAL_SHARE_SPENDING_RESPONSE: AgentStructuredResponse = {
  schema_version: "1.0",
  blocks: CORRECTED_SPENDING_RESPONSE.blocks.map((block) =>
    block.type === "spending_summary"
      ? {
          ...block,
          title: "My share this week compared with last week",
          spend_basis: "actual_share" as const,
          credits_cents: 2_500,
          previous_credits_cents: 1_200,
          unknown_credit_share_transactions: 1,
          previous_unknown_credit_share_transactions: 2,
          unknown_share_transactions: 0,
          previous_unknown_share_transactions: 3,
          change_percent: null,
        }
      : block,
  ),
};

const ADAPTED_HISTORICAL_RESPONSE: AgentStructuredResponse = {
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

function skipMobileAgentPanel(testInfo: TestInfo): void {
  test.skip(testInfo.project.name === "mobile-chromium", "Desktop Agent panel coverage");
}

async function openDesktopAgent(page: Page) {
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  const panel = page.getByTestId("agent-panel");
  await expect(panel).toBeVisible();
  return panel;
}

test("the original comparison wording renders purchase spend and separate credits", async ({
  page,
}, testInfo) => {
  skipMobileAgentPanel(testInfo);
  await mockAgentApp(page, {
    initialConversation: true,
    messages: (calls) => {
      const call = calls.at(-1);
      if (!call?.body?.client_message_id) return [];
      return canonicalMessages(
        CORRECTED_SPENDING_RESPONSE,
        call.body.text || ORIGINAL_QUERY,
        call.body.client_message_id,
      );
    },
  });
  await installAgentStream(page, {
    events: successfulEvents({
      response: CORRECTED_SPENDING_RESPONSE,
      deltas: ["Eligible purchase spending comparison is ready."],
      activity: "spending",
    }),
  });

  await page.goto("/");
  const panel = await openDesktopAgent(page);
  const streamCallsBeforeSend = (await agentStreamCalls(page)).length;
  await panel.getByLabel("Ask ExpenseOps Agent").fill(ORIGINAL_QUERY);
  await panel.getByLabel("Ask ExpenseOps Agent").press("Enter");

  await expect.poll(async () => (await agentStreamCalls(page)).length).toBe(streamCallsBeforeSend + 1);
  const answers = panel.getByLabel("ExpenseOps Agent response", { exact: true });
  await expect(answers).toHaveCount(1);
  const answer = answers.last();
  await expect(answer.getByText("Total card spend", { exact: true })).toBeVisible();
  await expect(answer.getByText("$500.00", { exact: true }).first()).toBeVisible();
  await expect(answer.getByText(/Prior period \$400\.00/)).toBeVisible();
  await expect(answer.getByText("Card credits reported separately $100.00", { exact: true })).toBeVisible();
  await expect(answer).not.toContainText("unsupported Agent response");
  await expect(panel.getByLabel("ExpenseOps Agent response in progress")).toHaveCount(0);
  expect((await agentStreamCalls(page)).at(-1)?.body?.text).toBe(ORIGINAL_QUERY);
});

test("an actual-share Agent summary labels attributable credits and both omission periods", async ({
  page,
}, testInfo) => {
  skipMobileAgentPanel(testInfo);
  await mockAgentApp(page, {
    initialConversation: true,
    messages: canonicalMessages(
      ACTUAL_SHARE_SPENDING_RESPONSE,
      "What was my actual share this week compared with last week?",
    ),
  });

  await page.goto("/");
  const panel = await openDesktopAgent(page);
  const answer = panel.getByLabel("ExpenseOps Agent response", { exact: true });
  await expect(answer.getByText("My actual share", { exact: true })).toBeVisible();
  await expect(
    answer.getByText("Attributable credits reported separately $25.00", { exact: true }),
  ).toBeVisible();
  await expect(answer.getByText("Prior attributable credits $12.00", { exact: true })).toBeVisible();
  await expect(answer.getByText(/1 current shared credit was omitted/)).toBeVisible();
  await expect(answer.getByText(/2 prior shared credits were omitted from the comparison/)).toBeVisible();
  await expect(answer.getByText(/3 prior shared purchases were omitted from the comparison/)).toBeVisible();
  await expect(answer).not.toContainText("25%");
  await expect(answer).not.toContainText("unsupported Agent response");
});

test("server-adapted historical net-spend answers remain readable without old totals", async ({
  page,
}, testInfo) => {
  skipMobileAgentPanel(testInfo);
  await mockAgentApp(page, {
    initialConversation: true,
    messages: canonicalMessages(ADAPTED_HISTORICAL_RESPONSE, ORIGINAL_QUERY),
  });

  await page.goto("/");
  const panel = await openDesktopAgent(page);
  const answer = panel.getByLabel("ExpenseOps Agent response", { exact: true });
  await expect(
    answer.getByText(
      "This saved spending answer used retired net-spend semantics and is not shown as current financial truth.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(answer.getByText("Recalculate this spending answer", { exact: true })).toBeVisible();
  await expect(
    answer.getByText(
      "Ask the question again to calculate eligible purchase spending with credits reported separately.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(answer).not.toContainText("unsupported Agent response");
});

type InsightsSummary = {
  total_cents: number;
  personal_cents: number;
  shared_cents: number;
  classified_cents: number;
  unreviewed_cents: number;
  credits_cents: number;
  unknown_share_transactions: number;
  unknown_credit_share_transactions: number;
  transaction_count: number;
  average_cents: number;
};

function spendingInsights(
  summary: InsightsSummary,
  options: {
    spendBasis?: SpendingBasis;
    comparison?: Partial<InsightsSummary>;
  } = {},
) {
  return {
    range: {
      start_date: "2026-08-10",
      end_date: "2026-08-16",
      previous_start_date: "2026-08-03",
      previous_end_date: "2026-08-09",
      granularity: "day",
    },
    scope: {
      currency: "USD",
      available_currencies: ["USD"],
      excluded_other_currency_transactions: 0,
      spend_basis: options.spendBasis ?? "card",
      viewer_share_identity_connected: true,
      pending_transactions_excluded: true,
    },
    summary,
    comparison: {
      total_cents: 40_000,
      personal_cents: 40_000,
      shared_cents: 0,
      classified_cents: 40_000,
      unreviewed_cents: 0,
      credits_cents: 0,
      unknown_share_transactions: 0,
      unknown_credit_share_transactions: 0,
      transaction_count: 4,
      average_cents: 10_000,
      ...options.comparison,
    },
    trend: [
      {
        period: "2026-08-10",
        total_cents: summary.total_cents,
        personal_cents: summary.personal_cents,
        shared_cents: summary.shared_cents,
        transactions: summary.transaction_count,
      },
    ],
    category_breakdown: summary.transaction_count
      ? [
          {
            name: "Food & Dining",
            amount_cents: 30_000,
            transaction_count: 3,
            percentage: 60,
            previous_amount_cents: 20_000,
          },
          {
            name: "Uncategorized",
            amount_cents: 19_000,
            transaction_count: 1,
            percentage: 38,
            previous_amount_cents: 20_000,
          },
          {
            name: "Travel",
            amount_cents: 1_000,
            transaction_count: 1,
            percentage: 2,
            previous_amount_cents: 0,
          },
        ]
      : [],
    subcategory_breakdown: [],
    merchant_breakdown: summary.transaction_count
      ? [
          { name: "Synthetic Cafe", amount_cents: 20_000, transaction_count: 2 },
          { name: "Synthetic Grocer", amount_cents: 20_000, transaction_count: 2 },
          { name: "Synthetic Transit", amount_cents: 10_000, transaction_count: 1 },
        ]
      : [],
    personal_shared: { personal: summary.personal_cents, shared: summary.shared_cents },
    shared_people: [],
    shared_groups: [],
    category_trend: summary.transaction_count
      ? [{ period: "2026-08-10", categories: { "Food & Dining": 30_000, Uncategorized: 19_000, Travel: 1_000 } }]
      : [],
    notable_changes: [],
    accounts: [],
    categories: summary.transaction_count ? ["Food & Dining", "Travel", "Uncategorized"] : [],
    merchants: summary.transaction_count
      ? ["Synthetic Cafe", "Synthetic Grocer", "Synthetic Transit"]
      : [],
    data_quality: {
      unknown_share_transactions: summary.unknown_share_transactions,
      unknown_credit_share_transactions: summary.unknown_credit_share_transactions,
      unreviewed_cents: summary.unreviewed_cents,
      pending_review_cents: summary.unreviewed_cents,
      uncategorized_cents: summary.transaction_count ? 19_000 : 0,
      pending_transactions_excluded: true,
    },
  };
}

async function mockInsights(
  page: Page,
  response: ReturnType<typeof spendingInsights>,
  actualShareResponse?: ReturnType<typeof spendingInsights>,
) {
  await page.route("**/api/**", (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/api/context") {
      return route.fulfill({
        json: {
          user: {
            id: 1,
            email: "day75@example.test",
            display_name: "Day 7.5",
            avatar_url: null,
          },
          workspace: { id: 1, name: "Synthetic workspace", workspace_type: "household" },
          features: { agent: { enabled: false, read_only: true } },
        },
      });
    }
    return route.fulfill({ status: 503, json: { detail: "Unavailable in Day 7.5 test" } });
  });
  await page.route("**/api/insights/spending?**", (route) => {
    const basis = new URL(route.request().url()).searchParams.get("spend_basis");
    return route.fulfill({
      json: basis === "actual_share" && actualShareResponse ? actualShareResponse : response,
    });
  });
  await page.route(/\/transactions(?:\?|$)/, (route) => route.fulfill({ json: [] }));
  await page.route("**/splitwise/me", (route) => route.fulfill({ json: {} }));
  await page.route("**/ai/memory", (route) => route.fulfill({ json: [] }));
  await page.route("**/ai/memory/settings", (route) => route.fulfill({ json: { transaction_learning_enabled: true } }));
  await page.route("**/ai/memory/metrics", (route) => route.fulfill({ json: { shown: 0, accepted: 0, edited: 0, rejected: 0, agreement_rate: null, correction_rate: null } }));
}

test("Insights ranks purchase spend and reports credits outside Total spend", async ({ page }) => {
  await mockInsights(
    page,
    spendingInsights({
      total_cents: 50_000,
      personal_cents: 30_000,
      shared_cents: 15_000,
      classified_cents: 45_000,
      unreviewed_cents: 5_000,
      credits_cents: 10_000,
      unknown_share_transactions: 0,
      unknown_credit_share_transactions: 0,
      transaction_count: 5,
      average_cents: 10_000,
    }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: /insights/i, exact: true }).click();
  const overview = page.getByLabel("Spending overview");
  await expect(overview.getByText("$500", { exact: true }).first()).toBeVisible();
  await expect(overview.getByText("Card credits", { exact: true })).toBeVisible();
  await expect(overview.getByText("$100", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /Food & Dining, \$300/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Uncategorized, \$190/ })).toBeVisible();
  await expect(page.getByRole("listitem", { name: /Synthetic Cafe, \$200/ })).toBeVisible();
  await expect(page.getByRole("listitem", { name: /Synthetic Grocer, \$200/ })).toBeVisible();
  await expect(page.getByRole("listitem", { name: /Synthetic Transit, \$100/ })).toBeVisible();
  await page.getByText(/^Data notes/).click();
  await expect(page.getByText("$190 is included in Total spend as Uncategorized.", { exact: true })).toBeVisible();
  await page.getByText("How this total is calculated", { exact: true }).click();
  await expect(page.getByText(/Credits, including refunds, are reported separately/)).toBeVisible();
});

test("a credit-only period still renders zero Total spend and the positive credit", async ({ page }) => {
  await mockInsights(
    page,
    spendingInsights({
      total_cents: 0,
      personal_cents: 0,
      shared_cents: 0,
      classified_cents: 0,
      unreviewed_cents: 0,
      credits_cents: 7_500,
      unknown_share_transactions: 0,
      unknown_credit_share_transactions: 0,
      transaction_count: 0,
      average_cents: 0,
    }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: /insights/i, exact: true }).click();
  const overview = page.getByLabel("Spending overview");
  await expect(overview.getByText("$0", { exact: true }).first()).toBeVisible();
  await expect(overview.getByText("$75", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("No spending data for this period")).toHaveCount(0);
});

test("a zero-current period still renders its non-zero prior purchase comparison", async ({ page }) => {
  await mockInsights(
    page,
    spendingInsights({
      total_cents: 0,
      personal_cents: 0,
      shared_cents: 0,
      classified_cents: 0,
      unreviewed_cents: 0,
      credits_cents: 0,
      unknown_share_transactions: 0,
      unknown_credit_share_transactions: 0,
      transaction_count: 0,
      average_cents: 0,
    }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: /insights/i, exact: true }).click();
  const overview = page.getByLabel("Spending overview");
  await expect(overview.getByText("$0", { exact: true }).first()).toBeVisible();
  await expect(overview.getByText(/−\$400 vs previous period/).first()).toBeVisible();
  await expect(page.getByText("No spending data for this period")).toHaveCount(0);
});

test("actual-share Insights disclose current and prior shared credits omitted without allocation", async ({
  page,
}) => {
  const cardResponse = spendingInsights({
    total_cents: 50_000,
    personal_cents: 30_000,
    shared_cents: 15_000,
    classified_cents: 45_000,
    unreviewed_cents: 5_000,
    credits_cents: 3_000,
    unknown_share_transactions: 0,
    unknown_credit_share_transactions: 0,
    transaction_count: 5,
    average_cents: 10_000,
  });
  const actualShareResponse = spendingInsights(
    {
      total_cents: 50_000,
      personal_cents: 30_000,
      shared_cents: 15_000,
      classified_cents: 45_000,
      unreviewed_cents: 5_000,
      credits_cents: 2_500,
      unknown_share_transactions: 0,
      unknown_credit_share_transactions: 1,
      transaction_count: 5,
      average_cents: 10_000,
    },
    {
      spendBasis: "actual_share",
      comparison: {
        credits_cents: 1_200,
        unknown_share_transactions: 3,
        unknown_credit_share_transactions: 2,
      },
    },
  );
  actualShareResponse.notable_changes = [
    {
      kind: "category",
      direction: "up",
      label: "Must not render from incomplete comparison",
      amount_cents: 10_000,
      detail: "+$100 vs previous period",
    },
  ];
  await mockInsights(page, cardResponse, actualShareResponse);

  await page.goto("/");
  await page.getByRole("button", { name: /insights/i, exact: true }).click();
  await expect(page.getByLabel("Spending overview").getByText("Card credits", { exact: true })).toBeVisible();
  await page.locator('select[id^="insights-spending-basis"]:visible').selectOption("actual_share");
  const overview = page.getByLabel("Spending overview");
  await expect(overview.getByText("Attributable credits", { exact: true })).toBeVisible();
  await expect(overview.getByText("Confirmed transactions", { exact: true })).toBeVisible();
  await expect(
    overview.getByText("Confirmed allocations only: +$100 vs previous period", { exact: true }).first(),
  ).toBeVisible();
  await expect(overview).not.toContainText("(+25%)");
  await expect(page.getByText("Classified actual share", { exact: true })).toBeVisible();
  await expect(page.getByText("Comparison incomplete", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Change analysis uses only confirmed allocations.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("No major changes detected", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Must not render from incomplete comparison", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Food & Dining, \$300/ })).not.toContainText(
    "vs previous period",
  );
  await expect(page.getByText(/3 prior-period shared purchases .* omitted from the comparison/)).toBeVisible();
  await expect(page.getByText(/1 current-period shared credit .* omitted from Attributable credits/)).toBeVisible();
  await expect(page.getByText(/2 prior-period shared credits .* omitted from the comparison/)).toBeVisible();
});

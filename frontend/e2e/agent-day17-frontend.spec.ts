import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";

import type { AgentStructuredResponse } from "../src/agent/contracts";
import { canonicalMessages, mockAgentApp } from "./fixtures/agent";

const LONG_REPLENISHMENT_NAME =
  "Organic grass-fed vanilla whey protein family-size refill pouch";
const LONG_STAPLE_NAME =
  "Ultra-absorbent recycled paper towel twelve-roll household pack";
const LONG_CANDIDATE_NAME =
  "Cold-brew coffee concentrate extra-large recyclable multi-serve bottle";
const LONG_CREATED_CANDIDATE_NAME =
  "Plant-based dishwasher tablets fragrance-free bulk refill carton";
const SIXTH_MERCHANT = "Neighborhood International Farmers Market and Bakery";

const RESPONSE: AgentStructuredResponse = {
  schema_version: "1.0",
  blocks: [
    {
      type: "text",
      text: "The household evidence and six requested merchants are shown below.",
    },
    {
      type: "replenishment_summary",
      title: "Household items to check",
      items: [
        {
          public_id: "101",
          name: LONG_REPLENISHMENT_NAME,
          predicted_due_on: "2026-08-20",
          confidence: 0.82,
          confidence_level: "high",
          evidence_basis: "purchase_pattern",
          due_state: "likely_due",
          reason:
            "Likely due based on several confirmed purchases, adjusted for the quantity in the latest receipt.",
          quantity: "1",
          unit: "pouch",
          last_acquired_on: "2026-07-18",
          confirmed_acquisition_count: 4,
        },
      ],
      acquisition_history: [],
      acquisition_history_truncated: false,
      total_count: 1,
      items_truncated: false,
    },
    {
      type: "classification_activity_summary",
      block_version: "1.1",
      title: "What ExpenseOps learned today",
      view: "summary",
      start_date: "2026-08-12",
      end_date: "2026-08-18",
      timezone: "America/Phoenix",
      counts: {
        transactions: 0,
        receipt_items: 0,
        categories: 0,
        new_categories: 0,
        receipt_matches: 0,
        new_household_items: 1,
        staple_candidates: 2,
        aliases: 1,
        cadence_updates: 1,
        uncertain: 0,
      },
      transactions: [],
      receipt_items: [],
      categories: [],
      new_categories: [],
      receipt_matches: [],
      new_household_items: [
        {
          created_by_decision_public_id: "decision-staple-1",
          public_id: "102",
          name: LONG_STAPLE_NAME,
          parent_category: "household_home",
          replenishment_eligibility: "replenishable",
          classification_confidence: 0.94,
          cadence_source: "learning",
          cadence_days: null,
          cadence_min_days: 20,
          cadence_max_days: 30,
          cadence_confidence: 0.76,
          activity_at: "2026-08-18T16:00:00Z",
        },
      ],
      staple_candidates: [
        {
          decision_public_id: "decision-candidate-1",
          receipt_item_public_id: "receipt-item-201",
          receipt_public_id: "201",
          source_available: true,
          merchant: "Neighborhood International Farmers Market and Bakery",
          name: LONG_CANDIDATE_NAME,
          parent_category: "food_dining",
          subcategory: "Coffee",
          concept: "Cold-brew concentrate",
          activity_type: "coffee_beverage",
          replenishment_eligibility: "potentially_replenishable",
          confidence: 0.79,
          confidence_band: "medium",
          decision_state: "provisional",
          created_household_item: false,
          household_item_public_id: null,
          household_item_name: null,
          learning_state: "candidate",
          applied_at: "2026-08-18T15:30:00Z",
        },
        {
          decision_public_id: "decision-candidate-2",
          receipt_item_public_id: "receipt-item-202",
          receipt_public_id: "202",
          source_available: true,
          merchant: "Arizona Home Supply Warehouse",
          name: LONG_CREATED_CANDIDATE_NAME,
          parent_category: "household_home",
          subcategory: "Cleaning supplies",
          concept: "Dishwasher tablets",
          activity_type: "household_consumable",
          replenishment_eligibility: "replenishable",
          confidence: 0.96,
          confidence_band: "high",
          decision_state: "final",
          created_household_item: true,
          household_item_public_id: "104",
          household_item_name: "Dishwasher tablets",
          learning_state: "tracked",
          applied_at: "2026-08-18T15:45:00Z",
        },
      ],
      aliases: [
        {
          public_id: "alias-1",
          concept: "Cold-brew concentrate",
          parent_category: "food_dining",
          raw_pattern: "CB CONC XL BTL",
          merchant: "Neighborhood International Farmers Market and Bakery",
          confidence: 0.93,
          authority: "receipt_evidence",
          active: true,
          created_at: "2026-08-18T15:30:00Z",
        },
      ],
      cadence_updates: [
        {
          created_by_decision_public_id: "decision-cadence-1",
          public_id: "103",
          name: "Dishwasher tablets extra-large ninety-six count box",
          parent_category: "household_home",
          replenishment_eligibility: "replenishable",
          classification_confidence: 0.91,
          cadence_source: "observed",
          cadence_days: 28,
          cadence_min_days: null,
          cadence_max_days: null,
          cadence_confidence: 0.88,
          activity_at: "2026-08-18T15:00:00Z",
        },
      ],
      uncertain: [],
      truncated_sections: [],
    },
    {
      type: "spending_summary",
      focus: "top_merchants",
      requested_limit: 6,
      title: "Top six merchants",
      start_date: "2026-08-01",
      end_date: "2026-08-18",
      currency_code: "USD",
      spend_basis: "card",
      total_cents: 12_345_678,
      previous_total_cents: null,
      credits_cents: 0,
      previous_credits_cents: 0,
      unknown_share_transactions: 0,
      previous_unknown_share_transactions: 0,
      unknown_credit_share_transactions: 0,
      previous_unknown_credit_share_transactions: 0,
      change_percent: null,
      highlights: [],
      top_categories: [],
      top_merchants: [
        ["Metropolitan Grocery Cooperative", 4_200_000, 5, 34],
        ["Synthetic Cafe", 2_800_000, 4, 22.7],
        ["Railway Market", 1_900_000, 3, 15.4],
        ["Downtown Pharmacy and Wellness", 1_500_000, 2, 12.2],
        ["Arizona Home Supply Warehouse", 1_100_000, 2, 8.9],
        [SIXTH_MERCHANT, 845_678, 1, 6.8],
      ].map(([name, amountCents, transactionCount, percentage]) => ({
        name: String(name),
        amount_cents: Number(amountCents),
        transaction_count: Number(transactionCount),
        percentage: Number(percentage),
        previous_amount_cents: null,
      })),
    },
  ],
};

async function openAgent(page: Page, testInfo: TestInfo): Promise<Locator> {
  await mockAgentApp(page, {
    initialConversation: true,
    messages: canonicalMessages(RESPONSE, "Show household learning and my top six merchants"),
  });
  await page.goto("/");
  if (testInfo.project.name === "mobile-chromium") {
    await page.getByRole("navigation", { name: "Primary mobile navigation" })
      .getByRole("button", { name: "Agent", exact: true })
      .click();
    return page.getByTestId("agent-page");
  }
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  return page.getByTestId("agent-panel");
}

async function expectResponsiveEvidenceRows(agent: Locator): Promise<void> {
  const replenishmentRow = agent.getByTestId("agent-replenishment-row").filter({
    hasText: LONG_REPLENISHMENT_NAME,
  });
  const learningRow = agent.getByTestId("agent-classification-row").filter({
    hasText: LONG_STAPLE_NAME,
  });
  const candidateRow = agent.getByTestId("agent-classification-row").filter({
    hasText: LONG_CANDIDATE_NAME,
  });
  const createdCandidateRow = agent.getByTestId("agent-classification-row").filter({
    hasText: LONG_CREATED_CANDIDATE_NAME,
  });
  for (const row of [replenishmentRow, learningRow, candidateRow, createdCandidateRow]) {
    await expect(row).toBeVisible();
    const dimensions = await row.evaluate((element) => {
      const rowBox = element.getBoundingClientRect();
      const firstChild = element.firstElementChild;
      const childBoxes = Array.from(element.children).map((child) => {
        const box = child.getBoundingClientRect();
        return { left: box.left, right: box.right, top: box.top, bottom: box.bottom };
      });
      return {
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
        contentWidth: firstChild?.getBoundingClientRect().width || 0,
        overflowWrap: firstChild ? getComputedStyle(firstChild.querySelector("p") || firstChild).overflowWrap : "",
        row: { left: rowBox.left, right: rowBox.right, top: rowBox.top, bottom: rowBox.bottom },
        children: childBoxes,
      };
    });
    expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
    expect(dimensions.contentWidth).toBeGreaterThan(120);
    expect(dimensions.overflowWrap).not.toBe("anywhere");
    for (const child of dimensions.children) {
      expect(child.left).toBeGreaterThanOrEqual(dimensions.row.left - 0.5);
      expect(child.right).toBeLessThanOrEqual(dimensions.row.right + 0.5);
      expect(child.top).toBeGreaterThanOrEqual(dimensions.row.top - 0.5);
      expect(child.bottom).toBeLessThanOrEqual(dimensions.row.bottom + 0.5);
    }
  }

  await expect(agent.getByRole("button", { name: `Open ${LONG_REPLENISHMENT_NAME}` })).toBeVisible();
  await expect(agent.getByRole("button", { name: `Open ${LONG_STAPLE_NAME}` })).toBeVisible();
  await expect(agent.getByRole("button", { name: `Open ${LONG_CANDIDATE_NAME}` })).toBeVisible();
  await expect(agent.getByRole("button", { name: `Open ${LONG_CREATED_CANDIDATE_NAME}` })).toBeVisible();
  await expect(replenishmentRow.getByText("Likely due", { exact: true })).toBeVisible();
  await expect(learningRow.getByText("Replenishable", { exact: true })).toBeVisible();
  await expect(candidateRow.getByText("Learning · not due", { exact: true })).toBeVisible();
  await expect(candidateRow).toContainText("No household item created");
  await expect(candidateRow).toContainText("79% confidence");
  await expect(createdCandidateRow.getByText("Tracked", { exact: true })).toBeVisible();
  await expect(createdCandidateRow).toContainText("Household item created");
  await expect(createdCandidateRow).toContainText("96% confidence");
  await expect(agent.getByText("CB CONC XL BTL → Cold-brew concentrate", { exact: true })).toBeVisible();
}

async function expectRankedMerchants(agent: Locator): Promise<void> {
  const spending = agent.getByTestId("agent-spending-summary");
  const ranking = spending.getByRole("list", { name: "Top merchants" });
  await expect(ranking.getByRole("listitem")).toHaveCount(6);
  await expect(ranking.getByText(SIXTH_MERCHANT, { exact: false })).toBeVisible();
  await expect(ranking.getByText("1 purchase · 6.8% of spend", { exact: true })).toBeVisible();
  await expect(ranking.getByText("$8,456.78", { exact: true })).toBeVisible();
  await expect(spending.getByRole("heading", { name: "Top categories" })).toHaveCount(0);
  const amount = ranking.getByText("$8,456.78", { exact: true });
  const amountMetrics = await amount.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    textOverflow: getComputedStyle(element).textOverflow,
  }));
  expect(amountMetrics.scrollWidth).toBeLessThanOrEqual(amountMetrics.clientWidth);
  expect(amountMetrics.textOverflow).not.toBe("ellipsis");
}

async function expectNoAgentOverflow(page: Page, agent: Locator): Promise<void> {
  const metrics = await agent.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    documentClientWidth: document.documentElement.clientWidth,
    documentScrollWidth: document.documentElement.scrollWidth,
  }));
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth);
  expect(metrics.documentScrollWidth).toBeLessThanOrEqual(metrics.documentClientWidth);
}

test("1024px companion keeps evidence, actions, and exact rankings readable", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "Companion-width coverage runs in Chromium");
  await page.setViewportSize({ width: 1024, height: 900 });
  const agent = await openAgent(page, testInfo);
  await expect(agent).toBeVisible();
  const panelBox = await agent.boundingBox();
  expect(panelBox?.width).toBeLessThanOrEqual(400);
  await expectResponsiveEvidenceRows(agent);
  await expectRankedMerchants(agent);
  await expectNoAgentOverflow(page, agent);

  const results = await new AxeBuilder({ page })
    .include('[data-testid="agent-panel"]')
    .withTags(["wcag2a", "wcag2aa"])
    .analyze();
  expect(results.violations.filter((violation) =>
    ["critical", "serious"].includes(violation.impact || ""),
  )).toEqual([]);
});

for (const width of [320, 375, 390]) {
  test(`mobile Agent evidence and rankings remain readable at ${width}px`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile-chromium", "Mobile-width coverage");
    await page.setViewportSize({ width, height: 844 });
    const agent = await openAgent(page, testInfo);
    await expect(agent).toBeVisible();
    await expectResponsiveEvidenceRows(agent);
    await expectRankedMerchants(agent);
    await expectNoAgentOverflow(page, agent);

    const results = await new AxeBuilder({ page })
      .include('[data-testid="agent-page"]')
      .withTags(["wcag2a", "wcag2aa"])
      .analyze();
    expect(results.violations.filter((violation) =>
      ["critical", "serious"].includes(violation.impact || ""),
    )).toEqual([]);
  });
}

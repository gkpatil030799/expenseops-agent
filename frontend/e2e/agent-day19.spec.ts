import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import type { AgentActionConfirmationBlock } from "../src/agent/contracts";
import { mockAgentApp } from "./fixtures/agent";

const SESSION_ID = "review-session-day19-1";
const CANDIDATE_1 = {
  review_item_public_id: "9c6b7e2a-1e4a-4b7e-9a2a-1a2b3c4d5e01",
  transaction_id: 26,
  merchant: "Costco Wholesale",
  amount_cents: 14218,
};
const CANDIDATE_2 = {
  review_item_public_id: "9c6b7e2a-1e4a-4b7e-9a2a-1a2b3c4d5e02",
  transaction_id: 27,
  merchant: "REI Co-op",
  amount_cents: 8999,
};

function reviewInboxPayload() {
  return {
    items: [
      {
        public_id: CANDIDATE_1.review_item_public_id,
        kind: "transaction_review",
        state: "open",
        unread: true,
        seen_at: null,
        created_at: "2026-08-19T10:00:00Z",
        updated_at: "2026-08-19T10:00:00Z",
        available_actions: ["personal", "recommended_split", "customize"],
        transaction: {
          id: CANDIDATE_1.transaction_id,
          merchant_name: CANDIDATE_1.merchant,
          name: CANDIDATE_1.merchant,
          amount_cents: CANDIDATE_1.amount_cents,
          currency: "USD",
          date: "2026-08-19",
          pending: false,
          status: "ask_user",
          institution_name: "ExpenseOps Demo Bank",
        },
        receipt: null,
        recommendation: null,
      },
    ],
    total_open: 1,
    unread_count: 1,
    limit: 100,
    offset: 0,
  };
}

function candidate(
  which: typeof CANDIDATE_1,
  overrides: Partial<{
    recommended_participant_names: string[];
    recommended_group_name: string | null;
    recommendation_label: string | null;
  }> = {},
) {
  return {
    review_item_public_id: which.review_item_public_id,
    kind: "transaction_review",
    transaction: {
      id: which.transaction_id,
      merchant: which.merchant,
      amount_cents: which.amount_cents,
      currency: "USD",
      occurred_on: "2026-08-19",
      pending: false,
      institution_name: "ExpenseOps Demo Bank",
    },
    receipt: null,
    recommended_participant_names: overrides.recommended_participant_names ?? [],
    recommended_group_name: overrides.recommended_group_name ?? null,
    recommendation_label: overrides.recommendation_label ?? null,
    available_actions: ["mark_personal", "propose_split", "customize", "skip"],
  };
}

function progress(overrides: Partial<{
  status: "active" | "completed" | "cancelled";
  reviewed: number;
  personal: number;
  split: number;
  skipped: number;
  stale: number;
  remaining: number;
}> = {}) {
  return {
    status: "active" as const,
    reviewed: 0,
    personal: 0,
    split: 0,
    skipped: 0,
    stale: 0,
    remaining: 2,
    total_candidates: 2,
    ...overrides,
  };
}

function confirmationBlock(
  action: "mark_transaction_personal" | "post_splitwise_expense",
  proposalId: string,
  status: AgentActionConfirmationBlock["status"] = "awaiting_confirmation",
): AgentActionConfirmationBlock {
  return {
    type: "action_confirmation",
    block_id: null,
    action,
    title: action === "mark_transaction_personal" ? "Mark transaction personal" : "Split transaction",
    summary:
      action === "mark_transaction_personal"
        ? "This transaction will be marked personal."
        : "This transaction will be split with Manasi Purohit.",
    details: [{ label: "Merchant", value: "Costco Wholesale" }],
    confirm_label: action === "mark_transaction_personal" ? "Mark personal" : "Split",
    cancel_label: "Cancel",
    proposal_id: proposalId,
    proposal_version: 1,
    status,
    expires_at: "2026-08-19T20:00:00Z",
  };
}

/** Installs review-session endpoints on top of the shared Agent app mock. mockAgentApp's
 * own catch-all 503s any unmatched /api/** path; routes registered afterward take priority. */
async function installReviewSessionRoutes(
  page: Page,
  {
    proposeDelayMs = 0,
  }: { proposeDelayMs?: number } = {},
): Promise<{ proposeCalls: unknown[]; advanceCalls: unknown[] }> {
  const proposeCalls: unknown[] = [];
  const advanceCalls: unknown[] = [];
  let step: "candidate1" | "candidate2" | "done" = "candidate1";

  await page.route("**/api/review-inbox**", (route) => route.fulfill({ json: reviewInboxPayload() }));

  await page.route(`**/api/agent/review-session/start`, (route) =>
    route.fulfill({
      json: {
        public_id: SESSION_ID,
        conversation_public_id: "conversation-public-1",
        progress: progress(),
        current: candidate(CANDIDATE_1),
      },
    }),
  );

  await page.route(`**/api/agent/review-session/${SESSION_ID}/propose`, async (route) => {
    proposeCalls.push(route.request().postDataJSON());
    if (proposeDelayMs) await new Promise((resolve) => setTimeout(resolve, proposeDelayMs));
    const proposalId = step === "candidate1" ? "proposal-day19-1" : "proposal-day19-2";
    const action = step === "candidate1" ? "mark_transaction_personal" : "post_splitwise_expense";
    return route.fulfill({ json: confirmationBlock(action, proposalId) });
  });

  await page.route("**/api/agent/proposals/proposal-day19-1/confirm", (route) =>
    route.fulfill({ json: confirmationBlock("mark_transaction_personal", "proposal-day19-1", "completed") }),
  );
  await page.route("**/api/agent/proposals/proposal-day19-2/confirm", (route) =>
    route.fulfill({ json: confirmationBlock("post_splitwise_expense", "proposal-day19-2", "completed") }),
  );

  await page.route(`**/api/agent/review-session/${SESSION_ID}/advance`, (route) => {
    advanceCalls.push(route.request().postDataJSON());
    if (step === "candidate1") {
      step = "candidate2";
      return route.fulfill({
        json: {
          public_id: SESSION_ID,
          conversation_public_id: "conversation-public-1",
          progress: progress({ reviewed: 1, personal: 1, remaining: 1 }),
          current: candidate(CANDIDATE_2, {
            recommended_participant_names: ["Manasi Purohit"],
            recommendation_label: "Recommended from your preference: split with Manasi Purohit.",
          }),
        },
      });
    }
    step = "done";
    return route.fulfill({
      json: {
        public_id: SESSION_ID,
        conversation_public_id: "conversation-public-1",
        progress: progress({ status: "completed", reviewed: 2, personal: 1, split: 1, remaining: 0 }),
        current: null,
      },
    });
  });

  return { proposeCalls, advanceCalls };
}

test("review session walks Personal then recommended Split, then completes, without leaving the Agent", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name === "mobile-chromium", "Desktop review-session coverage");
  await mockAgentApp(page, { agentReadOnly: false, initialConversation: false });
  await installReviewSessionRoutes(page);

  await page.goto("/");
  const startUrl = page.url();
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  const panel = page.getByTestId("agent-panel");

  await panel.getByRole("button", { name: "Review my 1 pending purchase" }).click();
  await expect(panel.getByText("Review purchase 1 of 2")).toBeVisible();
  await expect(panel.getByText("Costco Wholesale")).toBeVisible();

  await panel.getByRole("button", { name: "Personal", exact: true }).click();
  const confirmCard = panel.getByTestId("agent-action-confirmation");
  await expect(confirmCard).toContainText("Mark transaction personal");
  await confirmCard.getByRole("button", { name: "Mark personal" }).click();

  // Confirming auto-advances the session, so the confirmation card clears
  // straight to the next candidate rather than lingering on "Completed."
  await expect(panel.getByText("Review purchase 2 of 2")).toBeVisible();
  await expect(panel.getByText("REI Co-op")).toBeVisible();
  await expect(panel.getByText("Recommended from your preference: split with Manasi Purohit.")).toBeVisible();

  await panel.getByRole("button", { name: "Split with Manasi Purohit" }).click();
  await expect(confirmCard).toContainText("Split transaction");
  await confirmCard.getByRole("button", { name: "Split" }).click();

  await expect(panel.getByText("You're all caught up")).toBeVisible();
  await expect(
    panel.getByText("2 purchases reviewed: 1 personal, 1 split, 0 skipped."),
  ).toBeVisible();

  expect(page.url()).toBe(startUrl);
});

test("double-clicking Personal fires exactly one proposal", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile-chromium", "Desktop review-session coverage");
  await mockAgentApp(page, { agentReadOnly: false, initialConversation: false });
  const { proposeCalls } = await installReviewSessionRoutes(page, { proposeDelayMs: 250 });

  await page.goto("/");
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  const panel = page.getByTestId("agent-panel");
  await panel.getByRole("button", { name: "Review my 1 pending purchase" }).click();
  await expect(panel.getByText("Review purchase 1 of 2")).toBeVisible();

  await panel.getByRole("button", { name: "Personal", exact: true }).dblclick();
  await expect(panel.getByTestId("agent-action-confirmation")).toContainText(
    "Mark transaction personal",
  );
  await expect.poll(() => proposeCalls.length).toBe(1);
});

test("review-session candidate card stays bounded at 320px and has no serious axe violations", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chromium", "Mobile review-session coverage");
  await page.setViewportSize({ width: 320, height: 700 });
  await mockAgentApp(page, { agentReadOnly: false, initialConversation: false });
  await installReviewSessionRoutes(page);

  await page.goto("/?workspace=agent");
  const agent = page.getByTestId("agent-page");
  await agent.getByRole("button", { name: "Review my 1 pending purchase" }).click();
  await expect(agent.getByText("Review purchase 1 of 2")).toBeVisible();

  const personal = agent.getByRole("button", { name: "Personal", exact: true });
  expect((await personal.boundingBox())?.height).toBeGreaterThanOrEqual(44);
  const dimensions = await agent.evaluate((node) => ({
    clientWidth: node.clientWidth,
    scrollWidth: node.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);

  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(
    axe.violations.filter(
      (violation) => violation.impact === "critical" || violation.impact === "serious",
    ),
  ).toEqual([]);
});

test("a fresh conversation with pending items shows one welcome chip, not a duplicate teaser", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name === "mobile-chromium", "Desktop review-session coverage");
  await mockAgentApp(page, { agentReadOnly: false, initialConversation: false });
  await installReviewSessionRoutes(page);

  await page.goto("/");
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  const panel = page.getByTestId("agent-panel");

  await expect(panel.getByRole("button", { name: "Review my 1 pending purchase" })).toBeVisible();
  await expect(panel.getByRole("heading", { name: "Ask ExpenseOps" })).toBeVisible();
  await expect(panel.getByText("Or ask something else")).toBeVisible();
  // The old standalone teaser card (with its own "Live inbox" badge) must not
  // also render alongside the welcome screen's chip.
  await expect(panel.getByText("Live inbox")).not.toBeVisible();
});

test("typing a participant name resolves and proposes a split, without leaving the Agent", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name === "mobile-chromium", "Desktop review-session coverage");
  await mockAgentApp(page, { agentReadOnly: false, initialConversation: false });
  await installReviewSessionRoutes(page);

  const interpretCalls: unknown[] = [];
  await page.route(`**/api/agent/review-session/${SESSION_ID}/interpret`, (route) => {
    interpretCalls.push(route.request().postDataJSON());
    return route.fulfill({
      json: {
        status: "proposed",
        confirmation: confirmationBlock("post_splitwise_expense", "proposal-day19-typed"),
        message: null,
      },
    });
  });
  await page.route("**/api/agent/proposals/proposal-day19-typed/confirm", (route) =>
    route.fulfill({
      json: confirmationBlock("post_splitwise_expense", "proposal-day19-typed", "completed"),
    }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  const panel = page.getByTestId("agent-panel");

  await panel.getByRole("button", { name: "Review my 1 pending purchase" }).click();
  await expect(panel.getByText("Review purchase 1 of 2")).toBeVisible();

  const composer = panel.getByLabel("Ask ExpenseOps Agent");
  await composer.fill("split this with Manasi Purohit");
  await composer.press("Enter");

  const confirmCard = panel.getByTestId("agent-action-confirmation");
  await expect(confirmCard).toContainText("Split transaction");
  await expect.poll(() => interpretCalls.length).toBe(1);
  expect(interpretCalls[0]).toEqual({ text: "split this with Manasi Purohit" });

  await confirmCard.getByRole("button", { name: "Split" }).click();
  await expect(panel.getByText("Review purchase 2 of 2")).toBeVisible();
});

test("typing an unresolvable name shows a clarify message instead of an error", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name === "mobile-chromium", "Desktop review-session coverage");
  await mockAgentApp(page, { agentReadOnly: false, initialConversation: false });
  await installReviewSessionRoutes(page);

  await page.route(`**/api/agent/review-session/${SESSION_ID}/interpret`, (route) =>
    route.fulfill({
      json: {
        status: "clarify",
        confirmation: null,
        message: "I couldn't find Nobody Here in your Splitwise account.",
      },
    }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  const panel = page.getByTestId("agent-panel");

  await panel.getByRole("button", { name: "Review my 1 pending purchase" }).click();
  await expect(panel.getByText("Review purchase 1 of 2")).toBeVisible();

  const composer = panel.getByLabel("Ask ExpenseOps Agent");
  await composer.fill("split this with Nobody Here");
  await composer.press("Enter");

  await expect(panel.getByText("I couldn't find Nobody Here in your Splitwise account.")).toBeVisible();
  await expect(panel.getByTestId("agent-action-confirmation")).not.toBeVisible();
  // The candidate is still on screen and actionable with the buttons.
  await expect(panel.getByRole("button", { name: "Personal", exact: true })).toBeVisible();
});

test("the welcome screen and its prompts are hidden once a review session is active", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name === "mobile-chromium", "Desktop review-session coverage");
  await mockAgentApp(page, { agentReadOnly: false, initialConversation: false });
  await installReviewSessionRoutes(page);

  await page.goto("/");
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  const panel = page.getByTestId("agent-panel");

  await panel.getByRole("button", { name: "Review my 1 pending purchase" }).click();
  await expect(panel.getByText("Review purchase 1 of 2")).toBeVisible();

  await expect(panel.getByRole("heading", { name: "Ask ExpenseOps" })).not.toBeVisible();
  await expect(panel.getByText("How much did I spend last month?")).not.toBeVisible();
});

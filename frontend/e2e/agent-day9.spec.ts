import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";

import type {
  AgentActionConfirmationBlock,
  AgentStructuredResponse,
} from "../src/agent/contracts";
import { canonicalMessages, mockAgentApp } from "./fixtures/agent";

const PROPOSAL_ID = "proposal-day9-receipt-learning";

function receiptLearningBlock(
  status: AgentActionConfirmationBlock["status"] = "awaiting_confirmation",
  version = 1,
): AgentActionConfirmationBlock {
  return {
    type: "action_confirmation",
    action: "apply_receipt_learning_batch",
    title: "Learn household items from this receipt",
    summary:
      "Review one frozen batch. New items start in Learning with no invented cadence; nothing changes until you confirm.",
    details: [
      { label: "Merchant", value: "Costco" },
      { label: "Known matches", value: "0" },
      { label: "New Learning items", value: "4" },
      { label: "Not tracked", value: "2" },
      { label: "Needs input", value: "0" },
      { label: "Item 1", value: "KS EGGS 24CT → Eggs (Learning)" },
      { label: "Item 2", value: "ORG 2% MLK GAL → Milk (Learning)" },
      { label: "Item 3", value: "TIDE PODS 42CT → Laundry detergent (Learning)" },
      { label: "Item 4", value: "KS PAPER TOWELS 12RL → Paper towels (Learning)" },
      { label: "Item 5", value: "FOOD COURT COFFEE — do not track" },
      { label: "Item 6", value: "COTTON T-SHIRT — do not track" },
    ],
    confirm_label: "Confirm selected",
    cancel_label: "Cancel",
    proposal_id: PROPOSAL_ID,
    proposal_version: version,
    status,
    expires_at: "2026-08-17T20:00:00Z",
  };
}

function response(block: AgentActionConfirmationBlock): AgentStructuredResponse {
  return {
    schema_version: "1.0",
    blocks: [
      { type: "text", text: "I prepared one receipt-learning batch for review." },
      block,
    ],
  };
}

async function installConfirmRoute(
  page: Page,
  current: { block: AgentActionConfirmationBlock },
): Promise<unknown[]> {
  const bodies: unknown[] = [];
  await page.route(`**/api/agent/proposals/${PROPOSAL_ID}/confirm`, async (route) => {
    bodies.push(route.request().postDataJSON());
    current.block = receiptLearningBlock("completed", 3);
    await route.fulfill({ json: current.block });
  });
  return bodies;
}

async function expectNoOverflow(page: Page, container: Locator): Promise<void> {
  const dimensions = await container.evaluate((node) => ({
    clientWidth: node.clientWidth,
    scrollWidth: node.scrollWidth,
    documentClientWidth: document.documentElement.clientWidth,
    documentScrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
  expect(dimensions.documentScrollWidth).toBeLessThanOrEqual(dimensions.documentClientWidth);
}

test("receipt-learning proposal is explicit, single-flight, and answer-free", async ({
  page,
}, testInfo: TestInfo) => {
  test.skip(testInfo.project.name === "mobile-chromium", "Desktop receipt-learning coverage");
  const current = { block: receiptLearningBlock() };
  const fixture = await mockAgentApp(page, {
    agentReadOnly: false,
    initialConversation: true,
    messages: () =>
      canonicalMessages(
        response(current.block),
        "Learn the useful household items from this receipt.",
      ),
  });
  const bodies = await installConfirmRoute(page, current);

  await page.goto("/");
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  const card = page.getByTestId("agent-panel").getByTestId("agent-action-confirmation");
  await expect(card).toContainText("New Learning items");
  await expect(card).toContainText("Laundry detergent (Learning)");
  await expect(card).toContainText("FOOD COURT COFFEE — do not track");
  await expect(card).toContainText("nothing changes until you confirm");
  await card.getByRole("button", { name: "Confirm selected" }).dblclick();
  await expect.poll(() => bodies.length).toBe(1);
  await expect(card).toContainText("Completed. The confirmed action was applied.");
  expect(bodies).toEqual([{ proposal_version: 1 }]);
  expect(JSON.stringify(bodies)).not.toMatch(/Costco|eggs|coffee|line_id|cadence/i);
  expect(
    fixture.requests.filter(
      ({ method, pathname }) =>
        method !== "GET" && pathname.startsWith("/api/replenishment/receipts"),
    ),
  ).toEqual([]);

  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(
    axe.violations.filter(
      (violation) => violation.impact === "critical" || violation.impact === "serious",
    ),
  ).toEqual([]);
});

test("receipt-learning confirmation remains bounded at 320px", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chromium", "Mobile receipt-learning coverage");
  await page.setViewportSize({ width: 320, height: 700 });
  const current = { block: receiptLearningBlock() };
  await mockAgentApp(page, {
    agentReadOnly: false,
    initialConversation: true,
    messages: () =>
      canonicalMessages(
        response(current.block),
        "Learn the useful household items from this receipt.",
      ),
  });
  const bodies = await installConfirmRoute(page, current);

  await page.goto("/?workspace=agent");
  const agent = page.getByTestId("agent-page");
  const card = agent.getByTestId("agent-action-confirmation");
  await expect(card).toContainText("Paper towels (Learning)");
  const confirm = card.getByRole("button", { name: "Confirm selected" });
  expect((await confirm.boundingBox())?.height).toBeGreaterThanOrEqual(44);
  await expectNoOverflow(page, agent);
  await confirm.dblclick();
  await expect.poll(() => bodies.length).toBe(1);
  await expect(card).toContainText("Completed. The confirmed action was applied.");
  await expectNoOverflow(page, agent);
  expect(
    await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length })),
  ).toEqual({ local: 0, session: 0 });
});

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";

import type {
  AgentActionConfirmationBlock,
  AgentStructuredResponse,
} from "../src/agent/contracts";
import { canonicalMessages, mockAgentApp } from "./fixtures/agent";

const PROPOSAL_ID = "proposal-day11-itemized-receipt";

function itemizedBlock(
  status: AgentActionConfirmationBlock["status"] = "awaiting_confirmation",
  version = 1,
): AgentActionConfirmationBlock {
  return {
    type: "action_confirmation",
    action: "post_itemized_receipt_split",
    title: "Split restaurant receipt by item",
    summary:
      "Review every frozen item, tax/tip allocation, and final owed amount. Nothing is posted until you confirm.",
    details: [
      { label: "Merchant", value: "Dinner House" },
      { label: "Receipt total", value: "USD 90.00" },
      { label: "Tax / tip", value: "USD 6.00 / USD 9.00" },
      {
        label: "Allocation",
        value:
          "Tax: proportional to assigned item subtotal; tip: proportional to assigned item subtotal",
      },
      {
        label: "Person 1 — Runtime owner",
        value: "Paneer tikka, Dessert; items USD 21.00, tax USD 1.68, tip USD 2.52, final USD 25.20",
      },
      {
        label: "Person 2 — Gunjan Patil",
        value:
          "Chicken biryani, Cocktails, Dessert; items USD 54.00, tax USD 4.32, tip USD 6.48, final USD 64.80",
      },
      { label: "Destination", value: "Splitwise" },
      { label: "Effect", value: "Create one exact itemized Splitwise expense" },
    ],
    confirm_label: "Confirm itemized split",
    cancel_label: "Cancel",
    proposal_id: PROPOSAL_ID,
    proposal_version: version,
    status,
    expires_at: "2026-08-18T04:00:00Z",
  };
}

function response(block: AgentActionConfirmationBlock): AgentStructuredResponse {
  return {
    schema_version: "1.0",
    blocks: [
      { type: "text", text: "I prepared one action for your review." },
      block,
    ],
  };
}

async function installConfirm(
  page: Page,
  current: { block: AgentActionConfirmationBlock },
): Promise<unknown[]> {
  const bodies: unknown[] = [];
  await page.route(`**/api/agent/proposals/${PROPOSAL_ID}/confirm`, async (route) => {
    bodies.push(route.request().postDataJSON());
    current.block = itemizedBlock("completed", 2);
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

test("itemized receipt preview shows exact code-owned shares and confirms once", async ({
  page,
}, testInfo: TestInfo) => {
  test.skip(testInfo.project.name === "mobile-chromium", "Desktop itemized split coverage");
  const current = { block: itemizedBlock() };
  const fixture = await mockAgentApp(page, {
    agentReadOnly: false,
    initialConversation: true,
    messages: () =>
      canonicalMessages(
        response(current.block),
        "Paneer was mine, chicken and cocktails were Gunjan's, dessert was shared, and split tax and tip proportionally.",
      ),
  });
  const bodies = await installConfirm(page, current);

  await page.goto("/");
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  const panel = page.getByTestId("agent-panel");
  const card = panel.getByTestId("agent-action-confirmation");
  await expect(card).toContainText("Paneer tikka, Dessert");
  await expect(card).toContainText("final USD 25.20");
  await expect(card).toContainText("Chicken biryani, Cocktails, Dessert");
  await expect(card).toContainText("final USD 64.80");
  await expect(card).toContainText("Nothing is posted until you confirm");
  await card.getByRole("button", { name: "Confirm itemized split" }).dblclick();
  await expect.poll(() => bodies.length).toBe(1);
  await expect(card).toContainText("Completed. The confirmed action was applied.");
  expect(bodies).toEqual([{ proposal_version: 1 }]);
  expect(JSON.stringify(bodies)).not.toMatch(
    /Dinner House|Paneer|Gunjan|90\.00|line_id|user_id|splitwise_payload|owed/i,
  );
  expect(
    fixture.requests.filter(
      ({ method, pathname }) => method !== "GET" && pathname.startsWith("/transactions"),
    ),
  ).toEqual([]);

  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(
    axe.violations.filter(
      (violation) => violation.impact === "critical" || violation.impact === "serious",
    ),
  ).toEqual([]);
});

test("itemized receipt confirmation remains bounded and single-flight at 320px", async ({
  page,
}, testInfo: TestInfo) => {
  test.skip(testInfo.project.name !== "mobile-chromium", "Mobile itemized split coverage");
  await page.setViewportSize({ width: 320, height: 700 });
  const current = { block: itemizedBlock() };
  await mockAgentApp(page, {
    agentReadOnly: false,
    initialConversation: true,
    messages: () =>
      canonicalMessages(
        response(current.block),
        "Paneer was mine, chicken and cocktails were Gunjan's, dessert was shared, and split tax and tip proportionally.",
      ),
  });
  const bodies = await installConfirm(page, current);

  await page.goto("/?workspace=agent");
  const agent = page.getByTestId("agent-page");
  const card = agent.getByTestId("agent-action-confirmation");
  await expect(card).toContainText("final USD 64.80");
  const confirm = card.getByRole("button", { name: "Confirm itemized split" });
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

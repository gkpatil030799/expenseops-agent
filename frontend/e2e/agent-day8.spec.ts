import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";

import type {
  AgentActionConfirmationBlock,
  AgentStructuredResponse,
} from "../src/agent/contracts";
import { canonicalMessages, mockAgentApp } from "./fixtures/agent";

const PROPOSAL_ID = "proposal-day8-personal-1";

function actionBlock(
  status: AgentActionConfirmationBlock["status"] = "awaiting_confirmation",
  version = 1,
): AgentActionConfirmationBlock {
  return {
    type: "action_confirmation",
    action: "mark_transaction_personal",
    title: "Mark transaction personal",
    summary: "This transaction will be marked personal and removed from shared-expense review.",
    details: [
      { label: "Merchant", value: "Trader Joe's" },
      { label: "Date", value: "2026-08-14" },
      { label: "Amount", value: "USD 75.76" },
      { label: "Effect", value: "Mark personal" },
    ],
    confirm_label: "Mark personal",
    cancel_label: "Cancel",
    proposal_id: PROPOSAL_ID,
    proposal_version: version,
    status,
    expires_at: "2026-08-16T18:00:00Z",
  };
}

function splitActionBlock(
  status: AgentActionConfirmationBlock["status"] = "awaiting_confirmation",
  version = 1,
): AgentActionConfirmationBlock {
  return {
    type: "action_confirmation",
    action: "post_splitwise_expense",
    title: "Split expense",
    summary: "This will create a Splitwise expense after you confirm.",
    details: [
      { label: "Merchant", value: "Costco" },
      { label: "Date", value: "2026-08-16" },
      { label: "Total", value: "USD 84.20" },
      { label: "Payer", value: "You" },
      { label: "Share 1 — You", value: "USD 42.10" },
      { label: "Share 2 — Gunjan", value: "USD 42.10" },
      { label: "Destination", value: "Splitwise" },
      { label: "Effect", value: "Create one equal Splitwise expense" },
    ],
    confirm_label: "Confirm split",
    cancel_label: "Cancel",
    proposal_id: PROPOSAL_ID,
    proposal_version: version,
    status,
    expires_at: "2026-08-16T18:00:00Z",
  };
}

function actionResponse(block: AgentActionConfirmationBlock): AgentStructuredResponse {
  return {
    schema_version: "1.0",
    blocks: [
      { type: "text", text: "I prepared one action for your review." },
      block,
    ],
  };
}

async function installActionDecisionRoutes(
  page: Page,
  current: { block: AgentActionConfirmationBlock },
) {
  const decisions: Array<{ pathname: string; body: unknown }> = [];
  let releaseConfirm = () => {};
  let confirmHeld = false;
  const confirmRelease = new Promise<void>((resolve) => {
    releaseConfirm = resolve;
  });

  await page.route(`**/api/agent/proposals/${PROPOSAL_ID}/*`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    decisions.push({ pathname: url.pathname, body: request.postDataJSON() });
    if (url.pathname.endsWith("/confirm") && confirmHeld) await confirmRelease;
    current.block = {
      ...current.block,
      status: url.pathname.endsWith("/confirm") ? "completed" : "cancelled",
      proposal_version: 2,
    };
    return route.fulfill({ json: current.block });
  });

  return {
    decisions,
    holdConfirm: () => {
      confirmHeld = true;
    },
    releaseConfirm,
  };
}

async function openDesktopAgent(page: Page): Promise<Locator> {
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  const panel = page.getByTestId("agent-panel");
  await expect(panel).toBeVisible();
  return panel;
}

async function expectNoHorizontalOverflow(page: Page, container: Locator): Promise<void> {
  const metrics = await container.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    documentClientWidth: document.documentElement.clientWidth,
    documentScrollWidth: document.documentElement.scrollWidth,
  }));
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth);
  expect(metrics.documentScrollWidth).toBeLessThanOrEqual(metrics.documentClientWidth);
}

function skipMobileProject(testInfo: TestInfo): void {
  test.skip(testInfo.project.name === "mobile-chromium", "Desktop controlled-action coverage");
}

test("local action preview cancels without mutation and persists after reload", async ({
  page,
}, testInfo) => {
  skipMobileProject(testInfo);
  const current = { block: actionBlock() };
  const fixture = await mockAgentApp(page, {
    agentReadOnly: false,
    initialConversation: true,
    messages: () => canonicalMessages(actionResponse(current.block), "Mark this transaction personal"),
  });
  const actions = await installActionDecisionRoutes(page, current);

  await page.goto("/");
  const panel = await openDesktopAgent(page);
  await expect(panel.getByText("Controlled actions · confirmation required")).toBeVisible();
  const card = panel.getByTestId("agent-action-confirmation");
  await expect(card).toContainText("Trader Joe's");
  await expect(card).toContainText("USD 75.76");
  await expect(card).toContainText("Nothing changes until you confirm");

  await card.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect.poll(() => actions.decisions.length).toBe(1);
  expect(actions.decisions[0]).toEqual({
    pathname: `/api/agent/proposals/${PROPOSAL_ID}/cancel`,
    body: { proposal_version: 1 },
  });
  await expect(card).toContainText("Cancelled. Nothing was changed.");
  await expect(card.getByRole("button", { name: "Mark personal", exact: true })).toHaveCount(0);
  expect(
    fixture.requests.filter(
      ({ method, pathname }) => method !== "GET" && pathname.startsWith("/transactions"),
    ),
  ).toEqual([]);

  await page.reload();
  const reloaded = await openDesktopAgent(page);
  await expect(reloaded.getByTestId("agent-action-confirmation")).toContainText(
    "Cancelled. Nothing was changed.",
  );
});

test("explicit confirm is exact, single-flight, terminal, and answer-free", async ({
  page,
}, testInfo) => {
  skipMobileProject(testInfo);
  const current = { block: actionBlock() };
  const fixture = await mockAgentApp(page, {
    agentReadOnly: false,
    initialConversation: true,
    messages: () => canonicalMessages(actionResponse(current.block), "Mark this transaction personal"),
  });
  const actions = await installActionDecisionRoutes(page, current);
  actions.holdConfirm();

  await page.goto("/");
  const panel = await openDesktopAgent(page);
  const card = panel.getByTestId("agent-action-confirmation");
  const confirm = card.getByRole("button", { name: "Mark personal", exact: true });
  await confirm.dblclick();
  await expect.poll(() => actions.decisions.length).toBe(1);
  const applying = card.getByRole("button", { name: "Applying…", exact: true });
  await expect(applying).toBeDisabled();
  actions.releaseConfirm();

  await expect(card).toContainText("Completed. The confirmed action was applied.");
  expect(actions.decisions).toEqual([
    {
      pathname: `/api/agent/proposals/${PROPOSAL_ID}/confirm`,
      body: { proposal_version: 1 },
    },
  ]);
  expect(JSON.stringify(actions.decisions)).not.toMatch(
    /Trader Joe|75\.76|transaction_id|normalized|structured_response/i,
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

for (const width of [320, 375, 390]) {
  test(`mobile controlled action is bounded and single-flight at ${width}px`, async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile-chromium", "Mobile controlled-action coverage");
    await page.setViewportSize({ width, height: 700 });
    const current = { block: actionBlock() };
    await mockAgentApp(page, {
      agentReadOnly: false,
      initialConversation: true,
      messages: () => canonicalMessages(actionResponse(current.block), "Mark this transaction personal"),
    });
    const actions = await installActionDecisionRoutes(page, current);

    await page.goto("/?workspace=agent");
    const agent = page.getByTestId("agent-page");
    const card = agent.getByTestId("agent-action-confirmation");
    await expect(card).toBeVisible();
    const confirm = card.getByRole("button", { name: "Mark personal", exact: true });
    const cancel = card.getByRole("button", { name: "Cancel", exact: true });
    expect((await confirm.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    expect((await cancel.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    await expectNoHorizontalOverflow(page, agent);

    await confirm.dblclick();
    await expect.poll(() => actions.decisions.length).toBe(1);
    await expect(card).toContainText("Completed. The confirmed action was applied.");
    await expectNoHorizontalOverflow(page, agent);
    expect(
      await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length })),
    ).toEqual({ local: 0, session: 0 });
  });
}

test("read-only feature state exposes no action controls", async ({ page }, testInfo) => {
  skipMobileProject(testInfo);
  await mockAgentApp(page, {
    agentReadOnly: true,
    initialConversation: true,
    messages: canonicalMessages(actionResponse(actionBlock()), "Mark this transaction personal"),
  });

  await page.goto("/");
  const panel = await openDesktopAgent(page);
  await expect(panel.getByText("Read-only · grounded in your data")).toBeVisible();
  await expect(panel.getByTestId("agent-action-confirmation")).toBeVisible();
  await expect(panel.getByRole("button", { name: "Mark personal", exact: true })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Cancel", exact: true })).toHaveCount(0);
});

test("Splitwise proposal shows exact shares and confirms through the shared endpoint", async ({
  page,
}, testInfo) => {
  skipMobileProject(testInfo);
  const current = { block: splitActionBlock() };
  const fixture = await mockAgentApp(page, {
    agentReadOnly: false,
    initialConversation: true,
    messages: () => canonicalMessages(
      actionResponse(current.block),
      "Split this Costco transaction with Gunjan.",
    ),
  });
  const actions = await installActionDecisionRoutes(page, current);

  await page.goto("/");
  const panel = await openDesktopAgent(page);
  const card = panel.getByTestId("agent-action-confirmation");
  await expect(card).toContainText("Costco");
  await expect(card).toContainText("USD 84.20");
  await expect(card).toContainText("Share 1 — You");
  await expect(card).toContainText("USD 42.10");
  await expect(card).toContainText("Share 2 — Gunjan");
  await expect(card).toContainText("Splitwise");
  await card.getByRole("button", { name: "Confirm split", exact: true }).click();

  await expect(card).toContainText("Completed. The confirmed action was applied.");
  expect(actions.decisions).toEqual([
    {
      pathname: `/api/agent/proposals/${PROPOSAL_ID}/confirm`,
      body: { proposal_version: 1 },
    },
  ]);
  expect(JSON.stringify(actions.decisions)).not.toMatch(
    /Costco|84\.20|Gunjan|user_id|share|splitwise_payload|structured_response/i,
  );
  expect(
    fixture.requests.filter(
      ({ method, pathname }) => method !== "GET" && pathname.startsWith("/transactions"),
    ),
  ).toEqual([]);
});

test("ambiguous Splitwise outcome is terminal and never offers a blind retry", async ({
  page,
}, testInfo) => {
  skipMobileProject(testInfo);
  const current = { block: splitActionBlock() };
  const decisions: unknown[] = [];
  await mockAgentApp(page, {
    agentReadOnly: false,
    initialConversation: true,
    messages: () => canonicalMessages(
      actionResponse(current.block),
      "Split this Costco transaction with Gunjan.",
    ),
  });
  await page.route(`**/api/agent/proposals/${PROPOSAL_ID}/confirm`, async (route) => {
    decisions.push(route.request().postDataJSON());
    current.block = splitActionBlock("ambiguous", 3);
    await route.fulfill({ json: current.block });
  });

  await page.goto("/");
  const panel = await openDesktopAgent(page);
  const card = panel.getByTestId("agent-action-confirmation");
  await card.getByRole("button", { name: "Confirm split", exact: true }).click();
  await expect(card).toContainText("could not verify the outcome safely");
  await expect(card.getByRole("button", { name: "Confirm split", exact: true })).toHaveCount(0);
  await expect(card.getByRole("button", { name: "Retry", exact: true })).toHaveCount(0);
  expect(decisions).toEqual([{ proposal_version: 1 }]);
});

test("queued Splitwise action is polled to its terminal reconciled state without reposting", async ({
  page,
}, testInfo) => {
  skipMobileProject(testInfo);
  const current = { block: splitActionBlock() };
  const decisions: unknown[] = [];
  await mockAgentApp(page, {
    agentReadOnly: false,
    initialConversation: true,
    messages: () => canonicalMessages(
      actionResponse(current.block),
      "Split this Costco transaction with Gunjan.",
    ),
  });
  await page.route(`**/api/agent/proposals/${PROPOSAL_ID}/confirm`, async (route) => {
    decisions.push(route.request().postDataJSON());
    current.block = splitActionBlock("executing", 3);
    await route.fulfill({ json: current.block });
    setTimeout(() => {
      current.block = splitActionBlock("completed", 4);
    }, 100);
  });

  await page.goto("/");
  const panel = await openDesktopAgent(page);
  const card = panel.getByTestId("agent-action-confirmation");
  await card.getByRole("button", { name: "Confirm split", exact: true }).click();

  await expect(card).toContainText("Completed. The confirmed action was applied.");
  expect(decisions).toEqual([{ proposal_version: 1 }]);
});

test("ambiguous participant and disconnected Splitwise render guidance without action controls", async ({
  page,
}, testInfo) => {
  skipMobileProject(testInfo);
  const response: AgentStructuredResponse = {
    schema_version: "1.0",
    blocks: [
      {
        type: "text",
        text: "I found more than one Splitwise match for Gunjan. Which person do you mean?",
      },
      {
        type: "text",
        text: "Reconnect your personal Splitwise account before splitting.",
      },
      {
        type: "navigation",
        label: "Open Splitwise settings",
        target_surface: "integrations",
        entity: { kind: "integration", public_id: "splitwise" },
      },
    ],
  };
  await mockAgentApp(page, {
    agentReadOnly: false,
    initialConversation: true,
    messages: canonicalMessages(response, "Split this transaction with Gunjan."),
  });

  await page.goto("/");
  const panel = await openDesktopAgent(page);
  await expect(panel).toContainText("more than one Splitwise match");
  await expect(panel).toContainText("Reconnect your personal Splitwise account");
  await expect(panel.getByRole("button", { name: "Open Splitwise settings" })).toBeVisible();
  await expect(panel.getByTestId("agent-action-confirmation")).toHaveCount(0);
});

test("mobile Splitwise proposal is readable and confirms once at 320px", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chromium", "Mobile Splitwise action coverage");
  await page.setViewportSize({ width: 320, height: 700 });
  const current = { block: splitActionBlock() };
  await mockAgentApp(page, {
    agentReadOnly: false,
    initialConversation: true,
    messages: () => canonicalMessages(
      actionResponse(current.block),
      "Split this Costco transaction with Gunjan.",
    ),
  });
  const actions = await installActionDecisionRoutes(page, current);

  await page.goto("/?workspace=agent");
  const agent = page.getByTestId("agent-page");
  const card = agent.getByTestId("agent-action-confirmation");
  await expect(card).toContainText("Share 2 — Gunjan");
  const confirm = card.getByRole("button", { name: "Confirm split", exact: true });
  expect((await confirm.boundingBox())?.height).toBeGreaterThanOrEqual(44);
  await expectNoHorizontalOverflow(page, agent);
  await confirm.dblclick();
  await expect.poll(() => actions.decisions.length).toBe(1);
  await expect(card).toContainText("Completed. The confirmed action was applied.");
  await expectNoHorizontalOverflow(page, agent);
});

for (const failure of [
  { name: "stale proposal", status: 409 },
  { name: "cross-tenant proposal", status: 404 },
  { name: "mid-session flag disable", status: 404 },
]) {
  test(`${failure.name} fails safely without a browser-side mutation`, async ({
    page,
  }, testInfo) => {
    skipMobileProject(testInfo);
    const current = { block: actionBlock() };
    const fixture = await mockAgentApp(page, {
      agentReadOnly: false,
      initialConversation: true,
      messages: () => canonicalMessages(actionResponse(current.block), "Mark this transaction personal"),
    });
    const decisions: unknown[] = [];
    await page.route(`**/api/agent/proposals/${PROPOSAL_ID}/confirm`, async (route) => {
      decisions.push(route.request().postDataJSON());
      await route.fulfill({
        status: failure.status,
        json: { detail: "Agent action proposal is unavailable" },
      });
    });

    await page.goto("/");
    const panel = await openDesktopAgent(page);
    const card = panel.getByTestId("agent-action-confirmation");
    await card.getByRole("button", { name: "Mark personal", exact: true }).click();

    await expect(card.getByRole("alert")).toContainText(
      "could not be applied safely",
    );
    expect(decisions).toEqual([{ proposal_version: 1 }]);
    expect(
      fixture.requests.filter(
        ({ method, pathname }) => method !== "GET" && pathname.startsWith("/transactions"),
      ),
    ).toEqual([]);
  });
}

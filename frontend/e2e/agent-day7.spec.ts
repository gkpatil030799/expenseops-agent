import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";

import type { AgentStructuredResponse } from "../src/agent/contracts";
import {
  canonicalMessages,
  installAgentStream,
  mockAgentApp,
  releaseAgentStream,
  spendingResponse,
  successfulEvents,
  transactionResponse,
  waitForAgentStreamPause,
} from "./fixtures/agent";

function skipMobileProject(testInfo: TestInfo): void {
  test.skip(testInfo.project.name === "mobile-chromium", "Desktop feedback coverage");
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

const PARTIAL_BUT_TERMINAL_RESPONSE: AgentStructuredResponse = {
  schema_version: "1.0",
  blocks: [
    {
      type: "text",
      text: "Transaction reviews completed, but current deals were unavailable.",
    },
    {
      type: "attention_summary",
      block_version: "1.0",
      title: "Today needs attention",
      status: "partial",
      checked_domains: ["transactions"],
      unavailable_domains: ["deals"],
      items: [
        {
          priority: "action_required",
          domain: "transactions",
          title: "Expense reviews",
          detail: "Two transactions still need review.",
          count: 2,
          navigation: null,
        },
      ],
      items_truncated: false,
    },
  ],
};

test("completed assistant feedback is keyboard-safe, bounded, persistent, and answer-free", async ({
  page,
}, testInfo) => {
  skipMobileProject(testInfo);
  const fixture = await mockAgentApp(page, {
    initialConversation: true,
    messages: canonicalMessages(spendingResponse, "How much did I spend?"),
  });

  await page.goto("/");
  const panel = await openDesktopAgent(page);
  const answer = panel.getByLabel("ExpenseOps Agent response", { exact: true });
  const helpful = answer.getByRole("button", { name: "Helpful", exact: true });
  const notHelpful = answer.getByRole("button", { name: "Not helpful", exact: true });
  await expect(helpful).toBeVisible();
  await expect(notHelpful).toBeVisible();
  await expect(helpful).toHaveAttribute("aria-pressed", "false");

  const helpfulBox = await helpful.boundingBox();
  const notHelpfulBox = await notHelpful.boundingBox();
  expect(helpfulBox?.height).toBeGreaterThanOrEqual(44);
  expect(notHelpfulBox?.height).toBeGreaterThanOrEqual(44);

  await helpful.focus();
  await helpful.press("Enter");
  await expect.poll(() => fixture.feedbackRequests.length).toBe(1);
  await expect(helpful).toHaveAttribute("aria-pressed", "true");
  await expect(answer.getByRole("status")).toContainText("feedback saved");
  expect(fixture.feedbackRequests[0]).toEqual({
    method: "POST",
    pathname: "/api/agent/messages/message-assistant-1/feedback",
    body: { rating: "helpful", reason: null },
  });

  await notHelpful.focus();
  await notHelpful.press("Enter");
  const reason = answer.getByLabel(/What could be better/i);
  await expect(reason).toBeFocused();
  await reason.selectOption("wrong_data");
  const submit = answer.getByRole("button", { name: "Send feedback", exact: true });
  await submit.focus();
  await submit.press("Enter");

  await expect.poll(() => fixture.feedbackRequests.length).toBe(2);
  await expect(notHelpful).toHaveAttribute("aria-pressed", "true");
  expect(fixture.feedbackRequests[1]).toEqual({
    method: "POST",
    pathname: "/api/agent/messages/message-assistant-1/feedback",
    body: { rating: "not_helpful", reason: "wrong_data" },
  });
  expect(JSON.stringify(fixture.feedbackRequests)).not.toMatch(
    /structured_response|Food & Dining spending|transaction|prompt/i,
  );
  await expect(
    answer.getByText("You spent $412.00 on Food & Dining last month.", { exact: true }),
  ).toHaveCount(1);
  expect(
    fixture.requests.filter(
      ({ method, pathname }) =>
        !["GET", "HEAD", "OPTIONS"].includes(method) &&
        !pathname.endsWith("/feedback"),
    ),
  ).toEqual([]);
  expect(
    await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length })),
  ).toEqual({ local: 0, session: 0 });

  await page.reload();
  const reloadedPanel = await openDesktopAgent(page);
  const reloadedAnswer = reloadedPanel.getByLabel("ExpenseOps Agent response", { exact: true });
  await expect(
    reloadedAnswer.getByRole("button", { name: "Not helpful", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(reloadedAnswer.getByText("Feedback saved", { exact: true })).toBeVisible();
  await expect(reloadedAnswer.getByLabel(/What could be better/i)).toHaveCount(0);

  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(
    axe.violations.filter(
      (violation) => violation.impact === "critical" || violation.impact === "serious",
    ),
  ).toEqual([]);
});

test("feedback stays off streaming fragments and appears on a truthful terminal partial answer", async ({
  page,
}, testInfo) => {
  skipMobileProject(testInfo);
  await mockAgentApp(page, {
    messages: canonicalMessages(PARTIAL_BUT_TERMINAL_RESPONSE, "What needs attention?"),
  });
  await installAgentStream(page, {
    events: successfulEvents({
      response: PARTIAL_BUT_TERMINAL_RESPONSE,
      deltas: ["Checking the available areas."],
    }),
    pauseAfterIndexes: [1],
  });

  await page.goto("/");
  const panel = await openDesktopAgent(page);
  await panel.getByLabel("Ask ExpenseOps Agent").fill("What needs attention?");
  await panel.getByLabel("Ask ExpenseOps Agent").press("Enter");
  await waitForAgentStreamPause(page, 1);

  await expect(panel.getByLabel("ExpenseOps Agent response in progress")).toBeVisible();
  await expect(panel.getByRole("button", { name: "Helpful", exact: true })).toHaveCount(0);
  await releaseAgentStream(page);

  await expect(panel.getByLabel("ExpenseOps Agent response in progress")).toHaveCount(0);
  await expect(panel.getByText("Partial", { exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Helpful", exact: true })).toBeVisible();
});

test("disabled deep links expose no Agent entry point or Agent request", async ({ page }) => {
  const fixture = await mockAgentApp(page, { agentEnabled: false });

  await page.goto("/?workspace=agent");
  await expect(page.getByRole("heading", { name: "Expense Review" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Agent", exact: true })).toHaveCount(0);
  await expect(page).toHaveURL(/workspace=expenses/);
  expect(fixture.requests.filter(({ pathname }) => pathname.startsWith("/api/agent/"))).toEqual([]);
});

test("a mid-session kill switch rejects the next turn and disappears on reload", async ({
  page,
}, testInfo) => {
  skipMobileProject(testInfo);
  const fixture = await mockAgentApp(page, {
    initialConversation: true,
    messages: canonicalMessages(spendingResponse, "How much did I spend?"),
  });
  await page.route("**/api/agent/conversations/*/turns/stream", (route) =>
    route.fulfill({ status: 404, json: { detail: "Agent resource not found" } }),
  );

  await page.goto("/");
  const panel = await openDesktopAgent(page);
  fixture.setAgentEnabled(false);
  await panel.getByLabel("Ask ExpenseOps Agent").fill("Show recent transactions");
  await panel.getByLabel("Ask ExpenseOps Agent").press("Enter");

  await expect(panel.getByRole("alert")).toContainText("Agent conversation is unavailable");
  await expect(panel.getByRole("button", { name: "Retry", exact: true })).toHaveCount(0);
  expect(
    fixture.requests.filter(
      ({ method, pathname }) => method === "POST" && pathname.endsWith("/turns/stream"),
    ),
  ).toHaveLength(1);
  expect(fixture.feedbackRequests).toEqual([]);

  await panel.getByRole("button", { name: "Close ExpenseOps Agent" }).click();
  await expect(page.getByRole("heading", { name: "Expense Review" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Expense Review" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Agent", exact: true })).toHaveCount(0);
});

for (const width of [320, 375, 390]) {
  test(`mobile transaction feedback stays usable without overflow at ${width}px and after rotation`, async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile-chromium", "Mobile rotation coverage");
    await page.setViewportSize({ width, height: 700 });
    await mockAgentApp(page, {
      initialConversation: true,
      messages: canonicalMessages(transactionResponse, "Show recent transactions"),
    });

    await page.goto("/?workspace=agent");
    const agent = page.getByTestId("agent-page");
    await expect(agent).toBeVisible();
    const feedback = agent.getByLabel("Agent response feedback");
    const helpful = feedback.getByRole("button", { name: "Helpful", exact: true });
    const notHelpful = feedback.getByRole("button", { name: "Not helpful", exact: true });
    await expect(helpful).toBeVisible();
    await expect(notHelpful).toBeVisible();
    expect((await helpful.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    expect((await notHelpful.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    await expectNoHorizontalOverflow(page, agent);

    await page.setViewportSize({ width: 700, height: 320 });
    await expect(agent).toBeVisible();
    await expectNoHorizontalOverflow(page, agent);
    await expect(page.getByRole("navigation", { name: "Primary mobile navigation" })).toBeVisible();
    expect(
      await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length })),
    ).toEqual({ local: 0, session: 0 });
  });
}

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

import {
  agentStreamCallCount,
  agentStreamCalls,
  canonicalMessages,
  failedEvents,
  installAgentStream,
  installAgentStreamSequence,
  mockAgentApp,
  releaseAgentStream,
  spendingResponse,
  successfulEvents,
  textResponse,
  transactionResponse,
  userMessage,
  waitForAgentStreamPause,
} from "./fixtures/agent";

const SPENDING_QUESTION = "How much did I spend on Food & Dining last month?";

function skipMobileProject(testInfo: TestInfo): void {
  test.skip(testInfo.project.name === "mobile-chromium", "Desktop Agent panel coverage");
}

function skipUnlessChromium(testInfo: TestInfo): void {
  test.skip(testInfo.project.name !== "chromium", "Detailed behavior is covered in Chromium");
}

async function openDesktopAgent(page: Page): Promise<void> {
  const launcher = page.getByRole("button", { name: "Agent", exact: true });
  await launcher.click();
  await expect(page.getByTestId("agent-panel")).toBeVisible();
  await expect(page.getByLabel("Ask ExpenseOps Agent")).toBeFocused();
}

test("Agent stays absent and makes zero Agent requests when the feature is disabled", async ({ page }) => {
  const fixture = await mockAgentApp(page, { agentEnabled: false });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Expense Review" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Agent", exact: true })).toHaveCount(0);
  await page.waitForTimeout(50);

  expect(fixture.requests.filter((request) => request.pathname.startsWith("/api/agent/"))).toEqual([]);
});

test("desktop panel streams a canonical spending answer beside the existing page", async ({ page }, testInfo) => {
  skipMobileProject(testInfo);
  const events = successfulEvents({
    response: spendingResponse,
    deltas: ["You spent ", "$412.00 on Food & Dining last month."],
    activity: "spending",
  });
  await mockAgentApp(page, {
    messages: canonicalMessages(spendingResponse, SPENDING_QUESTION),
  });
  await installAgentStream(page, { events, pauseAfterIndexes: [1, 3] });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Expense Review" })).toBeVisible();
  await openDesktopAgent(page);
  const panel = page.getByTestId("agent-panel");
  await page.getByLabel("Ask ExpenseOps Agent").fill(SPENDING_QUESTION);
  await page.getByLabel("Ask ExpenseOps Agent").press("Enter");

  await waitForAgentStreamPause(page, 1);
  await expect(panel.getByText("Checking your spending…", { exact: true }).first()).toBeVisible();
  await expect(panel).not.toContainText("get_spending_insights");
  await releaseAgentStream(page);

  await waitForAgentStreamPause(page, 3);
  await expect(panel.getByText("You spent", { exact: true })).toBeVisible();
  await expect(panel).not.toContainText("$412.00 on Food & Dining last month.");
  await releaseAgentStream(page);

  await expect(panel.getByLabel("ExpenseOps Agent response in progress")).toHaveCount(0);
  const canonicalAnswer = panel.getByLabel("ExpenseOps Agent response", { exact: true });
  await expect(canonicalAnswer.getByText("$412.00", { exact: true }).first()).toBeVisible();
  await expect(canonicalAnswer.getByText("Food & Dining spending", { exact: true })).toBeVisible();
  await expect(
    canonicalAnswer.getByText("You spent $412.00 on Food & Dining last month.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Expense Review" })).toBeVisible();
  await expect(page.getByLabel("Ask ExpenseOps Agent")).toBeEnabled();
  await expect(page.getByLabel("Send message")).toBeVisible();

  const panelBox = await panel.boundingBox();
  const viewport = page.viewportSize();
  expect(panelBox).not.toBeNull();
  expect(viewport).not.toBeNull();
  expect(panelBox!.width).toBeLessThan(viewport!.width / 2);
  expect(panelBox!.x).toBeGreaterThan(viewport!.width / 2);

  if (testInfo.project.name === "chromium") {
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    const blocking = results.violations.filter(
      (violation) => violation.impact === "critical" || violation.impact === "serious",
    );
    expect(blocking).toEqual([]);
  }

  const close = page.getByRole("button", { name: "Close ExpenseOps Agent" });
  await close.focus();
  await close.press("Enter");
  await expect(panel).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Agent", exact: true })).toBeFocused();
});

test("desktop transaction question streams a bounded canonical list without writes", async ({
  page,
}, testInfo) => {
  skipUnlessChromium(testInfo);
  const question = "Show my recent transactions";
  const fixture = await mockAgentApp(page, {
    messages: canonicalMessages(transactionResponse, question),
  });
  await installAgentStream(page, {
    events: successfulEvents({
      response: transactionResponse,
      deltas: ["I found one matching transaction."],
      activity: "transactions",
    }),
    pauseAfterIndexes: [1],
  });

  await page.goto("/");
  await openDesktopAgent(page);
  const panel = page.getByTestId("agent-panel");
  await panel.getByLabel("Ask ExpenseOps Agent").fill(question);
  await panel.getByLabel("Ask ExpenseOps Agent").press("Enter");

  await waitForAgentStreamPause(page, 1);
  await expect(
    panel.getByText("Looking through your transactions…", { exact: true }).first(),
  ).toBeVisible();
  await expect(panel).not.toContainText("search_transactions");
  await releaseAgentStream(page);

  await expect(panel.getByLabel("ExpenseOps Agent response in progress")).toHaveCount(0);
  const canonicalAnswer = panel.getByLabel("ExpenseOps Agent response", { exact: true });
  await expect(canonicalAnswer.getByText("Recent transactions", { exact: true })).toBeVisible();
  await expect(canonicalAnswer.getByText("Showing 1 of 1", { exact: true })).toBeVisible();
  await expect(
    canonicalAnswer.getByRole("button", {
      name: /Open activity for A deliberately long merchant name/i,
    }),
  ).toHaveCount(1);
  await expect(canonicalAnswer.getByText("$123.45", { exact: true })).toBeVisible();

  const calls = await agentStreamCalls(page);
  expect(calls).toHaveLength(1);
  expect(calls[0].body?.text).toBe(question);
  expect(
    fixture.requests.filter(
      (request) =>
        request.method !== "GET" &&
        (request.pathname.startsWith("/transactions") ||
          request.pathname.startsWith("/splitwise")),
    ),
  ).toEqual([]);
});

for (const width of [320, 375, 390]) {
  test(`mobile Agent is a first-class destination without overflow at ${width}px`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile-chromium", "Mobile layout coverage");
    await page.setViewportSize({ width, height: 844 });
    await mockAgentApp(page, {
      initialConversation: true,
      messages: canonicalMessages(transactionResponse, "Show my recent transactions"),
    });

    await page.goto("/");
    const mobileNavigation = page.getByRole("navigation", { name: "Primary mobile navigation" });
    const agentDestination = mobileNavigation.getByRole("button", { name: "Agent", exact: true });
    expect((await agentDestination.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await agentDestination.click();

    const agentPage = page.getByTestId("agent-page");
    await expect(agentPage).toBeVisible();
    await expect(agentDestination).toHaveAttribute("aria-current", "page");
    await expect(page.getByLabel("Ask ExpenseOps Agent")).toBeFocused();
    await expect(page.getByText(/deliberately long merchant name/i)).toBeVisible();

    const dimensions = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
    const pageBox = await agentPage.boundingBox();
    expect(pageBox).not.toBeNull();
    expect(pageBox!.width).toBeGreaterThanOrEqual(width - 40);
    expect(pageBox!.width).toBeLessThanOrEqual(width);

    await mobileNavigation.getByRole("button", { name: "Expenses", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Expense Review" })).toBeVisible();
  });
}

test("mobile Agent sends and scrolls a canonical transaction answer before returning to Expenses", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chromium", "Mobile streaming coverage");
  const question = "Show my recent transactions";
  await page.setViewportSize({ width: 390, height: 844 });
  await mockAgentApp(page, {
    messages: canonicalMessages(transactionResponse, question),
  });
  await installAgentStream(page, {
    events: successfulEvents({
      response: transactionResponse,
      deltas: ["I found one matching transaction."],
      activity: "transactions",
    }),
    pauseAfterIndexes: [1],
  });

  await page.goto("/");
  const mobileNavigation = page.getByRole("navigation", { name: "Primary mobile navigation" });
  const agentDestination = mobileNavigation.getByRole("button", { name: "Agent", exact: true });
  await agentDestination.click();
  const agentPage = page.getByTestId("agent-page");
  const composer = agentPage.getByLabel("Ask ExpenseOps Agent");
  await expect(composer).toBeFocused();
  await composer.fill(question);
  await composer.press("Enter");

  await waitForAgentStreamPause(page, 1);
  await expect(
    agentPage.getByText("Looking through your transactions…", { exact: true }).first(),
  ).toBeVisible();
  await releaseAgentStream(page);

  await expect(agentPage.getByLabel("ExpenseOps Agent response in progress")).toHaveCount(0);
  const canonicalAnswer = agentPage.getByLabel("ExpenseOps Agent response", { exact: true });
  await expect(canonicalAnswer.getByText("Recent transactions", { exact: true })).toBeVisible();
  await expect(canonicalAnswer.getByText("Showing 1 of 1", { exact: true })).toBeVisible();
  await expect(canonicalAnswer.getByText(/unbrokenunbrokenunbroken/)).toBeVisible();

  const conversationRegion = agentPage
    .getByRole("list", { name: "Agent conversation messages" })
    .locator("xpath=..");
  const initialScroll = await conversationRegion.evaluate((element) => ({
    conversation: {
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
      scrollWidth: element.scrollWidth,
      clientWidth: element.clientWidth,
    },
    document: {
      clientHeight: document.documentElement.clientHeight,
      scrollHeight: document.documentElement.scrollHeight,
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    },
  }));
  const conversationCanScroll =
    initialScroll.conversation.scrollHeight > initialScroll.conversation.clientHeight;
  expect(conversationCanScroll).toBe(true);
  expect(initialScroll.conversation.scrollWidth).toBeLessThanOrEqual(
    initialScroll.conversation.clientWidth,
  );
  expect(initialScroll.document.scrollWidth).toBeLessThanOrEqual(
    initialScroll.document.clientWidth,
  );
  await conversationRegion.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  await expect
    .poll(() => conversationRegion.evaluate((element) => element.scrollTop))
    .toBeGreaterThan(0);
  const transactionRow = canonicalAnswer.getByRole("button", {
    name: /Open activity for A deliberately long merchant name/i,
  });
  await transactionRow.scrollIntoViewIfNeeded();
  await expect(transactionRow).toBeInViewport();
  await expect(composer).toBeInViewport();
  expect(await agentStreamCallCount(page)).toBe(1);

  await mobileNavigation.getByRole("button", { name: "Expenses", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Expense Review" })).toBeVisible();
  await expect(
    mobileNavigation.getByRole("button", { name: "Expenses", exact: true }),
  ).toHaveAttribute("aria-current", "page");
});

test("semantic and transport failures stay safe and recoverable", async ({ page }, testInfo) => {
  skipUnlessChromium(testInfo);
  const safeMessage = "ExpenseOps could not retrieve that data. Please retry.";
  await mockAgentApp(page, { messages: userMessage("Show spending") });
  await installAgentStream(page, { events: failedEvents(safeMessage) });

  await page.goto("/");
  await openDesktopAgent(page);
  await page.getByLabel("Ask ExpenseOps Agent").fill("Show spending");
  await page.getByLabel("Ask ExpenseOps Agent").press("Enter");

  const alert = page.getByRole("alert");
  await expect(alert).toContainText(safeMessage);
  await expect(alert.getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(page.getByText("raw provider stack secret")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Expense Review" })).toBeVisible();
});

test("a broken stream reports interruption without exposing internals", async ({ page }, testInfo) => {
  skipUnlessChromium(testInfo);
  const [started, terminal] = failedEvents("unused");
  await mockAgentApp(page, { messages: userMessage("Show spending") });
  await installAgentStream(page, { events: [started, terminal], errorBeforeIndex: 1 });

  await page.goto("/");
  await openDesktopAgent(page);
  await page.getByLabel("Ask ExpenseOps Agent").fill("Show spending");
  await page.getByLabel("Ask ExpenseOps Agent").press("Enter");

  const alert = page.getByRole("alert");
  await expect(alert).toContainText("The Agent response was interrupted");
  await expect(alert).not.toContainText("Simulated stream disconnect");
  await expect(alert.getByRole("button", { name: "Retry" })).toBeVisible();
});

test("an uncertain disconnect retries the same client message without a duplicate user bubble", async ({
  page,
}, testInfo) => {
  skipUnlessChromium(testInfo);
  const question = "Show spending during the retry window";
  const response = textResponse("The recovered read-only answer is ready.");
  let includeCanonicalAssistant = false;
  await mockAgentApp(page, {
    messages: (calls) => {
      const clientMessageId = calls.at(-1)?.body?.client_message_id;
      if (!clientMessageId) return [];
      const values = userMessage(question, clientMessageId);
      if (includeCanonicalAssistant) {
        values.push(canonicalMessages(response, question, clientMessageId)[1]);
      }
      return values;
    },
  });
  const [started, terminal] = failedEvents("unused");
  await installAgentStreamSequence(page, [
    { events: [started, terminal], errorBeforeIndex: 1 },
    {
      events: successfulEvents({
        response,
        deltas: ["The recovered read-only answer is ready."],
      }),
      pauseAfterIndexes: [0],
    },
  ]);

  await page.goto("/");
  await openDesktopAgent(page);
  const panel = page.getByTestId("agent-panel");
  await page.getByLabel("Ask ExpenseOps Agent").fill(question);
  await page.getByLabel("Ask ExpenseOps Agent").press("Enter");

  const retry = panel.getByRole("button", { name: "Retry" });
  await expect(retry).toBeVisible();
  await expect(panel.getByText(question, { exact: true })).toHaveCount(1);
  const [originalCall] = await agentStreamCalls(page);
  expect(originalCall.body?.client_message_id).toEqual(expect.any(String));
  expect(originalCall.body?.text).toBe(question);

  includeCanonicalAssistant = true;
  await retry.click();
  await waitForAgentStreamPause(page, 0);
  await expect(panel.getByText(question, { exact: true })).toHaveCount(1);
  const retryCalls = await agentStreamCalls(page);
  expect(retryCalls).toHaveLength(2);
  expect(retryCalls[1].body?.client_message_id).toBe(
    originalCall.body?.client_message_id,
  );
  expect(retryCalls[1].body?.text).toBe(originalCall.body?.text);
  await releaseAgentStream(page);

  await expect(panel.getByLabel("ExpenseOps Agent response in progress")).toHaveCount(0);
  await expect(
    panel.getByText("The recovered read-only answer is ready.", { exact: true }),
  ).toBeVisible();
  await expect(panel.getByText(question, { exact: true })).toHaveCount(1);
});

test("unknown structured blocks fail closed without rendering action controls", async ({ page }, testInfo) => {
  skipUnlessChromium(testInfo);
  await mockAgentApp(page, { messages: userMessage("Do something") });
  await installAgentStream(page, {
    events: [
      {
        schema_version: "1.0",
        sequence: 0,
        run_public_id: "run-public-1",
        type: "run_started",
        resumed: false,
      },
      {
        schema_version: "1.0",
        sequence: 1,
        run_public_id: "run-public-1",
        type: "structured_response",
        response: {
          schema_version: "1.0",
          blocks: [
            {
              type: "action_confirmation",
              title: "Transfer money",
              confirm_label: "Confirm transfer",
            },
          ],
        },
      },
    ],
  });

  await page.goto("/");
  await openDesktopAgent(page);
  await page.getByLabel("Ask ExpenseOps Agent").fill("Do something");
  await page.getByLabel("Ask ExpenseOps Agent").press("Enter");

  await expect(page.getByRole("alert")).toContainText(
    /unsupported Agent response|cannot safely display/i,
  );
  await expect(page.getByRole("button", { name: "Confirm transfer" })).toHaveCount(0);
  await expect(page.getByText("Transfer money", { exact: true })).toHaveCount(0);
});

test("write requests render a refusal and never expose or call write controls", async ({ page }, testInfo) => {
  skipUnlessChromium(testInfo);
  const refusal =
    "I can't split or change transactions in the read-only Agent. No action was taken.";
  const response = textResponse(refusal);
  const fixture = await mockAgentApp(page, {
    messages: canonicalMessages(response, "Split that Costco transaction with Gunjan"),
  });
  await installAgentStream(page, {
    events: successfulEvents({ response, deltas: [refusal] }),
  });

  await page.goto("/");
  await openDesktopAgent(page);
  const panel = page.getByTestId("agent-panel");
  await page.getByLabel("Ask ExpenseOps Agent").fill("Split that Costco transaction with Gunjan");
  await page.getByLabel("Ask ExpenseOps Agent").press("Enter");

  await expect(panel.getByText(refusal, { exact: true })).toBeVisible();
  await expect(
    panel.getByRole("button", { name: /confirm|mark personal|post.*splitwise|delete|buy/i }),
  ).toHaveCount(0);
  expect(
    fixture.requests.filter(
      (request) =>
        request.method !== "GET" &&
        (request.pathname.startsWith("/transactions") || request.pathname.startsWith("/splitwise")),
    ),
  ).toEqual([]);
});

test("an archive transition disables conversation controls and drops an immediate send race", async ({
  page,
}, testInfo) => {
  skipUnlessChromium(testInfo);
  const question = "This must not be sent while archiving";
  const fixture = await mockAgentApp(page, {
    initialConversation: true,
    messages: canonicalMessages(textResponse("Existing answer."), "Existing question"),
    holdArchiveRequest: true,
  });
  await installAgentStream(page, {
    events: successfulEvents({
      response: textResponse("This response must never render."),
      deltas: ["This response must never render."],
    }),
  });

  await page.goto("/");
  await openDesktopAgent(page);
  const panel = page.getByTestId("agent-panel");
  const composer = panel.getByLabel("Ask ExpenseOps Agent");
  const archive = panel.getByRole("button", { name: "Archive", exact: true });
  await composer.fill(question);

  await panel.evaluate((root) => {
    const archiveButton = Array.from(root.querySelectorAll<HTMLButtonElement>("button")).find(
      (button) => button.textContent?.trim() === "Archive",
    );
    const form = root.querySelector<HTMLTextAreaElement>("textarea")?.closest("form");
    if (!archiveButton || !form) throw new Error("Agent archive race controls are unavailable");
    archiveButton.click();
    form.requestSubmit();
  });
  await fixture.waitForArchiveRequest();

  await expect(composer).toBeDisabled();
  await expect(panel.getByLabel("Send message")).toBeDisabled();
  await expect(panel.getByLabel("Start new Agent conversation")).toBeDisabled();
  await expect(archive).toBeDisabled();
  await page.waitForTimeout(50);
  expect(await agentStreamCallCount(page)).toBe(0);

  fixture.releaseArchiveRequest();
  await expect(archive).toHaveCount(0);
  await expect(composer).toBeEnabled();
  expect(await agentStreamCallCount(page)).toBe(0);
});

test("keyboard users can enter multiline text, submit once, and regain launcher focus", async ({ page }, testInfo) => {
  skipUnlessChromium(testInfo);
  const response = textResponse("Here is the safe read-only response.");
  await mockAgentApp(page, {
    messages: canonicalMessages(response, "First line\nSecond line"),
  });
  await installAgentStream(page, {
    events: successfulEvents({ response, deltas: ["Here is the safe read-only response."] }),
  });

  await page.goto("/");
  const launcher = page.getByRole("button", { name: "Agent", exact: true });
  await launcher.focus();
  await launcher.press("Enter");
  const composer = page.getByLabel("Ask ExpenseOps Agent");
  await expect(composer).toBeFocused();
  await composer.fill("First line");
  await composer.press("Shift+Enter");
  await composer.pressSequentially("Second line");
  await expect(composer).toHaveValue("First line\nSecond line");
  await composer.press("Enter");

  await expect(page.getByText("Here is the safe read-only response.", { exact: true })).toBeVisible();
  expect(await agentStreamCallCount(page)).toBe(1);
  await page.keyboard.press("Escape");
  await expect(launcher).toBeFocused();
});

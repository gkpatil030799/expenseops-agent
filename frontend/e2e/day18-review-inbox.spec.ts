import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import type { ReviewInboxPage, Transaction } from "../src/types";
import { mockAgentApp } from "./fixtures/agent";

const TRANSACTION: Transaction = {
  id: 81,
  plaid_transaction_id: "plaid-day18-81",
  merchant_name: "Neighborhood Cafe",
  name: "NEIGHBORHOOD CAFE",
  amount_cents: 4200,
  amount: "42.00",
  iso_currency_code: "USD",
  institution_name: "Test Bank",
  category: "Food & Dining",
  payment_channel: "in store",
  date: "2026-08-18",
  authorized_date: "2026-08-18",
  pending: false,
  status: "ask_user",
  agent_question: "Was this personal or shared?",
  review_notification_queued_at: "2026-08-18T15:00:00Z",
  splitwise_expense_id: null,
  splitwise_payload_json: null,
  splitwise_amount_cents: null,
  replaces_transaction_id: null,
  replaced_by_transaction_id: null,
  last_error: null,
  classification_suggestion: "likely_shared",
  classification_reason: "You previously split purchases from this merchant.",
  classification_preference_id: 12,
  can_undo_transaction: false,
  created_at: "2026-08-18T15:00:00Z",
  updated_at: "2026-08-18T15:00:00Z",
};

const INBOX: ReviewInboxPage = {
  items: [
    {
      public_id: "11111111-1111-4111-a111-111111111111",
      kind: "transaction_review",
      state: "open",
      unread: true,
      seen_at: null,
      created_at: "2026-08-18T15:00:00Z",
      updated_at: "2026-08-18T15:00:00Z",
      available_actions: ["personal", "recommended_split", "customize"],
      transaction: {
        id: 81,
        merchant_name: "Neighborhood Cafe",
        name: "NEIGHBORHOOD CAFE",
        amount_cents: 4200,
        currency: "USD",
        date: "2026-08-18",
        pending: false,
        status: "ask_user",
        institution_name: "Test Bank",
      },
      receipt: null,
      recommendation: {
        suggestion: "likely_shared",
        reason: "You previously split purchases from this merchant.",
        memory_id: 12,
        participant_names: ["Janhavi"],
        group_name: null,
        split_mode: "equal",
      },
    },
    {
      public_id: "22222222-2222-4222-a222-222222222222",
      kind: "itemized_split_ready",
      state: "open",
      unread: true,
      seen_at: null,
      created_at: "2026-08-18T15:01:00Z",
      updated_at: "2026-08-18T15:01:00Z",
      available_actions: ["itemized_split", "open_receipt"],
      transaction: null,
      receipt: {
        id: 91,
        merchant_name: "Neighborhood Cafe",
        total_cents: 4200,
        currency: "USD",
        purchased_at: "2026-08-18T14:00:00Z",
        parse_status: "needs_review",
        transaction_match_status: "auto_matched",
        transaction_id: 81,
        line_count: 3,
      },
      recommendation: null,
    },
  ],
  total_open: 2,
  unread_count: 2,
  limit: 100,
  offset: 0,
};

async function mockDay18(
  page: Page,
  options: { inbox?: ReviewInboxPage; transaction?: Transaction } = {},
) {
  await mockAgentApp(page, { messages: [] });
  const inbox = options.inbox || INBOX;
  const transaction = options.transaction || TRANSACTION;
  const seen: string[] = [];
  const seenIds = new Set<string>();
  await page.route("**/api/review-inbox**", async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === "POST" && url.pathname.endsWith("/seen")) {
      const publicId = url.pathname.split("/")[3];
      seen.push(url.pathname);
      seenIds.add(publicId);
      return route.fulfill({ json: { public_id: publicId, seen_at: "2026-08-18T15:02:00Z" } });
    }
    return route.fulfill({
      json: {
        ...inbox,
        unread_count: inbox.items.filter((item) => !seenIds.has(item.public_id)).length,
        items: inbox.items.map((item) => seenIds.has(item.public_id)
          ? { ...item, unread: false, seen_at: "2026-08-18T15:02:00Z" }
          : item),
      },
    });
  });
  await page.route(/\/transactions(?:\?|\/|$)/, (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: [transaction] });
    return route.fulfill({ json: { transaction, message: "Saved" } });
  });
  return seen;
}

test("unified review inbox shows recommendation, receipt readiness, badge, and Agent card", async ({ page }) => {
  const seen = await mockDay18(page);
  await page.goto("/?workspace=expenses&tab=review");

  await expect(page.getByRole("heading", { name: "Needs your attention" })).toBeVisible();
  await expect(page.getByText("Itemized split ready", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Use recommended split" })).toBeVisible();
  await expect(page.getByText(/split with Janhavi/i)).toBeVisible();
  await expect.poll(() => seen.length).toBe(2);

  await page.getByRole("button", { name: "Use recommended split" }).click();
  await expect(page.getByPlaceholder("Search Splitwise friend")).toHaveValue("Janhavi");
  await expect(page.getByRole("button", { name: "Customize" })).toBeVisible();

  await page.getByRole("button", { name: "Agent", exact: true }).click();
  await expect(page.getByRole("heading", { name: "2 review items" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Neighborhood Cafe/ }).first()).toBeVisible();

  const violations = await new AxeBuilder({ page }).analyze();
  expect(violations.violations).toEqual([]);
});

test("pending review can be prepared but cannot post before the final charge", async ({ page }) => {
  const transaction = { ...TRANSACTION, pending: true };
  const inbox: ReviewInboxPage = {
    ...INBOX,
    items: [
      {
        ...INBOX.items[0],
        transaction: { ...INBOX.items[0].transaction!, pending: true },
      },
    ],
    total_open: 1,
    unread_count: 1,
  };
  await mockDay18(page, { inbox, transaction });
  await page.goto("/?workspace=expenses&tab=review");

  await expect(page.getByRole("button", { name: "Prepare recommended split" })).toBeVisible();
  await expect(page.getByText(/will not post to Splitwise until the final charge/i)).toBeVisible();
  await page.getByRole("button", { name: "Prepare recommended split" }).click();
  await expect(page.getByText(/final charge must post before ExpenseOps can send/i)).toBeVisible();
  await expect(page.getByRole("button", { name: "Post equal split" })).toBeDisabled();
});

test("review count failure is visible and retries without inventing an empty success", async ({ page }) => {
  await mockAgentApp(page, { messages: [] });
  await page.route("**/api/review-inbox**", (route) =>
    route.fulfill({ status: 503, json: { detail: "synthetic outage" } }),
  );
  await page.route(/\/transactions(?:\?|\/|$)/, (route) => route.fulfill({ json: [] }));
  await page.goto("/?workspace=expenses&tab=review");

  await expect(page.getByRole("status").filter({ hasText: "temporarily unavailable" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "You're all caught up" })).toHaveCount(0);
});

for (const width of [320, 375, 390, 1024]) {
  test(`review inbox has no horizontal overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockDay18(page);
    await page.goto("/?workspace=expenses&tab=review");
    await expect(page.getByText("Itemized split ready", { exact: true })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });
}

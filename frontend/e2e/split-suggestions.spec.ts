import { expect, test, type Page } from "@playwright/test";
import { mockAgentApp } from "./fixtures/agent";
import type { Transaction } from "../src/types";

const transaction: Transaction = {
  id: 81, plaid_transaction_id: "split-suggestions-81", merchant_name: "Neighborhood Cafe",
  name: "Neighborhood Cafe", amount_cents: 4200, amount: "42.00", iso_currency_code: "USD",
  date: "2026-08-21", authorized_date: null, pending: false, status: "ask_user",
  agent_question: null, splitwise_expense_id: null, splitwise_payload_json: null,
  last_error: null, classification_suggestion: "unsure", classification_reason: null,
  can_undo_transaction: false, created_at: "2026-08-21T12:00:00Z", updated_at: "2026-08-21T12:00:00Z",
};
const friend = { id: 7, first_name: "Sam", last_name: "Patel", display_name: "Sam Patel", email: "sam@example.com" };
const history = [1, 2].map((id) => ({
  ...transaction, id, status: "posted", splitwise_expense_id: `expense-${id}`,
  splitwise_payload_json: JSON.stringify({ group_id: 44, users__0__user_id: 1, users__1__user_id: 7 }),
}));

async function setup(page: Page, options: { history?: boolean; failed?: boolean } = {}) {
  await mockAgentApp(page);
  await page.route(/\/transactions(?:\?|\/|$)/, (route) => {
    const url = new URL(route.request().url());
    const status = url.searchParams.get("status");
    return route.fulfill({ json: status === "posted" ? (options.history === false ? [] : history)
      : status === "ask_user" ? [transaction] : [] });
  });
  await page.route("**/splitwise/friends*", (route) => options.failed && !route.request().url().includes("?q=")
    ? route.fulfill({ status: 503, json: { detail: "Unavailable" } })
    : route.fulfill({ json: [friend] }));
  await page.route("**/splitwise/groups", (route) => route.fulfill({ json: [{ id: 44, name: "Housemates" }] }));
  await page.route("**/splitwise/groups/44/members", (route) => route.fulfill({ json: [friend] }));
  await page.goto("/?workspace=expenses&tab=review");
  await page.getByRole("button", { name: "Split", exact: true }).click();
}

test("frequent friends and groups can be selected without typing, including after reload", async ({ page }) => {
  await setup(page);
  const friends = page.getByRole("region", { name: "Frequently used friends" });
  await expect(friends.getByText("2 splits")).toBeVisible();
  await friends.getByRole("button", { name: "Select Sam Patel" }).click();
  await expect(friends.getByRole("button", { name: "Select Sam Patel" })).toBeDisabled();
  await expect(page.getByPlaceholder("Search Splitwise friend")).toHaveValue("");
  await page.getByRole("button", { name: "Group", exact: true }).click();
  await page.getByRole("region", { name: "Frequently used groups" }).getByRole("button", { name: "Select Housemates" }).click();
  await expect(page.getByRole("button", { name: "Clear group" })).toBeVisible();
  await page.getByRole("button", { name: /Sam Patel.*sam@example.com/ }).click();
  await expect(page.getByPlaceholder("Search Splitwise group")).toHaveValue("");
  await page.reload();
  await page.getByRole("button", { name: "Split", exact: true }).click();
  await expect(page.getByRole("region", { name: "Frequently used friends" }).getByRole("button", { name: "Select Sam Patel" })).toBeEnabled();
});

test("users without past splits can choose from their contacts immediately", async ({ page }) => {
  await setup(page, { history: false });
  await expect(page.getByRole("region", { name: "Your Splitwise friends" }).getByRole("button", { name: "Select Sam Patel" })).toBeVisible();
});

test("search still works when suggestions fail to load", async ({ page }) => {
  await setup(page, { failed: true });
  await expect(page.getByRole("button", { name: "Retry suggestions" })).toBeVisible();
  await page.getByPlaceholder("Search Splitwise friend").fill("Sam");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page.getByRole("button", { name: /Sam Patel.*sam@example.com/ }).click();
  await expect(page.getByText("No friends selected.")).toHaveCount(0);
});

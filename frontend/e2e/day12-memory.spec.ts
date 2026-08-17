import { expect, test, type Page, type Route } from "@playwright/test";

import type { AIMemory } from "../src/types";
import { mockAgentApp } from "./fixtures/agent";

const NOW = "2026-08-17T12:00:00Z";

function memory(preference: "personal" | "shared" = "shared"): AIMemory {
  const shared = preference === "shared";
  return {
    id: 41,
    original_message: `Costco: usually ${preference}`,
    label: `Costco: usually ${preference}`,
    rationale: shared
      ? "You explicitly saved this preference. Suggested group Apartment · with Gunjan."
      : "You corrected a prior transaction interpretation.",
    source: shared ? "explicit_preference" : "correction",
    failure_reason: "none",
    final_action: shared ? "split_equal" : "personal",
    final_group_name: shared ? "Apartment" : null,
    final_participants: shared ? ["Gunjan"] : [],
    final_split_mode: shared ? "equal" : null,
    payer_included: true,
    custom_values: null,
    correction_type: shared ? "explicit_preference" : "user_edited",
    merchant: "Costco",
    amount_cents: null,
    currency: null,
    usage_count: 1,
    last_used_at: NOW,
    created_at: NOW,
  };
}

async function installMemoryApi(page: Page) {
  let memories: AIMemory[] = [];
  let learning = true;
  const writes: Array<{ method: string; path: string; body: unknown }> = [];
  const handler = async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const body = request.postData() ? request.postDataJSON() : null;
    if (request.method() !== "GET") writes.push({ method: request.method(), path: url.pathname, body });
    if (url.pathname === "/ai/memory/settings") {
      if (request.method() === "PATCH") learning = Boolean((body as { transaction_learning_enabled: boolean }).transaction_learning_enabled);
      return route.fulfill({ json: { transaction_learning_enabled: learning } });
    }
    if (url.pathname === "/ai/memory/metrics") {
      return route.fulfill({ json: { shown: 1, accepted: 0, edited: 0, rejected: 0, agreement_rate: null, correction_rate: null } });
    }
    if (url.pathname === "/ai/memory/preferences") {
      memories = [memory("shared")];
      return route.fulfill({ status: 201, json: memories[0] });
    }
    if (url.pathname === "/ai/memory/41/feedback") return route.fulfill({ json: { ok: true } });
    if (url.pathname === "/ai/memory/41") {
      if (request.method() === "DELETE") {
        memories = [];
        return route.fulfill({ json: { ok: true } });
      }
      memories = [memory("personal")];
      return route.fulfill({ json: memories[0] });
    }
    if (url.pathname === "/ai/memory") return route.fulfill({ json: memories });
    return route.fallback();
  };
  await page.route("**/ai/memory**", handler);
  return writes;
}

test("structured memory is explicit, correctable, explainable, and user-controlled", async ({ page }) => {
  await mockAgentApp(page);
  const writes = await installMemoryApi(page);
  await page.goto("/?workspace=settings");
  const mobileSection = page.getByLabel("Settings section");
  if (await mobileSection.isVisible()) await mobileSection.selectOption("learning");
  else await page.getByRole("button", { name: /Learned behavior/ }).click();

  const learning = page.getByRole("checkbox", { name: /Learn from confirmed/ });
  await expect(learning).toBeChecked();
  await learning.click();
  await expect.poll(() => writes.some((entry) => entry.path === "/ai/memory/settings" && JSON.stringify(entry.body) === '{"transaction_learning_enabled":false}')).toBe(true);
  await expect(learning).not.toBeChecked();

  await page.getByLabel("Merchant").fill("Costco");
  await page.getByLabel("Usual treatment").selectOption("shared");
  await page.getByLabel("People, comma-separated").fill("Gunjan");
  await page.getByLabel("Splitwise group (optional)").fill("Apartment");
  await page.getByRole("button", { name: "Save preference" }).click();
  await expect(page.getByText("Costco: usually shared")).toBeVisible();
  await expect(page.getByText(/explicitly saved.*Apartment.*Gunjan/)).toBeVisible();
  expect(JSON.stringify(writes)).not.toContain("original_message");
  expect(JSON.stringify(writes)).not.toContain("transcript");

  await page.getByRole("button", { name: "Change to personal" }).click();
  await expect(page.getByText("Costco: usually personal")).toBeVisible();
  await page.getByRole("button", { name: "Helpful", exact: true }).click();
  await page.getByRole("button", { name: /Delete preference Costco/ }).click();
  await expect(page.getByText("No structured preferences yet")).toBeVisible();
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
  await expect(page.locator("body")).not.toHaveCSS("overflow-x", "scroll");
});

import { expect, test, type Page, type Request } from "@playwright/test";

import type { AgentStructuredResponse } from "../src/agent/contracts";
import type { AttentionPreference } from "../src/attention";
import { mockAgentApp } from "./fixtures/agent";

const RESPONSE: AgentStructuredResponse = {
  schema_version: "1.0",
  blocks: [
    {
      type: "text",
      text: "Here is what needs attention from the ExpenseOps areas I checked.",
    },
    {
      type: "attention_summary",
      block_version: "1.0",
      title: "Needs attention",
      status: "complete",
      checked_domains: [
        "transactions",
        "replenishment",
        "receipts",
        "deals",
        "errands",
        "integrations",
      ],
      unavailable_domains: [],
      items: [
        {
          priority: "action_required",
          domain: "transactions",
          title: "2 expense reviews",
          detail: "Transactions are waiting for review or reconciliation.",
          count: 2,
          navigation: {
            type: "navigation",
            label: "View expenses",
            target_surface: "expense_review",
          },
        },
        {
          priority: "time_sensitive",
          domain: "replenishment",
          title: "1 household item is likely due",
          detail: "Laundry detergent",
          count: 1,
          navigation: {
            type: "navigation",
            label: "View household",
            target_surface: "household_today",
          },
        },
        {
          priority: "time_sensitive",
          domain: "deals",
          title: "1 relevant deal expires within 7 days",
          detail: "Target",
          count: 1,
          navigation: {
            type: "navigation",
            label: "View deals",
            target_surface: "deals",
          },
        },
      ],
      items_truncated: false,
    },
  ],
};

const DEFAULT_PREFERENCES: AttentionPreference = {
  enabled: true,
  categories: [
    "transactions",
    "receipts",
    "integrations",
    "replenishment",
    "deals",
    "errands",
  ],
  in_app_enabled: true,
  telegram_enabled: false,
  delivery_mode: "digest",
  quiet_start_hour: 22,
  quiet_end_hour: 7,
  timezone: "America/Phoenix",
  max_alerts_per_day: 3,
  cooldown_minutes: 240,
};

async function installAttentionApi(page: Page) {
  let preferences = { ...DEFAULT_PREFERENCES };
  const patches: unknown[] = [];
  let centerCalls = 0;
  await page.route("**/api/attention**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/attention/preferences" && request.method() === "PATCH") {
      const body = request.postDataJSON() as AttentionPreference;
      patches.push(body);
      preferences = { ...body, categories: [...body.categories] };
      return route.fulfill({ json: preferences });
    }
    if (url.pathname === "/api/attention") {
      centerCalls += 1;
      const active = preferences.enabled && preferences.in_app_enabled;
      return route.fulfill({
        json: {
          enabled: active,
          generated_at: "2026-08-17T12:00:00Z",
          response: active ? RESPONSE : null,
          preferences,
        },
      });
    }
    return route.fallback();
  });
  return {
    patches,
    centerCalls: () => centerCalls,
  };
}

function writeRequests(requests: Request[]) {
  return requests.filter((request) => !["GET", "HEAD", "OPTIONS"].includes(request.method()));
}

test("proactive flag hides the surface and deep link makes zero Attention requests", async ({
  page,
}) => {
  const fixture = await mockAgentApp(page, { agentProactive: false });

  await page.goto("/?workspace=expenses&tab=attention");

  await expect(page.getByRole("button", { name: "review", exact: true })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(page.getByRole("button", { name: "attention", exact: true })).toHaveCount(0);
  expect(fixture.requests.filter((request) => request.pathname.startsWith("/api/attention"))).toEqual(
    [],
  );
});

test("Attention Center loads canonical Day 6 cards and semantic navigation without Agent calls", async ({
  page,
}) => {
  const fixture = await mockAgentApp(page, { agentProactive: true });
  const attention = await installAttentionApi(page);
  const requests: Request[] = [];
  page.on("request", (request) => requests.push(request));

  await page.goto("/?workspace=expenses&tab=attention");

  await expect(page.getByRole("heading", { name: "Attention Center" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Needs attention" })).toBeVisible();
  await expect(page.getByText("2 expense reviews")).toBeVisible();
  await expect(page.getByText("Laundry detergent")).toBeVisible();
  await expect(page.getByText("Target", { exact: true })).toBeVisible();
  expect(attention.centerCalls()).toBeGreaterThanOrEqual(1);
  expect(fixture.requests.filter((request) => request.pathname.startsWith("/api/agent"))).toEqual(
    [],
  );
  expect(writeRequests(requests)).toEqual([]);

  await page.getByRole("button", { name: "View expenses" }).click();
  await expect(page.getByRole("button", { name: "review", exact: true })).toHaveAttribute(
    "aria-current",
    "page",
  );
});

test("controls persist an exact bounded preference and disabling in-app stops evaluation", async ({
  page,
}) => {
  await mockAgentApp(page, { agentProactive: true });
  const attention = await installAttentionApi(page);
  await page.goto("/?workspace=expenses&tab=attention");
  await expect(page.getByRole("heading", { name: "Needs attention" })).toBeVisible();
  const initialCenterCalls = attention.centerCalls();

  await page.getByLabel("Show in app").uncheck();
  await page.getByLabel("Telegram enabled").check();
  await page.getByLabel("Expiring relevant deals").uncheck();
  await page.getByLabel("Maximum Telegram alerts per day").fill("2");
  await page.getByRole("button", { name: "Save attention controls" }).click();

  await expect(page.getByText("Attention preferences saved.")).toBeVisible();
  await expect(page.getByText("In-app attention is paused.")).toBeVisible();
  expect(attention.patches).toHaveLength(1);
  expect(attention.patches[0]).toEqual({
    ...DEFAULT_PREFERENCES,
    categories: ["transactions", "receipts", "integrations", "replenishment", "errands"],
    in_app_enabled: false,
    telegram_enabled: true,
    max_alerts_per_day: 2,
  });
  expect(JSON.stringify(attention.patches[0])).not.toContain("response");
  expect(attention.centerCalls()).toBe(initialCenterCalls + 1);
});

test("mobile Attention controls and canonical cards do not overflow", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chromium", "mobile viewport coverage");
  await mockAgentApp(page, { agentProactive: true });
  await installAttentionApi(page);

  for (const width of [320, 375, 390]) {
    await page.setViewportSize({ width, height: 760 });
    await page.goto("/?workspace=expenses&tab=attention");
    await expect(page.getByRole("heading", { name: "Needs attention" })).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
    const box = await page.getByRole("button", { name: "Save attention controls" }).boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  }
});

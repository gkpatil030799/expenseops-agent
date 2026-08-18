import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

import type { PurchaseReceipt } from "../src/types";
import { mockAgentApp } from "./fixtures/agent";
import { emptyClassificationActivity } from "./fixtures/classification";

const COMPLETE_RECEIPT: PurchaseReceipt = {
  id: 151,
  source: "web",
  merchant: "Trader Joe's",
  purchased_at: "2026-08-17T10:50:00-07:00",
  total_cents: 8560,
  currency: "USD",
  parse_status: "needs_review",
  parse_quality: "complete",
  quality_message: null,
  parse_confidence: 0.97,
  failure_code: null,
  transaction_id: null,
  created_at: "2026-08-17T17:51:00Z",
  updated_at: "2026-08-17T17:51:00Z",
  decision_summary: { tracked: 0, ignored: 0, undecided: 1, total: 1 },
  items: [
    {
      id: 1511,
      raw_name: "PAPER TOWELS 12 ROLLS",
      normalized_name: "paper towels 12 rolls",
      quantity: 1,
      unit: "pack",
      line_total_cents: 5000,
      household_item_id: null,
      household_item_name: null,
      acquisition_id: null,
      match_status: "unmatched",
      match_confidence: 0.97,
      classification: "replenishable_household",
      classification_confidence: 0.98,
      canonical_name: "Paper towels",
    },
  ],
};

async function installHouseholdReceiptApi(page: Page) {
  let receipts: PurchaseReceipt[] = [];
  const uploads: { contentType: string; body: Buffer | null }[] = [];
  let nextUpload: PurchaseReceipt = COMPLETE_RECEIPT;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/replenishment/receipts/upload") {
      uploads.push({
        contentType: request.headers()["content-type"] || "",
        body: request.postDataBuffer(),
      });
      receipts = [nextUpload];
      return route.fulfill({ json: nextUpload });
    }
    if (url.pathname === "/api/household/errands") return route.fulfill({ json: [] });
    if (url.pathname === "/api/household/items") return route.fulfill({ json: [] });
    if (url.pathname === "/api/household/errand-plans/latest") {
      return route.fulfill({ json: null });
    }
    if (url.pathname === "/api/household/locations") return route.fulfill({ json: [] });
    if (url.pathname === "/api/replenishment/summary") {
      return route.fulfill({
        json: {
          this_week: [],
          learning: { confirmed_acquisitions: 0, items_with_history: 0, active_model: null },
          accuracy: { evaluated_predictions: 0, confidence_level: "insufficient" },
        },
      });
    }
    if (url.pathname === "/api/replenishment/classification-activity") {
      return route.fulfill({ json: emptyClassificationActivity });
    }
    if (url.pathname === "/api/replenishment/receipts") {
      const bucket = url.searchParams.get("bucket");
      const items = bucket === "active" ? receipts : [];
      return route.fulfill({
        json: { items, total: items.length, limit: 25, offset: 0, has_more: false },
      });
    }
    if (url.pathname === "/api/replenishment/gmail/status") {
      return route.fulfill({
        json: { configured: false, last_successful_sync_at: null, latest_receipt_at: null },
      });
    }
    return route.fallback();
  });
  return {
    uploads,
    setNextUpload(receipt: PurchaseReceipt) {
      nextUpload = receipt;
    },
  };
}

async function openReceipts(page: Page) {
  await page.goto("/?workspace=household");
  await page.getByRole("button", { name: /^Receipts/ }).click();
  await expect(page.getByRole("button", { name: "Take or upload receipt photo" })).toBeVisible();
}

test("photo is the primary web receipt path and complete extraction opens review", async ({
  page,
}, testInfo: TestInfo) => {
  test.skip(testInfo.project.name === "mobile-chromium", "Desktop upload behavior coverage");
  await mockAgentApp(page, { agentEnabled: false });
  const fixture = await installHouseholdReceiptApi(page);
  await openReceipts(page);

  await page.getByLabel("Take or upload a receipt photo").setInputFiles({
    name: "phone-receipt.jpg",
    mimeType: "image/jpeg",
    buffer: Buffer.from([0xff, 0xd8, 0xff, 0xdb, 0x01, 0x02]),
  });

  await expect(page.getByText("Receipt processed from Trader Joe's.")).toBeVisible();
  await expect(page.getByText("PAPER TOWELS 12 ROLLS")).toBeVisible();
  await expect(page.getByText("Recommended: track as Paper towels.")).toBeVisible();
  expect(fixture.uploads).toHaveLength(1);
  expect(fixture.uploads[0].contentType).toMatch(/^multipart\/form-data; boundary=/);
  expect(fixture.uploads[0].contentType).not.toContain("application/json");
  expect(fixture.uploads[0].body?.includes(Buffer.from("phone-receipt.jpg"))).toBe(true);

  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(
    axe.violations.filter(
      (violation) => violation.impact === "critical" || violation.impact === "serious",
    ),
  ).toEqual([]);
});

test("partial and failed photo states use useful human-facing copy", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile-chromium", "Desktop quality-state coverage");
  await mockAgentApp(page, { agentEnabled: false });
  const fixture = await installHouseholdReceiptApi(page);
  fixture.setNextUpload({
    ...COMPLETE_RECEIPT,
    id: 152,
    parse_quality: "partial",
    quality_message: "I found useful details but could not reliably read the total.",
    failure_code: "receipt_total_uncertain",
    total_cents: null,
  });
  await openReceipts(page);
  const input = page.getByLabel("Take or upload a receipt photo");
  await input.setInputFiles({
    name: "blurry.jpg",
    mimeType: "image/jpeg",
    buffer: Buffer.from([0xff, 0xd8, 0x01]),
  });
  await expect(
    page.getByText("I found useful details but could not reliably read the total.").first(),
  ).toBeVisible();
  await expect(page.getByText("PAPER TOWELS 12 ROLLS")).toBeVisible();

  fixture.setNextUpload({
    ...COMPLETE_RECEIPT,
    id: 153,
    merchant: null,
    total_cents: null,
    parse_status: "failed",
    parse_quality: "failed",
    quality_message: "This image does not look like a receipt.",
    failure_code: "receipt_non_receipt",
    decision_summary: { tracked: 0, ignored: 0, undecided: 0, total: 0 },
    items: [],
  });
  await input.setInputFiles({
    name: "vacation.jpg",
    mimeType: "image/jpeg",
    buffer: Buffer.from([0xff, 0xd8, 0x02]),
  });
  await expect(page.getByText("This image does not look like a receipt.").first()).toBeVisible();
  await expect(page.getByText(/MIME|schema|tokenizer|provider error/i)).toHaveCount(0);
});

test("camera-first receipt control is touch-sized and bounded at 320px", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chromium", "Mobile camera coverage");
  await page.setViewportSize({ width: 320, height: 700 });
  await mockAgentApp(page, { agentEnabled: false });
  await installHouseholdReceiptApi(page);
  await openReceipts(page);

  const upload = page.getByRole("button", { name: "Take or upload receipt photo" });
  expect((await upload.boundingBox())?.height).toBeGreaterThanOrEqual(44);
  const input = page.getByLabel("Take or upload a receipt photo");
  await expect(input).toHaveAttribute("accept", "image/jpeg,image/png,image/webp,application/pdf");
  await expect(input).toHaveAttribute("capture", "environment");
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
});

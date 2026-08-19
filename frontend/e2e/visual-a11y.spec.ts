import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { emptyClassificationActivity } from "./fixtures/classification";
import { mockInsightsDateRanges } from "./fixtures/insights";

const context = {
  user: {
    id: 1,
    email: "gunjan@example.com",
    display_name: "Gunjan Patil",
    avatar_url: null,
  },
  workspace: {
    id: 1,
    name: "Patil household",
    workspace_type: "household",
  },
};

async function mockExpenseDashboard(page: Page, transactions: unknown[] = []) {
  await page.route("**/api/**", (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/api/context") return route.fulfill({ json: context });
    if (pathname === "/api/review-inbox") return route.fulfill({ json: {
      items: [],
      total_open: 0,
      unread_count: 0,
      limit: 100,
      offset: 0,
    } });
    if (pathname === "/api/insights/activity") return route.fulfill({ json: [] });
    if (pathname === "/api/insights/financial-activity") return route.fulfill({ json: {
      events: [{
        id: 9,
        event_type: "splitwise_expense_posted",
        action: "splitwise_create",
        outcome: "succeeded",
        actor_user_id: 1,
        actor_display_name: "Gunjan Patil",
        channel: "dashboard",
        attempt: 1,
        transaction_id: 44,
        merchant_name: "Aldi",
        amount_cents: 4850,
        currency_code: "USD",
        provider_object_id: "splitwise-44",
        correlation_id: "request-44",
        created_at: "2026-08-12T18:30:00Z",
      }],
      total: 1,
      limit: 200,
      offset: 0,
      has_more: false,
    } });
    return route.fulfill({ status: 503, json: { detail: "Provider unavailable in visual test" } });
  });
  await page.route(/.*\/transactions.*/, (route) => route.fulfill({ json: transactions }));
  await page.route("**/splitwise/me", (route) =>
    route.fulfill({
      json: { id: 1, first_name: "Gunjan", last_name: "Patil", email: "gunjan@example.com" },
    }),
  );
  await page.route("**/ai/memory", (route) => route.fulfill({ json: [] }));
  await page.route("**/ai/memory/settings", (route) => route.fulfill({ json: { transaction_learning_enabled: true } }));
  await page.route("**/ai/memory/metrics", (route) => route.fulfill({ json: { shown: 0, accepted: 0, edited: 0, rejected: 0, agreement_rate: null, correction_rate: null } }));
}

async function mockSpendingInsights(page: Page) {
  await mockInsightsDateRanges(page);
  await page.route("**/api/insights/spending?**", (route) => route.fulfill({ json: {
    range: {
      start_date: "2026-07-14",
      end_date: "2026-08-12",
      previous_start_date: "2026-06-14",
      previous_end_date: "2026-07-13",
      granularity: "week",
    },
    scope: {
      currency: "USD",
      available_currencies: ["USD"],
      excluded_other_currency_transactions: 0,
      spend_basis: "card",
      viewer_share_identity_connected: true,
      pending_transactions_excluded: true,
    },
    summary: { total_cents: 128450, personal_cents: 74200, shared_cents: 43800, classified_cents: 118000, unreviewed_cents: 10450, credits_cents: 0, unknown_share_transactions: 0, unknown_credit_share_transactions: 0, transaction_count: 31, average_cents: 4144 },
    comparison: { total_cents: 116200, personal_cents: 68100, shared_cents: 39400, classified_cents: 107500, unreviewed_cents: 8700, credits_cents: 0, unknown_share_transactions: 0, unknown_credit_share_transactions: 0, transaction_count: 28, average_cents: 4150 },
    trend: [
      { period: "2026-07-13", total_cents: 17400, personal_cents: 11200, shared_cents: 5100, transactions: 5 },
      { period: "2026-07-20", total_cents: 28200, personal_cents: 16100, shared_cents: 9800, transactions: 7 },
      { period: "2026-07-27", total_cents: 22150, personal_cents: 12400, shared_cents: 8400, transactions: 6 },
      { period: "2026-08-03", total_cents: 37600, personal_cents: 19700, shared_cents: 14500, transactions: 8 },
      { period: "2026-08-10", total_cents: 23100, personal_cents: 14800, shared_cents: 6000, transactions: 5 },
    ],
    category_breakdown: [
      { name: "Food & Dining", amount_cents: 46200, transaction_count: 12, percentage: 36, previous_amount_cents: 38200 },
      { name: "Home & Bills", amount_cents: 31800, transaction_count: 5, percentage: 25, previous_amount_cents: 34000 },
      { name: "Transportation", amount_cents: 22800, transaction_count: 7, percentage: 18, previous_amount_cents: 19100 },
      { name: "Lifestyle", amount_cents: 17650, transaction_count: 4, percentage: 14, previous_amount_cents: 16400 },
      { name: "Health", amount_cents: 10000, transaction_count: 3, percentage: 8, previous_amount_cents: 8500 },
    ],
    subcategory_breakdown: [],
    merchant_breakdown: [
      { name: "Aldi", amount_cents: 24100, transaction_count: 4 },
      { name: "APS", amount_cents: 19800, transaction_count: 1 },
      { name: "Costco", amount_cents: 17600, transaction_count: 2 },
      { name: "Shell", amount_cents: 9200, transaction_count: 3 },
      { name: "Target", amount_cents: 8700, transaction_count: 2 },
    ],
    personal_shared: { personal: 74200, shared: 43800 },
    shared_people: [{ name: "Janhavi", amount_cents: 27600 }, { name: "Rahul", amount_cents: 16200 }],
    shared_groups: [{ name: "Apartment", amount_cents: 43800 }],
    category_trend: [
      { period: "2026-07-13", categories: { "Food & Dining": 6500, "Home & Bills": 5800, Transportation: 5100 } },
      { period: "2026-07-20", categories: { "Food & Dining": 9800, "Home & Bills": 8200, Transportation: 5300, Lifestyle: 4900 } },
      { period: "2026-07-27", categories: { "Food & Dining": 7100, "Home & Bills": 6000, Transportation: 4550, Health: 4500 } },
      { period: "2026-08-03", categories: { "Food & Dining": 15100, "Home & Bills": 7800, Transportation: 5200, Lifestyle: 9500 } },
      { period: "2026-08-10", categories: { "Food & Dining": 7700, "Home & Bills": 4000, Transportation: 2650, Lifestyle: 3250, Health: 5500 } },
    ],
    notable_changes: [
      { kind: "category", direction: "up", label: "Food & Dining", amount_cents: 8000, detail: "+$80 vs previous period" },
      { kind: "merchant", direction: "neutral", label: "Aldi", amount_cents: 24100, detail: "Top merchant · $241" },
    ],
    accounts: ["Chase checking", "Freedom card"],
    categories: ["Food & Dining", "Health", "Home & Bills", "Lifestyle", "Transportation"],
    merchants: ["Aldi", "APS", "Costco", "Shell", "Target"],
    data_quality: { unknown_share_transactions: 0, unknown_credit_share_transactions: 0, unreviewed_cents: 10450, pending_review_cents: 10450, uncategorized_cents: 0, pending_transactions_excluded: true },
  } }));
}

async function mockHouseholdOps(page: Page, options: { allClear?: boolean } = {}) {
  const errands = options.allClear ? [] : [
    { id: 11, title: "Pick up prescription", errand_type: "pickup", status: "planned", priority: "normal", place_name: "CVS Pharmacy", place_address: "500 N Central Ave, Phoenix, AZ", resolved_place_name: "CVS Pharmacy", resolved_place_address: "500 N Central Ave, Phoenix, AZ", resolved_latitude: 33.45, resolved_longitude: -112.07, resolved_provider_place_id: "cvs-1", resolved_open_now: true, resolved_opening_hours: null, place_resolution_status: "resolved", place_resolution_method: "automatic", due_at: null, estimated_duration_minutes: 10, notes: null, included_in_next_plan: true, linked_household_items: [], created_at: "2026-08-12T12:00:00Z", updated_at: "2026-08-12T12:00:00Z" },
    { id: 12, title: "Shop for groceries", errand_type: "purchase", status: "planned", priority: "normal", place_name: "Aldi", place_address: "1401 E Bell Rd, Phoenix, AZ", resolved_place_name: "Aldi", resolved_place_address: "1401 E Bell Rd, Phoenix, AZ", resolved_latitude: 33.64, resolved_longitude: -112.05, resolved_provider_place_id: "aldi-1", resolved_open_now: true, resolved_opening_hours: null, place_resolution_status: "resolved", place_resolution_method: "automatic", due_at: null, estimated_duration_minutes: 15, notes: null, included_in_next_plan: true, linked_household_items: [], created_at: "2026-08-12T12:00:00Z", updated_at: "2026-08-12T12:00:00Z" },
  ];
  const plan = options.allClear ? null : {
    id: 7,
    planned_for: null,
    base_location: "Home, 123 W Main Street",
    status: "planned",
    routing_provider: "google_routes",
    routing_is_optimized: true,
    route_url: "https://www.google.com/maps/dir/",
    is_stale: false,
    stale_reason: null,
    estimated_stop_minutes: 25,
    travel_duration_minutes: 31,
    distance_meters: 19312,
    baseline_travel_duration_minutes: 18,
    incremental_travel_duration_minutes: 13,
    available_minutes: 75,
    planning_mode: "while_out",
    primary_destination: null,
    final_destination: "Home, 123 W Main Street",
    stop_count: 2,
    stops: [
      { id: 21, stop_order: 1, place_name: "CVS Pharmacy", place_address: "500 N Central Ave, Phoenix, AZ", errands: [], household_items: [] },
      { id: 22, stop_order: 2, place_name: "Aldi", place_address: "1401 E Bell Rd, Phoenix, AZ", errands: [], household_items: [] },
    ],
    created_at: "2026-08-12T12:00:00Z",
    updated_at: "2026-08-12T12:00:00Z",
  };
  const responses: Record<string, unknown> = {
    "/api/household/errands": errands,
    "/api/household/items": [],
    "/api/household/errand-plans/latest": plan,
    "/api/household/locations": [{ id: 1, label: "Home", address: "123 W Main Street", latitude: 33.4, longitude: -112.1, location_type: "home", created_at: "2026-08-01T12:00:00Z", updated_at: "2026-08-01T12:00:00Z" }],
    "/api/replenishment/summary": { this_week: [], learning: { confirmed_acquisitions: 0, items_with_history: 0, active_model: null }, accuracy: { evaluated_predictions: 0, confidence_level: "insufficient" } },
    "/api/replenishment/classification-activity": emptyClassificationActivity,
    "/api/replenishment/receipts": [],
    "/api/replenishment/gmail/status": { configured: false, last_successful_sync_at: null, latest_receipt_at: null },
  };
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    const key = url.pathname === "/api/replenishment/receipts" ? url.pathname : url.pathname;
    if (key in responses) return route.fulfill({ json: responses[key] });
    return route.fallback();
  });
}

async function mockSettings(page: Page, role = "owner") {
  const consents = {
    gmail_receipts: false,
    gmail_promotions: false,
    model_receipt_processing: false,
    model_transaction_classification: false,
    structured_transaction_learning: false,
  };
  await page.route("**/api/**", (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/api/workspaces") return route.fulfill({ json: [{ id: 1, name: "Patil household", role, current: true }] });
    if (pathname === "/api/workspaces/1/members") return route.fulfill({ json: [
      { user_id: 1, email: "gunjan@example.com", display_name: "Gunjan Patil", role },
      { user_id: 2, email: "janhavi@example.com", display_name: "Janhavi", role: "member" },
    ] });
    if (pathname === "/api/integrations") return route.fulfill({ json: {
      gmail: { connected: true, identity: "household@gmail.com" },
      plaid: { connected: true, institutions: [{ id: 8, name: "Chase", owner_user_id: 1, owner_name: "Gunjan Patil", ownership_verified: true, is_mine: true }] },
      telegram: { connected: true, telegram_user_id: "123456", chat_id: "123456" },
      splitwise: { connected: true, available: true, identity: "Gunjan Patil", email: "gunjan@example.com", verified: true },
      google_maps: { connected: true, managed_by: "application" },
      openai: { connected: true, managed_by: "application" },
    } });
    if (pathname === "/api/integrations/onboarding") return route.fulfill({ json: { complete: true } });
    if (pathname === "/api/classification/settings") return route.fulfill({ json: {
      autonomous_enabled: true,
      global_rollout_enabled: false,
      effective_autonomous_enabled: false,
    } });
    if (pathname === "/api/privacy" && route.request().method() === "GET") return route.fulfill({ json: {
      policy_version: "2026-08-13",
      privacy_url: "/legal/privacy",
      terms_url: "/legal/terms",
      support_email: "support@example.com",
      retention: { authentication_sessions_days: 30, webhook_delivery_metadata_days: 30, completed_delivery_events_days: 30, promotion_messages_days: 180, ignored_receipts_days: 365, financial_audit_events_days: 2555 },
      consents,
      deletion: { confirmation: "DELETE gunjan@example.com", financial_history_retained_for_audit: true },
    } });
    if (pathname === "/api/privacy/consents" && route.request().method() === "POST") {
      const payload = route.request().postDataJSON() as { purpose: keyof typeof consents; granted: boolean };
      consents[payload.purpose] = payload.granted;
      return route.fulfill({ json: { ...payload, policy_version: "2026-08-13" } });
    }
    return route.fallback();
  });
  const groups = [{ id: 10, name: "Apartment", group_type: "home", simplify_by_default: true, invite_link: "https://www.splitwise.com/join/abc", members: [] }];
  const friends = [
    { id: 1, first_name: "Gunjan", last_name: "Patil", display_name: "Gunjan Patil", email: "gunjan@example.com", registration_status: "confirmed" },
    { id: 2, first_name: "Janhavi", last_name: "", display_name: "Janhavi", email: "janhavi@example.com", registration_status: "confirmed" },
  ];
  await page.route("**/splitwise/groups", (route) => route.fulfill({ json: groups }));
  await page.route("**/splitwise/friends", (route) => route.fulfill({ json: friends }));
  await page.route("**/splitwise/groups/10", (route) => route.fulfill({ json: groups[0] }));
  await page.route("**/splitwise/groups/10/members", (route) => route.fulfill({ json: friends }));
}

async function mockDeals(page: Page, options: { connected?: boolean; empty?: boolean; offers?: unknown[] } = {}) {
  const offers = options.offers ?? (options.empty ? [] : [
    {
      id: 71,
      merchant: "Target",
      category: "Shopping",
      headline: "Save on your next home order",
      description: "A practical offer for household essentials and pickup orders.",
      offer_type: "amount_off",
      percent_off: null,
      amount_off: 25,
      minimum_spend: 100,
      promo_code: "HOME25",
      expires_at: "2099-12-31T23:59:59Z",
      expiry_precision: "exact",
      destination_url: "https://www.target.com/circle/o/target-circle/-/123",
      destination_domain: "target.com",
      terms_summary: "Valid on one qualifying order.",
      trust_status: "trusted",
      trust_reason: "Merchant domain verified.",
      status: "active",
      score: 91,
      saved: false,
      why: ["Matches household shopping", "Strong offer value"],
      source_count: 1,
    },
    {
      id: 72,
      merchant: "Neighborhood Market",
      category: "Groceries",
      headline: "Weekend grocery savings",
      description: "An offer imported from a promotional email.",
      offer_type: "percent_off",
      percent_off: 15,
      amount_off: null,
      minimum_spend: null,
      promo_code: null,
      expires_at: null,
      expiry_precision: "unknown",
      destination_url: "https://offers.example.net/weekend",
      destination_domain: "offers.example.net",
      terms_summary: null,
      trust_status: "review",
      trust_reason: "Destination domain was not verified.",
      status: "active",
      score: 77,
      saved: true,
      why: ["Relevant to groceries"],
      source_count: 1,
    },
  ]);
  await page.route("**/api/promotions?**", (route) => route.fulfill({ json: {
    items: offers,
    total: offers.length,
    saved_total: offers.filter((offer) => Boolean((offer as { saved?: boolean }).saved)).length,
    limit: 100,
    offset: 0,
    has_more: false,
  } }));
  await page.route("**/api/promotions/categories", (route) => route.fulfill({ json: ["Groceries", "Shopping"] }));
  await page.route("**/api/integrations", (route) => route.fulfill({ json: {
    gmail: { connected: options.connected ?? true },
    plaid: { connected: true, institutions: [] },
    telegram: { connected: true },
    splitwise: { connected: true, available: true },
    google_maps: { connected: true, managed_by: "application" },
    openai: { connected: true, managed_by: "application" },
  } }));
}

async function chooseSettingsSection(page: Page, value: string, desktopLabel: RegExp) {
  const mobileSelect = page.getByLabel("Settings section");
  if (await mobileSelect.isVisible()) await mobileSelect.selectOption(value);
  else await page.getByRole("button", { name: desktopLabel }).click();
}

async function expectMobileTouchTargets(page: Page) {
  const undersized = await page.evaluate(() => {
    const selector = "button, a[href], input, select, textarea, summary, [role='button']";
    return Array.from(document.querySelectorAll<HTMLElement>(selector))
      .filter((element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
      })
      .map((element) => {
        const input = element instanceof HTMLInputElement ? element : null;
        const labeledControl = input && (input.type === "checkbox" || input.type === "radio") ? element.closest("label") : null;
        const rect = (labeledControl || element).getBoundingClientRect();
        return {
          element: element.tagName.toLowerCase(),
          name: element.getAttribute("aria-label") || labeledControl?.textContent?.trim().replace(/\s+/g, " ").slice(0, 80) || element.textContent?.trim().replace(/\s+/g, " ").slice(0, 80) || "unnamed",
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        };
      })
      .filter(({ width, height }) => width < 44 || height < 44);
  });
  expect(undersized, `Undersized touch targets: ${JSON.stringify(undersized, null, 2)}`).toEqual([]);
}

test("visual foundation keeps the expense dashboard stable", async ({ page }) => {
  await mockExpenseDashboard(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Expense Review" })).toBeVisible();
  await expect(page).toHaveScreenshot("expense-review-empty.png", { fullPage: true, timeout: 15_000 });
});

test("document branding is complete and product-facing", async ({ page }) => {
  await mockExpenseDashboard(page);
  await page.goto("/");

  await expect(page).toHaveTitle("ExpenseOps");
  await expect(page.locator('meta[name="description"]')).toHaveAttribute("content", /review shared expenses/i);
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute("content", "#0f172a");
  await expect(page.locator('link[rel="icon"]')).toHaveAttribute("href", "/static/favicon.svg");
});

test("@a11y expense dashboard has no serious accessibility violations", async ({ page }) => {
  await mockExpenseDashboard(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Expense Review" })).toBeVisible();

  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const blocking = results.violations.filter(
    (violation) => violation.impact === "critical" || violation.impact === "serious",
  );
  expect(blocking).toEqual([]);
});

test("primary destinations remain reachable from the responsive shell", async ({ page }) => {
  await mockExpenseDashboard(page);
  await page.goto("/");

  await page.getByRole("button", { name: "Household" }).click();
  await expect(page.getByRole("heading", { name: "Household operations" })).toBeVisible();

  await page.getByRole("button", { name: "Deals" }).click();
  await expect(page.getByRole("heading", { name: "Deals worth your attention" })).toBeVisible();

  await page.getByRole("button", { name: "Expenses", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Expense Review" })).toBeVisible();
});

test("mobile headers stay compact and Insights answers the primary question above the fold", async ({ page }) => {
  await page.setViewportSize({ width: 393, height: 727 });
  await mockExpenseDashboard(page);
  await mockSpendingInsights(page);
  await mockHouseholdOps(page);
  await mockDeals(page);
  await page.goto("/");

  const header = page.locator('[data-ui="page-header"]');
  await expect(header).toBeVisible();
  expect(await header.evaluate((element) => Math.round(element.getBoundingClientRect().height))).toBeLessThanOrEqual(190);

  await page.getByRole("button", { name: /insights/i, exact: true }).click();
  await expect(page.getByRole("heading", { name: "Spending Insights" })).toBeVisible();
  const total = page.getByLabel("Spending overview").getByText("$1,285", { exact: true });
  await expect(total).toBeVisible();
  expect(await total.evaluate((element) => Math.round(element.getBoundingClientRect().bottom))).toBeLessThanOrEqual(727);

  await page.getByRole("button", { name: "Household" }).click();
  await expect(page.getByRole("heading", { name: "Household operations" })).toBeVisible();
  expect(await header.evaluate((element) => Math.round(element.getBoundingClientRect().height))).toBeLessThanOrEqual(190);

  await page.getByRole("button", { name: "Deals" }).click();
  await expect(page.getByRole("heading", { name: "Deals worth your attention" })).toBeVisible();
  expect(await header.evaluate((element) => Math.round(element.getBoundingClientRect().height))).toBeLessThanOrEqual(190);
});

test("household Today prioritizes one action and summarizes the route", async ({ page }) => {
  await mockExpenseDashboard(page);
  await mockHouseholdOps(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Household" }).click();

  await expect(page.getByRole("heading", { name: "Household operations" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recommended next action" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Review your next route" })).toBeVisible();
  await expect(page.getByLabel("Route sequence")).toContainText("Start");
  await expect(page.getByLabel("Route sequence")).toContainText("CVS Pharmacy");
  await expect(page.getByLabel("Route sequence")).toContainText("Aldi");
  await expect(page.getByRole("heading", { name: "Find useful life-admin stops" })).toHaveCount(0);
  await expect(page).toHaveScreenshot("household-today.png", { fullPage: true, timeout: 15_000 });

  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const blocking = results.violations.filter(
    (violation) => violation.impact === "critical" || violation.impact === "serious",
  );
  expect(blocking).toEqual([]);

  await page.getByRole("button", { name: "Errands", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Find useful life-admin stops" })).toBeVisible();
});

test("household Today uses one compact all-clear state", async ({ page }) => {
  await mockExpenseDashboard(page);
  await mockHouseholdOps(page, { allClear: true });
  await page.goto("/");
  await page.getByRole("button", { name: "Household" }).click();

  await expect(page.getByRole("heading", { name: "Household queue is clear" })).toBeVisible();
  await expect(page.getByText("Receipts to review")).toHaveCount(0);
  await expect(page.getByText("Active errands")).toHaveCount(0);
});

test("household tabs do not create mobile document overflow", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await mockExpenseDashboard(page);
  await mockHouseholdOps(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Household" }).click();
  await expect(page.getByRole("heading", { name: "Household operations" })).toBeVisible();

  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
});

test("receipt review submits staged decisions as one atomic request", async ({ page }) => {
  await mockExpenseDashboard(page);
  await mockHouseholdOps(page, { allClear: true });
  const receipt = {
    id: 51,
    source: "gmail",
    merchant: "Aldi",
    purchased_at: "2026-08-12T18:00:00Z",
    total_cents: 1899,
    currency: "USD",
    parse_status: "needs_review",
    parse_quality: "complete",
    quality_message: null,
    parse_confidence: 0.94,
    failure_code: null,
    transaction_id: null,
    created_at: "2026-08-12T18:01:00Z",
    updated_at: "2026-08-12T18:01:00Z",
    decision_summary: { tracked: 0, ignored: 0, undecided: 1, total: 1 },
    items: [{ id: 501, raw_name: "Basmati Rice", normalized_name: "basmati rice", quantity: 1, unit: "bag", line_total_cents: 1899, household_item_id: null, household_item_name: null, acquisition_id: null, match_status: "unmatched", match_confidence: 0.4, classification: "uncertain", classification_confidence: 0.4, canonical_name: null }],
  };
  await page.route("**/api/household/items", (route) => route.fulfill({ json: [{ id: 71, name: "Rice", quantity: "1", unit: "bag", cadence_days: 30, cadence_source: "configured", replenishment_mode: "either", enabled: true, should_surface: false }] }));
  await page.route("**/api/replenishment/receipts?**", (route) => {
    const bucket = new URL(route.request().url()).searchParams.get("bucket");
    return route.fulfill({ json: bucket === "active" ? { items: [receipt], total: 1, limit: 25, offset: 0, has_more: false } : { items: [], total: 0, limit: 25, offset: 0, has_more: false } });
  });
  let patchCalls = 0;
  let submitted: Record<string, unknown> | null = null;
  await page.route("**/api/replenishment/receipts/51/items/**", (route) => {
    patchCalls += 1;
    return route.fulfill({ status: 500, json: { detail: "Line-by-line writes are forbidden" } });
  });
  await page.route("**/api/replenishment/receipts/51/decisions", async (route) => {
    submitted = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({ json: {
      ...receipt,
      updated_at: "2026-08-12T18:02:00Z",
      decision_summary: { tracked: 1, ignored: 0, undecided: 0, total: 1 },
      items: [{ ...receipt.items[0], household_item_id: 71, household_item_name: "Rice", match_status: "matched", match_confidence: 1 }],
    } });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Household" }).click();
  await page.getByRole("button", { name: /^Receipts/ }).click();
  await page.getByRole("button", { name: "Review receipt" }).click();
  await page.getByLabel("Match Basmati Rice").selectOption("71");
  await expect(page.getByText(/Decision staged/)).toBeVisible();
  expect(patchCalls).toBe(0);
  await page.getByRole("button", { name: "Save 1 decision" }).click();
  await expect(page.getByText("Saved 1 receipt decision together.")).toBeVisible();
  expect(submitted).toEqual({
    expected_updated_at: "2026-08-12T18:01:00Z",
    decisions: [{ line_id: 501, decision: "match", household_item_id: 71 }],
    finalize: "save",
  });
  expect(patchCalls).toBe(0);
});

test("new-user receipt review proposes useful staples and confirms one cadence-free batch", async ({ page }) => {
  await mockExpenseDashboard(page);
  await mockHouseholdOps(page, { allClear: true });
  const baseLine = {
    normalized_name: "line",
    quantity: 1,
    unit: "each",
    line_total_cents: 1000,
    household_item_id: null,
    household_item_name: null,
    acquisition_id: null,
    match_confidence: 0.95,
  };
  const receipt = {
    id: 91,
    source: "gmail",
    merchant: "Costco",
    purchased_at: "2026-08-17T18:00:00Z",
    total_cents: 8015,
    currency: "USD",
    parse_status: "needs_review",
    parse_quality: "complete",
    quality_message: null,
    parse_confidence: 0.99,
    failure_code: null,
    transaction_id: null,
    created_at: "2026-08-17T18:01:00Z",
    updated_at: "2026-08-17T18:01:00Z",
    decision_summary: { tracked: 0, ignored: 2, undecided: 4, total: 6 },
    items: [
      { ...baseLine, id: 901, raw_name: "KS EGGS 24CT", match_status: "unmatched", classification: "perishable_grocery", classification_confidence: 0.98, canonical_name: "Eggs" },
      { ...baseLine, id: 902, raw_name: "ORG 2% MLK GAL", match_status: "unmatched", classification: "perishable_grocery", classification_confidence: 0.98, canonical_name: "Milk" },
      { ...baseLine, id: 903, raw_name: "TIDE PODS 42CT", match_status: "unmatched", classification: "replenishable_household", classification_confidence: 0.98, canonical_name: "Laundry detergent" },
      { ...baseLine, id: 904, raw_name: "KS PAPER TOWELS 12RL", match_status: "unmatched", classification: "replenishable_household", classification_confidence: 0.98, canonical_name: "Paper towels" },
      { ...baseLine, id: 905, raw_name: "FOOD COURT COFFEE", match_status: "irrelevant", classification: "routine_consumption", classification_confidence: 0.99, canonical_name: null },
      { ...baseLine, id: 906, raw_name: "COTTON T-SHIRT", match_status: "irrelevant", classification: "one_time_purchase", classification_confidence: 0.99, canonical_name: null },
    ],
  };
  let active = true;
  let submitted: Record<string, unknown> | null = null;
  let lineWriteCalls = 0;
  await page.route("**/api/household/items", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/replenishment/receipts?**", (route) => {
    const bucket = new URL(route.request().url()).searchParams.get("bucket");
    return route.fulfill({
      json: bucket === "active" && active
        ? { items: [receipt], total: 1, limit: 25, offset: 0, has_more: false }
        : { items: [], total: 0, limit: 25, offset: 0, has_more: false },
    });
  });
  await page.route("**/api/replenishment/receipts/91/items/**", (route) => {
    lineWriteCalls += 1;
    return route.fulfill({ status: 500, json: { detail: "Line writes are forbidden" } });
  });
  await page.route("**/api/replenishment/receipts/91/decisions", async (route) => {
    submitted = route.request().postDataJSON() as Record<string, unknown>;
    active = false;
    return route.fulfill({
      json: {
        ...receipt,
        parse_status: "confirmed",
        updated_at: "2026-08-17T18:02:00Z",
        decision_summary: { tracked: 4, ignored: 2, undecided: 0, total: 6 },
      },
    });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Household" }).click();
  await page.getByRole("button", { name: /^Receipts/ }).click();
  await page.getByRole("button", { name: "Review receipt" }).click();
  await expect(page.getByText("Recommended: track as Eggs.")).toBeVisible();
  await expect(page.getByText("Recommended: track as Laundry detergent.")).toBeVisible();
  await expect(page.getByText("Not tracked · routine consumption")).toBeVisible();
  await expect(page.getByText("Not tracked · one-time purchase")).toBeVisible();
  await expect(page.getByText("It starts in Learning with no guessed cadence.").first()).toBeVisible();
  await expect(page.getByLabel(/Starting cadence|Cadence days/)).toHaveCount(0);
  await page.getByRole("button", { name: "Confirm receipt" }).click();
  await expect(page.getByText("Receipt confirmed: 4 tracked, 2 ignored, 0 left undecided.")).toBeVisible();
  expect(lineWriteCalls).toBe(0);
  expect(submitted).toEqual({
    expected_updated_at: "2026-08-17T18:01:00Z",
    decisions: [
      { line_id: 901, decision: "create", name: "Eggs", replenishment_mode: "either" },
      { line_id: 902, decision: "create", name: "Milk", replenishment_mode: "either" },
      { line_id: 903, decision: "create", name: "Laundry detergent", replenishment_mode: "either" },
      { line_id: 904, decision: "create", name: "Paper towels", replenishment_mode: "either" },
    ],
    finalize: "confirm",
    acknowledge_undecided: false,
  });
  expect(JSON.stringify(submitted)).not.toContain("cadence_days");
});

test("a stale household route cannot expose a start link", async ({ page }) => {
  await mockExpenseDashboard(page);
  await mockHouseholdOps(page);
  await page.route("**/api/household/errand-plans/latest", (route) => route.fulfill({ json: {
    id: 7,
    planned_for: null,
    base_location: "Home, 123 W Main Street",
    status: "planned",
    routing_provider: "google_routes",
    routing_is_optimized: true,
    route_url: null,
    is_stale: true,
    stale_reason: "A saved location changed. Recalculate before starting.",
    estimated_stop_minutes: 25,
    travel_duration_minutes: 31,
    distance_meters: 19312,
    baseline_travel_duration_minutes: 18,
    incremental_travel_duration_minutes: 13,
    available_minutes: 75,
    planning_mode: "while_out",
    primary_destination: null,
    final_destination: "Home, 123 W Main Street",
    stop_count: 1,
    stops: [{ id: 21, stop_order: 1, place_name: "CVS Pharmacy", place_address: "500 N Central Ave, Phoenix, AZ", errands: [], household_items: [] }],
    created_at: "2026-08-12T12:00:00Z",
    updated_at: "2026-08-12T12:00:00Z",
  } }));

  await page.goto("/");
  await page.getByRole("button", { name: "Household" }).click();
  await expect(page.getByText("Route needs recalculation before you leave.")).toBeVisible();
  await page.getByRole("button", { name: "Errands", exact: true }).click();
  await expect(page.getByText("Recalculate this route")).toBeVisible();
  await expect(page.getByRole("link", { name: "Start route" })).toHaveCount(0);
});

test("account identity exposes settings without occupying primary navigation", async ({ page }) => {
  await mockExpenseDashboard(page);
  await mockSettings(page);
  await page.goto("/");

  await page.getByRole("button", { name: "Open account menu for Gunjan Patil" }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Account", exact: true })).toBeVisible();
  await expect(page.getByText("Finish your setup")).toHaveCount(0);
  await expect(page).toHaveScreenshot("settings-account.png", { fullPage: true, timeout: 15_000 });
});

test("settings make workspace invitations and Splitwise group tools discoverable", async ({ page }) => {
  await mockExpenseDashboard(page);
  await mockSettings(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Open account menu for Gunjan Patil" }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();

  await chooseSettingsSection(page, "workspace", /Workspace & members/);
  await expect(page.getByRole("heading", { name: "Workspace and members" })).toBeVisible();
  await expect(page.getByPlaceholder("friend@example.com")).toBeVisible();

  await chooseSettingsSection(page, "splitwise", /Splitwise groups/);
  await expect(page.getByRole("heading", { name: "Manage groups and participants" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Create a new group" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Add an existing friend" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Invite by email" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Invite by link" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Current participants" })).toBeVisible();

  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const blocking = results.violations.filter(
    (violation) => violation.impact === "critical" || violation.impact === "serious",
  );
  expect(blocking).toEqual([]);
  const dimensions = await page.evaluate(() => ({ clientWidth: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
});

test("privacy settings expose consent, retention, legal, support, and guarded deletion", async ({ page }) => {
  await mockExpenseDashboard(page);
  await mockSettings(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Open account menu for Gunjan Patil" }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();
  await chooseSettingsSection(page, "privacy", /Privacy & account/);

  await expect(page.getByRole("heading", { name: "Privacy and account actions" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Privacy Policy" })).toHaveAttribute("href", "/legal/privacy");
  await expect(page.getByRole("link", { name: "Terms of Service" })).toHaveAttribute("href", "/legal/terms");
  await expect(page.getByRole("link", { name: "Contact support" })).toHaveAttribute("href", "mailto:support@example.com");
  await expect(page.getByText("2555 days")).toBeVisible();
  const deleteButton = page.getByRole("button", { name: "Delete my account" });
  await expect(deleteButton).toBeDisabled();
  await page.getByLabel(/Type DELETE gunjan@example.com/).fill("DELETE gunjan@example.com");
  await expect(deleteButton).toBeEnabled();
});

test("settings label personal and workspace connections and hide owner actions from members", async ({ page }) => {
  await mockExpenseDashboard(page);
  await mockSettings(page, "member");
  await page.goto("/");
  await page.getByRole("button", { name: "Open account menu for Gunjan Patil" }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();

  await chooseSettingsSection(page, "personal", /Personal connections/);
  await expect(page.getByText("Personal", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/Telegram user 123456/i)).toBeVisible();
  await expect(page.getByText(/verified payer/i)).toBeVisible();
  await expect(page.getByText("Chase", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /Disconnect/ })).toHaveCount(3);

  await chooseSettingsSection(page, "workspace-connections", /Workspace connections/);
  await expect(page.getByText("Workspace", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/household@gmail.com/i)).toBeVisible();
  await expect(page.getByRole("button", { name: /Disconnect/ })).toHaveCount(0);
  await expect(page.getByText("Owner managed").first()).toBeVisible();
});

test("deals prioritize value and disclose destination trust", async ({ page }) => {
  await mockExpenseDashboard(page);
  await mockDeals(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Deals" }).click();

  await expect(page.getByRole("heading", { name: "Deals worth your attention" })).toBeVisible();
  await expect(page.getByText("$25 off", { exact: true })).toBeVisible();
  await expect(page.getByText("Opens target.com")).toBeVisible();
  await expect(page.getByRole("link", { name: /Open deal/ })).toBeVisible();
  await expect(page.getByText("Unverified · offers.example.net")).toBeVisible();
  await expect(page.getByRole("button", { name: /Review link/ })).toBeVisible();
  await expect(page).toHaveScreenshot("deals-populated.png", { fullPage: true, timeout: 15_000 });

  await page.getByRole("button", { name: /Review link/ }).click();
  const destinationDialog = page.getByRole("dialog", { name: "Review this destination" });
  await expect(destinationDialog).toContainText("offers.example.net");
  await expect(destinationDialog.locator(":focus")).toHaveCount(1);
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("button", { name: /Review link/ })).toBeFocused();

  await page.getByRole("button", { name: "More actions for Target" }).click();
  await expect(page.getByRole("menuitem", { name: "Dismiss" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Not relevant" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Mute Target" })).toBeVisible();
  await page.keyboard.press("Escape");

  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const blocking = results.violations.filter(
    (violation) => violation.impact === "critical" || violation.impact === "serious",
  );
  expect(blocking).toEqual([]);
});

test("deals combine disconnected Gmail and an empty feed into one next step", async ({ page }) => {
  await mockExpenseDashboard(page);
  await mockDeals(page, { connected: false, empty: true });
  await page.goto("/");
  await page.getByRole("button", { name: "Deals" }).click();

  await expect(page.getByRole("heading", { name: "Connect Gmail to find your deals" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Connect Gmail" })).toBeVisible();
  await expect(page.getByText("No deals in this view")).toHaveCount(0);
});

test("deal expiry styling follows actual urgency", async ({ page }) => {
  const hour = 3_600_000;
  const base = {
    category: "Shopping",
    description: null,
    offer_type: "percent_off",
    percent_off: 10,
    amount_off: null,
    minimum_spend: null,
    promo_code: null,
    expiry_precision: "exact",
    destination_url: null,
    terms_summary: null,
    trust_status: "review",
    status: "active",
    score: 80,
    saved: false,
    why: [],
    source_count: 1,
  };
  await mockExpenseDashboard(page);
  await mockDeals(page, { offers: [
    { ...base, id: 81, merchant: "Past Offer", headline: "Expired offer", expires_at: new Date(Date.now() - hour).toISOString() },
    { ...base, id: 82, merchant: "Tomorrow Offer", headline: "Tomorrow offer", expires_at: new Date(Date.now() + 12 * hour).toISOString() },
    { ...base, id: 83, merchant: "Soon Offer", headline: "Soon offer", expires_at: new Date(Date.now() + 4 * 24 * hour).toISOString() },
    { ...base, id: 84, merchant: "Later Offer", headline: "Later offer", expires_at: new Date(Date.now() + 14 * 24 * hour).toISOString() },
    { ...base, id: 85, merchant: "Open Offer", headline: "No-expiry offer", expires_at: null },
  ] });
  await page.goto("/");
  await page.getByRole("button", { name: "Deals" }).click();

  await expect(page.getByText(/^Expired /)).toBeVisible();
  await expect(page.getByText("Expires tomorrow")).toBeVisible();
  await expect(page.getByText("Expires in 4 days")).toBeVisible();
  await expect(page.getByText("No expiry provided")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Later offer" })).toBeVisible();
});

test("deals remain usable without mobile document overflow", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await mockExpenseDashboard(page);
  await mockDeals(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Deals" }).click();
  await expect(page.getByText("$25 off", { exact: true })).toBeVisible();

  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
  await expect(page).toHaveScreenshot("deals-320.png", { fullPage: true, timeout: 15_000 });
});

test("primary mobile destinations keep touch targets at least 44px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockExpenseDashboard(page);
  await mockSpendingInsights(page);
  await mockHouseholdOps(page);
  await mockDeals(page);
  await mockSettings(page);
  await page.goto("/");

  await expectMobileTouchTargets(page);
  await page.getByRole("button", { name: /insights/i, exact: true }).click();
  await expect(page.getByRole("heading", { name: "Spending Insights" })).toBeVisible();
  await expectMobileTouchTargets(page);
  await page.getByRole("button", { name: /^Filters/ }).click();
  await page.getByRole("combobox", { name: "Account", exact: true }).selectOption("Chase checking");
  await page.getByRole("button", { name: "View insights" }).click();
  await expect(page.getByRole("button", { name: "Remove Account: Chase checking" })).toBeVisible();
  await expectMobileTouchTargets(page);

  await page.getByRole("button", { name: "Household" }).click();
  await expect(page.getByRole("heading", { name: "Household operations" })).toBeVisible();
  await expectMobileTouchTargets(page);
  for (const section of ["Errands", "Receipts", "Staples", "History"]) {
    await page.getByRole("button", { name: section, exact: true }).click();
    await expectMobileTouchTargets(page);
  }

  await page.getByRole("button", { name: "Deals" }).click();
  await expect(page.getByRole("heading", { name: "Deals worth your attention" })).toBeVisible();
  await expectMobileTouchTargets(page);
  await page.getByText("Terms and conditions").first().click();
  await page.getByRole("button", { name: /Review link/ }).click();
  await expectMobileTouchTargets(page);
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "Open account menu for Gunjan Patil" }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  await expectMobileTouchTargets(page);
  await page.getByLabel("Settings section").selectOption("splitwise");
  await expect(page.getByRole("heading", { name: "Manage groups and participants" })).toBeVisible();
  await expectMobileTouchTargets(page);
});

test("keyboard users can skip repeated navigation and overlays restore focus", async ({ page }) => {
  await mockExpenseDashboard(page);
  await mockDeals(page);
  await page.goto("/");

  const skipLink = page.getByRole("link", { name: "Skip to main content" });
  await skipLink.focus();
  await expect(skipLink).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();

  await page.getByRole("button", { name: "Deals" }).click();
  const reviewLink = page.getByRole("button", { name: /Review link/ });
  await reviewLink.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "Review this destination" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(reviewLink).toBeFocused();
});

test("reduced-motion preference suppresses application animation", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockExpenseDashboard(page);
  await page.goto("/");
  const timings = await page.evaluate(() => {
    const probe = document.createElement("div");
    probe.className = "ui-skeleton";
    document.body.appendChild(probe);
    const style = window.getComputedStyle(probe);
    const result = { animationDuration: style.animationDuration, transitionDuration: style.transitionDuration };
    probe.remove();
    return result;
  });
  expect(Number.parseFloat(timings.animationDuration)).toBeLessThanOrEqual(0.001);
  expect(Number.parseFloat(timings.transitionDuration)).toBeLessThanOrEqual(0.001);
});

test("primary workflows remain usable at a 200 percent zoom equivalent", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 900 });
  await mockExpenseDashboard(page);
  await mockHouseholdOps(page);
  await mockDeals(page);
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Expense Review" })).toBeVisible();
  await page.getByRole("button", { name: "Household" }).click();
  await expect(page.getByRole("heading", { name: "Household operations" })).toBeVisible();
  await page.getByRole("button", { name: "Deals" }).click();
  await expect(page.getByRole("heading", { name: "Deals worth your attention" })).toBeVisible();
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
});

test("mobile navigation leaves the end of page content unobscured", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockExpenseDashboard(page);
  await mockDeals(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Deals" }).click();
  await expect(page.getByText("$25 off", { exact: true })).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await page.waitForFunction(() => window.scrollY > 0);

  const spacing = await page.evaluate(() => {
    const main = document.querySelector("#main-content")!.getBoundingClientRect();
    const navigation = document.querySelector<HTMLElement>("nav[aria-label='Primary mobile navigation']")!.getBoundingClientRect();
    return { mainBottom: Math.round(main.bottom), navigationTop: Math.round(navigation.top) };
  });
  expect(spacing.mainBottom).toBeLessThanOrEqual(spacing.navigationTop);
});

test("expense views provide their own page identity", async ({ page }) => {
  await mockExpenseDashboard(page);
  await page.goto("/");

  await page.getByRole("button", { name: /insights/i, exact: true }).click();
  await expect(page.getByRole("heading", { name: "Spending Insights" })).toBeVisible();

  await page.getByRole("button", { name: /activity/i, exact: true }).click();
  await expect(page.getByRole("heading", { name: "Expense Activity" })).toBeVisible();
  await expect(page.getByText("Posted to Splitwise")).toBeVisible();
  await expect(page.locator("#main-content").getByText("Gunjan Patil", { exact: true })).toBeVisible();
  await expect(page.getByText("Attempt 1")).toBeVisible();
});

test("insights tell a scoped, accessible spending story", async ({ page }) => {
  await mockExpenseDashboard(page);
  await mockSpendingInsights(page);
  await page.goto("/");
  await page.getByRole("button", { name: /insights/i, exact: true }).click();

  await expect(page.getByRole("heading", { name: "Spending Insights" })).toBeVisible();
  await expect(page.getByText("Jul 14–Aug 12, 2026", { exact: true })).toBeVisible();
  await expect(page.getByText("Compared with Jun 14–Jul 13, 2026 · USD only · no currency conversion")).toBeVisible();
  await expect(page.getByLabel("Spending overview").getByText("$1,285", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "What changed" })).toBeVisible();
  await page.locator("[data-chart-point]").first().hover();
  await expect(page.getByRole("tooltip")).toContainText("transactions");
  await page.locator("[data-chart-point]").first().focus();
  await expect(page.getByRole("tooltip")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toBeHidden();
  await expect(page).toHaveScreenshot("spending-insights.png", { fullPage: true, timeout: 15_000 });

  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const blocking = results.violations.filter(
    (violation) => violation.impact === "critical" || violation.impact === "serious",
  );
  expect(blocking).toEqual([]);
});

test("insights remain usable without horizontal document overflow", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await mockExpenseDashboard(page);
  await mockSpendingInsights(page);
  await page.goto("/");
  await page.getByRole("button", { name: /insights/i, exact: true }).click();
  await expect(page.getByLabel("Spending overview").getByText("$1,285", { exact: true })).toBeVisible();

  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);

  const chart = page.getByRole("region", { name: "Scrollable spend over time chart" });
  await expect(chart).toBeVisible();
  const chartDimensions = await chart.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }));
  expect(chartDimensions.scrollWidth).toBeGreaterThan(chartDimensions.clientWidth);
  await expect(page.getByText("Scroll horizontally to explore each date without shrinking chart labels.")).toBeVisible();
});

test("review card prioritizes Personal and Split with progressive split steps", async ({ page }) => {
  await mockExpenseDashboard(page, [{
    id: 42,
    plaid_transaction_id: "plaid-42",
    merchant_name: "Aldi",
    name: "ALDI 12",
    amount_cents: 4250,
    amount: "42.50",
    iso_currency_code: "USD",
    institution_name: "Chase",
    category: "Groceries",
    payment_channel: "in store",
    date: "2026-08-12",
    authorized_date: "2026-08-12",
    pending: false,
    status: "ask_user",
    agent_question: "This looks like shared household spending. How should it be handled?",
    splitwise_expense_id: null,
    splitwise_payload_json: null,
    last_error: null,
    classification_suggestion: "likely_shared",
    classification_reason: "Household grocery merchant",
    can_undo_transaction: false,
    created_at: "2026-08-12T12:00:00Z",
    updated_at: "2026-08-12T12:00:00Z",
  }]);
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Aldi" })).toBeVisible();
  await expect(page.getByText("Chase")).toBeVisible();
  await expect(page.getByText("Groceries")).toBeVisible();
  await expect(page.getByRole("button", { name: "Personal" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Split", exact: true })).toBeVisible();
  await page.getByTestId("transaction-card-42").evaluate((element) => {
    window.scrollTo({ top: element.getBoundingClientRect().top + window.scrollY - 96 });
  });
  await expect(page.getByTestId("transaction-card-42")).toHaveScreenshot("transaction-review-card.png");

  await page.getByRole("button", { name: "More actions for Aldi" }).click();
  await expect(page.getByRole("menuitem", { name: "Save as draft" })).toBeVisible();
  await page.keyboard.press("Escape");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Split", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Choose people" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Choose split" })).toBeVisible();
  await expect(page.getByText("Step 3 · Review and post")).toBeVisible();
  await expectMobileTouchTargets(page);
  const dimensions = await page.evaluate(() => ({ clientWidth: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
});

test("long merchant names, large spend, and refunds remain readable on phones", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 844 });
  const transaction = {
    id: 91,
    plaid_transaction_id: "plaid-91",
    merchant_name: "A Very Long Neighborhood Cooperative Grocery and Household Market",
    name: "LONG MERCHANT",
    amount_cents: 12_345_678,
    amount: "123456.78",
    iso_currency_code: "USD",
    institution_name: "Chase Freedom Unlimited ending in 1234",
    category: "Groceries and household essentials",
    payment_channel: "in store",
    date: "2026-08-12",
    authorized_date: "2026-08-12",
    pending: false,
    status: "ask_user",
    agent_question: "Review this transaction.",
    splitwise_expense_id: null,
    splitwise_payload_json: null,
    last_error: null,
    classification_suggestion: "unsure",
    classification_reason: null,
    can_undo_transaction: false,
    created_at: "2026-08-12T12:00:00Z",
    updated_at: "2026-08-12T12:00:00Z",
  };
  await mockExpenseDashboard(page, [transaction, {
    ...transaction,
    id: 92,
    plaid_transaction_id: "plaid-92",
    merchant_name: "Refund from Neighborhood Cooperative Market",
    amount_cents: -25_050,
    amount: "-250.50",
  }]);
  await page.goto("/");

  await expect(page.getByRole("heading", { name: transaction.merchant_name })).toBeVisible();
  await expect(page.getByText("$123,456.78", { exact: true })).toBeVisible();
  await expect(page.getByText("-$250.50", { exact: true })).toBeVisible();
  const dimensions = await page.evaluate(() => ({ clientWidth: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
});

test("a failed transaction action stays visible without blocking another row", async ({ page }) => {
  const baseTransaction = {
    id: 101,
    plaid_transaction_id: "plaid-101",
    merchant_name: "Aldi",
    name: "ALDI 12",
    amount_cents: 4250,
    amount: "42.50",
    iso_currency_code: "USD",
    institution_name: "Chase",
    category: "Groceries",
    payment_channel: "in store",
    date: "2026-08-12",
    authorized_date: "2026-08-12",
    pending: false,
    status: "ask_user",
    agent_question: "How should this be handled?",
    splitwise_expense_id: null,
    splitwise_payload_json: null,
    last_error: null,
    classification_suggestion: "unsure",
    classification_reason: null,
    can_undo_transaction: false,
    created_at: "2026-08-12T12:00:00Z",
    updated_at: "2026-08-12T12:00:00Z",
  };
  await mockExpenseDashboard(page, [baseTransaction, {
    ...baseTransaction,
    id: 102,
    plaid_transaction_id: "plaid-102",
    merchant_name: "Target",
    name: "TARGET 42",
    amount_cents: 2800,
    amount: "28.00",
  }]);
  let releaseFailure: (() => void) | undefined;
  await page.route("**/transactions/101/personal", async (route) => {
    await new Promise<void>((resolve) => { releaseFailure = resolve; });
    await route.fulfill({
      status: 503,
      headers: { "Content-Type": "application/json", "X-Request-ID": "review-action-503" },
      body: JSON.stringify({ detail: "Splitwise is temporarily unavailable." }),
    });
  });
  await page.goto("/");

  await page.getByTestId("transaction-card-101").getByRole("button", { name: "Personal" }).click();
  await expect(page.getByTestId("transaction-card-101").getByText("Saving as personal…")).toBeVisible();
  await expect(page.getByTestId("transaction-card-102").getByRole("button", { name: "Personal" })).toBeEnabled();

  releaseFailure?.();
  await expect(page.getByTestId("transaction-card-101").getByRole("alert")).toContainText("Splitwise is temporarily unavailable.");
  await expect(page.getByTestId("transaction-card-101").getByRole("alert")).toContainText("review-action-503");
  await expect(page.getByTestId("transaction-card-101").getByRole("button", { name: "Personal" })).toBeEnabled();
});

test("ambiguous Splitwise actions remain visible in Recovery and can reconcile", async ({ page }) => {
  const transaction = {
    id: 201,
    plaid_transaction_id: "plaid-201",
    merchant_name: "Aldi",
    name: "ALDI 12",
    amount_cents: 4250,
    amount: "42.50",
    iso_currency_code: "USD",
    institution_name: "Chase",
    category: "Groceries",
    payment_channel: "in store",
    date: "2026-08-12",
    authorized_date: "2026-08-12",
    pending: false,
    status: "post_ambiguous",
    agent_question: null,
    splitwise_expense_id: null,
    splitwise_payload_json: "{}",
    last_error: "Splitwise timed out after the request was sent.",
    classification_suggestion: "likely_shared",
    classification_reason: null,
    can_undo_transaction: false,
    created_at: "2026-08-12T12:00:00Z",
    updated_at: "2026-08-12T12:01:00Z",
  };
  await mockExpenseDashboard(page);
  await page.route("**/transactions?status=post_ambiguous&limit=50", (route) =>
    route.fulfill({ json: [transaction] }),
  );
  await page.route("**/transactions/201/recovery/retry", (route) =>
    route.fulfill({ json: { transaction: { ...transaction, status: "posted", splitwise_expense_id: "expense-201", last_error: null }, message: "Financial operation recovered." } }),
  );
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Financial actions needing attention" })).toBeVisible();
  await expect(page.getByText(/has not been hidden or marked successful/i)).toBeVisible();
  await page.getByRole("button", { name: "Reconcile and retry" }).click();
  await expect(page.getByText("Financial operation recovered.", { exact: true })).toBeVisible();
});

for (const width of [320, 375, 390, 768, 1024, 1440]) {
  test(`dashboard has no document overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width < 768 ? 844 : 900 });
    await mockExpenseDashboard(page);
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Expense Review" })).toBeVisible();

    const dimensions = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
  });
}

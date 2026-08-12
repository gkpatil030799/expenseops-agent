import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

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
    if (pathname === "/api/insights/activity") return route.fulfill({ json: [] });
    return route.fulfill({ status: 503, json: { detail: "Provider unavailable in visual test" } });
  });
  await page.route(/.*\/transactions.*/, (route) => route.fulfill({ json: transactions }));
  await page.route("**/splitwise/me", (route) =>
    route.fulfill({
      json: { id: 1, first_name: "Gunjan", last_name: "Patil", email: "gunjan@example.com" },
    }),
  );
  await page.route("**/ai/memory", (route) => route.fulfill({ json: [] }));
}

async function mockSpendingInsights(page: Page) {
  await page.route("**/api/insights/spending?**", (route) => route.fulfill({ json: {
    range: {
      start_date: "2026-07-14",
      end_date: "2026-08-12",
      previous_start_date: "2026-06-14",
      previous_end_date: "2026-07-13",
      granularity: "week",
    },
    summary: { total_cents: 128450, personal_cents: 74200, shared_cents: 43800, transaction_count: 31, average_cents: 4144 },
    comparison: { total_cents: 116200, personal_cents: 68100, shared_cents: 39400, transaction_count: 28, average_cents: 4150 },
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
    data_quality: { unknown_share_transactions: 0, pending_review_cents: 12500, uncategorized_cents: 0, pending_transactions_excluded: true },
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

test("visual foundation keeps the expense dashboard stable", async ({ page }) => {
  await mockExpenseDashboard(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Expense Review" })).toBeVisible();
  await expect(page).toHaveScreenshot("expense-review-empty.png", { fullPage: true, timeout: 15_000 });
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

test("account identity exposes settings without occupying primary navigation", async ({ page }) => {
  await mockExpenseDashboard(page);
  await page.goto("/");

  await page.getByRole("button", { name: "Open account menu for Gunjan Patil" }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Your ExpenseOps setup" })).toBeVisible();
});

test("expense views provide their own page identity", async ({ page }) => {
  await mockExpenseDashboard(page);
  await page.goto("/");

  await page.getByRole("button", { name: /insights/i, exact: true }).click();
  await expect(page.getByRole("heading", { name: "Spending Insights" })).toBeVisible();

  await page.getByRole("button", { name: /activity/i, exact: true }).click();
  await expect(page.getByRole("heading", { name: "Expense Activity" })).toBeVisible();
});

test("insights tell a scoped, accessible spending story", async ({ page }) => {
  await mockExpenseDashboard(page);
  await mockSpendingInsights(page);
  await page.goto("/");
  await page.getByRole("button", { name: /insights/i, exact: true }).click();

  await expect(page.getByRole("heading", { name: "Spending Insights" })).toBeVisible();
  await expect(page.getByText("Jul 14–Aug 12, 2026", { exact: true })).toBeVisible();
  await expect(page.getByText("Compared with Jun 14–Jul 13, 2026 · Displayed as USD; no currency conversion applied")).toBeVisible();
  await expect(page.getByLabel("Spending overview").getByText("$1,285")).toBeVisible();
  await expect(page.getByRole("heading", { name: "What changed" })).toBeVisible();
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
  await expect(page.getByLabel("Spending overview").getByText("$1,285")).toBeVisible();

  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
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

  await page.getByRole("button", { name: "Split", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Choose people" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Choose split" })).toBeVisible();
  await expect(page.getByText("Step 3 · Review and post")).toBeVisible();
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

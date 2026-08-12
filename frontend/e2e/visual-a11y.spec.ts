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

async function mockExpenseDashboard(page: Page) {
  await page.route("**/api/**", (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/api/context") return route.fulfill({ json: context });
    if (pathname === "/api/insights/activity") return route.fulfill({ json: [] });
    return route.fulfill({ status: 503, json: { detail: "Provider unavailable in visual test" } });
  });
  await page.route(/.*\/transactions.*/, (route) => route.fulfill({ json: [] }));
  await page.route("**/splitwise/me", (route) =>
    route.fulfill({
      json: { id: 1, first_name: "Gunjan", last_name: "Patil", email: "gunjan@example.com" },
    }),
  );
  await page.route("**/ai/memory", (route) => route.fulfill({ json: [] }));
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
  await expect(page.getByRole("heading", { name: "Household command center" })).toBeVisible();

  await page.getByRole("button", { name: "Deals" }).click();
  await expect(page.getByRole("heading", { name: "Deals worth your attention" })).toBeVisible();

  await page.getByRole("button", { name: "Expenses", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Expense Review" })).toBeVisible();
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

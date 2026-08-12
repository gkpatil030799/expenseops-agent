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
  await page.route("**/api/context", (route) => route.fulfill({ json: context }));
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
  await expect(page).toHaveScreenshot("expense-review-empty.png", { fullPage: true });
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

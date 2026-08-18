import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import type { AgentStructuredResponse } from "../src/agent/contracts";
import { canonicalMessages, mockAgentApp } from "./fixtures/agent";

const ACTIVITY_BLOCK: Extract<
  AgentStructuredResponse["blocks"][number],
  { type: "classification_activity_summary" }
> = {
  type: "classification_activity_summary",
  block_version: "1.0",
  title: "Classification activity",
  view: "summary",
  activity_date: "2026-08-17",
  timezone: "UTC",
  counts: {
    transactions: 1,
    receipt_items: 1,
    categories: 2,
    new_categories: 1,
    receipt_matches: 1,
    new_household_items: 1,
    cadence_updates: 1,
    uncertain: 1,
  },
  transactions: [{
    decision_public_id: "decision-transaction-1",
    public_id: "41",
    source_available: true,
    version: 1,
    merchant: "Synthetic Cafe",
    occurred_on: "2026-08-17",
    parent_category: "food_dining",
    subcategory: "Coffee shops",
    concept: "Coffee",
    activity_type: "coffee_beverage",
    replenishment_eligibility: "not_replenishable",
    confidence: 0.99,
    confidence_band: "high",
    authority: "deterministic_exact",
    decision_state: "final",
    provenance_codes: ["deterministic_taxonomy_rule"],
    auto_finalize_at: null,
    finalized_at: "2026-08-17T15:00:00Z",
    corrects_decision_public_id: null,
    created_subcategory: false,
    created_concept: false,
    created_household_item: false,
    applied_at: "2026-08-17T15:00:00Z",
  }],
  receipt_items: [{
    decision_public_id: "decision-receipt-1",
    public_id: "8",
    receipt_public_id: "7",
    source_available: true,
    version: 1,
    merchant: "Household Store",
    name: "Paper Towels",
    parent_category: "household_home",
    subcategory: "Paper goods",
    concept: "Paper towels",
    activity_type: "household_consumable",
    replenishment_eligibility: "replenishable",
    confidence: 0.94,
    confidence_band: "high",
    authority: "receipt_evidence",
    decision_state: "final",
    provenance_codes: ["receipt_line_evidence"],
    auto_finalize_at: null,
    finalized_at: "2026-08-17T15:05:00Z",
    corrects_decision_public_id: null,
    created_subcategory: false,
    created_concept: true,
    created_household_item: true,
    household_item_public_id: "3",
    household_item_name: "Paper towels",
    applied_at: "2026-08-17T15:05:00Z",
  }],
  categories: [
    { parent_category: "food_dining", transaction_count: 1, receipt_item_count: 0, total_count: 1 },
    { parent_category: "household_home", transaction_count: 0, receipt_item_count: 1, total_count: 1 },
  ],
  new_categories: [{
    decision_public_id: "decision-receipt-1",
    parent_category: "household_home",
    subcategory: "Paper goods",
    source_type: "receipt_line",
    authority: "receipt_evidence",
    created_at: "2026-08-17T15:05:00Z",
  }],
  receipt_matches: [{
    receipt_public_id: "7",
    merchant: "Household Store",
    status: "ambiguous",
    confidence: 0.72,
    transaction_public_id: null,
    reason_code: "multiple_possible_transactions",
    attempted_at: "2026-08-17T15:04:00Z",
    matched_at: null,
  }],
  new_household_items: [{
    created_by_decision_public_id: "decision-receipt-1",
    public_id: "3",
    name: "Paper towels",
    parent_category: "household_home",
    replenishment_eligibility: "replenishable",
    classification_confidence: 0.94,
    cadence_source: "category_prior",
    cadence_days: 30,
    cadence_min_days: 21,
    cadence_max_days: 45,
    cadence_confidence: 0.55,
    activity_at: "2026-08-17T15:05:00Z",
  }],
  cadence_updates: [{
    created_by_decision_public_id: null,
    public_id: "3",
    name: "Paper towels",
    parent_category: "household_home",
    replenishment_eligibility: "replenishable",
    classification_confidence: 0.94,
    cadence_source: "observed",
    cadence_days: 28,
    cadence_min_days: 24,
    cadence_max_days: 33,
    cadence_confidence: 0.81,
    activity_at: "2026-08-17T16:00:00Z",
  }],
  uncertain: [{
    kind: "receipt_match",
    public_id: "7",
    receipt_public_id: null,
    label: "Household Store receipt",
    reasons: ["ambiguous_receipt_match"],
    confidence_band: null,
    decision_state: null,
    observed_at: "2026-08-17T15:04:00Z",
  }],
  truncated_sections: [],
};

const ACTIVITY_RESPONSE: AgentStructuredResponse = {
  schema_version: "1.0",
  blocks: [
    { type: "text", text: "Here is ExpenseOps classification activity for today." },
    ACTIVITY_BLOCK,
  ],
};

const LATEST_RECEIPT_RESPONSE: AgentStructuredResponse = {
  schema_version: "1.0",
  blocks: [
    { type: "text", text: "ExpenseOps found one categorized receipt line." },
    {
      type: "receipt_summary",
      public_id: "7",
      merchant: "Household Store",
      purchased_at: "2026-08-17T15:00:00Z",
      ingested_at: "2026-08-17T15:04:00Z",
      total_cents: 2_499,
      currency_code: "USD",
      status: "confirmed",
      transaction_linked: true,
      matched_line_count: 1,
      ignored_line_count: 0,
      unmatched_line_count: 0,
      total_line_count: 1,
      items: [{
        name: "Paper Towels",
        quantity: 1,
        unit: "pack",
        line_total_cents: 2_499,
        match_status: "matched",
        household_item_name: "Paper towels",
        parent_category: "household_home",
        subcategory: "Paper goods",
        concept: "Paper towels",
        activity_type: "household_consumable",
        replenishment_eligibility: "replenishable",
        classification_confidence: 0.94,
        confirmed_acquisition: true,
      }],
      items_truncated: false,
    },
  ],
};

async function installHouseholdActivity(
  page: Page,
  onCorrection?: (pathname: string, payload: unknown) => void,
  correctionError?: string,
  onConceptMutation?: (method: string, pathname: string, payload: unknown) => void,
): Promise<void> {
  const emptyReceiptPage = { items: [], total: 0, limit: 25, offset: 0, has_more: false };
  let concepts = [
    {
      id: 1,
      name: "Cafe beverage",
      parent_category: "food_dining",
      subcategory_id: 11,
      subcategory_name: "Coffee shops",
      item_activity_type: "coffee_beverage",
      replenishment_eligibility: "not_replenishable",
      linked_household_item_count: 0,
      can_merge_as_source: true,
    },
    {
      id: 2,
      name: "Coffee beverage",
      parent_category: "food_dining",
      subcategory_id: 11,
      subcategory_name: "Coffee shops",
      item_activity_type: "coffee_beverage",
      replenishment_eligibility: "not_replenishable",
      linked_household_item_count: 0,
      can_merge_as_source: true,
    },
    {
      id: 3,
      name: "Paper towels",
      parent_category: "household_home",
      subcategory_id: 12,
      subcategory_name: "Paper goods",
      item_activity_type: "household_consumable",
      replenishment_eligibility: "replenishable",
      linked_household_item_count: 1,
      can_merge_as_source: false,
    },
  ];
  let subcategories = [
    { id: 11, name: "Coffee shops", parent_category: "food_dining", concept_count: 2 },
    { id: 13, name: "Cafe visits", parent_category: "food_dining", concept_count: 0 },
    { id: 12, name: "Paper goods", parent_category: "household_home", concept_count: 1 },
  ];
  let householdItems = [
    {
      id: 3,
      name: "Paper towels",
      quantity: "1",
      unit: "pack",
      preferred_place_name: null,
      preferred_place_address: null,
      replenishment_mode: "either",
      cadence_days: 28,
      cadence_source: "observed",
      last_acquired_at: "2026-08-17T15:05:00Z",
      snoozed_until: null,
      enabled: true,
      notes: null,
      due_score: 0.1,
      due_state: "not_due",
      should_surface: false,
      linked_errand_id: null,
      created_at: "2026-08-17T15:05:00Z",
      updated_at: "2026-08-17T16:00:00Z",
    },
    {
      id: 4,
      name: "Paper towel",
      quantity: null,
      unit: null,
      preferred_place_name: null,
      preferred_place_address: null,
      replenishment_mode: "either",
      cadence_days: 30,
      cadence_source: "observed",
      last_acquired_at: "2026-07-18T15:05:00Z",
      snoozed_until: null,
      enabled: true,
      notes: null,
      due_score: 0.1,
      due_state: "not_due",
      should_surface: false,
      linked_errand_id: null,
      created_at: "2026-07-18T15:05:00Z",
      updated_at: "2026-08-17T16:00:00Z",
    },
  ];
  await page.route("**/api/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/classification/concepts" && request.method() === "GET") {
      return route.fulfill({ json: { concepts, has_more: false } });
    }
    if (url.pathname === "/api/classification/subcategories" && request.method() === "GET") {
      return route.fulfill({ json: { subcategories, has_more: false } });
    }
    if (url.pathname === "/api/household/items" && request.method() === "GET") {
      return route.fulfill({ json: householdItems });
    }
    const subcategoryRename = url.pathname.match(/^\/api\/classification\/subcategories\/(\d+)$/);
    if (subcategoryRename && request.method() === "PATCH") {
      const sourceId = Number(subcategoryRename[1]);
      const body = request.postDataJSON() as { name: string };
      onConceptMutation?.(request.method(), url.pathname, body);
      subcategories = subcategories.map((value) => value.id === sourceId ? { ...value, name: body.name } : value);
      return route.fulfill({ json: {
        applied: true,
        source_subcategory_id: sourceId,
        target_subcategory_id: sourceId,
        target_name: body.name,
        concepts_updated: 2,
        receipt_items_updated: 1,
        transactions_updated: 1,
      } });
    }
    const subcategoryMerge = url.pathname.match(/^\/api\/classification\/subcategories\/(\d+)\/merge$/);
    if (subcategoryMerge && request.method() === "POST") {
      const sourceId = Number(subcategoryMerge[1]);
      const body = request.postDataJSON() as { target_subcategory_id: number };
      const target = subcategories.find((value) => value.id === body.target_subcategory_id)!;
      onConceptMutation?.(request.method(), url.pathname, body);
      subcategories = subcategories.filter((value) => value.id !== sourceId);
      return route.fulfill({ json: {
        applied: true,
        source_subcategory_id: sourceId,
        target_subcategory_id: target.id,
        target_name: target.name,
        concepts_updated: 2,
        receipt_items_updated: 1,
        transactions_updated: 1,
      } });
    }
    const householdMerge = url.pathname.match(/^\/api\/classification\/household-items\/(\d+)\/merge$/);
    if (householdMerge && request.method() === "POST") {
      const sourceId = Number(householdMerge[1]);
      const body = request.postDataJSON() as { target_household_item_id: number };
      const target = householdItems.find((value) => value.id === body.target_household_item_id)!;
      onConceptMutation?.(request.method(), url.pathname, body);
      householdItems = householdItems.filter((value) => value.id !== sourceId);
      return route.fulfill({ json: {
        applied: true,
        source_household_item_id: sourceId,
        target_household_item_id: target.id,
        target_name: target.name,
        merge_event_id: 91,
        aliases_moved: 1,
        receipt_items_updated: 1,
        acquisitions_moved: 2,
        errand_links_updated: 1,
        plan_links_updated: 0,
        predictions_invalidated: 1,
        reverted: false,
      } });
    }
    const householdUndo = url.pathname.match(/^\/api\/classification\/household-items\/(\d+)\/merge\/undo$/);
    if (householdUndo && request.method() === "POST") {
      const sourceId = Number(householdUndo[1]);
      const body = request.postDataJSON() as { merge_event_id: number };
      onConceptMutation?.(request.method(), url.pathname, body);
      householdItems = [...householdItems, { ...householdItems[0], id: sourceId, name: "Paper towel" }];
      return route.fulfill({ json: {
        applied: true,
        source_household_item_id: sourceId,
        target_household_item_id: 3,
        target_name: "Paper towels",
        merge_event_id: body.merge_event_id,
        aliases_moved: 1,
        receipt_items_updated: 1,
        acquisitions_moved: 2,
        errand_links_updated: 1,
        plan_links_updated: 0,
        predictions_invalidated: 0,
        reverted: true,
      } });
    }
    const conceptRename = url.pathname.match(/^\/api\/classification\/concepts\/(\d+)$/);
    if (conceptRename && request.method() === "PATCH") {
      const conceptId = Number(conceptRename[1]);
      const body = request.postDataJSON() as { name: string };
      onConceptMutation?.(request.method(), url.pathname, body);
      concepts = concepts.map((concept) => concept.id === conceptId ? { ...concept, name: body.name } : concept);
      return route.fulfill({ json: {
        applied: true,
        source_concept_id: conceptId,
        target_concept_id: conceptId,
        target_name: body.name,
        aliases_moved: 1,
        receipt_items_updated: 1,
        transactions_updated: 1,
        household_items_merged: false,
      } });
    }
    const conceptMerge = url.pathname.match(/^\/api\/classification\/concepts\/(\d+)\/merge$/);
    if (conceptMerge && request.method() === "POST") {
      const sourceId = Number(conceptMerge[1]);
      const body = request.postDataJSON() as { target_concept_id: number };
      const target = concepts.find((concept) => concept.id === body.target_concept_id)!;
      onConceptMutation?.(request.method(), url.pathname, body);
      concepts = concepts.filter((concept) => concept.id !== sourceId);
      return route.fulfill({ json: {
        applied: true,
        source_concept_id: sourceId,
        target_concept_id: target.id,
        target_name: target.name,
        aliases_moved: 1,
        receipt_items_updated: 1,
        transactions_updated: 1,
        household_items_merged: false,
      } });
    }
    if (
      request.method() === "PATCH"
      && /^\/api\/classification\/(receipt-lines|transactions)\/\d+$/.test(url.pathname)
    ) {
      onCorrection?.(url.pathname, request.postDataJSON());
      if (correctionError) {
        return route.fulfill({ status: 400, json: { detail: correctionError } });
      }
      return route.fulfill({ json: { applied: true, reason: "applied", version: 2 } });
    }
    const responses: Record<string, unknown> = {
      "/api/household/errands": [],
      "/api/household/errand-plans/latest": null,
      "/api/household/locations": [],
      "/api/replenishment/summary": {
        this_week: [],
        learning: { confirmed_acquisitions: 1, items_with_history: 1, active_model: null },
        recent_receipts: [],
        accuracy: { evaluated_predictions: 0, confidence_level: "insufficient" },
      },
      "/api/replenishment/gmail/status": {
        configured: true,
        last_successful_sync_at: "2026-08-17T15:00:00Z",
        latest_receipt_at: "2026-08-17T15:04:00Z",
      },
      "/api/replenishment/classification-activity": {
        schema_version: "1.0",
        view: ACTIVITY_BLOCK.view,
        activity_date: url.searchParams.get("activity_date") || ACTIVITY_BLOCK.activity_date,
        timezone: ACTIVITY_BLOCK.timezone,
        as_of: "2026-08-17T17:00:00Z",
        counts: ACTIVITY_BLOCK.counts,
        transactions: ACTIVITY_BLOCK.transactions,
        receipt_items: ACTIVITY_BLOCK.receipt_items,
        categories: ACTIVITY_BLOCK.categories,
        new_categories: ACTIVITY_BLOCK.new_categories,
        receipt_matches: ACTIVITY_BLOCK.receipt_matches,
        new_household_items: ACTIVITY_BLOCK.new_household_items,
        cadence_updates: ACTIVITY_BLOCK.cadence_updates,
        uncertain: ACTIVITY_BLOCK.uncertain,
        truncated_sections: ACTIVITY_BLOCK.truncated_sections,
      },
    };
    if (url.pathname === "/api/replenishment/receipts") {
      return route.fulfill({ json: emptyReceiptPage });
    }
    if (url.pathname in responses) return route.fulfill({ json: responses[url.pathname] });
    return route.fallback();
  });
}

test("Agent renders a bounded retrospective without presenting uncertainty as blocking", async ({ page }) => {
  await mockAgentApp(page, {
    initialConversation: true,
    messages: canonicalMessages(ACTIVITY_RESPONSE, "What did ExpenseOps categorize today?"),
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Agent", exact: true }).click();

  const card = page.getByTestId("agent-classification-activity");
  await expect(card).toBeVisible();
  await expect(card.getByText("Synthetic Cafe")).toBeVisible();
  await expect(card.getByText("Paper Towels", { exact: true })).toBeVisible();
  await expect(card.getByRole("heading", { name: "New categories created" })).toBeVisible();
  await expect(card.getByText("Paper goods", { exact: true })).toBeVisible();
  await expect(card.getByRole("heading", { name: "Optional review" })).toBeVisible();
  await expect(card).toContainText("left this correctable instead of guessing");
  await expect(card).not.toContainText("candidate_ids");
  await expect(card).not.toContainText("account_id");

  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(results.violations.filter((violation) => ["critical", "serious"].includes(violation.impact || ""))).toEqual([]);
});

test("Agent latest receipt exposes the bounded canonical categorization", async ({ page }) => {
  await mockAgentApp(page, {
    initialConversation: true,
    messages: canonicalMessages(
      LATEST_RECEIPT_RESPONSE,
      "Show me all items categorized from my latest receipt",
    ),
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Agent", exact: true }).click();

  await expect(page.getByText("Category: Household & Home / Paper goods")).toBeVisible();
  await expect(page.getByText(/Concept: Paper towels/)).toBeVisible();
  await expect(page.getByText(/Activity: Household Consumable/)).toBeVisible();
  await expect(page.getByText(/Replenishment: Replenishable/)).toBeVisible();
  await expect(page.getByText("Review receipt", { exact: true })).toHaveCount(0);
});

test("Household Today shows classification history and keeps review and Add staple optional", async ({ page }, testInfo) => {
  await mockAgentApp(page);
  await installHouseholdActivity(page);
  if (testInfo.project.name === "mobile-chromium") {
    await page.setViewportSize({ width: 320, height: 760 });
  }
  await page.goto("/");
  await page.getByRole("button", { name: "Household", exact: true }).click();

  const overview = page.getByTestId("classification-activity-overview");
  await expect(overview.getByRole("heading", { name: "Categorized today" })).toBeVisible();
  await expect(overview).toContainText("Food & Dining · 1");
  await expect(overview).toContainText("Paper goods · Household & Home");
  await expect(overview).toContainText("1 optional review");
  await expect(overview).toContainText("Automatic processing is not blocked");

  if (testInfo.project.name === "mobile-chromium") {
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  }

  await page.getByRole("button", { name: "Receipts", exact: true }).click();
  await expect(page.getByText("Optional receipt review", { exact: true })).toBeVisible();
  await expect(page.getByText("No optional receipt reviews")).toBeVisible();
  await page.getByRole("button", { name: "Staples", exact: true }).click();
  await expect(page.getByRole("button", { name: "Add staple" })).toBeVisible();
});

test("Household classification history supports bounded receipt and transaction corrections", async ({ page }, testInfo) => {
  await mockAgentApp(page);
  const corrections: Array<{ pathname: string; payload: unknown }> = [];
  await installHouseholdActivity(page, (pathname, payload) => corrections.push({ pathname, payload }));
  if (testInfo.project.name === "mobile-chromium") {
    await page.setViewportSize({ width: 320, height: 760 });
  }
  await page.goto("/");
  await page.getByRole("button", { name: "Household", exact: true }).click();

  const overview = page.getByTestId("classification-activity-overview");
  await overview.getByLabel("Activity date (UTC)").fill("2026-08-16");
  await expect(overview.getByRole("heading", { name: "Categorized on 2026-08-16" })).toBeVisible();
  await overview.getByRole("button", { name: "Correct Paper Towels" }).click();
  const editorA11y = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(editorA11y.violations.filter((violation) => ["critical", "serious"].includes(violation.impact || ""))).toEqual([]);
  await overview.getByRole("button", { name: "Clear Subcategory (optional)" }).click();
  await overview.getByRole("textbox", { name: "Canonical concept (optional)" }).fill("Kitchen paper");
  await overview.getByRole("button", { name: "Save correction" }).click();

  await expect.poll(() => corrections).toEqual([{
    pathname: "/api/classification/receipt-lines/8",
    payload: {
      spending_parent_category: "household_home",
      item_activity_type: "household_consumable",
      replenishment_eligibility: "replenishable",
      subcategory_name: null,
      canonical_concept: "Kitchen paper",
    },
  }]);
  await expect(overview.getByRole("status")).toContainText("Paper Towels classification corrected");

  await overview.getByRole("button", { name: "Correct Synthetic Cafe" }).click();
  await overview.getByLabel("Parent category").selectOption("other_uncertain");
  await overview.getByRole("button", { name: "Save correction" }).click();
  await expect.poll(() => corrections).toHaveLength(2);
  expect(corrections[1]).toEqual({
    pathname: "/api/classification/transactions/41",
    payload: {
      spending_parent_category: "other_uncertain",
      item_activity_type: "uncertain",
      replenishment_eligibility: "uncertain",
      subcategory_name: null,
      canonical_concept: null,
    },
  });

  if (testInfo.project.name === "mobile-chromium") {
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  }
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(results.violations.filter((violation) => ["critical", "serious"].includes(violation.impact || ""))).toEqual([]);
});

test("a failed classification correction keeps the draft available", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "one browser covers server validation feedback");
  await mockAgentApp(page);
  await installHouseholdActivity(page, undefined, "That category combination is not supported.");
  await page.goto("/");
  await page.getByRole("button", { name: "Household", exact: true }).click();

  const overview = page.getByTestId("classification-activity-overview");
  await overview.getByRole("button", { name: "Correct Paper Towels" }).click();
  const concept = overview.getByRole("textbox", { name: "Canonical concept (optional)" });
  await concept.fill("My corrected concept");
  await overview.getByRole("button", { name: "Save correction" }).click();

  await expect(overview.getByRole("alert")).toHaveText("That category combination is not supported.");
  await expect(concept).toHaveValue("My corrected concept");
  await expect(overview.getByRole("button", { name: "Save correction" })).toBeVisible();
});

test("workspace owner can manage taxonomy and reversibly merge duplicate items", async ({ page }, testInfo) => {
  await mockAgentApp(page);
  const mutations: Array<{ method: string; pathname: string; payload: unknown }> = [];
  await installHouseholdActivity(
    page,
    undefined,
    undefined,
    (method, pathname, payload) => mutations.push({ method, pathname, payload }),
  );
  if (testInfo.project.name === "mobile-chromium") {
    await page.setViewportSize({ width: 320, height: 760 });
  }
  await page.goto("/");
  await page.getByRole("button", { name: "Household", exact: true }).click();

  const overview = page.getByTestId("classification-activity-overview");
  await overview.getByRole("button", { name: "Manage taxonomy and duplicates" }).click();
  const manager = overview.getByTestId("classification-concept-manager");
  await expect(manager).toContainText("Household merges repair bounded history and can be undone");

  await manager.getByLabel("Concept to change").selectOption("1");
  await manager.getByLabel("New concept name").fill("Cafe drink");
  await manager.getByRole("button", { name: "Rename concept" }).click();
  await expect.poll(() => mutations).toEqual([{
    method: "PATCH",
    pathname: "/api/classification/concepts/1",
    payload: { name: "Cafe drink" },
  }]);
  await expect(manager).toContainText("Renamed “Cafe beverage” to “Cafe drink”.");

  await manager.getByLabel("Keep this target concept").selectOption("2");
  page.once("dialog", (dialog) => dialog.accept());
  await manager.getByRole("button", { name: "Merge concepts" }).click();
  await expect.poll(() => mutations).toHaveLength(2);
  expect(mutations[1]).toEqual({
    method: "POST",
    pathname: "/api/classification/concepts/1/merge",
    payload: { target_concept_id: 2 },
  });
  await expect(manager).toContainText("Merged “Cafe drink” into “Coffee beverage”.");

  await manager.getByLabel("Subcategory to change").selectOption("11");
  await manager.getByLabel("New subcategory name").fill("Coffee places");
  await manager.getByRole("button", { name: "Rename subcategory" }).click();
  await expect.poll(() => mutations).toHaveLength(3);
  expect(mutations[2]).toEqual({
    method: "PATCH",
    pathname: "/api/classification/subcategories/11",
    payload: { name: "Coffee places" },
  });
  await manager.getByLabel("Keep this target subcategory").selectOption("13");
  page.once("dialog", (dialog) => dialog.accept());
  await manager.getByRole("button", { name: "Merge subcategories" }).click();
  await expect.poll(() => mutations).toHaveLength(4);
  expect(mutations[3]).toEqual({
    method: "POST",
    pathname: "/api/classification/subcategories/11/merge",
    payload: { target_subcategory_id: 13 },
  });

  await manager.getByLabel("Duplicate item to retire").selectOption("4");
  await manager.getByLabel("Canonical item to keep").selectOption("3");
  page.once("dialog", (dialog) => dialog.accept());
  await manager.getByRole("button", { name: "Merge household items" }).click();
  await expect.poll(() => mutations).toHaveLength(5);
  expect(mutations[4]).toEqual({
    method: "POST",
    pathname: "/api/classification/household-items/4/merge",
    payload: { target_household_item_id: 3 },
  });
  await expect(manager.getByRole("button", { name: "Undo last household merge" })).toBeVisible();
  await manager.getByRole("button", { name: "Undo last household merge" }).click();
  await expect.poll(() => mutations).toHaveLength(6);
  expect(mutations[5]).toEqual({
    method: "POST",
    pathname: "/api/classification/household-items/4/merge/undo",
    payload: { merge_event_id: 91 },
  });
  await expect(manager).toContainText("Household item merge undone");

  const a11y = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(a11y.violations.filter((violation) => ["critical", "serious"].includes(violation.impact || ""))).toEqual([]);
  if (testInfo.project.name === "mobile-chromium") {
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  }
});

test("owner controls autonomy separately from model-classification consent", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile-chromium", "desktop settings navigation coverage");
  await mockAgentApp(page);
  const classificationPatches: unknown[] = [];
  const consentPosts: unknown[] = [];
  const consents = {
    gmail_receipts: false,
    gmail_promotions: false,
    model_receipt_processing: false,
    model_transaction_classification: false,
    structured_transaction_learning: false,
  };
  let autonomousEnabled = true;
  await page.route("**/api/**", (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === "/api/workspaces") return route.fulfill({ json: [{ id: 1, name: "Patil household", role: "owner", current: true }] });
    if (pathname === "/api/workspaces/1/members") return route.fulfill({ json: [{ user_id: 1, email: "gunjan@example.com", display_name: "Gunjan Patil", role: "owner" }] });
    if (pathname === "/api/integrations") return route.fulfill({ json: {
      gmail: { connected: false },
      plaid: { connected: false, institutions: [] },
      telegram: { connected: false },
      splitwise: { connected: false, available: false },
      google_maps: { connected: true, managed_by: "application" },
      openai: { connected: true, managed_by: "application" },
    } });
    if (pathname === "/api/integrations/onboarding") return route.fulfill({ json: { complete: true } });
    if (pathname === "/api/classification/settings") {
      if (request.method() === "PATCH") {
        const body = request.postDataJSON() as { autonomous_enabled: boolean };
        classificationPatches.push(body);
        autonomousEnabled = body.autonomous_enabled;
      }
      return route.fulfill({ json: {
        autonomous_enabled: autonomousEnabled,
        global_rollout_enabled: false,
        effective_autonomous_enabled: false,
      } });
    }
    if (pathname === "/api/privacy" && request.method() === "GET") return route.fulfill({ json: {
      policy_version: "2026-08-17",
      privacy_url: "/legal/privacy",
      terms_url: "/legal/terms",
      support_email: "support@example.test",
      retention: {},
      consents,
      deletion: { confirmation: "DELETE gunjan@example.com", financial_history_retained_for_audit: true },
    } });
    if (pathname === "/api/privacy/consents" && request.method() === "POST") {
      const body = request.postDataJSON() as { purpose: keyof typeof consents; granted: boolean };
      consentPosts.push(body);
      consents[body.purpose] = body.granted;
      return route.fulfill({ json: { ...body, policy_version: "2026-08-17" } });
    }
    return route.fallback();
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Open account menu/ }).click();
  await page.getByRole("menuitem", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: /Learned behavior/ }).click();
  const autonomy = page.getByLabel("Automatic internal classification");
  await expect(autonomy).toBeChecked();
  await expect(page.getByText(/rollout switch is currently off/)).toBeVisible();
  await autonomy.click();
  await expect.poll(() => classificationPatches).toEqual([{ autonomous_enabled: false }]);
  await expect(autonomy).not.toBeChecked();

  await page.getByRole("button", { name: /Workspace connections/ }).click();
  await expect(page.getByText(/receipt photo or PDF bytes you submit/)).toBeVisible();
  await expect(page.getByText(/transaction merchant, description, and provider-category evidence/)).toBeVisible();
  const modelConsent = page.getByLabel("Model-assisted transaction categories");
  await modelConsent.click();
  await expect.poll(() => consentPosts).toEqual([{ purpose: "model_transaction_classification", granted: true }]);
  await expect(modelConsent).toBeChecked();
});

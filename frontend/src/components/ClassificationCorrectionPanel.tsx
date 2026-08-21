import { useState } from "react";
import { CheckCircle2, Pencil, X } from "lucide-react";

import {
  classificationCategoryLabel,
  classificationCorrectionPayload,
  type ClassificationActivityType,
  type ClassificationConceptList,
  type ClassificationConceptMutation,
  type ClassificationConceptSummary,
  type ClassificationCorrectionDraft,
  type ClassificationSubcategoryList,
  type ClassificationSubcategoryMutation,
  type ClassificationSubcategorySummary,
  type HouseholdItemMergeMutation,
  type ClassificationReceiptItemActivity,
  type ClassificationTransactionActivity,
  type ReplenishmentEligibility,
  type SpendingParentCategory,
} from "@/classificationActivity";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, apiErrorMessage } from "@/lib/api";
import type { HouseholdItem } from "@/types";

type CorrectableClassification =
  | { kind: "receipt-line"; row: ClassificationReceiptItemActivity }
  | { kind: "transaction"; row: ClassificationTransactionActivity };

const PARENT_CATEGORIES: SpendingParentCategory[] = [
  "food_dining",
  "household_home",
  "lifestyle_shopping",
  "personal_care",
  "health",
  "transportation",
  "travel",
  "entertainment",
  "subscriptions",
  "pets",
  "education_office",
  "services",
  "fees_taxes_discounts",
  "other_uncertain",
];

const ACTIVITY_TYPES: ClassificationActivityType[] = [
  "grocery",
  "household_consumable",
  "routine_consumption",
  "one_time_purchase",
  "restaurant_meal",
  "coffee_beverage",
  "food_delivery",
  "nightlife",
  "apparel",
  "electronics",
  "pharmacy",
  "personal_care",
  "beauty",
  "pet_supply",
  "automotive",
  "transportation",
  "travel",
  "entertainment",
  "subscription",
  "education_office",
  "service",
  "tax",
  "tip",
  "discount",
  "fee",
  "refund",
  "non_product",
  "uncertain",
];

const REPLENISHMENT_VALUES: ReplenishmentEligibility[] = [
  "replenishable",
  "potentially_replenishable",
  "not_replenishable",
  "uncertain",
];

const controlClass =
  "h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-800 outline-none transition hover:border-slate-400 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 sm:h-10";

export function ClassificationCorrectionPanel({
  transactions,
  receiptItems,
  onCorrected,
}: {
  transactions: ClassificationTransactionActivity[];
  receiptItems: ClassificationReceiptItemActivity[];
  onCorrected: () => Promise<void>;
}) {
  const rows: CorrectableClassification[] = [
    ...receiptItems.map((row): CorrectableClassification => ({ kind: "receipt-line", row })),
    ...transactions.map((row): CorrectableClassification => ({ kind: "transaction", row })),
  ];
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [draft, setDraft] = useState<ClassificationCorrectionDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [conceptManagerOpen, setConceptManagerOpen] = useState(false);
  const [concepts, setConcepts] = useState<ClassificationConceptSummary[]>([]);
  const [conceptsTruncated, setConceptsTruncated] = useState(false);
  const [conceptsLoading, setConceptsLoading] = useState(false);
  const [conceptSaving, setConceptSaving] = useState<"rename" | "merge" | null>(null);
  const [conceptError, setConceptError] = useState<string | null>(null);
  const [conceptNotice, setConceptNotice] = useState<string | null>(null);
  const [sourceConceptId, setSourceConceptId] = useState("");
  const [targetConceptId, setTargetConceptId] = useState("");
  const [renameConceptName, setRenameConceptName] = useState("");
  const [subcategories, setSubcategories] = useState<ClassificationSubcategorySummary[]>([]);
  const [subcategoriesTruncated, setSubcategoriesTruncated] = useState(false);
  const [sourceSubcategoryId, setSourceSubcategoryId] = useState("");
  const [targetSubcategoryId, setTargetSubcategoryId] = useState("");
  const [renameSubcategoryName, setRenameSubcategoryName] = useState("");
  const [subcategorySaving, setSubcategorySaving] = useState<"rename" | "merge" | null>(null);
  const [householdItems, setHouseholdItems] = useState<HouseholdItem[]>([]);
  const [sourceHouseholdItemId, setSourceHouseholdItemId] = useState("");
  const [targetHouseholdItemId, setTargetHouseholdItemId] = useState("");
  const [householdSaving, setHouseholdSaving] = useState<"merge" | "undo" | null>(null);
  const [lastHouseholdMerge, setLastHouseholdMerge] = useState<HouseholdItemMergeMutation | null>(null);

  if (!rows.length) return null;

  function openEditor(value: CorrectableClassification) {
    setEditingKey(classificationKey(value));
    setDraft(draftFrom(value.row));
    setError(null);
    setSuccess(null);
  }

  function closeEditor() {
    if (saving) return;
    setEditingKey(null);
    setDraft(null);
    setError(null);
  }

  async function loadConcepts() {
    setConceptsLoading(true);
    setConceptError(null);
    try {
      const [result, subcategoryResult, householdResult] = await Promise.all([
        api<ClassificationConceptList>("/api/classification/concepts?limit=200"),
        api<ClassificationSubcategoryList>("/api/classification/subcategories?limit=200"),
        api<HouseholdItem[]>("/api/household/items"),
      ]);
      setConcepts(result.concepts);
      setConceptsTruncated(result.has_more);
      setSubcategories(subcategoryResult.subcategories);
      setSubcategoriesTruncated(subcategoryResult.has_more);
      setHouseholdItems(householdResult);
      setSourceConceptId((current) => result.concepts.some((concept) => String(concept.id) === current) ? current : "");
      setTargetConceptId((current) => result.concepts.some((concept) => String(concept.id) === current) ? current : "");
      setSourceSubcategoryId((current) => subcategoryResult.subcategories.some((value) => String(value.id) === current) ? current : "");
      setTargetSubcategoryId((current) => subcategoryResult.subcategories.some((value) => String(value.id) === current) ? current : "");
      setSourceHouseholdItemId((current) => householdResult.some((value) => String(value.id) === current) ? current : "");
      setTargetHouseholdItemId((current) => householdResult.some((value) => String(value.id) === current) ? current : "");
    } catch (caught) {
      setConceptError(apiErrorMessage(caught, "ExpenseOps could not load classification concepts."));
    } finally {
      setConceptsLoading(false);
    }
  }

  async function openConceptManager() {
    setConceptManagerOpen(true);
    setConceptNotice(null);
    await loadConcepts();
  }

  async function renameConcept() {
    const source = concepts.find((concept) => String(concept.id) === sourceConceptId);
    const name = renameConceptName.trim();
    if (!source || !name || conceptSaving) return;
    setConceptSaving("rename");
    setConceptError(null);
    setConceptNotice(null);
    try {
      const result = await api<ClassificationConceptMutation>(
        `/api/classification/concepts/${source.id}`,
        { method: "PATCH", body: JSON.stringify({ name }) },
      );
      setConceptNotice(result.applied ? `Renamed “${source.name}” to “${result.target_name}”.` : `“${source.name}” already has that name.`);
      await loadConcepts();
      await onCorrected();
    } catch (caught) {
      setConceptError(apiErrorMessage(caught, "ExpenseOps could not rename this concept."));
    } finally {
      setConceptSaving(null);
    }
  }

  async function mergeConcept() {
    const source = concepts.find((concept) => String(concept.id) === sourceConceptId);
    const target = concepts.find((concept) => String(concept.id) === targetConceptId);
    if (!source || !target || conceptSaving) return;
    if (!window.confirm(
      `Merge the classification concept “${source.name}” into “${target.name}”? `
      + "This aligns classification labels, aliases, and current household semantic links; it does not combine household items or purchase history.",
    )) return;
    setConceptSaving("merge");
    setConceptError(null);
    setConceptNotice(null);
    try {
      const result = await api<ClassificationConceptMutation>(
        `/api/classification/concepts/${source.id}/merge`,
        { method: "POST", body: JSON.stringify({ target_concept_id: target.id }) },
      );
      setConceptNotice(`Merged “${source.name}” into “${result.target_name}”.`);
      setSourceConceptId("");
      setTargetConceptId("");
      setRenameConceptName("");
      await loadConcepts();
      await onCorrected();
    } catch (caught) {
      setConceptError(apiErrorMessage(caught, "ExpenseOps could not merge these concepts."));
    } finally {
      setConceptSaving(null);
    }
  }

  async function renameSubcategory() {
    const source = selectedSubcategory(subcategories, sourceSubcategoryId);
    const name = renameSubcategoryName.trim();
    if (!source || !name || subcategorySaving) return;
    setSubcategorySaving("rename");
    setConceptError(null);
    setConceptNotice(null);
    try {
      const result = await api<ClassificationSubcategoryMutation>(
        `/api/classification/subcategories/${source.id}`,
        { method: "PATCH", body: JSON.stringify({ name }) },
      );
      setConceptNotice(result.applied ? `Renamed “${source.name}” to “${result.target_name}”.` : `“${source.name}” already has that name.`);
      await loadConcepts();
      await onCorrected();
    } catch (caught) {
      setConceptError(apiErrorMessage(caught, "ExpenseOps could not rename this subcategory."));
    } finally {
      setSubcategorySaving(null);
    }
  }

  async function mergeSubcategory() {
    const source = selectedSubcategory(subcategories, sourceSubcategoryId);
    const target = selectedSubcategory(subcategories, targetSubcategoryId);
    if (!source || !target || subcategorySaving) return;
    if (!window.confirm(
      `Merge the subcategory “${source.name}” into “${target.name}”? `
      + "Current classifications will be corrected and prior ledger versions will remain in audit history.",
    )) return;
    setSubcategorySaving("merge");
    setConceptError(null);
    setConceptNotice(null);
    try {
      const result = await api<ClassificationSubcategoryMutation>(
        `/api/classification/subcategories/${source.id}/merge`,
        { method: "POST", body: JSON.stringify({ target_subcategory_id: target.id }) },
      );
      setConceptNotice(`Merged “${source.name}” into “${result.target_name}”.`);
      setSourceSubcategoryId("");
      setTargetSubcategoryId("");
      setRenameSubcategoryName("");
      await loadConcepts();
      await onCorrected();
    } catch (caught) {
      setConceptError(apiErrorMessage(caught, "ExpenseOps could not merge these subcategories."));
    } finally {
      setSubcategorySaving(null);
    }
  }

  async function mergeHouseholdItem() {
    const source = householdItems.find((value) => String(value.id) === sourceHouseholdItemId);
    const target = householdItems.find((value) => String(value.id) === targetHouseholdItemId);
    if (!source || !target || householdSaving) return;
    if (!window.confirm(
      `Merge “${source.name}” into “${target.name}”? `
      + "Receipt links, acquisition history, aliases, errands, cadence, and open predictions will be repaired. The source is disabled, not deleted, and this merge can be undone.",
    )) return;
    setHouseholdSaving("merge");
    setConceptError(null);
    setConceptNotice(null);
    try {
      const result = await api<HouseholdItemMergeMutation>(
        `/api/classification/household-items/${source.id}/merge`,
        { method: "POST", body: JSON.stringify({ target_household_item_id: target.id }) },
      );
      setLastHouseholdMerge(result);
      setConceptNotice(`Merged “${source.name}” into “${result.target_name}”.`);
      setSourceHouseholdItemId("");
      setTargetHouseholdItemId("");
      await loadConcepts();
      await onCorrected();
    } catch (caught) {
      setConceptError(apiErrorMessage(caught, "ExpenseOps could not merge these household items."));
    } finally {
      setHouseholdSaving(null);
    }
  }

  async function undoHouseholdItemMerge() {
    if (!lastHouseholdMerge || householdSaving) return;
    setHouseholdSaving("undo");
    setConceptError(null);
    try {
      await api<HouseholdItemMergeMutation>(
        `/api/classification/household-items/${lastHouseholdMerge.source_household_item_id}/merge/undo`,
        { method: "POST", body: JSON.stringify({ merge_event_id: lastHouseholdMerge.merge_event_id }) },
      );
      setConceptNotice("Household item merge undone. Both items and their histories were restored.");
      setLastHouseholdMerge(null);
      await loadConcepts();
      await onCorrected();
    } catch (caught) {
      setConceptError(apiErrorMessage(caught, "ExpenseOps could not safely undo this merge."));
    } finally {
      setHouseholdSaving(null);
    }
  }

  async function save(value: CorrectableClassification) {
    if (!draft || saving) return;
    if (!isPositiveIntegerId(value.row.public_id)) {
      setError("This classification source is no longer available to correct.");
      return;
    }
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const target = value.kind === "receipt-line" ? "receipt-lines" : "transactions";
      await api<{ applied: boolean; reason: string }>(
        `/api/classification/${target}/${value.row.public_id}`,
        {
          method: "PATCH",
          body: JSON.stringify(classificationCorrectionPayload(draft)),
        },
      );
      const label = classificationLabel(value);
      setEditingKey(null);
      setDraft(null);
      setSuccess(`${label} classification corrected.`);
      await onCorrected();
    } catch (caught) {
      setError(apiErrorMessage(caught, "ExpenseOps could not save this correction."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section aria-labelledby="classification-corrections-title" className="space-y-3 border-t border-indigo-100 pt-4">
      <div>
        <h3 id="classification-corrections-title" className="text-sm font-semibold text-slate-950">Review or correct a classification</h3>
        <p className="mt-1 text-xs leading-5 text-slate-600">
          Corrections update the canonical record and remain in audit history. They do not post, purchase, split, or otherwise move money.
        </p>
      </div>
      {success ? (
        <p role="status" className="flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />{success}
        </p>
      ) : null}
      <div className="space-y-2">
        {rows.map((value) => {
          const key = classificationKey(value);
          const expanded = key === editingKey && draft !== null;
          const label = classificationLabel(value);
          const editorId = `classification-correction-${value.kind}-${value.row.public_id}`;
          return (
            <div key={key} className="overflow-hidden rounded-xl border border-slate-200 bg-white">
              <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <p className="break-words text-sm font-semibold text-slate-950">{label}</p>
                  <p className="mt-1 break-words text-xs leading-5 text-slate-600">
                    {classificationCategoryLabel(value.row.parent_category)}
                    {value.row.subcategory ? ` / ${value.row.subcategory}` : ""}
                    {value.row.concept ? ` · ${value.row.concept}` : ""}
                    {` · ${classificationValueLabel(value.row.activity_type)}`}
                  </p>
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="w-full shrink-0 sm:w-auto"
                  onClick={() => expanded ? closeEditor() : openEditor(value)}
                  disabled={!value.row.source_available || !isPositiveIntegerId(value.row.public_id) || saving}
                  aria-expanded={expanded}
                  aria-controls={editorId}
                >
                  {expanded ? <X className="h-4 w-4" aria-hidden="true" /> : <Pencil className="h-4 w-4" aria-hidden="true" />}
                  {expanded ? "Cancel" : `Correct ${label}`}
                </Button>
              </div>
              {expanded ? (
                <form
                  id={editorId}
                  className="space-y-4 border-t border-slate-200 bg-slate-50/70 p-3 sm:p-4"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void save(value);
                  }}
                >
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    <label className="grid gap-1 text-xs font-medium text-slate-700">
                      Parent category
                      <select
                        className={controlClass}
                        value={draft.spending_parent_category}
                        onChange={(event) => {
                          const parent = event.target.value as SpendingParentCategory;
                          setDraft((current) => current ? {
                            ...current,
                            spending_parent_category: parent,
                            ...(parent === "other_uncertain" ? {
                              item_activity_type: "uncertain" as const,
                              replenishment_eligibility: "uncertain" as const,
                              subcategory_name: "",
                              canonical_concept: "",
                            } : {}),
                          } : current);
                        }}
                      >
                        {PARENT_CATEGORIES.map((category) => <option key={category} value={category}>{classificationCategoryLabel(category)}</option>)}
                      </select>
                    </label>
                    <label className="grid gap-1 text-xs font-medium text-slate-700">
                      Activity type
                      <select className={controlClass} value={draft.item_activity_type} onChange={(event) => setDraft((current) => current ? { ...current, item_activity_type: event.target.value as ClassificationActivityType } : current)}>
                        {ACTIVITY_TYPES.map((activity) => <option key={activity} value={activity}>{classificationValueLabel(activity)}</option>)}
                      </select>
                    </label>
                    <label className="grid gap-1 text-xs font-medium text-slate-700 sm:col-span-2 xl:col-span-1">
                      Replenishment
                      <select className={controlClass} value={draft.replenishment_eligibility} onChange={(event) => setDraft((current) => current ? { ...current, replenishment_eligibility: event.target.value as ReplenishmentEligibility } : current)}>
                        {REPLENISHMENT_VALUES.map((eligibility) => <option key={eligibility} value={eligibility}>{classificationValueLabel(eligibility)}</option>)}
                      </select>
                    </label>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <OptionalClassificationField
                      id={`${editorId}-subcategory`}
                      label="Subcategory (optional)"
                      value={draft.subcategory_name}
                      maximum={128}
                      onChange={(value) => setDraft((current) => current ? { ...current, subcategory_name: value } : current)}
                    />
                    <OptionalClassificationField
                      id={`${editorId}-concept`}
                      label="Canonical concept (optional)"
                      value={draft.canonical_concept}
                      maximum={255}
                      onChange={(value) => setDraft((current) => current ? { ...current, canonical_concept: value } : current)}
                    />
                  </div>
                  {error ? <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900">{error}</p> : null}
                  <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                    <Button type="button" variant="outline" onClick={closeEditor} disabled={saving}>Cancel</Button>
                    <Button type="submit" disabled={saving}>{saving ? "Saving correction…" : "Save correction"}</Button>
                  </div>
                </form>
              ) : null}
            </div>
          );
        })}
      </div>
      <div className="border-t border-slate-200 pt-3">
        {!conceptManagerOpen ? (
          <Button type="button" size="sm" variant="outline" onClick={() => void openConceptManager()}>
            Manage taxonomy and duplicates
          </Button>
        ) : (
          <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-3 sm:p-4" data-testid="classification-concept-manager">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h4 className="text-sm font-semibold text-slate-950">Manage classification taxonomy and duplicate items</h4>
                <p className="mt-1 text-xs leading-5 text-slate-600">
                  Workspace-owner control. Taxonomy changes preserve prior ledger versions. Household merges repair bounded history and can be undone.
                </p>
              </div>
              <Button type="button" size="sm" variant="ghost" onClick={() => setConceptManagerOpen(false)} disabled={conceptSaving !== null || subcategorySaving !== null || householdSaving !== null}>
                <X className="h-4 w-4" aria-hidden="true" /> Close
              </Button>
            </div>
            {conceptsLoading ? <p role="status" className="text-sm text-slate-600">Loading concepts…</p> : null}
            {conceptNotice ? <p role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">{conceptNotice}</p> : null}
            {conceptError ? <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900">{conceptError}</p> : null}
            {!conceptsLoading && concepts.length ? (
              <>
                <label className="grid gap-1 text-xs font-medium text-slate-700">
                  Concept to change
                  <select
                    className={controlClass}
                    value={sourceConceptId}
                    onChange={(event) => {
                      const next = event.target.value;
                      const selected = concepts.find((concept) => String(concept.id) === next);
                      setSourceConceptId(next);
                      setTargetConceptId("");
                      setRenameConceptName(selected?.name || "");
                      setConceptError(null);
                      setConceptNotice(null);
                    }}
                  >
                    <option value="">Choose a concept…</option>
                    {concepts.map((concept) => <option key={concept.id} value={concept.id}>{conceptOptionLabel(concept)}</option>)}
                  </select>
                </label>
                {selectedConcept(concepts, sourceConceptId) ? (
                  <div className="grid gap-4 lg:grid-cols-2">
                    <form className="space-y-3 rounded-lg border border-slate-200 bg-slate-50/70 p-3" onSubmit={(event) => { event.preventDefault(); void renameConcept(); }}>
                      <div><h5 className="text-sm font-semibold text-slate-900">Rename concept</h5><p className="mt-1 text-xs leading-5 text-slate-600">The old name remains a confirmed alias for future classification.</p></div>
                      {selectedConcept(concepts, sourceConceptId)?.linked_household_item_count ? <p className="text-xs leading-5 text-amber-800">This changes the classification label only. The linked HouseholdItem keeps its existing user-facing name and purchase history.</p> : null}
                      <label className="grid gap-1 text-xs font-medium text-slate-700">
                        New concept name
                        <Input value={renameConceptName} maxLength={255} onChange={(event) => setRenameConceptName(event.target.value)} />
                      </label>
                      <Button type="submit" size="sm" disabled={!renameConceptName.trim() || conceptSaving !== null}>{conceptSaving === "rename" ? "Renaming…" : "Rename concept"}</Button>
                    </form>
                    <form className="space-y-3 rounded-lg border border-slate-200 bg-slate-50/70 p-3" onSubmit={(event) => { event.preventDefault(); void mergeConcept(); }}>
                      <div><h5 className="text-sm font-semibold text-slate-900">Merge into another concept</h5><p className="mt-1 text-xs leading-5 text-slate-600">Only semantically compatible concepts are offered. The source name remains resolvable through the taxonomy.</p></div>
                      <label className="grid gap-1 text-xs font-medium text-slate-700">
                        Keep this target concept
                        <select className={controlClass} value={targetConceptId} onChange={(event) => setTargetConceptId(event.target.value)}>
                          <option value="">Choose a compatible target…</option>
                          {compatibleMergeTargets(concepts, sourceConceptId).map((concept) => <option key={concept.id} value={concept.id}>{conceptOptionLabel(concept)}</option>)}
                        </select>
                      </label>
                      <Button type="submit" size="sm" variant="outline" disabled={!targetConceptId || conceptSaving !== null}>{conceptSaving === "merge" ? "Merging…" : "Merge concepts"}</Button>
                    </form>
                  </div>
                ) : null}
                {conceptsTruncated ? <p className="text-xs text-amber-800">Only the first 200 concepts are shown. Narrow management is required before merging concepts outside this bounded list.</p> : null}
              </>
            ) : null}
            {!conceptsLoading && !concepts.length && !conceptError ? <p className="text-sm text-slate-600">No active classification concepts are available.</p> : null}
            {!conceptsLoading && subcategories.length ? (
              <div className="space-y-3 border-t border-slate-200 pt-4" data-testid="classification-subcategory-manager">
                <div>
                  <h5 className="text-sm font-semibold text-slate-950">Rename or merge subcategories</h5>
                  <p className="mt-1 text-xs leading-5 text-slate-600">Only subcategories under the same parent category can be merged.</p>
                </div>
                <label className="grid gap-1 text-xs font-medium text-slate-700">
                  Subcategory to change
                  <select
                    className={controlClass}
                    value={sourceSubcategoryId}
                    onChange={(event) => {
                      const next = event.target.value;
                      const selected = selectedSubcategory(subcategories, next);
                      setSourceSubcategoryId(next);
                      setTargetSubcategoryId("");
                      setRenameSubcategoryName(selected?.name || "");
                      setConceptError(null);
                      setConceptNotice(null);
                    }}
                  >
                    <option value="">Choose a subcategory…</option>
                    {subcategories.map((value) => <option key={value.id} value={value.id}>{subcategoryOptionLabel(value)}</option>)}
                  </select>
                </label>
                {selectedSubcategory(subcategories, sourceSubcategoryId) ? (
                  <div className="grid gap-4 lg:grid-cols-2">
                    <form className="space-y-3 rounded-lg border border-slate-200 bg-slate-50/70 p-3" onSubmit={(event) => { event.preventDefault(); void renameSubcategory(); }}>
                      <label className="grid gap-1 text-xs font-medium text-slate-700">
                        New subcategory name
                        <Input value={renameSubcategoryName} maxLength={128} onChange={(event) => setRenameSubcategoryName(event.target.value)} />
                      </label>
                      <Button type="submit" size="sm" disabled={!renameSubcategoryName.trim() || subcategorySaving !== null}>{subcategorySaving === "rename" ? "Renaming…" : "Rename subcategory"}</Button>
                    </form>
                    <form className="space-y-3 rounded-lg border border-slate-200 bg-slate-50/70 p-3" onSubmit={(event) => { event.preventDefault(); void mergeSubcategory(); }}>
                      <label className="grid gap-1 text-xs font-medium text-slate-700">
                        Keep this target subcategory
                        <select className={controlClass} value={targetSubcategoryId} onChange={(event) => setTargetSubcategoryId(event.target.value)}>
                          <option value="">Choose a compatible target…</option>
                          {compatibleSubcategoryTargets(subcategories, sourceSubcategoryId).map((value) => <option key={value.id} value={value.id}>{subcategoryOptionLabel(value)}</option>)}
                        </select>
                      </label>
                      <Button type="submit" size="sm" variant="outline" disabled={!targetSubcategoryId || subcategorySaving !== null}>{subcategorySaving === "merge" ? "Merging…" : "Merge subcategories"}</Button>
                    </form>
                  </div>
                ) : null}
                {subcategoriesTruncated ? <p className="text-xs text-amber-800">Only the first 200 subcategories are shown.</p> : null}
              </div>
            ) : null}
            {!conceptsLoading && (householdItems.length > 1 || lastHouseholdMerge) ? (
              <div className="space-y-3 border-t border-slate-200 pt-4" data-testid="classification-household-item-manager">
                <div>
                  <h5 className="text-sm font-semibold text-slate-950">Merge duplicate household items</h5>
                  <p className="mt-1 text-xs leading-5 text-slate-600">Choose the duplicate to retire, then the canonical item to keep. ExpenseOps preserves acquisition facts and repairs cadence, receipt links, aliases, errands, and open predictions.</p>
                </div>
                {householdItems.length > 1 ? <div className="grid gap-3 sm:grid-cols-2">
                  <label className="grid gap-1 text-xs font-medium text-slate-700">
                    Duplicate item to retire
                    <select className={controlClass} value={sourceHouseholdItemId} onChange={(event) => { setSourceHouseholdItemId(event.target.value); setTargetHouseholdItemId(""); }}>
                      <option value="">Choose an item…</option>
                      {householdItems.map((value) => <option key={value.id} value={value.id}>{value.name}</option>)}
                    </select>
                  </label>
                  <label className="grid gap-1 text-xs font-medium text-slate-700">
                    Canonical item to keep
                    <select className={controlClass} value={targetHouseholdItemId} onChange={(event) => setTargetHouseholdItemId(event.target.value)} disabled={!sourceHouseholdItemId}>
                      <option value="">Choose the item to keep…</option>
                      {householdItems.filter((value) => String(value.id) !== sourceHouseholdItemId).map((value) => <option key={value.id} value={value.id}>{value.name}</option>)}
                    </select>
                  </label>
                </div> : null}
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                  {householdItems.length > 1 ? <Button type="button" size="sm" variant="outline" onClick={() => void mergeHouseholdItem()} disabled={!sourceHouseholdItemId || !targetHouseholdItemId || householdSaving !== null}>{householdSaving === "merge" ? "Merging history…" : "Merge household items"}</Button> : null}
                  {lastHouseholdMerge ? <Button type="button" size="sm" variant="ghost" onClick={() => void undoHouseholdItemMerge()} disabled={householdSaving !== null}>{householdSaving === "undo" ? "Undoing…" : "Undo last household merge"}</Button> : null}
                </div>
              </div>
            ) : null}
          </div>
        )}
      </div>
    </section>
  );
}

function OptionalClassificationField({
  id,
  label,
  value,
  maximum,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  maximum: number;
  onChange: (value: string) => void;
}) {
  const hintId = `${id}-hint`;
  return (
    <div className="grid gap-1">
      <label htmlFor={id} className="text-xs font-medium text-slate-700">{label}</label>
      <Input id={id} value={value} maxLength={maximum} aria-describedby={hintId} onChange={(event) => onChange(event.target.value)} />
      <div className="flex min-h-5 items-start justify-between gap-2">
        <span id={hintId} className="text-xs leading-5 text-slate-500">Leave empty to clear this value.</span>
        <button type="button" aria-label={`Clear ${label}`} className="shrink-0 rounded text-xs font-semibold text-indigo-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={() => onChange("")} disabled={!value}>
          Clear
        </button>
      </div>
    </div>
  );
}

function draftFrom(
  row: ClassificationReceiptItemActivity | ClassificationTransactionActivity,
): ClassificationCorrectionDraft {
  return {
    spending_parent_category: row.parent_category,
    item_activity_type: row.activity_type,
    replenishment_eligibility: row.replenishment_eligibility,
    subcategory_name: row.subcategory || "",
    canonical_concept: row.concept || "",
  };
}

function classificationKey(value: CorrectableClassification): string {
  return `${value.kind}:${value.row.public_id}`;
}

function classificationLabel(value: CorrectableClassification): string {
  return value.kind === "receipt-line" ? value.row.name : value.row.merchant;
}

function classificationValueLabel(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function isPositiveIntegerId(value: string): boolean {
  return /^[1-9]\d*$/.test(value);
}

function selectedConcept(
  concepts: ClassificationConceptSummary[],
  conceptId: string,
): ClassificationConceptSummary | undefined {
  return concepts.find((concept) => String(concept.id) === conceptId);
}

function compatibleMergeTargets(
  concepts: ClassificationConceptSummary[],
  sourceConceptId: string,
): ClassificationConceptSummary[] {
  const source = selectedConcept(concepts, sourceConceptId);
  if (!source) return [];
  return concepts.filter((concept) => (
    concept.id !== source.id
    && concept.parent_category === source.parent_category
    && concept.subcategory_id === source.subcategory_id
    && concept.item_activity_type === source.item_activity_type
    && concept.replenishment_eligibility === source.replenishment_eligibility
  ));
}

function conceptOptionLabel(concept: ClassificationConceptSummary): string {
  const category = classificationCategoryLabel(concept.parent_category);
  return `${concept.name} — ${category}${concept.subcategory_name ? ` / ${concept.subcategory_name}` : ""}`;
}

function selectedSubcategory(
  subcategories: ClassificationSubcategorySummary[],
  subcategoryId: string,
): ClassificationSubcategorySummary | undefined {
  return subcategories.find((value) => String(value.id) === subcategoryId);
}

function compatibleSubcategoryTargets(
  subcategories: ClassificationSubcategorySummary[],
  sourceSubcategoryId: string,
): ClassificationSubcategorySummary[] {
  const source = selectedSubcategory(subcategories, sourceSubcategoryId);
  if (!source) return [];
  return subcategories.filter((value) => (
    value.id !== source.id && value.parent_category === source.parent_category
  ));
}

function subcategoryOptionLabel(value: ClassificationSubcategorySummary): string {
  return `${value.name} — ${classificationCategoryLabel(value.parent_category)}`;
}

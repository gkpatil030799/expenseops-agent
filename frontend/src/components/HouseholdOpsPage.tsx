import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  CalendarClock,
  Check,
  CheckCircle2,
  CircleOff,
  Clock3,
  Brain,
  ExternalLink,
  House,
  ListChecks,
  LocateFixed,
  MapPin,
  PackageCheck,
  Pencil,
  Plus,
  RefreshCw,
  ReceiptText,
  RotateCcw,
  Route,
  ShoppingBasket,
  Trash2,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { api } from "@/lib/api";
import type {
  Errand,
  ErrandPlan,
  HouseholdItem,
  PlaceCandidate,
  PlaceSearchResult,
  PlanningLocation,
  PurchaseReceipt,
  ReplenishmentLearningSummary,
  ReplenishmentSuggestion,
  SavedLocation,
  WhileOutPlan,
} from "@/types";

type ErrandForm = {
  title: string;
  errand_type: Errand["errand_type"];
  place_name: string;
  place_address: string;
  due_at: string;
  estimated_duration_minutes: string;
  priority: Errand["priority"];
  notes: string;
  included_in_next_plan: boolean;
};

type ItemForm = {
  name: string;
  quantity: string;
  unit: string;
  preferred_place_name: string;
  preferred_place_address: string;
  replenishment_mode: HouseholdItem["replenishment_mode"];
  cadence_days: string;
  last_acquired_at: string;
  notes: string;
};

type LocationForm = {
  label: string;
  address: string;
  location_type: SavedLocation["location_type"];
};

type ManualPlaceForm = {
  canonical_name: string;
  full_address: string;
};

type ReceiptItemDraft = {
  receiptId: number;
  lineId: number;
  name: string;
  cadence_days: string;
  replenishment_mode: HouseholdItem["replenishment_mode"];
};

type HouseholdView = "today" | "errands" | "receipts" | "staples" | "history";

type GmailReceiptSyncStatus = {
  configured: boolean;
  last_successful_sync_at: string | null;
  latest_receipt_at: string | null;
};

const emptyErrandForm: ErrandForm = {
  title: "",
  errand_type: "other",
  place_name: "",
  place_address: "",
  due_at: "",
  estimated_duration_minutes: "",
  priority: "normal",
  notes: "",
  included_in_next_plan: true,
};

const emptyItemForm: ItemForm = {
  name: "",
  quantity: "",
  unit: "",
  preferred_place_name: "",
  preferred_place_address: "",
  replenishment_mode: "either",
  cadence_days: "",
  last_acquired_at: "",
  notes: "",
};

const emptyLocationForm: LocationForm = {
  label: "",
  address: "",
  location_type: "custom",
};

const emptyManualPlaceForm: ManualPlaceForm = {
  canonical_name: "",
  full_address: "",
};

const controlClass =
  "h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-800 outline-none transition hover:border-slate-400 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 sm:h-10";

export function HouseholdOpsPage() {
  const [householdView, setHouseholdView] = useState<HouseholdView>("today");
  const [errands, setErrands] = useState<Errand[]>([]);
  const [items, setItems] = useState<HouseholdItem[]>([]);
  const [plan, setPlan] = useState<ErrandPlan | null>(null);
  const [receipts, setReceipts] = useState<PurchaseReceipt[]>([]);
  const [gmailSyncStatus, setGmailSyncStatus] = useState<GmailReceiptSyncStatus | null>(null);
  const [suggestions, setSuggestions] = useState<ReplenishmentSuggestion[]>([]);
  const [busy, setBusy] = useState<string | null>("initial");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showErrandForm, setShowErrandForm] = useState(false);
  const [showItemForm, setShowItemForm] = useState(false);
  const [editingErrandId, setEditingErrandId] = useState<number | null>(null);
  const [editingItemId, setEditingItemId] = useState<number | null>(null);
  const [errandForm, setErrandForm] = useState<ErrandForm>(emptyErrandForm);
  const [itemForm, setItemForm] = useState<ItemForm>(emptyItemForm);
  const [locations, setLocations] = useState<SavedLocation[]>([]);
  const [whileOut, setWhileOut] = useState<WhileOutPlan | null>(null);
  const [originChoice, setOriginChoice] = useState("manual");
  const [manualOrigin, setManualOrigin] = useState("");
  const [primaryDestination, setPrimaryDestination] = useState("");
  const [finalChoice, setFinalChoice] = useState("");
  const [availableMinutes, setAvailableMinutes] = useState("");
  const [currentLocation, setCurrentLocation] = useState<PlanningLocation | null>(null);
  const [geoStatus, setGeoStatus] = useState<string | null>(null);
  const [showLocationForm, setShowLocationForm] = useState(false);
  const [editingLocationId, setEditingLocationId] = useState<number | null>(null);
  const [locationForm, setLocationForm] = useState<LocationForm>(emptyLocationForm);
  const [placeSearch, setPlaceSearch] = useState<PlaceSearchResult | null>(null);
  const [manualPlace, setManualPlace] = useState<ManualPlaceForm>(emptyManualPlaceForm);
  const [rememberPlace, setRememberPlace] = useState(true);
  const [learning, setLearning] = useState<ReplenishmentLearningSummary | null>(null);
  const [receiptItemDraft, setReceiptItemDraft] = useState<ReceiptItemDraft | null>(null);
  const [reviewingReceiptId, setReviewingReceiptId] = useState<number | null>(null);
  const [expandedHistoryReceiptId, setExpandedHistoryReceiptId] = useState<number | null>(null);

  const activeErrands = useMemo(
    () => errands.filter((errand) => errand.status === "open" || errand.status === "planned"),
    [errands],
  );
  const errandHistory = useMemo(
    () => errands.filter((errand) => errand.status === "completed" || errand.status === "skipped"),
    [errands],
  );
  const receiptQueue = useMemo(
    () => receipts.filter((receipt) => receipt.parse_status === "needs_review" || receipt.parse_status === "failed"),
    [receipts],
  );
  const receiptHistory = useMemo(
    () => receipts.filter((receipt) => receipt.parse_status === "confirmed" || receipt.parse_status === "ignored"),
    [receipts],
  );
  const dueItems = useMemo(() => items.filter((item) => item.should_surface), [items]);
  const enabledItems = useMemo(() => items.filter((item) => item.enabled), [items]);

  useEffect(() => {
    void loadHouseholdOps();
  }, []);

  async function loadHouseholdOps() {
    setBusy((current) => current === "initial" ? "initial" : "refresh");
    setError(null);
    try {
      const [loadedErrands, loadedItems, latestPlan, loadedLocations, loadedLearning, loadedReceipts, loadedGmailStatus] = await Promise.all([
        api<Errand[]>("/api/household/errands"),
        api<HouseholdItem[]>("/api/household/items"),
        api<ErrandPlan | null>("/api/household/errand-plans/latest"),
        api<SavedLocation[]>("/api/household/locations"),
        api<ReplenishmentLearningSummary>("/api/replenishment/summary"),
        api<PurchaseReceipt[]>("/api/replenishment/receipts?limit=50"),
        api<GmailReceiptSyncStatus>("/api/replenishment/gmail/status"),
      ]);
      setErrands(loadedErrands);
      setItems(loadedItems);
      setPlan(latestPlan);
      setLocations(loadedLocations);
      setLearning(loadedLearning);
      setReceipts(loadedReceipts);
      setGmailSyncStatus(loadedGmailStatus);
      setOriginChoice((current) => {
        if (current !== "manual" || manualOrigin) return current;
        const preferred = loadedLocations.find((location) => location.location_type === "home");
        return preferred ? `saved:${preferred.id}` : current;
      });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function run(action: string, work: () => Promise<void>) {
    setBusy(action);
    setError(null);
    setNotice(null);
    try {
      await work();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function saveErrand() {
    await run("save-errand", async () => {
      const payload = {
        title: errandForm.title.trim(),
        errand_type: errandForm.errand_type,
        place_name: nullable(errandForm.place_name),
        place_address: nullable(errandForm.place_address),
        due_at: toIso(errandForm.due_at),
        estimated_duration_minutes: errandForm.estimated_duration_minutes
          ? Number(errandForm.estimated_duration_minutes)
          : null,
        priority: errandForm.priority,
        notes: nullable(errandForm.notes),
        included_in_next_plan: errandForm.included_in_next_plan,
      };
      if (editingErrandId) {
        await api<Errand>(`/api/household/errands/${editingErrandId}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        setNotice("Errand updated.");
      } else {
        await api<Errand>("/api/household/errands", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        setNotice("Errand added to the inbox.");
      }
      closeErrandForm();
      await loadHouseholdOps();
    });
  }

  async function saveItem() {
    await run("save-item", async () => {
      const payload = {
        name: itemForm.name.trim(),
        quantity: nullable(itemForm.quantity),
        unit: nullable(itemForm.unit),
        preferred_place_name: nullable(itemForm.preferred_place_name),
        preferred_place_address: nullable(itemForm.preferred_place_address),
        replenishment_mode: itemForm.replenishment_mode,
        cadence_days: Number(itemForm.cadence_days),
        last_acquired_at: toIso(itemForm.last_acquired_at),
        notes: nullable(itemForm.notes),
      };
      if (editingItemId) {
        await api<HouseholdItem>(`/api/household/items/${editingItemId}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        setNotice("Household item updated.");
      } else {
        await api<HouseholdItem>("/api/household/items", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        setNotice("Household item added.");
      }
      closeItemForm();
      await loadHouseholdOps();
    });
  }

  async function mutateErrand(
    errand: Errand,
    action: "complete" | "skip" | "toggle" | "delete",
  ) {
    if (action === "delete" && !window.confirm(`Delete “${errand.title}”?`)) return;
    await run(`${action}-errand-${errand.id}`, async () => {
      if (action === "delete") {
        await deleteRequest(`/api/household/errands/${errand.id}`);
      } else if (action === "toggle") {
        await api<Errand>(`/api/household/errands/${errand.id}`, {
          method: "PATCH",
          body: JSON.stringify({ included_in_next_plan: !errand.included_in_next_plan }),
        });
      } else if (action === "complete") {
        const markLinked = errand.linked_household_items.length
          ? window.confirm("Also mark the linked household item(s) as bought?")
          : false;
        await api<Errand>(`/api/household/errands/${errand.id}/complete`, {
          method: "POST",
          body: JSON.stringify({ mark_linked_items_acquired: markLinked }),
        });
        setNotice("Errand completed.");
      } else {
        await api<Errand>(`/api/household/errands/${errand.id}/skip`, { method: "POST" });
        setNotice("Errand skipped.");
      }
      await loadHouseholdOps();
    });
  }

  async function mutateItem(
    item: HouseholdItem,
    action: "bought" | "still-have" | "skip-once" | "disable" | "add" | "delete",
  ) {
    if (action === "delete" && !window.confirm(`Delete “${item.name}”?`)) return;
    await run(`${action}-item-${item.id}`, async () => {
      if (action === "delete") {
        await deleteRequest(`/api/household/items/${item.id}`);
      } else if (action === "add") {
        await api<Errand>(`/api/household/items/${item.id}/add-to-errands`, {
          method: "POST",
          body: JSON.stringify({}),
        });
        setNotice(`${item.name} was added to an appropriate trip.`);
      } else {
        await api<HouseholdItem>(`/api/household/items/${item.id}/${action}`, {
          method: "POST",
          body: action === "still-have" ? JSON.stringify({}) : undefined,
        });
        setNotice(
          action === "bought"
            ? `${item.name} marked as bought.`
            : action === "still-have"
              ? `${item.name} snoozed without changing purchase history.`
              : action === "disable"
                ? `${item.name} disabled.`
                : `${item.name} skipped once.`,
        );
      }
      await loadHouseholdOps();
    });
  }

  async function refreshReplenishment() {
    await run("replenishment", async () => {
      const result = await api<{
        suggestions: ReplenishmentSuggestion[];
        created_errand_count: number;
      }>("/api/household/replenishment/refresh", { method: "POST" });
      setSuggestions(result.suggestions);
      setNotice(
        result.suggestions.length
          ? "Replenishment recommendations refreshed."
          : "Nothing is due based on the current cadence data.",
      );
      await loadHouseholdOps();
    });
  }

  async function updateReceiptMatch(
    receipt: PurchaseReceipt,
    line: PurchaseReceipt["items"][number],
    value: string,
  ) {
    if (value === "create") {
      setReceiptItemDraft({
        receiptId: receipt.id,
        lineId: line.id,
        name: line.raw_name,
        cadence_days: "30",
        replenishment_mode: "either",
      });
      return;
    }
    if (receiptItemDraft?.lineId === line.id) setReceiptItemDraft(null);
    await run(`receipt-line-${line.id}`, async () => {
      await api(`/api/replenishment/receipts/${receipt.id}/items/${line.id}`, {
        method: "PATCH",
        body: JSON.stringify(
          value === "reject"
            ? { household_item_id: null, rejected: true }
            : { household_item_id: value ? Number(value) : null, rejected: false },
        ),
      });
      setNotice("Receipt match updated.");
      await loadHouseholdOps();
    });
  }

  async function trackReceiptItem() {
    if (!receiptItemDraft) return;
    await run(`receipt-new-item-${receiptItemDraft.lineId}`, async () => {
      await api(
        `/api/replenishment/receipts/${receiptItemDraft.receiptId}/items/${receiptItemDraft.lineId}/track`,
        {
          method: "POST",
          body: JSON.stringify({
            name: receiptItemDraft.name.trim(),
            cadence_days: Number(receiptItemDraft.cadence_days),
            replenishment_mode: receiptItemDraft.replenishment_mode,
          }),
        },
      );
      const name = receiptItemDraft.name.trim();
      setReceiptItemDraft(null);
      setNotice(`${name} is now tracked. Confirm the receipt to add this purchase to learning history.`);
      await loadHouseholdOps();
    });
  }

  async function undoReceiptAcquisition(receipt: PurchaseReceipt, acquisitionId: number) {
    if (!window.confirm("Undo this learned purchase? The receipt remains available for correction.")) return;
    await run(`undo-acquisition-${acquisitionId}`, async () => {
      await api(`/api/replenishment/acquisitions/${acquisitionId}/undo`, { method: "POST" });
      setNotice("Purchase history entry undone.");
      await loadHouseholdOps();
    });
  }

  async function receiptAction(receipt: PurchaseReceipt, action: "confirm" | "ignore") {
    await run(`receipt-${action}-${receipt.id}`, async () => {
      await api(`/api/replenishment/receipts/${receipt.id}/${action}`, { method: "POST" });
      setNotice(action === "confirm" ? "Receipt purchases added to learning history." : "Receipt ignored.");
      setReviewingReceiptId(null);
      await loadHouseholdOps();
    });
  }

  async function repeatErrand(errand: Errand) {
    await run(`repeat-errand-${errand.id}`, async () => {
      const created = await api<Errand>("/api/household/errands", {
        method: "POST",
        body: JSON.stringify({
          title: errand.title,
          errand_type: errand.errand_type,
          place_name: errand.resolved_place_name || errand.place_name,
          place_address: errand.resolved_place_address || errand.place_address,
          estimated_duration_minutes: errand.estimated_duration_minutes,
          priority: errand.priority,
          notes: errand.notes,
          included_in_next_plan: true,
        }),
      });
      if (errand.place_resolution_status === "resolved" && errand.resolved_place_name && errand.resolved_place_address) {
        await api<Errand>(`/api/household/errands/${created.id}/resolve-place`, {
          method: "POST",
          body: JSON.stringify({
            canonical_name: errand.resolved_place_name,
            full_address: errand.resolved_place_address,
            latitude: errand.resolved_latitude,
            longitude: errand.resolved_longitude,
            provider_place_id: errand.resolved_provider_place_id,
            open_now: errand.resolved_open_now,
            opening_hours: errand.resolved_opening_hours,
            remember_preference: false,
          }),
        });
      }
      setNotice(`${errand.title} was added back to your active errands.`);
      await loadHouseholdOps();
    });
  }

  async function runWeeklyLearning() {
    await run("weekly-learning", async () => {
      await api("/api/replenishment/weekly-run", {
        method: "POST",
        body: JSON.stringify({ run_key: `manual-${new Date().toISOString()}` }),
      });
      setNotice("Predictions refreshed. A model is promoted only when it beats the baseline.");
      await loadHouseholdOps();
    });
  }

  async function syncGmailReceipts() {
    await run("gmail-receipts", async () => {
      const result = await api<{ scanned: number; ingested: number; skipped: number }>(
        "/api/replenishment/gmail/sync",
        {
          method: "POST",
          body: JSON.stringify({ max_results: 25 }),
        },
      );
      setNotice(
        result.ingested
          ? `Gmail receipt sync found ${result.ingested} new receipt${result.ingested === 1 ? "" : "s"} from ${result.scanned} scanned messages.`
          : `Gmail receipt sync scanned ${result.scanned} messages. No new receipts were found.`,
      );
      await loadHouseholdOps();
    });
  }

  async function findUsefulErrands() {
    const origin = selectedLocation(originChoice, locations, currentLocation, manualOrigin);
    if (!origin) {
      setError("Choose a saved location, use your current location, or enter a starting address.");
      return;
    }
    await run("while-out", async () => {
      const result = await api<WhileOutPlan>("/api/household/errand-plans/while-out", {
        method: "POST",
        body: JSON.stringify({
          origin,
          primary_destination: primaryDestination.trim()
            ? { label: primaryDestination.trim(), address: primaryDestination.trim() }
            : null,
          final_destination: selectedLocation(finalChoice, locations, currentLocation, ""),
          available_minutes: availableMinutes ? Number(availableMinutes) : null,
          include_replenishment: true,
        }),
      });
      setWhileOut(result);
      setPlan(result.plan);
      setNotice(
        result.estimates_are_routed
          ? "Useful stops selected using measured routing estimates."
          : "Useful stops selected with deterministic fallback; no travel-time claim was made.",
      );
      const loadedErrands = await api<Errand[]>("/api/household/errands");
      setErrands(loadedErrands);
    });
  }

  async function searchErrandPlace(errand: Errand) {
    await run(`search-place-${errand.id}`, async () => {
      const result = await api<PlaceSearchResult>(
        `/api/household/errands/${errand.id}/place-search`,
        {
          method: "POST",
          body: JSON.stringify({
            query: errand.place_name || errand.title,
            latitude: currentLocation?.latitude,
            longitude: currentLocation?.longitude,
          }),
        },
      );
      setPlaceSearch(result);
      setManualPlace({
        canonical_name: errand.place_name || "",
        full_address: errand.place_address || "",
      });
    });
  }

  async function resolveErrandPlace(candidate: PlaceCandidate | ManualPlaceForm) {
    if (!placeSearch) return;
    await run(`resolve-place-${placeSearch.errand_id}`, async () => {
      await api<Errand>(`/api/household/errands/${placeSearch.errand_id}/resolve-place`, {
        method: "POST",
        body: JSON.stringify({
          ...candidate,
          remember_preference: rememberPlace,
        }),
      });
      setPlaceSearch(null);
      setManualPlace(emptyManualPlaceForm);
      setNotice("Concrete destination saved. This errand can now be routed.");
      await loadHouseholdOps();
    });
  }

  function requestCurrentLocation() {
    if (!navigator.geolocation) {
      setGeoStatus("This browser does not support geolocation. Use a saved or manual location.");
      return;
    }
    setGeoStatus("Requesting location permission…");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setCurrentLocation({
          label: "Current location",
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
        });
        setOriginChoice("current");
        setGeoStatus("Current location is ready for the next planning request.");
      },
      () => {
        setGeoStatus("Location permission was denied. Choose a saved or manual location instead.");
      },
      { enableHighAccuracy: false, timeout: 10_000, maximumAge: 300_000 },
    );
  }

  async function saveLocation() {
    await run("save-location", async () => {
      const payload = {
        label: locationForm.label.trim(),
        address: locationForm.address.trim(),
        location_type: locationForm.location_type,
      };
      if (editingLocationId) {
        await api<SavedLocation>(`/api/household/locations/${editingLocationId}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
      } else {
        await api<SavedLocation>("/api/household/locations", {
          method: "POST",
          body: JSON.stringify(payload),
        });
      }
      closeLocationForm();
      setNotice("Saved location updated.");
      await loadHouseholdOps();
    });
  }

  function editLocation(location: SavedLocation) {
    setEditingLocationId(location.id);
    setLocationForm({
      label: location.label,
      address: location.address,
      location_type: location.location_type,
    });
    setShowLocationForm(true);
  }

  async function deleteLocation(location: SavedLocation) {
    if (!window.confirm(`Delete saved location “${location.label}”?`)) return;
    await run(`delete-location-${location.id}`, async () => {
      await deleteRequest(`/api/household/locations/${location.id}`);
      if (originChoice === `saved:${location.id}`) setOriginChoice("manual");
      if (finalChoice === `saved:${location.id}`) setFinalChoice("");
      await loadHouseholdOps();
    });
  }

  function closeLocationForm() {
    setShowLocationForm(false);
    setEditingLocationId(null);
    setLocationForm(emptyLocationForm);
  }

  function applyShortcut(value: "home" | "work" | "costco" | "weekend") {
    if (value === "costco") {
      const costco = locations.find((item) => item.label.toLowerCase().includes("costco"));
      if (!costco) {
        setGeoStatus("Save a specific Costco branch or add and resolve a Costco errand first.");
        return;
      }
      setPrimaryDestination(costco.address);
      return;
    }
    if (value === "weekend") {
      setPrimaryDestination("");
      setFinalChoice("");
      setAvailableMinutes("");
      return;
    }
    const location = locations.find((item) => item.location_type === value);
    if (!location) {
      setGeoStatus(`Save a ${value} location first.`);
      return;
    }
    setPrimaryDestination("");
    setFinalChoice(`saved:${location.id}`);
  }

  function editErrand(errand: Errand) {
    setEditingErrandId(errand.id);
    setErrandForm({
      title: errand.title,
      errand_type: errand.errand_type,
      place_name: errand.place_name || "",
      place_address: errand.place_address || "",
      due_at: toLocalInput(errand.due_at),
      estimated_duration_minutes: errand.estimated_duration_minutes?.toString() || "",
      priority: errand.priority,
      notes: errand.notes || "",
      included_in_next_plan: errand.included_in_next_plan,
    });
    setShowErrandForm(true);
  }

  function editItem(item: HouseholdItem) {
    setEditingItemId(item.id);
    setItemForm({
      name: item.name,
      quantity: item.quantity || "",
      unit: item.unit || "",
      preferred_place_name: item.preferred_place_name || "",
      preferred_place_address: item.preferred_place_address || "",
      replenishment_mode: item.replenishment_mode,
      cadence_days: item.cadence_days.toString(),
      last_acquired_at: toLocalInput(item.last_acquired_at),
      notes: item.notes || "",
    });
    setShowItemForm(true);
  }

  function closeErrandForm() {
    setShowErrandForm(false);
    setEditingErrandId(null);
    setErrandForm(emptyErrandForm);
  }

  function closeItemForm() {
    setShowItemForm(false);
    setEditingItemId(null);
    setItemForm(emptyItemForm);
  }

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={<span className="inline-flex items-center gap-2"><House className="h-4 w-4" aria-hidden="true" />Household</span>}
        title="Household operations"
        description="See what needs attention, plan errands, and keep recurring household work moving."
        actions={<Button
            variant="secondary"
            className="bg-white/10 text-white ring-1 ring-white/15 hover:bg-white/15"
            onClick={loadHouseholdOps}
            disabled={busy !== null}
          >
            <RefreshCw className={`h-4 w-4 ${busy === "refresh" ? "animate-spin" : ""}`} />
            Refresh
          </Button>}
      />

      {error ? (
        <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
          <button className="ml-auto rounded p-0.5 hover:bg-rose-100" onClick={() => setError(null)} aria-label="Dismiss error">
            <X className="h-4 w-4" />
          </button>
        </div>
      ) : null}
      {notice ? (
        <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 shadow-sm">
          <CheckCircle2 className="h-4 w-4 text-emerald-600" />
          {notice}
        </div>
      ) : null}

      <HouseholdNavigation active={householdView} onChange={setHouseholdView} receiptCount={receiptQueue.length} />

      {householdView === "today" ? (
        <TodayOverview
          receiptCount={receiptQueue.length}
          unresolvedErrandCount={activeErrands.filter((errand) => errand.place_resolution_status !== "resolved").length}
          activeErrandCount={activeErrands.length}
          dueItemCount={dueItems.length}
          plan={plan}
          loading={busy === "initial"}
          onChange={setHouseholdView}
        />
      ) : null}

      {householdView === "receipts" ? <Card>
        <CardHeader className="flex-col items-start justify-between gap-4 space-y-0 sm:flex-row">
          <div>
            <div className="mb-1 inline-flex items-center gap-2 text-xs font-semibold uppercase text-slate-500">
              <Brain className="h-4 w-4" /> Learning from purchases
            </div>
            <CardTitle>Replenishment learning</CardTitle>
            <CardDescription>Receipt matches and your feedback improve estimates; every purchase remains correctable.</CardDescription>
          </div>
          <div className="flex w-full flex-wrap gap-2 sm:w-auto sm:justify-end">
            <Button variant="outline" onClick={syncGmailReceipts} disabled={busy !== null}>
              <ReceiptText className="h-4 w-4" /> {busy === "gmail-receipts" ? "Syncing Gmail…" : "Sync Gmail receipts"}
            </Button>
            <Button variant="outline" onClick={runWeeklyLearning} disabled={busy !== null}>
              <RefreshCw className={`h-4 w-4 ${busy === "weekly-learning" ? "animate-spin" : ""}`} /> Refresh predictions
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <GmailSyncStatus status={gmailSyncStatus} busy={busy === "gmail-receipts"} />
          {busy === "initial" && !learning ? <LoadingRows /> : (
            <>
              <section>
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-700">This week</h3>
                    <p className="text-xs text-slate-600">Likely needs based on confirmed purchase timing—not an inventory claim.</p>
                  </div>
                  <Badge variant="secondary">{learning?.this_week.length || 0} likely due</Badge>
                </div>
                {learning?.this_week.length ? (
                  <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                    {learning.this_week.map((prediction) => (
                      <div key={prediction.id} className="rounded-xl border border-slate-200 bg-slate-50/60 p-3">
                        <p className="font-semibold text-slate-950">{prediction.household_item_name}</p>
                        <p className="mt-1 text-xs text-slate-600">
                          {prediction.predicted_days_remaining <= 0 ? "Likely due now" : `Likely within ${Math.max(1, Math.ceil(prediction.predicted_days_remaining))} days`}
                        </p>
                        <p className="mt-1 text-xs leading-5 text-slate-600">{prediction.explanation}</p>
                        {prediction.evidence_label ? <p className="mt-2 text-xs font-medium text-amber-700">{prediction.evidence_label}</p> : null}
                      </div>
                    ))}
                  </div>
                ) : <EmptyPanel icon={PackageCheck} title="Nothing predicted for this week" description="Confirm purchases over time to build a useful pattern." compact />}
              </section>

              <section className="grid gap-3 sm:grid-cols-3">
                <Metric label="Confirmed purchases" value={learning?.learning.confirmed_acquisitions || 0} />
                <Metric label="Items with history" value={learning?.learning.items_with_history || 0} />
                <Metric label="Prediction engine" value={learning?.learning.active_model ? "Validated model" : "Adaptive baseline"} />
              </section>

              <section>
                <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="inline-flex items-center gap-2 text-sm font-semibold text-slate-900">
                      <ReceiptText className="h-4 w-4 text-slate-500" /> Receipts needing attention
                    </div>
                    <p className="mt-1 text-xs text-slate-600">Completed receipts leave this queue automatically.</p>
                  </div>
                  <Badge variant="secondary">{receiptQueue.length} to review</Badge>
                </div>
                {receiptQueue.length ? <div className="space-y-3">
                  <div className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200">
                    {receiptQueue.map((receipt) => <ReceiptQueueRow key={receipt.id} receipt={receipt} selected={reviewingReceiptId === receipt.id} busy={busy !== null} onReview={() => setReviewingReceiptId(receipt.id)} onIgnore={() => receiptAction(receipt, "ignore")} />)}
                  </div>
                  {receiptQueue.find((receipt) => receipt.id === reviewingReceiptId) ? <ReceiptReviewCard receipt={receiptQueue.find((receipt) => receipt.id === reviewingReceiptId)!} items={items} draft={receiptItemDraft} busy={busy !== null} onDraft={setReceiptItemDraft} onMatch={updateReceiptMatch} onTrack={trackReceiptItem} onUndo={undoReceiptAcquisition} onConfirm={(receipt) => receiptAction(receipt, "confirm")} onIgnore={(receipt) => receiptAction(receipt, "ignore")} onClose={() => setReviewingReceiptId(null)} /> : null}
                </div> : <EmptyPanel icon={CheckCircle2} title="Receipt queue is clear" description="New Gmail or Telegram receipts will appear here when they need a decision." compact />}

              </section>

              <details className="rounded-xl border border-slate-200 px-4 py-3">
                <summary className="flex min-h-11 cursor-pointer items-center text-sm font-semibold text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">Advanced prediction history & accuracy</summary>
                <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  <Metric label="Current method" value={learning?.accuracy.current_prediction_method?.replace(/_/g, " ") || "No prediction yet"} />
                  <Metric label="Training observations" value={learning?.accuracy.training_observations || 0} />
                  <Metric label="Validation observations" value={learning?.accuracy.validation_observations || 0} />
                  <Metric label="Evidence level" value={learning?.accuracy.confidence_level || "insufficient"} />
                  <Metric label="Validation method" value={learning?.accuracy.validation_method?.replace(/_/g, " ") || "Not eligible yet"} />
                  <Metric label="Strongest baseline MAE" value={learning?.accuracy.baseline_mae_days !== null && learning?.accuracy.baseline_mae_days !== undefined ? `${learning.accuracy.baseline_mae_days} days` : "Not evaluated"} />
                  <Metric label="Active model MAE" value={learning?.accuracy.active_model_mae_days !== null && learning?.accuracy.active_model_mae_days !== undefined ? `${learning.accuracy.active_model_mae_days} days` : "No active model"} />
                  <Metric label="Evaluated predictions" value={learning?.accuracy.evaluated_predictions || 0} />
                </div>
                <p className="mt-3 text-sm text-slate-600">{learning?.accuracy.evaluated_predictions ? `Historical prediction error: ${learning.accuracy.mae_days} mean absolute days.` : "Accuracy appears after a prediction is followed by the next valid confirmed purchase."}</p>
                {learning?.accuracy.confidence_level === "insufficient" || learning?.accuracy.confidence_level === "low" ? <p className="mt-2 text-sm font-medium text-amber-700">Early estimate — limited history.</p> : null}
                {learning?.accuracy.decision_reason ? <p className="mt-2 text-xs leading-5 text-slate-600">Latest model decision{learning.accuracy.latest_model_status ? ` (${learning.accuracy.latest_model_status})` : ""}: {learning.accuracy.decision_reason}</p> : null}
              </details>
            </>
          )}
        </CardContent>
      </Card> : null}

      {householdView === "errands" ? <WhileOutPanel
        locations={locations}
        originChoice={originChoice}
        manualOrigin={manualOrigin}
        primaryDestination={primaryDestination}
        finalChoice={finalChoice}
        availableMinutes={availableMinutes}
        currentLocationReady={currentLocation !== null}
        geoStatus={geoStatus}
        result={whileOut}
        busy={busy !== null}
        showLocationForm={showLocationForm}
        locationForm={locationForm}
        editingLocation={editingLocationId !== null}
        onOriginChoice={setOriginChoice}
        onManualOrigin={setManualOrigin}
        onPrimaryDestination={setPrimaryDestination}
        onFinalChoice={setFinalChoice}
        onAvailableMinutes={setAvailableMinutes}
        onCurrentLocation={requestCurrentLocation}
        onFind={findUsefulErrands}
        onShortcut={applyShortcut}
        onShowLocationForm={() => setShowLocationForm(true)}
        onLocationForm={setLocationForm}
        onSaveLocation={saveLocation}
        onCloseLocationForm={closeLocationForm}
        onEditLocation={editLocation}
        onDeleteLocation={deleteLocation}
      /> : null}

      {householdView === "errands" ? <div className="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)]">
        <Card>
          <CardHeader className="flex-row items-start justify-between gap-4 space-y-0">
            <div>
              <div className="mb-1 inline-flex items-center gap-2 text-xs font-semibold uppercase text-slate-500">
                <ListChecks className="h-4 w-4" /> Things to do
              </div>
              <CardTitle>Errand inbox</CardTitle>
              <CardDescription>{activeErrands.length} active errands ready to combine.</CardDescription>
            </div>
            <Button onClick={() => setShowErrandForm(true)}>
              <Plus className="h-4 w-4" /> Add errand
            </Button>
          </CardHeader>
          <CardContent className="space-y-4">
            {showErrandForm ? (
              <ErrandEditor
                form={errandForm}
                setForm={setErrandForm}
                editing={editingErrandId !== null}
                busy={busy !== null}
                onSave={saveErrand}
                onCancel={closeErrandForm}
              />
            ) : null}
            {placeSearch ? (
              <PlaceResolver
                result={placeSearch}
                manual={manualPlace}
                remember={rememberPlace}
                busy={busy !== null}
                onManual={setManualPlace}
                onRemember={setRememberPlace}
                onChoose={resolveErrandPlace}
                onClose={() => setPlaceSearch(null)}
              />
            ) : null}
            {busy === "initial" ? (
              <LoadingRows />
            ) : activeErrands.length ? (
              <div className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200">
                {activeErrands.map((errand) => (
                  <ErrandRow
                    key={errand.id}
                    errand={errand}
                    busy={busy !== null}
                    onEdit={() => editErrand(errand)}
                    onToggle={() => mutateErrand(errand, "toggle")}
                    onComplete={() => mutateErrand(errand, "complete")}
                    onSkip={() => mutateErrand(errand, "skip")}
                    onDelete={() => mutateErrand(errand, "delete")}
                    onChoosePlace={() => searchErrandPlace(errand)}
                  />
                ))}
              </div>
            ) : (
              <EmptyPanel icon={CheckCircle2} title="Errand inbox is clear" description="Add something you need to do while you're out." />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="mb-1 inline-flex items-center gap-2 text-xs font-semibold uppercase text-slate-500">
              <Route className="h-4 w-4" /> Next trip
            </div>
            <CardTitle>Your latest route</CardTitle>
            <CardDescription>The route builder above is now the single place to choose endpoints, available time, and useful stops.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {plan ? <PlanSummary plan={plan} /> : <EmptyPanel icon={MapPin} title="No trip planned yet" description="Include errands in the next trip, then create a grouped stop plan." compact />}
            <Button variant="outline" className="w-full" onClick={() => document.getElementById("trip-builder")?.scrollIntoView({ behavior: "smooth", block: "start" })}>
              <Route className="h-4 w-4" /> {plan ? "Change stops or re-optimize" : "Build a route"}
            </Button>
          </CardContent>
        </Card>
      </div> : null}

      {householdView === "staples" ? <Card>
        <CardHeader className="flex-row items-start justify-between gap-4 space-y-0">
          <div>
            <div className="mb-1 inline-flex items-center gap-2 text-xs font-semibold uppercase text-slate-500">
              <ShoppingBasket className="h-4 w-4" /> Probably running low
            </div>
            <CardTitle>Household replenishment</CardTitle>
            <CardDescription>Cadence-based estimates only—ExpenseOps does not claim to know your exact inventory.</CardDescription>
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" onClick={() => setShowItemForm(true)}>
              <Plus className="h-4 w-4" /> Add staple
            </Button>
            <Button onClick={refreshReplenishment} disabled={busy !== null}>
              <RefreshCw className={`h-4 w-4 ${busy === "replenishment" ? "animate-spin" : ""}`} /> Check what's due
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          {showItemForm ? (
            <ItemEditor form={itemForm} setForm={setItemForm} editing={editingItemId !== null} busy={busy !== null} onSave={saveItem} onCancel={closeItemForm} />
          ) : null}
          {suggestions.length ? (
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {suggestions.map((suggestion) => (
                <div key={suggestion.household_item_id} className="rounded-xl border border-indigo-100 bg-indigo-50/60 p-3 text-sm leading-5 text-slate-700">
                  <span className="font-semibold text-slate-900">{suggestion.household_item_name}</span>
                  <p className="mt-1 text-xs text-slate-600">{suggestion.message}</p>
                </div>
              ))}
            </div>
          ) : null}
          {dueItems.length ? (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {dueItems.map((item) => (
                <DueItemCard key={item.id} item={item} busy={busy !== null} onAction={(action) => mutateItem(item, action)} onEdit={() => editItem(item)} />
              ))}
            </div>
          ) : (
            <EmptyPanel icon={PackageCheck} title="No staples are currently surfaced" description="Add purchase history and cadence data, then refresh recommendations." compact />
          )}

          <div className="border-t border-slate-100 pt-5">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-900">Household staples</h3>
                <p className="text-xs text-slate-600">{enabledItems.length} enabled items tracked by cadence.</p>
              </div>
            </div>
            {items.length ? (
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {items.map((item) => (
                  <div key={item.id} className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50/60 p-3 transition hover:border-slate-300 hover:bg-white">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-semibold text-slate-900">{item.name}</span>
                        {!item.enabled ? <Badge variant="secondary">Disabled</Badge> : null}
                      </div>
                      <p className="mt-1 truncate text-xs text-slate-600">Every {item.cadence_days} days · {item.preferred_place_name || "No preferred store"}</p>
                    </div>
                    <div className="flex shrink-0 gap-1">
                      <IconButton label={`Edit ${item.name}`} onClick={() => editItem(item)}><Pencil className="h-4 w-4" /></IconButton>
                      <IconButton label={`Delete ${item.name}`} danger onClick={() => mutateItem(item, "delete")}><Trash2 className="h-4 w-4" /></IconButton>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-slate-600">No household staples configured yet.</p>
            )}
          </div>
        </CardContent>
      </Card> : null}

      {householdView === "history" ? (
        <HouseholdHistory
          receipts={receiptHistory}
          errands={errandHistory}
          items={items}
          draft={receiptItemDraft}
          expandedReceiptId={expandedHistoryReceiptId}
          busy={busy !== null}
          onToggleReceipt={(receiptId) => setExpandedHistoryReceiptId((current) => current === receiptId ? null : receiptId)}
          onDraft={setReceiptItemDraft}
          onMatch={updateReceiptMatch}
          onTrack={trackReceiptItem}
          onUndoReceipt={undoReceiptAcquisition}
          onRepeatErrand={repeatErrand}
          onDeleteErrand={(errand) => mutateErrand(errand, "delete")}
        />
      ) : null}
    </div>
  );
}

function HouseholdNavigation({ active, onChange, receiptCount }: { active: HouseholdView; onChange: (value: HouseholdView) => void; receiptCount: number }) {
  const views: Array<{ value: HouseholdView; label: string }> = [
    { value: "today", label: "Today" },
    { value: "errands", label: "Errands" },
    { value: "receipts", label: "Receipts" },
    { value: "staples", label: "Staples" },
    { value: "history", label: "History" },
  ];
  return <div className="relative overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
    <span className="pointer-events-none absolute inset-y-0 left-0 z-10 w-5 bg-gradient-to-r from-white to-transparent sm:hidden" aria-hidden="true" />
    <nav className="flex gap-1 overflow-x-auto p-1 px-4 sm:px-1" aria-label="Household Ops sections">{views.map((view) => <button key={view.value} type="button" onClick={() => onChange(view.value)} aria-current={active === view.value ? "page" : undefined} className={`min-h-11 whitespace-nowrap rounded-lg px-4 py-2 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${active === view.value ? "bg-indigo-600 text-white shadow-sm" : "text-slate-600 hover:bg-slate-50 hover:text-slate-950"}`}>{view.label}{view.value === "receipts" && receiptCount ? <span className={`ml-2 rounded-full px-1.5 py-0.5 text-xs ${active === view.value ? "bg-white/20 text-white" : "bg-amber-100 text-amber-800"}`}>{receiptCount}</span> : null}</button>)}</nav>
    <span className="pointer-events-none absolute inset-y-0 right-0 z-10 w-8 bg-gradient-to-l from-white to-transparent sm:hidden" aria-hidden="true" />
  </div>;
}

function TodayOverview({ receiptCount, unresolvedErrandCount, activeErrandCount, dueItemCount, plan, loading, onChange }: { receiptCount: number; unresolvedErrandCount: number; activeErrandCount: number; dueItemCount: number; plan: ErrandPlan | null; loading: boolean; onChange: (value: HouseholdView) => void }) {
  if (loading) return <section aria-label="Loading household overview" role="status" className="space-y-3"><span className="sr-only">Loading household overview</span><div className="ui-skeleton h-40 rounded-xl" /><div className="ui-skeleton h-28 rounded-xl" /></section>;

  const recommendation = receiptCount
    ? { icon: ReceiptText, eyebrow: "Learning needs a decision", title: `Review ${receiptCount} receipt${receiptCount === 1 ? "" : "s"}`, detail: "Confirm which purchases should teach replenishment timing.", action: "Review receipts", view: "receipts" as HouseholdView }
    : unresolvedErrandCount
      ? { icon: MapPin, eyebrow: "Route is blocked", title: `Choose locations for ${unresolvedErrandCount} errand${unresolvedErrandCount === 1 ? "" : "s"}`, detail: "Every stop needs a concrete destination before it can be routed.", action: "Resolve locations", view: "errands" as HouseholdView }
      : dueItemCount
        ? { icon: ShoppingBasket, eyebrow: "Likely needed soon", title: `Review ${dueItemCount} staple${dueItemCount === 1 ? "" : "s"}`, detail: "These are timing estimates based on confirmed purchase history, not inventory claims.", action: "Review staples", view: "staples" as HouseholdView }
        : activeErrandCount
          ? { icon: Route, eyebrow: plan ? "Next trip is ready" : "Errands can be combined", title: plan ? "Review your next route" : `Plan ${activeErrandCount} active errand${activeErrandCount === 1 ? "" : "s"}`, detail: plan ? "Check the latest stops before you leave." : "Choose an origin and endpoint to reduce separate trips.", action: plan ? "Open route details" : "Build a route", view: "errands" as HouseholdView }
          : null;

  if (!recommendation) {
    return <section className="rounded-xl border border-emerald-200 bg-emerald-50/70 px-5 py-6" aria-labelledby="household-all-clear"><div className="flex flex-col gap-4 sm:flex-row sm:items-center"><span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-white text-emerald-700 shadow-sm ring-1 ring-emerald-200"><CheckCircle2 className="h-5 w-5" /></span><div><h2 id="household-all-clear" className="text-lg font-semibold text-slate-950">Household queue is clear</h2><p className="mt-1 text-sm text-slate-700">No receipts, errands, or replenishment estimates need attention right now.</p></div></div></section>;
  }

  const Icon = recommendation.icon;
  return <section className="space-y-4" aria-labelledby="household-next-action">
    <div><h2 id="household-next-action" className="text-xl font-semibold text-slate-950">Recommended next action</h2><p className="mt-1 text-sm text-slate-600">One useful step, based on what is currently waiting.</p></div>
    <Card variant="command" className="overflow-hidden">
      <CardContent className="flex flex-col gap-5 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-4"><span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-white/10 text-indigo-100 ring-1 ring-white/15"><Icon className="h-5 w-5" /></span><div><p className="text-xs font-semibold uppercase tracking-[0.12em] text-indigo-200">{recommendation.eyebrow}</p><h3 className="mt-1 text-xl font-semibold text-white">{recommendation.title}</h3><p className="mt-1 max-w-2xl text-sm leading-6 text-slate-300">{recommendation.detail}</p></div></div>
        <Button className="shrink-0 bg-white text-indigo-950 hover:bg-indigo-50" onClick={() => onChange(recommendation.view)}>{recommendation.action}<ArrowRight className="h-4 w-4" /></Button>
      </CardContent>
    </Card>
    <div className="flex flex-wrap gap-2 text-xs text-slate-700" aria-label="Household queue summary">
      <QueuePill icon={ListChecks} value={activeErrandCount} label="active errands" onClick={() => onChange("errands")} />
      <QueuePill icon={ReceiptText} value={receiptCount} label="receipts to review" onClick={() => onChange("receipts")} />
      <QueuePill icon={ShoppingBasket} value={dueItemCount} label="staples surfaced" onClick={() => onChange("staples")} />
    </div>
    {plan ? <TodayRouteSummary plan={plan} onOpen={() => onChange("errands")} showAction={recommendation.view !== "errands"} /> : null}
  </section>;
}

function QueuePill({ icon: Icon, value, label, onClick }: { icon: typeof House; value: number; label: string; onClick: () => void }) {
  return <button type="button" onClick={onClick} className="inline-flex min-h-11 items-center gap-2 rounded-full border border-slate-200 bg-white px-3 font-medium shadow-sm hover:border-indigo-200 hover:bg-indigo-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"><Icon className="h-4 w-4 text-slate-500" /><strong className="tabular-nums text-slate-950">{value}</strong>{label}</button>;
}

function TodayRouteSummary({ plan, onOpen, showAction }: { plan: ErrandPlan; onOpen: () => void; showAction: boolean }) {
  const totalMinutes = plan.travel_duration_minutes !== null ? plan.travel_duration_minutes + plan.estimated_stop_minutes : null;
  return <Card variant="secondary"><CardContent className="space-y-4 p-4 sm:p-5"><div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Latest route</p><h3 className="mt-1 font-semibold text-slate-950">{plan.stop_count} {plan.stop_count === 1 ? "stop" : "stops"}{totalMinutes !== null ? ` · about ${totalMinutes} minutes` : ""}</h3></div>{showAction ? <Button variant="outline" size="sm" onClick={onOpen}>Open route details<ArrowRight className="h-4 w-4" /></Button> : null}</div><RouteSequence plan={plan} compact /></CardContent></Card>;
}

function GmailSyncStatus({ status, busy }: { status: GmailReceiptSyncStatus | null; busy: boolean }) {
  return <div className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-slate-50/70 p-4 sm:flex-row sm:items-center sm:justify-between"><div className="flex items-start gap-3"><span className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ring-4 ${status?.configured ? "bg-emerald-500 ring-emerald-100" : "bg-slate-400 ring-slate-200"}`} /><div><p className="text-sm font-semibold text-slate-900">{status?.configured ? "Automatic Gmail receipt import is active" : "Gmail receipt import is not configured"}</p><p className="mt-1 text-xs text-slate-600">{busy ? "Sync in progress…" : status?.last_successful_sync_at ? `Last successful sync ${formatRelativeTime(status.last_successful_sync_at)}.` : status?.configured ? "Waiting for the first successful scheduled sync." : "Add the Gmail credentials and enable receipt sync to import automatically."}</p></div></div>{status?.latest_receipt_at ? <p className="text-xs text-slate-600">Latest Gmail receipt {formatRelativeTime(status.latest_receipt_at)}</p> : null}</div>;
}

function HouseholdHistory({ receipts, errands, items, draft, expandedReceiptId, busy, onToggleReceipt, onDraft, onMatch, onTrack, onUndoReceipt, onRepeatErrand, onDeleteErrand }: { receipts: PurchaseReceipt[]; errands: Errand[]; items: HouseholdItem[]; draft: ReceiptItemDraft | null; expandedReceiptId: number | null; busy: boolean; onToggleReceipt: (receiptId: number) => void; onDraft: React.Dispatch<React.SetStateAction<ReceiptItemDraft | null>>; onMatch: (receipt: PurchaseReceipt, line: PurchaseReceipt["items"][number], value: string) => void; onTrack: () => void; onUndoReceipt: (receipt: PurchaseReceipt, acquisitionId: number) => void; onRepeatErrand: (errand: Errand) => void; onDeleteErrand: (errand: Errand) => void }) {
  return <div className="grid gap-5 xl:grid-cols-2"><Card><CardHeader><div className="mb-1 inline-flex items-center gap-2 text-xs font-semibold uppercase text-slate-500"><ReceiptText className="h-4 w-4" />Receipts</div><CardTitle>Receipt history</CardTitle><CardDescription>Completed and ignored receipts stay available without occupying the review queue.</CardDescription></CardHeader><CardContent>{receipts.length ? <div className="divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200 bg-white">{receipts.map((receipt) => <ReceiptHistoryRow key={receipt.id} receipt={receipt} expanded={expandedReceiptId === receipt.id} busy={busy} items={items} draft={draft} onToggle={() => onToggleReceipt(receipt.id)} onDraft={onDraft} onMatch={onMatch} onTrack={onTrack} onUndo={onUndoReceipt} />)}</div> : <EmptyPanel icon={ReceiptText} title="No completed receipts yet" description="Confirmed and ignored receipts will appear here." compact />}</CardContent></Card><Card><CardHeader><div className="mb-1 inline-flex items-center gap-2 text-xs font-semibold uppercase text-slate-500"><ListChecks className="h-4 w-4" />Errands</div><CardTitle>Errand history</CardTitle><CardDescription>Repeat a past errand without rebuilding its destination.</CardDescription></CardHeader><CardContent>{errands.length ? <div className="divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200 bg-white">{errands.map((errand) => <ErrandHistoryRow key={errand.id} errand={errand} busy={busy} onRepeat={() => onRepeatErrand(errand)} onDelete={() => onDeleteErrand(errand)} />)}</div> : <EmptyPanel icon={ListChecks} title="No completed errands yet" description="Completed and skipped errands will appear here." compact />}</CardContent></Card></div>;
}

function WhileOutPanel({
  locations,
  originChoice,
  manualOrigin,
  primaryDestination,
  finalChoice,
  availableMinutes,
  currentLocationReady,
  geoStatus,
  result,
  busy,
  showLocationForm,
  locationForm,
  editingLocation,
  onOriginChoice,
  onManualOrigin,
  onPrimaryDestination,
  onFinalChoice,
  onAvailableMinutes,
  onCurrentLocation,
  onFind,
  onShortcut,
  onShowLocationForm,
  onLocationForm,
  onSaveLocation,
  onCloseLocationForm,
  onEditLocation,
  onDeleteLocation,
}: {
  locations: SavedLocation[];
  originChoice: string;
  manualOrigin: string;
  primaryDestination: string;
  finalChoice: string;
  availableMinutes: string;
  currentLocationReady: boolean;
  geoStatus: string | null;
  result: WhileOutPlan | null;
  busy: boolean;
  showLocationForm: boolean;
  locationForm: LocationForm;
  editingLocation: boolean;
  onOriginChoice: (value: string) => void;
  onManualOrigin: (value: string) => void;
  onPrimaryDestination: (value: string) => void;
  onFinalChoice: (value: string) => void;
  onAvailableMinutes: (value: string) => void;
  onCurrentLocation: () => void;
  onFind: () => void;
  onShortcut: (value: "home" | "work" | "costco" | "weekend") => void;
  onShowLocationForm: () => void;
  onLocationForm: React.Dispatch<React.SetStateAction<LocationForm>>;
  onSaveLocation: () => void;
  onCloseLocationForm: () => void;
  onEditLocation: (location: SavedLocation) => void;
  onDeleteLocation: (location: SavedLocation) => void;
}) {
  return (
    <Card id="trip-builder" className="scroll-mt-4 overflow-hidden border-indigo-200">
      <CardHeader className="border-b border-indigo-100 bg-indigo-50/50">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="mb-1 inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.12em] text-indigo-700">
              <LocateFixed className="h-4 w-4" /> While I'm out
            </div>
            <CardTitle>Find useful life-admin stops</CardTitle>
            <CardDescription>
              ExpenseOps searches real places and compares complete routes from your start to endpoint.
            </CardDescription>
          </div>
          <Button variant="outline" onClick={onShowLocationForm}>
            <MapPin className="h-4 w-4" /> Manage Home & Work
          </Button>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => onShortcut("home")}>
            I'm going home
          </Button>
          <Button variant="outline" size="sm" onClick={() => onShortcut("work")}>
            I'm going to work
          </Button>
          <Button variant="outline" size="sm" onClick={() => onShortcut("costco")}>
            I'm going to Costco
          </Button>
          <Button variant="outline" size="sm" onClick={() => onShortcut("weekend")}>
            Plan weekend errands
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-5 pt-5">
        {showLocationForm ? (
          <div className="space-y-3 rounded-xl border border-slate-200 bg-slate-50/60 p-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold text-slate-900">
                {editingLocation ? "Edit saved location" : "Add saved location"}
              </h3>
              <IconButton label="Close location form" onClick={onCloseLocationForm}>
                <X className="h-4 w-4" />
              </IconButton>
            </div>
            <div className="grid gap-3 sm:grid-cols-[140px_minmax(0,0.7fr)_minmax(0,1.3fr)]">
              <Field label="Type">
                <select
                  className={controlClass}
                  value={locationForm.location_type}
                  onChange={(event) =>
                    onLocationForm((current) => ({
                      ...current,
                      location_type: event.target.value as SavedLocation["location_type"],
                    }))
                  }
                >
                  <option value="home">Home</option>
                  <option value="work">Work</option>
                  <option value="custom">Custom</option>
                </select>
              </Field>
              <Field label="Label">
                <Input
                  value={locationForm.label}
                  onChange={(event) =>
                    onLocationForm((current) => ({ ...current, label: event.target.value }))
                  }
                  placeholder="Home"
                />
              </Field>
              <Field label="Address">
                <Input
                  value={locationForm.address}
                  onChange={(event) =>
                    onLocationForm((current) => ({ ...current, address: event.target.value }))
                  }
                  placeholder="Stored privately in your database"
                />
              </Field>
            </div>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-xs text-slate-600">Only one Home and one Work profile are allowed.</p>
              <div className="flex gap-2">
                <Button variant="outline" onClick={onCloseLocationForm}>Cancel</Button>
                <Button
                  onClick={onSaveLocation}
                  disabled={busy || !locationForm.label.trim() || !locationForm.address.trim()}
                >
                  Save location
                </Button>
              </div>
            </div>
          </div>
        ) : null}

        {locations.length ? (
          <div className="flex flex-wrap gap-2">
            {locations.map((location) => (
              <div key={location.id} className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white pl-3 text-xs text-slate-700">
                <span className="font-semibold">{location.label}</span>
                <IconButton label={`Edit ${location.label}`} onClick={() => onEditLocation(location)}>
                  <Pencil className="h-3.5 w-3.5" />
                </IconButton>
                <IconButton label={`Delete ${location.label}`} danger onClick={() => onDeleteLocation(location)}>
                  <Trash2 className="h-3.5 w-3.5" />
                </IconButton>
              </div>
            ))}
          </div>
        ) : null}

        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <Field label="Starting from">
            <select className={controlClass} value={originChoice} onChange={(event) => onOriginChoice(event.target.value)}>
              <option value="manual">Manual address</option>
              {currentLocationReady ? <option value="current">Current location</option> : null}
              {locations.map((location) => <option key={location.id} value={`saved:${location.id}`}>{location.label}</option>)}
            </select>
          </Field>
          <Field label="Specific destination address (optional)">
            <Input value={primaryDestination} onChange={(event) => onPrimaryDestination(event.target.value)} placeholder="Full address, not a store name" />
          </Field>
          <Field label="Ending at (optional)">
            <select className={controlClass} value={finalChoice} onChange={(event) => onFinalChoice(event.target.value)}>
              <option value="">No fixed endpoint</option>
              {currentLocationReady ? <option value="current">Current location</option> : null}
              {locations.map((location) => <option key={location.id} value={`saved:${location.id}`}>{location.label}</option>)}
            </select>
          </Field>
          <Field label="Available minutes (optional)">
            <Input type="number" min="1" max="1440" value={availableMinutes} onChange={(event) => onAvailableMinutes(event.target.value)} placeholder="45" />
          </Field>
        </div>
        {originChoice === "manual" ? (
          <Field label="Starting address">
            <Input value={manualOrigin} onChange={(event) => onManualOrigin(event.target.value)} placeholder="Enter your starting address" />
          </Field>
        ) : null}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <Button variant="outline" onClick={onCurrentLocation}>
              <LocateFixed className="h-4 w-4" /> Use my current location
            </Button>
            {geoStatus ? <p className="mt-2 text-xs text-slate-600">{geoStatus}</p> : null}
          </div>
          <Button onClick={onFind} disabled={busy}>
            <Route className="h-4 w-4" /> {busy ? "Working…" : "Find useful errands"}
          </Button>
        </div>

        {result ? (
          <div className="grid gap-4 border-t border-slate-100 pt-5 xl:grid-cols-[minmax(0,1fr)_340px]">
            <PlanSummary plan={result.plan} />
            <div className="space-y-3">
              <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
                <h3 className="text-sm font-semibold text-slate-900">Why these stops</h3>
                <ul className="mt-2 space-y-2 text-xs leading-5 text-slate-600">
                  {result.recommendation_reasons.map((reason) => <li key={reason}>• {reason}</li>)}
                </ul>
              </div>
              {result.excluded.length ? (
                <details className="rounded-xl border border-slate-200 bg-white p-4">
                  <summary className="cursor-pointer text-sm font-semibold text-slate-900">Not added ({result.excluded.length})</summary>
                  <div className="mt-3 space-y-2">{result.excluded.map((item) => <div key={`${item.kind}-${item.id}`}><p className="text-xs font-semibold text-slate-800">{item.title}</p><p className="text-xs leading-5 text-slate-600">{item.reason}</p></div>)}</div>
                </details>
              ) : null}
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ErrandEditor({ form, setForm, editing, busy, onSave, onCancel }: { form: ErrandForm; setForm: React.Dispatch<React.SetStateAction<ErrandForm>>; editing: boolean; busy: boolean; onSave: () => void; onCancel: () => void }) {
  return (
    <div className="space-y-3 rounded-xl border border-indigo-200 bg-indigo-50/40 p-4">
      <div className="flex items-center justify-between"><h3 className="text-sm font-semibold text-slate-900">{editing ? "Edit errand" : "Add an errand"}</h3><IconButton label="Close errand form" onClick={onCancel}><X className="h-4 w-4" /></IconButton></div>
      <label className="grid gap-1.5 text-xs font-medium text-slate-700">What needs doing?<Input autoFocus value={form.title} onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))} placeholder="Return Amazon package" /></label>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Type"><select className={controlClass} value={form.errand_type} onChange={(event) => setForm((current) => ({ ...current, errand_type: event.target.value as Errand["errand_type"] }))}><option value="purchase">Purchase</option><option value="return">Return</option><option value="pickup">Pickup</option><option value="service">Service</option><option value="other">Other</option></select></Field>
        <Field label="Place"><Input value={form.place_name} onChange={(event) => setForm((current) => ({ ...current, place_name: event.target.value }))} placeholder="Costco" /></Field>
        <Field label="Due"><Input type="datetime-local" value={form.due_at} onChange={(event) => setForm((current) => ({ ...current, due_at: event.target.value }))} /></Field>
        <Field label="Priority"><select className={controlClass} value={form.priority} onChange={(event) => setForm((current) => ({ ...current, priority: event.target.value as Errand["priority"] }))}><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option></select></Field>
      </div>
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_170px]"><Field label="Address"><Input value={form.place_address} onChange={(event) => setForm((current) => ({ ...current, place_address: event.target.value }))} placeholder="Optional route address" /></Field><Field label="Stop minutes"><Input type="number" min="0" max="1440" value={form.estimated_duration_minutes} onChange={(event) => setForm((current) => ({ ...current, estimated_duration_minutes: event.target.value }))} placeholder="10" /></Field></div>
      <Field label="Notes"><Input value={form.notes} onChange={(event) => setForm((current) => ({ ...current, notes: event.target.value }))} placeholder="Optional details" /></Field>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><label className="inline-flex items-center gap-2 text-xs font-medium text-slate-700"><input type="checkbox" className="h-4 w-4 rounded border-slate-300 text-indigo-600" checked={form.included_in_next_plan} onChange={(event) => setForm((current) => ({ ...current, included_in_next_plan: event.target.checked }))} />Include in next trip</label><div className="flex gap-2"><Button variant="outline" onClick={onCancel}>Cancel</Button><Button onClick={onSave} disabled={busy || !form.title.trim()}>{editing ? "Save changes" : "Add errand"}</Button></div></div>
    </div>
  );
}

function ReceiptQueueRow({ receipt, selected, busy, onReview, onIgnore }: { receipt: PurchaseReceipt; selected: boolean; busy: boolean; onReview: () => void; onIgnore: () => void }) {
  const unresolved = receipt.items.filter((line) => !line.household_item_id && line.match_status !== "rejected").length;
  return (
    <div className={`flex flex-col gap-3 p-4 transition sm:flex-row sm:items-center sm:justify-between ${selected ? "bg-indigo-50/60" : "bg-white hover:bg-slate-50/70"}`}>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-semibold text-slate-950">{receipt.merchant || "Unknown merchant"}</p>
          <Badge className={receipt.parse_status === "failed" ? "bg-rose-50 text-rose-700" : "bg-amber-50 text-amber-700"}>{receipt.parse_status === "failed" ? "Processing failed" : "Needs review"}</Badge>
        </div>
        <p className="mt-1 text-xs text-slate-600">{receiptDate(receipt)} · {receipt.items.length} items{receipt.parse_status === "needs_review" ? ` · ${unresolved} still unmatched` : ""}{receiptTotal(receipt)}</p>
        {receipt.parse_status === "failed" ? <p className="mt-1 text-xs text-rose-700">The receipt could not be parsed. You can ignore it and upload a clearer copy.</p> : null}
      </div>
      <div className="flex shrink-0 gap-2">
        {receipt.parse_status === "needs_review" ? <Button size="sm" variant={selected ? "secondary" : "outline"} onClick={onReview} disabled={busy}>{selected ? "Reviewing" : "Review receipt"}</Button> : null}
        <Button size="sm" variant="ghost" onClick={onIgnore} disabled={busy}>Ignore</Button>
      </div>
    </div>
  );
}

function ReceiptReviewCard({ receipt, items, draft, busy, onDraft, onMatch, onTrack, onUndo, onConfirm, onIgnore, onClose }: { receipt: PurchaseReceipt; items: HouseholdItem[]; draft: ReceiptItemDraft | null; busy: boolean; onDraft: React.Dispatch<React.SetStateAction<ReceiptItemDraft | null>>; onMatch: (receipt: PurchaseReceipt, line: PurchaseReceipt["items"][number], value: string) => void; onTrack: () => void; onUndo: (receipt: PurchaseReceipt, acquisitionId: number) => void; onConfirm: (receipt: PurchaseReceipt) => void; onIgnore: (receipt: PurchaseReceipt) => void; onClose: () => void }) {
  const decided = receipt.items.filter((line) => line.household_item_id || line.match_status === "rejected").length;
  return (
    <div className="rounded-xl border border-indigo-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-indigo-100 bg-indigo-50/50 p-4">
        <div><p className="text-xs font-semibold uppercase tracking-wide text-indigo-700">Reviewing receipt</p><h3 className="mt-1 font-semibold text-slate-950">{receipt.merchant || "Unknown merchant"}</h3><p className="mt-1 text-xs text-slate-600">{decided} of {receipt.items.length} items classified{receiptTotal(receipt)}</p></div>
        <IconButton label="Close receipt review" onClick={onClose}><X className="h-4 w-4" /></IconButton>
      </div>
      <div className="p-4"><ReceiptItemsEditor receipt={receipt} items={items} draft={draft} busy={busy} onDraft={onDraft} onMatch={onMatch} onTrack={onTrack} onUndo={onUndo} /></div>
      <div className="sticky bottom-0 flex flex-wrap justify-end gap-2 border-t border-slate-200 bg-white/95 p-4 backdrop-blur">
        <Button variant="ghost" onClick={() => onIgnore(receipt)} disabled={busy}>Ignore receipt</Button>
        <Button onClick={() => onConfirm(receipt)} disabled={busy}><Check className="h-4 w-4" />Confirm current matches</Button>
      </div>
    </div>
  );
}

function ReceiptHistoryRow({ receipt, expanded, busy, items, draft, onToggle, onDraft, onMatch, onTrack, onUndo }: { receipt: PurchaseReceipt; expanded: boolean; busy: boolean; items: HouseholdItem[]; draft: ReceiptItemDraft | null; onToggle: () => void; onDraft: React.Dispatch<React.SetStateAction<ReceiptItemDraft | null>>; onMatch: (receipt: PurchaseReceipt, line: PurchaseReceipt["items"][number], value: string) => void; onTrack: () => void; onUndo: (receipt: PurchaseReceipt, acquisitionId: number) => void }) {
  return (
    <div>
      <button type="button" onClick={onToggle} aria-expanded={expanded} className="flex w-full items-center justify-between gap-4 p-3 text-left transition hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500">
        <span className="min-w-0"><span className="block truncate text-sm font-semibold text-slate-900">{receipt.merchant || "Unknown merchant"}</span><span className="mt-0.5 block text-xs text-slate-600">{receiptDate(receipt)} · {receipt.items.length} items{receiptTotal(receipt)}</span></span>
        <span className="flex shrink-0 items-center gap-2"><Badge variant="secondary">{receipt.parse_status}</Badge><span className="text-xs font-semibold text-indigo-700">{expanded ? "Hide" : "Details"}</span></span>
      </button>
      {expanded ? <div className="border-t border-slate-100 bg-slate-50/40 p-4"><p className="mb-3 text-xs text-slate-600">Matches remain correctable. Changes to confirmed purchases update the learning history.</p><ReceiptItemsEditor receipt={receipt} items={items} draft={draft} busy={busy} onDraft={onDraft} onMatch={onMatch} onTrack={onTrack} onUndo={onUndo} /></div> : null}
    </div>
  );
}

function ReceiptItemsEditor({ receipt, items, draft, busy, onDraft, onMatch, onTrack, onUndo }: { receipt: PurchaseReceipt; items: HouseholdItem[]; draft: ReceiptItemDraft | null; busy: boolean; onDraft: React.Dispatch<React.SetStateAction<ReceiptItemDraft | null>>; onMatch: (receipt: PurchaseReceipt, line: PurchaseReceipt["items"][number], value: string) => void; onTrack: () => void; onUndo: (receipt: PurchaseReceipt, acquisitionId: number) => void }) {
  if (!receipt.items.length) return <p className="text-sm text-slate-600">No line items were detected.</p>;
  return <div className="grid gap-3 sm:grid-cols-2">{receipt.items.map((line) => {
    const creatingThisItem = draft?.lineId === line.id;
    return <div key={line.id} className={`grid gap-1 rounded-lg ${creatingThisItem ? "border border-indigo-200 bg-indigo-50/50 p-3" : ""}`}>
      <label className="grid gap-1 text-xs font-medium text-slate-700"><span className="truncate">{line.raw_name}</span><select className={controlClass} value={creatingThisItem ? "create" : line.match_status === "rejected" ? "reject" : line.household_item_id?.toString() || ""} onChange={(event) => onMatch(receipt, line, event.target.value)} disabled={["ignored", "failed"].includes(receipt.parse_status) || busy} aria-label={`Match ${line.raw_name}`}><option value="">Unmatched — decide later</option>{items.map((item) => <option key={item.id} value={item.id}>Match to: {item.name}</option>)}<option value="create">＋ Track as a new household item…</option><option value="reject">Not a household item</option></select></label>
      {creatingThisItem && draft ? <div className="mt-2 grid gap-2"><p className="text-xs leading-5 text-slate-600">Give this staple a reusable name and starting cadence.</p><Input value={draft.name} onChange={(event) => onDraft((current) => current ? { ...current, name: event.target.value } : current)} placeholder="Household item name" /><div className="grid gap-2 sm:grid-cols-2"><label className="grid gap-1 text-xs font-medium text-slate-700">Starting cadence (days)<Input type="number" min="1" max="3650" value={draft.cadence_days} onChange={(event) => onDraft((current) => current ? { ...current, cadence_days: event.target.value } : current)} /></label><label className="grid gap-1 text-xs font-medium text-slate-700">How to replenish<select className={controlClass} value={draft.replenishment_mode} onChange={(event) => onDraft((current) => current ? { ...current, replenishment_mode: event.target.value as HouseholdItem["replenishment_mode"] } : current)}><option value="either">Errand or delivery</option><option value="errand">Errand</option><option value="delivery">Delivery</option></select></label></div><div className="flex justify-end gap-2"><Button size="sm" variant="outline" onClick={() => onDraft(null)}>Cancel</Button><Button size="sm" onClick={onTrack} disabled={!draft.name.trim() || !Number(draft.cadence_days) || busy}><Plus className="h-3.5 w-3.5" />Create & match</Button></div></div> : null}
      {line.acquisition_id ? <button type="button" className="justify-self-start rounded text-xs font-semibold text-rose-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={() => onUndo(receipt, line.acquisition_id!)}>Undo learned purchase</button> : null}
    </div>;
  })}</div>;
}

function ItemEditor({ form, setForm, editing, busy, onSave, onCancel }: { form: ItemForm; setForm: React.Dispatch<React.SetStateAction<ItemForm>>; editing: boolean; busy: boolean; onSave: () => void; onCancel: () => void }) {
  return (
    <div className="space-y-3 rounded-xl border border-indigo-200 bg-indigo-50/40 p-4">
      <div className="flex items-center justify-between"><h3 className="text-sm font-semibold text-slate-900">{editing ? "Edit household item" : "Add a household staple"}</h3><IconButton label="Close household item form" onClick={onCancel}><X className="h-4 w-4" /></IconButton></div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Field label="Item"><Input autoFocus value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} placeholder="Paper towels" /></Field><Field label="Quantity"><Input value={form.quantity} onChange={(event) => setForm((current) => ({ ...current, quantity: event.target.value }))} placeholder="1" /></Field><Field label="Unit"><Input value={form.unit} onChange={(event) => setForm((current) => ({ ...current, unit: event.target.value }))} placeholder="pack" /></Field><Field label="Cadence days"><Input type="number" min="1" max="3650" value={form.cadence_days} onChange={(event) => setForm((current) => ({ ...current, cadence_days: event.target.value }))} placeholder="35" /></Field></div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Field label="Preferred store"><Input value={form.preferred_place_name} onChange={(event) => setForm((current) => ({ ...current, preferred_place_name: event.target.value }))} placeholder="Costco" /></Field><Field label="Store address"><Input value={form.preferred_place_address} onChange={(event) => setForm((current) => ({ ...current, preferred_place_address: event.target.value }))} placeholder="Optional" /></Field><Field label="Mode"><select className={controlClass} value={form.replenishment_mode} onChange={(event) => setForm((current) => ({ ...current, replenishment_mode: event.target.value as HouseholdItem["replenishment_mode"] }))}><option value="errand">Errand</option><option value="delivery">Delivery</option><option value="either">Either</option></select></Field><Field label="Last bought"><Input type="datetime-local" value={form.last_acquired_at} onChange={(event) => setForm((current) => ({ ...current, last_acquired_at: event.target.value }))} /></Field></div>
      <Field label="Notes"><Input value={form.notes} onChange={(event) => setForm((current) => ({ ...current, notes: event.target.value }))} placeholder="Optional details" /></Field>
      <p className="text-xs leading-5 text-slate-600">Items without a last-bought date remain “unknown” and are not surfaced as due.</p>
      <div className="flex justify-end gap-2"><Button variant="outline" onClick={onCancel}>Cancel</Button><Button onClick={onSave} disabled={busy || !form.name.trim() || !Number(form.cadence_days)}>{editing ? "Save changes" : "Add staple"}</Button></div>
    </div>
  );
}

function PlaceResolver({ result, manual, remember, busy, onManual, onRemember, onChoose, onClose }: { result: PlaceSearchResult; manual: ManualPlaceForm; remember: boolean; busy: boolean; onManual: React.Dispatch<React.SetStateAction<ManualPlaceForm>>; onRemember: (value: boolean) => void; onChoose: (candidate: PlaceCandidate | ManualPlaceForm) => void; onClose: () => void }) {
  return (
    <div className="space-y-4 rounded-xl border border-indigo-200 bg-indigo-50/40 p-4">
      <div className="flex items-start justify-between gap-3">
        <div><h3 className="text-sm font-semibold text-slate-900">Choose a concrete destination</h3><p className="mt-1 text-xs text-slate-600">Search: {result.query}. Nothing is routed until you select a full address.</p></div>
        <IconButton label="Close place chooser" onClick={onClose}><X className="h-4 w-4" /></IconButton>
      </div>
      {result.candidates.length ? <div className="grid gap-2 sm:grid-cols-2">{result.candidates.map((candidate) => <button key={candidate.provider_place_id || candidate.full_address} type="button" onClick={() => onChoose(candidate)} disabled={busy || candidate.open_now === false} className="rounded-xl border border-slate-200 bg-white p-3 text-left transition hover:border-indigo-400 hover:bg-indigo-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"><span className="flex flex-wrap items-center gap-2 text-sm font-semibold text-slate-900">{candidate.canonical_name}{candidate.is_preferred ? <Badge variant="secondary">Preferred</Badge> : null}{candidate.open_now === true ? <Badge className="bg-emerald-50 text-emerald-700">Open now</Badge> : candidate.open_now === false ? <Badge variant="secondary">Closed</Badge> : null}</span><span className="mt-1 block text-xs leading-5 text-slate-600">{candidate.full_address}</span></button>)}</div> : null}
      {result.message ? <p className="text-xs leading-5 text-slate-600">{result.message}</p> : null}
      <details className="border-t border-indigo-100 pt-3">
        <summary className="cursor-pointer text-xs font-semibold text-slate-700">Provider could not find it? Enter a destination manually</summary>
        <div className="mt-3 space-y-3">
        <div className="grid gap-3 sm:grid-cols-2"><Field label="Business or place name"><Input value={manual.canonical_name} onChange={(event) => onManual((current) => ({ ...current, canonical_name: event.target.value }))} placeholder="Specific salon name" /></Field><Field label="Full street address"><Input value={manual.full_address} onChange={(event) => onManual((current) => ({ ...current, full_address: event.target.value }))} placeholder="123 Main St, City, State ZIP" /></Field></div>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><label className="inline-flex items-center gap-2 text-xs font-medium text-slate-700"><input type="checkbox" checked={remember} onChange={(event) => onRemember(event.target.checked)} className="h-4 w-4 rounded border-slate-300 text-indigo-600" />Remember as my preferred place</label><Button size="sm" onClick={() => onChoose(manual)} disabled={busy || !manual.canonical_name.trim() || !manual.full_address.trim()}>Use this destination</Button></div>
        </div>
      </details>
    </div>
  );
}

function ErrandRow({ errand, busy, onEdit, onToggle, onComplete, onSkip, onDelete, onChoosePlace }: { errand: Errand; busy: boolean; onEdit: () => void; onToggle: () => void; onComplete: () => void; onSkip: () => void; onDelete: () => void; onChoosePlace: () => void }) {
  return (
    <div className="group bg-white p-4 transition hover:bg-slate-50/70">
      <div className="flex items-start gap-3">
        <button onClick={onComplete} disabled={busy} className="mt-0.5 flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-slate-300 text-slate-500 transition hover:border-indigo-500 hover:bg-indigo-50 hover:text-indigo-700 sm:h-9 sm:w-9" aria-label={`Complete ${errand.title}`}><Check className="h-4 w-4" /></button>
        <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-slate-950">{errand.title}</h3>{errand.priority === "high" ? <Badge className="bg-rose-50 text-rose-700">High</Badge> : null}<Badge className={errand.included_in_next_plan ? "bg-indigo-50 text-indigo-700" : "bg-slate-100 text-slate-600"}>{errand.included_in_next_plan ? "In next trip" : "Not in trip"}</Badge><Badge className={errand.place_resolution_status === "resolved" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}>{errand.place_resolution_status === "resolved" ? "Location ready" : "Needs location"}</Badge>{errand.place_resolution_method === "automatic" ? <Badge variant="secondary">Auto-selected</Badge> : null}{errand.resolved_open_now === true ? <Badge className="bg-emerald-50 text-emerald-700">Open now</Badge> : null}</div><div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-600">{errand.place_resolution_status === "resolved" ? <span className="inline-flex items-center gap-1"><MapPin className="h-3.5 w-3.5" />{errand.resolved_place_name} · {errand.resolved_place_address}</span> : errand.place_name ? <span>Looking for: {errand.place_name}</span> : null}{errand.due_at ? <span className="inline-flex items-center gap-1"><CalendarClock className="h-3.5 w-3.5" />{dueLabel(errand.due_at)}</span> : null}{errand.estimated_duration_minutes !== null ? <span className="inline-flex items-center gap-1"><Clock3 className="h-3.5 w-3.5" />{errand.estimated_duration_minutes} min</span> : null}</div>{errand.place_resolution_status !== "resolved" ? <Button variant="outline" size="sm" className="mt-2" onClick={onChoosePlace} disabled={busy}><MapPin className="h-3.5 w-3.5" />Choose location</Button> : <Button variant="ghost" size="sm" className="mt-2" onClick={onChoosePlace} disabled={busy}>Change location</Button>}{errand.linked_household_items.length ? <p className="mt-2 text-xs text-indigo-700">Includes {errand.linked_household_items.map((item) => item.name).join(", ")}</p> : null}</div>
        <div className="flex shrink-0 flex-wrap justify-end gap-1"><IconButton label={errand.included_in_next_plan ? "Exclude from next trip" : "Include in next trip"} onClick={onToggle}>{errand.included_in_next_plan ? <CircleOff className="h-4 w-4" /> : <Plus className="h-4 w-4" />}</IconButton><IconButton label={`Edit ${errand.title}`} onClick={onEdit}><Pencil className="h-4 w-4" /></IconButton><IconButton label={`Skip ${errand.title}`} onClick={onSkip}><X className="h-4 w-4" /></IconButton><IconButton label={`Delete ${errand.title}`} danger onClick={onDelete}><Trash2 className="h-4 w-4" /></IconButton></div>
      </div>
    </div>
  );
}

function ErrandHistoryRow({ errand, busy, onRepeat, onDelete }: { errand: Errand; busy: boolean; onRepeat: () => void; onDelete: () => void }) {
  const destination = errand.resolved_place_name || errand.place_name;
  return <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
    <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><p className="truncate text-sm font-semibold text-slate-900">{errand.title}</p><Badge variant="secondary">{errand.status}</Badge></div><p className="mt-1 truncate text-xs text-slate-600">{new Date(errand.updated_at).toLocaleDateString()}{destination ? ` · ${destination}` : ""}</p></div>
    <div className="flex shrink-0 gap-1"><Button size="sm" variant="outline" onClick={onRepeat} disabled={busy}><RotateCcw className="h-3.5 w-3.5" />Do again</Button><IconButton label={`Delete ${errand.title} from history`} danger onClick={onDelete}><Trash2 className="h-4 w-4" /></IconButton></div>
  </div>;
}

function DueItemCard({ item, busy, onAction, onEdit }: { item: HouseholdItem; busy: boolean; onAction: (action: "bought" | "still-have" | "skip-once" | "disable" | "add") => void; onEdit: () => void }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3"><div><Badge className={item.due_state === "likely_due" ? "bg-rose-50 text-rose-700" : "bg-amber-50 text-amber-700"}>{item.due_state === "likely_due" ? "Likely due" : "Probably due"}</Badge><h3 className="mt-2 font-semibold text-slate-950">{item.name}</h3><p className="mt-1 text-xs text-slate-600">{item.quantity || ""} {item.unit || ""}{item.preferred_place_name ? ` · ${item.preferred_place_name}` : ""}</p></div><IconButton label={`Edit ${item.name}`} onClick={onEdit}><Pencil className="h-4 w-4" /></IconButton></div>
      <div className="mt-4 flex flex-wrap gap-2"><Button size="sm" onClick={() => onAction("add")} disabled={busy || item.linked_errand_id !== null}><Plus className="h-3.5 w-3.5" />{item.linked_errand_id ? "In next trip" : item.replenishment_mode === "delivery" ? "Add errand" : `Add${item.preferred_place_name ? ` to ${item.preferred_place_name}` : ""}`}</Button><Button variant="outline" size="sm" onClick={() => onAction("bought")} disabled={busy}>Bought it</Button><Button variant="outline" size="sm" onClick={() => onAction("still-have")} disabled={busy}>Still have it</Button><Button variant="ghost" size="sm" onClick={() => onAction("skip-once")} disabled={busy}>Skip once</Button><Button variant="ghost" size="sm" onClick={() => onAction("disable")} disabled={busy}>Disable</Button></div>
    </div>
  );
}

function PlanSummary({ plan }: { plan: ErrandPlan }) {
  const totalMinutes = plan.travel_duration_minutes !== null
    ? plan.travel_duration_minutes + plan.estimated_stop_minutes
    : null;
  const distanceMiles = plan.distance_meters !== null
    ? (plan.distance_meters / 1609.344).toFixed(1)
    : null;
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50/60">
      <div className="flex items-start justify-between gap-3 border-b border-slate-200 px-4 py-3"><div><h3 className="font-semibold text-slate-950">Next trip</h3><p className="mt-0.5 text-xs text-slate-600">{plan.stop_count} {plan.stop_count === 1 ? "stop" : "stops"}{plan.travel_duration_minutes !== null ? ` · Travel ${plan.travel_duration_minutes} min` : ""}{distanceMiles !== null ? ` · ${distanceMiles} mi` : ""}{plan.estimated_stop_minutes ? ` · Stops ${plan.estimated_stop_minutes} min` : ""}{totalMinutes !== null ? ` · Total ${totalMinutes} min` : ""}</p>{plan.incremental_travel_duration_minutes !== null ? <p className="mt-1 text-xs font-medium text-indigo-700">Approximately +{plan.incremental_travel_duration_minutes} routed minutes versus the baseline.</p> : null}</div><Badge variant="secondary">{plan.routing_is_optimized ? "Route optimized" : plan.travel_duration_minutes !== null ? "Measured route" : "Routing unavailable"}</Badge></div>
      <div className="border-b border-slate-200 bg-white px-4 py-4"><RouteSequence plan={plan} /></div>
      <ol className="divide-y divide-slate-200">{plan.stops.map((stop) => <li key={stop.id} className="flex gap-3 px-4 py-3"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-slate-900 text-xs font-semibold text-white">{stop.stop_order}</span><div className="min-w-0"><p className="text-sm font-semibold text-slate-900">{stop.place_name}</p>{stop.place_address ? <p className="truncate text-xs text-slate-600">{stop.place_address}</p> : null}<ul className="mt-1 space-y-0.5 text-xs text-slate-600">{stop.errands.map((errand) => <li key={`errand-${errand.id}`}>• {errand.title}</li>)}{stop.household_items.map((item) => <li key={`item-${item.id}`} className="text-indigo-700">• Buy {item.quantity ? `${item.quantity} ` : ""}{item.unit ? `${item.unit} ` : ""}{item.name}</li>)}</ul></div></li>)}</ol>
      <div className="p-3">{plan.route_url ? <Button asChild className="w-full"><a href={plan.route_url} target="_blank" rel="noreferrer"><ExternalLink className="h-4 w-4" />Start route</a></Button> : <p className="text-center text-xs text-slate-600">Add a place to at least one errand to start a route.</p>}</div>
    </div>
  );
}

function RouteSequence({ plan, compact = false }: { plan: ErrandPlan; compact?: boolean }) {
  const finalStop = plan.stops.at(-1);
  const hasSeparateEnd = Boolean(plan.final_destination);
  const nodes = [
    { key: "start", kind: "Start", name: plan.base_location || "Starting point", address: null as string | null },
    ...plan.stops.map((stop, index) => ({
      key: `stop-${stop.id}`,
      kind: !hasSeparateEnd && index === plan.stops.length - 1 ? "End" : `Stop ${index + 1}`,
      name: stop.place_name,
      address: stop.place_address,
    })),
    ...(hasSeparateEnd ? [{ key: "end", kind: "End", name: plan.final_destination || "Final destination", address: null as string | null }] : []),
  ];
  if (!plan.stops.length && !plan.final_destination) return <p className="text-sm text-slate-600">No concrete route stops yet.</p>;

  return <ol className={`flex flex-col ${compact ? "gap-1" : "gap-2"} sm:flex-row sm:items-stretch`} aria-label="Route sequence">
    {nodes.map((node, index) => <li key={node.key} className="flex min-w-0 flex-1 flex-col sm:flex-row sm:items-center">
      <div className={`min-w-0 flex-1 rounded-lg border px-3 ${compact ? "py-2" : "py-3"} ${node.kind === "Start" || node.kind === "End" ? "border-indigo-200 bg-indigo-50" : "border-slate-200 bg-white"}`}>
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-600">{node.kind}</p>
        <p className="mt-0.5 truncate text-xs font-semibold text-slate-900">{shortLocation(node.name)}</p>
        {!compact && node.address ? <p className="mt-0.5 truncate text-[11px] text-slate-500">{node.address}</p> : null}
      </div>
      {index < nodes.length - 1 ? <ArrowRight className="mx-auto my-1 h-4 w-4 shrink-0 rotate-90 text-slate-400 sm:mx-2 sm:my-0 sm:rotate-0" aria-hidden="true" /> : null}
    </li>)}
    {!hasSeparateEnd && finalStop ? <li className="sr-only">Route ends at {finalStop.place_name}</li> : null}
  </ol>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="grid gap-1.5 text-xs font-medium text-slate-700">{label}{children}</label>; }

function IconButton({ label, children, onClick, danger = false }: { label: string; children: React.ReactNode; onClick: () => void; danger?: boolean }) { return <button type="button" onClick={onClick} aria-label={label} title={label} className={`inline-flex h-11 w-11 items-center justify-center rounded-lg transition focus-visible:ring-2 focus-visible:ring-indigo-500/40 sm:h-10 sm:w-10 ${danger ? "text-rose-600 hover:bg-rose-50" : "text-slate-500 hover:bg-slate-100 hover:text-slate-900"}`}>{children}</button>; }

function EmptyPanel({ icon: Icon, title, description, compact = false }: { icon: typeof House; title: string; description: string; compact?: boolean }) { return <div className={`flex flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-slate-50/50 px-5 text-center ${compact ? "min-h-32 py-5" : "min-h-44 py-8"}`}><span className="flex h-10 w-10 items-center justify-center rounded-xl bg-white text-slate-500 shadow-sm ring-1 ring-slate-200"><Icon className="h-5 w-5" /></span><h3 className="mt-3 text-sm font-semibold text-slate-900">{title}</h3><p className="mt-1 max-w-sm text-xs leading-5 text-slate-600">{description}</p></div>; }

function LoadingRows() { return <div className="space-y-2" role="status" aria-label="Loading Household Ops"><div className="ui-skeleton h-20" /><div className="ui-skeleton h-20" /><div className="ui-skeleton h-20" /><span className="sr-only">Loading</span></div>; }

function Metric({ label, value }: { label: string; value: string | number }) { return <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-3"><p className="text-xs font-medium text-slate-600">{label}</p><p className="mt-1 text-lg font-semibold text-slate-950">{value}</p></div>; }

function nullable(value: string) { return value.trim() || null; }
function toIso(value: string) { return value ? new Date(value).toISOString() : null; }
function toLocalInput(value: string | null) { if (!value) return ""; const date = new Date(value); const offset = date.getTimezoneOffset() * 60_000; return new Date(date.getTime() - offset).toISOString().slice(0, 16); }
function dueLabel(value: string) { const due = new Date(value); const today = new Date(); const days = Math.ceil((due.getTime() - today.getTime()) / 86_400_000); if (days < 0) return "Overdue"; if (days === 0) return "Due today"; if (days === 1) return "Due tomorrow"; return `Due ${due.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`; }
function receiptDate(receipt: PurchaseReceipt) { return new Date(receipt.purchased_at || receipt.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }); }
function receiptTotal(receipt: PurchaseReceipt) { return receipt.total_cents === null ? "" : ` · ${new Intl.NumberFormat(undefined, { style: "currency", currency: receipt.currency || "USD" }).format(receipt.total_cents / 100)}`; }
function shortLocation(value: string) { const trimmed = value.trim(); if (!trimmed) return "Location"; try { const parsed = JSON.parse(trimmed) as { label?: string; address?: string }; return parsed.label || parsed.address || trimmed; } catch { return trimmed.split(",")[0] || trimmed; } }
function formatRelativeTime(value: string) { const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000)); if (seconds < 60) return "just now"; if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`; if (seconds < 86400) return `${Math.floor(seconds / 3600)} hr ago`; return new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" }); }
function selectedLocation(choice: string, locations: SavedLocation[], current: PlanningLocation | null, manual: string): PlanningLocation | null { if (choice === "current") return current; if (choice.startsWith("saved:")) { const location = locations.find((item) => item.id === Number(choice.slice(6))); return location ? { saved_location_id: location.id } : null; } return manual.trim() ? { label: "Starting point", address: manual.trim() } : null; }
async function deleteRequest(path: string) { const response = await fetch(path, { method: "DELETE" }); if (!response.ok) { const data = await response.json(); throw data; } }
function errorMessage(error: unknown) { if (typeof error === "string") return error; if (error && typeof error === "object" && "detail" in error && typeof (error as { detail?: unknown }).detail === "string") return (error as { detail: string }).detail; return "Household Ops could not complete that action."; }

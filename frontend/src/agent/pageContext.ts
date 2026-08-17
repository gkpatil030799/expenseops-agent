import {
  AGENT_SCHEMA_VERSION,
  type AgentEntityKind,
  type AgentNavigationBlock,
  type AgentPageContext,
  type AgentPageEntity,
  type AgentPageFilters,
  type AgentSurface,
} from "./contracts";

/** UI-only metadata. `label` is never part of the Agent request payload. */
export type AgentContextDescriptor = {
  pageContext: AgentPageContext | null;
  label: string;
};

export type AgentContextPublisher = (descriptor: AgentContextDescriptor) => void;
export type AgentNavigationRequest = Pick<AgentNavigationBlock, "target_surface"> & {
  entity?: AgentPageEntity | null;
};
export const MAX_AGENT_PAGE_CONTEXT_BYTES = 1_536;

export type AgentEntitySelection = {
  publicId: string | number;
  label?: string | null;
};

export type AgentHouseholdView = "today" | "errands" | "receipts" | "staples" | "history";
export type AgentSettingsSection =
  | "account"
  | "workspace"
  | "personal"
  | "workspace-connections"
  | "expense"
  | "splitwise"
  | "learning"
  | "privacy";

export const AGENT_SURFACES = [
  "home",
  "expense_review",
  "expense_insights",
  "expense_activity",
  "household_today",
  "household_errands",
  "household_receipts",
  "household_staples",
  "household_history",
  "deals",
  "settings",
  "integrations",
] as const satisfies readonly AgentSurface[];

export const AGENT_ENTITY_KINDS = [
  "transaction",
  "deal",
  "receipt",
  "errand",
  "household_item",
  "integration",
] as const satisfies readonly AgentEntityKind[];

export const AGENT_INTEGRATION_IDS = [
  "plaid",
  "gmail",
  "splitwise",
  "telegram",
  "google_maps",
  "openai",
] as const;

const ENTITY_SURFACES: Record<AgentEntityKind, readonly AgentSurface[]> = {
  transaction: ["expense_review", "expense_insights", "expense_activity"],
  deal: ["deals"],
  receipt: ["household_today", "household_receipts"],
  errand: ["household_today", "household_errands"],
  household_item: ["household_today", "household_staples", "household_history"],
  integration: ["settings", "integrations"],
};

const HOUSEHOLD_SURFACES: Record<AgentHouseholdView, AgentSurface> = {
  today: "household_today",
  errands: "household_errands",
  receipts: "household_receipts",
  staples: "household_staples",
  history: "household_history",
};

const HOUSEHOLD_LABELS: Record<AgentHouseholdView, string> = {
  today: "Household Today",
  errands: "Household Errands",
  receipts: "Receipt Review",
  staples: "Household Staples",
  history: "Household History",
};

const DATE_PRESET_LABELS: Record<string, string> = {
  "7d": "Last 7 Days",
  "30d": "Last 30 Days",
  "90d": "Last 90 Days",
  ytd: "Year to Date",
  this_month: "This Month",
  last_month: "Last Month",
  this_quarter: "This Quarter",
  last_quarter: "Last Quarter",
  custom: "Custom Range",
};

const FILTER_KEYS: Array<keyof AgentPageFilters> = [
  "start_date",
  "end_date",
  "date_preset",
  "account_id",
  "category",
  "merchant",
  "status",
  "currency_code",
  "spend_basis",
  "query",
];

export function buildExpenseReviewContext({
  merchant,
  startDate,
  endDate,
  status,
  transaction,
}: {
  merchant?: string | null;
  startDate?: string | null;
  endDate?: string | null;
  status?: string | null;
  transaction?: AgentEntitySelection | null;
} = {}): AgentContextDescriptor {
  const filters = safeFilters({
    merchant: safeText(merchant, 255),
    status: safeText(status, 64),
    ...safeDateRange(startDate, endDate),
  });
  const entity = numericEntity("transaction", transaction);
  return descriptor(
    pageContext("expense_review", filters, entity),
    label(["Expense Review", entity ? transaction?.label : null, !entity ? merchant : null]),
  );
}

export function buildExpenseActivityContext(
  transaction?: AgentEntitySelection | null,
): AgentContextDescriptor {
  const entity = numericEntity("transaction", transaction);
  return descriptor(
    pageContext("expense_activity", undefined, entity),
    label(["Expense Activity", entity ? transaction?.label : null]),
  );
}

export function buildExpenseInsightsContext({
  startDate,
  endDate,
  datePreset,
  accountId,
  category,
  merchant,
  reviewType,
  currencyCode,
  spendBasis,
}: {
  startDate?: string | null;
  endDate?: string | null;
  datePreset?: string | null;
  accountId?: string | null;
  category?: string | null;
  merchant?: string | null;
  reviewType?: string | null;
  currencyCode?: string | null;
  spendBasis?: "card" | "actual_share" | null;
}): AgentContextDescriptor {
  const filters = safeFilters({
    ...safeDateRange(startDate, endDate),
    date_preset: safeText(datePreset, 32),
    account_id: safeText(accountId, 128),
    category: safeText(category, 100),
    merchant: safeText(merchant, 255),
    status: reviewType && reviewType !== "all" ? safeText(reviewType, 64) : undefined,
    currency_code: safeCurrency(currencyCode),
    spend_basis: spendBasis === "card" || spendBasis === "actual_share" ? spendBasis : undefined,
  });
  return descriptor(
    pageContext("expense_insights", filters),
    label([
      "Insights",
      category,
      datePreset ? DATE_PRESET_LABELS[datePreset] || datePreset : null,
      merchant ? `Merchant: ${merchant}` : null,
    ]),
  );
}

export function buildDealsContext({
  category,
  query,
  view,
  deal,
}: {
  category?: string | null;
  query?: string | null;
  view?: "recommended" | "saved" | "expiring" | "all" | null;
  deal?: AgentEntitySelection | null;
} = {}): AgentContextDescriptor {
  const filters = safeFilters({
    category: safeText(category, 100),
    query: safeText(query, 200),
    status: view && view !== "recommended" && view !== "all" ? view : undefined,
  });
  const entity = numericEntity("deal", deal);
  return descriptor(
    pageContext("deals", filters, entity),
    label(["Deals", entity ? deal?.label : null, !entity ? category : null]),
  );
}

export function buildHouseholdContext({
  view,
  entity,
}: {
  view: AgentHouseholdView;
  entity?: ({ kind: "receipt" | "errand" | "household_item" } & AgentEntitySelection) | null;
}): AgentContextDescriptor {
  const surface = HOUSEHOLD_SURFACES[view];
  const resolvedEntity = entity ? numericEntity(entity.kind, entity) : undefined;
  const compatibleEntity = resolvedEntity && isAgentEntityCompatible(surface, resolvedEntity)
    ? resolvedEntity
    : undefined;
  return descriptor(
    pageContext(surface, undefined, compatibleEntity),
    label([HOUSEHOLD_LABELS[view], compatibleEntity ? entity?.label : null]),
  );
}

export function buildSettingsContext({
  section,
  integration,
}: {
  section: AgentSettingsSection;
  integration?: AgentEntitySelection | null;
}): AgentContextDescriptor {
  const integrationId = normalizeIntegrationId(integration?.publicId);
  const entity: AgentPageEntity | undefined = integrationId
    ? { kind: "integration", public_id: integrationId }
    : undefined;
  const isIntegrationSection = section === "personal" || section === "workspace-connections";
  const surface: AgentSurface = isIntegrationSection ? "integrations" : "settings";
  const sectionLabel = section === "workspace-connections"
    ? "Workspace Connections"
    : section === "personal"
      ? "Personal Connections"
      : titleCase(section);
  return descriptor(
    pageContext(surface, undefined, entity),
    label(["Settings", sectionLabel, entity ? integration?.label : null]),
  );
}

export function baseAgentContext(surface: AgentSurface): AgentContextDescriptor {
  const labels: Record<AgentSurface, string> = {
    home: "Home",
    expense_review: "Expense Review",
    expense_insights: "Insights",
    expense_activity: "Expense Activity",
    household_today: "Household Today",
    household_errands: "Household Errands",
    household_receipts: "Receipt Review",
    household_staples: "Household Staples",
    household_history: "Household History",
    deals: "Deals",
    settings: "Settings",
    integrations: "Integrations",
  };
  return descriptor(pageContext(surface), labels[surface]);
}

/** Stable semantic key used only to scope the user's clear-context choice. */
export function agentPageContextKey(context: AgentPageContext | null): string {
  if (context === null) return "none";
  const filters: AgentPageFilters = {};
  for (const key of FILTER_KEYS) {
    const value = context.filters?.[key];
    if (value !== null && value !== undefined && value !== "") {
      (filters as Record<string, unknown>)[key] = value;
    }
  }
  return JSON.stringify({
    schema_version: context.schema_version,
    surface: context.surface,
    filters,
    entity: context.entity || null,
  });
}

export function sameAgentContextDescriptor(
  left: AgentContextDescriptor,
  right: AgentContextDescriptor,
): boolean {
  return left.label === right.label && agentPageContextKey(left.pageContext) === agentPageContextKey(right.pageContext);
}

export function isAgentEntityCompatible(
  surface: AgentSurface,
  entity: AgentPageEntity | null | undefined,
): boolean {
  return !entity || ENTITY_SURFACES[entity.kind].includes(surface);
}

export function isAgentNavigationRequest(value: unknown): value is AgentNavigationRequest {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  if (
    !hasExactKeys(record, ["target_surface"])
    && !hasExactKeys(record, ["target_surface", "entity"])
  ) return false;
  if (!AGENT_SURFACES.includes(record.target_surface as AgentSurface)) return false;
  if (record.entity === null || record.entity === undefined) return true;
  if (!record.entity || typeof record.entity !== "object" || Array.isArray(record.entity)) return false;
  const entity = record.entity as Record<string, unknown>;
  if (!hasExactKeys(entity, ["kind", "public_id"])) return false;
  if (!AGENT_ENTITY_KINDS.includes(entity.kind as AgentEntityKind)) return false;
  if (typeof entity.public_id !== "string" || !entity.public_id || entity.public_id.length > 128) return false;
  const typedEntity = entity as AgentPageEntity;
  if (typedEntity.kind === "integration") {
    if (!AGENT_INTEGRATION_IDS.includes(typedEntity.public_id as (typeof AGENT_INTEGRATION_IDS)[number])) {
      return false;
    }
  } else if (!isNumericPublicId(typedEntity.public_id)) {
    return false;
  }
  return isAgentEntityCompatible(record.target_surface as AgentSurface, typedEntity);
}

function descriptor(pageContextValue: AgentPageContext, labelValue: string): AgentContextDescriptor {
  return { pageContext: pageContextValue, label: labelValue };
}

function pageContext(
  surface: AgentSurface,
  filters?: AgentPageFilters,
  entity?: AgentPageEntity,
): AgentPageContext {
  return {
    schema_version: AGENT_SCHEMA_VERSION,
    surface,
    ...(filters && Object.keys(filters).length ? { filters } : {}),
    ...(entity ? { entity } : {}),
  };
}

function safeFilters(filters: AgentPageFilters): AgentPageFilters | undefined {
  const values = Object.fromEntries(
    Object.entries(filters).filter(([, value]) => value !== null && value !== undefined && value !== ""),
  ) as AgentPageFilters;
  return Object.keys(values).length ? values : undefined;
}

function safeText(value: string | null | undefined, maxLength: number): string | undefined {
  const trimmed = value?.trim();
  return trimmed && trimmed.length <= maxLength ? trimmed : undefined;
}

function safeDateRange(
  startDate: string | null | undefined,
  endDate: string | null | undefined,
): Pick<AgentPageFilters, "start_date" | "end_date"> {
  const start = safeDate(startDate);
  const end = safeDate(endDate);
  if (start && end && start > end) return {};
  return { ...(start ? { start_date: start } : {}), ...(end ? { end_date: end } : {}) };
}

function safeDate(value: string | null | undefined): string | undefined {
  const trimmed = value?.trim();
  if (!trimmed || !/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) return undefined;
  const parsed = new Date(`${trimmed}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === trimmed
    ? trimmed
    : undefined;
}

function safeCurrency(value: string | null | undefined): string | undefined {
  const trimmed = value?.trim().toUpperCase();
  return trimmed && /^[A-Z]{3,8}$/.test(trimmed) ? trimmed : undefined;
}

function numericEntity(
  kind: Exclude<AgentEntityKind, "integration">,
  selection: AgentEntitySelection | null | undefined,
): AgentPageEntity | undefined {
  if (!selection) return undefined;
  const publicId = String(selection.publicId);
  return isNumericPublicId(publicId) ? { kind, public_id: publicId } : undefined;
}

function isNumericPublicId(value: string): boolean {
  return /^[1-9][0-9]{0,9}$/.test(value) && Number(value) <= 2_147_483_647;
}

function normalizeIntegrationId(value: string | number | null | undefined): string | undefined {
  const normalized = typeof value === "string" ? value.trim().toLowerCase() : "";
  return AGENT_INTEGRATION_IDS.includes(normalized as (typeof AGENT_INTEGRATION_IDS)[number])
    ? normalized
    : undefined;
}

function hasExactKeys(record: Record<string, unknown>, expected: string[]): boolean {
  const keys = Object.keys(record).sort();
  return keys.length === expected.length
    && keys.every((key, index) => key === [...expected].sort()[index]);
}

function label(parts: Array<string | null | undefined>): string {
  return parts
    .map((part) => part?.replace(/\s+/g, " ").trim())
    .filter((part): part is string => Boolean(part))
    .map((part) => part.slice(0, 80))
    .join(" · ");
}

function titleCase(value: string): string {
  return value.replace(/-/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

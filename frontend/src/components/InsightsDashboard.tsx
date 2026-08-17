import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  CalendarRange,
  ChartNoAxesCombined,
  ChevronDown,
  CircleDollarSign,
  Filter,
  RefreshCw,
  Store,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ResponsiveSheet } from "@/components/ui/responsive-sheet";
import {
  buildExpenseInsightsContext,
  type AgentContextPublisher,
} from "@/agent/pageContext";
import { customGranularity, dateRangeForPreset, type DatePreset } from "@/insightsLogic";
import {
  axisTicks,
  categoryColor,
  comparisonText,
  dateLabel,
  groupSmallCategories,
  meaningfulChange,
  money,
  signedMoney,
  xTickIndexes,
} from "@/insightsVisualization";
import { api } from "@/lib/api";

export type SpendingBasis = "card" | "actual_share";

type Summary = {
  total_cents: number;
  personal_cents: number;
  shared_cents: number;
  classified_cents: number;
  unreviewed_cents: number;
  credits_cents: number;
  unknown_share_transactions: number;
  unknown_credit_share_transactions: number;
  transaction_count: number;
  average_cents: number;
};

type Breakdown = {
  name: string;
  amount_cents: number;
  transaction_count?: number;
  percentage?: number;
  previous_amount_cents?: number;
};

type Trend = {
  period: string;
  total_cents: number;
  personal_cents: number;
  shared_cents: number;
  transactions: number;
};

type Insights = {
  range: {
    start_date: string;
    end_date: string;
    previous_start_date: string;
    previous_end_date: string;
    granularity: "day" | "week" | "month";
  };
  scope: {
    currency: string;
    available_currencies: string[];
    excluded_other_currency_transactions: number;
    spend_basis: SpendingBasis;
    viewer_share_identity_connected: boolean;
    pending_transactions_excluded: boolean;
  };
  summary: Summary;
  comparison: Summary;
  trend: Trend[];
  category_breakdown: Breakdown[];
  subcategory_breakdown: Breakdown[];
  merchant_breakdown: Breakdown[];
  personal_shared: { personal: number; shared: number };
  shared_people: Breakdown[];
  shared_groups: Breakdown[];
  category_trend: { period: string; categories: Record<string, number> }[];
  notable_changes: {
    kind: string;
    direction: string;
    label: string;
    amount_cents: number;
    detail: string;
  }[];
  accounts: string[];
  categories: string[];
  merchants: string[];
  data_quality: {
    unknown_share_transactions: number;
    unknown_credit_share_transactions: number;
    unreviewed_cents: number;
    pending_review_cents: number;
    uncategorized_cents: number;
    pending_transactions_excluded: boolean;
  };
};

const primaryPresets: [DatePreset, string][] = [
  ["7d", "7D"],
  ["30d", "30D"],
  ["90d", "3M"],
  ["ytd", "YTD"],
  ["custom", "Custom"],
];

const secondaryPresets: [DatePreset, string][] = [
  ["this_month", "This month"],
  ["last_month", "Last month"],
  ["this_quarter", "This quarter"],
  ["last_quarter", "Last quarter"],
];

const presets = [...primaryPresets, ...secondaryPresets];

export function InsightsDashboard({
  onAgentContextChange,
}: {
  onAgentContextChange?: AgentContextPublisher;
} = {}) {
  const initial = useMemo(() => dateRangeForPreset("30d"), []);
  const [preset, setPreset] = useState<DatePreset>("30d");
  const [start, setStart] = useState(initial.start);
  const [end, setEnd] = useState(initial.end);
  const [account, setAccount] = useState("");
  const [category, setCategory] = useState("");
  const [merchant, setMerchant] = useState("");
  const [merchantInput, setMerchantInput] = useState("");
  const [reviewType, setReviewType] = useState("all");
  const [basis, setBasis] = useState<SpendingBasis>("card");
  const [currency, setCurrency] = useState("");
  const [data, setData] = useState<Insights | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [topCount, setTopCount] = useState(5);
  const [sharedMode, setSharedMode] = useState<"people" | "groups">("people");
  const [trendMode, setTrendMode] = useState<"total" | "split">("total");
  const [reloadToken, setReloadToken] = useState(0);

  const agentContext = useMemo(
    () => buildExpenseInsightsContext({
      startDate: start,
      endDate: end,
      datePreset: preset,
      accountId: account,
      category,
      merchant,
      reviewType,
      currencyCode: currency,
      spendBasis: basis === "actual_share" ? "actual_share" : "card",
    }),
    [account, basis, category, currency, end, merchant, preset, reviewType, start],
  );

  useEffect(() => {
    onAgentContextChange?.(agentContext);
  }, [agentContext, onAgentContextChange]);

  useEffect(() => {
    const timer = window.setTimeout(() => setMerchant(merchantInput.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [merchantInput]);

  useEffect(() => {
    if (!start || !end || start > end) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const params = new URLSearchParams({
      start_date: start,
      end_date: end,
      review_type: reviewType,
      spend_basis: basis,
      granularity: customGranularity(start, end),
    });
    if (account) params.set("account_id", account);
    if (category) params.set("category", category);
    if (merchant) params.set("merchant", merchant);
    if (currency) params.set("currency_code", currency);

    api<Insights>(`/api/insights/spending?${params}`, { signal: controller.signal })
      .then(setData)
      .catch((value) => {
        if (!controller.signal.aborted) {
          setError(typeof value?.detail === "string" ? value.detail : "We couldn't load spending insights.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [start, end, account, category, merchant, reviewType, basis, currency, reloadToken]);

  function choosePreset(value: DatePreset) {
    setPreset(value);
    if (value !== "custom") {
      const range = dateRangeForPreset(value);
      setStart(range.start);
      setEnd(range.end);
    }
  }

  function clearFilters() {
    choosePreset("30d");
    setAccount("");
    setCategory("");
    setMerchant("");
    setMerchantInput("");
    setReviewType("all");
    setBasis("card");
    setCurrency("");
  }

  const filtered = Boolean(
    preset !== "30d" || account || category || merchant || reviewType !== "all" || basis !== "card" || currency,
  );
  const activeFilterCount = [account, category, merchant, reviewType !== "all", basis !== "card", currency].filter(Boolean).length;

  if (error && !data) {
    return (
      <CompactMessage
        title="Insights unavailable"
        detail={error}
        action={() => setReloadToken((value) => value + 1)}
      />
    );
  }

  return (
    <section className="space-y-5" aria-label="Spending insights" aria-busy={loading}>
      <InsightsControls
        preset={preset}
        start={start}
        end={end}
        account={account}
        category={category}
        merchant={merchant}
        merchantInput={merchantInput}
        reviewType={reviewType}
        basis={basis}
        currency={currency}
        data={data}
        filtered={filtered}
        activeFilterCount={activeFilterCount}
        choosePreset={choosePreset}
        setStart={setStart}
        setEnd={setEnd}
        setAccount={setAccount}
        setCategory={setCategory}
        setMerchant={setMerchant}
        setMerchantInput={setMerchantInput}
        setReviewType={setReviewType}
        setBasis={setBasis}
        setCurrency={setCurrency}
        clearFilters={clearFilters}
      />

      {error ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          <span>{error} The last loaded view is still shown below.</span>
          <Button variant="outline" size="sm" onClick={() => setReloadToken((value) => value + 1)}>
            <RefreshCw className="h-4 w-4" /> Retry
          </Button>
        </div>
      ) : null}

      {loading && !data ? (
        <DashboardSkeleton />
      ) : data && (
        data.summary.transaction_count ||
        data.comparison.transaction_count ||
        data.summary.total_cents ||
        data.comparison.total_cents ||
        data.summary.credits_cents ||
        data.comparison.credits_cents ||
        data.summary.unknown_credit_share_transactions ||
        data.comparison.unknown_credit_share_transactions ||
        data.summary.unknown_share_transactions ||
        data.comparison.unknown_share_transactions ||
        data.data_quality.unknown_share_transactions
      ) ? (
        <InsightsContent
          data={data}
          basis={basis}
          category={category}
          trendMode={trendMode}
          topCount={topCount}
          sharedMode={sharedMode}
          setCategory={setCategory}
          setMerchant={setMerchant}
          setMerchantInput={setMerchantInput}
          setReviewType={setReviewType}
          setTrendMode={setTrendMode}
          setTopCount={setTopCount}
          setSharedMode={setSharedMode}
        />
      ) : data ? (
        <CompactMessage title="No spending data for this period" detail="Try a wider date range or sync transactions." />
      ) : null}
    </section>
  );
}

type ControlsProps = {
  preset: DatePreset;
  start: string;
  end: string;
  account: string;
  category: string;
  merchant: string;
  merchantInput: string;
  reviewType: string;
  basis: SpendingBasis;
  currency: string;
  data: Insights | null;
  filtered: boolean;
  activeFilterCount: number;
  choosePreset: (value: DatePreset) => void;
  setStart: (value: string) => void;
  setEnd: (value: string) => void;
  setAccount: (value: string) => void;
  setCategory: (value: string) => void;
  setMerchant: (value: string) => void;
  setMerchantInput: (value: string) => void;
  setReviewType: (value: string) => void;
  setBasis: (value: SpendingBasis) => void;
  setCurrency: (value: string) => void;
  clearFilters: () => void;
};

function InsightsControls(props: ControlsProps) {
  const [rangeOpen, setRangeOpen] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const currencyOptions = Array.from(new Set([
    props.data?.scope.currency || "USD",
    ...(props.data?.scope.available_currencies || []),
  ])).sort();
  const secondaryPreset = secondaryPresets.find(([value]) => value === props.preset);
  const selectedRangeLabel = presets.find(([value]) => value === props.preset)?.[1] || "Date range";
  const chooseMobilePreset = (value: DatePreset) => {
    props.choosePreset(value);
    setRangeOpen(false);
  };
  return (
    <Card variant="primary" className="relative z-20 overflow-visible bg-white">
      <CardContent className="space-y-3 p-3 pt-3 sm:p-3 sm:pt-3">
        <div className="flex min-w-0 items-center gap-2 sm:hidden">
          <ResponsiveSheet
            open={rangeOpen}
            onOpenChange={setRangeOpen}
            title="Choose a date range"
            description="Compare a focused period without crowding the dashboard."
            trigger={
              <Button variant="outline" className="min-w-0 flex-1 justify-between px-3" aria-label={`Date range: ${selectedRangeLabel}`}>
                <span className="flex min-w-0 items-center gap-2"><CalendarRange className="h-4 w-4 shrink-0 text-indigo-600" /><span className="truncate">{selectedRangeLabel}</span></span>
                <ChevronDown className="h-4 w-4 shrink-0 text-slate-500" />
              </Button>
            }
          >
            <div className="grid grid-cols-2 gap-2" aria-label="Date range presets">
              {presets.map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={props.preset === value}
                  onClick={() => chooseMobilePreset(value)}
                  className={`min-h-11 rounded-control border px-3 text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${props.preset === value ? "border-indigo-600 bg-indigo-600 text-white" : "border-slate-200 bg-white text-slate-700 hover:border-indigo-300 hover:bg-indigo-50"}`}
                >
                  {label}
                </button>
              ))}
            </div>
          </ResponsiveSheet>

          <label className="sr-only" htmlFor="insights-spending-basis-mobile">Spending basis</label>
          <select id="insights-spending-basis-mobile" className="h-11 max-w-28 rounded-lg border border-slate-200 bg-white px-2 text-xs font-semibold text-slate-700 hover:border-indigo-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500" value={props.basis} onChange={(event) => props.setBasis(event.target.value as SpendingBasis)}>
            <option value="card">Card spend</option>
            <option value="actual_share">My share</option>
          </select>

          <ResponsiveSheet
            open={filtersOpen}
            onOpenChange={setFiltersOpen}
            title="Refine insights"
            description="Narrow the story by account, category, merchant, currency, or review type."
            trigger={
              <Button variant="outline" className="relative shrink-0 px-3" aria-label={`Filters${props.activeFilterCount ? `, ${props.activeFilterCount} active` : ""}`}>
                <Filter className="h-4 w-4" />
                <span>Filters</span>
                {props.activeFilterCount ? <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-indigo-100 px-1 text-[11px] font-semibold text-indigo-800">{props.activeFilterCount}</span> : null}
              </Button>
            }
            footer={<Button className="w-full" onClick={() => setFiltersOpen(false)}>View insights</Button>}
          >
            <AdvancedFilterFields props={props} currencyOptions={currencyOptions} />
          </ResponsiveSheet>
        </div>

        <div className="hidden flex-wrap items-center gap-2 sm:flex">
          <div className="flex min-w-0 flex-1 gap-1.5 overflow-x-auto" aria-label="Date range presets">
            {primaryPresets.map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={props.preset === value}
                onClick={() => props.choosePreset(value)}
                className={`min-h-11 min-w-11 shrink-0 rounded-lg border px-3 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 ${
                  props.preset === value
                    ? "border-indigo-600 bg-indigo-600 text-white"
                    : "border-slate-200 bg-white text-slate-700 hover:border-indigo-300 hover:bg-indigo-50"
                }`}
              >
                {label}
              </button>
            ))}
            <details className="group relative shrink-0">
              <summary className={`flex min-h-11 cursor-pointer list-none items-center gap-1 rounded-lg border px-3 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 ${secondaryPreset ? "border-indigo-600 bg-indigo-50 text-indigo-800" : "border-slate-200 bg-white text-slate-700 hover:border-indigo-300 hover:bg-indigo-50"}`}>
                {secondaryPreset?.[1] || "More ranges"}
                <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
              </summary>
              <div className="absolute left-0 top-full z-30 mt-2 min-w-44 rounded-xl border border-slate-200 bg-white p-1.5 shadow-xl">
                {secondaryPresets.map(([value, label]) => (
                  <button key={value} type="button" aria-pressed={props.preset === value} onClick={() => props.choosePreset(value)} className="flex min-h-11 w-full items-center rounded-lg px-3 text-left text-sm font-medium text-slate-700 hover:bg-indigo-50 hover:text-indigo-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">
                    {label}
                  </button>
                ))}
              </div>
            </details>
          </div>
          <label className="sr-only" htmlFor="insights-spending-basis-desktop">Spending basis</label>
          <select id="insights-spending-basis-desktop" className="h-11 rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-700 hover:border-indigo-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500" value={props.basis} onChange={(event) => props.setBasis(event.target.value as SpendingBasis)}>
            <option value="card">Card spend</option>
            <option value="actual_share">My actual share</option>
          </select>
          <details className="group relative shrink-0">
            <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-700 hover:border-indigo-300 hover:bg-indigo-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2">
              <span className="flex items-center gap-2">
                <Filter className="h-4 w-4 text-slate-500" /> Filters
                {props.activeFilterCount ? <span className="rounded-full bg-indigo-100 px-1.5 py-0.5 text-[11px] text-indigo-800">{props.activeFilterCount}</span> : null}
              </span>
              <ChevronDown className="h-4 w-4 text-slate-500 transition-transform group-open:rotate-180" />
            </summary>
            <div className="absolute right-0 top-full z-30 mt-2 w-[min(42rem,calc(100vw-2rem))] rounded-xl border border-slate-200 bg-white p-4 shadow-xl">
              <AdvancedFilterFields props={props} currencyOptions={currencyOptions} />
            </div>
          </details>
        </div>

        {props.preset === "custom" ? (
          <div className="flex flex-wrap items-end gap-3">
            <DateField label="Start date" value={props.start} onChange={props.setStart} />
            <span className="pb-3 text-slate-400" aria-hidden="true">→</span>
            <DateField label="End date" value={props.end} onChange={props.setEnd} />
            {props.start > props.end ? (
              <p role="alert" className="pb-2 text-sm text-rose-700">Start date must not be after end date.</p>
            ) : null}
          </div>
        ) : null}

        {props.filtered ? (
          <div className="flex flex-wrap items-center gap-2" aria-label="Active insight filters">
            {props.preset !== "30d" ? <Chip label={`Range: ${presets.find(([value]) => value === props.preset)?.[1]}`} onRemove={() => props.choosePreset("30d")} /> : null}
            {props.account ? <Chip label={`Account: ${props.account}`} onRemove={() => props.setAccount("")} /> : null}
            {props.category ? <Chip label={`Category: ${props.category}`} onRemove={() => props.setCategory("")} /> : null}
            {props.merchant ? <Chip label={`Merchant: ${props.merchant}`} onRemove={() => { props.setMerchant(""); props.setMerchantInput(""); }} /> : null}
            {props.reviewType !== "all" ? <Chip label={`Type: ${title(props.reviewType)}`} onRemove={() => props.setReviewType("all")} /> : null}
            {props.basis !== "card" ? <Chip label="Basis: My actual share" onRemove={() => props.setBasis("card")} /> : null}
            {props.currency ? <Chip label={`Currency: ${props.currency}`} onRemove={() => props.setCurrency("")} /> : null}
            <Button variant="ghost" size="sm" onClick={props.clearFilters}><X className="h-4 w-4" />Clear all</Button>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function AdvancedFilterFields({ props, currencyOptions }: { props: ControlsProps; currencyOptions: string[] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      <Select label="Account" value={props.account} onChange={props.setAccount} options={props.data?.accounts || []} all="All accounts" />
      <Select label="Category" value={props.category} onChange={props.setCategory} options={props.data?.categories || []} all="All categories" />
      <label className="grid gap-1 text-xs font-semibold text-slate-700">Merchant<Input value={props.merchantInput} onChange={(event) => props.setMerchantInput(event.target.value)} placeholder="All merchants" /></label>
      <Select label="Currency" value={props.currency || props.data?.scope.currency || "USD"} onChange={props.setCurrency} options={currencyOptions} all="Currency" includeAll={false} />
      <Select label="Type" value={props.reviewType} onChange={props.setReviewType} options={["personal", "shared"]} all="All types" />
    </div>
  );
}

type ContentProps = {
  data: Insights;
  basis: SpendingBasis;
  category: string;
  trendMode: "total" | "split";
  topCount: number;
  sharedMode: "people" | "groups";
  setCategory: (value: string) => void;
  setMerchant: (value: string) => void;
  setMerchantInput: (value: string) => void;
  setReviewType: (value: string) => void;
  setTrendMode: (value: "total" | "split") => void;
  setTopCount: (value: number) => void;
  setSharedMode: (value: "people" | "groups") => void;
};

function InsightsContent(props: ContentProps) {
  const { data } = props;
  const currency = data.scope.currency;
  const currentRange = formatRange(data.range.start_date, data.range.end_date);
  const previousRange = formatRange(data.range.previous_start_date, data.range.previous_end_date);
  const sharedItems = props.sharedMode === "people" ? data.shared_people : data.shared_groups;
  const actualShareBasis = data.scope.spend_basis === "actual_share";
  const incompletePurchaseComparison = actualShareBasis && Boolean(
    data.summary.unknown_share_transactions || data.comparison.unknown_share_transactions,
  );
  const incompleteCreditComparison = actualShareBasis && Boolean(
    data.summary.unknown_credit_share_transactions ||
      data.comparison.unknown_credit_share_transactions,
  );
  const dataNotes = [
    data.scope.excluded_other_currency_transactions
      ? `${data.scope.excluded_other_currency_transactions} transaction${data.scope.excluded_other_currency_transactions === 1 ? " is" : "s are"} excluded because this view is limited to ${currency}.`
      : "",
    data.data_quality.unreviewed_cents
      ? `${money(data.data_quality.unreviewed_cents, currency)} is unreviewed and shown separately from Personal and Shared.`
      : "",
    data.data_quality.uncategorized_cents
      ? `${money(data.data_quality.uncategorized_cents, currency)} is included in Total spend as Uncategorized.`
      : "",
    data.summary.credits_cents
      ? actualShareBasis
        ? `${money(data.summary.credits_cents, currency)} in attributable credits is reported separately and excluded from Total spend.`
        : `${money(data.summary.credits_cents, currency)} in card credits, including refunds, is reported separately and excluded from Total spend.`
      : "",
    data.data_quality.pending_transactions_excluded
      ? "Bank-pending transactions are excluded. Personal and Shared include only classified transactions."
      : "",
  ].filter(Boolean);

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700 sm:flex-row sm:items-center sm:justify-between">
        <span className="flex items-center gap-2 font-medium text-slate-900">
          <CalendarRange className="h-4 w-4 text-indigo-600" />
          {currentRange}
        </span>
        <span>Compared with {previousRange} · {currency} only · no currency conversion</span>
      </div>

      {actualShareBasis && (
        data.summary.unknown_share_transactions || data.comparison.unknown_share_transactions
      ) ? (
        <DataNotice tone="warning">
          {data.summary.unknown_share_transactions
            ? `${data.summary.unknown_share_transactions} current-period shared purchase${data.summary.unknown_share_transactions === 1 ? " has" : "s have"} no confirmed viewer allocation and ${data.summary.unknown_share_transactions === 1 ? "is" : "are"} omitted from My actual share. `
            : ""}
          {data.comparison.unknown_share_transactions
            ? `${data.comparison.unknown_share_transactions} prior-period shared purchase${data.comparison.unknown_share_transactions === 1 ? " has" : "s have"} no confirmed viewer allocation and ${data.comparison.unknown_share_transactions === 1 ? "is" : "are"} omitted from the comparison. `
            : ""}
          ExpenseOps will not guess a shared purchase allocation.
        </DataNotice>
      ) : null}
      {actualShareBasis && !data.scope.viewer_share_identity_connected ? (
        <DataNotice tone="warning">Connect and verify your own Splitwise account to calculate My actual share. ExpenseOps will not guess from another payer.</DataNotice>
      ) : null}
      {actualShareBasis && (
        data.summary.unknown_credit_share_transactions ||
        data.comparison.unknown_credit_share_transactions
      ) ? (
        <DataNotice tone="warning">
          {data.summary.unknown_credit_share_transactions
            ? `${data.summary.unknown_credit_share_transactions} current-period shared credit${data.summary.unknown_credit_share_transactions === 1 ? "" : "s"} ${data.summary.unknown_credit_share_transactions === 1 ? "has" : "have"} no confirmed viewer allocation and ${data.summary.unknown_credit_share_transactions === 1 ? "is" : "are"} omitted from Attributable credits. `
            : ""}
          {data.comparison.unknown_credit_share_transactions
            ? `${data.comparison.unknown_credit_share_transactions} prior-period shared credit${data.comparison.unknown_credit_share_transactions === 1 ? "" : "s"} ${data.comparison.unknown_credit_share_transactions === 1 ? "has" : "have"} no confirmed viewer allocation and ${data.comparison.unknown_credit_share_transactions === 1 ? "is" : "are"} omitted from the comparison. `
            : ""}
          ExpenseOps will not guess a shared credit allocation.
        </DataNotice>
      ) : null}
      {dataNotes.length ? (
        <details className="group rounded-xl border border-slate-200 bg-white">
          <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 rounded-xl px-4 text-sm font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">
            <span>Data notes <span className="ml-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{dataNotes.length}</span></span>
            <ChevronDown className="h-4 w-4 text-slate-500 transition-transform group-open:rotate-180" />
          </summary>
          <ul className="space-y-2 border-t border-slate-100 px-4 py-3 text-sm text-slate-600">
            {dataNotes.map((note) => <li key={note} className="flex gap-2"><span aria-hidden="true">•</span><span>{note}</span></li>)}
          </ul>
        </details>
      ) : null}

      <section aria-labelledby="insights-overview-heading">
        <h2 id="insights-overview-heading" className="sr-only">Spending overview</h2>
        <div className="grid gap-3 sm:grid-cols-4 xl:grid-cols-12">
          <Kpi
            label="Total spend"
            value={data.summary.total_cents}
            previous={data.comparison.total_cents}
            featured
            comparisonRange={previousRange}
            currency={currency}
            suppressPercentage={incompletePurchaseComparison}
          />
          <Kpi label="Personal" value={data.summary.personal_cents} previous={data.comparison.personal_cents} comparisonRange={previousRange} currency={currency} suppressPercentage={incompletePurchaseComparison} />
          <Kpi label="Shared" value={data.summary.shared_cents} previous={data.comparison.shared_cents} comparisonRange={previousRange} currency={currency} suppressPercentage={incompletePurchaseComparison} />
          <Kpi label="Unreviewed" value={data.summary.unreviewed_cents} previous={data.comparison.unreviewed_cents} comparisonRange={previousRange} currency={currency} suppressPercentage={incompletePurchaseComparison} />
          {data.summary.credits_cents || data.comparison.credits_cents ? (
            <Kpi label={actualShareBasis ? "Attributable credits" : "Card credits"} value={data.summary.credits_cents} previous={data.comparison.credits_cents} comparisonRange={previousRange} currency={currency} suppressPercentage={incompleteCreditComparison} />
          ) : null}
          <Kpi label={actualShareBasis ? "Confirmed transactions" : "Transactions"} raw={String(data.summary.transaction_count)} previousRaw={data.comparison.transaction_count} comparisonRange={previousRange} currency={currency} />
        </div>
        <details className="mt-2 text-xs text-slate-600">
          <summary className="flex min-h-11 cursor-pointer items-center rounded-md font-medium hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">How this total is calculated</summary>
          <p className="mt-1">Total {money(data.summary.total_cents, currency)} = Personal {money(data.summary.personal_cents, currency)} + Shared {money(data.summary.shared_cents, currency)} + Unreviewed {money(data.summary.unreviewed_cents, currency)}. Credits, including refunds, are reported separately and are not netted into Total spend.</p>
        </details>
      </section>

      <ChartCard title="What changed" eyebrow={`Compared with ${previousRange}`}>
        <Changes items={data.notable_changes} incomplete={incompletePurchaseComparison} />
      </ChartCard>

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(22rem,0.65fr)]">
        <ChartCard
          title="Spending over time"
          eyebrow={`${currentRange} · ${data.range.granularity} view`}
          action={<Toggle values={["total", "split"]} labels={["Total", "Personal / shared"]} value={props.trendMode} onChange={(value) => props.setTrendMode(value as "total" | "split")} />}
        >
          <LineChart values={data.trend} split={props.trendMode === "split"} granularity={data.range.granularity} currency={currency} />
        </ChartCard>
        <ChartCard title="Where the money went" eyebrow="Category composition">
          <Donut items={data.category_breakdown} total={data.summary.total_cents} onSelect={props.setCategory} currency={currency} />
        </ChartCard>
      </div>

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
        <ChartCard
          title="Top merchants"
          eyebrow="Largest totals in this view"
          action={<Toggle values={["5", "10"]} labels={["Top 5", "Top 10"]} value={String(props.topCount)} onChange={(value) => props.setTopCount(Number(value))} />}
        >
          <Bars
            items={data.merchant_breakdown.slice(0, props.topCount)}
            showCounts
            onSelect={(value) => { props.setMerchant(value); props.setMerchantInput(value); }}
            currency={currency}
          />
        </ChartCard>
        <ChartCard
          title="Personal and shared"
          eyebrow={actualShareBasis ? "Classified actual share" : "Classified card spend"}
        >
          <StackedSplit values={data.personal_shared} onSelect={props.setReviewType} currency={currency} />
        </ChartCard>
      </div>

      {props.category ? (
        <ChartCard title={`${props.category} detail`} eyebrow="Source categories">
          <Bars items={data.subcategory_breakdown} onSelect={() => undefined} interactive={false} currency={currency} />
        </ChartCard>
      ) : null}

      <ChartCard title="Category trend" eyebrow="How category mix changed over time">
        <CategoryTrend values={data.category_trend} granularity={data.range.granularity} currency={currency} />
      </ChartCard>

      <section aria-label="Shared spending detail">
        <ChartCard
          title="Shared with"
          eyebrow={props.sharedMode === "people" ? "People in confirmed splits" : "Groups in confirmed splits"}
          action={<Toggle values={["people", "groups"]} labels={["People", "Groups"]} value={props.sharedMode} onChange={(value) => props.setSharedMode(value as "people" | "groups")} />}
        >
          {sharedItems.length ? (
            <Bars items={sharedItems.slice(0, 10)} onSelect={() => undefined} interactive={false} currency={currency} />
          ) : (
            <p className="py-8 text-center text-sm text-slate-600">No shared spending in this period.</p>
          )}
        </ChartCard>
      </section>
    </div>
  );
}

function Kpi({
  label,
  value,
  previous,
  raw,
  previousRaw,
  featured = false,
  comparisonRange,
  currency,
  suppressPercentage = false,
}: {
  label: string;
  value?: number;
  previous?: number;
  raw?: string;
  previousRaw?: number;
  featured?: boolean;
  comparisonRange: string;
  currency: string;
  suppressPercentage?: boolean;
}) {
  const current = value ?? Number(raw || 0);
  const prior = previous ?? previousRaw;
  const comparison = prior === undefined ? null : raw ? countComparison(current, prior) : comparisonText(current, prior, currency);
  return (
    <Card variant="primary" className={featured ? "overflow-hidden border-indigo-200 bg-gradient-to-br from-indigo-50 via-white to-white sm:col-span-4 xl:col-span-4" : "sm:col-span-2 xl:col-span-2"}>
      <CardContent className={featured ? "flex min-h-40 flex-col justify-between p-5 pt-5 sm:p-5 sm:pt-5" : "min-h-32 p-4 pt-4 sm:p-4 sm:pt-4"}>
        <p className={`text-xs font-semibold uppercase tracking-[0.12em] ${featured ? "text-indigo-700" : "text-slate-600"}`}>{label}</p>
        <p className={`mt-2 font-semibold tabular-nums text-slate-950 ${featured ? "text-4xl sm:text-[2.75rem]" : "text-2xl"}`}>{raw || money(value || 0, currency)}</p>
        <div className="mt-2 text-xs text-slate-600">
          {comparison ? (
            <p className="inline-flex rounded-full bg-slate-100 px-2 py-1">
              {suppressPercentage ? `Confirmed allocations only: ${comparison.primary}` : comparison.primary}
              {comparison.secondary && !suppressPercentage ? ` ${comparison.secondary}` : ""}
            </p>
          ) : (
            <p>No comparison available</p>
          )}
          <span className="sr-only">Comparison period: {comparisonRange}</span>
        </div>
      </CardContent>
    </Card>
  );
}

function ChartCard({ title, eyebrow, action, children }: { title: string; eyebrow?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <Card variant="primary">
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div>
          {eyebrow ? <p className="mb-1 text-xs font-medium text-slate-500">{eyebrow}</p> : null}
          <CardTitle className="text-base text-slate-950">{title}</CardTitle>
        </div>
        {action}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function Bars({
  items,
  onSelect,
  comparison = false,
  showCounts = false,
  total = 0,
  interactive = true,
  currency,
}: {
  items: Breakdown[];
  onSelect: (name: string) => void;
  comparison?: boolean;
  showCounts?: boolean;
  total?: number;
  interactive?: boolean;
  currency: string;
}) {
  const max = Math.max(1, ...items.map((value) => value.amount_cents));
  return (
    <div className="space-y-2" role="list">
      {items.map((item) => {
        const content = (
          <>
            <span className="flex justify-between gap-3 text-sm">
              <span className="truncate font-medium text-slate-800">{item.name}</span>
              <span className="shrink-0 tabular-nums text-slate-900">{money(item.amount_cents, currency)}</span>
            </span>
            {showCounts && item.transaction_count ? <span className="mt-0.5 block text-xs text-slate-500">{item.transaction_count} transaction{item.transaction_count === 1 ? "" : "s"}</span> : null}
            <span className="mt-1.5 block h-2 overflow-hidden rounded-full bg-slate-100">
              <span className="block h-full rounded-full bg-indigo-600" style={{ width: `${Math.max(2, item.amount_cents / max * 100)}%` }} />
            </span>
            {comparison && meaningfulChange(item.amount_cents, item.previous_amount_cents || 0, total) ? <span className="mt-1 block text-xs text-slate-500">{signedMoney(item.amount_cents - (item.previous_amount_cents || 0), currency)} vs previous period</span> : null}
          </>
        );
        return interactive ? (
          <button type="button" role="listitem" key={item.name} onClick={() => onSelect(item.name)} className="block min-h-11 w-full rounded-lg p-2 text-left hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500" aria-label={`${item.name}, ${money(item.amount_cents, currency)}. Filter by ${item.name}.`}>
            {content}
          </button>
        ) : (
          <div role="listitem" key={item.name} className="rounded-lg p-2">{content}</div>
        );
      })}
    </div>
  );
}

function Donut({ items, total, onSelect, currency }: { items: Breakdown[]; total: number; onSelect: (name: string) => void; currency: string }) {
  const [activeName, setActiveName] = useState<string | null>(null);
  const grouped = groupSmallCategories(items);
  const groupedTotal = grouped.reduce((sum, item) => sum + item.amount_cents, 0);
  let offset = 0;
  const gradient = grouped.map((item) => {
    const start = offset;
    offset += groupedTotal ? item.amount_cents / groupedTotal * 100 : 0;
    return `${categoryColor(item.name)} ${start}% ${offset}%`;
  }).join(",");
  const activeItem = grouped.find((item) => item.name === activeName);
  const activePercentage = activeItem && groupedTotal ? Math.round(activeItem.amount_cents / groupedTotal * 100) : 0;

  return (
    <div className="grid items-center gap-5 sm:grid-cols-[180px_1fr]">
      <div className="relative mx-auto h-40 w-40 rounded-full transition-transform duration-200 motion-reduce:transition-none" style={{ background: `conic-gradient(${gradient})`, transform: activeItem ? "scale(1.035)" : "scale(1)" }} role="img" aria-label={`Category composition. ${money(total, currency)} total.`}>
        <div className="absolute inset-6 flex flex-col items-center justify-center rounded-full bg-white text-center">
          <strong className="text-lg tabular-nums text-slate-950">{money(activeItem?.amount_cents ?? total, currency)}</strong>
          <span className="max-w-24 truncate text-xs text-slate-500">{activeItem ? (activeItem.name === "Other" ? "Other categories" : activeItem.name) : "Total"}</span>
        </div>
        {activeItem ? <ChartTooltip className="bottom-full left-1/2 mb-2 -translate-x-1/2" title={activeItem.name === "Other" ? "Other categories" : activeItem.name} lines={[`${money(activeItem.amount_cents, currency)} · ${activePercentage}% of spend`]} /> : null}
      </div>
      <div className="space-y-1">
        {grouped.map((item) => {
          const percentage = groupedTotal ? Math.round(item.amount_cents / groupedTotal * 100) : 0;
          const label = item.name === "Other" ? "Other categories" : item.name;
          const content = <><span className="flex min-w-0 items-center gap-2"><span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: categoryColor(item.name) }} /><span className="truncate">{label}</span></span><span className="shrink-0 tabular-nums text-slate-600">{money(item.amount_cents, currency)} · {percentage}%</span></>;
          return item.name === "Other" ? (
            <div key={item.name} tabIndex={0} onFocus={() => setActiveName(item.name)} onBlur={() => setActiveName(null)} onPointerEnter={() => setActiveName(item.name)} onPointerLeave={() => setActiveName(null)} className="flex min-h-11 w-full items-center justify-between gap-3 rounded-lg px-2 text-sm outline-none hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-indigo-500" aria-label={`${label}, ${money(item.amount_cents, currency)}, ${percentage}%. Grouped from smaller categories.`}>{content}</div>
          ) : (
            <button key={item.name} type="button" onClick={() => onSelect(item.name)} onFocus={() => setActiveName(item.name)} onBlur={() => setActiveName(null)} onPointerEnter={() => setActiveName(item.name)} onPointerLeave={() => setActiveName(null)} className={`flex min-h-11 w-full items-center justify-between gap-3 rounded-lg px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${activeName === item.name ? "bg-indigo-50" : "hover:bg-slate-50"}`} aria-label={`${item.name}, ${money(item.amount_cents, currency)}, ${percentage}%. Filter by category.`}>{content}</button>
          );
        })}
      </div>
    </div>
  );
}

function LineChart({ values, split, granularity, currency }: { values: Trend[]; split: boolean; granularity: "day" | "week" | "month"; currency: string }) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const plottedValues = values.flatMap((value) => split
    ? [value.personal_cents, value.shared_cents]
    : [value.total_cents]);
  const floor = 0;
  const rawCeiling = Math.max(0, ...plottedValues);
  const rangeTicks = axisTicks(Math.max(1, rawCeiling - floor));
  const ceiling = floor + (rangeTicks.at(-1) || 1);
  const ticks = rangeTicks.map((tick) => floor + tick);
  const left = 58;
  const right = 620;
  const top = 18;
  const bottom = 238;
  const x = (index: number) => values.length === 1 ? (left + right) / 2 : left + index / (values.length - 1) * (right - left);
  const y = (amount: number) => bottom - (amount - floor) / (ceiling - floor) * (bottom - top);
  const points = (key: keyof Trend) => values.map((value, index) => `${x(index)},${y(Number(value[key]))}`).join(" ");
  const dateTicks = xTickIndexes(values.length, 5);
  const activeValue = activeIndex === null ? null : values[activeIndex];
  const activeY = activeValue
    ? Math.min(y(activeValue.total_cents), y(activeValue.personal_cents), y(activeValue.shared_cents))
    : top;
  const totalArea = values.length
    ? `${left},${bottom} ${points("total_cents")} ${right},${bottom}`
    : "";

  return (
    <div onPointerLeave={() => setActiveIndex(null)}>
      <div className="mb-3 flex flex-wrap gap-4 text-xs text-slate-600" aria-label="Chart legend">
        {split ? (
          <><Legend color="#475569" label="Personal" /><Legend color="#4f46e5" label="Shared" /></>
        ) : <Legend color="#4f46e5" label="Total spend" />}
      </div>
      <div className="relative -mx-1">
        <div
          className="overflow-x-auto rounded-lg px-1 pb-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 sm:overflow-visible"
          role="region"
          aria-label="Scrollable spend over time chart"
          aria-describedby="spend-chart-scroll-hint"
          tabIndex={0}
        >
          <div className="relative min-h-[240px] w-[640px] sm:w-full">
            <svg viewBox="0 0 640 280" className="h-[260px] w-[640px] max-w-none sm:h-[300px] sm:w-full" role="img" aria-label={`Spend over time, ${split ? "personal and shared series" : "total series"}`}>
              <defs><linearGradient id="insights-total-area" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#4f46e5" stopOpacity="0.18" /><stop offset="100%" stopColor="#4f46e5" stopOpacity="0" /></linearGradient></defs>
              {ticks.map((tick) => <g key={tick}><line x1={left} x2={right} y1={y(tick)} y2={y(tick)} stroke="#e2e8f0" /><text x={left - 8} y={y(tick) + 4} textAnchor="end" fontSize="11" fill="#64748b">{compactMoney(tick, currency)}</text></g>)}
              {!split && totalArea ? <polygon points={totalArea} fill="url(#insights-total-area)" /> : null}
              {split ? (
                <><polyline points={points("personal_cents")} fill="none" stroke="#475569" strokeWidth="2" strokeDasharray="6 5" /><polyline points={points("shared_cents")} fill="none" stroke="#4f46e5" strokeWidth="2" /></>
              ) : <polyline points={points("total_cents")} fill="none" stroke="#4f46e5" strokeWidth="2" />}
              {activeIndex !== null ? <line x1={x(activeIndex)} x2={x(activeIndex)} y1={top} y2={bottom} stroke="#6366f1" strokeDasharray="3 4" strokeWidth="1" /> : null}
              {values.map((value, index) => {
                const label = split
                  ? `${dateLabel(value.period, granularity, true)}. Personal ${money(value.personal_cents, currency)}. Shared ${money(value.shared_cents, currency)}. Total ${money(value.total_cents, currency)}. ${value.transactions} transactions.`
                  : `${dateLabel(value.period, granularity, true)}. Total spend ${money(value.total_cents, currency)}. ${value.transactions} transactions.`;
                return (
                  <g key={value.period} data-chart-point={value.period} role="img" tabIndex={0} aria-label={label} onFocus={() => setActiveIndex(index)} onBlur={() => setActiveIndex(null)} onPointerEnter={() => setActiveIndex(index)} onClick={() => setActiveIndex(index)} onKeyDown={(event) => { if (event.key === "Escape") setActiveIndex(null); }} className="cursor-crosshair outline-none focus:[&_circle]:stroke-indigo-950 focus:[&_circle]:stroke-[3px]">
                    <rect x={Math.max(left, x(index) - 22)} y={top} width="44" height={bottom - top} fill="transparent" />
                    {split ? (
                      <><circle cx={x(index)} cy={y(value.personal_cents)} r={activeIndex === index ? 5 : 3.5} fill="#475569" stroke="white" strokeWidth="1.5" /><circle cx={x(index)} cy={y(value.shared_cents)} r={activeIndex === index ? 5 : 3.5} fill="#4f46e5" stroke="white" strokeWidth="1.5" /></>
                    ) : <circle cx={x(index)} cy={y(value.total_cents)} r={activeIndex === index ? 5 : 3.5} fill="#4f46e5" stroke="white" strokeWidth="1.5" />}
                  </g>
                );
              })}
              {dateTicks.map((index) => <text key={values[index].period} x={x(index)} y="264" textAnchor="middle" fontSize="11" fill="#64748b">{dateLabel(values[index].period, granularity)}</text>)}
            </svg>
            {activeValue ? (
              <ChartTooltip
                className={`${activeIndex !== null && activeIndex > values.length / 2 ? "-translate-x-full -ml-2" : "ml-2"}`}
                style={{ left: `${x(activeIndex || 0) / 640 * 100}%`, top: `${Math.max(2, activeY / 280 * 100)}%` }}
                title={dateLabel(activeValue.period, granularity, true)}
                lines={split
                  ? [`Personal ${money(activeValue.personal_cents, currency)}`, `Shared ${money(activeValue.shared_cents, currency)}`, `${activeValue.transactions} transactions`]
                  : [`Total ${money(activeValue.total_cents, currency)}`, `${activeValue.transactions} transactions`]}
              />
            ) : null}
          </div>
        </div>
        <span aria-hidden="true" className="pointer-events-none absolute inset-y-0 right-0 w-8 rounded-r-lg bg-gradient-to-l from-white via-white/80 to-transparent sm:hidden" />
      </div>
      <p id="spend-chart-scroll-hint" className="mt-1 text-xs text-slate-500 sm:sr-only">Scroll horizontally to explore each date without shrinking chart labels.</p>
      <DataTable summary="View spending data table" headers={["Period", "Total", "Personal", "Shared", "Transactions"]} rows={values.map((value) => [dateLabel(value.period, granularity, true), money(value.total_cents, currency), money(value.personal_cents, currency), money(value.shared_cents, currency), String(value.transactions)])} />
    </div>
  );
}

function StackedSplit({ values, onSelect, currency }: { values: { personal: number; shared: number }; onSelect: (value: string) => void; currency: string }) {
  const magnitudeTotal = values.personal + values.shared;
  const personal = magnitudeTotal ? Math.round(values.personal / magnitudeTotal * 100) : 0;
  const shared = magnitudeTotal ? 100 - personal : 0;
  if (!magnitudeTotal) return <p className="py-8 text-center text-sm text-slate-600">No classified personal or shared spending in this period.</p>;
  return (
    <div className="space-y-3">
      <div className="flex h-11 overflow-hidden rounded-lg" aria-label={`Personal ${personal}%, Shared ${shared}%`}>
        <button type="button" onClick={() => onSelect("personal")} aria-label={`Filter Personal, ${money(values.personal, currency)}, ${personal}%`} className="bg-slate-600 text-xs font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white" style={{ width: `${personal}%` }}>{personal >= 15 ? `Personal ${personal}%` : null}</button>
        <button type="button" onClick={() => onSelect("shared")} aria-label={`Filter Shared, ${money(values.shared, currency)}, ${shared}%`} className="bg-indigo-600 text-xs font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white" style={{ width: `${shared}%` }}>{shared >= 15 ? `Shared ${shared}%` : null}</button>
      </div>
      <div className="flex flex-col justify-between gap-2 text-sm text-slate-700 sm:flex-row">
        <button type="button" onClick={() => onSelect("personal")} className="min-h-11 rounded-lg px-2 text-left hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"><Legend color="#475569" label={`Personal ${personal}% · ${money(values.personal, currency)}`} /></button>
        <button type="button" onClick={() => onSelect("shared")} className="min-h-11 rounded-lg px-2 text-left hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"><Legend color="#4f46e5" label={`Shared ${shared}% · ${money(values.shared, currency)}`} /></button>
      </div>
    </div>
  );
}

function CategoryTrend({ values, granularity, currency }: { values: { period: string; categories: Record<string, number> }[]; granularity: "day" | "week" | "month"; currency: string }) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const aggregate = new Map<string, number>();
  values.forEach((value) => Object.entries(value.categories).forEach(([name, amount]) => aggregate.set(name, (aggregate.get(name) || 0) + amount)));
  const grouped = groupSmallCategories([...aggregate].map(([name, amount_cents]) => ({ name, amount_cents })), 6);
  const names = grouped.map((item) => item.name);
  const normalized = values.map((value) => {
    const categories: Record<string, number> = {};
    let other = 0;
    Object.entries(value.categories).forEach(([name, amount]) => {
      if (names.includes(name) && name !== "Other") categories[name] = (categories[name] || 0) + amount;
      else other += amount;
    });
    if (names.includes("Other") && other) categories.Other = other;
    return { ...value, categories };
  });
  const totals = normalized.map((value) => Object.values(value.categories).reduce((sum, amount) => sum + amount, 0));
  const magnitudes = normalized.map((value) => Object.values(value.categories).reduce((sum, amount) => sum + amount, 0));
  const max = Math.max(1, ...magnitudes);
  const activeValue = activeIndex === null ? null : normalized[activeIndex];

  if (!values.length) return <p className="py-8 text-center text-sm text-slate-600">No category trend is available for this period.</p>;
  return (
    <div>
      <div className="relative hidden h-72 items-end gap-1.5 pt-16 sm:flex" role="img" aria-label="Stacked category composition over time" onPointerLeave={() => setActiveIndex(null)}>
        <span className="pointer-events-none absolute inset-x-0 bottom-6 top-16 flex flex-col justify-between" aria-hidden="true">{[100, 75, 50, 25, 0].map((tick) => <span key={tick} className="border-t border-dashed border-slate-200" />)}</span>
        {normalized.map((value, index) => {
          const label = [dateLabel(value.period, granularity, true), ...names.filter((name) => value.categories[name]).map((name) => `${name}: ${money(value.categories[name], currency)}`), `Total: ${money(totals[index], currency)}`].join(". ");
          return (
            <div key={value.period} data-category-period={value.period} tabIndex={0} onFocus={() => setActiveIndex(index)} onBlur={() => setActiveIndex(null)} onPointerEnter={() => setActiveIndex(index)} onClick={() => setActiveIndex(index)} onKeyDown={(event) => { if (event.key === "Escape") setActiveIndex(null); }} className="group relative z-10 flex h-full min-w-0 flex-1 cursor-crosshair flex-col justify-end rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-indigo-500" aria-label={label}>
              <span className={`flex w-full flex-col-reverse overflow-hidden rounded-t transition-opacity motion-reduce:transition-none ${activeIndex !== null && activeIndex !== index ? "opacity-45" : "opacity-100"}`} style={{ height: `${magnitudes[index] / max * 100}%` }}>
                {names.map((name) => <span key={name} className="transition-[filter] motion-reduce:transition-none group-hover:brightness-110" style={{ height: `${magnitudes[index] ? (value.categories[name] || 0) / magnitudes[index] * 100 : 0}%`, backgroundColor: categoryColor(name) }} />)}
              </span>
              <span className="mt-1 truncate text-center text-[10px] text-slate-500">{dateLabel(value.period, granularity)}</span>
            </div>
          );
        })}
        {activeValue && activeIndex !== null ? (
          <ChartTooltip
            className={activeIndex > normalized.length / 2 ? "-translate-x-full" : ""}
            style={{ left: `${normalized.length === 1 ? 50 : activeIndex / (normalized.length - 1) * 92 + 4}%`, top: 0 }}
            title={dateLabel(activeValue.period, granularity, true)}
            lines={[...names.filter((name) => activeValue.categories[name]).map((name) => `${name}: ${money(activeValue.categories[name], currency)}`), `Total: ${money(totals[activeIndex], currency)}`]}
          />
        ) : null}
      </div>
      <div className="space-y-2 sm:hidden" role="list" aria-label="Category trend summary">
        {normalized.map((value, index) => {
          const leader = names
            .map((name) => ({ name, amount: value.categories[name] || 0 }))
            .sort((left, right) => right.amount - left.amount)[0];
          return (
            <div key={value.period} role="listitem" className="flex items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-sm">
              <span><strong className="block text-slate-800">{dateLabel(value.period, granularity, true)}</strong><span className="text-xs text-slate-500">Top: {leader?.name || "No category"}</span></span>
              <span className="shrink-0 font-semibold tabular-nums text-slate-900">{money(totals[index], currency)}</span>
            </div>
          );
        })}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2">{names.map((name) => <Legend key={name} color={categoryColor(name)} label={name} />)}</div>
      <DataTable summary="View category trend data table" headers={["Period", ...names, "Total"]} rows={normalized.map((value, index) => [dateLabel(value.period, granularity, true), ...names.map((name) => money(value.categories[name] || 0, currency)), money(totals[index], currency)])} />
    </div>
  );
}

function Changes({
  items,
  incomplete = false,
}: {
  items: Insights["notable_changes"];
  incomplete?: boolean;
}) {
  if (incomplete) {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-dashed border-amber-300 bg-amber-50 px-4 py-5">
        <ChartNoAxesCombined className="h-5 w-5 text-amber-700" />
        <div>
          <p className="text-sm font-semibold text-amber-950">Comparison incomplete</p>
          <p className="text-xs text-amber-900">Change analysis uses only confirmed allocations.</p>
        </div>
      </div>
    );
  }
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {items.length ? items.map((item) => {
        const Icon = item.kind === "merchant" ? Store : item.direction === "down" ? ArrowDownRight : ArrowUpRight;
        return (
          <div key={`${item.kind}-${item.label}`} className="flex min-h-20 items-start gap-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-3">
            <span className="rounded-lg bg-white p-2 shadow-sm"><Icon className={`h-4 w-4 ${item.direction === "down" ? "text-sky-700" : item.kind === "merchant" ? "text-slate-600" : "text-indigo-700"}`} /></span>
            <div><p className="text-sm font-semibold text-slate-900">{item.label}</p><p className="mt-0.5 text-xs leading-5 text-slate-600">{item.detail}</p></div>
          </div>
        );
      }) : (
        <div className="col-span-full flex items-center gap-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-5">
          <ChartNoAxesCombined className="h-5 w-5 text-slate-500" />
          <div><p className="text-sm font-semibold text-slate-900">No major changes detected</p><p className="text-xs text-slate-600">Spending stayed within the material-change thresholds for this comparison.</p></div>
        </div>
      )}
    </div>
  );
}

function ChartTooltip({ title: heading, lines, className = "", style }: { title: string; lines: string[]; className?: string; style?: CSSProperties }) {
  return (
    <div role="tooltip" className={`pointer-events-none absolute z-30 w-max max-w-56 rounded-lg bg-slate-950 px-3 py-2 text-left text-xs text-white shadow-xl ${className}`} style={style}>
      <strong className="block font-semibold">{heading}</strong>
      {lines.map((line) => <span key={line} className="mt-0.5 block text-slate-200">{line}</span>)}
    </div>
  );
}

function DataTable({ summary, headers, rows }: { summary: string; headers: string[]; rows: string[][] }) {
  return (
    <details className="mt-4 rounded-lg border border-slate-200">
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between rounded-lg px-3 text-sm font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">
        {summary}<ChevronDown className="h-4 w-4" />
      </summary>
      <div className="overflow-x-auto border-t border-slate-200">
        <table className="w-full min-w-max text-left text-xs">
          <thead className="bg-slate-50 text-slate-600"><tr>{headers.map((header) => <th key={header} scope="col" className="px-3 py-2 font-semibold">{header}</th>)}</tr></thead>
          <tbody>{rows.map((row, rowIndex) => <tr key={`${row[0]}-${rowIndex}`} className="border-t border-slate-100">{row.map((cell, cellIndex) => cellIndex === 0 ? <th key={cellIndex} scope="row" className="px-3 py-2 font-medium text-slate-800">{cell}</th> : <td key={cellIndex} className="px-3 py-2 tabular-nums text-slate-700">{cell}</td>)}</tr>)}</tbody>
        </table>
      </div>
    </details>
  );
}

function DataNotice({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "warning" }) {
  return <p className={`rounded-xl border px-4 py-3 text-sm ${tone === "warning" ? "border-amber-200 bg-amber-50 text-amber-900" : "border-slate-200 bg-slate-50 text-slate-700"}`}>{children}</p>;
}

function Legend({ color, label }: { color: string; label: string }) {
  return <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: color }} aria-hidden="true" />{label}</span>;
}

function Select({ label, value, onChange, options, all, includeAll = true }: { label: string; value: string; onChange: (value: string) => void; options: string[]; all: string; includeAll?: boolean }) {
  return <label className="grid gap-1 text-xs font-semibold text-slate-700">{label}<select className="h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm sm:h-10" value={value} onChange={(event) => onChange(event.target.value)}>{includeAll ? <option value="">{all}</option> : null}{options.map((option) => <option key={option} value={option}>{title(option)}</option>)}</select></label>;
}

function DateField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <label className="grid gap-1 text-xs font-semibold text-slate-700">{label}<Input type="date" value={value} onChange={(event) => onChange(event.target.value)} /></label>;
}

function Toggle({ values, labels, value, onChange }: { values: string[]; labels: string[]; value: string; onChange: (value: string) => void }) {
  return <div className="flex rounded-lg bg-slate-100 p-1">{values.map((option, index) => <button key={option} type="button" onClick={() => onChange(option)} aria-pressed={value === option} className={`min-h-11 min-w-11 rounded-md px-2 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${value === option ? "bg-white text-indigo-700 shadow-sm" : "text-slate-600 hover:text-slate-950"}`}>{labels[index]}</button>)}</div>;
}

function Chip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return <span className="inline-flex min-h-11 items-center gap-1 rounded-full bg-indigo-50 py-1 pl-3 pr-1 text-xs font-medium text-indigo-800">{label}<button type="button" onClick={onRemove} className="inline-flex h-11 w-11 items-center justify-center rounded-full hover:bg-indigo-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500" aria-label={`Remove ${label}`}><X className="h-3 w-3" /></button></span>;
}

function CompactMessage({ title: heading, detail, action }: { title: string; detail: string; action?: () => void }) {
  return <div className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center"><CircleDollarSign className="mx-auto h-6 w-6 text-slate-500" /><h3 className="mt-2 font-semibold">{heading}</h3><p className="mt-1 text-sm text-slate-600">{detail}</p>{action ? <Button className="mt-3" variant="outline" onClick={action}><RefreshCw className="h-4 w-4" />Retry</Button> : null}</div>;
}

function DashboardSkeleton() {
  return <div className="space-y-5" role="status" aria-label="Loading insights"><span className="sr-only">Loading spending insights</span><div className="grid gap-3 lg:grid-cols-[1.35fr_2fr]"><div className="ui-skeleton h-40 rounded-xl" /><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{Array.from({ length: 4 }, (_, index) => <div key={index} className="ui-skeleton h-32 rounded-xl" />)}</div></div><div className="ui-skeleton h-44 rounded-xl" /><div className="ui-skeleton h-80 rounded-xl" /></div>;
}

function formatRange(start: string, end: string) {
  const startDate = new Date(`${start}T12:00:00`);
  const endDate = new Date(`${end}T12:00:00`);
  const sameYear = startDate.getFullYear() === endDate.getFullYear();
  const startLabel = startDate.toLocaleDateString("en-US", { month: "short", day: "numeric", year: sameYear ? undefined : "numeric" });
  const endLabel = endDate.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  return `${startLabel}–${endLabel}`;
}

function compactMoney(cents: number, currency: string) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(cents / 100);
}

function title(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function countComparison(current: number, previous: number) {
  const delta = current - previous;
  if (!delta) return { primary: "No change", secondary: null };
  return { primary: `${delta > 0 ? "+" : "−"}${Math.abs(delta)} vs previous period`, secondary: null };
}

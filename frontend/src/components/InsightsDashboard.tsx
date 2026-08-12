import { useEffect, useMemo, useState, type ReactNode } from "react";
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

type Summary = {
  total_cents: number;
  personal_cents: number;
  shared_cents: number;
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
    pending_review_cents: number;
    uncategorized_cents: number;
    pending_transactions_excluded: boolean;
  };
};

const presets: [DatePreset, string][] = [
  ["7d", "7D"],
  ["30d", "30D"],
  ["this_month", "This month"],
  ["last_month", "Last month"],
  ["90d", "90D"],
  ["this_quarter", "This quarter"],
  ["last_quarter", "Last quarter"],
  ["ytd", "YTD"],
  ["custom", "Custom"],
];

export function InsightsDashboard() {
  const initial = useMemo(() => dateRangeForPreset("30d"), []);
  const [preset, setPreset] = useState<DatePreset>("30d");
  const [start, setStart] = useState(initial.start);
  const [end, setEnd] = useState(initial.end);
  const [account, setAccount] = useState("");
  const [category, setCategory] = useState("");
  const [merchant, setMerchant] = useState("");
  const [merchantInput, setMerchantInput] = useState("");
  const [reviewType, setReviewType] = useState("all");
  const [basis, setBasis] = useState("card");
  const [data, setData] = useState<Insights | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [topCount, setTopCount] = useState(5);
  const [sharedMode, setSharedMode] = useState<"people" | "groups">("people");
  const [trendMode, setTrendMode] = useState<"total" | "split">("total");
  const [reloadToken, setReloadToken] = useState(0);

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
  }, [start, end, account, category, merchant, reviewType, basis, reloadToken]);

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
  }

  const filtered = Boolean(
    preset !== "30d" || account || category || merchant || reviewType !== "all" || basis !== "card",
  );
  const activeFilterCount = [account, category, merchant, reviewType !== "all", basis !== "card"].filter(Boolean).length;

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
      ) : data && data.summary.transaction_count ? (
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
  basis: string;
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
  setBasis: (value: string) => void;
  clearFilters: () => void;
};

function InsightsControls(props: ControlsProps) {
  return (
    <Card variant="primary" className="z-10 bg-white/95 backdrop-blur lg:sticky lg:top-2 lg:shadow-md">
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 gap-2 overflow-x-auto pb-1" aria-label="Date range presets">
            {presets.map(([value, label]) => (
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
          </div>
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

        <details className="group rounded-xl border border-slate-200 bg-slate-50/70">
          <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 rounded-xl px-3 text-sm font-semibold text-slate-800 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2">
            <span className="flex items-center gap-2">
              <Filter className="h-4 w-4 text-slate-500" />
              Refine view
              {props.activeFilterCount ? (
                <span className="rounded-full bg-indigo-100 px-2 py-0.5 text-xs text-indigo-800">
                  {props.activeFilterCount} active
                </span>
              ) : null}
            </span>
            <ChevronDown className="h-4 w-4 text-slate-500 transition-transform group-open:rotate-180" />
          </summary>
          <div className="border-t border-slate-200 p-3">
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
              <Select label="Account" value={props.account} onChange={props.setAccount} options={props.data?.accounts || []} all="All accounts" />
              <Select label="Category" value={props.category} onChange={props.setCategory} options={props.data?.categories || []} all="All categories" />
              <label className="grid gap-1 text-xs font-semibold text-slate-700">
                Merchant
                <Input value={props.merchantInput} onChange={(event) => props.setMerchantInput(event.target.value)} placeholder="All merchants" />
              </label>
              <Select label="Type" value={props.reviewType} onChange={props.setReviewType} options={["personal", "shared"]} all="All types" />
              <label className="grid gap-1 text-xs font-semibold text-slate-700">
                Spending basis
                <select className="h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm sm:h-10" value={props.basis} onChange={(event) => props.setBasis(event.target.value)}>
                  <option value="card">Card spend</option>
                  <option value="actual_share">My actual share</option>
                </select>
              </label>
            </div>
          </div>
        </details>

        {props.filtered ? (
          <div className="flex flex-wrap items-center gap-2" aria-label="Active insight filters">
            {props.preset !== "30d" ? <Chip label={`Range: ${presets.find(([value]) => value === props.preset)?.[1]}`} onRemove={() => props.choosePreset("30d")} /> : null}
            {props.account ? <Chip label={`Account: ${props.account}`} onRemove={() => props.setAccount("")} /> : null}
            {props.category ? <Chip label={`Category: ${props.category}`} onRemove={() => props.setCategory("")} /> : null}
            {props.merchant ? <Chip label={`Merchant: ${props.merchant}`} onRemove={() => { props.setMerchant(""); props.setMerchantInput(""); }} /> : null}
            {props.reviewType !== "all" ? <Chip label={`Type: ${title(props.reviewType)}`} onRemove={() => props.setReviewType("all")} /> : null}
            {props.basis !== "card" ? <Chip label="Basis: My actual share" onRemove={() => props.setBasis("card")} /> : null}
            <Button variant="ghost" size="sm" onClick={props.clearFilters}><X className="h-4 w-4" />Clear all</Button>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

type ContentProps = {
  data: Insights;
  basis: string;
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
  const currentRange = formatRange(data.range.start_date, data.range.end_date);
  const previousRange = formatRange(data.range.previous_start_date, data.range.previous_end_date);
  const sharedItems = props.sharedMode === "people" ? data.shared_people : data.shared_groups;

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700 sm:flex-row sm:items-center sm:justify-between">
        <span className="flex items-center gap-2 font-medium text-slate-900">
          <CalendarRange className="h-4 w-4 text-indigo-600" />
          {currentRange}
        </span>
        <span>Compared with {previousRange} · Displayed as USD; no currency conversion applied</span>
      </div>

      {props.basis === "actual_share" && data.data_quality.unknown_share_transactions ? (
        <DataNotice tone="warning">
          {data.data_quality.unknown_share_transactions} shared transaction{data.data_quality.unknown_share_transactions === 1 ? " has" : "s have"} no confirmed share and {data.data_quality.unknown_share_transactions === 1 ? "is" : "are"} excluded from this view.
        </DataNotice>
      ) : null}
      {data.data_quality.pending_review_cents >= 10_000 ? (
        <DataNotice>{money(data.data_quality.pending_review_cents)} is awaiting review and may change classified totals.</DataNotice>
      ) : null}
      {data.data_quality.pending_transactions_excluded ? (
        <p className="text-xs text-slate-500">Bank-pending transactions are excluded. Personal and Shared include only classified transactions.</p>
      ) : null}

      <section aria-labelledby="insights-overview-heading">
        <h2 id="insights-overview-heading" className="sr-only">Spending overview</h2>
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,2fr)]">
          <Kpi
            label="Total spend"
            value={data.summary.total_cents}
            previous={data.comparison.total_cents}
            featured
            comparisonRange={previousRange}
          />
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Kpi label="Personal" value={data.summary.personal_cents} previous={data.comparison.personal_cents} comparisonRange={previousRange} />
            <Kpi label="Shared" value={data.summary.shared_cents} previous={data.comparison.shared_cents} comparisonRange={previousRange} />
            <Kpi label="Transactions" raw={String(data.summary.transaction_count)} previousRaw={data.comparison.transaction_count} comparisonRange={previousRange} />
            <Kpi label="Average" value={data.summary.average_cents} previous={data.comparison.average_cents} comparisonRange={previousRange} />
          </div>
        </div>
      </section>

      <ChartCard title="What changed" eyebrow={`Compared with ${previousRange}`}>
        <Changes items={data.notable_changes} />
      </ChartCard>

      <ChartCard
        title="Spending over time"
        eyebrow={`${currentRange} · ${data.range.granularity} view`}
        action={<Toggle values={["total", "split"]} labels={["Total", "Personal / shared"]} value={props.trendMode} onChange={(value) => props.setTrendMode(value as "total" | "split")} />}
      >
        <LineChart values={data.trend} split={props.trendMode === "split"} granularity={data.range.granularity} />
      </ChartCard>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
        <ChartCard title="Where the money went" eyebrow="Category composition">
          <Donut items={data.category_breakdown} total={data.summary.total_cents} onSelect={props.setCategory} />
        </ChartCard>
        <ChartCard
          title="Top merchants"
          eyebrow="Largest totals in this view"
          action={<Toggle values={["5", "10"]} labels={["Top 5", "Top 10"]} value={String(props.topCount)} onChange={(value) => props.setTopCount(Number(value))} />}
        >
          <Bars
            items={data.merchant_breakdown.slice(0, props.topCount)}
            showCounts
            onSelect={(value) => { props.setMerchant(value); props.setMerchantInput(value); }}
          />
        </ChartCard>
      </div>

      {props.category ? (
        <ChartCard title={`${props.category} detail`} eyebrow="Source categories">
          <Bars items={data.subcategory_breakdown} onSelect={() => undefined} interactive={false} />
        </ChartCard>
      ) : null}

      <ChartCard title="Category trend" eyebrow="How category mix changed over time">
        <CategoryTrend values={data.category_trend} granularity={data.range.granularity} />
      </ChartCard>

      <section className="grid gap-5 lg:grid-cols-2" aria-label="Shared spending detail">
        <ChartCard title="Personal and shared" eyebrow="Classified card spend">
          <StackedSplit values={data.personal_shared} onSelect={props.setReviewType} />
        </ChartCard>
        <ChartCard
          title="Shared with"
          eyebrow={props.sharedMode === "people" ? "People in confirmed splits" : "Groups in confirmed splits"}
          action={<Toggle values={["people", "groups"]} labels={["People", "Groups"]} value={props.sharedMode} onChange={(value) => props.setSharedMode(value as "people" | "groups")} />}
        >
          {sharedItems.length ? (
            <Bars items={sharedItems.slice(0, 10)} onSelect={() => undefined} interactive={false} />
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
}: {
  label: string;
  value?: number;
  previous?: number;
  raw?: string;
  previousRaw?: number;
  featured?: boolean;
  comparisonRange: string;
}) {
  const current = value ?? Number(raw || 0);
  const prior = previous ?? previousRaw;
  const comparison = prior === undefined ? null : raw ? countComparison(current, prior) : comparisonText(current, prior);
  return (
    <Card variant={featured ? "command" : "primary"} className={featured ? "overflow-hidden" : ""}>
      <CardContent className={featured ? "flex min-h-40 flex-col justify-between p-5" : "min-h-32 p-4"}>
        <p className={`text-xs font-semibold uppercase tracking-[0.12em] ${featured ? "text-indigo-200" : "text-slate-600"}`}>{label}</p>
        <p className={`mt-2 font-semibold tabular-nums ${featured ? "text-4xl sm:text-5xl" : "text-2xl text-slate-950"}`}>{raw || money(value || 0)}</p>
        <div className={`mt-2 text-xs ${featured ? "text-slate-300" : "text-slate-600"}`}>
          {comparison ? (
            <p>{comparison.primary}{comparison.secondary ? ` ${comparison.secondary}` : ""}</p>
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
}: {
  items: Breakdown[];
  onSelect: (name: string) => void;
  comparison?: boolean;
  showCounts?: boolean;
  total?: number;
  interactive?: boolean;
}) {
  const max = Math.max(1, ...items.map((value) => Math.abs(value.amount_cents)));
  return (
    <div className="space-y-2" role="list">
      {items.map((item) => {
        const content = (
          <>
            <span className="flex justify-between gap-3 text-sm">
              <span className="truncate font-medium text-slate-800">{item.name}</span>
              <span className="shrink-0 tabular-nums text-slate-900">{money(item.amount_cents)}</span>
            </span>
            {showCounts && item.transaction_count ? <span className="mt-0.5 block text-xs text-slate-500">{item.transaction_count} transaction{item.transaction_count === 1 ? "" : "s"}</span> : null}
            <span className="mt-1.5 block h-2 overflow-hidden rounded-full bg-slate-100">
              <span className="block h-full rounded-full bg-indigo-600" style={{ width: `${Math.max(2, Math.abs(item.amount_cents) / max * 100)}%` }} />
            </span>
            {comparison && meaningfulChange(item.amount_cents, item.previous_amount_cents || 0, total) ? <span className="mt-1 block text-xs text-slate-500">{signedMoney(item.amount_cents - (item.previous_amount_cents || 0))} vs previous period</span> : null}
          </>
        );
        return interactive ? (
          <button type="button" role="listitem" key={item.name} onClick={() => onSelect(item.name)} className="block min-h-11 w-full rounded-lg p-2 text-left hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500" aria-label={`${item.name}, ${money(item.amount_cents)}. Filter by ${item.name}.`}>
            {content}
          </button>
        ) : (
          <div role="listitem" key={item.name} className="rounded-lg p-2">{content}</div>
        );
      })}
    </div>
  );
}

function Donut({ items, total, onSelect }: { items: Breakdown[]; total: number; onSelect: (name: string) => void }) {
  const grouped = groupSmallCategories(items);
  const groupedTotal = grouped.reduce((sum, item) => sum + item.amount_cents, 0);
  let offset = 0;
  const gradient = grouped.map((item) => {
    const start = offset;
    offset += groupedTotal ? item.amount_cents / groupedTotal * 100 : 0;
    return `${categoryColor(item.name)} ${start}% ${offset}%`;
  }).join(",");

  return (
    <div className="grid items-center gap-5 sm:grid-cols-[180px_1fr]">
      <div className="relative mx-auto h-40 w-40 rounded-full" style={{ background: `conic-gradient(${gradient})` }} role="img" aria-label={`Category composition. ${money(total)} total.`}>
        <div className="absolute inset-6 flex flex-col items-center justify-center rounded-full bg-white text-center">
          <strong className="text-lg tabular-nums text-slate-950">{money(total)}</strong>
          <span className="text-xs text-slate-500">Total</span>
        </div>
      </div>
      <div className="space-y-1">
        {grouped.map((item) => {
          const percentage = groupedTotal ? Math.round(item.amount_cents / groupedTotal * 100) : 0;
          const label = item.name === "Other" ? "Other categories" : item.name;
          const content = <><span className="flex min-w-0 items-center gap-2"><span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: categoryColor(item.name) }} /><span className="truncate">{label}</span></span><span className="shrink-0 tabular-nums text-slate-600">{money(item.amount_cents)} · {percentage}%</span></>;
          return item.name === "Other" ? (
            <div key={item.name} className="flex min-h-11 w-full items-center justify-between gap-3 rounded-lg px-2 text-sm">{content}</div>
          ) : (
            <button key={item.name} type="button" onClick={() => onSelect(item.name)} className="flex min-h-11 w-full items-center justify-between gap-3 rounded-lg px-2 text-sm hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500" aria-label={`${item.name}, ${money(item.amount_cents)}, ${percentage}%. Filter by category.`}>{content}</button>
          );
        })}
      </div>
    </div>
  );
}

function LineChart({ values, split, granularity }: { values: Trend[]; split: boolean; granularity: "day" | "week" | "month" }) {
  const ticks = axisTicks(Math.max(1, ...values.map((value) => value.total_cents)));
  const ceiling = ticks.at(-1) || 1;
  const left = 58;
  const right = 620;
  const top = 18;
  const bottom = 238;
  const x = (index: number) => values.length === 1 ? (left + right) / 2 : left + index / (values.length - 1) * (right - left);
  const y = (amount: number) => bottom - amount / ceiling * (bottom - top);
  const points = (key: keyof Trend) => values.map((value, index) => `${x(index)},${y(Number(value[key]))}`).join(" ");
  const dateTicks = xTickIndexes(values.length);

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-4 text-xs text-slate-600" aria-label="Chart legend">
        {split ? (
          <><Legend color="#475569" label="Personal" /><Legend color="#4f46e5" label="Shared" /></>
        ) : <Legend color="#4f46e5" label="Total spend" />}
      </div>
      <div className="min-h-[260px] w-full overflow-hidden">
        <svg viewBox="0 0 640 280" className="h-[260px] w-full sm:h-[300px]" role="img" aria-label={`Spend over time, ${split ? "personal and shared series" : "total series"}`}>
          {ticks.map((tick) => <g key={tick}><line x1={left} x2={right} y1={y(tick)} y2={y(tick)} stroke="#e2e8f0" /><text x={left - 8} y={y(tick) + 4} textAnchor="end" fontSize="11" fill="#64748b">{money(tick)}</text></g>)}
          {split ? (
            <><polyline points={points("personal_cents")} fill="none" stroke="#475569" strokeWidth="3" /><polyline points={points("shared_cents")} fill="none" stroke="#4f46e5" strokeWidth="3" /></>
          ) : <polyline points={points("total_cents")} fill="none" stroke="#4f46e5" strokeWidth="3" />}
          {values.map((value, index) => {
            const label = split
              ? `${dateLabel(value.period, granularity, true)}. Personal ${money(value.personal_cents)}. Shared ${money(value.shared_cents)}. Total ${money(value.total_cents)}. ${value.transactions} transactions.`
              : `${dateLabel(value.period, granularity, true)}. Total spend ${money(value.total_cents)}. ${value.transactions} transactions.`;
            return (
              <g key={value.period} role="img" tabIndex={0} aria-label={label} className="outline-none focus:[&_circle]:stroke-indigo-950 focus:[&_circle]:stroke-[3px]">
                {split ? (
                  <><circle cx={x(index)} cy={y(value.personal_cents)} r="4" fill="#475569" stroke="white" strokeWidth="1.5" /><circle cx={x(index)} cy={y(value.shared_cents)} r="4" fill="#4f46e5" stroke="white" strokeWidth="1.5" /></>
                ) : <circle cx={x(index)} cy={y(value.total_cents)} r="4" fill="#4f46e5" stroke="white" strokeWidth="1.5" />}
              </g>
            );
          })}
          {dateTicks.map((index) => <text key={values[index].period} x={x(index)} y="264" textAnchor="middle" fontSize="10" fill="#64748b">{dateLabel(values[index].period, granularity)}</text>)}
        </svg>
      </div>
      <DataTable summary="View spending data table" headers={["Period", "Total", "Personal", "Shared", "Transactions"]} rows={values.map((value) => [dateLabel(value.period, granularity, true), money(value.total_cents), money(value.personal_cents), money(value.shared_cents), String(value.transactions)])} />
    </div>
  );
}

function StackedSplit({ values, onSelect }: { values: { personal: number; shared: number }; onSelect: (value: string) => void }) {
  const total = values.personal + values.shared;
  const personal = total ? Math.round(values.personal / total * 100) : 0;
  const shared = total ? 100 - personal : 0;
  if (!total) return <p className="py-8 text-center text-sm text-slate-600">No classified personal or shared spending in this period.</p>;
  return (
    <div className="space-y-3">
      <div className="flex h-11 overflow-hidden rounded-lg" aria-label={`Personal ${personal}%, Shared ${shared}%`}>
        <button type="button" onClick={() => onSelect("personal")} aria-label={`Filter Personal, ${money(values.personal)}, ${personal}%`} className="bg-slate-600 text-xs font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white" style={{ width: `${personal}%` }}>{personal >= 15 ? `Personal ${personal}%` : null}</button>
        <button type="button" onClick={() => onSelect("shared")} aria-label={`Filter Shared, ${money(values.shared)}, ${shared}%`} className="bg-indigo-600 text-xs font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white" style={{ width: `${shared}%` }}>{shared >= 15 ? `Shared ${shared}%` : null}</button>
      </div>
      <div className="flex flex-col justify-between gap-2 text-sm text-slate-700 sm:flex-row">
        <button type="button" onClick={() => onSelect("personal")} className="min-h-11 rounded-lg px-2 text-left hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"><Legend color="#475569" label={`Personal ${personal}% · ${money(values.personal)}`} /></button>
        <button type="button" onClick={() => onSelect("shared")} className="min-h-11 rounded-lg px-2 text-left hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"><Legend color="#4f46e5" label={`Shared ${shared}% · ${money(values.shared)}`} /></button>
      </div>
    </div>
  );
}

function CategoryTrend({ values, granularity }: { values: { period: string; categories: Record<string, number> }[]; granularity: "day" | "week" | "month" }) {
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
  const max = Math.max(1, ...totals);

  if (!values.length) return <p className="py-8 text-center text-sm text-slate-600">No category trend is available for this period.</p>;
  return (
    <div>
      <div className="hidden h-72 items-end gap-1.5 sm:flex" role="img" aria-label="Stacked category composition over time">
        {normalized.map((value, index) => {
          const label = [dateLabel(value.period, granularity, true), ...names.filter((name) => value.categories[name]).map((name) => `${name}: ${money(value.categories[name])}`), `Total: ${money(totals[index])}`].join(". ");
          return (
            <div key={value.period} tabIndex={0} className="group flex h-full min-w-0 flex-1 flex-col justify-end rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-indigo-500" aria-label={label}>
              <span className="flex w-full flex-col-reverse overflow-hidden rounded-t" style={{ height: `${totals[index] / max * 100}%` }}>
                {names.map((name) => <span key={name} style={{ height: `${totals[index] ? (value.categories[name] || 0) / totals[index] * 100 : 0}%`, backgroundColor: categoryColor(name) }} />)}
              </span>
              <span className="mt-1 truncate text-center text-[10px] text-slate-500">{dateLabel(value.period, granularity)}</span>
            </div>
          );
        })}
      </div>
      <div className="space-y-2 sm:hidden" role="list" aria-label="Category trend summary">
        {normalized.map((value, index) => {
          const leader = names
            .map((name) => ({ name, amount: value.categories[name] || 0 }))
            .sort((left, right) => right.amount - left.amount)[0];
          return (
            <div key={value.period} role="listitem" className="flex items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-sm">
              <span><strong className="block text-slate-800">{dateLabel(value.period, granularity, true)}</strong><span className="text-xs text-slate-500">Top: {leader?.name || "No category"}</span></span>
              <span className="shrink-0 font-semibold tabular-nums text-slate-900">{money(totals[index])}</span>
            </div>
          );
        })}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2">{names.map((name) => <Legend key={name} color={categoryColor(name)} label={name} />)}</div>
      <DataTable summary="View category trend data table" headers={["Period", ...names, "Total"]} rows={normalized.map((value, index) => [dateLabel(value.period, granularity, true), ...names.map((name) => money(value.categories[name] || 0)), money(totals[index])])} />
    </div>
  );
}

function Changes({ items }: { items: Insights["notable_changes"] }) {
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

function Select({ label, value, onChange, options, all }: { label: string; value: string; onChange: (value: string) => void; options: string[]; all: string }) {
  return <label className="grid gap-1 text-xs font-semibold text-slate-700">{label}<select className="h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm sm:h-10" value={value} onChange={(event) => onChange(event.target.value)}><option value="">{all}</option>{options.map((option) => <option key={option} value={option}>{title(option)}</option>)}</select></label>;
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

function title(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function countComparison(current: number, previous: number) {
  const delta = current - previous;
  if (!delta) return { primary: "No change", secondary: null };
  return { primary: `${delta > 0 ? "+" : "−"}${Math.abs(delta)} vs previous period`, secondary: null };
}

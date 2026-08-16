import {
  AlertCircle,
  ArrowRight,
  BarChart3,
  CheckCircle2,
  ClipboardList,
  Inbox,
  PackageCheck,
  PlugZap,
  ReceiptText,
  Tags,
} from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import type {
  AgentEmptyStateBlock,
  AgentErrandSummaryBlock,
  AgentIntegrationStatusBlock,
  AgentNavigationBlock,
  AgentReceiptSummaryBlock,
  AgentReplenishmentSummaryBlock,
  AgentDealListBlock,
  AgentSpendingBreakdownItem,
  AgentSpendingSummaryBlock,
  AgentStructuredResponse,
  AgentTransactionListBlock,
} from "./contracts";
import { AgentProtocolError, parseAgentStructuredResponse } from "./validation";

export type AgentNavigationRequest = Pick<AgentNavigationBlock, "target_surface" | "entity">;

export function AgentResponseRenderer({
  response,
  onNavigate,
  onRetry,
}: {
  response: AgentStructuredResponse;
  onNavigate?: (request: AgentNavigationRequest) => void;
  onRetry?: () => void;
}) {
  try {
    parseAgentStructuredResponse(response);
  } catch (error) {
    const message =
      error instanceof AgentProtocolError
        ? error.message
        : "ExpenseOps cannot safely display this response.";
    return <UnsupportedResponse message={message} onRetry={onRetry} />;
  }

  return (
    <div className="space-y-3">
      {response.blocks.map((block, index) => {
        const key = block.block_id || `${block.type}-${index}`;
        switch (block.type) {
          case "text":
            return (
              <p key={key} className="whitespace-pre-wrap text-sm leading-6 text-slate-800 [overflow-wrap:anywhere]">
                {block.text}
              </p>
            );
          case "spending_summary":
            return (
              <SpendingSummaryCard
                key={key}
                block={block}
                onOpenInsights={onNavigate ? () => onNavigate({ target_surface: "expense_insights", entity: null }) : undefined}
              />
            );
          case "transaction_list":
            return <TransactionListCard key={key} block={block} onNavigate={onNavigate} />;
          case "replenishment_summary":
            return <ReplenishmentSummaryCard key={key} block={block} />;
          case "receipt_summary":
            return <ReceiptSummaryCard key={key} block={block} />;
          case "deal_list":
            return <DealListCard key={key} block={block} />;
          case "errand_summary":
            return <ErrandSummaryCard key={key} block={block} />;
          case "integration_status":
            return <IntegrationStatusCard key={key} block={block} />;
          case "error":
            return (
              <Card key={key} className="border-rose-200 bg-rose-50/70">
                <CardContent className="p-4 sm:p-4">
                  <div className="flex items-start gap-3">
                    <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-rose-600" aria-hidden="true" />
                    <div className="min-w-0 flex-1">
                      <p className="font-semibold text-rose-950">{block.title}</p>
                      <p className="mt-1 text-sm leading-5 text-rose-800 [overflow-wrap:anywhere]">{block.message}</p>
                      {block.retryable && onRetry ? (
                        <Button className="mt-3" size="sm" variant="outline" onClick={onRetry}>
                          Retry
                        </Button>
                      ) : null}
                    </div>
                  </div>
                </CardContent>
              </Card>
            );
          case "empty":
            return <AgentEmptyCard key={key} block={block} onNavigate={onNavigate} />;
          default:
            return <UnsupportedResponse key={key} onRetry={onRetry} />;
        }
      })}
    </div>
  );
}

function ReplenishmentSummaryCard({ block }: { block: AgentReplenishmentSummaryBlock }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardHeading icon={<PackageCheck className="size-5" aria-hidden="true" />} title={block.title} subtitle={`${block.items.length} of ${block.total_count}`} />
      </CardHeader>
      <CardContent className="space-y-3 pt-0">
        {block.items.length ? (
          <div className="divide-y divide-slate-200 overflow-hidden rounded-control border border-slate-200 bg-white">
            {block.items.map((item) => (
              <div key={item.public_id} className="flex min-h-14 items-start justify-between gap-3 px-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="font-semibold text-slate-900 [overflow-wrap:anywhere]">{item.name}</p>
                  <p className="mt-0.5 text-xs text-slate-600 [overflow-wrap:anywhere]">
                    {item.reason || replenishmentStateLabel(item.due_state)}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    Evidence: {humanize(item.confidence_level)} · {humanize(item.evidence_basis)}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    {[item.quantity ? `${item.quantity}${item.unit ? ` ${item.unit}` : ""}` : null, item.last_acquired_on ? `Last acquired ${formatDate(item.last_acquired_on)}` : "No confirmed purchase yet"].filter(Boolean).join(" · ")}
                  </p>
                </div>
                <Badge variant="secondary" className="shrink-0">{replenishmentStateLabel(item.due_state)}</Badge>
              </div>
            ))}
          </div>
        ) : null}
        {block.acquisition_history.length ? (
          <section aria-label="Recent confirmed purchases">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Recent confirmed purchases</h4>
            <ul className="mt-2 space-y-2 text-sm text-slate-700">
              {block.acquisition_history.map((item, index) => (
                <li key={`${item.acquired_on}-${index}`} className="rounded-control bg-slate-50 px-3 py-2 [overflow-wrap:anywhere]">
                  {formatDate(item.acquired_on)}{item.merchant ? ` · ${item.merchant}` : ""}
                  {item.quantity != null ? ` · ${item.quantity}${item.unit ? ` ${item.unit}` : ""}` : ""}
                </li>
              ))}
            </ul>
            {block.acquisition_history_truncated ? (
              <p className="mt-2 text-xs text-slate-500">
                Showing the {block.acquisition_history.length} most recent confirmed purchases.
              </p>
            ) : null}
          </section>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ReceiptSummaryCard({ block }: { block: AgentReceiptSummaryBlock }) {
  const receiptDate = block.purchased_at || block.ingested_at;
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardHeading
          icon={<ReceiptText className="size-5" aria-hidden="true" />}
          title={block.merchant || "Receipt"}
          subtitle={[receiptDate ? formatDateTime(receiptDate) : null, block.total_cents != null ? formatMinor(block.total_cents, block.currency_code) : null].filter(Boolean).join(" · ")}
        />
      </CardHeader>
      <CardContent className="space-y-3 pt-0">
        <div className="flex flex-wrap gap-2 text-xs">
          <Badge variant="secondary">{humanize(block.status)}</Badge>
          {block.transaction_linked ? <Badge variant="outline">Card transaction linked</Badge> : null}
          {block.unmatched_line_count ? <Badge variant="outline">{block.unmatched_line_count} need review</Badge> : null}
        </div>
        {block.items.length ? (
          <div className="divide-y divide-slate-200 overflow-hidden rounded-control border border-slate-200 bg-white">
            {block.items.map((item, index) => (
              <div key={`${item.name}-${index}`} className="flex min-h-12 items-start justify-between gap-3 px-3 py-2">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-slate-900 [overflow-wrap:anywhere]">{item.name}</p>
                  <p className="mt-0.5 text-xs text-slate-600 [overflow-wrap:anywhere]">
                    {[item.quantity != null ? `${item.quantity}${item.unit ? ` ${item.unit}` : ""}` : null, item.household_item_name ? `Matched to ${item.household_item_name}` : humanize(item.match_status)].filter(Boolean).join(" · ")}
                  </p>
                </div>
                {item.line_total_cents != null ? <span className="shrink-0 text-sm tabular-nums">{formatMinor(item.line_total_cents, block.currency_code)}</span> : null}
              </div>
            ))}
          </div>
        ) : null}
        {block.items_truncated ? <p className="text-xs text-slate-500">Showing {block.items.length} of {block.total_line_count} lines.</p> : null}
      </CardContent>
    </Card>
  );
}

function DealListCard({ block }: { block: AgentDealListBlock }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardHeading icon={<Tags className="size-5" aria-hidden="true" />} title={block.title} subtitle={`${block.deals.length} of ${block.total_count}`} />
      </CardHeader>
      <CardContent className="pt-0">
        <div className="space-y-2">
          {block.deals.map((deal) => (
            <article key={deal.public_id} className="rounded-control border border-slate-200 bg-white p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-slate-900 [overflow-wrap:anywhere]">{deal.merchant}</p>
                  <p className="mt-1 text-sm text-slate-700 [overflow-wrap:anywhere]">{deal.headline}</p>
                </div>
                {deal.saved ? (
                  <Badge className="shrink-0">Saved</Badge>
                ) : deal.relevant_to_need ? (
                  <Badge className="shrink-0">Relevant to a need</Badge>
                ) : (
                  <Badge variant="secondary" className="shrink-0">Current offer</Badge>
                )}
              </div>
              <p className="mt-2 text-xs text-slate-600 [overflow-wrap:anywhere]">
                {[deal.category, dealValueLabel(deal), deal.expires_at ? `Expires ${formatDateTime(deal.expires_at)}` : null, deal.promo_code ? `Code ${deal.promo_code}` : null].filter(Boolean).join(" · ")}
              </p>
              {deal.relevance_reasons.length ? <p className="mt-2 text-xs text-slate-600 [overflow-wrap:anywhere]">{deal.relevance_reasons.join(" · ")}</p> : null}
            </article>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function ErrandSummaryCard({ block }: { block: AgentErrandSummaryBlock }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardHeading icon={<ClipboardList className="size-5" aria-hidden="true" />} title={block.title} subtitle={`${block.errands.length} of ${block.total_count}`} />
      </CardHeader>
      <CardContent className="space-y-4 pt-0">
        {block.errands.length ? (
          <div className="divide-y divide-slate-200 overflow-hidden rounded-control border border-slate-200 bg-white">
            {block.errands.map((errand) => (
              <div key={errand.public_id} className="flex min-h-14 items-start gap-3 px-3 py-2.5">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-slate-500" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-slate-900 [overflow-wrap:anywhere]">{errand.title}</p>
                  <p className="mt-0.5 text-xs text-slate-600 [overflow-wrap:anywhere]">
                    {[humanize(errand.status), humanize(errand.priority), errand.due_on ? formatDate(errand.due_on) : null, errand.place_name || humanize(errand.place_resolution_status)].filter(Boolean).join(" · ")}
                  </p>
                </div>
                {errand.included_in_next_plan ? <Badge variant="outline" className="shrink-0">Next trip</Badge> : null}
              </div>
            ))}
          </div>
        ) : null}
        {block.plan ? (
          <section aria-label="Stored errand plan" className="rounded-control bg-indigo-50 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h4 className="text-sm font-semibold text-indigo-950">Stored plan</h4>
              <Badge variant={block.plan.is_stale ? "outline" : "secondary"}>{block.plan.is_stale ? "Needs refresh" : humanize(block.plan.status)}</Badge>
            </div>
            {block.plan.stale_reason ? <p className="mt-1 text-xs text-indigo-800 [overflow-wrap:anywhere]">{block.plan.stale_reason}</p> : null}
            <p className="mt-1 text-xs text-indigo-800">
              {[block.plan.travel_duration_minutes != null ? `${block.plan.travel_duration_minutes} min travel` : null, block.plan.estimated_stop_minutes ? `${block.plan.estimated_stop_minutes} min at stops` : null, block.plan.distance_meters != null ? formatDistance(block.plan.distance_meters) : null].filter(Boolean).join(" · ")}
            </p>
            <ol className="mt-3 space-y-2">
              {block.plan.stops.map((stop) => (
                <li key={stop.order} className="flex gap-2 text-sm text-indigo-950">
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-white font-semibold">{stop.order}</span>
                  <span className="min-w-0 [overflow-wrap:anywhere]">
                    {stop.place_name}{stop.errands.length ? ` · ${stop.errands.join(", ")}` : ""}
                    {stop.errands_truncated || stop.household_items_truncated ? " · More tasks not shown" : ""}
                  </span>
                </li>
              ))}
            </ol>
            {block.plan.stops_truncated ? (
              <p className="mt-2 text-xs text-indigo-800">
                Showing {block.plan.stops.length} of {block.plan.total_stop_count} planned stops.
              </p>
            ) : null}
          </section>
        ) : null}
      </CardContent>
    </Card>
  );
}

function IntegrationStatusCard({ block }: { block: AgentIntegrationStatusBlock }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardHeading icon={<PlugZap className="size-5" aria-hidden="true" />} title={block.title} />
      </CardHeader>
      <CardContent className="pt-0">
        <dl className="divide-y divide-slate-200 overflow-hidden rounded-control border border-slate-200 bg-white">
          {block.integrations.map((integration) => (
            <div key={integration.provider} className="flex min-h-14 items-center justify-between gap-3 px-3 py-2">
              <div className="min-w-0">
                <dt className="text-sm font-semibold text-slate-900 [overflow-wrap:anywhere]">{humanize(integration.provider)}</dt>
                {integration.message ? <dd className="mt-0.5 text-xs text-slate-600 [overflow-wrap:anywhere]">{integration.message}</dd> : null}
                {integration.last_successful_sync_at ? <dd className="mt-0.5 text-xs text-slate-500">Last sync {formatDateTime(integration.last_successful_sync_at)}</dd> : null}
              </div>
              <Badge variant={integration.status === "connected" || integration.status === "ready" ? "secondary" : "outline"} className="shrink-0">
                {humanize(integration.status)}
              </Badge>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

function CardHeading({ icon, title, subtitle }: { icon: ReactNode; title: string; subtitle?: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <CardTitle className="text-base [overflow-wrap:anywhere]">{title}</CardTitle>
        {subtitle ? <p className="mt-1 text-xs text-slate-600 [overflow-wrap:anywhere]">{subtitle}</p> : null}
      </div>
      <span className="flex size-10 shrink-0 items-center justify-center rounded-control bg-indigo-100 text-indigo-700">{icon}</span>
    </div>
  );
}

function SpendingSummaryCard({
  block,
  onOpenInsights,
}: {
  block: AgentSpendingSummaryBlock;
  onOpenInsights?: () => void;
}) {
  return (
    <Card className="overflow-hidden border-indigo-200 bg-gradient-to-br from-white to-indigo-50/70">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-indigo-700">
              {formatDateRange(block.start_date, block.end_date)}
            </p>
            <CardTitle className="mt-1 text-base">{block.title}</CardTitle>
          </div>
          <span className="flex size-10 shrink-0 items-center justify-center rounded-control bg-indigo-100 text-indigo-700">
            <BarChart3 className="size-5" aria-hidden="true" />
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 pt-0">
        <div>
          <p className="text-3xl font-semibold tracking-tight text-slate-950">
            {formatMinor(block.total_cents, block.currency_code)}
          </p>
          {block.previous_total_cents !== null ? (
            <p className="mt-1 text-xs text-slate-600">
              Prior period {formatMinor(block.previous_total_cents, block.currency_code)}
              {typeof block.change_percent === "number"
                ? ` · ${formatPercentage(block.change_percent)}`
                : ""}
            </p>
          ) : null}
        </div>
        {block.highlights.length ? (
          <ul className="grid gap-1.5 text-sm text-slate-700">
            {block.highlights.map((highlight) => (
              <li key={highlight} className="flex gap-2">
                <span className="mt-2 size-1.5 shrink-0 rounded-full bg-indigo-500" aria-hidden="true" />
                <span className="min-w-0 [overflow-wrap:anywhere]">{highlight}</span>
              </li>
            ))}
          </ul>
        ) : null}
        <Breakdown title="Top categories" rows={block.top_categories} currency={block.currency_code} />
        <Breakdown title="Top merchants" rows={block.top_merchants} currency={block.currency_code} />
        {onOpenInsights ? (
          <Button variant="ghost" size="sm" onClick={onOpenInsights}>
            Open Insights <ArrowRight className="size-4" aria-hidden="true" />
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}

function Breakdown({
  title,
  rows,
  currency,
}: {
  title: string;
  rows: AgentSpendingBreakdownItem[];
  currency: string;
}) {
  if (!rows.length) return null;
  return (
    <section aria-label={title}>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h4>
      <div className="mt-2 divide-y divide-slate-200 rounded-control border border-slate-200 bg-white/80">
        {rows.slice(0, 5).map((row) => (
          <div key={row.name} className="flex min-h-11 items-center justify-between gap-3 px-3 py-2 text-sm">
            <span className="min-w-0 truncate font-medium text-slate-800">{row.name}</span>
            <span className="shrink-0 tabular-nums text-slate-600">
              {formatMinor(row.amount_cents, currency)}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function TransactionListCard({
  block,
  onNavigate,
}: {
  block: AgentTransactionListBlock;
  onNavigate?: (request: AgentNavigationRequest) => void;
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <CardTitle className="text-base">{block.title}</CardTitle>
            <p className="mt-1 text-xs text-slate-600">
              Showing {block.transactions.length} of {block.total_count}
            </p>
          </div>
          <ReceiptText className="size-5 text-indigo-600" aria-hidden="true" />
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="divide-y divide-slate-200 overflow-hidden rounded-control border border-slate-200 bg-white">
          {block.transactions.map((transaction) => {
            const content = (
              <>
                <span className="min-w-0 flex-1 text-left">
                  <span className="block truncate text-sm font-semibold text-slate-900">
                    {transaction.merchant}
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-slate-600">
                    {[transaction.occurred_on ? formatDate(transaction.occurred_on) : null, transaction.category, transaction.pending ? "Pending" : transaction.status]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </span>
                <span className="shrink-0 text-sm font-semibold tabular-nums text-slate-900">
                  {formatMinor(transaction.amount_cents, transaction.currency_code)}
                </span>
              </>
            );
            return onNavigate ? (
              <button
                key={transaction.public_id}
                type="button"
                className="flex min-h-14 w-full items-center gap-3 px-3 py-2 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-600"
                onClick={() =>
                  onNavigate({
                    target_surface: "expense_activity",
                    entity: { kind: "transaction", public_id: transaction.public_id },
                  })
                }
                aria-label={`Open activity for ${transaction.merchant}`}
              >
                {content}
              </button>
            ) : (
              <div key={transaction.public_id} className="flex min-h-14 items-center gap-3 px-3 py-2">
                {content}
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

function AgentEmptyCard({
  block,
  onNavigate,
}: {
  block: AgentEmptyStateBlock;
  onNavigate?: (request: AgentNavigationRequest) => void;
}) {
  return (
    <Card className="border-dashed bg-slate-50/70">
      <CardContent className="flex flex-col items-center p-5 text-center sm:p-5">
        <span className="flex size-11 items-center justify-center rounded-control border border-slate-200 bg-white text-slate-500">
          <Inbox className="size-5" aria-hidden="true" />
        </span>
        <p className="mt-3 font-semibold text-slate-900">{block.title}</p>
        <p className="mt-1 max-w-sm text-sm leading-5 text-slate-600 [overflow-wrap:anywhere]">{block.message}</p>
        {block.suggested_navigation && onNavigate ? (
          <Button
            className="mt-3"
            size="sm"
            variant="outline"
            onClick={() => onNavigate(block.suggested_navigation!)}
          >
            {block.suggested_navigation.label}
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}

function UnsupportedResponse({ message, onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <Card className="border-amber-200 bg-amber-50/70">
      <CardContent className="p-4 sm:p-4">
        <div className="flex items-start gap-3">
          <AlertCircle className="mt-0.5 size-5 shrink-0 text-amber-700" aria-hidden="true" />
          <div>
            <p className="font-semibold text-amber-950">Response unavailable</p>
            <p className="mt-1 text-sm text-amber-800 [overflow-wrap:anywhere]">
              {message || "ExpenseOps cannot safely display this Agent response yet."}
            </p>
            {onRetry ? (
              <Button className="mt-3" size="sm" variant="outline" onClick={onRetry}>
                Reload conversation
              </Button>
            ) : null}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function formatMinor(amountCents: number, currencyCode: string): string {
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: currencyCode,
    }).format(amountCents / 100);
  } catch {
    return `${currencyCode} ${(amountCents / 100).toFixed(2)}`;
  }
}

function formatDate(value: string): string {
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(date);
}

function formatDateTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : new Intl.DateTimeFormat("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      }).format(parsed);
}

function humanize(value: string): string {
  return value
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function replenishmentStateLabel(value: string): string {
  if (value === "likely_due") return "Likely due";
  if (value === "probably_due") return "Probably due";
  if (value === "not_due") return "Not due";
  return "Learning";
}

function dealValueLabel(deal: AgentDealListBlock["deals"][number]): string | null {
  if (deal.percent_off != null) return `${deal.percent_off}% off`;
  if (deal.amount_off_cents != null && deal.currency_code) {
    return `${formatMinor(deal.amount_off_cents, deal.currency_code)} off`;
  }
  return null;
}

function formatDistance(distanceMeters: number): string {
  if (distanceMeters < 1_000) return `${distanceMeters} m`;
  return `${(distanceMeters / 1_609.344).toFixed(1)} mi`;
}

function formatDateRange(start: string, end: string): string {
  return `${formatDate(start)} – ${formatDate(end)}`;
}

function formatPercentage(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

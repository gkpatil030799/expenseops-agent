import { AlertCircle, ArrowRight, BarChart3, Inbox, ReceiptText } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import type {
  AgentEmptyStateBlock,
  AgentNavigationBlock,
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

function formatDateRange(start: string, end: string): string {
  return `${formatDate(start)} – ${formatDate(end)}`;
}

function formatPercentage(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

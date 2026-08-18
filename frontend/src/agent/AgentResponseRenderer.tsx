import {
  AlertCircle,
  ArrowRight,
  BarChart3,
  Brain,
  CheckCircle2,
  ClipboardList,
  Coffee,
  Clock3,
  Inbox,
  Info,
  ListChecks,
  LoaderCircle,
  PackageCheck,
  PlugZap,
  ReceiptText,
  Tags,
  ShieldCheck,
} from "lucide-react";
import { useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { classificationCategoryLabel } from "@/classificationActivity";

import type {
  AgentAttentionPriority,
  AgentAttentionSummaryBlock,
  AgentActionConfirmationBlock,
  AgentClassificationActivityBlock,
  AgentEmptyStateBlock,
  AgentErrandSummaryBlock,
  AgentIntegrationStatusBlock,
  AgentLifestyleSummaryBlock,
  AgentNavigationBlock,
  AgentReceiptSummaryBlock,
  AgentReplenishmentSummaryBlock,
  AgentDealListBlock,
  AgentSpendingBreakdownItem,
  AgentSpendingSummaryBlock,
  AgentStructuredResponse,
  AgentTransactionListBlock,
} from "./contracts";
import {
  isAgentNavigationRequest,
  type AgentNavigationRequest,
} from "./pageContext";
import {
  AGENT_RESPONSE_UNAVAILABLE_MESSAGE,
  parseAgentStructuredResponse,
} from "./validation";

export type { AgentNavigationRequest } from "./pageContext";

export function AgentResponseRenderer({
  response,
  onNavigate,
  onRetry,
  onConfirmAction,
  onCancelAction,
  isActionPending,
}: {
  response: AgentStructuredResponse;
  onNavigate?: (request: AgentNavigationRequest) => void;
  onRetry?: () => void;
  onConfirmAction?: (
    block: AgentActionConfirmationBlock,
  ) => Promise<AgentActionConfirmationBlock>;
  onCancelAction?: (
    block: AgentActionConfirmationBlock,
  ) => Promise<AgentActionConfirmationBlock>;
  isActionPending?: (proposalId: string) => boolean;
}) {
  try {
    parseAgentStructuredResponse(response);
  } catch {
    return <UnsupportedResponse onRetry={onRetry} />;
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
          case "lifestyle_summary":
            return <LifestyleSummaryCard key={key} block={block} />;
          case "transaction_list":
            return <TransactionListCard key={key} block={block} onNavigate={onNavigate} />;
          case "replenishment_summary":
            return <ReplenishmentSummaryCard key={key} block={block} onNavigate={onNavigate} />;
          case "receipt_summary":
            return <ReceiptSummaryCard key={key} block={block} onNavigate={onNavigate} />;
          case "deal_list":
            return <DealListCard key={key} block={block} onNavigate={onNavigate} />;
          case "errand_summary":
            return <ErrandSummaryCard key={key} block={block} onNavigate={onNavigate} />;
          case "integration_status":
            return <IntegrationStatusCard key={key} block={block} onNavigate={onNavigate} />;
          case "classification_activity_summary":
            return <ClassificationActivityCard key={key} block={block} onNavigate={onNavigate} />;
          case "attention_summary":
            return <AttentionSummaryCard key={key} block={block} onNavigate={onNavigate} />;
          case "navigation":
            return <NavigationCard key={key} block={block} onNavigate={onNavigate} />;
          case "action_confirmation":
            return (
              <ActionConfirmationCard
                key={key}
                block={block}
                pending={isActionPending?.(block.proposal_id) || false}
                onConfirm={onConfirmAction}
                onCancel={onCancelAction}
              />
            );
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

function ActionConfirmationCard({
  block,
  pending,
  onConfirm,
  onCancel,
}: {
  block: AgentActionConfirmationBlock;
  pending: boolean;
  onConfirm?: (
    block: AgentActionConfirmationBlock,
  ) => Promise<AgentActionConfirmationBlock>;
  onCancel?: (
    block: AgentActionConfirmationBlock,
  ) => Promise<AgentActionConfirmationBlock>;
}) {
  const [error, setError] = useState("");
  const awaiting = block.status === "awaiting_confirmation";
  const statusCopy: Record<AgentActionConfirmationBlock["status"], string> = {
    awaiting_confirmation: "Review the exact effect below. Nothing changes until you confirm.",
    confirmed: "Confirmed. ExpenseOps is preparing the action.",
    executing: "ExpenseOps is applying the confirmed action.",
    completed: "Completed. The confirmed action was applied.",
    cancelled: "Cancelled. Nothing was changed.",
    expired: "This confirmation expired. Ask the Agent to prepare a new one.",
    failed: "This action could not be completed safely. Nothing else will be retried automatically.",
    ambiguous: "ExpenseOps could not verify the outcome safely. Review the transaction before retrying.",
  };

  async function decide(
    callback: ((value: AgentActionConfirmationBlock) => Promise<AgentActionConfirmationBlock>) | undefined,
  ) {
    if (!callback || pending || !awaiting) return;
    setError("");
    try {
      await callback(block);
    } catch {
      setError("The proposal changed or could not be applied safely. Reload the conversation.");
    }
  }

  return (
    <Card
      data-testid="agent-action-confirmation"
      className={block.status === "completed" ? "border-emerald-300 bg-emerald-50/40" : "border-amber-300 bg-amber-50/40"}
    >
      <CardHeader className="pb-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-control bg-amber-100 text-amber-800">
            <ShieldCheck className="size-5" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-amber-800">
              Confirmation required
            </p>
            <CardTitle className="mt-1 text-base [overflow-wrap:anywhere]">{block.title}</CardTitle>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 pt-0">
        <p className="text-sm leading-5 text-slate-800 [overflow-wrap:anywhere]">{block.summary}</p>
        <dl className="divide-y divide-slate-200 overflow-hidden rounded-control border border-slate-200 bg-white">
          {block.details.map((detail) => (
            <div key={detail.label} className="flex min-h-11 items-start justify-between gap-3 px-3 py-2">
              <dt className="text-xs font-medium text-slate-500">{detail.label}</dt>
              <dd className="min-w-0 text-right text-sm font-semibold text-slate-900 [overflow-wrap:anywhere]">{detail.value}</dd>
            </div>
          ))}
        </dl>
        <p className="text-xs leading-5 text-slate-600" role="status">{statusCopy[block.status]}</p>
        {awaiting && onConfirm && onCancel ? (
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button
              type="button"
              variant="outline"
              className="min-h-11"
              disabled={pending}
              onClick={() => void decide(onCancel)}
            >
              {block.cancel_label}
            </Button>
            <Button
              type="button"
              className="min-h-11"
              disabled={pending}
              onClick={() => void decide(onConfirm)}
            >
              {pending ? (
                <LoaderCircle className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              ) : null}
              {pending ? "Applying…" : block.confirm_label}
            </Button>
          </div>
        ) : null}
        {error ? <p className="text-sm text-rose-700" role="alert">{error}</p> : null}
      </CardContent>
    </Card>
  );
}

function ReplenishmentSummaryCard({ block, onNavigate }: { block: AgentReplenishmentSummaryBlock; onNavigate?: (request: AgentNavigationRequest) => void }) {
  return (
    <Card>
      <CardHeader className="p-4 pb-3 sm:p-4 sm:pb-3">
        <CardHeading icon={<PackageCheck className="size-5" aria-hidden="true" />} title={block.title} subtitle={`${block.items.length} of ${block.total_count}`} />
      </CardHeader>
      <CardContent className="space-y-3 px-4 pb-4 pt-0 sm:px-4 sm:pb-4">
        {block.items.length ? (
          <div className="divide-y divide-slate-200 overflow-hidden rounded-control border border-slate-200 bg-white">
            {block.items.map((item) => (
              <div
                key={item.public_id}
                className="grid min-h-14 min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-x-2 gap-y-2 px-3 py-2.5"
                data-testid="agent-replenishment-row"
              >
                <div className="col-span-2 min-w-0">
                  <p className="break-words font-semibold text-slate-900">{item.name}</p>
                  <p className="mt-0.5 break-words text-xs text-slate-600">
                    {item.reason || replenishmentStateLabel(item.due_state)}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    Evidence: {humanize(item.confidence_level)} · {humanize(item.evidence_basis)}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    {[item.quantity ? `${item.quantity}${item.unit ? ` ${item.unit}` : ""}` : null, item.last_acquired_on ? `Last acquired ${formatDate(item.last_acquired_on)}` : "No confirmed purchase yet"].filter(Boolean).join(" · ")}
                  </p>
                </div>
                <Badge variant="secondary" className="min-w-0 max-w-full justify-self-start whitespace-normal text-left">
                  {replenishmentStateLabel(item.due_state)}
                </Badge>
                <EntityNavigationButton label={`Open ${item.name}`} request={{ target_surface: "household_staples", entity: { kind: "household_item", public_id: item.public_id } }} onNavigate={onNavigate} />
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

function ReceiptSummaryCard({ block, onNavigate }: { block: AgentReceiptSummaryBlock; onNavigate?: (request: AgentNavigationRequest) => void }) {
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
                  {item.parent_category ? (
                    <p className="mt-0.5 text-xs leading-5 text-slate-600 [overflow-wrap:anywhere]">
                      {`Category: ${classificationCategoryLabel(item.parent_category)}${item.subcategory ? ` / ${item.subcategory}` : ""}`}
                      {` · Concept: ${item.concept || "Not assigned"}`}
                      {` · Activity: ${item.activity_type ? humanize(item.activity_type) : "Uncertain"}`}
                      {` · Replenishment: ${item.replenishment_eligibility ? humanize(item.replenishment_eligibility) : "Uncertain"}`}
                    </p>
                  ) : null}
                </div>
                {item.line_total_cents != null ? <span className="shrink-0 text-sm tabular-nums">{formatMinor(item.line_total_cents, block.currency_code)}</span> : null}
              </div>
            ))}
          </div>
        ) : null}
        {block.items_truncated ? <p className="text-xs text-slate-500">Showing {block.items.length} of {block.total_line_count} lines.</p> : null}
        <EntityNavigationButton label="Open receipt details" request={{ target_surface: "household_receipts", entity: { kind: "receipt", public_id: block.public_id } }} onNavigate={onNavigate} />
      </CardContent>
    </Card>
  );
}

function DealListCard({ block, onNavigate }: { block: AgentDealListBlock; onNavigate?: (request: AgentNavigationRequest) => void }) {
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
              <EntityNavigationButton label={`Open ${deal.merchant} deal`} request={{ target_surface: "deals", entity: { kind: "deal", public_id: deal.public_id } }} onNavigate={onNavigate} />
            </article>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function ErrandSummaryCard({ block, onNavigate }: { block: AgentErrandSummaryBlock; onNavigate?: (request: AgentNavigationRequest) => void }) {
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
                <EntityNavigationButton label={`Open ${errand.title}`} request={{ target_surface: "household_errands", entity: { kind: "errand", public_id: errand.public_id } }} onNavigate={onNavigate} />
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

function IntegrationStatusCard({ block, onNavigate }: { block: AgentIntegrationStatusBlock; onNavigate?: (request: AgentNavigationRequest) => void }) {
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
              <EntityNavigationButton label={`Open ${humanize(integration.provider)}`} request={{ target_surface: "integrations", entity: { kind: "integration", public_id: integration.provider } }} onNavigate={onNavigate} />
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

const ATTENTION_PRESENTATION: {
  priority: AgentAttentionPriority;
  label: string;
  icon: ReactNode;
  iconClassName: string;
}[] = [
  {
    priority: "action_required",
    label: "Action required",
    icon: <AlertCircle className="size-4" aria-hidden="true" />,
    iconClassName: "bg-rose-100 text-rose-700",
  },
  {
    priority: "time_sensitive",
    label: "Time sensitive",
    icon: <Clock3 className="size-4" aria-hidden="true" />,
    iconClassName: "bg-amber-100 text-amber-800",
  },
  {
    priority: "useful_to_know",
    label: "Useful to know",
    icon: <Info className="size-4" aria-hidden="true" />,
    iconClassName: "bg-indigo-100 text-indigo-700",
  },
];

function ClassificationActivityCard({
  block,
  onNavigate,
}: {
  block: AgentClassificationActivityBlock;
  onNavigate?: (request: AgentNavigationRequest) => void;
}) {
  const decisionCount = block.counts.transactions + block.counts.receipt_items;
  const isRange = block.block_version === "1.1";
  const stapleCandidates = isRange ? block.staple_candidates : [];
  const aliases = isRange ? block.aliases : [];
  return (
    <Card
      data-testid="agent-classification-activity"
      className="min-w-0 overflow-hidden border-indigo-200 bg-gradient-to-br from-white to-indigo-50/60"
    >
      <CardHeader className="pb-3">
        <CardHeading
          icon={<Brain className="size-5" aria-hidden="true" />}
          title={block.title}
          subtitle={`${isRange ? formatDateRange(block.start_date, block.end_date) : formatDate(block.activity_date)} · ${block.timezone}`}
        />
      </CardHeader>
      <CardContent className="min-w-0 space-y-4 pt-0">
        {block.view === "summary" ? (
          <div
            className="grid gap-2"
            style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 7rem), 1fr))" }}
            aria-label="Classification activity totals"
          >
            <ClassificationMetric label="Decisions" value={decisionCount} />
            <ClassificationMetric label="Categories used" value={block.counts.categories} />
            <ClassificationMetric label="New categories" value={block.counts.new_categories} />
            <ClassificationMetric label="Receipt matches" value={block.counts.receipt_matches} />
            {isRange ? <ClassificationMetric label="Staple candidates" value={block.counts.staple_candidates} /> : null}
            {isRange ? <ClassificationMetric label="Aliases learned" value={block.counts.aliases} /> : null}
            <ClassificationMetric label="Optional review" value={block.counts.uncertain} />
          </div>
        ) : null}

        <ClassificationSection title="Transactions" visible={block.transactions.length > 0}>
          {block.transactions.map((row) => (
            <ClassificationRow
              key={row.decision_public_id}
              title={row.merchant}
              detail={`${classificationCategoryLabel(row.parent_category)} · ${row.subcategory || humanize(row.activity_type)}`}
              badge={humanize(row.decision_state)}
              navigation={row.source_available ? {
                target_surface: "expense_activity",
                entity: { kind: "transaction", public_id: row.public_id },
              } : null}
              onNavigate={onNavigate}
            />
          ))}
        </ClassificationSection>

        <ClassificationSection title="Receipt items" visible={block.receipt_items.length > 0}>
          {block.receipt_items.map((row) => (
            <ClassificationRow
              key={row.decision_public_id}
              title={row.name}
              detail={`${row.merchant ? `${row.merchant} · ` : ""}${classificationCategoryLabel(row.parent_category)}${row.household_item_name ? ` · Tracked as ${row.household_item_name}` : ""}`}
              badge={humanize(row.decision_state)}
              navigation={row.source_available ? {
                target_surface: "household_receipts",
                entity: { kind: "receipt", public_id: row.receipt_public_id },
              } : null}
              onNavigate={onNavigate}
            />
          ))}
        </ClassificationSection>

        <ClassificationSection title="Categories used" visible={block.categories.length > 0}>
          {block.categories.map((row) => (
            <ClassificationRow
              key={row.parent_category}
              title={classificationCategoryLabel(row.parent_category)}
              detail={`${row.transaction_count} transaction${row.transaction_count === 1 ? "" : "s"} · ${row.receipt_item_count} receipt item${row.receipt_item_count === 1 ? "" : "s"}`}
              badge={String(row.total_count)}
            />
          ))}
        </ClassificationSection>

        <ClassificationSection title="New categories created" visible={block.new_categories.length > 0}>
          {block.new_categories.map((row) => (
            <ClassificationRow
              key={row.decision_public_id}
              title={row.subcategory}
              detail={`${classificationCategoryLabel(row.parent_category)} · From ${humanize(row.source_type)} evidence`}
              badge={humanize(row.authority)}
            />
          ))}
        </ClassificationSection>

        <ClassificationSection title="Receipt matches" visible={block.receipt_matches.length > 0}>
          {block.receipt_matches.map((row) => (
            <ClassificationRow
              key={`${row.receipt_public_id}-${row.attempted_at}`}
              title={row.merchant || "Receipt"}
              detail={classificationMatchLabel(row.status)}
              badge={`${Math.round(row.confidence * 100)}%`}
              navigation={{
                target_surface: "household_receipts",
                entity: { kind: "receipt", public_id: row.receipt_public_id },
              }}
              onNavigate={onNavigate}
            />
          ))}
        </ClassificationSection>

        <ClassificationSection title="New household staples" visible={block.new_household_items.length > 0}>
          {block.new_household_items.map((row) => (
            <ClassificationRow
              key={row.public_id}
              title={row.name}
              detail={`${classificationCategoryLabel(row.parent_category)} · ${cadenceLabel(row)}`}
              badge={humanize(row.replenishment_eligibility)}
              navigation={{
                target_surface: "household_staples",
                entity: { kind: "household_item", public_id: row.public_id },
              }}
              onNavigate={onNavigate}
            />
          ))}
        </ClassificationSection>

        <ClassificationSection title="Staple candidates" visible={stapleCandidates.length > 0}>
          {stapleCandidates.map((row) => (
            <ClassificationRow
              key={row.decision_public_id}
              title={row.name}
              detail={`${row.merchant ? `${row.merchant} · ` : ""}${classificationCategoryLabel(row.parent_category)} · ${classificationCandidateHouseholdItemLabel(row)} · ${Math.round(row.confidence * 100)}% confidence`}
              badge={classificationLearningStateLabel(row.learning_state)}
              navigation={classificationCandidateNavigation(row)}
              onNavigate={onNavigate}
            />
          ))}
        </ClassificationSection>

        <ClassificationSection title="Merchant aliases learned" visible={aliases.length > 0}>
          {aliases.map((row) => (
            <ClassificationRow
              key={row.public_id}
              title={`${row.raw_pattern} → ${row.concept}`}
              detail={`${row.merchant ? `${row.merchant} · ` : ""}${classificationCategoryLabel(row.parent_category)} · ${Math.round(row.confidence * 100)}% confidence`}
              badge={row.active ? "Learned alias" : "Inactive alias"}
            />
          ))}
        </ClassificationSection>

        <ClassificationSection title="Cadence learned" visible={block.cadence_updates.length > 0}>
          {block.cadence_updates.map((row) => (
            <ClassificationRow
              key={`${row.public_id}-${row.activity_at}`}
              title={row.name}
              detail={`${cadenceLabel(row)} · ${humanize(row.cadence_source)}`}
              badge={`${Math.round(row.cadence_confidence * 100)}%`}
              navigation={{
                target_surface: "household_staples",
                entity: { kind: "household_item", public_id: row.public_id },
              }}
              onNavigate={onNavigate}
            />
          ))}
        </ClassificationSection>

        <ClassificationSection title="Optional review" visible={block.uncertain.length > 0}>
          {block.uncertain.map((row) => (
            <ClassificationRow
              key={`${row.kind}-${row.public_id}-${row.observed_at}`}
              title={row.label}
              detail={`${row.reasons.map(humanize).join(" · ")}. ExpenseOps left this correctable instead of guessing.`}
              badge={row.confidence_band ? humanize(row.confidence_band) : humanize(row.kind)}
              navigation={classificationUncertainNavigation(row)}
              onNavigate={onNavigate}
            />
          ))}
        </ClassificationSection>

        {block.truncated_sections.length ? (
          <p className="text-xs leading-5 text-slate-600">
            This bounded view has more {block.truncated_sections.map(humanize).join(", ")} activity.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ClassificationMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="min-w-0 rounded-control border border-slate-200 bg-white/90 p-3">
      <p className="break-words text-xs leading-4 text-slate-600">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums text-slate-950">{value}</p>
    </div>
  );
}

function ClassificationSection({
  title,
  visible,
  children,
}: {
  title: string;
  visible: boolean;
  children: ReactNode;
}) {
  if (!visible) return null;
  return (
    <section aria-label={title}>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-600">{title}</h4>
      <div className="mt-2 divide-y divide-slate-200 overflow-hidden rounded-control border border-slate-200 bg-white/90">
        {children}
      </div>
    </section>
  );
}

function ClassificationRow({
  title,
  detail,
  badge,
  navigation,
  onNavigate,
}: {
  title: string;
  detail: string;
  badge: string;
  navigation?: AgentNavigationRequest | null;
  onNavigate?: (request: AgentNavigationRequest) => void;
}) {
  return (
    <div
      className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-x-2 gap-y-2 px-3 py-2.5"
      data-testid="agent-classification-row"
    >
      <div className="col-span-2 min-w-0">
        <p className="break-words text-sm font-semibold text-slate-900">{title}</p>
        <p className="mt-0.5 break-words text-xs leading-5 text-slate-600">{detail}</p>
      </div>
      <Badge variant="secondary" className="min-w-0 max-w-full justify-self-start whitespace-normal text-left">
        {badge}
      </Badge>
      {navigation ? (
        <EntityNavigationButton label={`Open ${title}`} request={navigation} onNavigate={onNavigate} />
      ) : null}
    </div>
  );
}

function classificationMatchLabel(status: AgentClassificationActivityBlock["receipt_matches"][number]["status"]): string {
  if (status === "auto_matched") return "Matched to a card transaction";
  if (status === "ambiguous") return "More than one possible card transaction; optional review";
  return "No eligible card transaction found; receipt processing continued";
}

function cadenceLabel(row: AgentClassificationActivityBlock["cadence_updates"][number]): string {
  if (row.cadence_days != null) return `About every ${row.cadence_days} days`;
  if (row.cadence_min_days != null && row.cadence_max_days != null) {
    return `About every ${row.cadence_min_days}–${row.cadence_max_days} days`;
  }
  return "Cadence still learning";
}

function classificationUncertainNavigation(
  row: AgentClassificationActivityBlock["uncertain"][number],
): AgentNavigationRequest | null {
  if (row.kind === "transaction") {
    return { target_surface: "expense_activity", entity: { kind: "transaction", public_id: row.public_id } };
  }
  const receiptId = row.kind === "receipt_item" ? row.receipt_public_id : row.public_id;
  return receiptId
    ? { target_surface: "household_receipts", entity: { kind: "receipt", public_id: receiptId } }
    : null;
}

function classificationCandidateNavigation(
  row: Extract<AgentClassificationActivityBlock, { block_version: "1.1" }>["staple_candidates"][number],
): AgentNavigationRequest | null {
  if (row.household_item_public_id) {
    return {
      target_surface: "household_staples",
      entity: { kind: "household_item", public_id: row.household_item_public_id },
    };
  }
  return row.source_available
    ? {
        target_surface: "household_receipts",
        entity: { kind: "receipt", public_id: row.receipt_public_id },
      }
    : null;
}

function classificationCandidateHouseholdItemLabel(
  row: Extract<AgentClassificationActivityBlock, { block_version: "1.1" }>["staple_candidates"][number],
): string {
  return row.created_household_item ? "Household item created" : "No household item created";
}

function classificationLearningStateLabel(
  value: Extract<AgentClassificationActivityBlock, { block_version: "1.1" }>["staple_candidates"][number]["learning_state"],
): string {
  if (value === "candidate") return "Learning · not due";
  if (value === "learning") return "Learning";
  return "Tracked";
}

function AttentionSummaryCard({
  block,
  onNavigate,
}: {
  block: AgentAttentionSummaryBlock;
  onNavigate?: (request: AgentNavigationRequest) => void;
}) {
  const unavailable = joinLabels(block.unavailable_domains.map(attentionDomainLabel));
  const coverage = block.status === "partial"
    ? `Partial result. Checked ${block.checked_domains.length} ExpenseOps ${block.checked_domains.length === 1 ? "area" : "areas"}. Couldn't check ${unavailable} right now.`
    : `All selected checks completed. Checked ${block.checked_domains.length} ExpenseOps ${block.checked_domains.length === 1 ? "area" : "areas"}.`;

  return (
    <Card
      data-testid="agent-attention-summary"
      className="min-w-0 overflow-hidden border-indigo-200 bg-gradient-to-br from-white to-indigo-50/60"
    >
      <CardHeader className="pb-3">
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-indigo-700">
              Cross-domain summary
            </p>
            <CardTitle className="mt-1 text-base [overflow-wrap:anywhere]">{block.title}</CardTitle>
          </div>
          <span className="flex size-10 shrink-0 items-center justify-center rounded-control bg-indigo-100 text-indigo-700">
            <ListChecks className="size-5" aria-hidden="true" />
          </span>
        </div>
      </CardHeader>
      <CardContent className="min-w-0 space-y-4 pt-0">
        {ATTENTION_PRESENTATION.map((presentation) => {
          const items = block.items.filter((item) => item.priority === presentation.priority);
          if (!items.length) return null;
          return (
            <section key={presentation.priority} aria-label={presentation.label}>
              <h4 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-600">
                <span className={`flex size-7 shrink-0 items-center justify-center rounded-control ${presentation.iconClassName}`}>
                  {presentation.icon}
                </span>
                {presentation.label}
              </h4>
              <ul className="mt-2 divide-y divide-slate-200 overflow-hidden rounded-control border border-slate-200 bg-white/90">
                {items.map((item) => (
                  <li key={`${item.priority}-${item.domain}`} className="min-w-0 px-3 py-3">
                    <div className="flex min-w-0 items-start gap-3">
                      <Badge variant="secondary" className="min-w-7 shrink-0 justify-center tabular-nums" aria-label={`${item.count} ${item.title}`}>
                        {item.count}
                      </Badge>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold text-slate-900 [overflow-wrap:anywhere]">{item.title}</p>
                        {item.detail ? (
                          <p className="mt-1 text-xs leading-5 text-slate-600 [overflow-wrap:anywhere]">{item.detail}</p>
                        ) : null}
                      </div>
                    </div>
                    <AttentionNavigationButton navigation={item.navigation} onNavigate={onNavigate} />
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
        {!block.items.length ? (
          <p className="rounded-control border border-dashed border-slate-200 bg-white/70 px-3 py-3 text-sm leading-5 text-slate-600">
            {block.items_truncated
              ? "No attention items appeared in the bounded results; additional matching records may exist."
              : "No attention items were found in the areas that completed."}
          </p>
        ) : null}
        {block.items_truncated ? (
          <p className="text-xs text-slate-600">
            This bounded summary may not include every matching item or record.
          </p>
        ) : null}
        <div className={block.status === "partial" ? "rounded-control border border-amber-200 bg-amber-50 px-3 py-2.5" : "border-t border-slate-200 pt-3"}>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={block.status === "partial" ? "outline" : "secondary"}>
              {block.status === "partial" ? "Partial" : "Checks complete"}
            </Badge>
            <p className="min-w-0 flex-1 text-xs leading-5 text-slate-600 [overflow-wrap:anywhere]" aria-label="Attention coverage">
              {coverage}
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function AttentionNavigationButton({
  navigation,
  onNavigate,
}: {
  navigation?: AgentNavigationBlock | null;
  onNavigate?: (request: AgentNavigationRequest) => void;
}) {
  if (!navigation || !onNavigate) return null;
  const request: AgentNavigationRequest = {
    target_surface: navigation.target_surface,
    entity: navigation.entity || null,
  };
  if (!isAgentNavigationRequest(request)) return null;
  return (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      className="mt-2 min-h-11 min-w-0 w-full justify-between sm:w-auto"
      onClick={() => onNavigate(request)}
      aria-label={navigation.label}
    >
      <span className="min-w-0 truncate">{navigation.label}</span>
      <ArrowRight className="size-4 shrink-0" aria-hidden="true" />
    </Button>
  );
}

function attentionDomainLabel(domain: AgentAttentionSummaryBlock["checked_domains"][number]): string {
  if (domain === "spending") return "spending";
  if (domain === "lifestyle") return "lifestyle";
  if (domain === "transactions") return "transactions";
  if (domain === "replenishment") return "household needs";
  if (domain === "receipts") return "receipts";
  if (domain === "deals") return "deals";
  if (domain === "errands") return "errands";
  if (domain === "classification") return "classification activity";
  return "integrations";
}

function joinLabels(values: string[]): string {
  if (values.length < 2) return values[0] || "the requested area";
  if (values.length === 2) return `${values[0]} and ${values[1]}`;
  return `${values.slice(0, -1).join(", ")}, and ${values.at(-1)}`;
}

function NavigationCard({
  block,
  onNavigate,
}: {
  block: AgentNavigationBlock;
  onNavigate?: (request: AgentNavigationRequest) => void;
}) {
  const request: AgentNavigationRequest = {
    target_surface: block.target_surface,
    entity: block.entity || null,
  };
  if (!onNavigate || !isAgentNavigationRequest(request)) return null;
  return (
    <Button
      type="button"
      variant="outline"
      className="min-h-11 min-w-0 w-full justify-between"
      onClick={() => onNavigate(request)}
      aria-label={block.label}
    >
      <span className="min-w-0 truncate">{block.label}</span><ArrowRight className="size-4 shrink-0" aria-hidden="true" />
    </Button>
  );
}

function EntityNavigationButton({
  label,
  request,
  onNavigate,
}: {
  label: string;
  request: AgentNavigationRequest;
  onNavigate?: (request: AgentNavigationRequest) => void;
}) {
  if (!onNavigate || !isAgentNavigationRequest(request)) return null;
  return (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      className="min-h-11 shrink-0"
      onClick={() => onNavigate(request)}
      aria-label={label}
    >
      Open<ArrowRight className="size-4" aria-hidden="true" />
    </Button>
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

function LifestyleSummaryCard({ block }: { block: AgentLifestyleSummaryBlock }) {
  const basisLabel = block.spend_basis === "actual_share" ? "My actual share" : "Card spend";
  return (
    <Card className="overflow-hidden border-amber-200 bg-gradient-to-br from-white to-amber-50/60">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-amber-800">
              {formatDateRange(block.start_date, block.end_date)}
            </p>
            <CardTitle className="mt-1 text-base [overflow-wrap:anywhere]">{block.title}</CardTitle>
          </div>
          <span className="flex size-10 shrink-0 items-center justify-center rounded-control bg-amber-100 text-amber-800">
            <Coffee className="size-5" aria-hidden="true" />
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 pt-0">
        <div
          className="grid gap-2"
          style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 8rem), 1fr))" }}
        >
          <LifestyleMetric label={basisLabel} value={formatMinor(block.total_cents, block.currency_code)} />
          <LifestyleMetric label="Purchases" value={String(block.transaction_count)} />
          <LifestyleMetric label="Average check" value={formatMinor(block.average_cents, block.currency_code)} />
        </div>
        {block.previous_total_cents !== null && block.previous_transaction_count !== null ? (
          <p className="text-xs text-slate-600">
            Prior comparable period: {formatMinor(block.previous_total_cents, block.currency_code)} across {block.previous_transaction_count} purchase{block.previous_transaction_count === 1 ? "" : "s"}.
          </p>
        ) : null}
        <p className="text-xs text-slate-600">
          Personal {formatMinor(block.personal_cents, block.currency_code)} · Shared {formatMinor(block.shared_cents, block.currency_code)} · Unreviewed {formatMinor(block.unreviewed_cents, block.currency_code)}
        </p>
        <p className="text-xs text-slate-600">
          Weekdays {block.weekday_count} · Weekends {block.weekend_count}
        </p>
        {block.credits_cents ? (
          <p className="text-xs font-medium text-emerald-700">Credits reported separately {formatMinor(block.credits_cents, block.currency_code)}</p>
        ) : null}
        {block.unknown_share_transactions || block.previous_unknown_share_transactions || block.unknown_credit_share_transactions || block.previous_unknown_credit_share_transactions ? (
          <p className="text-xs font-medium text-amber-900">Actual-share figures use confirmed allocations only; some shared purchases or credits were omitted.</p>
        ) : null}
        {block.uncertain_transaction_count ? (
          <p className="text-xs font-medium text-slate-700">{block.uncertain_transaction_count} Food & Dining transaction{block.uncertain_transaction_count === 1 ? " was" : "s were"} left unclassified rather than guessed.</p>
        ) : null}
        {block.observations.length ? (
          <ul className="space-y-1.5 text-sm text-slate-700">
            {block.observations.map((observation) => <li key={observation} className="[overflow-wrap:anywhere]">• {observation}</li>)}
          </ul>
        ) : null}
        <LifestyleBreakdown title="Activity mix" rows={block.activities} currency={block.currency_code} />
        <LifestyleBreakdown title="Top merchants" rows={block.top_merchants} currency={block.currency_code} />
      </CardContent>
    </Card>
  );
}

function LifestyleMetric({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0 rounded-control bg-white/80 p-3"><p className="text-xs font-medium text-slate-600">{label}</p><p className="mt-1 break-words text-xl font-semibold tabular-nums text-slate-950">{value}</p></div>;
}

function LifestyleBreakdown({ title, rows, currency }: { title: string; rows: AgentLifestyleSummaryBlock["top_merchants"]; currency: string }) {
  if (!rows.length) return null;
  return (
    <section aria-label={title}>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h4>
      <ol className="mt-2 divide-y divide-slate-200 rounded-control border border-slate-200 bg-white/80" aria-label={title}>
        {rows.map((row, index) => (
          <li key={row.name} className="min-w-0 px-3 py-2 text-sm">
            <p className="break-words font-medium text-slate-800"><span className="text-slate-500">{index + 1}.</span> {humanize(row.name)}</p>
            <div className="mt-1 flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-xs text-slate-600">
              <span>{purchaseCountLabel(row.transaction_count)} · {formatSharePercentage(row.percentage)} of spend</span>
              <span className="max-w-full break-words font-medium tabular-nums text-slate-700">{formatMinor(row.amount_cents, currency)}</span>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function SpendingSummaryCard({
  block,
  onOpenInsights,
}: {
  block: AgentSpendingSummaryBlock;
  onOpenInsights?: () => void;
}) {
  const focus = block.focus ?? "summary";
  const showSummary = focus === "summary" || focus === "comparison" || focus === "change_explanation";
  const showCategories = focus !== "top_merchants";
  const showMerchants = focus !== "top_categories";
  return (
    <Card data-testid="agent-spending-summary" className="overflow-hidden border-indigo-200 bg-gradient-to-br from-white to-indigo-50/70">
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
        {showSummary ? <div>
          <p className="text-xs font-medium text-slate-600">
            {block.spend_basis === "actual_share" ? "My actual share" : "Total card spend"}
          </p>
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
          {block.credits_cents ? (
            <p className="mt-1 text-xs font-medium text-emerald-700">
              {block.spend_basis === "actual_share" ? "Attributable credits" : "Card credits"} reported separately {formatMinor(block.credits_cents, block.currency_code)}
            </p>
          ) : null}
          {block.previous_credits_cents ? (
            <p className="mt-1 text-xs text-slate-600">
              {block.spend_basis === "actual_share" ? "Prior attributable credits" : "Prior card credits"} {formatMinor(block.previous_credits_cents, block.currency_code)}
            </p>
          ) : null}
          {block.unknown_share_transactions || block.previous_unknown_share_transactions ? (
            <p className="mt-2 text-xs font-medium text-amber-800">
              {block.unknown_share_transactions
                ? `${block.unknown_share_transactions} current shared purchase${block.unknown_share_transactions === 1 ? " was" : "s were"} omitted because your allocation is unavailable. `
                : ""}
              {block.previous_unknown_share_transactions
                ? `${block.previous_unknown_share_transactions} prior shared purchase${block.previous_unknown_share_transactions === 1 ? " was" : "s were"} omitted from the comparison because your allocation is unavailable.`
                : ""}
            </p>
          ) : null}
          {block.unknown_credit_share_transactions || block.previous_unknown_credit_share_transactions ? (
            <p className="mt-2 text-xs font-medium text-amber-800">
              {block.unknown_credit_share_transactions
                ? `${block.unknown_credit_share_transactions} current shared credit${block.unknown_credit_share_transactions === 1 ? "" : "s"} ${block.unknown_credit_share_transactions === 1 ? "was" : "were"} omitted because your allocation is unavailable. `
                : ""}
              {block.previous_unknown_credit_share_transactions
                ? `${block.previous_unknown_credit_share_transactions} prior shared credit${block.previous_unknown_credit_share_transactions === 1 ? "" : "s"} ${block.previous_unknown_credit_share_transactions === 1 ? "was" : "were"} omitted from the comparison because your allocation is unavailable.`
                : ""}
            </p>
          ) : null}
        </div> : null}
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
        {showCategories ? <Breakdown title="Top categories" rows={block.top_categories} currency={block.currency_code} /> : null}
        {showMerchants ? <Breakdown title="Top merchants" rows={block.top_merchants} currency={block.currency_code} /> : null}
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
      <ol className="mt-2 divide-y divide-slate-200 rounded-control border border-slate-200 bg-white/80" aria-label={title}>
        {rows.map((row, index) => (
          <li key={row.name} className="min-w-0 px-3 py-2 text-sm">
            <p className="break-words font-medium text-slate-800"><span className="text-slate-500">{index + 1}.</span> {row.name}</p>
            <div className="mt-1 flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-xs text-slate-600">
              <span>{purchaseCountLabel(row.transaction_count)} · {formatSharePercentage(row.percentage)} of spend</span>
              <span className="max-w-full break-words font-medium tabular-nums text-slate-700">
                {formatMinor(row.amount_cents, currency)}
              </span>
            </div>
          </li>
        ))}
      </ol>
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
  const navigationRequest: AgentNavigationRequest | null = block.suggested_navigation
    ? {
        target_surface: block.suggested_navigation.target_surface,
        entity: block.suggested_navigation.entity || null,
      }
    : null;
  return (
    <Card className="border-dashed bg-slate-50/70">
      <CardContent className="flex flex-col items-center p-5 text-center sm:p-5">
        <span className="flex size-11 items-center justify-center rounded-control border border-slate-200 bg-white text-slate-500">
          <Inbox className="size-5" aria-hidden="true" />
        </span>
        <p className="mt-3 font-semibold text-slate-900">{block.title}</p>
        <p className="mt-1 max-w-sm text-sm leading-5 text-slate-600 [overflow-wrap:anywhere]">{block.message}</p>
        {block.suggested_navigation && navigationRequest && onNavigate && isAgentNavigationRequest(navigationRequest) ? (
          <Button
            className="mt-3"
            size="sm"
            variant="outline"
            onClick={() => onNavigate(navigationRequest)}
          >
            {block.suggested_navigation.label}
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}

function UnsupportedResponse({ onRetry }: { onRetry?: () => void }) {
  return (
    <Card className="border-amber-200 bg-amber-50/70">
      <CardContent className="p-4 sm:p-4">
        <div className="flex items-start gap-3">
          <AlertCircle className="mt-0.5 size-5 shrink-0 text-amber-700" aria-hidden="true" />
          <div>
            <p className="font-semibold text-amber-950">Response unavailable</p>
            <p className="mt-1 text-sm text-amber-800 [overflow-wrap:anywhere]">
              {AGENT_RESPONSE_UNAVAILABLE_MESSAGE}
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

function purchaseCountLabel(value: number): string {
  return `${value} purchase${value === 1 ? "" : "s"}`;
}

function formatSharePercentage(value: number): string {
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(value)}%`;
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

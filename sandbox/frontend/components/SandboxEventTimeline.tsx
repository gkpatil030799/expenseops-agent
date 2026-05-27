import { ChevronDown, Copy, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";

import type { SandboxEvent } from "../types";
import {
  Button,
  Card,
  CopyButton,
  formatDateTime,
  humanizeEventType,
  shortId,
  StatusPill,
  statusIcon,
} from "./sandboxUi";

const highlighted = new Set([
  "sandbox_e2e_started",
  "sandbox_e2e_completed",
  "sandbox_e2e_failed",
  "sandbox_transaction_create_succeeded",
  "sandbox_webhook_fire_succeeded",
  "plaid_webhook_received",
  "plaid_transactions_sync_completed",
  "plaid_transactions_sync_failed",
  "telegram_notification_send_succeeded",
  "telegram_notification_skipped_duplicate",
  "sandbox_telegram_send_succeeded",
  "sandbox_telegram_send_skipped_duplicate",
  "sandbox_integrity_error",
]);

type Props = {
  events: SandboxEvent[];
  traceFilter: string;
  eventFilter: string;
  autoRefresh: boolean;
  onTraceFilter: (value: string) => void;
  onEventFilter: (value: string) => void;
  onAutoRefresh: (value: boolean) => void;
  onRefresh: () => void;
};

export function SandboxEventTimeline({
  events,
  traceFilter,
  eventFilter,
  autoRefresh,
  onTraceFilter,
  onEventFilter,
  onAutoRefresh,
  onRefresh,
}: Props) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const eventTypes = useMemo(
    () => Array.from(new Set(events.map((event) => event.event_type))).sort(),
    [events],
  );
  const filtered = events.filter(
    (event) =>
      (!traceFilter || event.trace_id.includes(traceFilter)) &&
      (!eventFilter || event.event_type === eventFilter),
  );

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 p-4">
        <div>
          <h2 className="text-base font-semibold text-slate-950">Event timeline</h2>
          <p className="mt-1 text-sm text-slate-500">
            Latest 100 events across Plaid webhooks, sync, and notification claims.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <input
            value={traceFilter}
            onChange={(event) => onTraceFilter(event.target.value)}
            placeholder="Filter trace_id"
            className="rounded-md border border-slate-200 px-3 py-2 text-sm outline-none focus:border-slate-400 focus:ring-2 focus:ring-slate-100"
          />
          <select
            value={eventFilter}
            onChange={(event) => onEventFilter(event.target.value)}
            className="rounded-md border border-slate-200 px-3 py-2 text-sm outline-none focus:border-slate-400 focus:ring-2 focus:ring-slate-100"
          >
            <option value="">All events</option>
            {eventTypes.map((type) => (
              <option key={type} value={type}>
                {humanizeEventType(type)}
              </option>
            ))}
          </select>
          <label className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(event) => onAutoRefresh(event.target.checked)}
            />
            Auto-refresh
          </label>
          <Button variant="secondary" onClick={onRefresh}>
            <RefreshCw className="h-4 w-4" />
            Refresh
          </Button>
        </div>
      </div>

      <div className="divide-y divide-slate-100">
        {filtered.length ? (
          filtered.map((event) => {
            const open = expanded[event.id];
            const Icon = statusIcon(event.status);
            const payloadJson = JSON.stringify(event.payload || {}, null, 2);
            return (
              <article
                key={event.id}
                className={`p-4 transition ${
                  highlighted.has(event.event_type) ? "bg-emerald-50/25" : "bg-white"
                }`}
              >
                <div className="grid gap-3 lg:grid-cols-[180px_minmax(0,1fr)_160px] lg:items-start">
                  <div className="text-xs text-slate-500">{formatDateTime(event.created_at)}</div>
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-100 text-slate-600">
                        <Icon className="h-3.5 w-3.5" />
                      </span>
                      <button
                        type="button"
                        onClick={() => setExpanded((current) => ({ ...current, [event.id]: !open }))}
                        className="inline-flex items-center gap-1 text-left text-sm font-semibold text-slate-950"
                      >
                        {humanizeEventType(event.event_type)}
                        <ChevronDown className={`h-3.5 w-3.5 transition ${open ? "rotate-180" : ""}`} />
                      </button>
                      <StatusPill value={event.status} />
                      {event.payload?.webhook_code ? (
                        <span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600">
                          {String(event.payload.webhook_code)}
                        </span>
                      ) : null}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                      <span className="font-mono">{event.event_type}</span>
                      <span>trace {shortId(event.trace_id, 10)}</span>
                      <CopyButton value={event.trace_id} label="Copy trace ID" />
                    </div>
                    {event.message ? (
                      <p className="mt-2 text-sm text-slate-600">{event.message}</p>
                    ) : null}
                    {open ? (
                      <div className="mt-3 rounded-lg border border-slate-200 bg-slate-950 p-3">
                        <div className="mb-2 flex justify-end">
                          <button
                            type="button"
                            onClick={() => void navigator.clipboard?.writeText(payloadJson)}
                            className="inline-flex items-center gap-1 rounded-md bg-white/10 px-2 py-1 text-xs font-medium text-slate-200 hover:bg-white/15"
                          >
                            <Copy className="h-3.5 w-3.5" />
                            Copy payload
                          </button>
                        </div>
                        <pre className="max-h-72 overflow-auto text-xs leading-5 text-slate-100">
                          {payloadJson}
                        </pre>
                      </div>
                    ) : null}
                  </div>
                  <div className="space-y-1 text-xs text-slate-500 lg:text-right">
                    <div>request {shortId(event.plaid_request_id)}</div>
                    <div>item {shortId(event.plaid_item_id)}</div>
                  </div>
                </div>
              </article>
            );
          })
        ) : (
          <div className="p-8 text-center text-sm text-slate-500">
            No events match the current filters.
          </div>
        )}
      </div>
    </Card>
  );
}

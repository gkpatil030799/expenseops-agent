import { CalendarDays, CreditCard, Flame, Search } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import type { SandboxCreateTransactionResponse, SandboxTransactionRequest } from "../types";
import { Button, Card, CopyButton, shortId, StatusPill } from "./sandboxUi";

const presets = [
  ["Coffee", "ExpenseOps Sandbox Coffee", "12.34"],
  ["Campus Pantry", "Campus Pantry Sandbox Groceries", "48.20"],
  ["Sugar Monkeys", "Sugar Monkeys Sandbox Dinner", "86.50"],
  ["Uber", "Uber Sandbox Ride", "6.33"],
  ["Netflix", "Netflix Sandbox Subscription", "19.57"],
  ["OpenAI", "OpenAI Sandbox Subscription", "20.00"],
  ["Refund", "Sandbox Refund Test", "-9.99"],
] as const;

type Props = {
  loading: boolean;
  response: SandboxCreateTransactionResponse | null;
  onCreate: (payload: SandboxTransactionRequest) => void;
  onFireWebhook?: () => void;
  onSyncNow?: () => void;
  onTrace?: (traceId: string) => void;
  title?: string;
  helperText?: string;
  submitLabel?: string;
  showAutoFlags?: boolean;
  showResponseActions?: boolean;
};

export function SandboxTransactionForm({
  loading,
  response,
  onCreate,
  onFireWebhook,
  onSyncNow,
  onTrace,
  title = "Custom transaction builder",
  helperText = "Creates a fake Plaid transaction. Depending on webhook configuration, Plaid may still emit webhook updates. Use the timeline to confirm what happened.",
  submitLabel = "Create in Plaid Sandbox",
  showAutoFlags = false,
  showResponseActions = false,
}: Props) {
  const [form, setForm] = useState<SandboxTransactionRequest>({
    description: "ExpenseOps Sandbox Coffee",
    amount: "12.34",
    iso_currency_code: "USD",
    date_transacted: "",
    date_posted: "",
    auto_fire_webhook: false,
    auto_sync_after: false,
  });
  const [validationError, setValidationError] = useState<string | null>(null);

  function update<K extends keyof SandboxTransactionRequest>(
    key: K,
    value: SandboxTransactionRequest[K],
  ) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function submit() {
    const amount = Number(form.amount);
    if (!form.description.trim()) {
      setValidationError("Description is required.");
      return;
    }
    if (!form.iso_currency_code.trim()) {
      setValidationError("Currency is required.");
      return;
    }
    if (!Number.isFinite(amount) || amount === 0) {
      setValidationError("Amount must be a valid non-zero number.");
      return;
    }
    setValidationError(null);
    onCreate({
      ...form,
      description: form.description.trim(),
      iso_currency_code: form.iso_currency_code.trim().toUpperCase(),
      auto_fire_webhook: showAutoFlags ? form.auto_fire_webhook : false,
      auto_sync_after: showAutoFlags ? form.auto_sync_after : false,
    });
  }

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-slate-950">{title}</h2>
          <p className="mt-1 text-sm text-slate-500">
            {helperText}
          </p>
        </div>
        <StatusPill value="sandbox" label="Plaid Sandbox API" />
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {presets.map(([label, description, amount]) => (
          <button
            type="button"
            key={label}
            onClick={() => setForm((current) => ({ ...current, description, amount }))}
            className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 shadow-sm transition hover:border-slate-300 hover:text-slate-950"
          >
            {label} {amount}
          </button>
        ))}
      </div>

      <div className="mt-5 grid gap-3 md:grid-cols-2">
        <Field label="Description" icon={CreditCard}>
          <input
            value={form.description}
            onChange={(event) => update("description", event.target.value)}
            className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none transition focus:border-slate-400 focus:ring-2 focus:ring-slate-100"
          />
        </Field>
        <Field label="Amount" icon={Flame}>
          <input
            value={form.amount}
            onChange={(event) => update("amount", event.target.value)}
            className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none transition focus:border-slate-400 focus:ring-2 focus:ring-slate-100"
          />
        </Field>
        <Field label="Currency">
          <input
            value={form.iso_currency_code}
            onChange={(event) => update("iso_currency_code", event.target.value.toUpperCase())}
            className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none transition focus:border-slate-400 focus:ring-2 focus:ring-slate-100"
          />
        </Field>
        <Field label="Date transacted" icon={CalendarDays}>
          <input
            type="date"
            value={form.date_transacted}
            onChange={(event) => update("date_transacted", event.target.value)}
            className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none transition focus:border-slate-400 focus:ring-2 focus:ring-slate-100"
          />
        </Field>
        <Field label="Date posted" icon={CalendarDays}>
          <input
            type="date"
            value={form.date_posted}
            onChange={(event) => update("date_posted", event.target.value)}
            className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none transition focus:border-slate-400 focus:ring-2 focus:ring-slate-100"
          />
        </Field>
      </div>

      <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
        Leave dates blank to use backend-safe today. If Plaid rejects a date, use today or a date
        within the last 14 days.
      </div>

      {showAutoFlags ? (
      <div className="mt-4 grid gap-2 text-sm text-slate-700 sm:grid-cols-2">
        <label className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
          <input
            type="checkbox"
            checked={form.auto_fire_webhook}
            onChange={(event) => update("auto_fire_webhook", event.target.checked)}
          />
          Auto fire webhook
        </label>
        <label className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
          <input
            type="checkbox"
            checked={form.auto_sync_after}
            onChange={(event) => update("auto_sync_after", event.target.checked)}
          />
          Auto sync after
        </label>
      </div>
      ) : null}

      {validationError ? (
        <div className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {validationError}
        </div>
      ) : null}

      <div className="mt-5 flex flex-wrap gap-2">
        <Button disabled={loading} onClick={submit}>
          {loading ? "Creating..." : submitLabel}
        </Button>
        {response && showResponseActions ? (
          <>
            {onFireWebhook ? (
              <Button variant="secondary" onClick={onFireWebhook}>
                Ask Plaid to Fire Webhook
              </Button>
            ) : null}
            {onSyncNow ? (
              <Button variant="secondary" onClick={onSyncNow}>
                Pull Transactions via /transactions/sync
              </Button>
            ) : null}
            {onTrace ? (
            <Button variant="ghost" onClick={() => onTrace(response.trace_id)}>
              <Search className="h-4 w-4" />
              View Events for Trace
            </Button>
            ) : null}
          </>
        ) : null}
      </div>

      {response ? (
        <div className="mt-4 rounded-lg bg-slate-50 p-3 text-sm ring-1 ring-slate-100">
          <div className="mb-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-800">
            Created in Plaid Sandbox. Not imported into ExpenseOps until webhook or sync runs.
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-slate-700">Trace</span>
            <code className="rounded bg-white px-2 py-1 text-xs">{response.trace_id}</code>
            <CopyButton value={response.trace_id} label="Copy trace ID" />
          </div>
          <div className="mt-2 text-xs text-slate-500">
            Plaid request: <span className="font-mono text-slate-700">{shortId(response.plaid_request_id)}</span>
          </div>
        </div>
      ) : null}
    </Card>
  );
}

function Field({
  label,
  children,
  icon: Icon,
}: {
  label: string;
  children: ReactNode;
  icon?: typeof CalendarDays;
}) {
  return (
    <label className="block">
      <span className="flex items-center gap-1.5 text-xs font-medium uppercase text-slate-500">
        {Icon ? <Icon className="h-3.5 w-3.5" /> : null}
        {label}
      </span>
      <div className="mt-1">{children}</div>
    </label>
  );
}

import { ArrowRight, Play, RadioTower } from "lucide-react";

import type { SandboxRunResponse } from "../types";
import { SandboxTransactionsTable } from "./SandboxTransactionsTable";
import {
  Button,
  Card,
  CopyButton,
  shortId,
  StatusPill,
  statusIcon,
} from "./sandboxUi";

type Props = {
  loading: boolean;
  response: SandboxRunResponse | null;
  onRun: () => void;
};

const pipeline = [
  "sandbox_item_ready",
  "transaction_created",
  "webhook_fired",
  "sync_completed",
  "telegram",
];

export function SandboxRunPanel({ loading, response, onRun }: Props) {
  const latestSync = response?.details.fallback_sync_attempts?.at(-1);
  const transaction = response?.details.transaction?.transaction;
  const stepByName = new Map(response?.steps.map((step) => [step.name, step]) || []);
  const telegramStatus = deriveTelegramStatus(response);

  return (
    <Card className="overflow-hidden">
      <div className="border-b border-slate-200 bg-gradient-to-r from-slate-950 to-slate-800 p-5 text-white">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full bg-white/10 px-2.5 py-1 text-xs font-semibold text-emerald-200 ring-1 ring-white/10">
              <RadioTower className="h-3.5 w-3.5" />
              Primary QA flow
            </div>
            <h2 className="mt-3 text-xl font-semibold">Run Full Flow</h2>
            <p className="mt-1 max-w-2xl text-sm text-slate-300">
              Create a sandbox transaction, fire Plaid webhooks, run Transactions Sync, and observe the Telegram review path.
            </p>
          </div>
          <Button onClick={onRun} disabled={loading}>
            <Play className="h-4 w-4" />
            {loading ? "Running..." : "Run Full Flow: Create → Webhook → Sync → Telegram"}
          </Button>
        </div>
      </div>

      <div className="p-5">
        {response ? (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-semibold text-slate-700">Trace</span>
              <code className="rounded-md bg-slate-100 px-2 py-1 text-xs text-slate-800">
                {response.trace_id}
              </code>
              <CopyButton value={response.trace_id} label="Copy trace ID" />
              <StatusPill value={response.status} />
            </div>

            <div className="grid gap-3 md:grid-cols-5">
              {pipeline.map((name, index) => {
                const step =
                  name === "telegram"
                    ? { name, status: telegramStatus, detail: null }
                    : stepByName.get(name);
                const status = step?.status || "unknown";
                const Icon = statusIcon(status);
                return (
                  <div key={name} className="relative rounded-lg border border-slate-200 bg-slate-50 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="flex h-8 w-8 items-center justify-center rounded-full bg-white text-slate-700 shadow-sm ring-1 ring-slate-200">
                        <Icon className="h-4 w-4" />
                      </span>
                      <StatusPill value={status} />
                    </div>
                    <div className="mt-3 text-sm font-semibold text-slate-900">
                      {stepLabel(name)}
                    </div>
                    {step?.detail ? <p className="mt-1 text-xs text-slate-500">{step.detail}</p> : null}
                    {index < pipeline.length - 1 ? (
                      <ArrowRight className="absolute -right-2 top-1/2 hidden h-4 w-4 -translate-y-1/2 text-slate-300 md:block" />
                    ) : null}
                  </div>
                );
              })}
            </div>

            {response.steps.some((step) => step.name === "webhook_received" && step.status === "unknown") &&
            response.steps.some((step) => step.name === "sync_completed" && step.status !== "failed") ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
                Webhook receipt was not observed before fallback sync completed. This is a warning,
                not a full failure.
              </div>
            ) : null}

            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              <Fact label="Created transaction" value={String(transaction?.description || "none")} />
              <Fact label="Amount" value={String(transaction?.amount || "n/a")} />
              <Fact label="Create request" value={shortId(response.details.transaction?.plaid_request_id)} />
              <Fact label="Webhook request" value={shortId(response.details.webhook?.plaid_request_id)} />
              <Fact label="Added count" value={String(latestSync?.added_count ?? 0)} />
              <Fact label="Fallback sync used" value={response.details.fallback_sync_attempts ? "yes" : "no"} />
            </div>

            <SandboxTransactionsTable
              title="Newly synced in this run"
              transactions={latestSync?.added_transactions || []}
            />
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-8 text-center">
            <p className="text-sm font-medium text-slate-700">No E2E run yet.</p>
            <p className="mt-1 text-sm text-slate-500">
              Start with the primary action above to create a traceable sandbox transaction.
            </p>
          </div>
        )}
      </div>
    </Card>
  );
}

export function StatusBadge({ value }: { value: string }) {
  return <StatusPill value={value} />;
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3 ring-1 ring-slate-100">
      <div className="text-xs font-medium uppercase text-slate-500">{label}</div>
      <div className="mt-1 break-words font-mono text-xs text-slate-900">{value}</div>
    </div>
  );
}

function stepLabel(name: string) {
  const labels: Record<string, string> = {
    sandbox_item_ready: "Create item",
    transaction_created: "Create transaction",
    webhook_fired: "Fire webhook",
    sync_completed: "Sync",
    telegram: "Telegram",
  };
  return labels[name] || name.replace(/_/g, " ");
}

function deriveTelegramStatus(response: SandboxRunResponse | null) {
  const detailText = JSON.stringify(response?.details || {});
  if (detailText.includes("telegram_notification_send_succeeded")) return "success";
  if (detailText.includes("telegram_notification_skipped_duplicate")) return "skipped";
  return "unknown";
}

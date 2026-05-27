import { RefreshCw, RotateCcw, Send } from "lucide-react";

import type { SandboxSyncResponse, SandboxWebhookResponse } from "../types";
import { Button, Card, shortId, StatusPill } from "./sandboxUi";

type Props = {
  webhookCode: string;
  setWebhookCode: (value: string) => void;
  webhookResponse: SandboxWebhookResponse | null;
  syncResponse: SandboxSyncResponse | null;
  loadingWebhook: boolean;
  loadingSync: boolean;
  loadingReset?: boolean;
  onFireWebhook: () => void;
  onSyncNow: () => void;
  onResetEvents?: () => void;
};

const webhookCodes = [
  "SYNC_UPDATES_AVAILABLE",
  "DEFAULT_UPDATE",
  "HISTORICAL_UPDATE",
  "INITIAL_UPDATE",
];

export function SandboxWebhookPanel({
  webhookCode,
  setWebhookCode,
  webhookResponse,
  syncResponse,
  loadingWebhook,
  loadingSync,
  loadingReset,
  onFireWebhook,
  onSyncNow,
  onResetEvents,
}: Props) {
  return (
    <Card className="p-5">
      <div>
        <h2 className="text-base font-semibold text-slate-950">Operations</h2>
        <p className="mt-1 text-sm text-slate-500">
          Fire webhooks, run manual sync, and reset only Sandbox Lab event logs.
        </p>
      </div>

      <div className="mt-4 space-y-4">
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
          <label className="text-xs font-medium uppercase text-slate-500">Webhook code</label>
          <select
            value={webhookCode}
            onChange={(event) => setWebhookCode(event.target.value)}
            className="mt-1 w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-slate-400 focus:ring-2 focus:ring-slate-100"
          >
            {webhookCodes.map((code) => (
              <option key={code}>{code}</option>
            ))}
          </select>
          <p className="mt-2 text-xs text-slate-500">
            SYNC_UPDATES_AVAILABLE is recommended for Transactions Sync.
          </p>
          <Button disabled={loadingWebhook} onClick={onFireWebhook}>
            <Send className="h-4 w-4" />
            {loadingWebhook ? "Firing..." : "Fire webhook"}
          </Button>
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          <Button variant="secondary" disabled={loadingSync} onClick={onSyncNow}>
            <RefreshCw className="h-4 w-4" />
            {loadingSync ? "Syncing..." : "Sync Now"}
          </Button>
          {onResetEvents ? (
            <Button variant="danger" disabled={loadingReset} onClick={onResetEvents}>
              <RotateCcw className="h-4 w-4" />
              {loadingReset ? "Resetting..." : "Reset Events"}
            </Button>
          ) : null}
        </div>
      </div>

      {webhookResponse ? (
        <div className="mt-4 grid gap-2 text-sm">
          <Fact label="Webhook fired" value={webhookResponse.webhook_fired ? "true" : "false"} />
          <Fact label="Trace" value={shortId(webhookResponse.trace_id, 10)} />
          <Fact label="Plaid request" value={shortId(webhookResponse.plaid_request_id)} />
        </div>
      ) : null}

      {syncResponse ? (
        <div className="mt-4 grid gap-2 text-sm sm:grid-cols-2">
          <Fact label="Added" value={String(syncResponse.added_count)} />
          <Fact label="Modified" value={String(syncResponse.modified_count)} />
          <Fact label="Removed" value={String(syncResponse.removed_count)} />
          <Fact label="Cursor" value={syncResponse.cursor_present ? "present" : "missing"} />
          <Fact label="Updated" value={String(syncResponse.cursor_updated)} />
          <Fact label="Next cursor" value={String(syncResponse.next_cursor_present)} />
        </div>
      ) : null}
    </Card>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3 ring-1 ring-slate-100">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-medium uppercase text-slate-500">{label}</div>
        {value === "true" || value === "present" ? <StatusPill value="success" label="ok" /> : null}
      </div>
      <div className="mt-1 break-words font-mono text-xs text-slate-900">{value}</div>
    </div>
  );
}

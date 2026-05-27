import { AlertTriangle, CheckCircle2, Database, Link2, RadioTower, TerminalSquare } from "lucide-react";

import type { SandboxStatus } from "../types";
import { Button, Card, formatDateTime, shortId, StatusPill } from "./sandboxUi";

type Props = {
  status: SandboxStatus | null;
  onRefresh: () => void;
};

export function SandboxStatusCard({ status, onRefresh }: Props) {
  const ready = Boolean(
    status?.enabled &&
      status.plaid_env === "sandbox" &&
      status.webhook_url_configured &&
      status.state_exists &&
      status.access_token_exists &&
      status.transactions_cursor_exists,
  );

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-slate-950">Environment details</h2>
          <p className="mt-1 text-sm text-slate-500">Current Plaid Sandbox wiring and local state.</p>
        </div>
        <Button variant="secondary" onClick={onRefresh}>
          Refresh
        </Button>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <StatusPill value={ready ? "ready" : "unknown"} label={ready ? "Ready" : "Needs setup"} />
        {status && !status.enabled ? <StatusPill value="disabled" label="Disabled" /> : null}
        {status && status.plaid_env !== "sandbox" ? (
          <StatusPill value="wrong env" label="Wrong Plaid env" />
        ) : null}
        {status && !status.webhook_url_configured ? (
          <StatusPill value="missing" label="Missing webhook URL" />
        ) : null}
        {status && !status.transactions_cursor_exists ? (
          <StatusPill value="missing" label="Cursor missing" />
        ) : null}
      </div>

      {status && status.plaid_env !== "sandbox" ? (
        <div className="mt-4 flex gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <AlertTriangle className="mt-0.5 h-4 w-4" />
          <span>SANDBOX ONLY — NO REAL BANK DATA. Set PLAID_ENV=sandbox.</span>
        </div>
      ) : null}

      <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
        <StatusItem icon={CheckCircle2} label="Enabled" value={yesNo(status?.enabled)} />
        <StatusItem icon={TerminalSquare} label="PLAID_ENV" value={status?.plaid_env || "unknown"} />
        <StatusItem icon={Link2} label="Webhook URL" value={shortUrl(status?.webhook_url)} />
        <StatusItem icon={Database} label="Sandbox item" value={yesNo(status?.state_exists)} />
        <StatusItem icon={RadioTower} label="Access token" value={status?.access_token_redacted || "missing"} />
        <StatusItem label="Item ID" value={shortId(status?.item_id)} />
        <StatusItem label="Cursor" value={yesNo(status?.transactions_cursor_exists)} />
        <StatusItem label="Latest trace" value={shortId(status?.latest_known_trace_id, 10)} />
        <StatusItem label="Latest event" value={formatDateTime(status?.latest_event_timestamp)} />
      </dl>
    </Card>
  );
}

function StatusItem({
  icon: Icon,
  label,
  value,
}: {
  icon?: typeof CheckCircle2;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-md bg-slate-50 p-3">
      <dt className="flex items-center gap-2 text-xs font-medium uppercase text-slate-500">
        {Icon ? <Icon className="h-3.5 w-3.5" /> : null}
        {label}
      </dt>
      <dd className="mt-1 break-words font-mono text-xs text-slate-900">{value}</dd>
    </div>
  );
}

function yesNo(value: boolean | undefined) {
  if (value === undefined) return "unknown";
  return value ? "yes" : "no";
}

function shortUrl(value?: string | null) {
  if (!value) return "missing";
  try {
    const url = new URL(value);
    return `${url.host}${url.pathname}`;
  } catch {
    return shortId(value, 18);
  }
}

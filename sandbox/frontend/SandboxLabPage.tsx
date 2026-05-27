import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Database,
  FlaskConical,
  Play,
  RadioTower,
  RefreshCw,
  Send,
  ShieldCheck,
  TerminalSquare,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { sandboxApiClient } from "./api";
import { SandboxEventTimeline } from "./components/SandboxEventTimeline";
import { SandboxFlowStepper } from "./components/SandboxFlowStepper";
import type { FlowStep } from "./components/SandboxFlowStepper";
import { SandboxJsonViewer } from "./components/SandboxJsonViewer";
import { SandboxRunPanel } from "./components/SandboxRunPanel";
import { SandboxStatusCard } from "./components/SandboxStatusCard";
import { SandboxTransactionForm } from "./components/SandboxTransactionForm";
import { SandboxTransactionsTable } from "./components/SandboxTransactionsTable";
import {
  Button,
  Card,
  formatDateTime,
  shortId,
  StatusPill,
} from "./components/sandboxUi";
import { useSandboxApi } from "./hooks/useSandboxApi";
import type {
  SandboxCreateTransactionResponse,
  SandboxEvent,
  SandboxRunResponse,
  SandboxStatus,
  SandboxSyncResponse,
  SandboxTransactionRequest,
  SandboxWebhookResponse,
} from "./types";

type SandboxSection = "overview" | "webhook" | "manual-sync" | "e2e" | "events";

const navItems: Array<{ id: SandboxSection; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "webhook", label: "Webhook Flow" },
  { id: "manual-sync", label: "Manual Sync Flow" },
  { id: "e2e", label: "Full E2E" },
  { id: "events", label: "Event Explorer" },
];

export function SandboxLabPage() {
  const {
    status,
    events,
    error,
    loading,
    setError,
    loadStatus,
    loadEvents,
    runAction,
  } = useSandboxApi();
  const [activeSection, setActiveSection] = useState<SandboxSection>("overview");
  const [runResponse, setRunResponse] = useState<SandboxRunResponse | null>(null);
  const [transactionResponse, setTransactionResponse] =
    useState<SandboxCreateTransactionResponse | null>(null);
  const [webhookResponse, setWebhookResponse] = useState<SandboxWebhookResponse | null>(null);
  const [syncResponse, setSyncResponse] = useState<SandboxSyncResponse | null>(null);
  const [traceFilter, setTraceFilter] = useState("");
  const [eventFilter, setEventFilter] = useState("");
  const [webhookCode, setWebhookCode] = useState("SYNC_UPDATES_AVAILABLE");
  const [autoRefresh, setAutoRefresh] = useState(true);

  const latestTransactions = useMemo(
    () =>
      syncResponse?.added_transactions ||
      runResponse?.details.fallback_sync_attempts?.at(-1)?.added_transactions ||
      [],
    [runResponse, syncResponse],
  );

  const latestRunEvent = events.find((event) =>
    ["sandbox_e2e_completed", "sandbox_e2e_failed"].includes(event.event_type),
  );
  const latestTelegramEvent = events.find((event) =>
    event.event_type.includes("telegram") || event.event_type.includes("notification"),
  );

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => {
      void loadEvents(traceFilter || undefined);
      void loadStatus();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, loadEvents, loadStatus, traceFilter]);

  async function refreshAll(traceId?: string) {
    await Promise.all([loadStatus(), loadEvents(traceId || traceFilter || undefined)]);
  }

  async function runE2E() {
    const data = await runAction("run-e2e", () => sandboxApiClient.runE2E());
    setRunResponse(data);
    setTraceFilter(data.trace_id);
    await refreshAll(data.trace_id);
  }

  async function createTransaction(payload: SandboxTransactionRequest) {
    const data = await runAction("create-transaction", () =>
      sandboxApiClient.createTransaction(payload),
    );
    setTransactionResponse(data);
    setTraceFilter(data.trace_id);
    await refreshAll(data.trace_id);
  }

  async function fireWebhook(traceId?: string) {
    const data = await runAction("fire-webhook", () =>
      sandboxApiClient.fireWebhook(webhookCode, traceId),
    );
    setWebhookResponse(data);
    setTraceFilter(data.trace_id);
    await refreshAll(data.trace_id);
  }

  async function syncNow(traceId?: string) {
    const data = await runAction("sync-now", () => sandboxApiClient.syncNow(traceId));
    setSyncResponse(data);
    setTraceFilter(data.trace_id);
    await refreshAll(data.trace_id);
  }

  async function resetEvents() {
    await runAction("reset-events", () => sandboxApiClient.resetEvents());
    setTraceFilter("");
    await refreshAll("");
  }

  const blocked = Boolean(status && (!status.enabled || status.plaid_env !== "sandbox"));

  return (
    <main className="min-h-screen bg-slate-100">
      <section className="mx-auto flex w-full max-w-[1540px] flex-col gap-5 px-4 py-5 sm:px-6 lg:px-8">
        <SandboxHero status={status} />

        {blocked ? (
          <div className="flex gap-2 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
            <AlertTriangle className="mt-0.5 h-4 w-4" />
            <span>
              Sandbox Lab is blocked. Set ENABLE_EXPENSEOPS_SANDBOX_LAB=true and PLAID_ENV=sandbox.
            </span>
          </div>
        ) : null}

        {error ? <ErrorAlert error={error} onDismiss={() => setError(null)} /> : null}

        <SandboxNav active={activeSection} onChange={setActiveSection} />

        {activeSection === "overview" ? (
          <OverviewPage
            status={status}
            latestRunEvent={latestRunEvent}
            latestTelegramEvent={latestTelegramEvent}
            onOpen={setActiveSection}
          />
        ) : null}

        {activeSection === "webhook" ? (
          <WebhookFlowPage
            blocked={blocked}
            loading={loading}
            events={events}
            transactionResponse={transactionResponse}
            webhookResponse={webhookResponse}
            syncResponse={syncResponse}
            webhookCode={webhookCode}
            setWebhookCode={setWebhookCode}
            onCreate={(payload) => void createTransaction(payload)}
            onFireWebhook={() => void fireWebhook(transactionResponse?.trace_id)}
            onSyncNow={() => void syncNow(transactionResponse?.trace_id)}
            onShowEvents={(traceId) => {
              setTraceFilter(traceId);
              setActiveSection("events");
              void loadEvents(traceId);
            }}
          />
        ) : null}

        {activeSection === "manual-sync" ? (
          <ManualSyncFlowPage
            blocked={blocked}
            loading={loading}
            events={events}
            transactionResponse={transactionResponse}
            syncResponse={syncResponse}
            onCreate={(payload) => void createTransaction(payload)}
            onSyncNow={() => void syncNow(transactionResponse?.trace_id)}
            onShowEvents={(traceId) => {
              setTraceFilter(traceId);
              setActiveSection("events");
              void loadEvents(traceId);
            }}
          />
        ) : null}

        {activeSection === "e2e" ? (
          <FullE2EPage
            blocked={blocked}
            loading={loading}
            runResponse={runResponse}
            onRun={() => void runE2E()}
          />
        ) : null}

        {activeSection === "events" ? (
          <EventExplorerPage
            events={events}
            traceFilter={traceFilter}
            eventFilter={eventFilter}
            autoRefresh={autoRefresh}
            status={status}
            runResponse={runResponse}
            transactionResponse={transactionResponse}
            webhookResponse={webhookResponse}
            syncResponse={syncResponse}
            loadingReset={loading === "reset-events"}
            onTraceFilter={setTraceFilter}
            onEventFilter={setEventFilter}
            onAutoRefresh={setAutoRefresh}
            onRefresh={() => void refreshAll()}
            onResetEvents={() => void resetEvents()}
          />
        ) : null}
      </section>
    </main>
  );
}

function OverviewPage({
  status,
  latestRunEvent,
  latestTelegramEvent,
  onOpen,
}: {
  status: SandboxStatus | null;
  latestRunEvent?: SandboxEvent;
  latestTelegramEvent?: SandboxEvent;
  onOpen: (section: SandboxSection) => void;
}) {
  return (
    <div className="space-y-5">
      <StatusOverviewStrip
        status={status}
        latestRunEvent={latestRunEvent}
        latestTelegramEvent={latestTelegramEvent}
      />
      <div className="grid gap-4 lg:grid-cols-3">
        <WorkflowCard
          title="Webhook Flow"
          description="Create transaction → Fire Plaid webhook → Backend receives webhook → Sync → Telegram"
          cta="Open Webhook Flow"
          onClick={() => onOpen("webhook")}
        />
        <WorkflowCard
          title="Manual Sync Flow"
          description="Create transaction → Pull /transactions/sync → Import → Telegram"
          cta="Open Manual Sync Flow"
          onClick={() => onOpen("manual-sync")}
        />
        <WorkflowCard
          title="Full E2E"
          description="Automated smoke test with fallback sync."
          cta="Open Full E2E"
          onClick={() => onOpen("e2e")}
        />
      </div>
    </div>
  );
}

function WebhookFlowPage({
  blocked,
  loading,
  events,
  transactionResponse,
  webhookResponse,
  syncResponse,
  webhookCode,
  setWebhookCode,
  onCreate,
  onFireWebhook,
  onSyncNow,
  onShowEvents,
}: {
  blocked: boolean;
  loading: string | null;
  events: SandboxEvent[];
  transactionResponse: SandboxCreateTransactionResponse | null;
  webhookResponse: SandboxWebhookResponse | null;
  syncResponse: SandboxSyncResponse | null;
  webhookCode: string;
  setWebhookCode: (value: string) => void;
  onCreate: (payload: SandboxTransactionRequest) => void;
  onFireWebhook: () => void;
  onSyncNow: () => void;
  onShowEvents: (traceId: string) => void;
}) {
  const traceIds = [transactionResponse?.trace_id, webhookResponse?.trace_id, syncResponse?.trace_id].filter(
    Boolean,
  ) as string[];
  const flowEvents = eventsForTraces(events, traceIds);
  const steps = webhookSteps(transactionResponse, webhookResponse, flowEvents);

  return (
    <div className="space-y-5">
      <FlowHeader
        title="Webhook Flow"
        description="Test the production-style Plaid webhook path without automatically running manual sync."
      />
      <Card className="p-4 text-sm leading-6 text-slate-600">
        Sandbox Lab detaches the webhook before create-only so Plaid does not auto-import the
        transaction. When you click Ask Plaid to Fire Webhook, it re-attaches the webhook and fires
        SYNC_UPDATES_AVAILABLE.
      </Card>
      <SandboxFlowStepper steps={steps} />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="space-y-5">
          <SandboxTransactionForm
            loading={loading === "create-transaction" || blocked}
            response={transactionResponse}
            title="Step 1: Create in Plaid Sandbox"
            helperText="Creates a fake Plaid transaction while keeping the webhook detached until you explicitly ask Plaid to fire one."
            submitLabel="Create in Plaid Sandbox"
            onCreate={onCreate}
            onTrace={onShowEvents}
          />
          <Card className="p-5">
            <h2 className="text-base font-semibold text-slate-950">Step 2: Ask Plaid to Fire Webhook</h2>
            <p className="mt-1 text-sm text-slate-500">
              Manually asks Plaid Sandbox to send SYNC_UPDATES_AVAILABLE.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <select
                value={webhookCode}
                onChange={(event) => setWebhookCode(event.target.value)}
                className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-slate-400 focus:ring-2 focus:ring-slate-100"
              >
                {["SYNC_UPDATES_AVAILABLE", "DEFAULT_UPDATE", "HISTORICAL_UPDATE", "INITIAL_UPDATE"].map((code) => (
                  <option key={code}>{code}</option>
                ))}
              </select>
              <Button disabled={loading === "fire-webhook" || blocked} onClick={onFireWebhook}>
                <Send className="h-4 w-4" />
                {loading === "fire-webhook" ? "Asking Plaid..." : "Ask Plaid to Fire Webhook"}
              </Button>
            </div>
            <p className="mt-2 text-xs text-slate-500">
              SYNC_UPDATES_AVAILABLE is recommended for Transactions Sync.
            </p>
          </Card>
          <Card className="p-5">
            <h2 className="text-base font-semibold text-slate-950">Manual fallback</h2>
            <p className="mt-1 text-sm text-slate-500">
              Use this only if you want to recover a webhook test that has not synced yet.
            </p>
            <Button variant="secondary" disabled={loading === "sync-now" || blocked} onClick={onSyncNow}>
              <RefreshCw className="h-4 w-4" />
              Use manual sync fallback
            </Button>
          </Card>
        </div>
        <FlowObservationPanel
          title="Webhook observations"
          events={flowEvents}
          traces={traceIds}
          syncResponse={syncResponse}
          webhookResponse={webhookResponse}
        />
      </div>
    </div>
  );
}

function ManualSyncFlowPage({
  blocked,
  loading,
  events,
  transactionResponse,
  syncResponse,
  onCreate,
  onSyncNow,
  onShowEvents,
}: {
  blocked: boolean;
  loading: string | null;
  events: SandboxEvent[];
  transactionResponse: SandboxCreateTransactionResponse | null;
  syncResponse: SandboxSyncResponse | null;
  onCreate: (payload: SandboxTransactionRequest) => void;
  onSyncNow: () => void;
  onShowEvents: (traceId: string) => void;
}) {
  const traceIds = [transactionResponse?.trace_id, syncResponse?.trace_id].filter(Boolean) as string[];
  const flowEvents = eventsForTraces(events, traceIds);
  const steps = manualSyncSteps(transactionResponse, syncResponse, flowEvents);

  return (
    <div className="space-y-5">
      <FlowHeader
        title="Manual Sync Flow"
        description="Test direct /transactions/sync without relying on Plaid webhook delivery."
      />
      <SandboxFlowStepper steps={steps} />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="space-y-5">
          <SandboxTransactionForm
            loading={loading === "create-transaction" || blocked}
            response={transactionResponse}
            title="Step 1: Create in Plaid Sandbox"
            helperText="Creates a fake Plaid transaction. Depending on webhook configuration, Plaid may still emit webhook updates. Use the timeline to confirm what happened."
            submitLabel="Create in Plaid Sandbox"
            onCreate={onCreate}
            onTrace={onShowEvents}
          />
          <Card className="p-5">
            <h2 className="text-base font-semibold text-slate-950">
              Step 2: Pull Transactions via /transactions/sync
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              Manually imports Plaid changes.
            </p>
            <Button disabled={loading === "sync-now" || blocked} onClick={onSyncNow}>
              <RefreshCw className="h-4 w-4" />
              {loading === "sync-now" ? "Pulling..." : "Pull Transactions via /transactions/sync"}
            </Button>
          </Card>
          <SandboxTransactionsTable
            title="Newly synced in this run"
            transactions={syncResponse?.added_transactions || []}
          />
        </div>
        <FlowObservationPanel
          title="Manual sync observations"
          events={flowEvents}
          traces={traceIds}
          syncResponse={syncResponse}
        />
      </div>
    </div>
  );
}

function FullE2EPage({
  blocked,
  loading,
  runResponse,
  onRun,
}: {
  blocked: boolean;
  loading: string | null;
  runResponse: SandboxRunResponse | null;
  onRun: () => void;
}) {
  return (
    <div className="space-y-5">
      <FlowHeader
        title="Full E2E"
        description="One-click automated smoke test. This flow may use fallback sync and will label that as a warning, not a failure."
      />
      <SandboxRunPanel loading={loading === "run-e2e" || blocked} response={runResponse} onRun={onRun} />
    </div>
  );
}

function EventExplorerPage({
  events,
  traceFilter,
  eventFilter,
  autoRefresh,
  status,
  runResponse,
  transactionResponse,
  webhookResponse,
  syncResponse,
  loadingReset,
  onTraceFilter,
  onEventFilter,
  onAutoRefresh,
  onRefresh,
  onResetEvents,
}: {
  events: SandboxEvent[];
  traceFilter: string;
  eventFilter: string;
  autoRefresh: boolean;
  status: SandboxStatus | null;
  runResponse: SandboxRunResponse | null;
  transactionResponse: SandboxCreateTransactionResponse | null;
  webhookResponse: SandboxWebhookResponse | null;
  syncResponse: SandboxSyncResponse | null;
  loadingReset: boolean;
  onTraceFilter: (value: string) => void;
  onEventFilter: (value: string) => void;
  onAutoRefresh: (value: boolean) => void;
  onRefresh: () => void;
  onResetEvents: () => void;
}) {
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
      <SandboxEventTimeline
        events={events}
        traceFilter={traceFilter}
        eventFilter={eventFilter}
        autoRefresh={autoRefresh}
        onTraceFilter={onTraceFilter}
        onEventFilter={onEventFilter}
        onAutoRefresh={onAutoRefresh}
        onRefresh={onRefresh}
      />
      <aside className="space-y-3">
        <Card className="p-5">
          <h2 className="text-base font-semibold text-slate-950">Explorer controls</h2>
          <p className="mt-1 text-sm text-slate-500">
            Reset clears only Sandbox Lab event logs, not app transactions.
          </p>
          <Button variant="danger" disabled={loadingReset} onClick={onResetEvents}>
            {loadingReset ? "Resetting..." : "Reset Events"}
          </Button>
        </Card>
        <SandboxJsonViewer title="Latest status response" data={status} />
        <SandboxJsonViewer title="Latest run-e2e response" data={runResponse} />
        <SandboxJsonViewer title="Latest transaction response" data={transactionResponse} />
        <SandboxJsonViewer title="Latest webhook response" data={webhookResponse} />
        <SandboxJsonViewer title="Latest sync response" data={syncResponse} />
      </aside>
    </div>
  );
}

function SandboxHero({ status }: { status: SandboxStatus | null }) {
  return (
    <header className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-200 bg-slate-950 px-5 py-5 text-white">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-md bg-emerald-400/15 text-emerald-200 ring-1 ring-emerald-300/20">
                <FlaskConical className="h-5 w-5" />
              </span>
              <div>
                <h1 className="text-2xl font-semibold tracking-normal">ExpenseOps Sandbox Lab</h1>
                <p className="mt-1 text-sm text-slate-300">
                  Test Plaid Sandbox transactions, webhooks, sync, and Telegram review flow.
                </p>
              </div>
            </div>
            <div className="mt-4 inline-flex items-center gap-2 rounded-full bg-amber-400/10 px-3 py-1.5 text-xs font-semibold text-amber-100 ring-1 ring-amber-300/20">
              <ShieldCheck className="h-3.5 w-3.5" />
              SANDBOX ONLY — NO REAL BANK DATA
            </div>
          </div>
          <a
            href="/"
            className="inline-flex items-center gap-2 rounded-md bg-white px-3.5 py-2 text-sm font-semibold text-slate-950 shadow-sm transition hover:bg-slate-100"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to dashboard
          </a>
        </div>
      </div>
      <div className="grid gap-3 px-5 py-4 text-sm sm:grid-cols-3">
        <HeroFact label="Plaid Env" value={status?.plaid_env || "unknown"} />
        <HeroFact label="Webhook" value={status?.webhook_url_configured ? "Configured" : "Missing"} />
        <HeroFact label="Latest Trace" value={shortId(status?.latest_known_trace_id, 10)} />
      </div>
    </header>
  );
}

function SandboxNav({
  active,
  onChange,
}: {
  active: SandboxSection;
  onChange: (section: SandboxSection) => void;
}) {
  return (
    <nav className="rounded-lg border border-slate-200 bg-white p-1 shadow-sm">
      <div className="grid gap-1 md:grid-cols-5">
        {navItems.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => onChange(item.id)}
            className={`rounded-md px-3 py-2 text-sm font-semibold transition ${
              active === item.id
                ? "bg-slate-950 text-white shadow-sm"
                : "text-slate-600 hover:bg-slate-100 hover:text-slate-950"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>
    </nav>
  );
}

function StatusOverviewStrip({
  status,
  latestRunEvent,
  latestTelegramEvent,
}: {
  status: SandboxStatus | null;
  latestRunEvent?: SandboxEvent;
  latestTelegramEvent?: SandboxEvent;
}) {
  const cards = [
    {
      icon: TerminalSquare,
      label: "Plaid Env",
      value: status?.plaid_env || "unknown",
      status: status?.plaid_env === "sandbox" ? "ready" : "wrong env",
      badge: status?.plaid_env === "sandbox" ? "Ready" : "Wrong env",
    },
    {
      icon: RadioTower,
      label: "Webhook URL",
      value: status?.webhook_url_configured ? shortId(status.webhook_url, 16) : "missing",
      status: status?.webhook_url_configured ? "connected" : "missing",
      badge: status?.webhook_url_configured ? "Connected" : "Missing",
    },
    {
      icon: Database,
      label: "Sandbox Item",
      value: status?.state_exists ? "yes" : "no",
      status: status?.state_exists ? "ready" : "missing",
      badge: status?.state_exists ? "Ready" : "Missing",
    },
    {
      icon: Database,
      label: "Cursor",
      value: status?.transactions_cursor_exists ? "active" : "missing",
      status: status?.transactions_cursor_exists ? "active" : "missing",
      badge: status?.transactions_cursor_exists ? "Active" : "Missing",
    },
    {
      icon: FlaskConical,
      label: "Latest Run",
      value: latestRunEvent ? shortId(latestRunEvent.trace_id, 8) : "unknown",
      status:
        latestRunEvent?.event_type === "sandbox_e2e_failed"
          ? "failed"
          : latestRunEvent
            ? "completed"
            : "unknown",
      badge:
        latestRunEvent?.event_type === "sandbox_e2e_failed"
          ? "Failed"
          : latestRunEvent
            ? "Completed"
            : "Unknown",
    },
    {
      icon: ShieldCheck,
      label: "Telegram Observed",
      value: latestTelegramEvent ? formatDateTime(latestTelegramEvent.created_at) : "Observed in timeline",
      status: latestTelegramEvent?.event_type.includes("failed") ? "failed" : latestTelegramEvent ? "success" : "unknown",
      badge: latestTelegramEvent ? "Observed" : "Timeline",
    },
  ];

  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
      {cards.map((card) => (
        <Card key={card.label} className="p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-md bg-slate-100 text-slate-600">
              <card.icon className="h-4 w-4" />
            </span>
            <StatusPill value={card.status} label={card.badge} />
          </div>
          <div className="mt-3 text-xs font-medium uppercase text-slate-500">{card.label}</div>
          <div className="mt-1 truncate text-sm font-semibold text-slate-950">{card.value}</div>
        </Card>
      ))}
    </div>
  );
}

function WorkflowCard({
  title,
  description,
  cta,
  onClick,
}: {
  title: string;
  description: string;
  cta: string;
  onClick: () => void;
}) {
  return (
    <Card className="p-5">
      <h2 className="text-base font-semibold text-slate-950">{title}</h2>
      <p className="mt-2 min-h-12 text-sm leading-6 text-slate-500">{description}</p>
      <Button variant="secondary" onClick={onClick}>
        {cta}
        <ArrowRight className="h-4 w-4" />
      </Button>
    </Card>
  );
}

function FlowHeader({ title, description }: { title: string; description: string }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="text-xl font-semibold text-slate-950">{title}</h2>
        <p className="mt-1 text-sm text-slate-500">{description}</p>
      </div>
      <StatusPill value="sandbox" label="SANDBOX ONLY" />
    </div>
  );
}

function FlowObservationPanel({
  title,
  events,
  traces,
  syncResponse,
  webhookResponse,
}: {
  title: string;
  events: SandboxEvent[];
  traces: string[];
  syncResponse?: SandboxSyncResponse | null;
  webhookResponse?: SandboxWebhookResponse | null;
}) {
  const webhookReceived = findEvent(events, "plaid_webhook_received");
  const syncStarted = findEvent(events, "plaid_transactions_sync_started");
  const syncCompleted = findEvent(events, "plaid_transactions_sync_completed");
  const telegramEvent = events.find((event) => event.event_type.includes("telegram"));

  return (
    <aside className="space-y-5">
      <Card className="p-5">
        <h2 className="text-base font-semibold text-slate-950">{title}</h2>
        <div className="mt-4 space-y-3">
          <Observation label="Trace IDs" value={traces.length ? traces.map((trace) => shortId(trace, 10)).join(", ") : "none"} />
          <Observation label="Webhook request" value={shortId(webhookResponse?.plaid_request_id)} />
          <Observation label="Webhook received" value={webhookReceived ? formatDateTime(webhookReceived.created_at) : "not observed"} />
          <Observation label="Sync started" value={syncStarted ? formatDateTime(syncStarted.created_at) : "not observed"} />
          <Observation label="Sync completed" value={syncCompleted ? formatDateTime(syncCompleted.created_at) : "not observed"} />
          <Observation label="Added count" value={String(syncResponse?.added_count ?? 0)} />
          <Observation label="Telegram" value={telegramEvent ? telegramEvent.event_type : "not observed"} />
        </div>
        {webhookReceived && !syncStarted ? (
          <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
            Webhook was received but sync was not observed yet.
          </div>
        ) : null}
      </Card>
      <SandboxTransactionsTable
        title="Latest review-needed transactions"
        transactions={syncResponse?.added_transactions || []}
      />
    </aside>
  );
}

function Observation({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-slate-100 pb-2 last:border-0 last:pb-0">
      <span className="text-sm text-slate-500">{label}</span>
      <span className="max-w-[220px] truncate text-right font-mono text-xs text-slate-900">{value}</span>
    </div>
  );
}

function ErrorAlert({ error, onDismiss }: { error: unknown; onDismiss: () => void }) {
  const text = JSON.stringify(error, null, 2);
  const isDateError = text.includes("date") || text.includes("last 14 days");
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
      <div className="flex items-start justify-between gap-3">
        <div className="flex gap-2">
          <AlertTriangle className="mt-0.5 h-4 w-4" />
          <div>
            <div className="font-semibold">Sandbox API error</div>
            <p className="mt-1">
              {isDateError
                ? "Plaid rejected the transaction date. Use today or a date within the last 14 days. If timezone mismatch occurs, leave date blank so backend picks a safe date."
                : "The Sandbox Lab action failed. Expand the raw JSON drawer for details."}
            </p>
          </div>
        </div>
        <button type="button" onClick={onDismiss} className="font-medium underline">
          Dismiss
        </button>
      </div>
      <details className="mt-3">
        <summary className="cursor-pointer font-medium">Raw error JSON</summary>
        <pre className="mt-2 max-h-72 overflow-auto rounded-md bg-red-950 p-3 text-xs text-red-50">
          {text}
        </pre>
      </details>
    </div>
  );
}

function HeroFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-slate-50 p-3">
      <div className="text-xs font-medium uppercase text-slate-500">{label}</div>
      <div className="mt-1 truncate font-mono text-xs text-slate-900">{value}</div>
    </div>
  );
}

function eventsForTraces(events: SandboxEvent[], traces: string[]) {
  if (!traces.length) return events;
  return events.filter((event) => traces.includes(event.trace_id));
}

function findEvent(events: SandboxEvent[], eventType: string) {
  return events.find((event) => event.event_type === eventType);
}

function stepStatus(events: SandboxEvent[], successType: string, failedType?: string): FlowStep["status"] {
  if (failedType && findEvent(events, failedType)) return "failed";
  if (findEvent(events, successType)) return "success";
  return "unknown";
}

function webhookSteps(
  transactionResponse: SandboxCreateTransactionResponse | null,
  webhookResponse: SandboxWebhookResponse | null,
  events: SandboxEvent[],
): FlowStep[] {
  return [
    {
      id: "create",
      title: "Create in Plaid",
      description: "Create the fake transaction in Plaid Sandbox only.",
      status: transactionResponse?.created ? "success" : "unknown",
    },
    {
      id: "fire",
      title: "Fire webhook",
      description: "Ask Plaid Sandbox to call the real webhook endpoint.",
      status: webhookResponse?.webhook_fired ? "success" : stepStatus(events, "sandbox_webhook_fire_succeeded", "sandbox_webhook_fire_failed"),
    },
    {
      id: "received",
      title: "Webhook received",
      description: "Observe plaid_webhook_received for this flow.",
      status: stepStatus(events, "plaid_webhook_received"),
    },
    {
      id: "sync",
      title: "Webhook sync",
      description: "Observe webhook-triggered Transactions Sync.",
      status: stepStatus(events, "plaid_transactions_sync_completed", "plaid_transactions_sync_failed"),
    },
    {
      id: "telegram",
      title: "Telegram",
      description: "Expected max one review notification.",
      status: telegramStatusFromEvents(events),
    },
  ];
}

function manualSyncSteps(
  transactionResponse: SandboxCreateTransactionResponse | null,
  syncResponse: SandboxSyncResponse | null,
  events: SandboxEvent[],
): FlowStep[] {
  return [
    {
      id: "create",
      title: "Create in Plaid",
      description: "Create the fake transaction in Plaid Sandbox only.",
      status: transactionResponse?.created ? "success" : "unknown",
    },
    {
      id: "sync",
      title: "Pull sync",
      description: "Pull /transactions/sync directly.",
      status: syncResponse ? "success" : stepStatus(events, "plaid_transactions_sync_completed", "plaid_transactions_sync_failed"),
    },
    {
      id: "import",
      title: "Import result",
      description: "Review added, modified, removed counts.",
      status: syncResponse ? "success" : "unknown",
      detail: syncResponse
        ? `added ${syncResponse.added_count}, modified ${syncResponse.modified_count}, removed ${syncResponse.removed_count}`
        : null,
    },
    {
      id: "telegram",
      title: "Telegram",
      description: "Observe notification result if an ask_user row was imported.",
      status: telegramStatusFromEvents(events),
    },
  ];
}

function telegramStatusFromEvents(events: SandboxEvent[]): FlowStep["status"] {
  if (events.some((event) => event.event_type.includes("telegram_send_failed"))) return "failed";
  if (events.some((event) => event.event_type.includes("telegram_send_succeeded"))) return "success";
  if (events.some((event) => event.event_type.includes("telegram_send_skipped_duplicate"))) return "skipped";
  return "unknown";
}

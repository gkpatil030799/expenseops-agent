import type {
  SandboxCreateTransactionResponse,
  SandboxEventsResponse,
  SandboxReliabilityDefinition,
  SandboxReliabilityResult,
  SandboxReliabilityRunAggregate,
  SandboxRunResponse,
  SandboxScenarioDefinition,
  SandboxScenarioResult,
  SandboxScenarioRunAggregate,
  SandboxStatus,
  SandboxSyncResponse,
  SandboxTransactionRequest,
  SandboxWebhookResponse,
} from "./types";

async function sandboxApi<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/sandbox${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw data;
  return data as T;
}

export const sandboxApiClient = {
  status: () => sandboxApi<SandboxStatus>("/status"),
  scenarios: () => sandboxApi<SandboxScenarioDefinition[]>("/scenarios"),
  scenarioRuns: () => sandboxApi<{ results: SandboxScenarioResult[] }>("/scenario-runs"),
  reliabilityTests: () => sandboxApi<SandboxReliabilityDefinition[]>("/reliability-tests"),
  reliabilityRuns: () =>
    sandboxApi<{ results: SandboxReliabilityResult[] }>("/reliability-runs"),
  runReliabilityTest: (testId: string) =>
    sandboxApi<SandboxReliabilityResult>(`/reliability-tests/${testId}/run`, {
      method: "POST",
    }),
  runAllReliabilityTests: () =>
    sandboxApi<SandboxReliabilityRunAggregate>("/reliability-tests/run-all", {
      method: "POST",
    }),
  resetReliabilityRuns: () =>
    sandboxApi<{ cleared: boolean }>("/reliability-runs/reset", { method: "POST" }),
  runScenario: (scenarioId: string) =>
    sandboxApi<SandboxScenarioResult>(`/scenarios/${scenarioId}/run`, { method: "POST" }),
  runAllScenarios: () =>
    sandboxApi<SandboxScenarioRunAggregate>("/scenarios/run-all", { method: "POST" }),
  resetScenarioRuns: () =>
    sandboxApi<{ cleared: boolean }>("/scenario-runs/reset", { method: "POST" }),
  runE2E: () => sandboxApi<SandboxRunResponse>("/run-e2e", { method: "POST" }),
  createTransaction: (payload: SandboxTransactionRequest) =>
    sandboxApi<SandboxCreateTransactionResponse>("/create-transaction", {
      method: "POST",
      body: JSON.stringify({
        ...payload,
        amount: Number(payload.amount),
        date_transacted: payload.date_transacted || undefined,
        date_posted: payload.date_posted || undefined,
      }),
    }),
  fireWebhook: (webhook_code = "SYNC_UPDATES_AVAILABLE", trace_id?: string) =>
    sandboxApi<SandboxWebhookResponse>("/fire-webhook", {
      method: "POST",
      body: JSON.stringify({
        webhook_type: "TRANSACTIONS",
        webhook_code,
        trace_id,
      }),
    }),
  syncNow: (trace_id?: string) =>
    sandboxApi<SandboxSyncResponse>("/sync-now", {
      method: "POST",
      body: JSON.stringify({ trace_id }),
    }),
  events: (params: { trace_id?: string; limit?: number } = {}) => {
    const search = new URLSearchParams();
    if (params.trace_id) search.set("trace_id", params.trace_id);
    if (params.limit) search.set("limit", String(params.limit));
    const suffix = search.toString() ? `?${search}` : "";
    return sandboxApi<SandboxEventsResponse>(`/events${suffix}`);
  },
  resetEvents: () => sandboxApi<{ cleared: boolean }>("/reset-events", { method: "POST" }),
};

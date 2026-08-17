import type { Page, Request as PlaywrightRequest } from "@playwright/test";

import type {
  AgentConversation,
  AgentFeedbackCreate,
  AgentFeedbackOut,
  AgentMessage,
  AgentRunOut,
  AgentStreamEvent,
  AgentStructuredResponse,
} from "../../src/agent/contracts";

const NOW = "2026-08-15T07:00:00Z";

export type RecordedRequest = {
  method: string;
  pathname: string;
};

export type AgentTurnPostBody = {
  text?: string;
  client_message_id?: string;
  page_context?: unknown;
};

export type RecordedAgentStreamCall = {
  method: string;
  pathname: string;
  body: AgentTurnPostBody | null;
};

export type RecordedAgentFeedbackRequest = {
  method: string;
  pathname: string;
  body: AgentFeedbackCreate | null;
};

export type MockAgentApp = {
  conversation: AgentConversation;
  requests: RecordedRequest[];
  feedbackRequests: RecordedAgentFeedbackRequest[];
  setAgentEnabled: (enabled: boolean) => void;
  waitForArchiveRequest: () => Promise<void>;
  releaseArchiveRequest: () => void;
};

export type AgentStreamAttempt = {
  events: unknown[];
  pauseAfterIndexes?: number[];
  errorBeforeIndex?: number | null;
  delayMs?: number;
};

export const agentConversation: AgentConversation = {
  public_id: "conversation-public-1",
  title: "Spending questions",
  archived_at: null,
  created_at: NOW,
  updated_at: NOW,
};

export const spendingResponse: AgentStructuredResponse = {
  schema_version: "1.0",
  blocks: [
    {
      type: "text",
      text: "You spent $412.00 on Food & Dining last month.",
    },
    {
      type: "spending_summary",
      title: "Food & Dining spending",
      start_date: "2026-07-01",
      end_date: "2026-07-31",
      currency_code: "USD",
      spend_basis: "card",
      total_cents: 41_200,
      previous_total_cents: 32_600,
      credits_cents: 0,
      previous_credits_cents: 0,
      unknown_share_transactions: 0,
      previous_unknown_share_transactions: 0,
      unknown_credit_share_transactions: 0,
      previous_unknown_credit_share_transactions: 0,
      change_percent: null,
      highlights: ["Personal: $250.00", "Shared: $162.00"],
      top_categories: [
        {
          name: "Food & Dining",
          amount_cents: 41_200,
          transaction_count: 8,
          percentage: 100,
          previous_amount_cents: 32_600,
        },
      ],
      top_merchants: [
        {
          name: "Local Bistro",
          amount_cents: 16_800,
          transaction_count: 3,
          percentage: 40.8,
          previous_amount_cents: 12_000,
        },
      ],
    },
  ],
};

export const transactionResponse: AgentStructuredResponse = {
  schema_version: "1.0",
  blocks: [
    {
      type: "text",
      text: `I found one matching transaction. ${"unbroken".repeat(80)}`,
    },
    {
      type: "transaction_list",
      title: "Recent transactions",
      transactions: [
        {
          public_id: "transaction-public-1",
          merchant:
            "A deliberately long merchant name that must stay inside the mobile Agent card",
          amount_cents: 12_345,
          currency_code: "USD",
          occurred_on: "2026-08-10",
          category: "Food & Dining",
          status: "personal",
          pending: false,
        },
      ],
      total_count: 1,
    },
  ],
};

export function textResponse(text: string): AgentStructuredResponse {
  return { schema_version: "1.0", blocks: [{ type: "text", text }] };
}

export function canonicalMessages(
  response: AgentStructuredResponse,
  userText: string,
  clientMessageId = "browser-message-1",
): AgentMessage[] {
  return [
    {
      public_id: "message-user-1",
      conversation_public_id: agentConversation.public_id,
      role: "user",
      text: userText,
      structured_response: null,
      client_message_id: clientMessageId,
      feedback_eligible: false,
      feedback: null,
      created_at: NOW,
    },
    {
      public_id: "message-assistant-1",
      conversation_public_id: agentConversation.public_id,
      role: "assistant",
      text: null,
      structured_response: response,
      client_message_id: null,
      feedback_eligible: true,
      feedback: null,
      created_at: "2026-08-15T07:00:02Z",
    },
  ];
}

export function userMessage(userText: string, clientMessageId?: string): AgentMessage[] {
  return canonicalMessages(
    textResponse("This response is not available."),
    userText,
    clientMessageId,
  ).slice(0, 1);
}

export function successfulEvents({
  response,
  deltas,
  activity,
}: {
  response: AgentStructuredResponse;
  deltas: string[];
  activity?: "spending" | "transactions";
}): AgentStreamEvent[] {
  let sequence = 0;
  const values: AgentStreamEvent[] = [
    {
      schema_version: "1.0",
      sequence: sequence++,
      run_public_id: "run-public-1",
      type: "run_started",
      resumed: false,
    },
  ];
  if (activity) {
    values.push(
      {
        schema_version: "1.0",
        sequence: sequence++,
        run_public_id: "run-public-1",
        type: "tool_started",
        activity,
        message:
          activity === "spending"
            ? "Checking your spending…"
            : "Looking through your transactions…",
      },
      {
        schema_version: "1.0",
        sequence: sequence++,
        run_public_id: "run-public-1",
        type: "tool_completed",
        activity,
        message: activity === "spending" ? "Spending data is ready." : "Transactions are ready.",
      },
    );
  }
  deltas.forEach((delta) => {
    values.push({
      schema_version: "1.0",
      sequence: sequence++,
      run_public_id: "run-public-1",
      type: "assistant_delta",
      delta,
    });
  });
  values.push(
    {
      schema_version: "1.0",
      sequence: sequence++,
      run_public_id: "run-public-1",
      type: "structured_response",
      response,
    },
    {
      schema_version: "1.0",
      sequence: sequence++,
      run_public_id: "run-public-1",
      type: "assistant_completed",
      message: canonicalMessages(response, "Question")[1],
    },
    {
      schema_version: "1.0",
      sequence: sequence++,
      run_public_id: "run-public-1",
      type: "run_completed",
      run: completedRun(),
    },
  );
  return values;
}

export function failedEvents(message: string): AgentStreamEvent[] {
  return [
    {
      schema_version: "1.0",
      sequence: 0,
      run_public_id: "run-public-1",
      type: "run_started",
      resumed: false,
    },
    {
      schema_version: "1.0",
      sequence: 1,
      run_public_id: "run-public-1",
      type: "run_failed",
      run: { ...completedRun(), status: "failed", error_code: "agent_provider_failed" },
      code: "agent_provider_failed",
      message,
      retryable: true,
    },
  ];
}

export async function mockAgentApp(
  page: Page,
  {
    agentEnabled = true,
    agentReadOnly = true,
    initialConversation = false,
    messages = [],
    holdArchiveRequest = false,
  }: {
    agentEnabled?: boolean;
    agentReadOnly?: boolean;
    initialConversation?: boolean;
    messages?:
      | AgentMessage[]
      | ((streamCalls: RecordedAgentStreamCall[]) => AgentMessage[]);
    holdArchiveRequest?: boolean;
  } = {},
): Promise<MockAgentApp> {
  const requests: RecordedRequest[] = [];
  const feedbackRequests: RecordedAgentFeedbackRequest[] = [];
  const feedbackByMessage = new Map<string, AgentFeedbackOut>();
  let agentFeatureEnabled = agentEnabled;
  let conversationExists = initialConversation;
  let markArchiveStarted = () => {};
  const archiveStarted = new Promise<void>((resolve) => {
    markArchiveStarted = resolve;
  });
  let releaseHeldArchive = () => {};
  const archiveRelease = new Promise<void>((resolve) => {
    releaseHeldArchive = resolve;
  });
  page.on("request", (request) => recordRequest(requests, request));

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/context") {
      return route.fulfill({
        json: {
          user: {
            id: 1,
            email: "gunjan@example.com",
            display_name: "Gunjan Patil",
            avatar_url: null,
          },
          workspace: {
            id: 1,
            name: "Patil household",
            workspace_type: "household",
          },
          features: { agent: { enabled: agentFeatureEnabled, read_only: agentReadOnly } },
        },
      });
    }
    if (url.pathname === "/api/agent/conversations") {
      if (request.method() === "POST") {
        conversationExists = true;
        return route.fulfill({ status: 201, json: agentConversation });
      }
      return route.fulfill({ json: conversationExists ? [agentConversation] : [] });
    }
    const feedbackMatch = /^\/api\/agent\/messages\/([^/]+)\/feedback$/.exec(url.pathname);
    if (feedbackMatch) {
      const messagePublicId = decodeURIComponent(feedbackMatch[1]);
      const body = request.postDataJSON() as AgentFeedbackCreate | null;
      feedbackRequests.push({ method: request.method(), pathname: url.pathname, body });
      const existing = feedbackByMessage.get(messagePublicId);
      const feedback: AgentFeedbackOut = {
        schema_version: "1.0",
        public_id: existing?.public_id || "feedback-public-1",
        message_public_id: messagePublicId,
        conversation_public_id: agentConversation.public_id,
        run_public_id: "run-public-1",
        rating: body?.rating || "helpful",
        reason: body?.reason || null,
        created_at: existing?.created_at || NOW,
        updated_at: "2026-08-16T16:00:00Z",
      };
      feedbackByMessage.set(messagePublicId, feedback);
      return route.fulfill({ json: feedback });
    }
    if (url.pathname === `/api/agent/conversations/${agentConversation.public_id}`) {
      if (request.method() === "DELETE") {
        markArchiveStarted();
        if (holdArchiveRequest) await archiveRelease;
        conversationExists = false;
        return route.fulfill({ status: 204, body: "" });
      }
      const responseMessages =
        typeof messages === "function" ? messages(await agentStreamCalls(page)) : messages;
      const messagesWithFeedback = responseMessages.map((message) => {
        const feedback = feedbackByMessage.get(message.public_id);
        return feedback ? { ...message, feedback } : message;
      });
      return route.fulfill({
        json: {
          conversation: agentConversation,
          messages: messagesWithFeedback,
          messages_total: messagesWithFeedback.length,
          messages_offset: 0,
          messages_has_more: false,
        },
      });
    }
    if (url.pathname.endsWith("/turns/stream")) {
      return route.fulfill({ status: 500, json: { detail: "Stream shim was not installed" } });
    }
    return route.fulfill({ status: 503, json: { detail: "Unavailable in Agent browser test" } });
  });

  await page.route(/\/transactions(?:\?|\/|$)/, (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: [] });
    return route.fulfill({ status: 409, json: { detail: "Writes are disabled in Agent tests" } });
  });
  await page.route("**/splitwise/**", (route) => route.fulfill({ json: [] }));
  await page.route("**/splitwise/me", (route) =>
    route.fulfill({
      json: {
        id: 1,
        first_name: "Gunjan",
        last_name: "Patil",
        email: "gunjan@example.com",
      },
    }),
  );
  await page.route("**/ai/memory", (route) => route.fulfill({ json: [] }));
  return {
    conversation: agentConversation,
    requests,
    feedbackRequests,
    setAgentEnabled: (enabled) => {
      agentFeatureEnabled = enabled;
    },
    waitForArchiveRequest: () => archiveStarted,
    releaseArchiveRequest: releaseHeldArchive,
  };
}

export async function installAgentStream(
  page: Page,
  attempt: AgentStreamAttempt,
): Promise<void> {
  await installAgentStreamSequence(page, [attempt]);
}

export async function installAgentStreamSequence(
  page: Page,
  attempts: AgentStreamAttempt[],
): Promise<void> {
  if (!attempts.length) throw new Error("At least one Agent stream attempt is required");
  const scripts = attempts.map(
    ({ events, pauseAfterIndexes = [], errorBeforeIndex = null, delayMs = 8 }) => ({
      frames: events.map((event) => semanticFrame(event)),
      pauses: pauseAfterIndexes,
      errorAt: errorBeforeIndex,
      delay: delayMs,
    }),
  );
  await page.addInitScript(
    (browserScripts) => {
      const browserWindow = window as typeof window & {
        __expenseopsAgentE2E: {
          calls: RecordedAgentStreamCall[];
          release: (() => void) | null;
          waitingAt: number | null;
        };
      };
      const nativeFetch = window.fetch.bind(window);
      const state = {
        calls: [] as RecordedAgentStreamCall[],
        release: null as (() => void) | null,
        waitingAt: null as number | null,
      };
      browserWindow.__expenseopsAgentE2E = state;
      window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const request = input instanceof Request ? input : null;
        const url = new URL(request?.url || String(input), window.location.href);
        if (!url.pathname.endsWith("/turns/stream")) return nativeFetch(input, init);
        const script = browserScripts[Math.min(state.calls.length, browserScripts.length - 1)];
        const rawBody = typeof init?.body === "string" ? init.body : null;
        let body: AgentTurnPostBody | null = null;
        if (rawBody) {
          try {
            body = JSON.parse(rawBody) as AgentTurnPostBody;
          } catch {
            body = null;
          }
        }
        state.calls.push({
          method: init?.method || request?.method || "GET",
          pathname: url.pathname,
          body,
        });
        const signal = init?.signal || request?.signal;
        const encoder = new TextEncoder();
        return new Response(
          new ReadableStream<Uint8Array>({
            async start(controller) {
              let ended = false;
              const abort = () => {
                if (ended) return;
                ended = true;
                state.release?.();
                controller.error(new DOMException("Aborted", "AbortError"));
              };
              signal?.addEventListener("abort", abort, { once: true });
              try {
                for (let index = 0; index < script.frames.length; index += 1) {
                  await new Promise((resolve) => window.setTimeout(resolve, script.delay));
                  if (ended) return;
                  if (script.errorAt === index) {
                    ended = true;
                    controller.error(new TypeError("Simulated stream disconnect"));
                    return;
                  }
                  controller.enqueue(encoder.encode(script.frames[index]));
                  if (script.pauses.includes(index)) {
                    state.waitingAt = index;
                    await new Promise<void>((resolve) => {
                      state.release = resolve;
                    });
                    state.release = null;
                    state.waitingAt = null;
                    if (ended) return;
                  }
                }
                ended = true;
                signal?.removeEventListener("abort", abort);
                controller.close();
              } catch (error) {
                if (!ended) controller.error(error);
              }
            },
          }),
          {
            status: 200,
            headers: {
              "Content-Type": "text/event-stream; charset=utf-8",
              "X-Request-ID": "agent-e2e-request-1",
            },
          },
        );
      };
    },
    scripts,
  );
}

export async function waitForAgentStreamPause(page: Page, index: number): Promise<void> {
  await page.waitForFunction(
    (expected) =>
      (window as typeof window & {
        __expenseopsAgentE2E?: { waitingAt: number | null };
      }).__expenseopsAgentE2E?.waitingAt === expected,
    index,
  );
}

export async function releaseAgentStream(page: Page): Promise<void> {
  await page.evaluate(() => {
    const state = (
      window as typeof window & {
        __expenseopsAgentE2E?: { release: (() => void) | null };
      }
    ).__expenseopsAgentE2E;
    if (!state?.release) throw new Error("Agent stream is not paused");
    state.release();
  });
}

export async function agentStreamCallCount(page: Page): Promise<number> {
  return (await agentStreamCalls(page)).length;
}

export async function agentStreamCalls(page: Page): Promise<RecordedAgentStreamCall[]> {
  return page.evaluate(
    () =>
      (window as typeof window & {
        __expenseopsAgentE2E?: { calls: RecordedAgentStreamCall[] };
      }).__expenseopsAgentE2E?.calls || [],
  );
}

function semanticFrame(event: unknown): string {
  const record = event as { type?: unknown; sequence?: unknown };
  return `id: ${String(record.sequence ?? 0)}\nevent: ${String(record.type ?? "message")}\ndata: ${JSON.stringify(event)}\n\n`;
}

function completedRun(): AgentRunOut {
  return {
    public_id: "run-public-1",
    status: "completed",
    model_name: "gpt-5-mini",
    prompt_version: "expenseops-readonly-v1.0",
    input_tokens: 20,
    output_tokens: 8,
    total_tokens: 28,
    error_code: null,
    created_at: NOW,
    started_at: "2026-08-15T07:00:01Z",
    completed_at: "2026-08-15T07:00:02Z",
  };
}

function recordRequest(target: RecordedRequest[], request: PlaywrightRequest): void {
  const url = new URL(request.url());
  if (
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/transactions") ||
    url.pathname.startsWith("/splitwise") ||
    url.pathname.startsWith("/ai/")
  ) {
    target.push({ method: request.method(), pathname: url.pathname });
  }
}

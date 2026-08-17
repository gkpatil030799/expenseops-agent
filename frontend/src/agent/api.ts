import { api } from "@/lib/api";

import type {
  AgentConversation,
  AgentConversationDetail,
  AgentFeedbackCreate,
  AgentFeedbackOut,
  AgentPageContext,
  AgentStreamEvent,
  AgentTurnCreate,
} from "./contracts";
import {
  AgentProtocolError,
  parseAgentConversation,
  parseAgentConversationDetail,
  parseAgentConversationList,
  parseAgentFeedback,
  parseAgentStreamEvent,
} from "./validation";

const MAX_STREAM_BUFFER = 256_000;

export class AgentStreamError extends Error {
  readonly code: string;
  readonly status: number | null;
  readonly retryable: boolean;
  readonly correlationId: string | null;

  constructor({
    code,
    message,
    status = null,
    retryable = true,
    correlationId = null,
  }: {
    code: string;
    message: string;
    status?: number | null;
    retryable?: boolean;
    correlationId?: string | null;
  }) {
    super(message);
    this.name = "AgentStreamError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
    this.correlationId = correlationId;
  }
}

export async function listAgentConversations(): Promise<AgentConversation[]> {
  return parseAgentConversationList(await api<unknown>("/api/agent/conversations"));
}

export async function createAgentConversation(title?: string): Promise<AgentConversation> {
  return parseAgentConversation(
    await api<unknown>("/api/agent/conversations", {
      method: "POST",
      body: JSON.stringify(title ? { title } : {}),
    }),
  );
}

export async function loadAgentConversation(publicId: string): Promise<AgentConversationDetail> {
  const first = parseAgentConversationDetail(
    await api<unknown>(`/api/agent/conversations/${encodeURIComponent(publicId)}`),
  );
  if (!first.messages_has_more) return first;
  const offset = Math.max(0, first.messages_total - 100);
  return parseAgentConversationDetail(
    await api<unknown>(
      `/api/agent/conversations/${encodeURIComponent(publicId)}?message_limit=100&message_offset=${offset}`,
    ),
  );
}

export async function archiveAgentConversation(publicId: string): Promise<void> {
  await api(`/api/agent/conversations/${encodeURIComponent(publicId)}`, { method: "DELETE" });
}

export async function submitAgentFeedback(
  messagePublicId: string,
  payload: AgentFeedbackCreate,
): Promise<AgentFeedbackOut> {
  const feedback = parseAgentFeedback(
    await api<unknown>(
      `/api/agent/messages/${encodeURIComponent(messagePublicId)}/feedback`,
      {
        method: "POST",
        body: JSON.stringify({ rating: payload.rating, reason: payload.reason ?? null }),
      },
    ),
  );
  if (feedback.message_public_id !== messagePublicId) throw new AgentProtocolError();
  return feedback;
}

export async function streamAgentTurn({
  conversationPublicId,
  text,
  clientMessageId,
  pageContext,
  signal,
  onEvent,
}: {
  conversationPublicId: string;
  text: string;
  clientMessageId: string;
  pageContext?: AgentPageContext | null;
  signal?: AbortSignal;
  onEvent: (event: AgentStreamEvent) => void;
}): Promise<void> {
  const requestId = createClientRequestId();
  let response: Response;
  try {
    response = await fetch(
      `/api/agent/conversations/${encodeURIComponent(conversationPublicId)}/turns/stream`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "text/event-stream",
          "Content-Type": "application/json",
          "X-Request-ID": requestId,
        },
        body: JSON.stringify({
          text,
          client_message_id: clientMessageId,
          page_context: pageContext || null,
        } satisfies AgentTurnCreate),
        signal,
      },
    );
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new AgentStreamError({
      code: typeof navigator !== "undefined" && !navigator.onLine ? "offline" : "network_error",
      message:
        typeof navigator !== "undefined" && !navigator.onLine
          ? "You appear to be offline. Reconnect and retry."
          : "ExpenseOps lost the connection. Please retry.",
      correlationId: requestId,
    });
  }

  const correlationId = response.headers.get("X-Request-ID") || requestId;
  if (!response.ok) {
    const detail = await safeHttpError(response);
    throw new AgentStreamError({
      code: response.status === 401 ? "session_expired" : `http_${response.status}`,
      message: safeHttpMessage(response.status, detail),
      status: response.status,
      retryable: response.status === 408 || response.status === 409 || response.status === 429 || response.status >= 500,
      correlationId,
    });
  }
  if (!response.headers.get("Content-Type")?.toLowerCase().includes("text/event-stream")) {
    throw new AgentStreamError({
      code: "invalid_stream",
      message: "ExpenseOps returned an unreadable Agent response. Please retry.",
      status: response.status,
      correlationId,
    });
  }
  if (!response.body) {
    throw new AgentStreamError({
      code: "stream_unavailable",
      message: "Streaming is unavailable in this browser session. Please retry.",
      correlationId,
    });
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = new AgentSseParser();
  let terminal = false;
  let expectedSequence = 0;
  const acceptEvent = (event: AgentStreamEvent) => {
    if (terminal) {
      throw new AgentProtocolError("ExpenseOps received data after the Agent turn ended.");
    }
    if (event.sequence !== expectedSequence) {
      throw new AgentProtocolError("ExpenseOps received an out-of-order Agent response.");
    }
    expectedSequence += 1;
    onEvent(event);
    terminal = event.type === "run_completed" || event.type === "run_failed";
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      const events = parser.push(decoder.decode(value, { stream: true }));
      for (const event of events) {
        acceptEvent(event);
      }
    }
    for (const event of parser.finish(decoder.decode())) {
      acceptEvent(event);
    }
  } catch (error) {
    if (signal?.aborted) throw error;
    if (error instanceof AgentProtocolError || error instanceof AgentStreamError) throw error;
    throw new AgentStreamError({
      code: "stream_disconnected",
      message: "The Agent response was interrupted. Reload the conversation or retry.",
      correlationId,
    });
  } finally {
    reader.releaseLock();
  }
  if (!terminal) {
    throw new AgentStreamError({
      code: "stream_disconnected",
      message: "The Agent response ended early. Reload the conversation or retry.",
      correlationId,
    });
  }
}

export class AgentSseParser {
  private buffer = "";

  push(chunk: string): AgentStreamEvent[] {
    this.buffer += chunk;
    if (this.buffer.length > MAX_STREAM_BUFFER) {
      throw new AgentProtocolError("The Agent stream exceeded its safe buffer.");
    }
    const events: AgentStreamEvent[] = [];
    while (true) {
      const match = /\r?\n\r?\n/.exec(this.buffer);
      if (!match || match.index === undefined) break;
      const frame = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      const event = parseFrame(frame);
      if (event) events.push(event);
    }
    return events;
  }

  finish(chunk = ""): AgentStreamEvent[] {
    const events = this.push(chunk);
    if (this.buffer.trim()) {
      const event = parseFrame(this.buffer);
      if (event) events.push(event);
    }
    this.buffer = "";
    return events;
  }
}

function parseFrame(frame: string): AgentStreamEvent | null {
  let declaredType = "";
  const data: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    let value = separator < 0 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") declaredType = value;
    if (field === "data") data.push(value);
  }
  if (!data.length) return null;
  let decoded: unknown;
  try {
    decoded = JSON.parse(data.join("\n"));
  } catch {
    throw new AgentProtocolError("ExpenseOps received malformed Agent stream data.");
  }
  const event = parseAgentStreamEvent(decoded);
  if (declaredType && event.type !== declaredType) {
    throw new AgentProtocolError("ExpenseOps received a mismatched Agent stream event.");
  }
  return event;
}

function createClientRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `agent-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

async function safeHttpError(response: Response): Promise<string> {
  try {
    const text = (await response.text()).slice(0, 2_000);
    const parsed = JSON.parse(text) as { detail?: unknown };
    return typeof parsed.detail === "string" ? parsed.detail : "";
  } catch {
    return "";
  }
}

function safeHttpMessage(status: number, detail: string): string {
  if (status === 401) return "Your session expired. Sign in again to continue.";
  if (status === 404) return "This Agent conversation is unavailable.";
  if (status === 409) return detail || "That request is already being processed.";
  if (status === 429) return "Too many Agent requests. Wait a moment and retry.";
  if (status >= 500) return "The Agent service is temporarily unavailable. Please retry.";
  return detail || "ExpenseOps could not send that Agent request.";
}

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AgentSseParser,
  submitAgentFeedback,
  streamAgentTurn,
} from "./api";
import type { AgentStreamEvent } from "./contracts";
import { AgentProtocolError } from "./validation";

const streamBase = {
  schema_version: "1.0",
  run_public_id: "run-public-1",
} as const;

function deltaEvent(sequence: number, delta: string) {
  return {
    ...streamBase,
    sequence,
    type: "assistant_delta",
    delta,
  } as const;
}

function completedEvent(sequence: number) {
  return {
    ...streamBase,
    sequence,
    type: "run_completed",
    run: {
      public_id: "run-public-1",
      status: "completed",
      model_name: "gpt-5-mini",
      prompt_version: "expenseops-readonly-v1.0",
      input_tokens: 20,
      output_tokens: 8,
      total_tokens: 28,
      error_code: null,
      created_at: "2026-08-15T07:00:00Z",
      started_at: "2026-08-15T07:00:01Z",
      completed_at: "2026-08-15T07:00:02Z",
    },
  } as const;
}

function failedEvent(sequence: number) {
  return {
    ...streamBase,
    sequence,
    type: "run_failed",
    run: null,
    code: "agent_provider_failed",
    message: "ExpenseOps could not complete that request.",
    retryable: true,
  } as const;
}

function frame(event: object, lineEnding = "\n"): string {
  const type = "type" in event ? String(event.type) : "message";
  return [
    `id: ${"sequence" in event ? String(event.sequence) : "0"}`,
    `event: ${type}`,
    `data: ${JSON.stringify(event)}`,
    "",
    "",
  ].join(lineEnding);
}

function responseFromChunks(chunks: Uint8Array[]): Response {
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        chunks.forEach((chunk) => controller.enqueue(chunk));
        controller.close();
      },
    }),
    {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream; charset=utf-8",
        "X-Request-ID": "server-request-1",
      },
    },
  );
}

function byteIndex(haystack: Uint8Array, needle: Uint8Array): number {
  outer: for (let index = 0; index <= haystack.length - needle.length; index += 1) {
    for (let offset = 0; offset < needle.length; offset += 1) {
      if (haystack[index + offset] !== needle[offset]) continue outer;
    }
    return index;
  }
  return -1;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("AgentSseParser", () => {
  it("holds fragmented frames until their separator arrives", () => {
    const parser = new AgentSseParser();
    const payload = `${frame(deltaEvent(0, "You spent "))}${frame(completedEvent(1))}`;
    const firstBoundary = payload.indexOf("data:") + 9;
    const finalBoundary = payload.length - 1;

    expect(parser.push(payload.slice(0, firstBoundary))).toEqual([]);
    expect(parser.push(payload.slice(firstBoundary, finalBoundary))).toEqual([
      deltaEvent(0, "You spent "),
    ]);
    expect(parser.push(payload.slice(finalBoundary))).toEqual([completedEvent(1)]);
    expect(parser.finish()).toEqual([]);
  });

  it("supports CRLF, comments, and JSON split across multiple data lines", () => {
    const parser = new AgentSseParser();
    const event = deltaEvent(0, "Café ☕");
    const serialized = JSON.stringify(event);
    const splitAt = serialized.indexOf('"delta"');
    const payload = [
      ": heartbeat",
      "event: assistant_delta",
      `data: ${serialized.slice(0, splitAt)}`,
      `data: ${serialized.slice(splitAt)}`,
      "",
      "",
    ].join("\r\n");

    expect(parser.push(payload)).toEqual([event]);
  });

  it("rejects malformed JSON and an SSE type that disagrees with its payload", () => {
    const malformed = new AgentSseParser();
    expect(() => malformed.push("event: assistant_delta\ndata: {not-json}\n\n")).toThrow(
      /malformed Agent stream data/i,
    );

    const mismatched = new AgentSseParser();
    expect(() =>
      mismatched.push(`event: run_completed\ndata: ${JSON.stringify(deltaEvent(0, "hello"))}\n\n`),
    ).toThrow(/mismatched Agent stream event/i);
  });

  it("rejects unknown schema versions and semantic event types", () => {
    const version = new AgentSseParser();
    expect(() =>
      version.push(
        frame({
          ...deltaEvent(0, "hello"),
          schema_version: "2.0",
        }),
      ),
    ).toThrow(/version/i);

    const type = new AgentSseParser();
    expect(() =>
      type.push(
        frame({
          ...streamBase,
          sequence: 0,
          type: "raw_openai_event",
          payload: "must stay private",
        }),
      ),
    ).toThrow(/unknown Agent stream event/i);
  });
});

describe("streamAgentTurn", () => {
  it("preserves UTF-8 characters split across byte chunks", async () => {
    const encoder = new TextEncoder();
    const text = `${frame(deltaEvent(0, "Café ☕ — grounded"))}${frame(completedEvent(1))}`;
    const bytes = encoder.encode(text);
    const coffee = encoder.encode("☕");
    const coffeeAt = byteIndex(bytes, coffee);
    expect(coffeeAt).toBeGreaterThan(0);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseFromChunks([
          bytes.slice(0, coffeeAt + 1),
          bytes.slice(coffeeAt + 1, coffeeAt + 2),
          bytes.slice(coffeeAt + 2),
        ]),
      ),
    );
    const events: AgentStreamEvent[] = [];

    await streamAgentTurn({
      conversationPublicId: "conversation 1",
      text: "How much did I spend?",
      clientMessageId: "browser-message-1",
      onEvent: (event) => events.push(event),
    });

    expect(events).toEqual([deltaEvent(0, "Café ☕ — grounded"), completedEvent(1)]);
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      "/api/agent/conversations/conversation%201/turns/stream",
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
        body: JSON.stringify({
          text: "How much did I spend?",
          client_message_id: "browser-message-1",
          page_context: null,
        }),
      }),
    );
  });

  it("accepts run_failed as a terminal event", async () => {
    const bytes = new TextEncoder().encode(frame(failedEvent(0), "\r\n"));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseFromChunks([bytes])));
    const events: AgentStreamEvent[] = [];

    await expect(
      streamAgentTurn({
        conversationPublicId: "conversation-1",
        text: "Show spending",
        clientMessageId: "browser-message-2",
        onEvent: (event) => events.push(event),
      }),
    ).resolves.toBeUndefined();
    expect(events).toEqual([failedEvent(0)]);
  });

  it("rejects a stream that closes without a terminal event", async () => {
    const bytes = new TextEncoder().encode(frame(deltaEvent(0, "Incomplete")));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseFromChunks([bytes])));

    const result = streamAgentTurn({
      conversationPublicId: "conversation-1",
      text: "Show spending",
      clientMessageId: "browser-message-3",
      onEvent: () => undefined,
    });

    await expect(result).rejects.toMatchObject({
      code: "stream_disconnected",
      correlationId: "server-request-1",
    });
  });

  it.each([
    {
      name: "a duplicate terminal",
      events: [completedEvent(0), completedEvent(1)],
    },
    {
      name: "an event after the terminal",
      events: [completedEvent(0), deltaEvent(1, "Too late")],
    },
  ])("rejects $name", async ({ events }) => {
    const bytes = new TextEncoder().encode(events.map((event) => frame(event)).join(""));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseFromChunks([bytes])));
    const received: AgentStreamEvent[] = [];

    await expect(
      streamAgentTurn({
        conversationPublicId: "conversation-1",
        text: "Show spending",
        clientMessageId: "browser-message-terminal",
        onEvent: (event) => received.push(event),
      }),
    ).rejects.toBeInstanceOf(AgentProtocolError);
    expect(received).toEqual([completedEvent(0)]);
  });

  it("rejects a non-contiguous sequence before dispatching the offending event", async () => {
    const first = deltaEvent(0, "Grounded prefix");
    const bytes = new TextEncoder().encode(`${frame(first)}${frame(completedEvent(2))}`);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseFromChunks([bytes])));
    const received: AgentStreamEvent[] = [];

    await expect(
      streamAgentTurn({
        conversationPublicId: "conversation-1",
        text: "Show spending",
        clientMessageId: "browser-message-sequence",
        onEvent: (event) => received.push(event),
      }),
    ).rejects.toThrow(/out-of-order Agent response/i);
    expect(received).toEqual([first]);
  });

  it("rejects a mid-stream run identity change before dispatching it", async () => {
    const first = deltaEvent(0, "Grounded prefix");
    const switchedRun = {
      ...deltaEvent(1, "Must stay isolated"),
      run_public_id: "run-public-2",
    };
    const bytes = new TextEncoder().encode(
      `${frame(first)}${frame(switchedRun)}${frame(completedEvent(2))}`,
    );
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseFromChunks([bytes])));
    const received: AgentStreamEvent[] = [];

    await expect(
      streamAgentTurn({
        conversationPublicId: "conversation-1",
        text: "Show spending",
        clientMessageId: "browser-message-run-identity",
        onEvent: (event) => received.push(event),
      }),
    ).rejects.toThrow(/another run/i);
    expect(received).toEqual([first]);
  });
});

describe("submitAgentFeedback", () => {
  it("sends only the bounded feedback contract and validates the response", async () => {
    const feedback = {
      schema_version: "1.0",
      public_id: "feedback-public-1",
      message_public_id: "assistant/message 1",
      conversation_public_id: "conversation-public-1",
      run_public_id: "run-public-1",
      rating: "not_helpful",
      reason: "wrong_data",
      created_at: "2026-08-16T16:00:00Z",
      updated_at: "2026-08-16T16:00:00Z",
    } as const;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(feedback), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(
      submitAgentFeedback("assistant/message 1", {
        rating: "not_helpful",
        reason: "wrong_data",
      }),
    ).resolves.toEqual(feedback);

    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      "/api/agent/messages/assistant%2Fmessage%201/feedback",
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
        body: JSON.stringify({ rating: "not_helpful", reason: "wrong_data" }),
      }),
    );
    const options = vi.mocked(fetch).mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(options.body))).toEqual({
      rating: "not_helpful",
      reason: "wrong_data",
    });
    expect(String(options.body)).not.toMatch(/structured_response|answer|transaction|prompt/i);
  });

  it("rejects a feedback response bound to a different message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            schema_version: "1.0",
            public_id: "feedback-public-1",
            message_public_id: "different-message",
            conversation_public_id: "conversation-public-1",
            run_public_id: "run-public-1",
            rating: "helpful",
            reason: null,
            created_at: "2026-08-16T16:00:00Z",
            updated_at: "2026-08-16T16:00:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(
      submitAgentFeedback("message-assistant-1", { rating: "helpful", reason: null }),
    ).rejects.toBeInstanceOf(AgentProtocolError);
  });
});

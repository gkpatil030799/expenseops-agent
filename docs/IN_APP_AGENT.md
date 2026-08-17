# In-App ExpenseOps Agent

## Purpose and Day 3 scope

Day 3 makes the read-only ExpenseOps Agent available inside the authenticated web application. It
adds a responsive conversation surface and a semantic streaming transport over the Day 1 durable
agent foundation and the Day 2 read-only runtime. It does not expand the Agent's authority.

The original Day 3 slice answered grounded questions about spending and transactions. The current
read-only domain expansion is documented in `EXPANDED_READ_ONLY_AGENT.md`. It cannot change
a transaction, create a Splitwise expense, buy anything, send a Telegram message, modify an
errand, or call an arbitrary application endpoint. Those exclusions are enforced by the server's
tool registry and effect policy, not merely by UI copy or a model instruction.

This document is the implementation contract for the in-app experience. The platform-neutral
contracts are intentionally suitable for a later native client, but Day 3 delivers the responsive
web/PWA surface only.

## Build-versus-integrate decision

ExpenseOps integrates supported infrastructure where it removes generic work and keeps its own
product boundaries where tenancy, financial grounding, persistence, or privacy require them.

| Concern | Decision | Reason |
| --- | --- | --- |
| Model and tool loop | Use the pinned OpenAI Agents SDK and `Runner.run_streamed(...)` | The SDK owns model invocation, function-tool callbacks, turn progression, and the provider-stream lifecycle. ExpenseOps does not maintain a parallel model loop. |
| Application state | Keep ExpenseOps conversations, messages, runs, and tool-call audits in PostgreSQL | They already have authenticated workspace/user ownership, retention, deletion, idempotency, and RLS semantics. OpenAI conversation state is not mixed with this source of truth. |
| Tool authority | Keep the ExpenseOps `AgentToolRegistry` and trusted server context | The model cannot choose workspace/user identity, bypass effect policy, or call services outside the allowlist. |
| Customer streaming | Adapt SDK execution into versioned ExpenseOps semantic SSE | Raw provider events are implementation details and may contain partial JSON or ungrounded draft text. Clients receive only safe product events. |
| Browser transport | Use `fetch` with a `ReadableStream` | A turn is an authenticated `POST` with JSON input. Native `EventSource` cannot send that request shape. |
| SSE parsing | Use the small bounded repository parser | The current protocol is intentionally narrow. It handles LF/CRLF framing, comments, multiline `data`, schema validation, and a finite buffer without adding a dependency. |
| Rendering | Use a strict block renderer and existing ExpenseOps primitives | No raw HTML, arbitrary component payload, markdown runtime, or provider-defined UI crosses the boundary. |
| Responsive shell | Use a contextual desktop panel and a mobile Agent destination | Desktop users retain the underlying workspace context; smaller screens receive a focused full-screen experience rather than a cramped overlay. |
| Observability | Use ExpenseOps run/tool records and sanitized application logs | OpenAI tracing remains disabled so application data is not copied into a second unapproved trace store. |

The relevant OpenAI design surfaces are documented in the official
[Agents SDK overview](https://developers.openai.com/api/docs/guides/agents),
[running-agents guide](https://developers.openai.com/api/docs/guides/agents/running-agents),
[results and state guide](https://developers.openai.com/api/docs/guides/agents/results),
[streaming guide](https://developers.openai.com/api/docs/guides/streaming-responses),
[conversation-state guide](https://developers.openai.com/api/docs/guides/conversation-state), and
[function-calling guide](https://developers.openai.com/api/docs/guides/function-calling).

The Agents SDK version remains pinned in the repository. An upgrade is a dependency and behavior
change and must pass the full Agent regression suite before rollout.

## Streaming design

### Two distinct streams

The implementation deliberately separates provider execution streaming from customer-visible
response streaming:

1. `OpenAIAgentsRuntime` starts the supported SDK path with `Runner.run_streamed(...)` and consumes
   `result.stream_events()` while the SDK runs the tool loop.
2. Raw SDK/provider events are not forwarded to the browser, stored as chat fragments, or exposed
   as the public protocol.
3. Tool adapters publish only trusted, generic progress signals such as "Checking your spending"
   and "Transactions are ready." They never publish raw arguments, result rows, or credentials.
4. The model's typed final output is advisory. The orchestrator reconstructs the canonical answer
   from successful, validated tool evidence captured during that run.
5. The assistant message and terminal run state are committed atomically.
6. Only after that canonical commit does ExpenseOps emit bounded text deltas, the structured
   response, the completed assistant message, and the terminal run event.

This means the user sees progressive activity and text presentation without being shown raw model
tokens that could be partial JSON, later contradicted, or not grounded in canonical financial
data. The text deltas are slices of the already-grounded ExpenseOps response, not direct provider
token deltas.

### Public endpoint

```http
POST /api/agent/conversations/{conversation_public_id}/turns/stream
Accept: text/event-stream
Content-Type: application/json

{
  "text": "What were my top merchants last month?",
  "client_message_id": "caller-generated-id",
  "page_context": { "schema_version": "1.0", "surface": "expense_insights", "filters": {} }
}
```

The route requires the same authenticated user and active workspace as the non-streaming turn
endpoint. It is available only when both read-only Agent flags are enabled and is rate limited to
10 turns per minute for each workspace/user pair.

Each frame is a versioned semantic event:

```text
id: 3
event: tool_completed
data: {"schema_version":"1.0","sequence":3,"run_public_id":"...","type":"tool_completed","activity":"spending","message":"Spending data is ready."}

```

Responses set `Cache-Control: no-store, no-transform` and `X-Accel-Buffering: no`. The protocol
currently supports:

- `run_started`, including whether a terminal turn is being replayed;
- `tool_started` and `tool_completed` with a fixed activity type and safe status copy;
- `assistant_delta` containing canonical grounded text only;
- `structured_response` containing a strict ExpenseOps response contract;
- `assistant_completed` containing the canonical persisted assistant message;
- terminal `run_completed` or `run_failed`.

Every public event uses schema version `1.0`, a non-negative per-delivery sequence, and an optional
opaque run public ID. Unknown versions, event types, invalid shapes, mismatched SSE event names, and
malformed JSON fail closed in the client.

### Canonical event order

A successful first execution normally follows this order:

```text
run_started
  -> zero or more tool_started/tool_completed pairs
  -> canonical assistant and completed run committed
  -> one or more assistant_delta events
  -> structured_response
  -> assistant_completed
  -> run_completed
```

An error may end in `run_failed`. When a failed run has a persisted safe assistant error, the
stream may deliver its structured error response and assistant completion before the terminal
failure. Provider exceptions, stack traces, prompts, raw tool payloads, and database rows never
become public error content.

### Replay and idempotency

`client_message_id` is the idempotency key within a conversation. Resubmitting the same ID with the
same text and page context returns the existing terminal turn without another provider call or
duplicate tool execution. Its stream begins with `run_started` and `resumed: true`, then recreates
the canonical customer events from the persisted result.

The SSE `id` and `sequence` values are per delivery; they are not a durable event-log offset.
ExpenseOps does not promise `Last-Event-ID` replay. After an interrupted stream, the client reloads
the canonical conversation from the REST endpoint. A disconnect after the final commit does not
erase the answer; the reload retrieves it. A duplicate while the original run is still in progress
fails safely rather than starting parallel work. Reusing an ID with different content or context
is a conflict.

The UI's explicit Retry control represents a new user attempt and may use a new client message ID.
A transport retry that intends idempotent replay must retain and resend the original ID and input.

### Cancellation and disconnects

Closing or replacing the Agent view aborts the browser request. Server-side generator cancellation
propagates into the orchestration task, which is cancelled and awaited; the run is marked cancelled
on a best-effort basis. Cancellation does not introduce a second execution path or persist partial
assistant fragments.

There is an unavoidable commit boundary: if the canonical assistant response was committed just
before the client disconnected, cancellation does not roll that completed turn back. Reloading the
conversation is the recovery path. If the HTTP stream ends without `run_completed` or `run_failed`,
the client reports a recoverable interruption rather than treating partial content as final.

## Persistence and grounding guarantees

Stream fragments are transient presentation state. The database stores one canonical user message,
one terminal assistant message, run metadata, and audited tool calls. It does not store every text
delta. Final assistant persistence and the terminal run transition share one transaction so the UI
does not observe a completed run without its canonical answer.

Only successful same-run read-tool evidence may supply user-specific facts. At its Day 3 boundary,
the runtime exposed exactly:

- `get_spending_insights`, backed by the canonical Insights service;
- `search_transactions`, backed by a tenant-scoped transaction query.

The model can select and parameterize these tools within their schema, but the server rebuilds
financial blocks from their validated output. Top categories and top merchants are bounded,
canonical breakdowns rather than numbers repeated from model prose. Failed retrieval produces an
explicit error, not a plausible financial answer.

## Tenancy, authorization, and privacy

The client never supplies workspace or user identity to the Agent runtime. Authentication and the
active workspace are resolved by the server, then closed over the registry's trusted tool context.
Conversation and turn operations are scoped by both workspace and owner user, with PostgreSQL RLS
as defense in depth. A private conversation owned by another user or workspace is returned as not
found rather than disclosed.

Privacy properties of the Day 3 path include:

- `store=False` for OpenAI Responses and disabled OpenAI tracing/sensitive trace capture;
- bounded ExpenseOps-owned message history instead of SDK sessions or OpenAI Conversations;
- no credentials, provider access tokens, raw Plaid payloads, account numbers, or raw tool rows in
  the browser protocol;
- safe fixed tool progress labels instead of model arguments or query values;
- strict structured blocks and plain-text rendering, with no HTML injection or arbitrary links;
- no Agent chat written to local storage or session storage;
- no new analytics, advertising, notification, or third-party browser integration;
- normal authenticated account/workspace deletion and retention semantics for persisted Agent
  records.

OpenAI's current platform data controls are described in the official
[data-controls guide](https://developers.openai.com/api/docs/guides/your-data). Any future tracing or
external observability integration must receive a separate privacy and retention review; see the
[observability guide](https://developers.openai.com/api/docs/guides/agents/integrations-observability).

## Frontend architecture

### Shell and responsive behavior

The Agent is a lazy-loaded feature domain rather than another large section embedded directly in
the application shell.

- On desktop, a contextual panel can remain beside the current ExpenseOps workspace. Opening and
  closing it preserves the underlying page and returns focus to the launcher.
- On mobile, Agent is a primary navigation destination and uses the available viewport as a
  focused conversation surface. It is not a narrow desktop panel layered over the page.
- Conversation history uses the existing responsive sheet primitive and supports selecting,
  creating, and archiving private conversations.
- The message history is independently scrollable; the composer remains reachable above mobile
  safe-area insets.
- The feature domain reuses the existing `Surface`, `Button`, `Badge`, focus, spacing, color, and
  loading-state conventions.

The shell passes only a versioned allowlisted page-context object built from application state. It
does not scrape visible DOM text. The context label is visible to the user so phrases such as
"this category" are not resolved through invisible ambient state.

### Client modules

The frontend is split by responsibility:

- `AgentExperience.tsx` owns the responsive conversation UI, history surface, composer, progress,
  empty/loading/error states, and accessibility announcements;
- `useAgentController.ts` owns transient request/conversation state, cancellation, optimistic user
  presentation, terminal reconciliation, and retry/reload behavior;
- `api.ts` owns authenticated REST calls, POST streaming, bounded SSE parsing, and safe transport
  errors;
- `contracts.ts` mirrors the platform-neutral Python schema;
- `validation.ts` performs runtime validation at the network boundary;
- `AgentResponseRenderer.tsx` renders only supported canonical blocks and semantic navigation.

The stream parser maintains a maximum 256,000-character undecoded buffer, accepts LF or CRLF frame
separators, supports comments and multiline `data`, checks declared event names against payload
types, and requires a terminal event. The contract currently validates non-negative sequence
numbers; it does not claim durable or monotonic sequence replay across HTTP deliveries.

### Strict block rendering

The Day 3 renderer initially supported these read-only blocks:

- `text`;
- `spending_summary`;
- `transaction_list`;
- `empty`;
- `error`.

Amounts are received in integer minor units and formatted in the client; strings are rendered as
text. Unknown blocks or schema versions show a safe unavailable state. The renderer does not use
`dangerouslySetInnerHTML`, execute model-provided code, interpret arbitrary markdown, load remote
components, or turn arbitrary model URLs into navigation.

For `spending_summary`, Total and prior-period Total are non-negative eligible purchase spending;
category and merchant rows use the same purchase-only universe. Required `spend_basis` is either
`card` or `actual_share`, allowing the renderer to label Total card spend versus My actual share
without inference. `credits_cents` is a separate non-negative magnitude and never reduces the
primary Total. The flattened block also requires non-negative `previous_credits_cents`, current
and prior `unknown_share_transactions`, and current and prior
`unknown_credit_share_transactions`. Those period-specific counts separately disclose shared
purchases and credits omitted from actual-share results because ExpenseOps has no canonical viewer
allocation; the UI never guesses one. Confirmed amounts and deltas remain visible, but an exact
delta is labeled `Confirmed allocations only` and its percentage is suppressed when either
purchase period is incomplete. Category deltas are hidden and What changed is explicitly marked
incomplete rather than reporting no material change. Card-basis credits use raw
card magnitude and all four unknown-share counts must be zero. Direct Insights labels the
actual-share count as Confirmed transactions because omitted shared purchases are not counted.
The TypeScript network validator
enforces those invariants rather than accepting signed primary spending values. Persisted legacy
blocks missing the credit contract are projected as the agreed text plus recalculation empty state
instead of rendering retired net-spend numbers.

Supported navigation is semantic: a fixed application-owned action can request an existing
Expense Review, Insights, Activity, Deals, Household, or Settings destination. The shell performs
the actual allowlisted navigation. The model cannot provide an arbitrary route, callback, or
external URL.

### Accessibility and interaction

The in-app surface retains keyboard and screen-reader behavior expected of the rest of ExpenseOps:

- launchers, close/history/new/archive controls, prompts, and send actions are native buttons with
  accessible names and visible focus treatment;
- the desktop launcher reports expanded state and controls its panel;
- the composer uses a real label, supports Enter to send and Shift+Enter for a newline, respects
  input-method composition, limits input to 4,000 characters, and uses touch-sized controls;
- loading and streaming state set `aria-busy`; errors use an alert; a coarse polite live region
  announces meaningful progress without reading every text delta;
- animation has reduced-motion fallbacks;
- loading, empty, offline, session-expired, rate-limit, interruption, and safe server-error states
  remain usable on desktop and mobile.

## Feature flags and rollout

The frontend learns availability from the authenticated context bootstrap:

```json
{
  "features": {
    "agent": {
      "enabled": true,
      "read_only": true
    }
  }
}
```

`enabled` is true only when both `AGENT_ENABLED` and `AGENT_READ_TOOLS_ENABLED` are true. When it is
false, the desktop launcher and mobile destination are absent, an old Agent route falls back to the
normal product surface, and the frontend makes no Agent API calls. Read-turn endpoints independently
return not found when either flag is off. `AGENT_ENABLED=false` is the full Agent API kill switch;
with only read tools disabled, the foundation's private conversation APIs remain available but no
model or read-tool turn can run.

The following capabilities remain disabled and are not implied by the in-app UI:

- `AGENT_WRITE_ACTIONS_ENABLED=false`;
- `AGENT_PROACTIVE_ENABLED=false`;
- `AGENT_PURCHASING_ENABLED=false`.

A safe rollout enables the read-only flags for a controlled environment, verifies tenant isolation,
cost, latency, stream completion, cancellation, and grounded-answer quality, and expands exposure
only after those signals are healthy. Disabling either read flag removes and stops the in-app
read-only experience; disabling `AGENT_ENABLED` stops the entire Agent API. Existing conversation
data remains governed by normal retention and deletion behavior.

## Enforced limits

| Boundary | Current limit |
| --- | ---: |
| Turns per workspace/user | 10 per minute |
| User message | 4,000 characters |
| Client message ID | 64 characters |
| Conversation title | 120 characters |
| SDK turns | 4 |
| Read-tool calls | 3 |
| Total Agent wall time | 30 seconds |
| Individual tool time | 12 seconds |
| Model output | 800 tokens |
| History supplied to the model | 12 recent messages |
| Individual history message | 2,000 characters |
| Total model history | 12,000 characters |
| Transaction search results | 25 |
| Spending categories/merchants | 10 each |
| Structured response blocks | 50 |
| Conversation detail API | maximum 500 messages; UI loads the latest 100 when needed |
| Browser SSE pending buffer | 256,000 characters |

These are server- or client-owned safety bounds. The model, page context, and browser request cannot
raise the server execution limits.

## Verification strategy

The in-app Agent is release-ready only when the following remain green:

### Backend and protocol

- strict Python event-contract serialization and rejection of unknown/invalid payloads;
- semantic SSE framing, ordering, safe tool progress, canonical deltas, terminal success/failure,
  and early-disconnect behavior;
- SDK `run_streamed` coverage with deterministic fakes plus the separately gated live-provider
  smoke test;
- identical grounding and response semantics between streaming and non-streaming turn endpoints;
- idempotent terminal replay, input mismatch conflict, duplicate-in-progress behavior, cancellation,
  and final-only persistence;
- feature-off 404 behavior, workspace/user rate limiting, authentication, ownership, and
  cross-tenant not-found behavior;
- exact financial evidence, bounded merchant/category/transaction results, empty data, tool
  failure, provider failure, timeout, and zero-write regression tests.

### Frontend

- parser tests for arbitrary chunk boundaries, LF/CRLF, comments, multiline data, malformed JSON,
  event/type mismatch, unknown schema/type, bounded buffering, and missing terminal events;
- runtime contract and strict renderer tests for all supported blocks, exact minor-unit values,
  unknown-block fallback, plain-text safety, and fixed semantic navigation;
- controller tests for optimistic state, canonical reconciliation, reload after interruption,
  idempotency semantics, abort cleanup, retry, archive/new conversation, and safe HTTP states;
- browser tests at desktop and approximately 375-pixel mobile widths for panel/page behavior,
  scroll containment, safe-area composer placement, no horizontal overflow, keyboard operation,
  focus return, screen-reader announcements, feature-disabled zero Agent calls, and read-only copy;
- application TypeScript checking, linting, unit tests, production build, and the existing browser
  regression suite.

### Repository and operations

- Ruff, the complete backend suite, fresh and incremental migrations, and schema-head checks;
- no writes or external side effects in the registered Day 3 tool set;
- sanitized logs and support correlation IDs without secrets or raw financial payloads;
- rollout and rollback tests in the target environment using the two read-only feature flags.

## Non-goals and Day 4 exclusions

Day 3 intentionally does not include:

- write tools, approval cards, confirmation execution, or undo workflows;
- transaction edits, personal/shared classification, Splitwise posting, receipt reconciliation, or
  integration configuration;
- proactive recommendations, notifications, purchasing, carts, checkout, or merchant automation;
- Telegram replacement or changes to existing Telegram behavior;
- new read domains for deals, errands, receipts, replenishment, integrations, or household state;
- multi-agent orchestration, a generic workflow engine, MCP, arbitrary SQL/Python/shell/HTTP tools,
  vector search, or long-term semantic memory;
- raw provider-token streaming, provider event passthrough, arbitrary markdown/HTML, remote UI
  components, or arbitrary model-authored links;
- React Native, an APK, a separate native backend, offline answer generation, push notifications,
  or background mobile execution;
- context extraction from every product screen or unrestricted deep-link routing.

Day 4 may add explicitly scoped context adapters or read domains after their canonical services,
contracts, privacy behavior, and tenant tests are approved. Any `WRITE` or `EXTERNAL_ACTION` tool
requires a separate threat model, immutable proposal, exact preview and parameter hash, explicit
confirmation, idempotent execution, audit/recovery semantics, independent feature flags, and a new
release review. The existence of a Day 3 renderer contract must not be treated as authorization to
turn those future blocks or tools on.

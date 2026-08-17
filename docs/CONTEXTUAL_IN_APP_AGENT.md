# Contextual In-App ExpenseOps Agent

## Scope and outcome

Day 5 lets a new Agent turn use the small semantic context owned by the page the user is currently
viewing. It extends the read-only Agent delivered in Days 1–4; it does not create a second Agent,
conversation store, authorization path, tool registry, renderer, or browser memory layer.

Page context answers **where to look**, never **what is true**. The authenticated server session
still supplies the user and active workspace, and an existing tenant-scoped read tool still supplies
every user-specific fact. Context never enables a write or external action.

## Build-versus-integrate decisions

| Concern | Decision | Reason |
| --- | --- | --- |
| Page state | Add small page-owned adapters over existing React state | Insights, review, deals, household, receipts, errands, and settings already own their semantic selection/filter state. Copying all page data into a global Agent store would duplicate authority and increase rerenders. |
| Turn propagation | Reuse the existing typed `AgentPageContext` request field | Backend and TypeScript contracts, strict validation, per-run persistence, and idempotency comparison already exist. |
| Model/tool loop | Keep the pinned OpenAI Agents SDK runner | The SDK already owns model invocation and bounded tool-loop progression. No second orchestration loop is needed. |
| SDK context | Keep `RunContextWrapper` for trusted server dependencies only | SDK run context is available to tools/hooks and is not model-visible. ExpenseOps therefore continues to pass `ReadToolExecutor` there; the small page hint is explicit bounded model input. |
| Conversation state | Keep bounded ExpenseOps message replay | PostgreSQL remains the canonical, tenant-owned history. SDK sessions, OpenAI Conversations, and `previous_response_id` are not mixed into this path. |
| Entity facts | Re-resolve an identifier through an existing read tool | Client merchant names, headlines, receipt lines, item names, and errand titles are not factual authority. |
| UI | Reuse the responsive Day 3 panel/page and Day 4 cards | Day 5 adds context visibility, clearing, and intentional entity focus without redesigning chat or cards. |
| Evaluation | Extend repository tests and the existing scripted real-tool seam | Deterministic assertions over tool choice, arguments, canonical output, tenancy, and zero writes are more useful than introducing a new eval platform. |

The pinned `openai-agents==0.20.0` `RunContextWrapper` is deliberately not used as a hidden
page-context transport. Its context is dependency injection for local tools and lifecycle hooks;
it is not supplied to the model. The official running-agents documentation also recommends
choosing one conversation-state strategy because mixing histories can duplicate context. See the
[Agents SDK running guide](https://developers.openai.com/api/docs/guides/agents/running-agents).

## Context architecture

```text
page-owned semantic state
  -> strict, tiny AgentContextDescriptor
       - pageContext: AgentPageContext | null
       - safe local display label
  -> App owns only the current descriptor and explicit cleared state
  -> Agent controller snapshots it when Send begins
  -> POST /api/agent/.../turns/stream
  -> strict server contract + surface/entity compatibility validation
  -> same-tenant entity existence check before provider access
  -> bounded untrusted model hint
  -> existing tenant-scoped READ tool
  -> code-owned canonical semantic response

authenticated user/workspace -------------------------------> tool context
                                  (never copied from the page)
```

The frontend context module is platform-neutral. Adapters publish semantic values from component
state, not DOM text, selectors, component names, rendered datasets, callbacks, or arbitrary URLs.
The display label is a local UX value and is not added to the server payload.

### Safe fields

The request can contain only the versioned allowlisted surface, bounded filters, and at most one
typed entity reference. Useful filters include ISO start/end dates, account, category, merchant,
review status, spend basis, currency, and a bounded query where supported. An entity contains only
its closed kind and identifier.

It does not contain:

- `workspace_id`, `user_id`, role, or any authorization decision;
- a database object, provider payload, credential, or arbitrary URL;
- raw page/DOM text, transaction facts, deal copy, receipt lines, item facts, or errand notes;
- display labels used by the context chip;
- page transitions that did not participate in an Agent turn.

### Supported semantics

The implementation preserves existing contract names where they already describe the page. The
surface/entity compatibility rules prevent contradictory crafted combinations.

| Page | Semantic context | Canonical read authority |
| --- | --- | --- |
| Expense Review | Review filters; one deliberately focused transaction when present | `search_transactions` |
| Expense Insights | Exact visible date range, category/merchant/account/review, basis, currency | `get_spending_insights` |
| Expense Activity | Activity filters | `search_transactions` |
| Deals | Deal list filters; one deliberately focused deal | `get_relevant_deals` |
| Household | Current section; one deliberately focused household item or errand | `get_household_replenishment` or `get_errands_and_plan` |
| Receipt review/history | One deliberately opened receipt | `get_receipts` |
| Settings/integrations | Settings surface and one allowlisted integration where useful | `get_integration_status` |

Hover, keyboard focus, the first row in a list, and an arbitrary element left expanded are not
silently treated as the user's entity. A reference such as "this" is used only when there is one
compatible interpretation; otherwise the Agent asks a concise clarification before a model or
tool call can invent a selection.

## Lifecycle and precedence

Each Send snapshots the current descriptor. Navigation, filter changes, selection changes, and
Clear context affect the **next** turn only. They do not rewrite an optimistic message, an in-flight
request, an idempotent retry, or an earlier `AgentRun.page_context_json`.

An uncertain transport retry keeps the original text, client message ID, and nullable page-context
snapshot. This preserves the existing exact-input idempotency contract even if the user navigates
before selecting Retry.

The interpretation order is:

1. explicit wording in the current user message;
2. the compatible current-page hint captured for that turn;
3. bounded canonical conversation history for an obvious direct follow-up.

Thus "What about Travel instead?" is not pinned to a Food & Dining page filter. Context helps
resolve a missing referent; it does not override the user's domain, entity, or filter.

Clear context is intentionally session-only UI state. No financial or page context is written to
`localStorage` or `sessionStorage`. Reloading derives context again from the current application
page.

## Tenancy, grounding, and write boundary

Typed client validation is an ergonomics and minimization boundary, not authorization. Before a
contextual entity can influence a tool call, the backend re-resolves its identifier under the
authenticated active workspace. Missing, malformed, deleted, and cross-workspace identifiers have
the same external not-found behavior and stop before provider access.

The model sees the validated shape as explicitly untrusted data. It does not see client-supplied
entity facts. A hostile merchant, promotion headline, receipt line, or errand title can enter only
through a bounded canonical read result and remains data; it cannot alter the tool allowlist,
tenant scope, instructions, output renderer, or effect policy.

All registered runtime tools remain `READ`. Contextual requests such as "Split this with Gunjan",
"Map this line", "Complete this", and "Save this deal" return a truthful read-only response with
zero domain mutation, provider action, action proposal, and write-tool call. The rollout flags stay:

```text
AGENT_WRITE_ACTIONS_ENABLED=false
AGENT_PROACTIVE_ENABLED=false
AGENT_PURCHASING_ENABLED=false
```

## Semantic navigation

Navigation responses contain a closed application surface and optional compatible entity; they do
not contain a URL or callback. Both response validation and the application router use explicit
allowlists. Unknown targets and incompatible entity/surface pairs are ignored or rejected rather
than interpreted with prefix matching. Navigation never performs a domain mutation.

## Performance and reproducible measurement

Context overhead is measured independently from canonical tool evidence and assistant output.
Do not compare unmatched conversations, changing datasets, or different models and call that a
context delta.

### Deterministic local measurements

Run from the repository root after installing the locked dependencies:

```bash
.venv/bin/pytest -q tests/test_agent_day5_context.py
cd frontend && npm test -- --run src/agent/pageContext.test.ts
cd frontend && npm run build
```

The contextual backend tests exercise no-context, filter-context, and entity-context inputs with
the same Pydantic path used by production. The measurement record below uses:

- UTF-8 context payload bytes;
- UTF-8 model-input bytes before and after context;
- the prompt version, registered tool count, maximum tool calls, model turns, output tokens,
  history messages, history characters, and run timeout.

Payload size is `len(context.model_dump_json(exclude_none=True).encode("utf-8"))`. Model-input
size is compact, key-sorted UTF-8 JSON over the production `_sdk_input` return value; the delta
uses the same user turn and history with only `page_context` changed. Byte counts are deterministic
regression signals, not token estimates. The largest representative frontend adapter payload is
also asserted below the 1,536-byte contract ceiling in `pageContext.test.ts`.

### Live usage and latency

With an explicitly supplied OpenAI key, run the opt-in smoke once:

```bash
RUN_LIVE_AGENT_SMOKE=1 .venv/bin/pytest -q tests/test_agent_live_smoke.py
```

For a before/after comparison, use the same model, fresh conversation, data fixture, user wording,
and tool result. Record at least three paired turns and report the median and p95 separately for:

- `AgentRun.input_tokens`, `output_tokens`, and `total_tokens`;
- `AgentRun.latency_ms`;
- each `AgentToolCall.latency_ms`;
- provider request count.

Exact provider token counts should not be hard-coded in CI because tokenization and model behavior
can change. Hard limits and a small bounded delta are the regression assertions. Frontend tests
also assert that a context change neither remounts the Agent controller nor creates an extra
conversation-list request; full Playwright runs check document overflow at 320, 375, and 390 px.

### Measurement record

This record was captured on 2026-08-16 with the locked dependencies. A value is reported as not
run when the corresponding opt-in gate was not executed.

| Measurement | Result |
| --- | --- |
| No-context payload | 0 bytes |
| Largest tested semantic context | 734 bytes (1,536-byte hard ceiling) |
| Context model-input byte delta | 878 bytes; 52 bytes without context, 930 bytes with context |
| Prompt version and runtime budgets | `expenseops-readonly-v1.2`; 7 registered READ tools; 3 tool calls; 4 model turns; 30 s run; 12 s/tool; 800 output tokens; 12 history messages/12,000 chars |
| Frontend production assets | main JS 628.80 kB / 175.29 kB gzip; Agent chunk 54.06 kB / 14.04 kB gzip; CSS 58.08 kB / 10.75 kB gzip |
| Live input/output tokens | Smoke passed: contextual spending 7,003/246; contextual transaction 6,708/141; household 6,691/104. This is not a paired no-context delta. |
| Live run/tool latency | Smoke passed: contextual spending 5,128/8 ms; contextual transaction 3,416/12 ms; household 2,877/16 ms. A paired median/p95 benchmark was not run. |

## Verification matrix

The Day 5 regression suite covers the minimum requested behavior across four levels:

- strict contract/unit tests: valid/no/cleared context, surface/entity compatibility, bounded
  payloads, identity/raw-data rejection, explicit-over-context precedence, ambiguity, hostile data,
  tenant re-resolution, immutable run snapshots, idempotent retries, and zero-write behavior;
- deterministic contextual evals over real existing read handlers: Insights, transaction, deal,
  household-item history, receipt review, errand state, missing/deleted entities, cross-workspace
  identifiers, prompt injection, and contextual write requests;
- frontend unit tests: every page adapter, local-only labels, stable semantic equality, nullable
  send/retry snapshots, clear behavior, navigation allowlists, and no browser storage;
- Playwright E2E: exact posted `page_context`, navigation/filter/entity changes on the next turn,
  old-call immutability, clear-to-null, canonical cards, clarification, contextual refusal, zero
  forbidden requests, desktop panel continuity, and mobile widths across the configured Chromium,
  Firefox, WebKit, and mobile-Chromium projects.

Run the complete release gates with:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
cd frontend && npm test
cd frontend && npm run lint
cd frontend && npm run build
cd frontend && npm run test:e2e
```

Migrations and dependencies are unchanged by the frontend context adapters. If that changes, run
the repository's fresh-install, incremental-upgrade, Alembic drift, and dependency audit gates in
addition to the commands above.

## Deliberate limitations

Day 5 does not add broad multi-evidence composition, browser memory, vector memory, workflow or
multi-agent orchestration, proactive behavior, purchasing, native mobile state, or any write tool.
A contextual request should stay within one canonical tool where possible. Questions that truly
need broad deterministic composition remain Day 6 work rather than weakening grounding today.

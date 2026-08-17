# Expanded Read-Only ExpenseOps Agent

## Implemented outcome

ExpenseOps now exposes one authenticated, model-assisted read experience across spending,
transactions, household replenishment, receipts, promotions, errands, stored route plans, and
integration readiness. The implementation extends the existing Agent rather than creating a
second assistant, a second policy layer, or parallel domain logic.

The shipped authority remains deliberately narrow:

- eight registered tools are all classified as `READ`;
- user and workspace identity are derived from the authenticated server session;
- account facts are rebuilt from validated same-run tool evidence;
- domain mutations, provider actions, proactive behavior, and purchasing remain unavailable;
- the responsive web client renders versioned semantic blocks instead of model-authored UI.

“Read-only” describes domain and external-action authority. A turn still writes the Agent's own
conversation, message, run, and tool-call audit records so that retries are idempotent and the
result is recoverable. It does not update the financial, household, receipt, promotion, errand,
route, or integration records it reads.

## Architecture

```text
authenticated web request + active workspace
                  |
                  v
      UnifiedAgentService
      - private conversation ownership
      - idempotent user message and run
      - durable run/tool audit state
                  |
                  v
      official OpenAI Agents SDK
      - Agent + Runner.run_streamed
      - bounded model/tool loop
      - strict function-tool adapters
                  |
                  v
      AgentToolRegistry
      - explicit seven-tool allowlist
      - strict input/output validation
      - feature/effect policy
      - signed tenant-bound dispatch
                  |
                  v
      canonical services and read projections
      - financial insights and transactions
      - replenishment and acquisition history
      - receipts and receipt lines
      - ranked promotions
      - errands and stored plans
      - safe integration state
                  |
                  v
      code-owned grounded response
      -> versioned semantic SSE -> strict web renderer
```

ExpenseOps PostgreSQL remains the source of truth for domain data and Agent history. Each turn
replays bounded local history; it does not mix an SDK session, an OpenAI Conversation, or
`previous_response_id` with the application-owned conversation record.

### Official SDK reuse and application authority

`OpenAIAgentsRuntime` uses the official Agents SDK's `Agent`, `Runner.run_streamed`,
`FunctionTool`, `ModelSettings`, `RunConfig`, and `ToolContext` surfaces. SDK function
schemas pass through `ensure_strict_json_schema`, and the SDK owns provider invocation, tool-loop
progression, usage aggregation, and streamed execution.

The SDK does not replace the custom `AgentToolRegistry`. Each function callback passes parsed
arguments to `ReadToolExecutor`, which enters the existing registry's `prepare` and
`execute_read` route. The registry remains authoritative for:

- the exact allowlist and tool version;
- strict Pydantic input and output models with unknown fields rejected;
- rejection of tenant, identity, credential, and secret-shaped schema or payload keys;
- finite JSON values and safe output validation;
- effect and feature-flag policy;
- a time-bounded, HMAC-protected dispatch bound to the issuing workspace and user;
- revalidation immediately before handler execution.

The official SDK therefore removes generic orchestration work without becoming a second
authorization, tenancy, persistence, or financial-calculation system.

## Canonical domain reads

The Agent layer does not rerun ingestion, extraction, prediction generation, ranking, route
optimization, or provider synchronization. It reads already-established application state and
adapts it to small typed projections.

| Domain | Canonical source reused | Read projection |
| --- | --- | --- |
| Spending | `SpendingInsightsService.build` | Exact selected-period and comparable-period aggregates, currency scope, bounded category/merchant breakdowns, and deterministic notable changes. |
| Transactions | Tenant-scoped `ExpenseTransaction` query and the existing display-name rule | Deterministically ordered matching rows with supported date, merchant, category, review, amount, currency, and pending filters. Removed transactions stay excluded. |
| Household | `HouseholdItem`, current persisted `ReplenishmentPrediction`, `ReplenishmentService.should_surface`, `due_score`, and `due_state` | Canonical due states, safe evidence levels, package quantity/unit, and confirmed non-void acquisition history. The read path never generates a prediction. |
| Receipts | `PurchaseReceipt`, `PurchaseReceiptItem`, linked transaction, household item, and confirmed acquisition state | Recent/review/detail projections over stored parse and reconciliation facts. Detail lines are always selected through the tenant-scoped parent receipt. |
| Deals | `PromotionOffer`, workspace `PromotionSettings`, and persisted score breakdown | Active, non-suppressed, in-window offers meeting the canonical score-or-saved gate, with bounded deterministic relevance reasons. The read does not rescore or refresh promotions. |
| Errands | `Errand`, `ErrandPlan`, ordered stops and links, plus canonical plan fingerprint comparison | Current or filtered errands and an optional already-stored plan with code-computed freshness. The read does not resolve places or calculate a route. |
| Integrations | `IntegrationStatusService` | A safe local snapshot for Plaid, Gmail, Splitwise, Telegram, Google Maps, and OpenAI based on tenant records, consent/configuration, and last stored sync state. It performs no provider health call. |

## Seven-tool surface

The public tool surface is organized by user intent rather than by database table or CRUD
operation. All inputs and outputs use strict, extra-forbidden typed schemas.

| Tool | Bounded input | Canonical output |
| --- | --- | --- |
| `get_spending_insights` | Required ISO start/end dates, at most 730 days; optional account, category, merchant, review type, spend basis, and three-letter currency; a server-owned `same_weekdays_last_week` comparison mode is available only to the closed weekly-comparison flow | Non-negative eligible-purchase current/comparable aggregates, required `card|actual_share` basis, and separate non-negative current/prior credit magnitudes; current/prior unknown shared-purchase and shared-credit counts make actual-share omissions explicit, with exact percentages suppressed for incomplete purchase periods, while card basis includes raw eligible credit magnitude; up to 10 purchase-ranked categories, 10 purchase-ranked merchants, 4 purchase-based notable changes, and 16 available currencies; explicit cross-currency and pending exclusions. |
| `search_transactions` | Optional merchant, ISO date range, category, mutually consistent review filter, signed minor-unit amount bounds, currency, pending inclusion, and limit | Up to 25 minimal transaction rows plus `total_count`, `result_limit`, and `truncated`. Amount filters intentionally compare signed `amount_cents`, so refunds may be negative. |
| `get_household_replenishment` | `view=due|learning|item_history`; query, 0–90-day due horizon, item identifier only for history, and limit | Up to 20 items or 20 confirmed acquisitions; package quantity/unit, `likely_due|probably_due|not_due|learning`, qualitative confidence/evidence, last acquisition, count, snooze state, and truncation metadata. `learning` includes only enabled items without a current prediction or with insufficient evidence. |
| `get_receipts` | `view=recent|needs_review|detail`; merchant and bounded ingestion dates for lists, receipt identifier only for detail, list limit and line limit | Up to 20 receipt summaries or one detail with up to 25 lines; stored status, dates, minor-unit total/currency, safe match counts, transaction-link presence, household match name, confirmed-acquisition state, and truncation metadata. |
| `get_relevant_deals` | Optional deal identifier, category, bounded search text, 1–90-day expiry window, existing-need-only filter, and limit | Up to 12 current offers plus count/truncation; merchant, headline, category/type, discount and minimum-spend facts, currency, promo code, expiry, persisted score, saved/trust state, and up to 3 deterministic relevance reasons. |
| `get_errands_and_plan` | Optional mutually exclusive errand/plan identifiers, closed status filter, next-plan-only flag, optional latest plan, and limit | Up to 25 errands and optionally one stored plan; the semantic card preserves all 25 bounded errands, at most 12 stops, and 20 linked errands/items per collection, with counts and truncation/freshness markers. |
| `get_integration_status` | Optional unique closed provider list, one to six entries | One safe status per requested provider: label, personal/workspace/application scope, closed connection state, bounded message, and optional last successful sync time. |

Consolidation keeps closely related views inside one capability:

- aggregate spending and row-level transaction search remain separate because their semantics and
  evidence shapes differ;
- household due, learning, and item history share one replenishment vocabulary;
- receipt list, review queue, and detail share one parent-scoped receipt boundary;
- errands and the stored plan are returned together because plan usefulness and freshness depend
  on the errands it represents;
- one integration snapshot prevents six near-identical configuration tools.

This structure reduces tool-selection ambiguity and schema tokens, avoids exposing internal table
boundaries, and keeps a typical supported request inside the three-call run budget. Adding a new
view still requires a typed schema, canonical semantics, privacy review, and tenant tests; a
consolidated tool is not an unbounded query language.

## Grounded semantic responses

The model's final draft is advisory. `_grounded_response` selects validated evidence captured from
the same run and deterministically constructs `AgentStructuredResponse` version `1.0`. Model-authored
account numbers, amounts, statuses, dates, links, or actions are not copied into the canonical
answer.

The platform-neutral response union now includes these domain cards alongside existing text,
empty, error, and navigation blocks:

- `spending_summary` and `transaction_list`;
- `replenishment_summary`, including item history and explicit due/evidence state;
- `receipt_summary`, including bounded safe line-item matching facts;
- `deal_list`, including offer metadata and bounded relevance explanations;
- `errand_summary`, including optional stored-plan stops and freshness;
- `integration_status`, using closed provider, scope, and state enums.

Backend Pydantic contracts, matching TypeScript contracts, strict runtime validation, and an
allowlisted React renderer all enforce the same block shapes. Unknown versions, events, fields, or
block types fail closed. Customer streaming exposes ExpenseOps semantic progress and canonical
blocks, never raw provider events, partial provider JSON, or arbitrary HTML.

## Data sent to the model and client

Only the minimum fields needed to answer supported questions enter tool output. Relevant examples
are names and titles, canonical dates/statuses, integer minor-unit financial values, currencies,
bounded counts, qualitative replenishment evidence, stored offer facts, plan freshness, and safe
integration messages.

The following stay outside the tool surface:

- workspace IDs, user IDs, database session state, tenancy headers, and authorization context;
- Plaid access tokens, account/routing numbers, provider secrets, OAuth or refresh tokens, cookies,
  API keys, and encrypted credential material;
- raw Plaid payloads and unnecessary provider identifiers;
- receipt images/PDFs, full email bodies, Gmail message metadata, parser prompts, parser confidence
  internals, raw provider responses, and receipt source fingerprints;
- promotion source bodies, raw score-breakdown JSON, tracking URLs, provider ingestion metadata,
  and suppressed or inactive offers;
- errand notes, full addresses/coordinates, route polylines, raw route responses, provider place
  IDs, and plan input snapshots/fingerprints;
- numeric replenishment confidence, model artifacts, feature rows, feedback internals, and voided
  or unconfirmed acquisition evidence;
- provider diagnostics, stack traces, internal policy text, and arbitrary model-generated UI.

Tool arguments are persisted for audit after sensitive-key rejection. Tool results are not copied
wholesale into the audit record; the tool call stores bounded result metadata, latency, status, and
safe error information. Canonical assistant cards persist only the fields defined by the shared
response contract.

## Tenancy, injection, and write boundaries

### Tenant isolation

Authentication resolves the active workspace and user before orchestration. `ReadToolExecutor`
creates a worker-owned database session and copies only that trusted context. The model cannot
supply or override it.

Every domain read includes an explicit workspace predicate even where tenant-scoped ORM models and
PostgreSQL FORCE RLS provide defense in depth. Personal integration records additionally filter by
the authenticated user; workspace integration, financial, household, receipt, promotion, and
errand state is intentionally shared with members of the same active workspace. Agent
conversations remain private to their owner, including between members of one workspace.

Receipt-line tables are accessed only by joining through a workspace-scoped `PurchaseReceipt`;
linked transactions, household items, and acquisitions are independently re-scoped before their
identifiers or names are returned. Crafted cross-workspace entity IDs produce the same not-found
behavior as missing entities.

### Prompt-injection containment

Merchant names, receipt lines, promotion headlines and codes, errand titles, place names, page
context, conversation text, and all tool results are treated as untrusted data. Hostile strings are
preserved as literal user data where useful, but they cannot register a tool, change a feature
flag, alter tenant scope, create a side effect, or become executable UI. This is enforced by the
allowlist, strict schemas, server-owned context, code-owned grounding, and renderer—not by prompt
wording alone.

### No domain writes or external actions

The seven handlers do not flush or commit a domain mutation. They do not classify or remove a
transaction, post to Splitwise, create or correct an acquisition, parse or reconcile a receipt,
save or redeem a deal, create/complete an errand, generate/recalculate a route, modify an
integration, send a message, or purchase anything.

Recognized consequential wording is intercepted before the provider/tool loop and receives a
truthful read-only response. Independently, the registry makes side effects impossible through the
current seven-tool allowlist and requires any future `WRITE` or `EXTERNAL_ACTION` tool to use the
separate proposal/confirmation policy; no such tool is registered in this runtime.

## Empty, failure, cancellation, and retry behavior

A successful query with no matching rows produces a domain-specific `empty` block. It does not
turn zero rows into a recommendation, inferred transaction, likely receipt, or guessed status.
When no supported tool evidence exists, the response states which read domains are supported and
that no account data was retrieved.

Invalid tool arguments, unknown tools, malformed model output, retrieval failures, provider
failures, exhausted budgets, and timeouts fail closed with stable safe codes. A failed tool prevents
a plausible account answer; the terminal response is an `error` block with retryability and no raw
exception or provider body. Cancellation marks the run terminal and never persists a partial
assistant fragment.

Each client turn supplies a `client_message_id`. Retrying a completed turn replays its one canonical
result without another provider/tool execution. A conflicting reuse is rejected, and an uncertain
stream disconnect can retry the exact original ID without duplicating the optimistic or canonical
user message. Streaming consumers settle only after a terminal event.

## Budgets and performance measurement

Server-owned hard limits currently enforce:

| Budget | Limit |
| --- | ---: |
| Read-tool calls per run | 3 |
| SDK turns per run | 4 |
| Total run wall time | 30 seconds |
| Tool callback time | 12 seconds |
| Provider client timeout | 20 seconds, with at most 1 SDK client retry |
| Model output | 800 tokens |
| Replayed history | 12 messages |
| One history message | 2,000 characters |
| Total replayed history | 12,000 characters |
| Public turn rate | 10 per minute per workspace/user pair |

These are safety ceilings, not claimed latency objectives. Result-specific caps in the tool table
bound serialized evidence, response size, and renderer work. Some canonical read paths still scan
more database rows than they return; that residual is called out below and remains contained by
the database/tool/run timeouts rather than being described as a response-cap guarantee.

Measured with compact JSON containing each tool's `name`, `description`, and SDK `parameters`
schema, the current seven-tool catalog is 7,147 bytes (roughly 1,787 tokens using the deliberately
conservative characters/4 estimate). The original two-tool catalog is 2,824 bytes (roughly 706
tokens), so the expansion adds 4,323 bytes / about 1,081 estimated tokens and makes the catalog 2.53 times its
original size. The hard three-call, four-turn, and 800-output-token budgets were not increased.
Each deterministic single-domain acceptance scenario uses exactly one tool call.

A clean production build comparison against the responsive Agent baseline measured total frontend
assets at 708,781 -> 723,623 raw bytes (+14,842, +2.09%) and build-reported JavaScript/CSS gzip at
approximately 192,037 -> 194,964 bytes (+2,927, +1.52%). The lazy Agent chunk accounts for the
change: 36,468 -> 51,286 raw bytes (+40.6%) and approximately 10,628 -> 13,540 gzip bytes (+27.4%);
it remains absent from the disabled or unopened initial experience. These build and schema
measurements are reproducible engineering baselines, not production latency or billing claims.

The seeded deterministic single-domain scenarios produced compact canonical response payloads of
497–1,211 bytes and completed in 18.9–32.5 ms with the provider replaced by the deterministic test
seam; those timings measure application/tool/persistence overhead, not model latency. One real
provider smoke sample measured spending at 5,090 ms end-to-end / 8 ms tool time / 6,315 input and
239 output tokens, and replenishment at 3,426 ms / 14 ms tool time / 6,123 input and 107 output
tokens. These two live observations verify the vertical slices but are not a statistically valid
latency or cost SLO.

Performance should be measured with a fixed, seeded scenario set covering each view, empty state,
maximum legal result, and representative cross-domain request. Run each scenario cold and warm and
record:

1. end-to-end turn and individual tool latency from `AgentRun.latency_ms` and
   `AgentToolCall.latency_ms`;
2. provider request count and input/output/total tokens from the persisted run metadata;
3. database query count and database time using test/observability instrumentation;
4. returned row cardinality, truncation, canonical response bytes, and SSE event count;
5. timeout, cancellation, invalid-output, retry, and terminal failure rates.

Report p50, p95, and p99 separately for model time, tool time, and end-to-end time, tagged by tool
view, model name, prompt version, deployment revision, and cold/warm condition. Compare a candidate
revision against the same seeded baseline and investigate regressions before changing caps. Do not
log prompts, raw tool payloads, or customer financial content to obtain these measurements.

## Feature flags and operations

All Agent capability flags default to false:

```env
AGENT_ENABLED=false
AGENT_READ_TOOLS_ENABLED=false
AGENT_WRITE_ACTIONS_ENABLED=false
AGENT_PROACTIVE_ENABLED=false
AGENT_PURCHASING_ENABLED=false
```

The read experience is available only when both `AGENT_ENABLED` and
`AGENT_READ_TOOLS_ENABLED` are true. The web Agent entry point stays hidden when either is off, and
turn endpoints return not found without invoking the provider. Enabling the read slice also
requires `OPENAI_API_KEY`; missing provider configuration fails closed.

Recommended rollout keeps the write, proactive, and purchasing flags false, enables the two read
flags in a controlled environment, exercises one scenario per tool, and monitors run/tool latency,
safe error codes, token use, HTTP failures, and database pool pressure. `AGENT_ENABLED=false` is the
full kill switch; disabling it stops new Agent API work while retaining existing private
conversation and audit records under normal lifecycle policy.

The integration-status tool reports stored/configured readiness only. It must not be used as a
substitute for provider synthetic monitoring or deployment readiness checks. Production still
uses the repository release gate, `/readiness`, PostgreSQL RLS/FORCE RLS validation, the shared
rate limiter, normal backup/restore procedure, and request/correlation IDs for incident review.

## Verification coverage

The deterministic backend suites verify:

- strict tool metadata, closed enums, invalid filter combinations, date/range bounds, result caps,
  ordering, totals, and truncation;
- exact spending reconciliation with `SpendingInsightsService` and signed amount filtering;
- due/learning/history semantics, current-prediction selection, qualitative evidence, confirmed
  non-void acquisition history, and zero prediction or domain writes;
- recent/review/detail receipt semantics, parent-scoped lines and linked entities, and safe hostile
  receipt text;
- promotion eligibility/ranking, persisted relevance, expired/suppressed filtering, and zero
  settings creation or rescoring;
- errand filters, stored plan order/freshness, bounded linked data, and no routing call;
- personal-versus-workspace integration scope, consent/configuration states, last stored sync time,
  and zero provider calls;
- same-workspace sharing where intended, private conversation ownership, cross-workspace entity
  isolation, prompt-injection data, write refusals, and zero domain/provider side effects;
- registry tamper resistance, safe failures, wall/tool budgets, cancellation fencing,
  idempotency, final-only persistence, and canonical grounding.

Frontend unit tests cover fragmented UTF-8 SSE, LF/CRLF framing, malformed/unknown events,
sequence and terminal enforcement, the structured-response allowlist, and strict rendering.
Browser coverage exercises every expanded semantic card, hostile provider text, feature-disabled
zero requests, read-only refusals, responsive desktop and 320/375/390-pixel layouts, inner-scroll
ownership, horizontal overflow, keyboard/focus behavior, live regions, disconnect/retry
idempotency, and coexistence with existing ExpenseOps navigation. Release verification includes
the Playwright suite; the repository CI release gate runs backend tests and Ruff, frontend unit
tests, lint and production build, migration parity, dependency audit, and a container build.

The optional live-provider smoke remains separately gated because deterministic CI must not depend
on network access, credentials, cost, or model drift. A live smoke complements but never replaces
the tenancy, grounding, and zero-write suites.

## Dependency and migration status

The expanded seven-tool capability adds no package dependency. It reuses the repository's existing
`openai-agents==0.20.0` pin, OpenAI client stack, SQLAlchemy/Pydantic runtime, and frontend stack.
SDK upgrades remain deliberate dependency changes and require the complete Agent regression suite.

It also adds no database migration. The tools project existing domain tables and reuse the existing
Agent conversation/run/tool-call schema and idempotent-run index. Fresh-install and incremental
Alembic checks remain release requirements, but there is no schema operation specific to the
expanded read surface.

## Explicit limitations and debt

- Grounding currently renders the final successful tool result from a run. A future answer that
  must combine multiple tool outputs into one card set needs a deterministic multi-evidence
  composition contract rather than model-authored synthesis.
- Tool consolidation is intentional, but each additional view increases schema and handler
  complexity. Split a tool when views stop sharing one canonical vocabulary or privacy boundary.
- Household due state is an explainable forecast, not inventory truth. Package quantity/unit may
  be missing, and numeric confidence is intentionally withheld.
- Receipt facts inherit parser and user-review quality. Detail is capped, and the Agent cannot
  correct a parse, mapping, or acquisition.
- Deal relevance reflects persisted ranking features and known household evidence; it is not a
  claim that an offer is optimal, available at checkout, or worth purchasing. No live merchant
  availability or price verification occurs.
- Errand plans are stored snapshots. Freshness can identify changed inputs, but the Agent cannot
  recalculate the route or confirm current traffic, hours, or place availability.
- Integration status is configuration/storage truth, not a live provider probe. “Ready” or
  “connected” does not guarantee a provider request would succeed at that moment.
- Receipt child records rely on parent-scoped joins rather than carrying an independent workspace
  column. Any future direct child-table projection must preserve that parent join or add an
  equivalent tenant boundary.
- Page context remains typed and tenant-revalidated but covers only supported surfaces and entity
  kinds. It is context, never authorization or fresh account evidence.
- The model and prompt remain probabilistic. Strict schemas, registry policy, tenant filtering,
  code-owned grounding, and fail-safe rendering must remain mandatory after model or SDK changes.
- There is no long-term semantic memory, vector store, arbitrary SQL/Python/shell/HTTP tool,
  multi-agent handoff, proactive scheduler, write confirmation UX, or purchasing workflow in this
  capability.
- Production latency, cost, and answer-quality targets require measured traffic and a sanitized
  evaluation policy. The current hard budgets are safety controls, not evidence of an achieved SLO.
- Household candidate selection, stored-plan freshness, plan relationships, and integration
  aggregation can scan more tenant rows than their response caps. They avoid provider calls and
  are timeout-bounded, but production-scale SQL scan caps/projections remain performance debt.

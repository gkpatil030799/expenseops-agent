# Multi-Evidence ExpenseOps Agent

## Scope

Day 6 lets one read-only Agent run compose several validated same-run READ-tool results into one
canonical answer. It does not add a second agent, a workflow engine, memory, write authority,
browser caching, or model-authored account facts.

The authority boundary is unchanged:

```text
model plans existing READ tools
  -> ExpenseOps registry validates and executes each selected tool in the authenticated tenant
  -> narrow completeness guards may invoke a compatible missing pair half or an omitted,
     explicitly named attention read through that same registry (total remains at most three)
  -> successful bounded outputs enter a same-run evidence bundle
  -> ExpenseOps deterministically deduplicates, orders, and composes the evidence
  -> the existing versioned semantic response is persisted and streamed
```

Page context can narrow tool arguments, but it is still only an untrusted hint. It never becomes
evidence.

## Grounding limitation found

Days 2–5 correctly grounded single-domain answers because `_grounded_response` ignored the model's
financial draft and rebuilt a semantic block from the latest successful validated tool output. The
same choice discarded earlier successful outputs when a model called two or three tools: only
`executor.evidence[-1]` reached response construction. In addition, any recorded tool failure
failed the whole response even when another requested domain had already returned useful evidence.

The smallest safe change is an in-memory, same-run evidence bundle in front of the existing
domain response builders. It does not require another database table, an unbounded payload store,
or another model synthesis call.

## Build-versus-integrate decisions

| Concern | Decision | Reason |
| --- | --- | --- |
| Model/tool loop | Keep the pinned OpenAI Agents SDK with a fact-free terminal marker | The SDK already provides bounded model/tool selection and aggregate usage. Its strict terminal object now only confirms `evidence_collected`; it no longer asks the provider to draft financial cards that ExpenseOps will discard. |
| Account-fact synthesis | Build a small ExpenseOps composer | Deterministic domain precedence, exact amounts, dates, statuses, partial coverage, and semantic cards are product authority, not generic model/runtime concerns. |
| Explicit-request completeness | Build two narrow ExpenseOps guards | The SDK remains the general planner, but a validated spending/transaction half can safely supply its compatible missing half, and a bounded 2–3-area attention request can map only its explicitly named domains. A generic planner or combination-tool family would be broader and less auditable. |
| Programmatic Tool Calling | Do not add it | Existing outputs are already small and bounded, and each result may affect the model's next read. Multiple calls alone do not justify a new hosted execution path or model/dependency migration. |
| Parallel reads | Keep sequential execution for this beta | The current executor deliberately serializes persisted tool-call sequence, tenant-scoped sessions, cancellation, and a three-call budget. No paired measurement yet proves concurrency offsets the added failure and audit complexity. |
| Caching | Add none | Idempotent turn replay already avoids duplicate provider work. No repeated-read benchmark demonstrates a useful cache win, and financial responses must not enter browser storage. |
| Observability | Extend existing aggregate run/tool metadata and structured logs | `AgentRun`, `AgentToolCall`, SDK usage, and Railway logs already provide the needed seam without introducing another telemetry store. |
| Benchmarking | Add a provider-free repository harness | The required deterministic composition cases need repeatable CI/local measurements, not a hosted eval or a new benchmark dependency. |
| Evals | Keep deterministic repository tests plus the opt-in live smoke | Canonical reconciliation, tenancy, injection resistance, and zero mutation require exact assertions. A few live calls are observations, not an SLO. |

Official OpenAI model guidance recommends comparing representative tasks by task success,
completeness, evidence, tokens, latency, and cost; it also notes that multiple tool calls alone do
not require Programmatic Tool Calling. See [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model).

## Evidence bundle

`RunEvidenceBundle` is constructed only from successful `ReadToolEvidence` captured after registry
input/output validation. Each entry records:

- tool name and version;
- persisted call sequence;
- normalized bounded arguments;
- validated safe output projection;
- measured tool latency.

Failed calls are recorded separately by tool name and safe error code. The bundle selects the
highest-sequence terminal outcome for each canonical domain, preserving bounded Days 2–5 refine
behavior without combining stale and current results. Exact equivalent repeats collapse to their
highest sequence. A later distinct success replaces an earlier success or failure; a later failure
likewise makes that domain unavailable even if it succeeded earlier. Multiple outcomes claiming
the same sequence are ambiguous and fail closed. Every raw call still counts against the three-call
budget and remains in the normal tool-call audit/latency telemetry; only selected terminal outcomes
enter the composed bundle. A fixed domain order means selection sequence cannot arbitrarily reorder
the final response.

The bundle is ephemeral. The normal `AgentToolCall` audit record persists a result hash and small
semantic counts, not the raw output. No extra raw result, prompt, provider response, or chain of
thought is stored for Day 6.

## Composition rules

The composer receives the bundle, current user text, current date, and the code-owned read-only
action-refusal state. The provider returns only a strict `schema_version=1.0`,
`completion=evidence_collected` marker after tool selection; it cannot return a competing factual
card or prose draft for the composer to trust or discard.

- One successful domain preserves its existing Day 2–5 response behavior.
- Several successful domains produce one concise intro followed by existing canonical domain
  cards in fixed order.
- Exact duplicates do not create duplicate cards.
- A failed requested domain appears as bounded unavailable coverage while successful domains stay
  useful.
- All-empty checked domains produce a truthful empty result.
- No deterministic relationship is created merely because two strings seem similar.
- An appended write request may retain the read summary, but always adds the code-owned read-only
  refusal and performs no mutation or provider action.

Existing semantic blocks remain the source of detailed spending, transaction, replenishment,
receipt, deal, errand, and integration facts. A cross-domain attention block is justified only for
the concise category/count overview; it remains versioned, strictly validated, platform-neutral,
and paired only with semantic navigation.

### Narrow completeness guards

Prompt v1.4 still leaves general READ-tool planning to the model and explicitly requires every
user-named attention area within the three-call cap. A live smoke exposed one narrower failure:
the provider could stop after only one half of an explicit spending-comparison-plus-matching-
transactions request. ExpenseOps now deterministically checks that compound intent after the SDK
turn. If exactly one half succeeded, it derives compatible date/category/merchant/review/currency
scope from that normalized same-run evidence. Spend basis uses only the allowlisted explicit
`card`/`actual share` selector, then compatible context, then the canonical card default. The guard
then invokes the missing tool through the same tenant-bound registry executor.
Unrepresentable account, amount, transaction-ID, review-status, or ambiguous-basis scopes fail
closed. The guard never parses account identity or account facts from prose; its call is persisted,
timed, and counted against the unchanged three-call budget. Other combinations remain
model-planned.

For an attention-form question that positively names two or three supported areas, code maps only
those names to existing bounded READ inputs, exposes only those tools to the SDK, and completes an
omitted named read through the same executor. Negative cues are excluded; spending is not silently
mapped because it requires an explicit or contextual date scope. Requests with fewer than two,
more than three, or ambiguous named areas keep normal model planning. This is bounded
intent-to-existing-tool routing, not a new aggregate tool or authority for the returned facts.

### Attention ordering

Attention uses canonical state only:

1. **Action required:** a canonical unreviewed amount in the checked spending range,
   transactions awaiting review/reconciliation, receipts in `needs_review`/`failed`, and
   integrations whose canonical status is `attention_required`.
2. **Time sensitive:** open/planned errands that are due or high/urgent priority, relevant deals
   expiring within seven days, and household items whose canonical due state is `likely_due`.
3. **Useful to know:** `probably_due` household items, later relevant deals, routine open errands
   or an available stored plan, and other non-urgent evidence already labeled relevant by its
   domain.

No model score, arbitrary importance, merchant assumption, or inferred urgency changes this
ordering. Empty categories are omitted and each domain remains capped by its existing tool and
response limits. `items_truncated` is true when either the 12-item attention cap or any selected
source projection was truncated. In the latter case, prose says it inspected bounded records and
does not claim that unseen rows contain no attention items; unscoped counts are labeled “at least.”

### Domain combinations

- **Replenishment + deals:** the household service remains authority for `likely_due` /
  `probably_due`; Promotion Intelligence remains authority for current, need-related offers and
  expiry. The response says an offer *may be useful*, never that a purchase is required.
- **Spending + transactions:** Spending Insights owns aggregate/current/comparison totals.
  Transaction rows are supporting detail and never get re-summed into a competing total.
- **Receipts + replenishment:** receipt tool v1.1 exposes a bounded set of tenant-verified
  confirmed household-item IDs; the composer relates those only to identical IDs in the checked
  replenishment projection. Arbitrary receipt lines do not imply consumption or need, and missing
  IDs produce an explicit unverified-relationship sentence.
- **Household + errands:** errand tool v1.1 exposes canonical household-item IDs on errands and
  stored-plan stops. The composer intersects those with identical due-item IDs; it does not infer a
  link from names, merchants, places, or a retailer's likely inventory.

## Page context interaction

Day 5 precedence remains explicit user wording, then compatible current page context, then bounded
canonical conversation history. Context may resolve “this” to a household item or narrow a deal
query, but the corresponding same-tenant read tool must return the facts before they can enter the
bundle. Cross-workspace and missing contextual entities still stop before provider access.
For the established Insights-context “Why did this increase?” path, the policy exposes only
Spending Insights to the SDK unless current wording requests transactions or another domain; this
preserves the exact one-read behavior without treating page state as evidence. Explicit compound
requests retain the normal bounded tool set and the narrow completeness guards described above.

## Partial failure and no-evidence behavior

Coverage is classified as `complete`, `partial`, or `failed` from bundle contents, never from model
prose. Partial means at least one validated evidence set and at least one failed requested read.
The answer identifies the unavailable domain without storing or exposing raw exception text. If
every requested read fails, the existing safe failed-run behavior is retained. If every successful
read is empty, the response says that nothing needs attention in the ExpenseOps areas checked.

## Performance observability

### Existing telemetry reused

Before Day 6, ExpenseOps already persisted:

- `AgentRun.latency_ms`, input/output/total tokens, model, prompt version, status, and safe error;
- `AgentRun.metadata_json.provider_request_count` from Agents SDK `usage.requests`;
- `AgentToolCall` tool name/version/sequence/status/latency and safe result hash/count metadata;
- redacted structured events for completed/failed runs and tool calls;
- request/correlation IDs and tenant log context.

Agents SDK tracing remains disabled and `store=False` remains enabled. The application does not
turn on sensitive tracing merely to get performance numbers. There is no existing Prometheus,
StatsD, or product analytics dependency to reuse; Railway supplies service-level CPU, memory, and
HTTP metrics, while application-level Agent measurements stay in existing records/logs.

### Day 6 aggregate fields

Terminal run metadata/logging adds only bounded numeric or enum values:

- exact SDK turn count from the runner's public `current_turn`, provider request count from
  `usage.requests`, and SDK runtime latency;
- aggregate tool latency and completed/failed call counts;
- evidence-set count and `complete` / `partial` / `failed` coverage;
- deterministic composition latency;
- compact canonical response UTF-8 bytes and default structured-response payload UTF-8 bytes.

`sdk_turn_count` and `provider_request_count` are separate: the former is the runner turn and the
latter counts model-provider requests reported by SDK usage. A provider-latency value derived by
subtracting tool time executed inside the SDK loop from SDK-loop time is an upper-bound
provider-plus-orchestration estimate, not direct network/model latency, because SDK scheduling and
callback overhead remain inside it. Post-SDK completeness-guard reads remain in total tool latency
but are not retroactively subtracted from that SDK-loop estimate.
`canonical_response_bytes` measures the compact `exclude_none` projection used for stable local
comparison. `response_payload_bytes` measures the default Pydantic structured-response JSON,
including null/default fields, before any HTTP/SSE transport envelope. Neither value claims to be
wire bytes.
No raw prompt, page context, tool arguments/output, receipt line, merchant, deal headline, or
provider payload is added to metrics or logs.

## Deterministic benchmark

Run the provider-free suite from the repository root:

```bash
.venv/bin/python scripts/benchmark_agent_day6.py --repetitions 250 --warmups 25
```

Use `--format json` for a machine-readable result. The harness uses the production output models,
evidence bundler, composer, structured-response serialization, and Day 5 context policy. It has
exactly ten seeded scenarios:

1. spending-only;
2. transaction-only;
3. replenishment-only;
4. deal-only;
5. contextual single-domain;
6. replenishment + deals;
7. spending + transactions;
8. attention-summary multi-domain;
9. partial tool failure;
10. maximum legal bounded response.

Each iteration deep-copies the seed, strictly validates every tool output, constructs/deduplicates
the bundle, composes and revalidates the canonical response, and serializes two projections. The
compact `exclude_none` projection is the canonical comparison metric; the default projection,
including null/default fields, matches the application response-payload metric before any SSE or
HTTP envelope. `application_processing_ms` covers validation, bundling, composition, response
validation, and both serializations. `total_ms` additionally includes fixture-copy and
metric-projection overhead. The clock is `time.perf_counter_ns`; median uses
`statistics.median`; p95 is nearest-rank.

This is a local deterministic composition benchmark. Seeded validated outputs enter at the
post-handler evidence seam, so it does **not** execute database queries or tool handlers and is not
a tool-query latency benchmark. It also excludes network, OpenAI latency, browser rendering, and
production load, so it must not be represented as a production SLO. Evidence and response
projection bytes are exact UTF-8 JSON sizes, not token or wire-size estimates. Actual tool latency
is observed separately through `AgentToolCall.latency_ms` and the opt-in live smoke.

### Measurement record

The table below is populated only from a completed run in the current worktree. Machine speed and
background load affect timing; correctness, cardinalities, and byte counts are deterministic.

<!-- DAY6_BENCHMARK_RESULTS_START -->
Captured on 2026-08-16 with Python 3.13.3 on Darwin arm64, 25 warmups, and 250 measured
repetitions per scenario (2,500 observations):

| Scenario | App median ms | App p95 ms | Total median ms | Total p95 ms | State | Calls | Evidence sets | Failures | Evidence bytes | Compact bytes | Payload bytes |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| spending-only | 0.058 | 0.065 | 0.100 | 0.110 | complete | 1 | 1 | 0 | 1,322 | 742 | 796 |
| transaction-only | 0.051 | 0.057 | 0.083 | 0.089 | complete | 1 | 1 | 0 | 949 | 732 | 764 |
| replenishment-only | 0.044 | 0.050 | 0.072 | 0.080 | complete | 1 | 1 | 0 | 613 | 611 | 661 |
| deal-only | 0.044 | 0.047 | 0.072 | 0.077 | complete | 1 | 1 | 0 | 599 | 568 | 642 |
| contextual single-domain | 0.060 | 0.064 | 0.088 | 0.095 | complete | 1 | 1 | 0 | 567 | 568 | 642 |
| replenishment + deals | 0.088 | 0.095 | 0.140 | 0.150 | complete | 2 | 2 | 0 | 1,211 | 1,132 | 1,240 |
| spending + transactions | 0.112 | 0.121 | 0.184 | 0.198 | complete | 2 | 2 | 0 | 2,270 | 1,464 | 1,534 |
| attention summary | 0.107 | 0.118 | 0.177 | 0.190 | complete | 3 | 3 | 0 | 1,648 | 1,081 | 1,203 |
| partial tool failure | 0.097 | 0.104 | 0.163 | 0.173 | partial | 3 | 2 | 1 | 1,566 | 906 | 998 |
| maximum legal bounded response | 0.420 | 0.445 | 0.816 | 0.865 | complete | 3 | 3 | 0 | 16,585 | 6,666 | 7,126 |

Across all scenarios, application processing measured 0.082 ms median / 0.420 ms p95 and the
complete deterministic iteration measured 0.130 ms median / 0.816 ms p95. Measured suite wall
time was 538.705 ms. The intentionally seeded partial case is one of ten scenarios (10%); that is a
test-fixture rate, not a production failure estimate. The registered tool-schema projection was
9,073 bytes, up 1,781 bytes (24.42%) from the 7,292-byte Day 5 record. The strict provider
completion-schema projection fell from 7,274 bytes to 399 bytes (94.51%) after replacing discarded
financial-card drafts with the fact-free terminal marker. Together those two schema projections
fell from 14,566 to 9,472 bytes (34.97%); these are JSON byte comparisons, not token estimates. The
schema comparison excludes prompt-instruction text; live aggregate input tokens capture the
combined request instead. The 16,585-byte maximum seed evidence was reduced to a 6,666-byte
compact canonical response and a
7,126-byte default response payload by the fixed 8-transaction, 8-replenishment-item, 6-deal, and
12-block multi-domain caps; raw evidence is not persisted for this benchmark.
<!-- DAY6_BENCHMARK_RESULTS_END -->

The registered tool and strict provider-completion schema projections are measured separately.
Live SDK aggregate input/output tokens include instructions, history, schemas, tool calls/results,
and final output; the pinned SDK does not expose an authoritative “tool-result tokens only” field.
Therefore Day 6 reports exact tool-result bytes locally and aggregate tokens from live calls rather
than presenting a byte-based token guess as fact.

The production frontend build keeps the Agent and attention renderer in the existing lazy chunk:

| Asset | Day 5 raw / gzip | Day 6 raw / gzip | Result |
| --- | ---: | ---: | --- |
| Agent lazy chunk | 53.94 / 14.02 kB | 60.19 / 15.39 kB | +6.25 / +1.37 kB for attention contracts and UI |
| Main application | 628.84 / 175.32 kB | 628.82 / 175.30 kB | Essentially flat; attention UI did not move into main |
| CSS | 58.08 / 10.77 kB | 58.48 / 10.82 kB | +0.40 / +0.05 kB |

## Live-provider observations

The opt-in smoke uses the configured model and synthetic tenant data:

```bash
RUN_LIVE_AGENT_SMOKE=1 .venv/bin/pytest -q tests/test_agent_live_smoke.py \
  --junitxml=/tmp/day6-live.xml
```

The smoke includes one explicit spending-plus-transactions observation and one explicit
named-attention observation. The first asserts both persisted calls use the same exact dates,
`Restaurants` category, and `USD`, with card basis and pending rows excluded, before accepting the
canonical supporting-detail composition. The second explicitly requests transaction reviews, due
household items, and integration readiness. The latter asserts the corresponding three READ calls
and exact canonical checked-domain coverage; deterministic evals still own the broader planning
matrix rather than a single provider sample. JUnit properties record each run's latency,
individual/aggregate tool latency, selected safe tool names, SDK turns, provider requests,
input/output/total tokens, evidence/failure counts, coverage, composition time, and both response
projections. A small number of model calls demonstrates that the integration works; it does not
establish a production median, p95, cost baseline, or SLO.

Captured on 2026-08-16 with the configured `gpt-4.1-mini` provider and the smoke's synthetic tenant
fixture (`1 passed` in 20.85 seconds):

| Individual observation | Run / SDK / provider+orchestration estimate ms | Tool latency ms | SDK turns / provider requests | Calls / evidence / failures | State | Compact / payload bytes | Input / output / total tokens |
| --- | ---: | --- | ---: | ---: | --- | ---: | ---: |
| Spending + transactions | 4,425 / 4,408 / 4,396 | 23 total: insights 12, transactions 11 | 2 / 2 | 2 / 2 / 0 | complete | 803 / 873 | 5,478 / 84 / 5,562 |
| Named attention | 6,427 / 6,422 / 6,383 | 39 total: transactions 11, household 12, integrations 16 | 4 / 4 | 3 / 3 / 0 | complete; 2 items | 857 / 949 | 7,498 / 177 / 7,675 |

Both composition measurements rounded to 0 ms at integer telemetry resolution. Named attention
selected exactly `search_transactions`, `get_household_replenishment`, and
`get_integration_status`. These are two individual integration observations, not distributional
statistics or SLO evidence.

## Parallelism and caching decision

The Day 6 runtime deliberately retains `parallel_tool_calls=False` and
`max_function_tool_concurrency=1`. Read operations may be logically independent, but the current
executor's sequence/budget ledger and one-session-per-call audit behavior are intentionally simple.
Parallel execution should be reconsidered only with a planner that declares one independent batch,
per-call isolated cancellation, deterministic sequence reservation, and a paired latency benchmark
that shows a material win without tenancy or recovery regressions.

No response or tool cache is added. The idempotency key already makes exact transport retries return
the original terminal turn. If future traces show repeat-read waste, evaluate provider prompt-cache
usage and an authenticated, tenant-keyed short-lived application cache separately; never cache
financial evidence in browser storage.

## Privacy and security verification

Multi-evidence tests must prove each tool independently derives the workspace/user from trusted
server state. A safe first tool cannot launder a crafted cross-tenant second identifier into the
bundle. Hostile merchant names, receipt lines, deal headlines, and errand titles remain escaped,
bounded data and are never interpreted as instructions by the composer.

The rollout flags remain:

```text
AGENT_WRITE_ACTIONS_ENABLED=false
AGENT_PROACTIVE_ENABLED=false
AGENT_PURCHASING_ENABLED=false
```

Day 6 adds no package dependency or database migration. It versions the receipt and errand READ
tool projections to v1.1 for bounded exact household-item IDs. It does not add proactive attention,
purchases, long-term memory, embeddings, a vector database, multi-agent orchestration, or a
workflow engine.

## Validation

Focused validation:

```bash
.venv/bin/pytest -q tests/test_agent_day6_benchmark.py
.venv/bin/python scripts/benchmark_agent_day6.py --repetitions 250 --warmups 25
```

The full gate remains backend tests and Ruff; frontend unit/lint/build; full Playwright; applicable
Alembic head/drift checks; dependency audit only if dependencies change; opt-in live smoke; and
`git diff --check`.

## Deliberate limitations and Day 7 recommendation

- Outside the explicit spending-plus-transactions and 2–3 named-attention guards, model planning
  can still omit a relevant domain. Canonical composition never fabricates missing evidence; the
  guards execute only existing tenant-bound READ tools and do not become a generic automatic
  planner.
- Sequential reads favor auditability over speculative latency gains.
- Deterministic local timing does not include service-query, provider, browser, or production-load
  distributions.
- Aggregate SDK usage does not isolate tool-result tokens or direct model/network latency.
- No long-term memory, personalization engine, write proposal execution, or proactive scheduler is
  introduced.

Day 7 should harden the beta with production-safe sampled aggregate monitoring, paired representative
live measurements, failure/cancellation exercises, and user-feedback/eval review. Parallel reads or
caching should remain conditional on those measurements; write authority should stay a separate,
explicitly confirmed phase.

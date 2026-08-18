# Day 17 Agent intelligence and UX cleanup

## Status and scope

Day 17 is a focused quality change over the Day 16 checkpoint. It does not add a new product
domain, data authority, model turn, write tool, provider, migration, or dependency. It makes the
existing Agent preserve a user's analytical objective, resolve time deterministically, expose the
smallest canonical read capability, compose the requested fact first, and render that result safely
inside the existing desktop companion and mobile Agent.

The permanent deterministic benchmark is `scripts/benchmark_agent_day17.py`. It pins the instant
to `2026-08-18T01:30:00Z`, the user timezone to `America/Phoenix`, and local today to 2026-08-17.
Its fixtures are synthetic and make no provider or production-data call.

## Reproduced failures and their seams

| Real-user failure | Reproduced failure layer | Day 17 correction |
| --- | --- | --- |
| Top category returned a generic total | The canonical spending result reached a composer that knew only the tool name, not the requested analytical objective | `top_categories` survives in a closed query plan and produces a direct category, amount, share, count, and category-only card |
| Top five merchants returned a generic summary or a silently sliced list | Requested N was not retained; the renderer also sliced every breakdown to five | `top_merchants` carries a bounded `top_n`; the server returns at most that N and the ordered renderer displays every returned row with amount, count, and share |
| “This month” could behave like a rolling period | Date arguments were largely delegated to the model, the runtime used a UTC date, and Insights presets were calculated from the browser clock | One shared backend resolver maps each closed phrase in the user's configured timezone; the Agent and authenticated Insights presets now use the same calendar ranges |
| Typical restaurant check could be unsupported or vague | Lifestyle routing could narrow the tool, but “typical check” was not retained as a response objective; the UI labeled an average “Typical” | `average_check` routes to Lifestyle & Dining and explicitly says “average”; no median is invented |
| Restaurant increase repeated two totals | Lifestyle output lacked a cross-period merchant delta and the generic composer did no decomposition | Canonical code supplies merchant changes and composes total, purchase-count, average-check, and largest measured merchant changes |
| Recent staple candidates could route to due items | Replenishment due-state and classification learning history were not separated by a typed intent or a recent local-date read | `recent_learning` uses classification activity `view=staple_candidates`; copy explicitly says candidates are not predicted due |
| Week comparison could be unsupported and did not lead with a conclusion | A six-phrase regex rejected normal grammar mistakes; generic spending copy required the user to infer the direction | The typed comparison accepts the measured variants, preserves same-weekdays semantics, and starts with Yes/No plus absolute and meaningful percentage change |
| Narrow replenishment rows broke into character-sized columns | Name, badge, and navigation were forced into one flex row; viewport breakpoints did not reflect the 22-rem companion; `overflow-wrap:anywhere` permitted single-character breaks | Rows use container-safe `minmax(0,1fr)` grids, full-width text, wrapping badges/actions, `min-w-0`, `break-words`, and auto-fit metric grids |

The same seam also explained coffee wording, “anything you're unsure about?”, the typo-heavy
restaurant query, learning summaries, and the contextual “why did this increase?” question. The
problem was not one prompt. It crossed routing, temporal interpretation, objective retention,
canonical output, contracts, and rendering.

## Query-objective architecture

`app/agent/query_planning.py` defines a frozen, closed `AgentQueryPlan`. Its objective enum covers:

- total spend, spending comparison, top categories, top merchants, and transaction rows;
- average check, lifestyle total/frequency, and change explanation;
- recent learning, learning summary, and uncertain classifications;
- the existing receipt, replenishment, and attention intents needed for policy compatibility.

A plan contains only code-reviewed fields: domain, one tool name, resolved current and optional
comparison ranges, bounded top N, lifestyle subtype, classification view, and the closed weekly
comparison mode. The plan validates objective/domain/tool agreement at construction. It has no
workspace ID, SQL, URL, provider token, mutation verb, or arbitrary tool name.

Planning precedence is:

1. an explicit date/activity/objective in the latest user wording;
2. compatible semantic page filters;
3. a typed, bounded conversational carry reconstructed from recent user turns;
4. the documented objective default.

The four-turn dining chain reconstructs typed plans from at most the recent three user turns. The
third turn retains both month ranges as an exact comparison pair, and the fourth searches the union
of those two ranges. An explicit new period clears incompatible carry. Ambiguous selectors and
control-only text fail back to the existing SDK planner instead of being guessed.

## Tool routing and dynamic exposure

For a high-confidence Day 17 plan, the official Agents SDK receives exactly the selected existing
read tool and a named tool choice. The executor overlays the code-owned range/objective arguments,
then validates them through the existing strict registry. One-tool analytical turns therefore use:

```text
natural language
  -> closed query objective and local date range
  -> one existing canonical READ tool
  -> validated canonical output
  -> objective-aware deterministic composer
  -> strict semantic block and SSE
```

This does not remove the SDK. It bounds the SDK's choice for a request whose intent is already
deterministically established. Existing compound questions, explicit transaction-plus-aggregate
requests, action proposals, and low-confidence fallbacks keep their existing policies and budgets.
No tool result, merchant name, page label, or model-authored ID can expand the server allowlist.

Tool descriptions now state positive and negative boundaries: lifestyle history is not household
replenishment, recent learning is not due-state, spending aggregate is not transaction rows, and
receipts are not classification history. This reduces ambiguity without creating duplicate tools.

The settled Lifestyle tool is v1.3. Its `merchant_limit` is bounded to 1–8 and is overridden only
by a typed Lifestyle top-merchant objective, so the canonical service—not the renderer or model—
returns the requested number of rows. The transaction search tool is v1.2. For the final dining
drill-down it accepts only the server-owned closed `lifestyle_activity_type` and a paired current /
comparison range, excludes pending rows, and rejects collisions with transaction IDs, category
filters, and recovery scopes. It selects the exact union of those intervals using canonical
card-basis Lifestyle purchase eligibility and one canonical currency; groceries, credits,
transfers, removed/pending/uncertain rows, gap dates, and other currencies do not leak into the
drill-down. The model cannot author or widen this scope.

## Temporal semantics

`app/services/temporal_range_service.py` is the single backend authority for closed Agent phrases
and non-custom Insights presets. It uses Python's standard-library `datetime`/`zoneinfo` and
inclusive local dates. `app/agent/query_planning.py` imports and re-exports the shared types and
functions rather than maintaining Agent-only calendar arithmetic.

| Phrase | Deterministic range |
| --- | --- |
| today | local today |
| yesterday | local today minus one day |
| this week | Monday through local today |
| last week | previous Monday through Sunday |
| last 7 days | local today minus six days through today |
| this month | first day of the local calendar month through today |
| last month | complete previous calendar month |
| last 30 days / recently | local today minus 29 days through today |
| this quarter | first day of the current quarter through today |
| last quarter | complete previous calendar quarter |
| last 90 days | local today minus 89 days through today |
| year to date / this year | January 1 through today |
| last year | complete previous calendar year |
| explicit range | the validated inclusive dates supplied by the user, at most 731 days for spending |
| page context | the already validated semantic page start and end dates |

The week starts Monday, matching existing spending comparison behavior. Week-to-date comparison is
against the same weekdays of the previous week rather than a complete seven-day week.

The runtime reads the exact existing `ProactiveAttentionPreference.timezone` for the authenticated
workspace/user pair and never creates or changes a preference as a side effect of a question or
dashboard visit. A missing, invalid, or unsafe IANA zone falls back to UTC.

The authenticated
`GET /api/insights/date-range?preset={7d|30d|this_month|last_month|90d|this_quarter|last_quarter|ytd}`
interface returns the preset, inclusive start/end dates, granularity, and timezone from that same
authority. Insights uses it for every non-custom preset. It keeps explicitly entered Custom dates
local to that UI, but it no longer falls back to browser-local `Date` arithmetic for a preset. A
failed or malformed resolution is visible; superseded requests are aborted/ignored, and the Agent
page context remains an unfiltered `Insights` context until the selected range resolves. This
prevents a stale filter or a different browser timezone from silently reaching a contextual Agent
question.

Parity tests cover Phoenix near a UTC date boundary and America/New_York across a daylight-saving
boundary, plus authenticated/no-write/UTC-fallback API behavior and stale/malformed frontend
responses. Classification retrospectives convert inclusive local dates to tenant-scoped UTC
half-open timestamps in the canonical service. Transaction/spending services continue to consume
their canonical date columns; their eligibility and financial-truth rules are unchanged.

The settled temporal gate passed 115 focused parity/planner backend tests and a separate 80-test
related backend run, seven focused Vitest cases, the frontend lint/build gates, and three focused
Chromium Insights/context flows.

For the pinned Phoenix instant, the permanent assertions are:

- this month: 2026-08-01 through 2026-08-17;
- last month: 2026-07-01 through 2026-07-31;
- last 30 days/recently: 2026-07-19 through 2026-08-17;
- this week: 2026-08-17 through 2026-08-17;
- contextual Last 90 Days: 2026-05-20 through 2026-08-17.

## Top-N and canonical financial truth

Top N is parsed only for ranking objectives, clamped to 1–10, and defaults to one top category or
five top merchants when wording does not provide a number. The response block records its focus and
requested limit. A top-category block cannot contain merchant rows; a top-merchant block cannot
contain category rows; either list exceeding requested N fails strict validation.

Rankings still come from `SpendingInsightsService`. Day 17 does not change transaction eligibility:

- positive eligible purchases determine spend and rankings;
- credits/refunds remain a separate non-negative fact and do not reduce purchase rankings;
- pending, removed, transfer, payment, and other excluded rows remain excluded;
- personal/shared/unreviewed totals reconcile;
- My Actual Share reports only confirmed allocations and suppresses an exact percentage when the
  comparison is incomplete;
- cross-currency rows remain excluded under the existing explicit currency policy.

## Change decomposition

The model never calculates a financial difference. Canonical code calculates:

- current minus previous purchase spend;
- current minus previous purchase count;
- current minus previous average purchase/check;
- positive category and merchant deltas from canonical current and prior amounts.

Contributors are sorted by delta and stable name ordering. Lifestyle merchant changes are bounded
to eight canonical rows. The answer says “measured increase” and does not infer motives, health,
relationships, addiction, or unsupported causal explanations.

## Direct-answer policy

The first semantic text block answers the requested question before supporting detail:

- rankings lead with the winning name and amount;
- total questions lead with the total and period;
- comparisons lead with Yes/No, absolute change, and a meaningful percentage when valid;
- average-check questions explicitly name the average statistic;
- explanations lead with the total delta, then count, average, and measured contributors;
- learning questions identify durable classification facts and distinguish candidates from due
  items;
- uncertainty questions list recent correctable outcomes rather than unrelated recommendations.

The model's terminal output remains a fact-free evidence marker. It does not author totals, dates,
ranking order, classification state, or response cards.

## Response, SSE, and error contracts

The spending semantic block adds optional backward-compatible fields:

- `focus`: `summary`, `comparison`, `top_categories`, `top_merchants`, or
  `change_explanation`;
- `requested_limit`: required only for a ranking focus and bounded to 1–10.

Persisted blocks without those fields hydrate as the historical summary shape. Classification
activity adds a v1.1 local-date range shape with `start_date`, `end_date`, `timezone`, staple
candidates, aliases, and corresponding counts while retaining strict v1.0 hydration. Lifestyle
canonical output adds bounded merchant deltas. The SSE activity union names lifestyle separately
from spending and keeps strict start/completion sequencing.

The TypeScript validator rejects unknown fields, invalid dates/timezones/currency codes, impossible
counts, unrelated classification sections, and focus/list mismatches. Protocol details are logged
server-side; the user sees one stable safe message and Retry action. Supported deterministic
responses do not enter that fallback.

## Narrow-card and responsive UX

Day 17 preserves the shell, lazy loading, context chip, feedback, confirmations, desktop companion,
and mobile full-screen behavior. Targeted rendering changes are:

- `min-w-0` at Agent message and card containment boundaries;
- container-responsive auto-fit metric grids rather than viewport-only column counts;
- two-column content/action grids with descriptive text spanning the row;
- wrapping status badges and action labels instead of fixed-width truncation;
- `break-words` for long merchant/item labels, avoiding character-by-character wrapping;
- ordered ranking rows with visible rank, purchase count, spend share, and amount;
- explicit “Household item created” / “No household item created” evidence for each recent staple
  candidate, alongside its learning state and confidence;
- no renderer-side `slice(0, 5)` that contradicts the server's requested N;
- “Average check” instead of “Typical check”;
- screen-reader headings for completed and streaming Agent answers.

Permanent Playwright coverage uses the actual companion width and mobile widths, includes long
unbroken and hostile-looking labels, both created and not-created staple candidates, and asserts no
horizontal overflow or one-character columns. The focused run passed four applicable
Chromium/mobile-Chromium cases at 1024px companion and 320/375/390px mobile widths; the other 12
project/case combinations were intentionally skipped by the spec. The same cases passed their
serious/critical WCAG A/AA axe assertions.

## Platform-first decisions

| Concern | Decision | Reason |
| --- | --- | --- |
| Model/tool loop | **Reuse existing** official OpenAI Agents SDK runner, typed function tools, tool filtering, and named tool choice | It already owns provider execution and usage; a second framework would duplicate state and safety policy |
| One-tool analytical route | **Integrate** the closed ExpenseOps plan with SDK tool exposure | Official OpenAI guidance recommends exposing only relevant tools and says direct calls are appropriate when one call is sufficient and results are small |
| Query objective | **Build custom** small typed ExpenseOps contract | Financial question intent, date meaning, and canonical response focus are product semantics, not generic orchestration |
| Dates/timezones | **Reuse existing** Python `datetime` and `zoneinfo`; **build one shared bounded ExpenseOps service** | Agent phrases and authenticated Insights presets now share the same closed calendar arithmetic and user-timezone lookup; another library or date microservice adds no measured value |
| Page context | **Reuse existing** semantic `AgentPageContext` | It already carries validated surface, dates, category, basis, and entity information |
| Typo handling | **Build custom** eight-token measured normalizer and keep model fallback | A spellchecker/NLP platform is unnecessary for the fixed regression vocabulary and could silently alter merchant/category names |
| Facts and cards | **Reuse existing** canonical services and semantic components; extend their versioned contracts | A second spending/lifestyle/classification engine would risk financial and tenancy drift |
| PTC, sub-agents, vector DB | **Do not add** | These tasks need one small read result, not hosted aggregation, autonomous delegation, or semantic memory retrieval |

The official OpenAI documentation advises keeping prompts/tool descriptions lean, exposing only
tools relevant to the task, and preferring a direct tool call when one call is sufficient and its
output is already small. Day 17 follows that shape and benchmarks the result rather than adding
another orchestration layer. See [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model).

## Permanent evaluation method

`scripts/benchmark_agent_day17.py` reports every regression separately. The fixed set contains:

- 13 exact prompt rows: the 12 requested scenarios plus both required week-comparison phrasings;
- nine close paraphrases covering `spendings`, `then`, `restrant`, `frm`, `mnth`, `reciept`,
  `catagory`, and `cofee`;
- the exact four-turn dining follow-up chain;
- the explicit this-month/last-month/last-30-days distinction;
- inert hostile category/merchant labels and a control-only prompt;
- synthetic canonical spending, lifestyle, classification, and transaction outputs.

Each exact row asserts objective, local dates, one exposed read tool, selected tool, arguments,
canonical reconciliation, direct answer, requested ranking length, semantic block type, no
unsupported response, one simulated canonical call, and no write exposure. The benchmark measures
local planning/date/composition overhead separately from provider, database, and browser work.

The Day 16 baseline is a recorded deterministic reproduction at commit `72d4705` (tree-equivalent
to the requested Day 16 checkpoint). “0/13 before” means none could satisfy the complete Day 17
gate because no typed objective reached the composer; it does not claim every provider turn chose
the wrong tool. Provider-dependent before rates were not replayed or fabricated.

### Latest deterministic result

Command:

```bash
.venv/bin/python scripts/benchmark_agent_day17.py --repetitions 250 --warmups 25 --format markdown
```

Recorded locally on 2026-08-18:

| Measure | Before | After |
| --- | ---: | ---: |
| Complete exact regression acceptance | 0/13 | 13/13 |
| Deterministic single-tool exact routes | 5/13 | 13/13 |
| Exact + paraphrase + follow-up routing | not available | 26/26 |
| Wrong-domain routes | provider-dependent | 0 |
| Unnecessary clarifications | provider-dependent | 0 |
| Unsupported deterministic responses | provider-dependent | 0 |
| Maximum exposed tools per supported turn | 9 | 1 |
| Mean tools exposed across exact prompts | 5.923 | 1.000 |
| Registered read tools | 9 | 9 |
| Registered total tools with existing actions | 13 | 13 |
| Registered read metadata/schema bytes | 12,050 | 15,022 |
| Total metadata/schema bytes with existing actions | 17,227 | 20,199 |
| Mean exposed metadata/schema bytes | not recorded | 2,292.5 |
| Mean exposed-schema reduction versus the complete current read surface | — | 84.7% |

Current serialized read and total schema are each 2,972 bytes larger because classification range
output, explicit positive/negative tool boundaries, bounded Lifestyle merchant count, exact
comparison pairs, and the server-owned Lifestyle transaction drill-down are represented; tool
counts are unchanged. The projected current sizes are approximately 3,756 read-schema tokens,
5,050 total-schema tokens, and 574 mean exposed tokens per supported exact turn (743 projected
tokens of total growth from Day 16). Per-turn exposed schema is much smaller because a supported
analytical turn sees one tool. All token values in this paragraph use bytes/4 and are projections,
not provider-reported input tokens.

Local deterministic latency from the same fixed workload is recorded by the script. It excludes
network, provider, database queries, and browser rendering; those omissions are explicit rather
than blended into a misleading end-to-end number. The deterministic run makes zero model turns,
uses zero provider input/output tokens, and costs $0. The current Agents SDK runtime uses two
provider requests in one bounded loop for a supported analytical turn: the selected tool call and
the fact-free terminal marker. Day 17 adds no provider request and executes one canonical read call.

| Local deterministic stage | Median | p95 |
| --- | ---: | ---: |
| Query objective + routing | 0.0378 ms | 0.0457 ms |
| Date resolver alone | 0.0020 ms | 0.0023 ms |
| Canonical composition | 0.0241 ms | 0.0312 ms |
| Route + composition | 0.0593 ms | 0.0720 ms |

## Opt-in live model observation

The paid matrix is deliberately excluded from CI. It uses one isolated in-memory synthetic
workspace, pinned canonical data, `2026-08-18T01:30:00Z`, `America/Phoenix`, prompt version
`expenseops-readonly-v1.5`, and the cost-sensitive `gpt-4.1-mini` model. Write actions, proactive Agent,
and purchasing are disabled. It makes no production-data lookup.

The bounded matrix runs 18 Agent turns:

- all 13 exact real-user prompt variants listed above;
- one explicit rolling-30-day control, so it is observed separately from calendar month-to-date;
- the exact four-turn dining follow-up in one conversation.

Every other exact/control prompt gets a new conversation, which avoids unrelated history and keeps
provider input bounded. The test validates the typed objective and local dates before each call,
then asserts one exposed/selected canonical read tool, exact code-owned arguments, one completed
read call, requested ranking N, direct deterministic copy, reconciled semantic blocks, strict
persisted-response hydration, and zero proposal or financial-operation rows.

```bash
RUN_DAY17_LIVE_AGENT_EVAL=1 \
  .venv/bin/pytest -q -s tests/test_agent_day17_live_smoke.py
```

The command emits one `DAY17_LIVE_AGENT_EVAL_METRICS={...}` JSON record with per-case and aggregate
completed runs, provider requests, SDK turns, read-tool calls, failures, token usage, cost, and
latency. The pricing snapshot is deliberately model-matched and dated: as checked on 2026-08-18,
standard text pricing for `gpt-4.1-mini` was $0.40 per million input tokens and $1.60 per million
output tokens on the
[official GPT-4.1 mini model page](https://developers.openai.com/api/docs/models/gpt-4.1-mini).
Changing the pinned model without changing the matching snapshot fails closed instead of silently
applying the wrong price.

The final frozen-tree paid gate passed all 3 tests on 2026-08-18. All 18 Agent runs completed with
18 successful read-tool calls, zero failed tool calls, zero write proposals/operations, and at most
one exposed tool. The matrix made 36 provider requests and 36 SDK turns: exactly two provider
requests and one canonical read call per turn. It used 74,399 input tokens and 1,654 output tokens
(76,053 total), for a summed per-run estimate of **32,409 micros / $0.032409** under the pinned
price snapshot.

| Live synthetic measure | Observed result |
| --- | ---: |
| Completed exact/control/follow-up turns | 18/18 |
| Tool calls / failed tool calls | 18 / 0 |
| Provider requests / SDK turns | 36 / 36 |
| End-to-end latency | 2,908.5 ms median / 17,077 ms p95 and max / 78,031 ms total |
| SDK runtime latency | 2,901 ms median |
| Canonical tool latency | 13 ms median |

This was the final settled Lifestyle v1.3 / transaction-search v1.2 registry with pinned synthetic
data only. The high-resolution deterministic benchmark above separately measures local planning
and composition. These live results are one bounded synthetic observation, not production latency
or quality guarantees.

The non-provider fixture portion also passes in normal CI and proves the synthetic data's canonical
spending, credit/pending, restaurant, coffee, staple-candidate, and uncertainty facts. This final
18-turn gate supersedes both the earlier one-prompt connectivity check and the pre-freeze matrix.

## Security and tenancy

- Query plans contain no tenant identifiers; the executor obtains workspace/user scope only from
  the authenticated SQLAlchemy session and existing registry context.
- Foreign page entities still pass through existing ownership validation before planning.
- Classification range reads use tenant-scoped tables and local-to-UTC boundaries inside the
  canonical service.
- Merchant/product/category strings remain data. They cannot change objective, range, allowlist,
  totals, renderer type, tenant, or write policy.
- A model-generated foreign ID remains subject to strict input validation and tenant-scoped lookup.
- Only READ metadata is exposed for these analytical plans. No confirmation boundary, Splitwise
  flow, receipt mutation, purchasing authority, or proactive behavior changes.
- Existing prompt-injection, RLS/cross-tenant, SSE, idempotency, and action-confirmation suites
  remain release gates.

## Known limitations

- “Recently” is intentionally a documented rolling 30-day default; users can state a different
  supported or explicit range.
- Week start is currently an ExpenseOps-wide Monday policy, not a per-user preference.
- Custom Insights ranges remain explicit user-entered date-only values; all predefined presets are
  resolved by the shared backend service.
- Typical restaurant check is the canonical arithmetic average. Median is not computed or implied.
- Merchant/category contributors are measured changes, not causal explanations.
- The bounded typo map covers only demonstrated product mistakes. Unknown or genuinely ambiguous
  language remains with the SDK fallback or asks a safe clarification.
- The follow-up plan is reconstructed from bounded canonical conversation text; it is not a new
  permanent-memory system.
- The deterministic benchmark cannot claim model quality, provider latency, browser paint time, or
  production accuracy. Those require their separate opt-in/live and Playwright gates.

## Validation commands

Focused permanent gates:

```bash
.venv/bin/pytest -q \
  tests/test_insights_date_ranges.py \
  tests/test_agent_day17_query_planning.py \
  tests/test_agent_day17_runtime.py \
  tests/test_agent_day17_benchmark.py \
  tests/test_agent_day17_live_smoke.py

.venv/bin/ruff check \
  app/services/temporal_range_service.py \
  app/api/insights_routes.py \
  app/agent/query_planning.py \
  scripts/benchmark_agent_day17.py \
  tests/test_agent_day17_query_planning.py \
  tests/test_agent_day17_runtime.py \
  tests/test_agent_day17_benchmark.py \
  tests/test_agent_day17_live_smoke.py

cd frontend
npm test -- --run
npm run lint
npm run build
npx playwright test e2e/agent-day17-frontend.spec.ts
```

Full backend, Agent, financial truth, classification, receipt/replenishment, tenancy/RLS,
prompt-injection, action, SSE, frontend, browser, migration/drift, and dependency suites remain
required before a Day 17 checkpoint is declared complete.

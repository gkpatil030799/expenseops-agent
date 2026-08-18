# Autonomous Universal Classification (Day 16)

## Status and product boundary

Day 16 is implemented on the current local branch and is not a production rollout record. Its
purpose is safe, reversible internal bookkeeping: receipt lines and eligible transactions receive
canonical semantic classifications; a uniquely supported receipt can link to its Plaid transaction;
replenishable concepts can create Learning items and acquisitions; and cadence can begin without a
user-entered interval. Review is retrospective. The pipeline does not gain authority to purchase,
post Splitwise expenses, send provider writes, change feature flags, or bypass an existing
confirmation.

The operator kill switch `AUTONOMOUS_CLASSIFICATION_ENABLED` defaults to `false`. A workspace owner
must also opt in. No migration, application startup, or deploy runs the historical backfill.

The shorter [AUTONOMOUS_CLASSIFICATION.md](AUTONOMOUS_CLASSIFICATION.md) remains a concise operations
summary. This document is the complete Day 16 architecture and evaluation contract.

## Current architecture audit

### Classification mechanisms found and retained

| Domain | Existing or current mechanism | Day 16 role |
| --- | --- | --- |
| Receipt extraction | One bounded multimodal `ReceiptParser`, `ReceiptArtifact`, arithmetic/quality checks, and `ParsedReceiptItem` semantic hints | Raw evidence only; parsing does not grant database or external-action authority. |
| Receipt-line semantics | Day 9 closed line classification, item normalization, receipt decisions, aliases, and match states | Compatibility projection and evidence below explicit corrections and confirmed aliases. |
| Spending | `ExpenseTransaction`, signed purchase/credit rules, provider category, `SpendingInsightsService` | Canonical bank fact and financial-truth semantics; Day 16 does not create a second spend engine. |
| Lifestyle/dining | Day 10 deterministic lifestyle classification | Preserved. Coffee, restaurants, delivery, and nightlife remain non-replenishment activity. |
| Merchant/category rules | Merchant normalization, provider category mapping, and bounded taxonomy rules | Deterministic evidence before the model. Mixed retailers are not forced into narrow categories. |
| Structured memory | Confirmed aliases and explicit transaction corrections | Higher authority than generic rule/provider/model evidence; tenant scoped. |
| Household learning | `HouseholdItem`, aliases, `AcquisitionService`, quantity normalization, predictions, and adaptive cadence | Operational projection reused for only high-confidence, verified, replenishable receipt evidence. |
| Audit/activity | `AuditEvent`, acquisition facts, receipt match facts, and current projections | Supplemented by the append-only `classification_decisions` ledger and bounded retrospective read. |
| Automation | Gmail sync, Plaid sync, tenant job contexts, job leases, and bounded jobs | Reused by the autonomous pipeline, finalizer, and explicit backfill. |

The old taxonomies overlapped without being identical: Plaid provider strings, spending parent
labels, Day 9 receipt tracking classes, lifestyle subtypes, HouseholdItem names, and free-text
receipt categories each described a different dimension. Day 16 does not collapse them. It keeps
provider/parser facts immutable, adds a controlled semantic decision, and updates compatibility
projections for existing consumers.

Canonical sources of truth are now:

- receipt-line classification history: the append-only, versioned `classification_decisions`
  ledger; the `PurchaseReceiptItem` classification fields are its current read projection;
- transaction category history: the same ledger; `ExpenseTransaction` canonical classification
  fields are the current projection, while raw Plaid categories remain separate evidence;
- reusable product semantics: tenant-scoped `ClassificationConcept` and
  `ClassificationConceptAlias`; `HouseholdItem` is the operational replenishment projection, not a
  universal product ontology;
- receipt-to-transaction identity: `PurchaseReceipt.transaction_id` plus bounded match status,
  confidence, timestamps, and code-owned evidence;
- purchase history: non-voided `HouseholdItemAcquisition` rows;
- cadence: `HouseholdItem` cadence fields with explicit source/provenance and persisted prediction
  history.

Before Day 16, receipt confirmation/review could gate ordinary learning, users needed a manually
created staple before some receipt lines could contribute, historical rows had no bounded
classification backfill, and receipt-to-Plaid linking did not cover both arrival orders and all
pending/posting cases. Those safe internal gates can disappear. Review and correction remain
available. Explicit confirmation remains mandatory for Splitwise, purchases, and every other
consequential provider write.

## Universal classification model

Receipt lines and transactions retain their own domain models. The reusable decision contract
contains only:

- source type and source entity ID;
- controlled spending parent;
- optional normalized subcategory;
- item/activity type;
- replenishment eligibility;
- optional canonical concept;
- confidence and confidence band;
- authority and bounded provenance reason codes;
- decision state, version, application/finalization time, and correction link.

It contains no workspace supplied by the model, SQL, URL, arbitrary action, Splitwise payload,
purchase request, or provider credential. The server session supplies tenant scope and application
code decides every persistence operation.

### Category, concept, and replenishment are independent

| Example | Parent / subcategory | Activity | Replenishment | Concept |
| --- | --- | --- | --- | --- |
| Starbucks latte | Food & Dining / Coffee | Coffee beverage | Not replenishable | Coffee beverage |
| Restaurant entrée | Food & Dining / Restaurants | Restaurant meal | Not replenishable | Restaurant meal |
| Tide Pods | Household & Home / Laundry supplies | Household consumable | Replenishable | Laundry detergent |
| T-shirt | Lifestyle & Shopping / Apparel | Apparel | Not replenishable | Optional/general |
| Sales tax | Fees / Taxes / Discounts / Taxes | Tax | Not replenishable | Sales tax |
| `HOME 24` | Other / Uncertain | Uncertain | Uncertain | None |

This separation prevents “frequent” from becoming “replenishable.” Coffee visits, dining,
delivery, nightlife, services, tax, tip, discounts, refunds, and one-time purchases cannot create
staples through the autonomous path.

## Controlled taxonomy and dynamic creation

The closed top-level enum is:

- Food & Dining
- Household & Home
- Lifestyle & Shopping
- Personal Care
- Health
- Transportation
- Travel
- Entertainment
- Subscriptions
- Pets
- Education / Office
- Services
- Fees / Taxes / Discounts
- Other / Uncertain

The model cannot add a parent. A high-confidence decision may reuse or create a normalized,
workspace-scoped subcategory or concept under one of those parents. Creation is serialized within
the tenant taxonomy namespace, checks exact/normalized/fuzzy collisions first, and uses database
uniqueness as the final race guard. Labels that are brand-, merchant-, package-, SKU-, or receipt
abbreviation-specific are rejected. Medium decisions do not create durable taxonomy. Low evidence
becomes Other / Uncertain.

Workspace owners can list and rename concepts. Rename preserves the old name as a confirmed alias
and appends correction versions for current receipt/transaction projections. Owners can merge two
concepts only when their semantic dimensions are exactly compatible, aliases do not conflict, the
source is not already merged, the target is not part of a chain, and the bounded online mutation
limit is respected. Concept merge retargets a linked HouseholdItem's current semantic concept
pointer, but deliberately does not combine item names, cadence, acquisitions, or purchase history.
When both compatible concepts have active HouseholdItems, the owner performs the concept merge
first and then uses the separate bounded, audited, reversible HouseholdItem merge. That second
operation moves canonical in-product links and history, recomputes the retained item, tombstones
the source, and never changes an external-provider record.

## Evidence precedence and application policy

The effective precedence is:

1. explicit user correction;
2. confirmed alias or explicit structured preference;
3. deterministic exact taxonomy match;
4. verified linked-receipt composition, Plaid/provider category, and merchant evidence;
5. one bounded structured-output model batch for unresolved candidates;
6. code-owned validation and policy;
7. safe application, provisional application, or Other / Uncertain.

Higher authority cannot be overwritten by a later lower-authority run. Replaying the same logical
source is ledger- and acquisition-idempotent.

The confidence policy is code owned:

- high (`>= 0.85`): final immediately; safe dynamic taxonomy/learning may apply;
- medium (`>= 0.65` and `< 0.85`): provisional immediately, no durable category/concept/staple
  pollution, due after the configured grace period;
- low (`< 0.65`): Other / Uncertain immediately with no specific
  subcategory/concept/staple. A rejected/low model proposal is final; a wholly unresolved ingestion
  row may remain provisional only long enough for the single bounded finalizer opportunity.

Every meaningful receipt line and eligible transaction therefore reaches a category projection;
uncertainty is a valid category, not a blocked state.

## Build-versus-integrate and bounded model role

ExpenseOps reuses the existing parser, canonical transaction model, financial-truth engine,
normalizers, HouseholdItem/acquisition/cadence services, tenant session/RLS policy, Agent runtime,
and job leases. A small custom taxonomy and immutable decision ledger are warranted because an
external ontology, vector store, or generic “LLM memory” cannot enforce ExpenseOps authority,
financial, correction, audit, and workspace boundaries.

The configured unresolved-candidate model is `gpt-5.6-luna`; production configuration is not
changed automatically. Known rules and aliases make no model call. Unresolved receipt lines or
transactions use at most one bounded batch (default maximum 25), strict JSON schema, `store: false`,
and sanitized candidate fields. Email addresses and long account/payment/phone-like numbers are
redacted before the request. The response may propose only the semantic fields listed above and a
bounded provisional cadence range. Application code validates parent enums, cross-field
consistency, normalized labels, merchant/brand pollution, confidence, and reason codes.

Provider outage, timeout, rate limit, rejection, malformed JSON, or invalid enums cannot block
receipt/Plaid persistence or deterministic reconciliation. The record remains safely classified as
Other / Uncertain or provisional for a later bounded pass.

## Receipt processing and confirmation policy

Gmail, Telegram, and web image/text ingestion converge through the same receipt ingestion and
classification services. Source changes provenance and deduplication identity, not semantic
policy. Gmail continues to run through the existing scheduled sync architecture; a manual photo is
the fallback when no digital receipt exists.

Immediately after a usable parse, the pipeline:

1. persists canonical receipt facts and lines;
2. assigns every meaningful line a semantic category;
3. attempts deterministic receipt-to-Plaid reconciliation;
4. applies confirmed aliases and high-confidence safe decisions;
5. creates only safe missing concepts/HouseholdItems;
6. records only verified eligible acquisitions;
7. applies a prior where supportable and refreshes prediction;
8. retains uncertain/provisional rows for optional retrospective correction.

`parse_status=needs_review` is a UX state, not permission to perform reversible internal
bookkeeping. A safe acquisition additionally requires a present, sane purchase date; complete line
declaration; verified arithmetic; no parse failure; high receipt/line confidence; a positive line
amount; a final high-confidence replenishable decision; and no ignored/failed state. Partial
receipts still categorize readable lines but do not fabricate missing facts. Unusable receipts
retain failure state and create no items/acquisitions.

Ignoring a receipt reverses only unconfirmed autonomous acquisitions and aliases, repairs cadence,
and excludes the receipt from future backfill. A previously user-confirmed acquisition is not
silently removed.

## Receipt-to-Plaid reconciliation

Identity is deterministic; an LLM is never the authoritative matcher. Candidate eligibility
requires the same workspace, purchase direction, compatible currency, amount within two cents,
receipt/transaction or authorized date within two days, compatible normalized merchant identity,
and an unambiguous existing-link state. Posted rows outrank equivalent pending rows. A near tie is
`AMBIGUOUS`; absent credible evidence is `NO_MATCH`; a unique supported candidate is
`AUTO_MATCHED`.

Reconciliation runs in both arrival orders. When a Plaid row arrives after a receipt, the receipt
is retried. Pending-to-posted replacement migrates receipt and acquisition links to the canonical
posted row without creating a second purchase. Removal retries a unique active candidate. Exact
cross-channel semantic duplicate receipts may share one transaction and one logical acquisition;
different or near-tied purchases do not collapse.

The permanent generic regression covers grocery, mixed retailer, restaurant, ordinary retail, and
Trader Joe's without merchant-specific matching code. The fixed Day 16 benchmark separately covers
an exact different-merchant negative, a near tie, no candidate, and posted-over-pending preference.

## Linked receipt evidence and mixed baskets

Only one nonignored, nonfailed, complete, arithmetic-verified, auto-matched receipt can drive a
transaction category. Every positive, non-adjustment line must already have a final/corrected,
high-confidence parent. Tax/fee/discount adjustment rows do not determine the basket.

Code sums positive eligible line cents by parent. If the largest parent is at least 60% of eligible
spend, it becomes the conservative transaction parent; a subcategory/activity/replenishment value
is retained only when the dominant lines agree. If more than one parent exists and none reaches
60%, the transaction becomes Other / Uncertain with `linked_receipt_verified_mixed_basket`
provenance. The per-line classifications remain available as granular evidence. This phase does not
rewrite spending analytics into item-level accounting.

Credits use corrected signed financial semantics and classify as refunds/credits. Transfers, card
payments, removed rows, pending transactions, and other excluded bank movements do not become
purchase categories or staples. Spending category, personal/shared recommendation, and Splitwise
state remain separate.

## Household auto-creation and cadence

A new HouseholdItem is created only for a final, high-confidence, canonical replenishable concept
on an acquisition-safe receipt line. It begins in Learning state; the user never has to enter a
cadence. The source hierarchy is:

1. explicit user configured cadence;
2. observed user acquisition history;
3. quantity-adjusted/adaptive observed history;
4. bounded product/category prior;
5. bounded consent-gated model prior;
6. Learning/unknown when no credible estimate exists.

Category/model priors are marked provisional with a range, confidence, provenance, and estimated
time. They are never described as learned from the user. New valid acquisitions recalculate
cadence and predictions; observed history automatically replaces a prior. Corrections and undo
void or supersede wrong acquisitions, repair aliases/items, and rebuild cadence/predictions.

## Corrections, learning ledger, and retrospective experience

A line or transaction correction appends a new version, links the previous decision, updates the
current projection, upgrades or voids the relevant alias, repairs or removes autonomous
HouseholdItem/acquisition state, recomputes linked transaction evidence, and refreshes cadence.
Automation cannot overwrite a correction. A correction made while the finalizer is planning a
model call wins because provider I/O holds no target-row lock and the row is reloaded/locked before
apply.

The retrospective API, UI, and `get_classification_activity` Agent READ tool query durable tenant
records, not model memory. The bounded daily view groups:

- transactions categorized;
- receipt items categorized;
- categories used and newly created;
- receipt-to-Plaid outcomes;
- new HouseholdItems;
- cadence updates;
- provisional/uncertain items.

It supports questions such as “What did ExpenseOps categorize today?”, “Which receipts matched
Plaid?”, “What new staples were created?”, and “Anything uncertain?”. Raw prompts, images, account
IDs, match candidate details, and secrets are not returned.

## Grace-period finalizer

Medium decisions persist with `classification_auto_finalize_at`. The finalizer is a bounded,
idempotent tenant job, not a workflow engine. A committed workspace lease prevents overlapping
workers. It snapshots at most the configured batch without target locks, optionally makes one
consent-gated model call, then locks and reloads each current row with skip-locked semantics before
apply. It rechecks tenant, source status, due time, decision state, correction authority, feature
settings, and consent. A correction or kill-switch change during planning therefore wins.

Provider failure falls back to the existing safe projection; one row finalizes once. Failed source
rows and ignored receipts are excluded. A swallowed live receipt/Plaid application or learning
failure rolls back the partial classification and records `classification_retry_at`; the finalizer
claims that durable marker, rechecks the source and controls, repairs exactly once, and clears it
only after completeness is verified. The recommended service command is wired for the standard
release process but is not deployed by this local Day 16 task:

```bash
python -m app.jobs.classification_finalizer --forever
```

`railway.classification-finalizer.json` uses that command, a bounded poll interval, and an
on-failure restart policy. One-shot operation remains available by omitting `--forever`; operators
can add `--no-model`, `--batch-size`, or `--poll-seconds` within configured bounds.

## Historical backfill

Backfill is an explicit, tenant-scoped CLI with three durable cursors: receipt matching, receipt
lines, and transactions. It reads a bounded page, optionally makes one consent-gated model batch
without holding data-row locks, then reacquires the settings/checkpoint and exact page rows. The
whole page and cursors commit together. Failure rolls the page back; replay is idempotent.

Dry run for one workspace:

```bash
.venv/bin/python scripts/backfill_autonomous_classification.py \
  --workspace-id <workspace-id> --batch-size 100 --max-pages 1 --dry-run
```

Bounded mutation, deterministic first:

```bash
.venv/bin/python scripts/backfill_autonomous_classification.py \
  --workspace-id <workspace-id> --batch-size 100 --max-pages 1
```

`--use-model` is separate, explicit, bounded, and requires the applicable active user consent. No
production backfill is run by migration, startup, this document, or the test suite.

## Privacy, tenancy, and prompt-injection boundaries

- every category, concept, alias, decision, receipt, transaction, HouseholdItem, acquisition,
  correction, and match is selected by authenticated workspace scope;
- composite tenant foreign keys, runtime query scoping, and PostgreSQL RLS protect linked rows;
- receipt model processing checks the actual receipt/account owner's active consent before the
  provider call; another workspace member's consent cannot substitute;
- transaction model classification uses its own owner/member consent purpose;
- consent revocation, suspended/deleted user, or removed membership fails closed;
- the model request contains bounded sanitized evidence and `store: false`;
- receipt, merchant, email subject, and product text are inert external data;
- strings such as `SYSTEM`, `AUTO APPROVE`, `REVEAL OPENAI KEY`, `POST TO SPLITWISE`, and
  `CHANGE WORKSPACE` cannot change policy, tenant, tools, flags, or confirmation boundaries;
- no Day 16 service calls an external financial-action provider.

## Observability

Aggregate, content-free metrics cover:

- `receipt_items_categorized`
- `transactions_categorized`
- `categories_auto_created`
- `household_items_auto_created`
- `aliases_auto_created`
- `acquisitions_auto_recorded`
- `receipt_plaid_auto_matched`
- `receipt_plaid_ambiguous`
- `classifications_provisional`
- `classifications_auto_finalized`
- `classification_corrections`
- `cadence_estimates_created`
- `cadence_estimates_replaced_by_history`
- `classification_model_calls`
- `classification_rule_hits`

Safe finalizer/backfill summaries also include batch counts, failures, provider failure code,
latency, tokens, and cost when known. Raw merchant names, receipt lines, transaction descriptions,
prompts, model output, images, credentials, and account IDs do not enter general telemetry. Cost is
reported only when the configured pricing snapshot exactly names the configured model; otherwise
it remains null.

## Deterministic quality and manual-work benchmark

Run:

```bash
.venv/bin/python scripts/benchmark_autonomous_classification_day16.py --pretty
```

The checked fixed corpus currently records:

| Measure | Local deterministic result |
| --- | ---: |
| Receipt items | 30 |
| Transaction cases through the full service | 21 |
| Parent/activity/replenishment precision against fixed golden labels | 100% / 100% / 100% |
| Canonical concept precision (19 explicitly labeled cases) | 100% |
| Subcategory precision (17 explicitly labeled cases) | 100% |
| False specific categories on expected-uncertain rows | 0 |
| Specific parent outputs in the broad corpus | 47 of 51 (92.157%) |
| Full-week deterministic/provider decisions | 23 of 25 (92%) |
| Full-week provisional/model candidates | 2 of 25 (8%) |
| Full-week Other / Uncertain projections | 2 of 25 (8%) |
| Provider calls/tokens/cost | 0 / 0 / $0.00 |
| Reconciliation outcomes | 9 |
| Reconciliation outcome accuracy | 100% |
| Auto-match precision / recall | 100% / 100% |
| False auto-matches | 0 |
| Ambiguous outcome rate in deliberately mixed corpus | 11.111% |
| First-purchase category priors | 10 |
| Exact observed interval replacing a prior | 10 days → 10-day observed cadence; 0-day error |
| False staples in the simulated week | 0 |
| Auto-apply precision in the simulated week | 100% |
| False category/concept creations in the simulated week | 0 / 0 |

The realistic synthetic week starts with zero staples and includes three receipts (Gmail,
photographed Target, and restaurant), 18 meaningful lines, seven Plaid rows, one unique Trader
Joe's match, and ten replenishable concepts. The pre-Day-16 workflow proxy contains 49 required
manual actions: 18 line categories, ten staple creations, ten cadence entries, three receipt
confirmations, seven transaction categories, and one receipt link. The Day 16 deterministic path
requires zero setup/confirmation actions and produces two optional uncertain rows. That is a
49-action (100%) reduction for this supported synthetic corpus, not a claim that production users
will have a zero correction rate.

The final local benchmark run recorded receipt classification median/p95 latency of 38.924/246.947
ms, transaction classification median/p95 latency of 3.223/4.014 ms, 317.481 ms receipt-to-
categorized latency, and 20.995 ms Plaid-to-categorized latency. These are machine-dependent local
measurements, not production SLOs. Finalizer runtime, backfill throughput, provisional correction
rate, real-user correction rate, and irregular-cadence future error remain null until staged
evaluation provides evidence; live model latency/tokens/cost are recorded separately below.

### Tool-surface growth

The current registry contains nine READ tools and 13 total tools. Canonical schemas are 12,050 READ
bytes and 17,227 total bytes. Compared with the recorded Day 13 baseline (12 tools, 16,231 bytes),
Day 16 adds one READ tool and 996 schema bytes, approximately 249 tokens using a four-byte heuristic.
It does not raise provider turn or tool-call budgets.

## Chaos traceability and live model evaluation

The exact named 30-scenario failure contract is in
`scripts/day16_chaos_traceability.py`. It maps each scenario to executable pytest node IDs and
validates that every referenced test contains real assertions. It is not a count-only placeholder.

```bash
.venv/bin/python scripts/day16_chaos_traceability.py
.venv/bin/python scripts/day16_chaos_traceability.py --list
.venv/bin/python scripts/day16_chaos_traceability.py --run
```

The synthetic live classification evaluation is deliberately paid-call opt-in and sends no real
user data:

```bash
RUN_DAY16_LIVE_MODEL_EVAL=1 \
  .venv/bin/pytest -q tests/test_day16_live_classification_eval.py
```

Normal CI collects it as skipped and makes no paid call. The opt-in gate passed on 2026-08-17 with
one ten-record synthetic batch on `gpt-5.6-luna`: 5,191 ms latency, 963 input tokens, 793 output
tokens, and an estimated cost of $0.001144 using the configured $0.20/M input and $1.20/M output
snapshot. The result satisfied the closed schema, emitted at least one honest Other / Uncertain
decision, preserved bounded taxonomy labels, and exposed no action authority. A preflight run also
found that `uniqueItems` is outside the Responses strict-schema subset; the schema now omits that
keyword while code still rejects duplicate reason codes, and the successful live run verifies the
fixed provider contract.

## Rollout configuration and staged procedure

Recommended initial configuration (disabled):

```dotenv
AUTONOMOUS_CLASSIFICATION_ENABLED=false
AUTONOMOUS_CLASSIFICATION_GRACE_HOURS=24
AUTONOMOUS_CATEGORY_CREATION_ENABLED=true
AUTONOMOUS_CADENCE_ESTIMATION_ENABLED=true
CLASSIFICATION_MODEL=gpt-5.6-luna
CLASSIFICATION_BATCH_SIZE=25
CLASSIFICATION_FINALIZER_BATCH_SIZE=100
CLASSIFICATION_FINALIZER_POLL_SECONDS=300
CLASSIFICATION_BACKFILL_BATCH_SIZE=100
```

The rollout sequence is:

1. Deploy schema/code/services with the global flag false; verify migration head, RLS, runtime role,
   web/Gmail/Telegram/Plaid health, and that the finalizer process is not applying rows.
2. In an isolated staging workspace, enable global plus workspace autonomy, keep model use off, run
   one deterministic dry-run backfill page, and inspect uncertainty and projected counts. There is
   no hidden production backfill or broad all-workspace request.
3. Run the opt-in synthetic live model eval. Then enable one internal beta workspace with 24-hour
   grace and monitor corrections, false category/staple creation, match ambiguity, tokens, latency,
   and model cost. If a “provisional-only for every decision” mode is required, add and test an
   explicit mode first; the current flags do not pretend to provide it.
4. After human review, enable category/cadence creation for that beta workspace and run one bounded
   deterministic backfill page. Commit/checkpoint evidence before each subsequent page. Add
   `--use-model` only with owner consent and an explicit cost budget.
5. Expand one workspace cohort at a time. Disable the workspace or global flag on abnormal
   correction, collision, tenancy, cost, latency, or reconciliation signals. Existing ledger/source
   facts remain available for repair.

No Day 16 implementation step commits, pushes, merges, deploys, changes Railway, enables a
production flag, mutates production data, or runs production backfill automatically.

## Validation evidence snapshot

The isolated local PostgreSQL 18.4 release gate (never production) passed:

- fresh migration `0 -> 0033`;
- incremental migration `0023 -> 0033`;
- current Alembic heads check;
- runtime-grant reconciliation;
- Alembic drift check with “No new upgrade operations”;
- 12/12 restricted `expenseops_runtime` security/RLS tests on the fresh database;
- 12/12 restricted `expenseops_runtime` security/RLS tests on the incremental database.

Production-release packaging tests passed 27/27. The workflow has a distinct classification
finalizer service ID, validates `railway.classification-finalizer.json`, deploys and waits for that
service, uses the same protected expected-autonomy value across services, and preflights the model
and API key. This is configuration/test evidence only; it did not deploy or change Railway.

The settled-tree validation on 2026-08-17 also passed:

- backend: 1,616 passed, 20 configured skips;
- Day 16 chaos runner: 30 named scenarios, 36 mapped test references, 39 executed tests, and 224
  mapped assertions or expected raises;
- frontend unit: 118/118;
- ESLint: zero errors and the existing bounded 20 warnings;
- TypeScript and production build: green, with only the existing bundle-size advisory;
- Playwright: 330 passed and 166 intentional project/viewport skips across Chromium, mobile
  Chromium, Firefox, and WebKit, including visual and accessibility coverage;
- Python and npm dependency audits: no known vulnerabilities; `pip check`: no broken requirements;
- Ruff lint, Python compilation, Alembic single-head, and `git diff --check`: green.

The successful live eval and deterministic benchmark are described above. None of these local gates
performed a production deployment, production backfill, provider write, or feature enablement.

## Accepted limitations and remaining evidence

- The taxonomy is intentionally conservative, not a universal retail ontology. Weak evidence stays
  Other / Uncertain.
- Concept merge remains taxonomy-only; combining compatible HouseholdItem cadence, acquisitions,
  and purchase history requires the separate explicit, audited, reversible HouseholdItem merge.
- Receipt photos are not retained for arbitrary delayed reprocessing; provider outage may require
  resubmission unless a separately reviewed encrypted short-TTL artifact store is added.
- Conservative receipt matching can produce ambiguous/no-match outcomes; this is preferable to a
  false financial identity link.
- The deterministic corpus is synthetic and curated. Its 100% figures are regression evidence, not
  production prevalence or general model accuracy.
- Model-prior cadence quality, irregular-interval future error, production correction rate,
  finalizer runtime, backfill throughput, and production latency/cost require staged observations.
- The single synthetic live model eval passed on 2026-08-17. It is a provider-contract and bounded
  corpus check, not a claim of production prevalence or universal classification accuracy.
- Isolated PostgreSQL migration and restricted-runtime RLS gates are green; production migration,
  service health, and cohort rollout still require the standard human-reviewed release process.
- Local release gates are complete, but deployment and cohort enablement still require the standard
  human-reviewed release process and production health observation.

After a successful human-reviewed rollout, the recommended next product phase is Agent Intelligence
& UX Cleanup. It must not start automatically from Day 16 completion.

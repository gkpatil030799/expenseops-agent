# Autonomous Classification (Day 16)

## Status and boundary

Day 16 adds an internal, reversible classification layer for receipt lines and eligible Plaid
transactions. It does not add a purchase, payment, Splitwise, email, Telegram, or other external
write authority. Existing explicit-confirmation and outbox/reconciliation boundaries remain the
only paths to consequential actions.

The global rollout switch is `AUTONOMOUS_CLASSIFICATION_ENABLED` and defaults to `false`. Each
workspace also has an owner-controlled autonomy setting. Both must be enabled for live automatic
application. Category creation and cadence estimation have separate internal kill switches. No
historical backfill runs during migration or application startup.

## Build versus integrate

ExpenseOps reuses its existing canonical components:

- the single-call multimodal receipt parser and deterministic receipt arithmetic checks;
- `ExpenseTransaction` and Plaid sync, including pending-to-posted replacement handling;
- `SpendingInsightsService` and `LifestyleDiningService` rather than a second spend engine;
- `ItemNormalizationService`, `AcquisitionService`, and the replenishment predictor;
- tenant-scoped SQLAlchemy sessions, PostgreSQL RLS, job leases, and the existing Agent runtime.

A small custom taxonomy, decision ledger, and receipt-to-transaction reconciler are necessary
because the prior database stored provider/free-text categories only and had no durable authority,
confidence, correction, or provenance contract. A generic LLM memory/vector platform would not
provide those financial and tenancy guarantees.

For ambiguous evidence, the bounded fallback model is `gpt-5.6-luna`. OpenAI documents it as a
cost-sensitive model for high-volume workloads and lists Responses, image input, function calling,
and structured outputs support. ExpenseOps sends only bounded candidate fields, uses a strict JSON
schema, `store: false`, one batch request, and no chain-of-thought storage. Deterministic rules and
confirmed aliases always take precedence. Model output can propose internal semantics only; code
validates and applies them.

## Canonical dimensions

Every applied decision records independent dimensions:

- controlled spending parent category;
- workspace-scoped subcategory where safely supportable;
- item/activity type;
- replenishment eligibility;
- optional canonical item concept;
- confidence, confidence band, authority, provenance codes, state, and version.

The dimensions intentionally remain separate. A restaurant meal can be `Food & Dining` and
`restaurant_meal` while always being `not_replenishable`. A grocery receipt may contain a mixed
basket of perishable groceries, routine consumption, household consumables, one-time purchases,
discounts, and explicit return lines without collapsing the entire receipt into one item type.

Low-confidence evidence becomes `Other / Uncertain`. Medium-confidence evidence is provisional for
a bounded grace period. High-confidence deterministic, confirmed-alias, receipt, or provider
evidence may finalize immediately. New dynamic subcategories/concepts use normalized tenant-local
uniqueness and database locking; a user correction has the highest authority.

## Receipt and Plaid reconciliation

`ReceiptTransactionReconciliationService` runs in both arrival orders. It requires the same
workspace, purchase direction, exact currency, an amount within two cents, a receipt/authorized
date within two days, and sufficient normalized merchant similarity. Posted transactions are
preferred. Near ties and occupied links remain ambiguous rather than guessed.

The receipt stores only a bounded status, confidence, timestamps, and code-owned evidence fields.
Raw candidate evidence is not exposed through the API or Agent. Pending-to-posted replacement
atomically migrates receipt and acquisition links. Plaid's raw provider category remains preserved
separately from the current canonical classification, so a later sync cannot erase a correction.

## Safe autonomous learning

A receipt line may create or reuse a HouseholdItem and record an acquisition only when all of these
are true:

- classification is final and high-confidence;
- the concept is replenishable;
- the receipt date is present;
- all line items are declared complete;
- arithmetic is verified;
- the line amount is positive.

Dining, coffee visits, delivery, nightlife, services, tax, tip, discounts, refunds/returns, and
one-time purchases never create replenishment items. Explicit returned/refunded rows retain a
negative signed amount and are non-learning adjustments. A user-confirmed acquisition is never
undone by later background automation.

Ignoring a receipt reverses only unconfirmed autonomous acquisitions and aliases and repairs its
cadence; explicit user-confirmed learning remains intact. Ignored receipts are excluded from the
historical classifier/backfill. Deleting an item referenced by the immutable decision ledger
soft-disables it so audit history remains valid.

## Cadence

The source hierarchy is:

1. user configured cadence;
2. observed acquisition cadence;
3. bounded model/category prior;
4. Learning/unknown.

Priors are explicitly marked provisional and include a range, confidence, and provenance. A second
eligible acquisition replaces a prior with observed cadence, including supported quantity
normalization. Corrections and undo rebuild the learning cadence and prediction. Disabling cadence
estimation prevents autonomous prior or observed-cadence calculation; it does not remove explicit
user configuration.

## Corrections and audit

`classification_decisions` is an append-only, tenant-scoped ledger. Runtime roles receive
`SELECT`/`INSERT` only; `UPDATE` and `DELETE` are forbidden. Current receipt-line, transaction, and
HouseholdItem projections point to the latest valid version. A correction appends a new version,
links the prior version, upgrades confirmed aliases, moves or voids any autonomous acquisition,
recomputes cadence, and disables an orphaned auto-created item when appropriate.

The read API and Agent expose bounded, safe retrospective views for categories used/created,
receipt matches, newly created household items, cadence updates, and uncertain decisions. They do
not expose raw prompts, receipt images, provider payloads, account IDs, secret values, or raw match
evidence.

Workspace owners may rename a canonical classification concept or merge one compatible concept
into another. These operations are bounded and atomic: current receipt-line and transaction
projections receive new correction ledger versions, active aliases move to the retained concept,
and the source concept becomes a historical merge pointer. Existing decision rows are never
rewritten. A merge requires identical parent, subcategory, activity, and replenishment dimensions.

Concept merge remains taxonomy-only: it does not combine HouseholdItem acquisition, cadence, or
purchase history. It does retarget a linked HouseholdItem's current semantic concept pointer so
future alias resolution cannot continue learning against a tombstoned concept. When both concepts
have active HouseholdItems, the owner must first merge the compatible concepts, then explicitly
merge the now-compatible HouseholdItems. Ambiguous duplicate active items fail closed between
those two confirmed steps.

The separate HouseholdItem merge is bounded, owner-only, audited, and reversible. It moves only
canonical in-product links and history under ordered tenant-scoped locks, recomputes the retained
item from canonical acquisition history, and tombstones rather than deletes the source. Its audit
snapshot supports a fail-closed undo when no intervening incompatible change has occurred. It does
not alter or create external-provider records. Duplicate aliases and open operational projections
are resolved conservatively; immutable decisions and historical prediction/feedback facts remain
available for audit.

Owners may also rename or merge compatible classification subcategories. These operations require
matching parents, update current projections, append corrected decision versions, and leave the
source as a historical merge pointer. Existing ledger versions are never rewritten.

## Finalizer and historical backfill

The finalizer uses a committed per-workspace lease and PostgreSQL `FOR UPDATE SKIP LOCKED` claims.
It processes due provisional rows in bounded global order, never overwrites a user correction, and
uses at most one consent-gated model batch. Provider outage/rejection falls back deterministically.

Historical backfill is an explicit CLI operation only:

```bash
.venv/bin/python scripts/backfill_autonomous_classification.py --workspace-id <id> --dry-run
```

Mutation mode uses three durable cursors and commits a whole bounded page with its checkpoint. It
reconciles receipts before classifying, is idempotent, and never calls external write providers.
Historical model use requires both the explicit CLI flag and active consent from a current active
workspace member. A revoked/deleted user or removed membership cannot authorize a call.

## Privacy, telemetry, and cost

Existing receipt-model consent remains authoritative for receipt candidates. Transaction model
classification has a separate consent. Rule hits, decisions, provisional/final counts, categories
or concepts created, acquisitions, cadence estimates, model calls, failures, and latency/cost are
logged as aggregate counters only. Merchant names, receipt lines, transaction descriptions,
prompts, model outputs, and images are never written to telemetry.

Cost estimation is emitted only when configured prices match the exact configured model. Missing or
mismatched pricing remains null rather than presenting an invented estimate.

## Accepted limitations

- The controlled taxonomy is intentionally bounded. Novel or weak labels remain Other/Uncertain.
- Workspace subcategory/concept growth is conservative; this is not a universal retail ontology.
- Receipt photos are intentionally not retained after parsing, so a later provider outage retry
  requires resubmission unless a separately reviewed encrypted short-TTL artifact store is added.
- Receipt-to-Plaid matching is conservative and may report ambiguous/no-match when facts are close;
  it never guesses through a near tie.
- Automated classification improves internal organization and replenishment evidence only. It does
  not authorize an external action.

## Release procedure

Before enabling any workspace, operators must complete migration/RLS/readiness checks, deterministic
classification and reconciliation gates, prompt-injection and cross-tenant tests, complete backend
and frontend regressions, desktop/mobile accessibility checks, and a dry-run backfill report. Enable
the global flag only after human review, then opt in a small workspace cohort and monitor aggregate
correction, ambiguity, provisional, latency, and cost signals. Roll back by disabling the workspace
or global switch; immutable decisions and source facts remain recoverable.

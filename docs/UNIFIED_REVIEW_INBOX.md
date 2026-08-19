# Unified Review Inbox

Day 18 makes ExpenseOps bring actionable purchase decisions to the user. It does not add an
autonomous financial action, another Splitwise engine, or a generic workflow platform.

## User problem

Before Day 18, `ExpenseTransaction.status` already identified purchases needing a decision and
Telegram proactively delivered them. The web Review page only loaded transactions when opened,
the Agent required page context or a prompt, Gmail itemized receipts stayed discoverable only in
receipt history, and no durable user-specific seen/unread identity connected these surfaces.

The result was one domain decision presented as several disconnected experiences. A user could
resolve a Telegram card and still see stale controls elsewhere, or receive a supported Agent split
request as a generic read-only refusal because action-intent recognition did not cover terse normal
phrasing.

## Audit result and authority

- `ExpenseTransaction.status` remains authoritative for Personal/Shared/Posted state.
- `PurchaseReceipt` and its reconciliation/classification fields remain authoritative for receipt
  matching and itemized readiness.
- `FinancialOperation` and the transactional outbox remain authoritative for Splitwise execution,
  retry, and reconciliation.
- `ReviewItem` is a presentation projection only. It cannot mark a transaction Personal, post a
  split, link a receipt, or confirm a proposal.
- Day 13 Attention remains an informational/awareness surface. The Review Inbox contains only
  decisions the user can act on.

## Build versus integrate

### Reuse existing

- Plaid webhook/upsert and pending-replacement linkage
- transaction state machine and Review mutations
- Telegram delivery/callback flow
- Agent action proposals and model-free confirmation
- Splitwise participant resolution, share calculators, `FinancialOperation`, outbox, and
  reconciliation
- Gmail ingestion, receipt parser, autonomous classification, receipt–Plaid matching, and Day 11
  itemized assignment
- Day 12 user-owned structured memory
- current authenticated API client, visibility API, and dashboard refresh primitives

### Integrate

- Domain transitions synchronously maintain the projection in the same database transaction.
- Web and Agent read the same strict review API.
- Telegram continues to use transaction IDs, but stale callbacks re-read the canonical source and
  cannot mutate an already resolved purchase.
- Structured memory is read at serialization time; no copied recommendation becomes a second
  authority.

### Build custom

- One small tenant-scoped `review_items` table for durable identity and seen state
- Three strict authenticated endpoints for page, badge, and idempotent seen acknowledgement
- A visibility-aware bounded polling loop with exponential failure backoff
- A compact proactive Agent region that is not conversation history

No event bus, Redis, WebSocket, Kafka, workflow engine, notification framework, or new provider
client was introduced.

## Canonical review identity

The unique key is `(workspace_id, owner_user_id, kind, source_type, source_entity_id)`. A public UUID
is used by the browser; numeric source IDs are never accepted by the seen endpoint. Current kinds:

- `transaction_review`
- `itemized_split_ready`
- `receipt_match_needed`
- `financial_reconciliation` (reserved in the projection contract; existing financial recovery UI
  remains authoritative in this checkpoint)

States are `open`, `resolved`, and `stale`. `seen_at` is independent of source state. Viewing an item
does not resolve it. Reopening after an undo resets `seen_at`; the browser suppression key includes
`updated_at`, so a genuinely reopened item is announced again without duplicate seen requests.

The database enforces one owner/kind/source row and a unique public ID. Concurrent inserts use a
savepoint and recover the winning row after a uniqueness race.

## Transaction lifecycle

Plaid upsert performs classification and receipt reconciliation, links any pending replacement,
then calls `ReviewInboxService.sync_transaction()` before its commit.

- `ask_user` or `shared_draft` with no Splitwise expense: ensure one open task.
- Personal, Posted, Removed, in-progress recovery, or an existing provider expense: close the task.
- Undo back to review: reopen the same task and make it unread.
- Duplicate sync: update the same row.
- Pending → posted replacement: move the existing task to the posted transaction ID while retaining
  its public identity.

The inbox also revalidates open source state before returning a page. A deleted, resolved, or changed
source becomes stale even if an unusual code path missed the normal hook.

## Pending charges

Current canonical policy prohibits posting a pending transaction unless an explicit server setting
allows it. Day 18 preserves that rule. Pending cards remain visible and users may select participants
or prepare a recommendation, but the post control is disabled with:

> The final charge must post before ExpenseOps can send this split.

When the final charge replaces the pending row, the same review identity uses the final amount. No
second task or Splitwise expense is created.

## Web inbox and badge

The existing Review tab is now the actionable inbox. It presents:

1. Personal/Split transaction decisions
2. itemized splits ready for assignment
3. receipts needing a transaction match

The navigation and Agent entry points show the unread count; the page header shows total open
actionable items. Informational Attention rows are not counted. Transaction cards retain existing
Personal, participant/group selection, equal/custom calculations, preview, and post controls.

When memory supports an exact user-owned recommendation, the card explains why and offers
`Use recommended split` (or `Prepare recommended split` while pending) plus `Customize`. The
recommendation only pre-fills existing controls and never confirms or posts.

## Agent proactive discovery and natural language

Opening the Agent displays up to three current review items in a dedicated header card without
creating assistant messages or requiring a prompt. Selecting one navigates to the canonical Review
or receipt surface.

The following closed phrases select exactly one proposal tool when there is one active transaction:

- `split with me and Janhavi`
- `split with Janhavi`
- `split this with Janhavi`
- `split this between me and Janhavi`
- `50/50 with Janhavi`
- `this was shared with Janhavi`
- `mark this personal`
- `this is personal`

`me` is resolved as the verified authenticated Splitwise payer and filtered out of friend selection.
Participant names still resolve server-side. One active task supplies a semantic target; multiple
tasks produce `Which purchase do you want to split?` and no model or proposal call.

The false read-only refusal was caused by the narrow action recognizer failing these terse phrases,
after which the generic consequential-request guard returned the fallback. Day 18 expands only the
closed supported intent set. With writes disabled, split requests now say `Splitting is currently
disabled`; a disconnected integration says to connect and verify Splitwise; ambiguity and
ineligibility retain their precise server messages.

All successful action requests create the existing immutable proposal. The current transaction,
amount, status, integration, payer, participants, shares, and source fingerprint are frozen into the
code-owned confirmation. No model is called during confirmation and no provider request occurs
before it.

## Telegram parity

Telegram continues to use the existing notification timestamps and callback data rather than a
second task table. Every callback now reloads the transaction first. If web or Agent already resolved
it, Telegram edits/sends a clear `already resolved` response and performs no mutation. Telegram
Personal/Split paths call the same `TransactionService`; their transition closes the ReviewItem in
the same transaction and web polling removes it.

The current delivery record does not persist every original Telegram message ID in a form suitable
for reliable later editing. Day 18 therefore uses clear stale-callback behavior instead of adding a
message synchronization framework.

## Gmail and itemized readiness

Receipt ingestion and reconciliation call `sync_receipt()` after parse, classification, match,
confirm, ignore, and restore. A receipt becomes `itemized_split_ready` only when all of these hold:

- owner is a member of the receipt workspace;
- parse state is usable and the receipt is not ignored/failed;
- receipt is deterministically auto-matched to a current actionable transaction;
- no Splitwise expense already exists;
- total, currency, arithmetic, and completeness are exact;
- all non-overhead lines have positive prices and sum to subtotal;
- the bounded line count is at most 20;
- dining evidence comes from durable line classification/activity or transaction category.

An ambiguous receipt creates `receipt_match_needed` instead. Resolving the match makes that old task
stale and creates one itemized-ready task. No transaction is invented. Item assignment continues to
use Day 11: the model may interpret names, application code allocates every cent, and the exact
proposal still requires confirmation.

Repeated Gmail events and cross-channel receipt artifacts retain the Day 15/16 idempotency and
strong artifact-identity rules. The ReviewItem uniqueness boundary adds a final presentation dedupe.

## Polling and consistency

One browser-level review poll runs only while the authenticated document is visible. The first poll
is after 10 seconds, jitter is bounded to 2 seconds, and failures back off exponentially to 60
seconds while preserving the last good page. A visible stale-status message explains that ExpenseOps
is retrying. Existing dashboard polling no longer fetches the inbox, preventing duplicate component
requests. Domain actions trigger an immediate refresh.

This yields a beta event-to-web visibility target of roughly 10–12 seconds plus API latency without
new infrastructure. Local deterministic measurement on 2026-08-18 covered in-process SQLite
projection plus read only: 11.948 ms visibility and 6.293 ms for two source decisions. These are not
production network SLAs.

## Privacy, tenancy, and injection boundaries

- Reads require both trusted `workspace_id` and `user_id` session scope.
- Every query filters workspace and owner; another member in the same workspace cannot see a private
  owner task.
- New receipt/transaction rows are accepted only for a current workspace member.
- PostgreSQL enables and forces the exact workspace RLS policy.
- Public IDs cannot reveal foreign existence; foreign/missing seen requests fail closed.
- Recommendation lookup remains user-owned and tenant-scoped.
- Merchant, receipt-line, email-subject, and participant strings are inert data. They are never
  copied into action codes or confirmation authority.
- General telemetry records only event names and aggregate counts—never merchant, receipt line,
  participant, or amount.

## Safe observability

Current structured events include:

- `transaction_review_item_created`
- `transaction_review_item_updated`
- `transaction_review_item_seen`
- `transaction_review_item_resolved`
- `itemized_split_review_created`
- `receipt_match_review_created`
- `review_badge_count`

Existing proposal, audit, financial-operation, outbox, Telegram, and reconciliation events provide
recommendation acceptance, customization, cross-channel resolution, stale action, and provider
outcome evidence without duplicating raw payloads.

## Failure and chaos traceability

| # | Scenario | Fail-safe evidence |
|---:|---|---|
| 1 | Plaid webhook duplicate | verified webhook/outbox replay tests plus unique source projection |
| 2 | Pending → posted | public review identity migration regression |
| 3 | Transaction removed before review | inbox source-revalidation regression |
| 4 | Web and Telegram decide together | stale callback plus canonical state guard |
| 5 | Write feature disabled after creation | Day 18 disabled-split runtime regression |
| 6 | Splitwise disconnected | Day 8 disconnected integration regression |
| 7 | Participant removed | confirmation/provider preflight revalidation tests |
| 8 | Proposal expires | `proposal_expired` controlled-action regression |
| 9 | Timeout before send | preflight timeout creates no provider action |
| 10 | Timeout after possible send | ambiguous operation/reconciliation regression |
| 11 | Reconciliation ambiguous | financial journal ambiguity remains recoverable |
| 12 | Gmail duplicate | source external ID and artifact-hash idempotency tests |
| 13 | Receipt parse partial | Day 15 quality/partial receipt matrix |
| 14 | Receipt match ambiguous | match-needed → itemized-ready transition regression |
| 15 | Receipt edited after task creation | receipt source fingerprint/task re-evaluation |
| 16 | Itemized proposal stale | existing receipt/transaction fingerprint validation |
| 17 | Web poll failure | stale-data preservation and backoff implementation/browser contract |
| 18 | Browser offline/reconnect | shared API offline classification tests; next poll retries |
| 19 | User loses workspace access | authenticated dependency and tenant query fail closed |
| 20 | Cross-tenant task | foreign workspace and same-workspace foreign-user regressions |
| 21 | Hostile external text | synthetic hostile Gmail subject/line regression, zero provider calls |
| 22 | Frontend stale/malformed version | exact-key semantic parser rejection tests |
| 23 | Two tabs mark seen | idempotent backend seen plus browser request suppression |
| 24 | Review count failure | last good page remains visible with retry status |

## Verification snapshot

The deterministic benchmark seeds five transactions: three Personal and two requiring review. It
observes exactly two tasks, requires zero manual searches and zero prompts, resolves one through the
web service and one through the Telegram service boundary, ends at zero, and constructs no provider
client. A synthetic Gmail dining receipt matches Plaid and produces one itemized-ready task with zero
provider calls.

Browser coverage includes the 22-rem companion path, 320/375/390/1024 widths, touch-sized controls,
semantic headings, accessible badge labels, non-color unread text, keyboard controls, and Axe.

## Migration and rollout

Migration `20260818_0034` creates one small indexed table, backfills unresolved owned
`ask_user/shared_draft` transactions, installs FORCE RLS, and has a non-destructive downgrade that
drops only the presentation projection. There is no dependency change.

Production rollout should keep existing Agent write, Splitwise, Gmail, and receipt-model controls
unchanged. Run migration/grant/readiness gates, deploy workers before web using the standard process,
then monitor badge counts, stale-action rates, polling errors, and decision latency. No backfill other
than the migration's bounded transaction projection is needed.

## Known limitations

- The existing financial-operation recovery section remains separate; the reserved review kind is
  not populated in Day 18.
- Telegram stale callbacks are clear and inert, but old messages are not proactively edited after a
  web resolution because durable message identity is insufficient.
- Beta freshness uses bounded polling rather than push delivery.
- Agent proactive cards navigate to the canonical action surface; they are not duplicated into
  conversation history.
- No browser/desktop notification, snooze/dismiss, native mobile, autonomous Splitwise posting, or
  purchasing was added.

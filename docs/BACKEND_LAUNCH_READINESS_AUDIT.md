# ExpenseOps Backend Launch-Readiness Audit

**Audit date:** August 12, 2026  
**Release standard:** General availability for all customers  
**Audited revision:** `5300e3e` plus the local, uncommitted Insights candidate  
**Decision:** **NO-GO for a broad customer launch**

## Executive summary

ExpenseOps is healthy enough for a controlled personal or design-user pilot, but the backend is not ready for an all-customer launch. Several failure modes can silently:

- assign an expense to the wrong Splitwise payer;
- create duplicate or inconsistent Splitwise expenses;
- lose accepted Plaid work or Telegram notifications;
- skip Gmail messages permanently;
- allow one workspace member to disrupt shared integrations;
- produce incorrect financial Insights; or
- leave customer data without demonstrated recovery protection.

This audit did **not** find evidence that ExpenseOps has been hacked or that production data is currently corrupted. It identifies architectural and operational risks that emerge under concurrency, process restarts, provider failures, and larger multi-user usage.

## Verification performed

- Full backend suite: **522 tests passed**.
- Ruff: **passed**.
- Installed Python dependency consistency: **passed**.
- Production `/health`: HTTP 200.
- Production `/readiness`: HTTP 200.
- Production database check: passed.
- Production migration: current at `20260811_0014`.
- Required production integrations reported configured.
- Latest web deployment was running revision `5300e3e`.
- Railway configuration was inspected read-only; no secret values were exposed.
- No repository or infrastructure changes were made as part of the audit.

The current suite gives good happy-path coverage, but it does not establish safety under concurrent submissions, process termination, provider timeouts after remote success, rolling deployments, or PostgreSQL race conditions.

## P0 — launch blockers

### 1. A transaction can be assigned to the wrong Splitwise payer

Plaid connections are attached to a workspace rather than to the user who owns each account. Splitwise is also represented as one shared workspace integration. When an expense is created, its payer can therefore be derived from the shared Splitwise account instead of from the person whose card produced the transaction.

Evidence:

- [`PlaidItem` model](../app/models.py#L284)
- [Equal-split payer resolution](../app/services/transaction_service.py#L901)
- [Custom-split payer resolution](../app/services/transaction_service.py#L942)

Required before launch:

- Persist a verified owner for every Plaid account.
- Persist the Splitwise identity connected by each ExpenseOps user.
- Require an explicit payer mapping for financial posting.
- Block posting whenever payer ownership is ambiguous.

### 2. Splitwise mutations are not idempotent or atomic

Expense creation checks local state, calls Splitwise, and only then commits the returned Splitwise ID. A timeout or crash after Splitwise succeeds but before the local commit can cause a retry to create a duplicate expense. Concurrent submissions have the same risk.

Undo has the inverse failure: Splitwise deletion can succeed while the local database still records the expense as posted.

Evidence:

- [Splitwise creation flow](../app/services/transaction_service.py#L1006)
- [Splitwise undo flow](../app/services/transaction_service.py#L865)
- [Transaction mutation API](../app/api/transaction_routes.py#L95)

Required before launch:

- Durable Splitwise operation journal.
- Unique idempotency key per transaction and action.
- Atomic operation claim and explicit submitting state.
- Deterministic provider correlation marker.
- Reconciliation for ambiguous provider outcomes.
- Transactional outbox for post-commit events.

### 3. Posted expenses are not reconciled when Plaid changes them

Plaid can modify a settled amount, replace a pending transaction, reverse a transaction, or remove it. ExpenseOps updates or removes the local source row without reconciling the existing Splitwise expense.

This can leave a tip-adjusted purchase with the wrong Splitwise amount or a reversed charge that remains owed in Splitwise.

Evidence:

- [Plaid transaction updates](../app/services/transaction_service.py#L390)
- [Plaid transaction removals](../app/services/transaction_service.py#L827)

Required before launch:

- Persist pending-to-posted replacement relationships.
- Preserve source revisions rather than silently overwriting them.
- Introduce explicit reversal and removal states.
- Queue user-visible reconciliation for any posted source that changes.

### 4. Accepted Plaid work and Telegram notifications can be lost

Plaid acknowledges a webhook and schedules processing inside the web process. A restart after the response can permanently lose the accepted work.

Telegram notification state is committed before Telegram is contacted. A crash between those operations leaves the notification marked as sent even though the user never received it.

Evidence:

- [Plaid in-process background task](../app/api/plaid_routes.py#L176)
- [Telegram notification claim](../app/services/transaction_service.py#L680)
- [Telegram delivery](../app/services/transaction_service.py#L535)

Required before launch:

- Durable database-backed queues.
- Delivery leases with expiry.
- Retry and dead-letter policies.
- Event and recipient-level deduplication.
- Explicit pending, processing, delivered, failed, and reconciled states.

### 5. Multi-tenant database isolation is incomplete

Three pre-tenancy unique indexes remain globally scoped. One tenant can therefore block another tenant from using the same preferred-place key, weekly job key, or active-model state.

Evidence:

- [Preferred-place global key](../alembic/versions/20260809_0006_place_resolution.py#L52)
- [Weekly-run global key](../alembic/versions/20260809_0008_replenishment_learning.py#L269)
- [Active-model global key](../alembic/versions/20260809_0009_replenishment_reliability.py#L67)
- [Multi-tenant migration](../alembic/versions/20260811_0012_multitenant_foundation.py#L42)

A clean migration followed by schema comparison reproduced these uniqueness mismatches and additional drift.

Tenant filtering currently depends on SQLAlchemy session scope. PostgreSQL row-level security is intentionally disabled, so an unscoped session or missed query boundary can bypass that defense.

- [Application-level tenant criteria](../app/tenancy.py#L128)
- [Production RLS restriction](../app/config.py#L160)

Required before launch:

- Remove every obsolete global unique index.
- Add workspace-scoped constraints and collision tests.
- Introduce database-enforced tenant isolation or an equivalently reviewed data-access boundary.
- Separate trusted job/webhook database roles from customer request roles.

### 6. Workspace integration authorization is too broad

Normal workspace members can disconnect workspace Gmail, remove all Telegram identities, and disable Plaid connections. These destructive shared operations do not require an owner or administrator role.

Evidence:

- [Gmail disconnect](../app/api/integration_routes.py#L195)
- [Telegram disconnect](../app/api/integration_routes.py#L239)
- [Plaid disconnect](../app/api/integration_routes.py#L247)

Required before launch:

- Define connection ownership and workspace roles.
- Require owner/admin authorization for shared destructive actions.
- Restrict a member-level disconnect to that member's identity where applicable.
- Record actor-attributed audit events for every integration change.

### 7. Gmail synchronization can permanently skip messages

Receipt synchronization reads only one page and has no durable message cursor. Promotion synchronization can ignore Gmail pagination and still advance its checkpoint. A busy mailbox can therefore leave messages permanently unprocessed.

Evidence:

- [Receipt message collection](../app/services/gmail_receipt_service.py#L48)
- [Promotion incremental history](../app/services/gmail_promotion_ingestion_service.py#L233)

Required before launch:

- Consume every Gmail page before advancing the checkpoint.
- Persist a resumable cursor/checkpoint.
- Make message processing idempotent.
- Test mailbox bursts, page boundaries, token expiry, retries, and partial failures.

### 8. Cron jobs can report success even when every workspace failed

Jobs load and process tenants serially. Per-workspace failures are caught and swallowed, allowing Railway to report a successful process even if no workspace succeeded. There are no distributed leases, overlap protection, durable retries, batching, sharding, or dead-letter handling.

Evidence:

- [Tenant job enumeration](../app/job_tenancy.py#L25)
- [Gmail receipt job](../app/jobs/gmail_receipts.py#L20)
- [Promotion job](../app/jobs/promotions.py#L32)
- [Weekly replenishment job](../app/jobs/weekly_replenishment.py#L19)

The latest cron service deployments were configured successfully, but their next scheduled executions had not yet proved the audited revision operationally.

### 9. The Insights candidate can display financially incorrect numbers

At the time of this audit, Insights existed only in the local uncommitted candidate and was not in the deployed revision. Its backend currently:

- adds different currencies together;
- can show the payer's share rather than the signed-in viewer's share;
- includes unreviewed and error transactions in totals while excluding them from Personal and Shared;
- can double-count reconnect/import duplicates; and
- can produce misleading percentages around refunds.

Evidence:

- [Transaction selection](../app/services/spending_insights_service.py#L68)
- [Share selection](../app/services/spending_insights_service.py#L215)
- [Summary aggregation](../app/services/spending_insights_service.py#L244)

Required before deployment:

- Define a documented accounting identity for every KPI.
- Group by currency or use versioned FX conversions.
- Resolve the authenticated viewer's exact Splitwise identity.
- Separate drafts, errors, unreviewed transactions, refunds, and reversals.
- Exclude unresolved reconnect duplicates from totals.

### 10. Financial actions lack a durable actor-attributed ledger

Transactions are mutated in place. The current activity representation is reconstructed from the latest row state rather than from append-only financial events.

The system cannot reliably answer who classified, posted, retried, or undid a transaction; which channel initiated it; what changed; or which provider operation was involved.

Evidence:

- [Audit event model](../app/models.py#L184)
- [Transaction action routes](../app/api/transaction_routes.py#L53)
- [Transaction state mutations](../app/services/transaction_service.py#L838)

Required before launch:

- Append-only financial action events.
- Actor, workspace, channel, request and idempotency identifiers.
- Before/after allocation state.
- Provider operation ID and safe result/failure code.
- Retention independent of mutable transaction display state.

### 11. Disaster recovery is not demonstrated

There was no verified automated backup/PITR configuration, declared recovery point objective, declared recovery time objective, or completed restore drill.

Required before launch:

- Automated encrypted backups and point-in-time recovery.
- Documented retention policy.
- Off-system recovery strategy where appropriate.
- Declared RPO and RTO.
- Timed restore drill with integrity verification.

## P1 — required before general availability

### Authentication and security

- Require verified OIDC email claims before creating an account.
- Make OAuth-state consumption atomic; the current read/check/update sequence has a concurrency replay window: [OAuth state consumption](../app/services/oauth_state_service.py#L45).
- Add encryption-key versions and a rotation procedure for stored provider credentials.
- Use a shared rate-limit backend rather than process-local memory.
- Add trusted-host, HTTPS-forwarding, HSTS, CSP, `nosniff`, and referrer-policy controls at a verified application or edge layer.
- Return safe public error codes rather than raw provider exception detail.

### Reliability and performance

- Move Telegram receipt download, parsing, Splitwise, and OpenAI work out of the web request path.
- Add durable Telegram `update_id` deduplication.
- Add provider retries with exponential backoff, jitter, `Retry-After`, circuit breaking, and request budgets.
- Configure database pool size, overflow, acquisition timeout, recycle, statement timeout, and lock timeout.
- Add composite indexes for review queues, notification retries, dates, statuses, and Insights queries.
- Remove per-offer and per-item N+1 query patterns.
- Avoid loading up to two years of transaction data into Python for each Insights request.

### Operations and privacy

- Add metrics and alerts for latency, provider errors, DB pool pressure, queue age, stuck jobs, notification delivery, and sync freshness.
- Define retention and deletion policies for Gmail content, raw Plaid payloads, sessions, OAuth state, link codes, prediction history, and promotion messages.
- Document and obtain the required consent for sending receipt or promotion content to configured model providers.
- Pin Python dependencies and container image digests.
- Add automated CI; no GitHub Actions workflow or dependency-vulnerability gate was present during the audit.
- Run database migrations as a dedicated deployment step rather than in every web replica: [Docker start command](../Dockerfile#L22).
- Return HTTP 503 when schema readiness fails; the route currently reports `ready` even when its migration check is false: [Readiness endpoint](../app/main.py#L100).

## Existing strengths

- Split calculations use integer cents and `Decimal`.
- Paid and owed shares are required to sum exactly to the total.
- Pending transactions are blocked from Splitwise posting by default.
- Plaid webhook signature verification is enforced in production.
- Provider credentials are encrypted at rest.
- Session and OAuth tokens are stored as hashes.
- Production uses OIDC authentication.
- Secure cookies and CSRF-origin validation exist.
- Tenant request context and structured logging are present.
- The automated test suite is substantial and fast.

These are strong foundations. The core release problem is that ExpenseOps now operates as a financial, multi-tenant, distributed system, where identity correctness, idempotency, durable delivery, and recovery are part of the product contract.

## Required release sequence

1. Fix Plaid account ownership, Splitwise identity mapping, and workspace authorization.
2. Introduce an explicit financial state machine, operation journal, idempotency, and append-only audit ledger.
3. Add a transactional outbox and durable worker queue for Plaid, Telegram, Gmail, and Splitwise operations.
4. Correct tenant database constraints and enforce tenant isolation at the database boundary.
5. Implement complete Gmail pagination, durable checkpoints, and truthful cron outcomes.
6. Correct Insights currency, actor, duplicate, refund, and accounting semantics.
7. Configure automated backups/PITR and complete a restore drill.
8. Add PostgreSQL concurrency, crash-window, multi-tenant, and provider-failure tests.
9. Add CI, dependency scanning, load testing, SLOs, and alerting.
10. Perform a staged rollout only after the release gates below pass.

## Mandatory release gates

- No duplicate Splitwise expense under replay, timeout, or concurrency.
- Recoverable Splitwise create and delete ambiguity.
- Correct payer attribution for every member and account.
- Correct Plaid replacement, modification, reversal, and removal reconciliation.
- No mixed-currency aggregation without explicit conversion provenance.
- Viewer-correct Splitwise share calculations.
- Reconciled and documented KPI equations.
- Complete actor-attributed financial audit history.
- Durable, retryable webhook and notification processing.
- Two-tenant collision tests for every scoped unique key.
- Kill/restart tests after webhook acknowledgement and notification claim.
- Gmail multi-page and burst-processing tests.
- Provider 429, timeout, 5xx, token-expiry, and partial-failure tests.
- Load and soak tests with p95/p99 and query-count budgets.
- Successful backup restoration within the declared RTO and RPO.
- Rolling-deployment and rollback compatibility test.

## Immediate operating recommendation

- Do not release ExpenseOps broadly yet.
- Pause additional shared-workspace onboarding until payer ownership, integration authorization, Splitwise idempotency, and tenant uniqueness are corrected.
- Continue the current controlled pilot only with close monitoring and deliberate financial verification.
- Do not deploy the uncommitted Insights candidate until its accounting issues are resolved.
- Never deploy from a dirty working tree; create a reproducible commit and run the release gates against that exact revision.

This document is a point-in-time engineering audit. It should be updated as blockers are remediated, with each P0 linked to implementation changes, tests, operational evidence, and a named release approver.

# Remaining Launch-Hardening Phases

**Created:** August 14, 2026

**Status:** Hardening may pause for bounded feature development; public launch approval remains blocked

**Canonical strategy:** [Consolidated Launch-Remediation Strategy](./CONSOLIDATED_LAUNCH_REMEDIATION_STRATEGY_2026-08-14.md)

**Current audit:** [Full-Application Launch Re-audit](./FULL_APPLICATION_LAUNCH_REAUDIT_2026-08-14.md)

## Decision

ExpenseOps can pause most of the launch-hardening program while selected product features are built.
After those features are complete, the program can resume from the remaining work below. Immediate
credential containment is the exception: any credential potentially exposed during the audit must
be revoked and rotated before further production deployment, even while other hardening work is
paused.

This pause does **not** mean the application is approved for broad onboarding or general
availability. New features can change the threat model, data flows, user journeys, operational load,
and accessibility surface. Therefore, the hardening baseline must be updated and the new features
must be included when the audits resume.

## Current checkpoint

The active remediation program contains eight main phases, `R0` through `R7`. Work has crossed phase
boundaries because several safety fixes were urgent.

| Phase | Current checkpoint | Still required |
| --- | --- | --- |
| R0 — Incident containment | Secret representation redaction is included in the Phase 0A/0B checkpoint. The checkpoint has passed 667 backend tests, Ruff, migration upgrade, schema checks, and a credential-free regression run. | Rotate and revoke every potentially exposed provider credential; rotate the application encryption key safely; inspect provider logs; record evidence; finish enforced no-network test proof. |
| R1 — Trustworthy customer surface | R1A delivered a visual/responsive foundation and automated UI coverage. | Close false-empty and misleading integration states; authoritative pagination/totals; recovery UX; mobile chart readability; onboarding truth; production Plaid CSP; complete keyboard and screen-reader journeys. |
| R2 — Tenant isolation and workers | Local fixes now set and restore worker workspace/user context and scope fresh Plaid/Splitwise sessions. | Activate and test restricted PostgreSQL web/worker roles; replace general RLS bypass; prove invitations and every worker under RLS; separate migrations from web startup; make Railway use `/readiness`; recover eligible dead notifications after Telegram connection. |
| R3 — Security and identity boundaries | Local fixes harden redirects, request IDs, Telegram binding/private-chat enforcement, Plaid webhook limits/freshness/replay handling, and production-mode behavior. | Add OIDC PKCE/nonce and cached asynchronous JWKS handling; recent-auth for destructive actions; global request/schema bounds; promotion-domain trust repair; supply-chain/SBOM/signing controls; independent security re-audit with no P0/P1. |
| R4 — Financial, privacy, lifecycle, and operational truth | Local retention fixes preserve replay dependencies and scrub aged Telegram payloads. | Implement complete account/workspace deletion semantics; correct fleet operations reporting; provision retention; unify receipt decisions; finish notification replay; move Insights to bounded SQL aggregation; verify financial truth by actor, currency, status, and refunds. |
| R5 — Scale and maintainability | Not closed. | Cursor pagination, bounded tenant jobs, Gmail history sync, queued provider pagination, outbox heartbeats/concurrency, query and latency budgets, frontend code splitting, bundle budget, and zero-warning lint. |
| R6 — Production resilience proof | Not closed; primarily production and operational evidence. | Railway service topology, dedicated migration/worker/retention services, backups and PITR, timed restore drill, delivered alerts, failure/rollback exercises, configuration-drift checks, and documented RPO/RTO. |
| R7 — Exact release, canary, and re-audit | Not started. | Build an immutable release candidate, run the full production-equivalent matrix, conduct a staged canary, repeat independent Codex and Claude audits against the same SHA/deployment, and approve expansion only with zero P0/P1 findings. |

The Phase 0A/0B changes referenced above belong to the dedicated hardening checkpoint. Until that
checkpoint is reviewed, merged, migrated, deployed, and verified in production, it is not production
evidence.

## Rules during the feature-development pause

Feature work is allowed, but the following safety floor remains mandatory:

1. Keep onboarding limited to the existing controlled users. Do not represent the app as
   launch-ready or open public registration.
2. Preserve the current hardening work in an intentional checkpoint branch or commit before mixing
   it with feature development.
3. Every new table and query must be workspace scoped. Every mutation must enforce the same role and
   ownership rules in the backend, not only in the UI.
4. Financial or provider side effects must be idempotent, durable, recoverable, and auditable.
5. Do not place secrets, financial details, receipt contents, or precise locations in logs, URLs,
   client storage, or notification previews.
6. New list endpoints must be paginated and bounded. New provider calls and background jobs must
   have timeouts, retries, concurrency limits, and per-tenant failure isolation.
7. New UI must distinguish loading, empty, stale, unauthorized, and failed states; it must remain
   responsive and keyboard accessible.
8. Add backend, frontend, migration, tenant-isolation, and failure-path tests with each feature.
9. Keep database migrations backward-compatible and keep the repository migration chain at one
   head.
10. Do not weaken readiness, webhook verification, tenant scoping, retention, or security headers to
    make a feature easier to ship.

## Feature checkpoint requirements

Before a feature is considered complete:

- its product behavior and data classification are documented;
- authentication, authorization, workspace ownership, and privacy rules are explicit;
- provider actions define idempotency and retry behavior;
- loading, success, empty, stale, and failure states are tested;
- mobile and desktop layouts are verified at supported widths;
- migrations upgrade cleanly from the current production revision;
- the full existing regression suite remains green;
- documentation and environment-variable examples are updated without real values.

For an in-app assistant or PWA specifically, the assistant must use authenticated, tenant-scoped
domain services rather than duplicating the Telegram route logic. Financial actions must continue to
require an explicit preview and confirmation. API responses and financial data must never be cached
by the service worker, and lock-screen notifications should be private by default.

## How hardening resumes

After the planned features are complete:

1. Freeze the feature set and record the exact Git SHA, migrations, environment contract, and live
   Railway topology.
2. Re-run the complete UI, UX, backend, cybersecurity, privacy, and operations audits against the
   new code. Add new findings to the existing register rather than replacing historical evidence.
3. Reconcile the result with this document and the canonical strategy; reopen any previously fixed
   control affected by new features.
4. Complete R0 first, including provider-side credential rotation and evidence.
5. Complete R1 and R2 in parallel where safe. Do not deploy tenant-sensitive integration UI until
   its R2 backend contract passes.
6. Complete R3, then R4, because identity and public-entry boundaries protect the financial and
   privacy workflows validated in R4.
7. Complete R5 before load or canary testing.
8. Complete R6 with real Railway, backup, restore, alert, and rollback evidence.
9. Complete R7 against one immutable release candidate. Expand beyond controlled users only after
   the final audits report zero open P0/P1 findings.

```text
Feature development pause
        ↓
Feature freeze + new audit baseline
        ↓
R0 containment
        ↓
R1 customer truth  ↔  R2 tenant/worker safety
        ↓
R3 identity and public boundaries
        ↓
R4 financial/privacy/operations truth
        ↓
R5 scale and maintainability
        ↓
R6 production recovery proof
        ↓
R7 immutable canary and independent re-audit
```

## Launch rule

It is safe to postpone these phases; it is not safe to skip them. Feature completion, passing unit
tests, or a successful Railway deployment does not by itself authorize public launch. Broad rollout
requires the R7 exit gate and production evidence for every applicable earlier phase.

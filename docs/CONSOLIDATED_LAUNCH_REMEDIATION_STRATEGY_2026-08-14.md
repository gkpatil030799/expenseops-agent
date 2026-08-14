# ExpenseOps Consolidated Launch-Remediation Strategy

**Created:** August 14, 2026
**Current audit:** [Full-Application Launch Re-audit](./FULL_APPLICATION_LAUNCH_REAUDIT_2026-08-14.md)
**Audited revision:** `fce8c5bd1bf27a480a3228422590ac728656b648`
**First design-user beta:** Conditional GO in the independent review; combined gates remain open
**GA remediation:** Not started; NO-GO
**Target outcome:** independently verified GO for controlled onboarding, followed by staged GA

## Purpose

This is the active strategy for closing the August 14 re-audit. It does not overwrite the original
[Launch-Readiness Remediation Plan](./LAUNCH_READINESS_REMEDIATION_PLAN.md) or its
[Traceability](./LAUNCH_READINESS_TRACEABILITY.md). Those documents remain the historical record of
what was implemented and considered complete. The re-audit found production evidence and untested
paths that reopen several gates.

The independent Claude Code review is archived in
[INDEPENDENT_DESIGN_USER_BETA_AUDIT_2026-08-14.md](./INDEPENDENT_DESIGN_USER_BETA_AUDIT_2026-08-14.md)
and has been reconciled with the live-production full audit. It evaluated a narrower,
operator-supported beta; this strategy preserves that scope rather than treating its conditional GO
as a GA approval.

## Decision principles

1. Contain the credential incident before normal development or deployment.
2. After containment, customer-visible UI/UX truth is the first product workstream.
3. UI/UX and tenant/backend safety may proceed in parallel, but customer-facing integration changes
   cannot deploy before their backend and security dependencies pass.
4. Fix root causes and contracts, not individual screenshots or error strings.
5. Keep historical completion evidence; add re-audit evidence rather than rewriting it.
6. Prefer production behavior over tests when they disagree.
7. A finding closes only with code, regression tests, and applicable production proof.
8. Keep `main` deployable and migrations backward-compatible at every checkpoint.

## Source reconciliation

| Source | Status | How it is used |
| --- | --- | --- |
| August 14 Codex full audit | Read and normalized | Canonical current finding set and release verdict |
| August 12 Visual audit | Read | Historical visual intent and unresolved presentation details |
| August 12 UX audit | Read | Historical journeys, trust, accessibility, and recovery baseline |
| Historical backend audit | Read | Original correctness/reliability baseline |
| Existing remediation plan/traceability | Read | Prior implementation evidence and gates that must be reopened |
| Independent design-user beta audit | Read and reconciled | Adds scoped beta verdict, hermetic-test, Railway/README drift, bootstrap, lint, and license findings |

Audit disagreements were resolved using:

`production observation > restricted-role integration test > browser/provider E2E > unit test > code inspection > design opinion`

## Gates reopened by the re-audit

| Previous gate | Why it is reopened |
| --- | --- |
| V4/V8 visual/accessibility | Mobile chart readability, tall headers, incomplete state snapshots, and production-container CSP were not covered adequately |
| Phase 2 action integrity | Failures can still masquerade as signed out/empty; Settings remains all-or-nothing; failed Undo is invisible |
| Phase 3 identity/tenancy | Production RLS is inactive; invitations/workers are not proven under a restricted role |
| Phase 4 financial correctness | Non-USD preview truth and unsupported pending/draft actions remain |
| Phase 5 durable workers | Splitwise/Plaid tenant context, dead notification recovery, and lease behavior remain unsafe |
| Phase 6 domain correctness | Review/Activity/Deals pagination, receipt decision safety, and Insights scalability remain incomplete |
| Phase 7 security/operations | Deletion, retention, operational status, CSP, redirects, migration ownership, readiness, backup/restore, and alerts remain open |
| Phase 8 GA validation | Re-audit completed with NO-GO; closure and exact-release revalidation are now required |

## Phase overview

| Phase | Outcome | Deployment rule |
| --- | --- | --- |
| R0 | Incident contained and evidence frozen | Must complete before normal deployment |
| R1 | Customer surface is visually coherent and truthful | May develop beside R2; dependent integration UI waits for R2 |
| R2 | Tenant isolation and worker correctness proven | Required before cohort expansion or tenant-sensitive optional integrations |
| R3 | Identity, webhook, redirect, and link trust hardened | Required before any public exposure |
| R4 | Financial, privacy, lifecycle, and operational truth aligned | Required before GA |
| R5 | Work is bounded, scalable, and maintainable | Required before scale/canary |
| R6 | Recovery, monitoring, and release operations proven | Required before canary |
| R7 | Exact artifact passes canary and independent re-audit | Final launch gate |

## R0 — Incident containment and evidence freeze

### Objective

Remove credential risk and create one reliable finding register before implementation continues.

### Work

- Pause broad onboarding and nonessential production deployment.
- Rotate every credential potentially exposed in the audit transcript.
- Revoke superseded values provider-side and inspect provider usage/security logs.
- Rotate application encryption through the versioned keyring and existing rotation workflow.
- Verify all encrypted provider credentials remain readable, then remove the old key.
- Reauthorize/reconnect providers where rotation invalidates consent.
- Convert secret-bearing settings to redacted Pydantic types/fields.
- Add tests for redacted settings, validation failures, logs, and exception representations.
- Record the exact audited Git revision, live service configuration, and production deployment.
- Create canonical finding IDs with source, severity, owner, phase, PR, test, production evidence, and
  status.
- Run repository/history secret scans without printing candidate values.
- Make the backend suite hermetic before recording a new release baseline.
- Inject deterministic route/place providers in ordinary API tests.
- Deny outbound network access by default in unit/integration tests; keep real-provider contract tests
  explicit and opt-in.
- Run the suite with provider variables absent and with inert variables present.
- Record owner-bootstrap and legacy-variable cleanup as redacted presence/absence attestations.

### Exit gate

- No superseded credential remains valid.
- Existing encrypted integrations survive staged application-key rotation.
- Automated tests prove secrets are redacted from representations and logs.
- No high-confidence credential is present in Git history.
- Every P0 has an owner and a closure test.
- The complete suite is green regardless of ambient provider-secret presence.
- Ordinary tests make zero external provider calls.
- No bootstrap or legacy single-user variable remains active after owner migration.
- Both August 14 audits are represented in the canonical register.

## Controlled design-user beta gate

R0–R7 remain the GA lane. A single pre-approved, operator-supported design-user beta does not need
every GA scale/governance item, but it is **currently held**. It may resume only when all beta gates
below are documented against the exact deployed revision.

| Gate | Required evidence |
| --- | --- |
| B0 — containment | R0 credential rotation/redaction complete; no superseded credential remains valid |
| B1 — deterministic release | Exact-SHA suite fully green; ordinary tests cannot call providers; production-container Plaid CSP check passes |
| B2 — tenant/runtime safety | `/health` and `/readiness` both green under the intended restricted role; Splitwise/Plaid worker context tests pass |
| B3 — live preflight | Fresh backup; repository-head migration verified; OAuth test users/redirects; Telegram webhook; owner bootstrap and legacy-variable cleanup attested |
| B4 — customer guardrails | False-empty/member-onboarding/Undo/currency P0s closed; optional broken paths hidden; deletion behavior either fixed or accurately disclosed with a tested operator procedure |

This gate is stricter than the independent review's original checklist because the later live audit
observed production and current-code defects that the code-only review could not see.

## R1 — Trustworthy customer surface and visual refinement

### Objective

Preserve the improved brand while ensuring that every visible state and financial action tells the
truth on desktop and mobile.

### UI foundation

- Correct Plaid CSP using the minimum script/frame/connect origins and production-container tests.
- Consolidate Card/Surface primitives and spacing/shadow tokens.
- Resolve undefined utilities, load the intended font, and add favicon/theme/document metadata.
- Route-split major pages; lazy-load Sandbox Lab and Plaid rather than shipping them in the initial
  bundle.
- Keep 44px touch targets, focus visibility, reduced motion, and tabular financial numerals.
- Replace native destructive confirmation with accessible dialogs and return-focus behavior.
- Reduce action-heavy mobile header height while preserving page identity.

### State truth and recovery

- Model `idle`, `loading`, `success`, `empty`, `stale`, `unauthorized`, and `error` explicitly.
- Never translate network/backend failure into Sign in, Not connected, or All caught up.
- Load Review, Recovery, Activity, Settings sections, Household panels, and Deals independently.
- Preserve last-known-good data as visibly stale during refresh failure.
- Add persistent mutation outcomes with support correlation IDs and safe retry/reconciliation.
- Preserve the full path/query/hash through session renewal and OAuth recovery.

### Expense Review and Activity

- Implement authoritative totals and complete Review pagination.
- Add Activity pagination beyond 200.
- Hide backend-ineligible pending Split/Draft actions.
- Require participant/allocation validity before draft or post.
- Render failed Undo under the affected recent row.
- Make all split previews and confirmations ISO-currency aware.
- Retain Personal and Split as the dominant choices while progressive details remain secondary.

### Onboarding and Settings

- Build a role-aware activation checklist per personal capability, not “any integration exists.”
- Fix member Plaid completion so it never calls an owner-only sync operation.
- Display active workspace clearly on desktop and mobile before financial actions.
- Provide branded OAuth cancel/error/wrong-account recovery.
- Give Telegram plain `/start`, `/help`, connection status, and a Settings return path.
- Load connection cards independently and place feedback beside the acted-on control.
- Add pending invitation list, expiry/status, resend, and revoke.
- Add concise pre-auth Privacy, Terms, support, and data-use context.

### Insights, Household, and Deals

- Replace compressed mobile SVG charts with responsive charts/tables and readable hit regions.
- Keep tooltips and keyboard focus behavior equivalent; remove inert clickable styling.
- Scope Household busy state to one operation; make editors inline/modal or move focus predictably.
- Announce Household outcomes and preserve support IDs.
- Make Saved, Expiring, search, and category Deals views complete across pagination.
- Add a discoverable muted-merchant preferences/unmute view.
- Move SPA focus to the new page heading and announce navigation.

### Exit gate

- Failed API requests cannot render successful empty/disconnected/signed-out states.
- More-than-50 Review and more-than-200 Activity fixtures are fully navigable and reconcile.
- Owner and member activation pass end to end.
- Plaid loads through the production server with the actual CSP.
- Non-USD split previews and final confirmation are correct.
- Offline, timeout, 401, provider outage, malformed response, and partial failure are distinct and
  recoverable.
- Screenshot and interaction tests pass at 320, 375, 390, 768, 1024, and 1440 pixels in Chromium,
  Firefox, and WebKit.
- Keyboard and screen-reader smoke journeys pass on every primary workflow.

## R2 — Tenant isolation and worker correctness

### Objective

Make workspace and provider identity explicit, fail-closed, and provable across web, workers, crons,
invitations, and retries.

### Work

- Give every outbox event an explicit workspace/user execution envelope.
- Add one worker context manager that sets both identities and always resets them in `finally`.
- Resolve Splitwise credentials only from the event's explicit actor/workspace.
- Scope Plaid in the same database session before any tenant query.
- Add randomized interleaved multi-workspace event tests using real credential-resolution code.
- Add dedicated non-superuser, non-`BYPASSRLS` PostgreSQL web and worker roles.
- Keep owner credentials only in the migration job.
- Replace general caller-settable RLS bypass with a privilege-bound trusted mechanism.
- Test invitation preview/acceptance under restricted RLS.
- Run web, outbox, receipt, promotion, retention, and weekly jobs under restricted identities.
- Remove migrations from web startup and point Railway health checks to `/readiness`.
- Requeue eligible dead review notifications when a user completes Telegram connection.

### Exit gate

- Production `/readiness` is 200 using the real runtime identity.
- Every tenant table has `ENABLE RLS` and `FORCE RLS` and ordinary runtime cannot bypass either.
- Cross-workspace SQL, API, worker, credential, notification, and invitation access fails closed.
- Splitwise and Plaid workers pass under the restricted PostgreSQL role.
- Interleaved workspaces never change credential ownership.
- Crash/retry tests create no duplicate financial action or cross-tenant message.
- Web startup does not run migrations.

## R3 — Security and identity boundary hardening

### Objective

Close public-entry, identity, redirect, webhook, and untrusted-link abuse paths.

### Work

- Normalize post-auth destinations and reject protocol-relative/external redirects.
- Construct canonical redirects from configured public origin, never arbitrary forwarded host.
- Add OIDC PKCE S256 and nonce binding where supported.
- Cache discovery/JWKS with TTL, bounded refresh-on-unknown-key, async I/O, and cheap structural
  rejection.
- Require appropriate OIDC client secret/algorithm policy and verified Gmail identity.
- Add recent-auth/step-up for deletion, ownership transfer, and other high-impact actions.
- Add edge and ASGI body limits plus Pydantic string/list/metadata bounds.
- Add webhook IP/provider rate budgets, concurrency limits, replay age, replay uniqueness, bounded
  key caching, and retention.
- Remove Telegram query-string secret fallback; use the secret header and constant-time comparison.
- Consume Telegram link codes atomically.
- Replace promotion substring trust with registrable-domain equality, authenticated-sender evidence,
  curated mappings, and redirect-chain validation.
- Return stable redacted public errors and validate/generate request correlation IDs safely.
- Ensure security headers apply to middleware-generated auth and error responses.
- Add SBOM, image scanning, signing/provenance, dependency hashes, and digest-pinned actions/images.

### Exit gate

- Open-redirect, forged-host, replay, oversized-payload, malicious-link, code-race, and cross-tenant
  abuse tests pass.
- OIDC key rotation/outage does not block the event loop or remove all application availability.
- Sensitive account actions require recent authentication.
- No secret appears in URLs, access logs, settings representations, or errors.
- An independent security audit has no open P0/P1.

## R4 — Financial, privacy, lifecycle, and operational truth

### Objective

Make customer promises, financial state machines, data retention, and operator dashboards describe
the same reality.

### Work

- Define deletion semantics for personal, exclusive, shared, immutable, provider, and precise
  location data.
- Delete or irreversibly anonymize every record promised by policy and copy.
- Correct retention state names and include Plaid events and sensitive payloads.
- Provision and observe the retention job.
- Correct operations status to actual outbox, financial, lease, Gmail, and reconciliation states.
- Separate fleet-global operational queries from selected-workspace customer scope safely.
- Add customer/operator replay for eligible dead notifications.
- Require an explicit decision or acknowledgement for every receipt line.
- Route all web, Gmail, and Telegram receipt confirmation through one concurrency-safe batch service.
- Publish backend financial-action capabilities consumed by the UI.
- Move Insights calculations to bounded SQL aggregation/rollups with currency, actor, refund, and
  status invariants.
- Add data export/session-management controls if required by the final privacy policy.

### Exit gate

- Exclusive/shared deletion fixtures prove the documented contract on PostgreSQL.
- No applicable exact address, coordinate, provider credential, or exclusive content remains after
  deletion.
- Retention dry-run and execution remove exactly intended rows.
- Operations metrics match seeded queue, lease, failure, freshness, and reconciliation fixtures.
- Every receipt line has a durable final or explicitly deferred decision.
- Financial totals reconcile by viewer, status, sign, and currency.
- Privacy/legal review approves implementation and customer copy.

## R5 — Scale, responsiveness, and maintainability

### Objective

Remove unbounded work and ensure one slow tenant/provider cannot degrade everyone.

### Work

- Use cursor pagination and authoritative totals for Review, Activity, Deals, receipts, and admin
  operations.
- Split large frontend page components into bounded domain components and enforce bundle budgets.
- Batch tenant jobs with bounded concurrency, pagination, per-tenant outcomes, and credential
  failure isolation/backoff.
- Use Gmail incremental history for receipts rather than repeated broad scans.
- Move Plaid/manual provider pagination out of request handlers into durable operations.
- Use smaller outbox claims or lease heartbeats and provider-specific concurrency.
- Remove synchronous database/network work from async middleware.
- Add SQL indexes/rollups and query-count budgets for Insights, Review, Activity, and operations.
- Define p95/p99 API, worker age, provider latency, database pool, memory, bundle, and query budgets.
- Remove the 20 frontend lint warnings and enforce `--max-warnings=0`.
- Add a CI outbound-network policy plus an explicit, separately invoked provider contract suite.
- Publish per-route chunk output and enforce an initial-bundle budget.

### Exit gate

- No customer endpoint or scheduled job performs unbounded work.
- Sandbox/provider SDK code is absent from the initial customer bundle.
- Representative load/soak tests meet declared budgets.
- A slow or revoked credential cannot stop other tenants.
- Outbox recovery produces no duplicate send or head-of-line starvation.

## R6 — Production resilience proof

### Objective

Demonstrate that the exact operating system recovers from failures and tells a human when it cannot.

### Work

- Provision and observe migration, outbox, receipt, promotion, retention, and weekly services.
- Enable backups, PITR, and required snapshot schedules.
- Perform a timed restore into an isolated environment and validate application data.
- Configure alerts for readiness, error/latency, DB pressure, queue age, dead letters, financial
  ambiguity, worker failures, Gmail freshness, and provider failure.
- Test delivery to the intended human channel.
- Exercise timeout, 429, token expiry, partial outage, dead-letter replay, worker kill, rolling
  deployment, failed migration, rollback, and old/new version compatibility.
- Record service identifiers, immutable release reference, RPO/RTO, evidence links, and operators in
  the runbook.
- Encode or formally declare the expected Railway service topology, commands, schedules, database
  role class, migration ownership, and healthcheck path in version control.
- Add a release-time drift check comparing the expected manifest with live Railway configuration.
- Keep README deployment claims synchronized with that manifest.
- Before the controlled beta, attest a fresh backup, repository-head migration, Google OAuth test
  users/redirect URIs, Telegram secret-header webhook, owner bootstrap, and legacy-variable removal
  without recording values.

### Exit gate

- Restore succeeds within the declared RPO/RTO, or objectives are revised honestly.
- Every critical alert reaches a human.
- Every cron completes at least one observed production cycle.
- Worker termination safely recovers leased work.
- Migration failure blocks deployment.
- Rollback of the exact release artifact is demonstrated.

## R7 — Exact-release validation, canary, and independent re-audit

### Objective

Prove the immutable release candidate and expand access only through measured stages.

### Work

- Build one immutable release candidate from a recorded Git SHA.
- Run all tests against its production container, CSP/middleware, and restricted PostgreSQL role.
- Run real-provider sandbox/controlled-account owner and member journeys.
- Execute responsive, accessibility, browser, slow-network, concurrency, tenant-isolation, privacy,
  recovery, security, load, and soak suites.
- Begin with current design users, then use explicit canary stages with rollback thresholds.
- Have Codex and Claude repeat their audits independently against the same revision/deployment.
- Reconcile independent results by evidence and update traceability.
- Make a deliberate license decision before encouraging public contribution, reuse, or distribution.
- Add a documentation-to-runtime consistency check and execute both hermetic and explicit
  real-provider contract suites against the exact release SHA.

### Exit gate

- Zero open P0 or P1 findings.
- Every closed finding links to implementation, regression test, and production evidence.
- No isolation, notification, reconciliation, deletion, or link-trust violation occurs in canary.
- Product, engineering, security, privacy, and operations owners approve the release record.
- Only then expand beyond the controlled design-user beta or enable broader registration.

## Dependency map

```text
R0 incident containment
 ├──> R1 trustworthy customer surface ───────────────┐
 ├──> R2 tenant isolation and workers ──┬────────────┤
 └──> R3 security boundaries ───────────┘            v
                                     R4 truth and lifecycle
                                               |
                                               v
                                     R5 scale and maintenance
                                               |
                                               v
                                     R6 production proof
                                               |
                                               v
                                     R7 canary and re-audit
```

R1 and R2 may be developed in parallel after R0. Plaid/member onboarding, financial actions,
provider controls, and link-trust UI must not deploy independently of their R2/R3 backend gates.

## Parallel workstreams

| Workstream | Primary phases | Responsibilities |
| --- | --- | --- |
| UI/UX | R1; customer-facing R4; frontend R5 | State truth, visual refinement, onboarding, pagination UI, accessibility, responsive tests |
| Tenant/financial backend | R2; financial R4 | Context envelopes, restricted roles, idempotency/recovery, financial capabilities and aggregation |
| Security/privacy | R0, R3, privacy R4 | Rotation, identity/webhook/link hardening, deletion policy/tests, security review |
| Platform/reliability | Railway parts of R2, R5, R6 | Roles, migration ownership, jobs, scaling, backups, monitoring, restore and rollback |
| Independent QA | All phases | Failure fixtures, provider E2E, production-container browser tests, traceability and re-audit |

## Pull-request and evidence discipline

Every PR must:

- address one root cause or tightly coupled contract;
- reference canonical finding IDs and phase;
- include failure-path tests, not only success tests;
- include restricted-role PostgreSQL coverage for tenant-sensitive changes;
- include desktop and mobile screenshots for presentation changes;
- keep migrations backward-compatible;
- document deployment order and rollback;
- update traceability without deleting historical evidence;
- avoid real secrets and provider payloads in fixtures/logs; and
- leave `main` deployable.

## Finding register schema

Track each item using:

| Field | Meaning |
| --- | --- |
| ID | Stable identifier such as `SEC-P0-001` |
| Source | Codex, Claude, historical audit, production incident, or multiple |
| Severity | P0, P1, or P2 |
| Release scope | Beta blocker, beta guardrail, GA blocker, or post-GA debt |
| Customer journey | Review, onboarding, integrations, Household, Deals, privacy, operations, etc. |
| Root cause | Technical/systemic reason, not the visible symptom |
| Owner | Accountable workstream/person |
| Phase | R0–R7 |
| PR/commit | Immutable implementation reference |
| Regression proof | Unit/integration/E2E/security/load test |
| Production proof | Deployment, health, logs/metrics, screenshots, restore/alert evidence |
| Status | Open, in progress, code complete, production verified, independently closed |

## Immediate next actions

1. Complete R0 credential rotation and redaction work.
2. Fix and isolate the live-provider test, then record a deterministic exact-SHA baseline.
3. Create bounded R1 and R2 branches/worktrees in parallel.
4. Do not deploy R1 integration changes or expand the cohort until the controlled-beta gate passes.

## Implementation checkpoint — R1A UI foundation and responsive polish

**Status:** code complete and locally verified on 2026-08-14. This is a bounded presentation slice
of R1, not closure of the full trustworthy-customer-surface phase and not launch approval.

Delivered in this checkpoint:

- one canonical surface contract now drives both `Surface` and `Card` variants;
- card-body overrides no longer inherit a hidden desktop `padding-top: 0` conflict;
- command headers use explicit spacing, compact icon actions on phones, and keep their page identity
  within a 190px regression cap at the tested 393px viewport;
- the mobile Insights first viewport now reaches the primary Total Spend answer without scrolling;
- mobile Insights date ranges and advanced filters use accessible bottom sheets rather than clipped
  preset rails and absolute native disclosures;
- the spend-over-time chart retains readable 11px labels and 44-unit datum hit regions on phones by
  scrolling inside the chart card, with an explicit edge cue and semantic data-table fallback;
- missing product metadata, theme color, title, and a local SVG favicon were added;
- undefined visual utility references were removed and the unloaded Inter declaration was replaced
  with the intentional system-font stack; and
- Chromium, mobile Chromium, Firefox, and WebKit screenshot baselines were regenerated for the
  intentional presentation changes.

Validation recorded for this checkpoint:

- 21 frontend unit tests passed;
- production frontend build passed; the pre-existing initial-bundle size advisory remains open for
  R5 performance work;
- lint completed with zero errors and 20 existing warnings; and
- 144 cross-browser visual/accessibility/reflow tests passed, including 320/375/390/768/1024/1440px
  overflow checks, mobile touch targets, first-viewport hierarchy, chart containment, and branding.

Explicitly not claimed by R1A:

- no backend, financial, identity, provider, privacy, Railway, or secret work changed;
- no production deployment occurred;
- R0 credential rotation and the P0/P1 launch blockers remain open; and
- full R1 still requires truthful Review failure/empty states, complete queue totals, provider-state
  presentation, and the backend contracts on which those states depend.

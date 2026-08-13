# ExpenseOps Launch-Readiness Remediation Plan

**Plan date:** August 12, 2026  
**Inputs:** Visual Aesthetics Audit, UX Launch-Readiness Audit, Backend Launch-Readiness Audit  
**Release standard:** General availability for all customers  
**Priority:** Visual aesthetics first, followed by UX integrity, backend correctness, reliability, security, and production operations  
**Implementation status:** Phases 0–2 complete on `agent/launch-readiness`; Phase 3 next

## Strategy

The three audits form one launch-readiness program. Their overlapping findings should be implemented as small, sequential workstreams rather than as three independent rewrites.

Visual aesthetics is the first implementation priority. Presentation work must not, however, give misleading financial operations, unsafe integration controls, stale routes, or unverified deal links a stronger appearance of trust. Components whose meaning depends on unresolved backend work may be designed and tested early, but they must remain disabled or feature-gated until their correctness gates pass.

## Current baseline

The working tree contains uncommitted Insights/UI work and audit documents. Before implementation:

1. Preserve the current work in a dedicated branch or commit.
2. Establish an exact frontend and backend test baseline.
3. Create a launch-readiness implementation branch.
4. Keep the Insights feature out of production until its accounting is corrected.
5. Pause additional shared-workspace onboarding until payer ownership, authorization, Splitwise idempotency, and tenant constraints are fixed.

## Master sequence

| Phase | Workstream | Outcome |
| --- | --- | --- |
| 0 | Baseline protection | Clean, reproducible starting point |
| 1 | Visual aesthetics | Premium, responsive visual system |
| 2 | UX action integrity | Truthful success, failure, loading, and recovery |
| 3 | Identity and tenancy | Correct ownership, roles, and isolation |
| 4 | Financial correctness | Idempotent, auditable expense operations |
| 5 | Durable background processing | No lost webhooks, notifications, or Gmail messages |
| 6 | Product-domain correctness | Trustworthy Insights, routes, and deals |
| 7 | Security and operations | Backups, monitoring, CI, privacy, and platform hardening |
| 8 | GA validation | Staged rollout followed by a complete re-audit |

## Phase 0 — Protect the baseline

This phase changes no product behavior.

- Preserve the current uncommitted Insights/UI work separately.
- Create a clean `launch-readiness` integration branch.
- Record current frontend and backend test results.
- Create a traceability matrix mapping every audit item to a PR and test.
- Keep `main` deployable after every merge.
- Use feature flags for incomplete workflows.
- Configure and verify a database backup before the first migration.
- Establish PostgreSQL integration testing; SQLite is insufficient for concurrency and isolation validation.

### Exit gate

- The working tree is clean.
- Existing tests, lint, and builds pass.
- The exact release revision is recorded.
- No uncommitted feature can be accidentally deployed.

## Phase 1 — Visual aesthetics first

Deliver the visual work as eight reviewable pull requests.

### V1. Visual foundation and regression harness

Encode the audited design language centrally:

- colors, typography, radii, spacing, shadows, and motion;
- 1360px page maximum width;
- Command, Primary, Secondary, and Row surface levels;
- indigo as the single primary-action accent;
- rose only for actual errors or destructive actions;
- tabular numerals for financial values;
- reduced-motion behavior; and
- a minimum visible text size of 12px.

Create shared components:

- `AppShell`
- `PageHeader`
- `Surface`
- `FilterToolbar`
- `AccountMenu`
- `OverflowMenu`
- `ResponsiveSheet`
- `StatusMessage`
- `MerchantAvatar`

Add Playwright screenshot testing and Axe accessibility checks. Correct the frontend lint configuration so TypeScript and TSX are meaningfully checked.

Likely code areas:

- `frontend/src/index.css`
- `frontend/tailwind.config.ts`
- `frontend/src/components/ui/*`
- new shared layout and interaction components

### V2. Navigation, mobile shell, and shared page headers

- Rename “Expense review” to “Expenses.”
- Order primary navigation as Expenses → Household → Deals.
- Move Settings and Sign out into the account/workspace menu.
- Create an approximately 64px desktop navigation bar.
- Create a mobile top bar and fixed bottom navigation.
- Give Review, Insights, Activity, Household, Deals, and Settings their own contextual page identity.
- Replace inconsistent dark headers with one shared header system.
- Keep workspace and user identity reachable at every supported width.
- Use 44–48px touch targets.

Likely code areas:

- `frontend/src/App.tsx`
- `frontend/src/components/HouseholdOpsPage.tsx`
- `frontend/src/components/PromotionsPage.tsx`
- `frontend/src/components/AccountSettingsPage.tsx`

### V3. Expense Review hierarchy

- Replace the large filter card with a compact toolbar.
- Move advanced filters into a desktop popover or mobile sheet.
- Show active filters as removable chips.
- Simplify transaction cards around merchant, amount, metadata, one status, one recommendation, and two primary choices.
- Remove amount-based warning colors.
- Use slate for spending and emerald for refunds or credits.
- Add merchant initials, account/card, category, currency, and provenance.
- Make Personal and Split the dominant decisions.
- Move Draft and secondary actions into overflow.
- Present splitting as:
  1. Choose people
  2. Choose split
  3. Review and post
- Add participant initials and selected chips.
- Keep allocation validation and totals visible.
- Make the final summary and Post action sticky on mobile.
- Reduce recent Review activity to five compact rows.
- Keep one synchronization affordance.

Account/card information must come from a trustworthy backend contract; the frontend must not fabricate it.

Likely code areas:

- `frontend/src/App.tsx`
- `frontend/src/types.ts`
- recommended extraction into `frontend/src/components/expenses/*`

### V4. Insights narrative and charts

The visual redesign may be built and tested, but Insights remains feature-gated until Phase 6 corrects its accounting.

- Make Total Spend visually primary.
- Place “What Changed” immediately below the KPI tier.
- Show exact current and comparison date ranges.
- Collapse analytical filters.
- Reorder charts into a coherent customer narrative.
- Merge redundant category visualizations.
- Improve line charts, donut, category trend, legends, axes, tooltips, and focus behavior.
- Provide responsive mobile chart or table alternatives.
- Remove fake interactive behavior.
- Make every chart keyboard accessible.
- Extract the compressed Insights component into maintainable subcomponents.

Likely code areas:

- `frontend/src/components/InsightsDashboard.tsx`
- `frontend/src/insightsLogic.ts`
- `frontend/src/insightsVisualization.ts`
- recommended extraction into `frontend/src/components/insights/*`

### V5. Household command center

- Replace the errand-specific hero with “Household operations.”
- Replace four large zero cards with one compact all-clear state.
- When work exists, show one recommended next action.
- Keep the complete route builder under Errands.
- Show only a route summary on Today.
- Represent routes visually as Start → Stops → End.
- Prevent completed receipts from dominating active work.
- Add mobile tab edge fades without document overflow.

Likely code area:

- `frontend/src/components/HouseholdOpsPage.tsx`

### V6. Settings information architecture

Desktop composition:

- category navigation on the left;
- selected settings content on the right.

Mobile composition:

- a navigable list of settings destinations.

Sections:

1. Account
2. Workspace and members
3. Personal connections
4. Workspace connections
5. Expense preferences
6. Splitwise groups
7. Learned behavior
8. Privacy and account actions

Additional changes:

- Hide onboarding after completion.
- Show explicit onboarding progress while incomplete.
- Display provider logos and exact connected identities.
- Separate personal and workspace-managed connections.
- Add a dedicated Danger zone.
- Display only controls authorized for the current role.

Some identity and role details will be populated after Phase 3.

Likely code areas:

- `frontend/src/components/AccountSettingsPage.tsx`
- `frontend/src/components/GroupManagementPanel.tsx`
- `frontend/src/onboardingLogic.ts`

### V7. Deals hierarchy

- Use the shared page header.
- Combine disconnected Gmail and empty deals into one purposeful state.
- Make offer value the strongest card content.
- Add merchant initials.
- Keep Open and Save visible.
- Move Dismiss, Not relevant, and Mute into overflow.
- Show the destination domain before opening.
- Use neutral, amber, and rose expiry treatment only according to actual urgency.

Unknown links must not receive a trusted appearance before Phase 6 implements link verification.

Likely code area:

- `frontend/src/components/PromotionsPage.tsx`

### V8. Responsive, accessibility, and visual validation

Required viewport matrix:

- 320px
- 375px
- 390px
- 768px
- 1024px
- 1440px

Required states:

- loading;
- empty;
- error;
- all-clear;
- data-dense;
- long merchant/workspace/member names;
- large amounts and refunds;
- connected and disconnected integrations;
- collapsed and expanded transaction;
- every split step;
- open menus, sheets, and filter controls;
- route available and stale;
- every deal-expiry state.

### Visual exit gate

- No document-level horizontal overflow.
- Every touch target is at least 44px on touch layouts.
- Mobile navigation does not cover content or sticky actions.
- Build, tests, and TypeScript-aware lint pass.
- Screenshot tests pass in Chromium, Firefox, and WebKit.
- No critical or serious Axe violations.
- Keyboard order follows visual order.
- Focus is not trapped or lost.
- WCAG AA contrast passes.
- The application remains usable at 200% zoom.
- Reduced-motion behavior works.
- No inert element has fake interactive styling.
- Charts have keyboard and screen-reader equivalents.

## Phase 2 — Shared UX action integrity

Build an application-wide behavior layer:

- Correctly handle successful `204` responses.
- Centralize JSON, empty, and malformed response parsing.
- Introduce structured API errors and correlation IDs.
- Add row-level progress rather than one global busy state.
- Prevent duplicate submission.
- Provide persistent, explicit success and failure feedback.
- Add accessible live-region announcements.
- Add a global React error boundary.
- Handle expired sessions, offline mode, slow connections, and provider outages.
- Preserve last-known-good data and clearly mark it stale.
- Refetch authoritative state after mutations.

Financial retry controls remain disabled until Phase 4 makes the underlying operations idempotent.

### Exit gate

- Successful empty responses never appear to fail.
- Failure never masquerades as empty or successful.
- Unrelated rows remain usable while one action is pending.
- Session-expired, offline, slow, malformed, and provider-outage states are distinct and recoverable.

## Phase 3 — Identity, ownership, permissions, and tenant safety

Use expand → backfill → validate → enforce migrations.

Backend work:

- Add a verified owner to every Plaid account/item.
- Add per-user Splitwise identity mappings.
- Make Telegram identities recipient-specific.
- Define personal versus workspace integrations.
- Add an owner/member/admin permission matrix.
- Require owner/admin authorization for workspace-level destructive actions.
- Add member removal and ownership transfer.
- Protect the final remaining owner.
- Fix obsolete globally unique tenant indexes.
- Reconcile Alembic/model schema drift.
- Introduce PostgreSQL RLS with separate request and trusted-worker roles.
- Require verified OIDC email claims.
- Make OAuth-state consumption atomic.
- Record actor-attributed integration audit events.

Frontend work:

- Preserve signed-out invitation URLs through authentication.
- Handle wrong-account invitation recovery.
- Automatically switch into a newly joined workspace.
- Display exact connected identities.
- Make role restrictions visible before actions are attempted.
- Let every user independently connect and verify Telegram.
- Add privacy and data-use explanations during onboarding.

### Exit gate

- Cross-workspace reads and mutations fail at the database boundary.
- A member cannot disconnect another user or workspace-owned integration.
- Correct payer identity resolves for every owned account.
- Ambiguous ownership is blocked rather than guessed.
- Signed-out and wrong-account invitation journeys pass.
- Member removal, ownership transfer, and last-owner safeguards pass.
- Two-member Telegram delivery works independently.

## Phase 4 — Financial correctness and action recovery

Introduce:

- an explicit financial transaction state machine;
- a durable Splitwise operation journal;
- unique idempotency keys;
- atomic operation claims using row locking or compare-and-swap;
- provider correlation identifiers;
- a recoverable ambiguous-provider state;
- pending-to-posted Plaid replacement relationships;
- reversal, modification, and removal reconciliation;
- append-only financial audit events;
- a transactional outbox; and
- valid-draft database invariants.

Frontend work:

- Keep failed operations in a visible Recovery queue.
- Provide Retry and Return to Review actions.
- Show explicit durable success confirmation.
- Disable Draft until participants and allocations are valid.
- Do not expose unsupported actions for pending transactions.
- Display actor, channel, attempt, result, and timestamp in Activity.

### Exit gate

- Concurrent submission creates exactly one Splitwise expense.
- A timeout after remote success cannot create a duplicate.
- Splitwise create/delete ambiguity is recoverable.
- Tip changes, replacements, reversals, and removed charges reconcile correctly.
- Every financial mutation produces an immutable audit event.
- Failed operations cannot disappear or appear successful.

## Phase 5 — Durable workers and schedulers

Recommended initial architecture: PostgreSQL transactional outbox plus dedicated Railway worker services using leases and `FOR UPDATE SKIP LOCKED`.

Implement:

- a durable Plaid webhook worker;
- a Telegram notification and receipt worker;
- a Splitwise mutation/reconciliation worker;
- Gmail receipt and promotion workers;
- recipient-level delivery records;
- retry with exponential backoff, jitter, and `Retry-After` support;
- lease expiry and recovery;
- dead-letter handling;
- Telegram `update_id` deduplication;
- complete Gmail pagination before checkpoint advancement;
- resumable Gmail cursors;
- scheduler overlap protection;
- batched tenant enumeration; and
- truthful complete, partial, and failed job outcomes.

### Exit gate

- Kill-after-webhook-acknowledgement tests pass.
- Kill-after-notification-claim tests pass.
- Duplicate provider events are harmless.
- Expired leases recover safely.
- Gmail multipage and burst tests pass.
- Provider 429, timeout, token-expiry, and partial-failure tests pass.
- A complete tenant failure cannot be reported as success.

## Phase 6 — Product-domain correctness

### Insights

Use a single-currency view initially rather than silently introducing FX conversion.

- Resolve the signed-in viewer's exact Splitwise share.
- Separate reporting by currency.
- Define and enforce reconciled KPI equations.
- Separate personal, posted shared, unreviewed, draft, error, refund, reversal, and removal states.
- Exclude unresolved reconnect/import duplicates.
- Correct refund signs and percentages.
- Aggregate efficiently in SQL.
- Add appropriate indexes and query budgets.
- Display accurate freshness and exact comparison dates.

### Household Ops

- Require provider-verified IDs, addresses, or coordinates for every route point.
- Reject generic labels as concrete route destinations.
- Bind route plans to complete input fingerprints.
- Mark a plan stale after any relevant input changes.
- Disable Start Route until recalculated.
- Load Household panels independently.
- Paginate receipts and show truthful counts.
- Add batch receipt classification and decision summaries.
- Add recovery for ignored receipts.
- Invalidate stale replenishment suggestions after item changes.

### Deals

- Distinguish loading, disconnected, empty, partial, and failed states.
- Add complete pagination and truthful totals.
- Implement Save and Unsave.
- Implement Mute confirmation, Undo, and discoverable Unmute.
- Suppress unsafe URLs or show a domain-disclosing warning interstitial.
- Add explicit link trust status and reason.

### Exit gate

- Financial totals reconcile by currency and viewer.
- Refund and duplicate datasets produce truthful output.
- Routes contain only verified, current destinations.
- A stale route cannot launch.
- Unverified deal destinations cannot silently inherit product trust.
- Household and Deals failures never appear as legitimate empty states.

## Phase 7 — Security, privacy, and production operations

- Configure automated encrypted backups and PITR.
- Declare RPO and RTO and complete a timed restore drill.
- Run migrations as a dedicated deployment job rather than in every web replica.
- Return HTTP 503 from `/readiness` when schema or critical conditions are unsafe.
- Add shared rate limiting, likely backed by Redis.
- Add encryption-key versioning and a rotation process.
- Configure trusted-host, proxy/HTTPS, HSTS, CSP, `nosniff`, and referrer-policy controls.
- Return safe public errors rather than raw provider exception details.
- Configure database pool, acquisition, statement, and lock timeouts.
- Add composite performance indexes.
- Define and implement data-retention and deletion jobs.
- Publish Privacy Policy, Terms, support, and data-processing consent surfaces.
- Commit reproducible Python and frontend dependency locks.
- Pin container images and use a non-root runtime.
- Add GitHub Actions for lint, tests, PostgreSQL migrations, security scanning, and reproducible builds.
- Add metrics and alerts for queue age, sync freshness, provider errors, delivery failure, DB pressure, and latency.

### Exit gate

- Backups restore within the declared RPO/RTO.
- Migration and rollback procedures are proven.
- CI, dependency, image, and security gates pass against the exact release revision.
- Operational dashboards and alerts cover every critical provider and worker path.
- Privacy, data-use, deletion, Terms, and support are visible at the relevant decision points.

## Phase 8 — GA validation and re-audit

Before requesting another launch audit:

- Mark every audit item resolved with linked code, test, and operational evidence.
- Cover every P0 with automated end-to-end tests.
- Pass PostgreSQL migration and schema checks.
- Pass concurrency and crash-injection tests.
- Pass cross-tenant security tests.
- Pass provider cancellation, timeout, 429, token-expiry, and outage tests.
- Pass accessibility, keyboard, screen-reader, cross-browser, and slow-network tests.
- Pass load and soak tests against declared p95/p99 and query-count budgets.
- Restore a backup successfully within the declared recovery objectives.
- Pass rolling-deployment and rollback compatibility tests.
- Complete a staged canary rollout without reconciliation, isolation, or delivery violations.

Only then should the Visual Aesthetics, UX Launch-Readiness, and Backend Launch-Readiness audits be repeated independently.

## Pull-request discipline

Every pull request must:

- address one bounded concern;
- keep `main` deployable;
- include automated tests;
- include desktop and mobile screenshots for presentation changes;
- use backward-compatible migrations;
- update the audit traceability matrix;
- avoid secrets and sensitive provider payloads;
- state its rollback procedure; and
- pass its phase gate before dependent work begins.

## Planned first implementation

After approval, implementation starts with:

1. Phase 0 baseline protection.
2. V1 visual foundation and regression harness.
3. V2 navigation, mobile shell, and shared page headers.

No application implementation was started while creating this plan.

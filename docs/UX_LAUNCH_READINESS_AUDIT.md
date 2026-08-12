# ExpenseOps UX Launch-Readiness Audit

**Audit date:** August 12, 2026  
**Release standard:** Mass-market customer launch  
**Decision:** **NO-GO**  
**Scope:** Expense Review, Insights, Activity, Household Ops, Deals, onboarding, integrations, workspaces, settings, mobile responsiveness, accessibility, visual system, failure recovery, and customer trust.

## Executive summary

ExpenseOps has a credible visual foundation, but it is not ready for a broad customer launch. The dark navy/indigo identity, typography, cards, spacing, and chart presentation are generally strong. The blockers are deeper than presentation: financial correctness, action integrity, multi-user permissions, onboarding reliability, mobile usability, resilience, and informed consent.

The current experience could cause customers to:

- believe a failed Splitwise post succeeded;
- create a shared draft without any participant;
- see multiple currencies combined and labeled as USD;
- lose a workspace invitation during sign-in;
- believe their Telegram is connected when notifications are routed elsewhere;
- disconnect shared integrations without sufficient permission or recovery;
- launch a stale or insufficiently resolved route;
- open an unverified promotional link as though it were trusted;
- encounter an empty state when the underlying request actually failed.

The next release phase should freeze new feature development and focus on correctness, permissions, recoverability, resilience, accessibility, mobile behavior, and release validation.

## Method

This was a read-only heuristic and implementation audit. It included:

- source-level review of the frontend and relevant backend services;
- rendered inspection at 1440px desktop and 390px mobile widths;
- examination of core success, empty, loading, error, destructive, and recovery states;
- review of onboarding and multi-user journeys;
- review of chart meaning, interaction, and financial reconciliation.

This audit was not a substitute for moderated usability research, screen-reader testing, penetration testing, legal review, or production observability analysis.

## Scorecard

| Area | Mass-market readiness |
| --- | ---: |
| Visual design and theme | 8/10 |
| Expense Review workflow | 4/10 |
| Insights and financial trust | 3/10 |
| Household Ops | 4/10 |
| Deals | 4/10 |
| Onboarding | 2/10 |
| Multi-user and workspace safety | 2/10 |
| Mobile experience | 3/10 |
| Accessibility | 4/10 |
| Error recovery and resilience | 2/10 |
| **Overall launch readiness** | **3/10** |

## Severity definitions

- **P0 — Launch blocker:** Can produce incorrect financial outcomes, loss of user control, unauthorized changes, broken critical journeys, or serious trust and safety risk.
- **P1 — Must resolve before general availability:** Broadly damages comprehension, completion, accessibility, reliability, or scalability.
- **P2 — Quality improvement:** Does not independently block launch, but is required for a coherent, high-quality customer experience.

## P0 launch blockers

### 1. Failed Splitwise actions can silently disappear

The generic action wrapper stores failures in internal `log` state, but that state is not rendered in the customer experience. A failed Splitwise operation can move a transaction into an error state, after which Review and Recent Activity no longer retrieve it. Polling then removes it from view.

**Customer harm:** A customer can reasonably conclude that a split succeeded when it failed.

**Required resolution:**

- show persistent inline failure details;
- provide retry and return-to-review actions;
- show explicit success confirmation;
- retain failed transactions in a visible recovery queue;
- never remove an operation until its terminal result is visible.

**Evidence:** `frontend/src/App.tsx`, `app/services/transaction_service.py`

### 2. Draft can create an invalid shared-expense state

The collapsed transaction card exposes Draft before a participant or group has been selected. The backend can persist `shared_draft` with no actual split participant.

**Customer harm:** The transaction leaves Review and contaminates shared-spending analytics despite not representing a real split.

**Required resolution:** Draft must remain disabled until the split has a valid target and allocation.

### 3. Insights performs invalid cross-currency aggregation

Transactions retain a currency, but the Insights service sums raw cents without grouping or conversion. The interface formats the result as USD.

**Customer harm:** USD, CAD, EUR, or other currencies can be combined into a single misleading dollar total.

**Required resolution:**

- provide single-currency views; or
- convert currency using a disclosed rate, date, and source;
- make currency visible in Review and Insights.

**Evidence:** `app/services/spending_insights_service.py`, `frontend/src/insightsVisualization.ts`

### 4. Signed-out recipients can lose workspace invitations

The application processes an invite token only after authentication, but the sign-in action does not preserve the current invitation URL through authentication.

After successful acceptance, the returned workspace is also not selected automatically.

**Customer harm:** A primary multi-user acquisition and onboarding journey fails for signed-out recipients.

**Required resolution:**

- preserve the entire invitation return path through sign-in;
- handle wrong-account recovery;
- switch directly into the joined workspace;
- show a clear confirmation and next action.

**Evidence:** `frontend/src/App.tsx`, `app/api/auth_routes.py`

### 5. Integration ownership and permissions are unsafe or misleading

The UI presents integrations as workspace-wide, while Telegram identities are user-specific. A member may see Telegram as connected even when only another member receives notifications. Ordinary members can also access disconnect controls for workspace-wide Gmail, Plaid, Telegram, and Splitwise connections.

**Customer harm:** Notifications can go to the wrong person, and a member can disable services for everyone.

**Required resolution:**

- distinguish personal integrations from workspace integrations;
- display the exact connected account or identity;
- enforce owner/member permissions in both UI and API;
- let each member connect and verify their own Telegram identity;
- route notifications to every intended recipient.

**Evidence:** `app/api/integration_routes.py`, `app/job_tenancy.py`, `frontend/src/components/AccountSettingsPage.tsx`

### 6. Owners cannot revoke member access or transfer ownership

Members can be invited, but the owner lacks a complete workflow for removing a member or transferring ownership.

**Customer harm:** Access to household and financial data cannot be revoked after a mistake, relationship change, or compromised account.

**Required resolution:** Add role-aware member removal, ownership transfer, last-owner safeguards, confirmation, and an auditable record.

### 7. Successful `204` operations can appear to fail

The shared frontend API helper always parses JSON. Several successful delete, leave, and disconnect endpoints return an empty `204` response.

**Customer harm:** The server can complete an operation while the UI throws, remains stale, and tells the customer nothing useful.

**Required resolution:** Handle empty responses centrally and refetch authoritative state after mutations.

**Evidence:** `frontend/src/lib/api.ts`, `app/api/integration_routes.py`, `app/api/workspace_routes.py`

### 8. Errand routes can still contain unverified destinations

The manual destination field accepts arbitrary text, and the backend treats any nonempty address string as concrete. A customer can enter a generic label such as “Costco” and recreate the unspecified-waypoint problem.

**Customer harm:** The generated route may not point to the physical destination the optimizer evaluated.

**Required resolution:** Every origin, endpoint, primary destination, and errand stop must use a provider-verified address or coordinates before routing.

**Evidence:** `frontend/src/components/HouseholdOpsPage.tsx`, `app/services/route_planning_service.py`

### 9. A generated route can become stale without warning

Changing origin, endpoint, available time, saved locations, or included errands does not invalidate the existing plan. Start Route remains available.

**Customer harm:** The displayed route may no longer correspond to the inputs visible on screen.

**Required resolution:** Bind every plan to an input fingerprint, immediately mark it stale after relevant changes, and disable route launch until recalculated.

### 10. Unverified promotion links are presented as trusted

Unknown HTTPS domains may remain active with a review trust status, while the frontend opens all available deal URLs in the same way.

**Customer harm:** Email-derived phishing or redirect links can inherit the product’s visual trust.

**Required resolution:** Suppress unverified links or show a domain-disclosing warning interstitial before navigation.

**Evidence:** `app/services/promotion_trust_service.py`, `frontend/src/components/PromotionsPage.tsx`

### 11. Privacy and informed consent are not visible enough

Sign-in and integration surfaces do not visibly explain what bank and email data is accessed, who in a workspace can see it, retention and deletion behavior, or where Privacy Policy, Terms, and support are located.

**Required resolution:** Provide these explanations before authentication and before each sensitive integration is connected. If external legal pages already exist, link them prominently within these journeys.

## P1 issues required before broad rollout

### Mobile shell and navigation

Rendered inspection at 390px showed document-level horizontal overflow and clipped content:

- primary navigation extends beyond the viewport;
- Household Ops and secondary tabs become partially inaccessible;
- workspace identity is clipped;
- headings and supporting copy are cut off;
- the first Insights viewport is consumed by navigation, header, and filters;
- several controls are too small for reliable touch interaction.

**Required resolution:** Build a deliberate mobile navigation model, remove document-level horizontal overflow, collapse analytical filters, and use touch-sized action targets.

### Financial meaning and reconciliation

- “My Actual Share” is not reliably calculated relative to the logged-in viewer.
- Total Spend may include unreviewed spending that is absent from Personal and Shared.
- Refunds can appear like positive charges.
- Pending transactions expose actions the backend later rejects.
- Review count and amount can be truncated at 50 without disclosure.
- “Last synced” can reflect transaction modification rather than bank synchronization.
- Review cards omit account/card identity and category.

Every financial value must be reconcilable, scoped, current, and explainable.

### Transactional feedback

Personal, Draft, Split, Post, Undo, Connect, Disconnect, Invite, and workspace actions need:

- local progress state;
- duplicate-submit prevention;
- explicit success;
- visible and actionable failure;
- retry or Undo where appropriate.

A single global busy state should not disable unrelated transactions.

### Activity is not a complete audit trail

Activity is reconstructed from the current transaction state rather than immutable events. It does not reliably represent actors, failures, Telegram decisions, retries, or undo history.

For a financial workflow, Activity should be an append-only, paginated audit history with actor, channel, timestamp, action, result, and correlation to the affected transaction.

### Insights interaction defects

- The synthetic “Other” donut segment filters literal `Other`, which does not represent the grouped segment.
- Personal versus Shared mode lacks a sufficiently clear visible legend.
- Focus points remain tied to total spend rather than the split lines.
- Some apparent chart controls perform no action.
- Category Trend uses a wide fixed canvas on mobile.
- Some chart labels are too small.
- “Previous period” does not show the exact comparison dates.
- All KPI cards have equal emphasis despite Total Spend being primary.
- Retry relies on mutating merchant text rather than explicitly refetching.

### Household loading and scale

- One failed endpoint can block the complete Household initial load.
- Initial loading can display false zero or queue-clear states.
- Pending receipts can disappear behind a newest-50 limit.
- Every receipt-line decision can reload multiple endpoints.
- Partial confirmation does not summarize tracked, ignored, and undecided lines.
- Replenishment suggestions can remain stale after item changes.
- Ignore receipt lacks a clear recovery path.

Today should prioritize real objects and actions, rather than displaying four counts with equal weight.

### Deals resilience and control

- Loading failures can appear as a legitimate empty state.
- “All deals” operates on a capped subset.
- Saved deals cannot be unsaved.
- Mute merchant lacks confirmation, Undo, and an unmute location.
- Clipboard and sync failures are not surfaced consistently.
- Destructive and primary actions have similar visual weight.

### Settings information architecture

Settings currently contains onboarding, integrations, workspaces, members, invitations, Splitwise administration, agent memory, learned corrections, and sign-out in one long page.

It should be separated into:

1. Account
2. Workspace and members
3. Personal connections
4. Workspace connections
5. Expense preferences
6. Splitwise groups
7. Learned behavior
8. Privacy and account actions

Controls must reflect the current user’s role before an action is attempted.

### Reliability and degraded states

The application needs customer-grade handling for:

- expired sessions;
- offline mode;
- slow connections;
- provider outages;
- partial API failure;
- malformed or empty API responses;
- retry exhaustion;
- unexpected frontend rendering errors.

Add a global error boundary, centralized API behavior, last-known-good state, and contextual recovery actions.

### Accessibility

Required work includes:

- accessible live announcements for success and error updates;
- keyboard-accessible chart interaction;
- no-op controls removed from the tab order;
- larger touch targets;
- visible explanations that do not depend on native `title` tooltips;
- focus movement after dialogs and dynamically opened panels;
- reduced-motion support;
- screen-reader verification of forms, tables, charts, and status changes.

## Visual and theme assessment

The visual system is not the launch blocker.

### Strong elements

- credible navy/indigo identity;
- disciplined primary-action color;
- clean typography and spacing;
- restrained neutral content surfaces;
- intentional empty states;
- clearer chart styling and category colors;
- strong desktop composition.

### Required system improvements

- create one reusable dark page-header component;
- use the same active-navigation treatment everywhere;
- give Settings a page identity;
- reduce the “every block is a card” effect;
- define compact, standard, command, and analytical card variants;
- visually separate destructive actions;
- avoid tiny chart labels;
- preserve useful hierarchy when layouts stack on mobile.

## Surface-specific direction

### Expense Review

- Keep Personal and Split as the dominant decisions.
- Reveal participant, group, payer, and allocation controls only after Split.
- Remove Draft from the default collapsed state until it is valid.
- Show account/card, category, settlement status, and transaction provenance.
- Replace ambiguous simultaneous badges such as “Settled” and “Needs review” with clearer language.
- Remove the duplicate or ambiguous refresh concept.
- Collapse filters when queue volume is low.

### Insights

- Make Total Spend visually primary.
- Show exact current and comparison date ranges.
- Guarantee KPI reconciliation or visibly explain excluded amounts.
- Make chart legends, values, and drill-down behavior truthful.
- Use responsive chart alternatives rather than shrinking desktop charts.
- Surface data freshness and currency.

### Household Ops

- Use a household-wide page title rather than an errand-only title on every tab.
- Make Today a prioritized command center with one recommended next action.
- Compress zero-state status cards.
- Verify all route locations and invalidate stale plans.
- Batch receipt classification rather than reloading after each line.

### Deals

- Clearly distinguish loading, empty, disconnected, and failed states.
- Verify or warn before navigating to email-derived destinations.
- Keep Open and Save visible; move destructive feedback into an overflow menu.
- Support unsave, unmute, pagination, and truthful totals.

### Onboarding and Settings

Recommended first-run sequence:

1. Create account and personal workspace.
2. Explain privacy and workspace visibility.
3. Connect Plaid.
4. Review the first transaction.
5. Connect personal Telegram notifications.
6. Connect Splitwise if sharing is needed.
7. Connect Gmail as an optional receipt and deals feature.
8. Invite a workspace member.

Each step should explain what it enables, what data it accesses, whether it is optional, and how successful connection is verified.

## Required launch workstreams

### 1. Financial correctness

- currency handling;
- refund semantics;
- viewer-relative share calculation;
- KPI reconciliation;
- pending-transaction action rules;
- truthful counts and totals.

### 2. Action integrity

- explicit success and failure states;
- failed-operation recovery queue;
- valid draft requirements;
- immutable activity history;
- idempotency and duplicate-submit protection.

### 3. Identity and access

- preserved invitation flow;
- owner/member permission matrix;
- member removal and ownership transfer;
- personal versus workspace integrations;
- correct multi-user Telegram delivery.

### 4. Safety and trust

- privacy, terms, data-use, and support surfaces;
- verified route destinations;
- route freshness;
- promotion-link trust;
- clear destructive-action consequences.

### 5. Mobile and accessibility

- responsive global shell;
- compact analytical filters;
- touch-sized targets;
- keyboard and screen-reader support;
- reduced motion;
- validation at 320, 375, 390, 768, and 1024px.

### 6. Resilience and scale

- partial loading and last-known-good data;
- pagination and truthful result totals;
- offline and session-expired experiences;
- global error boundary;
- centralized API response and error handling.

### 7. Release validation

- component and end-to-end tests for every critical journey;
- owner/member permission tests;
- signed-out and wrong-account invitation tests;
- multiple members connecting Telegram independently;
- provider cancel, timeout, outage, and retry tests;
- failed Splitwise post recovery;
- currency and refund reconciliation;
- stale route invalidation;
- unverified promotion-link handling;
- screen-reader and keyboard validation;
- cross-browser and slow-network testing.

## Launch exit criteria

The mass-market launch should remain blocked until:

- every P0 finding is resolved and covered by automated end-to-end tests;
- financial totals reconcile by currency and user perspective;
- no failed operation can disappear or appear successful;
- every workspace mutation is role-safe and recoverable;
- invitation onboarding succeeds from a signed-out state;
- every user can independently verify their connected identities;
- routes contain only verified, current destinations;
- unverified deal links cannot inherit product trust silently;
- the complete application is usable without horizontal document overflow at supported mobile widths;
- loading, empty, failure, offline, and expired-session states are distinct;
- privacy, data use, deletion, Terms, and support are visible at the appropriate decision points;
- accessibility and critical-journey test suites pass.

## Final recommendation

Do not launch ExpenseOps to all customers in its current state.

The product is visually ahead of its operational maturity. Another visual-polish pass or additional charts will not close the launch gap. The next phase should be a dedicated launch-hardening program centered on financial correctness, identity and permissions, transactional feedback, route and link safety, mobile usability, accessibility, resilience, and end-to-end validation.

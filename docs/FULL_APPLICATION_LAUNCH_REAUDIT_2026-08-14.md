# ExpenseOps Full-Application Launch Re-audit

**Audit date:** August 14, 2026
**Audited revision:** `fce8c5bd1bf27a480a3228422590ac728656b648`
**Branch state:** clean `main`, matching `origin/main`
**Production reviewed:** `https://expenseops-production.up.railway.app`
**Evaluation standard:** General availability, with a separate controlled-beta reconciliation
**GA decision:** **NO-GO for broad customer launch**

This is the current, cross-functional launch audit of ExpenseOps. It covers desktop and mobile UI,
end-to-end customer experience, backend correctness, reliability, production operations,
cybersecurity, privacy, and tenant isolation. It supersedes earlier readiness conclusions only where
new evidence contradicts them; it does not erase the historical record.

Related historical documents:

- [Visual Aesthetics Audit](./VISUAL_AESTHETICS_AUDIT.md)
- [UX Launch-Readiness Audit](./UX_LAUNCH_READINESS_AUDIT.md)
- [Backend Launch-Readiness Audit](./BACKEND_LAUNCH_READINESS_AUDIT.md)
- [Original Launch-Readiness Remediation Plan](./LAUNCH_READINESS_REMEDIATION_PLAN.md)
- [Launch-Readiness Traceability](./LAUNCH_READINESS_TRACEABILITY.md)
- [Production Operations Runbook](./PRODUCTION_OPERATIONS_RUNBOOK.md)
- [Independent Design-User Beta Audit](./INDEPENDENT_DESIGN_USER_BETA_AUDIT_2026-08-14.md)
- [August 14 Closure Strategy](./CONSOLIDATED_LAUNCH_REMEDIATION_STRATEGY_2026-08-14.md)

## Independent audit source and scope

The user supplied an independent Claude Code audit, now archived locally as
[INDEPENDENT_DESIGN_USER_BETA_AUDIT_2026-08-14.md](./INDEPENDENT_DESIGN_USER_BETA_AUDIT_2026-08-14.md).
The original
[Claude Code artifact](https://claude.ai/code/artifact/29ab7639-180e-49fe-8f3f-5f8b460a22bf?via=auto_preview)
remained organization-scoped, so the attached text export is the durable comparison source.

The two audits use different launch bars:

- the independent audit evaluates one pre-approved, operator-supported external design user; and
- this full-application audit evaluates broad/multi-customer launch and includes live Railway and
  production-browser evidence.

Neither verdict should be quoted without that release scope.

## Release-scope disposition

| Release | Independent review | Reconciled current disposition |
| --- | --- | --- |
| First external design-user beta | Conditional GO after one test fix and live preflight | **Held** until the combined beta gate closes; current production evidence adds credential, readiness/RLS, CSP, worker, and customer-truth conditions |
| General availability | NO-GO | **NO-GO**; R0–R7 closure and exact-release re-audit required |

## Cross-audit reconciliation

| Topic | Independent audit | Full audit evidence | Adjudication |
| --- | --- | --- | --- |
| GA launch | NO-GO | NO-GO | Full agreement |
| Controlled beta | Conditional GO based mainly on code/history and manual preflight | Live `/readiness` 503, production CSP failure, credential incident, worker and UX truth defects | Conditional status is not yet earned; use the combined beta gate in the strategy |
| Financial journal | Idempotency, leases, and ambiguity recovery are real | Confirmed as verified strengths | Preserve the architecture; fix worker execution context around it |
| Backend suite | 582 passed, 1 failed when live Maps routing was enabled | 583 passed in a different environment | Both results are credible and prove the suite is non-hermetic |
| RLS/readiness code | Correctly fail-closed | Confirmed | Strong code is being bypassed by current Railway health/runtime configuration |
| Live RLS | Not observable in code-only review | Privileged production role; `/readiness` reports `database_rls:false` | Production observation controls; P0 remains |
| CSP | General restrictive wiring assessed positively | Clean production browser showed Plaid CDN blocked | General CSP quality and Plaid incompatibility can both be true; production failure controls |
| Trust-critical UX | Historical blockers considered closed for beta | Current source shows false-empty, member Plaid, failed Undo, and currency-preview defects | Newer direct evidence controls; UX P0s remain |
| Account deletion | Historical V6 evidence treated self-service deletion as absent/disclosed | Current UI exposes deletion, but service leaves promised exclusive data | Historical conclusion is stale; incomplete implemented deletion is a P0 contract defect |
| Migration/IaC | Migration job not represented in repository | Live web still runs Alembic; no dedicated migration service observed | Agreement, with stronger production evidence |
| README Railway claim | False | Confirmed directly | Documentation corrected and runtime drift remains a remediation item |
| Bundle/lint | 604 KB chunk and 20-warning ceiling | Same | Agreement; maintainability work remains |
| Backup/restore/alerts | Unverifiable and required | Unproven live | Full agreement |

Unique independent findings incorporated here are the live-provider test leak, Railway/README source-
of-truth drift, owner-bootstrap cleanup attestation, lint warning headroom, and a deliberate license
decision before public distribution. The independent audit does not remove any current P0 because it
did not have the later live production evidence.

## Executive verdict

ExpenseOps has a significantly stronger product and engineering foundation than at the start of the
previous remediation program. The visual system is cohesive, most core journeys are responsive, the
financial-operation journal is materially safer, credentials are encrypted at rest, and the
automated suite is broad.

It is nevertheless not safe to launch broadly. Current evidence includes:

- an immediate credential-rotation incident;
- a production database role that bypasses the promised RLS tenant boundary;
- two background-worker scope defects that the privileged database role masks;
- production Plaid onboarding blocked by the live Content Security Policy;
- financial queues that can falsely appear empty or complete;
- member Plaid onboarding that ends in an authorization error;
- deletion and retention behavior that does not match customer-facing promises; and
- no proven backup/PITR restore or delivered-alert evidence.

Do not expand beyond the current pre-approved design-user cohort or enable public registration until
the applicable release gates in the closure strategy pass.

Unless a finding explicitly says otherwise, P0/P1 severity in this document is assessed against GA.
The strategy separately identifies which items block the controlled beta.

## Discipline scorecard

| Discipline | Verdict | Summary |
| --- | --- | --- |
| Visual design | Conditional pass | Coherent navy/indigo identity and improved hierarchy; responsive charts, density, dialogs, and component consistency need work |
| Customer UX | No-go | Failures can appear as signed-out, empty, disconnected, or successful states in critical journeys |
| Backend correctness | No-go | Durable worker tenant scope, notification recovery, receipt safety, and scale paths have confirmed defects |
| Cybersecurity | No-go | Active rotation incident, inactive production RLS, redirect/webhook/link-trust weaknesses |
| Privacy/data lifecycle | No-go | Deletion and retention promises are not fulfilled completely |
| Production operations | No-go | Readiness is failing without gating traffic; recovery and alert delivery are unproven |

## Audit method and evidence hierarchy

The audit used:

- source inspection across frontend, API, services, jobs, models, migrations, CI, container, and docs;
- desktop, tablet, and mobile responsive review at 320, 375, 390, 768, 1024, and 1440 CSS pixels;
- Chromium, mobile Chromium, Firefox, and WebKit browser coverage;
- backend, frontend, migration, lint, build, and dependency validation;
- read-only Railway service, deployment, configuration-presence, health, readiness, role, and log
  inspection;
- a clean production-browser check of the actual FastAPI middleware and CSP; and
- threat modeling across unauthenticated, malicious-tenant, replay, provider, operator, and
  compromised-runtime scenarios.

When evidence conflicts, use this order:

1. observed production behavior;
2. production-container or restricted-role integration test;
3. end-to-end browser/provider test;
4. unit or component test;
5. code inspection;
6. design intent or documentation claim.

No exploitation, destructive testing, provider mutation, code change, or production configuration
change was performed as part of the audit. The inadvertent settings-representation incident is
documented below; no secret value is reproduced in this report.

## Immediate incident: credential disclosure during audit

### INC-P0-001 — Settings representations expose secret values

During configuration introspection, the default Pydantic Settings representation printed non-empty
local `.env` values into the private audit tool transcript. The values are not repeated here.

Root cause: secret-bearing settings are ordinary strings in
[`app/config.py`](../app/config.py), so model representations and potentially diagnostic paths do not
redact them.

Scope observed included live-looking provider/application credentials for Plaid, Splitwise,
Telegram, OpenAI, Gmail/Google OAuth, Google Maps, and application encryption. `.env` is ignored and
untracked, and no corresponding Git exposure was found. Transcript exposure is still a security
incident; every affected value must be considered compromised.

Required response:

1. pause broad onboarding and avoid printing Settings objects;
2. revoke/rotate every non-empty local credential at its provider;
3. replace affected local and Railway values;
4. rotate the application encryption key through the staged keyring process, never by blind
   replacement;
5. re-register callbacks and reauthorize users where provider rotation requires it;
6. inspect provider usage/security logs and revoke sessions if incident policy requires it;
7. use `SecretStr` or `Field(repr=False)` for every secret-bearing field; and
8. add tests proving settings, logs, validation errors, and exceptions cannot reveal values.

Closure evidence:

- all superseded credentials are provider-revoked;
- the application can still decrypt every stored integration after staged key rotation;
- no secret value appears in `repr(Settings)`, logs, exceptions, or tests; and
- repository and history scans report no high-confidence credential exposure.

## P0 launch blockers

### SEC-P0-001 — Production database tenant isolation is not active

Live `/readiness` returned HTTP 503 with `database_rls:false`. The application correctly treats the
current database identity being superuser or `BYPASSRLS` as a production-critical failure in
[`app/main.py`](../app/main.py). Read-only database inspection confirmed the production runtime uses
the privileged `postgres` role.

Application-level SQLAlchemy tenant criteria exist, so this audit did not prove a current cross-
tenant read. The required second boundary is absent: one missed ORM predicate could become a data
breach.

Do not switch roles blindly. Current code contains restricted-role workflow defects, including
invitation and background-worker paths, which the privileged role masks.

Required closure:

- separate least-privilege web, worker/cron, and migration identities;
- remove caller-settable general RLS bypass from ordinary runtime paths;
- apply workspace scope before every tenant query in the same transaction/session;
- run real PostgreSQL two-tenant, invitation, webhook, and worker tests under restricted roles; and
- make `/readiness`, not `/health`, the Railway deployment/traffic health gate.

### BE-P0-001 — Splitwise outbox worker lacks a safe workspace credential context

[`app/jobs/outbox.py`](../app/jobs/outbox.py) scopes the database and sets an active user, but the
Splitwise handler does not set and reliably clear the active workspace before constructing the
transaction/Splitwise services. Credential lookup in
[`app/services/splitwise_service.py`](../app/services/splitwise_service.py) depends on that active
workspace.

Consequences:

- with removed legacy global Splitwise credentials, queued operations fail; or
- after another handler leaves a stale context, an operation can resolve credentials for the wrong
  workspace.

Existing worker tests monkeypatch Splitwise and do not exercise actual credential resolution.

Required closure: explicit event execution envelopes, one scoped worker context manager that sets
and resets both user and workspace in `finally`, actual per-user credential-resolution tests, and
randomized interleaved multi-workspace worker tests.

### BE-P0-002 — Plaid outbox flow is incompatible with restricted RLS

[`app/api/plaid_routes.py`](../app/api/plaid_routes.py) opens a new session and queries `PlaidItem`
before applying workspace scope. The outbox caller's scope exists on a different session. Under the
required restricted role, the item is invisible and the webhook repeatedly retries or dead-letters.

The current privileged role and superuser-based CI conceal this defect.

Required closure: carry item workspace identity in the durable event, scope the same database
session before lookup, and run the real worker using a non-superuser PostgreSQL test role.

### SEC-P0-002 — Production CSP blocks Plaid Link

[`frontend/index.html`](../frontend/index.html) loads Plaid Link from its CDN, while
[`app/security_middleware.py`](../app/security_middleware.py) permits scripts only from the
application origin and does not define the necessary Plaid frame/connect allowances.

A clean production Chromium session confirmed:

- `window.Plaid` was unavailable;
- the Plaid CDN script request was blocked by CSP; and
- new-bank onboarding cannot open Plaid Link.

Required closure: define the minimum documented Plaid script/frame/connect origins, keep every other
source closed, and add an end-to-end test against the production container and middleware—not only
the Vite development server.

### UX-P0-001 — Load failures can masquerade as signed out or “all caught up”

[`frontend/src/App.tsx`](../frontend/src/App.tsx) resolves authentication in a `finally` path even
when `/api/context` fails. Transaction-load errors are caught into state that is not rendered. Empty
arrays then produce the successful “You’re all caught up” view.

This can visually erase transactions requiring review or recovery during an outage.

Required closure:

- explicit `idle`, `loading`, `success`, `empty`, `stale`, `unauthorized`, and `error` states;
- independent Review, Recovery, and Activity loading/failure boundaries;
- successful empty states only after a confirmed successful fetch; and
- tests for offline, timeout, 401, 403, 5xx, malformed response, and partial endpoint failure.

### DATA-P0-001 — Review totals silently describe only the first 50 rows

[`app/api/transaction_routes.py`](../app/api/transaction_routes.py) defaults to 50 rows without a
complete total/pagination contract. The frontend performs no continuation request but presents count
and amount as the whole queue.

Required closure: cursor/offset pagination with stable ordering and authoritative totals, plus UI
continuation and a seeded greater-than-50 reconciliation test.

### QA-P0-001 — Ordinary tests can call the live Google Routes API

The independent audit produced `582 passed, 1 failed` while another run of the same revision
produced 583 passed. Source inspection explains the difference:

- the API test overrides only the database;
- the endpoint constructs `WhileOutService(db)` without injected settings/provider;
- the service reads cached ambient `.env` settings; and
- when both `HOUSEHOLD_ROUTING_PROVIDER=google_maps` and a non-empty Google Maps key are present, it
  constructs a real HTTP client and calls Google Routes/Route Matrix.

The hard-coded fallback assertion then fails when the paid provider succeeds. A key alone is not
sufficient if the provider remains `fallback`; both settings are required.

This is a beta and GA release blocker because the exact same revision can be green or red depending
on ambient credentials, and ordinary test execution can create external cost/provider telemetry.

Required closure: inject/override the route service/provider, block outbound networking by default in
ordinary tests, isolate explicitly opt-in provider contract tests, clear cached settings around
environment tests, and prove the full suite passes with provider variables absent and with inert
values present.

### UX-P0-002 — Member Plaid onboarding ends in an owner-only sync failure

Settings promises that each member connects their own bank. Link/token exchange accepts an
authenticated member, but the client immediately calls an owner-only manual sync endpoint. The
resulting 403 is presented as a generic sync failure and review data is not refreshed reliably.

Required closure: queue or complete the authenticated member's own item import, return an operation
status from exchange, refresh from that result, and expose only actions the member is authorized to
perform.

### UX-P0-003 — Failed Undo is invisible in Recently handled

Undo errors are stored per transaction, but the Recent Activity/Recently handled component does not
render those notices. A financial reversal can fail with no visible result, retry, reconciliation,
or support identifier.

Required closure: keep the affected row, show persistent transaction-scoped outcome, and provide a
safe retry/reconciliation action and correlation ID.

### UX-P0-004 — Custom split previews mislabel non-USD transactions

The transaction amount uses its ISO currency, while equal/exact/percentage/share previews hard-code
the dollar symbol. A EUR or CAD allocation can be represented as USD immediately before posting.

Required closure: format every intermediate and final value with the transaction currency and add
non-USD split-preview/posting tests.

### PRIV-P0-001 — Account deletion does not fulfill the product promise

[`app/services/data_lifecycle_service.py`](../app/services/data_lifecycle_service.py) revokes
identities and credentials and removes only selected receipt/promotion records in an exclusive
workspace. It leaves membership/workspace state, bank/financial/outbox history, household/model
data, errands/routes, exact saved addresses and coordinates, preferred places, memories,
checkpoints, settings, and other records.

Customer-facing copy says content only that user could access is removed. The implementation and
promise do not match.

Required closure: a reviewed data inventory and deletion matrix for personal, exclusive-workspace,
shared-workspace, immutable financial, and legally retained data; complete deletion or irreversible
anonymization; updated copy; and Postgres end-to-end deletion tests.

### OPS-P0-001 — Recoverability and production gating are not proven

Production remained online while `/readiness` returned 503. Railway uses `/health` as its health
check. Its web start command still runs `alembic upgrade head` before Uvicorn, contrary to the
dedicated migration-owner model in the runbook.

There is no verified evidence of enabled PITR/backups, a timed isolated restore, achieved RPO/RTO,
retention execution, or delivered alerts. The project itself lists these as open GA gates.

Required closure:

- dedicated migration job with owner credentials;
- least-privilege application processes with no migration command;
- `/readiness` gating;
- enabled backup/PITR policy;
- a timed restore into an isolated environment;
- monitored retention and worker/cron cycles; and
- delivered human alert tests and rollback proof.

## P1 — high-priority findings before GA

### UI and visual implementation

1. **Settings all-or-nothing loading.** One failed request in a `Promise.all` can make all providers
   look disconnected. Each section needs independent loading, stale, failure, and retry state.
2. **Mobile Insights readability.** A fixed 640-unit SVG compresses labels and data points to very
   small effective sizes on narrow screens. Use responsive chart/table alternatives and minimum
   readable/tappable geometry.
3. **Large initial bundle.** A single roughly 604 KB minified JavaScript chunk includes the large
   Sandbox Lab and a globally blocking Plaid script. Route-split and lazy-load both.
4. **Household global busy state.** One action disables unrelated panels. Use operation-scoped state.
5. **Oversized mobile page headers.** Action-heavy headers consume too much of the first viewport.
6. **Native destructive confirmation.** `window.confirm` is used across Settings, Household, and
   Splitwise. Replace it with accessible, contextual dialogs and explicit consequences.
7. **Component-system drift.** Card and Surface primitives overlap, `CardContent` spacing is
   surprising, and an undefined `shadow-elevated` class is referenced.
8. **Metadata polish.** The declared Inter stack is not loaded consistently; favicon, theme color,
   and richer document metadata are missing.
9. **Lint debt.** Twenty warnings include missing hook dependencies and dead UI state.

### End-to-end customer experience

1. First-run users land in Review without a role-aware activation path; completion currently means
   any integration exists rather than the user's required personal capabilities.
2. OIDC, Gmail, and Splitwise cancellation/callback failures can expose raw API/framework pages
   instead of branded recovery with preserved destination and support correlation.
3. Expired-session recovery claims to preserve the page but does not carry the full
   path/query/hash through sign-in.
4. Mobile account controls hide the active workspace, and workspace choices are not supplied to the
   global menu. Financial actions can occur without clear household context.
5. Plain Telegram `/start`, `/help`, and arbitrary text outside a pending flow can receive no reply.
6. Pending transactions expose Split/Draft controls even though the backend rejects the operation.
7. Draft can be offered before a participant is selected, leading to a payer-only invalid attempt.
8. Splitwise searches rely on placeholders, lack clear no-results states, and do not provide direct
   connection recovery when identity lookup fails.
9. Financial Activity stops at 200 entries and has no Load more.
10. Invitation administration lacks a pending list, expiry/status, resend, and revoke UI even though
    the backend supports revocation.
11. Receipt and place editors mount far from their row triggers on mobile without focus/scroll or
    return-focus handling.
12. Household status messages lack consistent live-region semantics and correlation identifiers.
13. Deals search, Saved, Expiring, and category views are incomplete beyond the first 100 items.
14. Merchant mute copy promises a preferences recovery path that does not exist.
15. Pre-auth sign-in has no concise Privacy, Terms, support, or data-use context.
16. SPA navigation does not move focus or announce page changes.

### Backend and reliability

1. `/api/admin/operations` queries outbox and financial states that do not match the real state
   machines. It hides retries, dead events, and reconciliation work; released leases can be counted
   as expired; Gmail freshness can mask one lagging flow; and tenant scope prevents fleet truth.
2. Retention purges outbox state `completed`, but successful rows use `succeeded`; sensitive payloads
   are retained indefinitely.
3. A dead Telegram review notification cannot self-heal after a user connects Telegram because the
   stable dedupe record and queued marker remain.
4. Gmail and weekly jobs enumerate/decrypt every tenant serially. One corrupt/revoked credential can
   fail a global run before per-tenant isolation.
5. Receipt Gmail scanning uses page tokens but not a durable incremental history cursor, causing
   repeated historical scans and growing freshness risk.
6. Receipt auto-confirm considers only matched lines; unmatched lines can disappear behind a
   confirmed receipt. Legacy web and Telegram confirm paths bypass the newer optimistic-version and
   undecided-acknowledgement contract.
7. Insights loads up to two years of current and prior ORM rows and aggregates in Python.
8. Plaid exchange and manual all-item sync perform provider pagination inside the HTTP request.
9. The outbox claims up to 25 events with one 180-second lease, processes serially, and does not
   heartbeat. Slow provider work creates head-of-line and duplicate-side-effect windows.
10. Structured logs and status endpoints exist, but no reliable fleet metrics, provider latency,
    queue-age, p95/p99, pool-pressure, or delivered-alert evidence exists.
11. `railway.json` contains only a schema declaration. Expected services, migration ownership,
    commands, schedules, role class, and readiness healthcheck are neither version-controlled nor
    checked against live Railway configuration.
12. Owner-bootstrap and legacy single-user variable removal are documented but lack a redacted
    release attestation, making a launch-day cleanup step easy to skip.

### Cybersecurity and privacy

1. **Plaid webhook amplification/replay.** The public endpoint buffers an unbounded body before
   verification, persists invalid requests, permits attacker-controlled key fetches, lacks a strict
   accepted-token age check, and lacks replay uniqueness and adequate retention.
2. **OIDC bearer availability risk.** Discovery/JWKS synchronous HTTP is performed from async
   middleware without a cache, so fabricated tokens can block the event loop and amplify IdP calls.
3. **Open redirects.** Protocol-relative `redirect_after` values pass the leading-slash test; HTTPS
   redirects can trust unvalidated forwarded host data before TrustedHost handles the request.
4. **Promotion phishing trust.** Merchant substring matching and unverified email From domains can
   label attacker-controlled destinations trusted and skip the interstitial.
5. **Schema/body bounds.** There is no global request-body cap and multiple strings/lists/metadata
   fields have no bounded size or count.
6. **OIDC hardening.** Authorization uses state but not nonce/PKCE; asymmetric algorithms should be
   explicit; secret requirements should be validated; Gmail must require `email_verified is True`.
7. **High-impact actions.** Account deletion and ownership transfer lack recent-authentication or
   step-up assurance.
8. **Auth middleware blocking.** Auth performs synchronous database and identity work on every
   authenticated request and writes `last_seen` each time.
9. **Release coverage.** GitHub CI does not run the complete browser suite against the production
   container/middleware or use a restricted PostgreSQL runtime role.

## P2 — hardening and coherence

### UI/UX

- A chart tooltip includes Total where Total is not plotted in that view.
- Native `<details>` is used for complex analytical filters rather than a responsive product
  popover/sheet.
- Deals mobile tabs can clip without a strong continuation cue.
- Settings/Household subviews are not URL-addressable, so Back/deep links cannot restore a subtask.
- Settings nests another `<main>` inside the application shell.
- Provider-outage status can remain sticky after successful recovery.
- Several form controls rely on placeholders rather than programmatic labels.
- Splitwise and Household notices are not consistently `alert`/`status` live regions.
- Closed place candidates cannot be selected for a future trip.
- There is no customer-facing data export control.
- Error extraction often drops support/correlation IDs.
- Visual snapshot coverage remains thinner for error/loading, Activity, invitations, group
  management, receipt review, and destructive dialogs than for happy paths.

### Backend/security/operations

- Gmail HTTP has a timeout but no bounded retry/backoff or `Retry-After` behavior.
- Telegram delivery is at-least-once; provider acceptance followed by process failure before the
  database commit can duplicate a message.
- Telegram still accepts a query-string webhook-secret fallback and uses a non-constant-time
  comparison; link-code consumption is not atomic.
- Raw provider error details can be returned to clients instead of stable public error codes.
- Arbitrary `X-Request-ID` values are trusted/reflected and can exceed database limits.
- Admin monitoring state-name and tenant-scope defects create blind spots.
- Legacy consent can fail open when no managed Gmail account exists; it should be explicitly
  local-only.
- Dependency audits pass, but Python dependencies lack hashes, CI actions/images use mutable tags,
  and no SBOM, signature, or provenance gate was found.
- Sessions are strong but customers cannot view/revoke individual devices or revoke all sessions.
- Frontend lint has exactly 20 warnings against a 20-warning CI ceiling, leaving no headroom and
  allowing the warning baseline to become permanent debt.
- The all-rights-reserved repository posture is explicit, but a deliberate license decision is
  required before inviting public contribution, reuse, or distribution.
- Detailed public readiness reveals migration/integration configuration metadata.
- Security headers were present on normal production responses but not consistently observed on
  middleware-generated authentication errors.
- Docker deployment traceability to an immutable source commit is weaker than desired.

## Verified strengths

The following should be preserved:

- a cohesive visual identity and shared headers/surfaces;
- responsive navigation, overflow protection, minimum touch targets, visible focus, reduced motion,
  and accessible chart data tables in covered paths;
- trustworthy Insights date/currency/refund explanations and viewer-relative share work;
- verified route destinations and stale-route launch prevention;
- deals destination disclosure and an interstitial for unverified links;
- durable financial-operation journal, deterministic idempotency marker, leases, retries,
  reconciliation, and immutable Activity foundations;
- verified Plaid owner and personal Splitwise identity enforcement in the core service;
- strong random session tokens stored only as hashes with expiration and revocation;
- secure production cookie attributes and Origin checks for cookie-authenticated mutations;
- short-lived, hashed, encrypted, atomically consumed OAuth state;
- authenticated Fernet encryption with versioned key rotation;
- application-level tenant criteria and write validation;
- Plaid ES256/body-hash verification and Telegram secret-header/update-id protection;
- CORS/host allowlists, HSTS, no-sniff, frame denial, generic 500 handling, and disabled production
  API docs;
- parameterized SQLAlchemy usage and escaped React rendering with no shipped
  `dangerouslySetInnerHTML` path;
- non-root container execution and locked npm installation; and
- no confirmed SQL injection, command injection, server-side SSRF, or shipped React XSS.

## Validation results

| Gate | Result |
| --- | --- |
| Backend tests | 583 passed in the Codex run; independent run produced 582 passed/1 failed with live Google routing enabled, confirming environment dependence |
| Focused reliability tests | 88 passed |
| Ruff | Passed |
| Fresh migrations | Reached `20260813_0023`; `alembic check` clean |
| Frontend unit tests | 21 passed |
| Frontend lint | 0 errors, 20 warnings |
| Frontend production build | Passed; one 604.21 KB minified / 167.58 KB gzip initial-chunk warning |
| Playwright | 136 passed across Chromium, mobile Chromium, Firefox, and WebKit |
| Python dependency audit | No known vulnerabilities |
| npm dependency audit | 0 vulnerabilities |
| Repository secret scan | No high-confidence tracked credential; `.env` ignored/untracked |
| Production `/health` | HTTP 200 |
| Production `/readiness` | HTTP 503 because `database_rls:false` |
| Production Plaid script | Blocked by CSP in clean Chromium |

The mostly green checks do not invalidate the findings, and the discrepant backend result is itself
evidence that the suite is environment-dependent. Current automation does not exercise the actual
production CSP, restricted-role PostgreSQL worker behavior, real provider owner/member onboarding,
backup restore, alert delivery, or several false-success failure states.

## Production observations

- The web service and outbox worker were online at audit time.
- Gmail receipt/promotion cron services existed, but one observed build-only deployment is not proof
  of a successful current execution cycle.
- The app healthcheck targeted `/health`, not the failing `/readiness` contract.
- The web start command still included `alembic upgrade head`.
- No dedicated migration or retention service was visible.
- The production runtime database identity was privileged.
- No current queue records existed to prove the worker-context defects had executed safely.
- The deployment metadata did not provide the desired immutable commit-to-artifact traceability.

## Coverage still required

- Real first-run OIDC, wrong-account, callback cancellation, expired state, and session-expiry E2E.
- Owner and member Plaid, Gmail, Splitwise, and Telegram onboarding with real provider callbacks.
- Two-tenant restricted PostgreSQL API, invitation, webhook, worker, and cron tests.
- Failure-not-empty tests for every primary page.
- More-than-50 Review and more-than-200 Activity tests.
- Non-USD custom split preview/posting tests.
- Production-container CSP/security-header browser suite.
- Webhook replay, oversized body, rate, forged-host, and redirect tests.
- Mobile receipt/resolver focus and return-focus tests.
- Manual VoiceOver, TalkBack, NVDA/JAWS, high-contrast, switch, and voice-control journeys.
- Physical-device iOS/Android navigation and keyboard tests.
- Provider timeout, 429, token-expiry, partial outage, retry, dead-letter, and crash-window tests.
- Load/soak tests with declared latency, memory, query, queue-age, and bundle budgets.
- Timed backup restore, rolling deployment, failed migration, rollback, and alert delivery drills.

## Release rule

No finding is closed by a code change alone. Closure requires:

1. linked implementation;
2. automated regression coverage at the correct boundary;
3. applicable customer-facing or operational evidence;
4. no regression in existing workflows; and
5. an independent re-audit against the exact immutable release candidate.

The full phased closure program is defined in
[CONSOLIDATED_LAUNCH_REMEDIATION_STRATEGY_2026-08-14.md](./CONSOLIDATED_LAUNCH_REMEDIATION_STRATEGY_2026-08-14.md).

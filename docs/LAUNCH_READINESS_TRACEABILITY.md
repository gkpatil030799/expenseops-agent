# ExpenseOps Launch-Readiness Traceability

**Program branch:** `agent/launch-readiness`

**Baseline commit:** `f11f960`
**Last updated:** August 12, 2026

This file records the implementation and validation evidence for the remediation program in
[`LAUNCH_READINESS_REMEDIATION_PLAN.md`](./LAUNCH_READINESS_REMEDIATION_PLAN.md).

## Baseline gate

| Check | Result |
| --- | --- |
| Backend Ruff | Passed |
| Backend tests | 522 passed; 5 deprecation warnings |
| Frontend unit tests | 15 passed |
| Frontend production build | Passed |
| Existing frontend lint command | Passed, but did not inspect TypeScript/TSX |
| Production health/readiness at audit time | HTTP 200; DB and migration checks passed |

The pre-remediation Insights/UI work and all three audits were preserved in `f11f960` before
visual-foundation changes began.

## Workstream status

| Workstream | Status | Evidence |
| --- | --- | --- |
| Phase 0 — baseline protection | Complete | Baseline commit `f11f960`; backend and frontend baselines recorded above |
| V1 — visual foundation and regression harness | Complete | Shared tokens/primitives; TypeScript-aware lint; Playwright visual, overflow, and axe gates; implementation checkpoint on `agent/launch-readiness` |
| V2 — navigation, mobile shell, page headers | Complete | Responsive app shell, three-destination primary nav, account menu, mobile bottom nav, and shared contextual headers; implementation checkpoint on `agent/launch-readiness` |
| V3 — Expense Review hierarchy | Complete | Compact filter sheet/chips, trustworthy transaction metadata, neutral amount hierarchy, two dominant decisions, overflow actions, guided split steps, and five-row recent activity; implementation checkpoint on `agent/launch-readiness` |
| V4 — Insights narrative and charts | Complete (visual layer) | Scoped reporting context, primary Total Spend hierarchy, comparison narrative, collapsed filters, coherent chart order, keyboard-readable points, mobile alternatives, and data tables; accounting release gate remains open until Phase 6 |
| V5 — Household command center | Complete (visual layer) | Household-wide page identity, one prioritized Today action, compact all-clear state, route summary on Today, full builder under Errands, Start → Stops → End sequence, active/history receipt separation, and mobile tab fades |
| V6 — Settings information architecture | Complete (visual layer) | Eight explicit destinations, desktop sidebar/mobile selector, owner-aware workspace controls, separated connection scopes, discoverable Splitwise group tools, dedicated privacy/danger section, and completed-onboarding suppression |
| V7 — Deals hierarchy | Complete (visual layer) | Value-first deal cards, merchant identity, trusted-domain disclosure, review interstitials for unverified links, visible Open/Save actions, feedback overflow, purposeful empty/disconnected states, and urgency-based expiry treatment |
| V8 — visual/accessibility validation | Complete (automated gate) | Six-width responsive matrix, 44px touch-target audit across primary and expanded flows, keyboard skip/focus restoration, reduced motion, 200%-zoom-equivalent layout, mobile-nav clearance, edge-state fixtures, and four-project Playwright coverage |
| Phase 2 — UX action integrity | Complete | Structured API client; global resilience surfaces; scoped Expense, Deals, Settings, and Splitwise actions; cross-browser failure-isolation gate; checkpoint on `agent/launch-readiness` |
| Phase 3 — identity and tenancy | In progress | Tenant-safe uniqueness and schema parity; verified OIDC email; atomic OAuth state claims; invite return/switch/wrong-account recovery; owner/member API matrix; removal/transfer; per-user Telegram and Splitwise; explicit Plaid ownership; exact connected identities. PostgreSQL RLS/request-worker role rollout remains an open exit-gate item. |
| Phase 4 — financial correctness | In progress | Durable Splitwise create/update/delete journal, deterministic idempotency marker, atomic leases, pending-to-posted relationships, finalized-amount and removal reconciliation, valid-draft database invariant, append-only audit events, and visible Recovery UI implemented; transactional outbox moves into Phase 5 |
| Phase 5 — durable workers | In progress | Transactional outbox schema/service, leased retry/dead-letter worker, durable production Plaid webhook acknowledgement, post-delivery Telegram sent markers, Telegram `update_id` dedupe/retry, resumable Gmail receipt/history pagination, scheduler overlap leases, and truthful cron failures implemented; Splitwise worker, inbound Telegram queue, provider Retry-After, and Railway worker rollout remain open |
| Phase 6 — product-domain correctness | In progress | Insights financial truth, Household verified-route freshness, receipt pagination/recovery, and Deals trust/reversibility are implemented; atomic batch receipt submission and the combined product-domain browser gate remain open |
| Phase 7 — security and operations | Not started | — |
| Phase 8 — GA validation and re-audit | Not started | — |

## Release rule

No item is considered resolved merely because its code was changed. Resolution requires a linked
implementation commit, automated tests, applicable screenshots or operational evidence, and a
passing phase exit gate.

## Phase 3 interim evidence

| Check | Result |
| --- | --- |
| Tenant uniqueness migration | Duplicate preferred-place keys, model versions, active models, and job keys work independently across workspaces |
| Migration/model parity | Clean `alembic upgrade head` followed by `alembic check` passes |
| Identity assurance | OIDC requires a provider-verified email; OAuth state is consumed with an atomic compare-and-set update |
| Invitations | Signed-out return URL is preserved; acceptance selects the joined workspace; wrong-account flow preserves the invitation and offers account switching |
| Workspace access | Owner-only shared Gmail mutations, member removal, ownership transfer, final-owner protection, and actor-attributed audit events are covered |
| Personal providers | Each member resolves their own Telegram recipient and Splitwise payer; ambiguous multi-member delivery is blocked rather than sent to the first identity |
| Bank ownership | New Plaid links are attributed to the authenticated user; legacy links require explicit confirmation; posting by a different actor is rejected |
| Settings UX | Exact Gmail, Telegram, Splitwise, Plaid owner, and verification details are visible; role-restricted controls are omitted |
| Regression gate | Backend 529 passed; focused Phase 3 backend 139 passed; frontend unit 20 passed; production build passed; Playwright 120 passed across Chromium, mobile Chromium, Firefox, and WebKit |

Phase 3 is not complete until PostgreSQL row-level security and separate request/trusted-worker
roles are implemented and proven against a real PostgreSQL test database. This remaining control is
intentionally not represented as complete by the ORM isolation tests.

## Phase 4 interim evidence

| Check | Result |
| --- | --- |
| Durable operation journal | Splitwise create/delete attempts persist action, generation, idempotency key, actor, request, state, lease, provider object, error, correlation, and completion timestamps |
| Duplicate protection | A successful create replays its stored result without a second provider call; an active lease rejects a concurrent submit |
| Ambiguous create recovery | Timeout-after-send moves the transaction to `post_ambiguous`; retry searches the deterministic Splitwise marker before any new create |
| Ambiguous delete recovery | Timeout-after-delete moves the transaction to `undo_ambiguous`; retry verifies provider absence before clearing local posted state |
| Finalized amount reconciliation | A changed Plaid amount proportionally rescales the saved paid/owed allocation and updates the existing Splitwise expense through the durable journal; ambiguous updates verify provider state before retry |
| Replacement and removal reconciliation | Plaid `pending_transaction_id` links pending and posted rows; replaced/removed rows delete any existing Splitwise expense before becoming removed |
| Draft integrity | `shared_draft` requires a non-empty participant/allocation payload in both service validation and a database check constraint; the unsafe one-click Telegram Draft action is no longer offered |
| Customer recovery | Error and ambiguous transactions remain visible in a dedicated Recovery section; confirmed failures can either retry or remove the old split and return to review |
| Auditability | Success, failure, ambiguity, recovery, actor, provider object, correlation, and idempotency identifiers emit append-only audit events |
| Migration parity | Clean `alembic upgrade head` followed by `alembic check` passes with the reconciliation fields and draft constraint |
| Focused regression gate | Backend 140 passed; Ruff passed; frontend unit 20 passed; lint had zero errors; production build passed |

Phase 4 remains in progress only because post-commit provider notifications/events must move to the
transactional outbox introduced in Phase 5. The direct financial mutation paths, Plaid replacement,
amount modification, reversal/removal handling, and valid-draft invariant are implemented and covered.

## Phase 5 interim evidence

| Check | Result |
| --- | --- |
| Transactional outbox | Tenant-scoped events persist payload, dedupe key, correlation, state, attempts, availability, lease, error, and completion metadata |
| Worker recovery | PostgreSQL claims use `FOR UPDATE SKIP LOCKED`; expired leases are reclaimable; failures receive bounded exponential retry and terminal dead-letter state |
| Plaid acknowledgement | Production webhooks commit both their webhook record and `plaid.sync_item` outbox event before returning success; local/test mode retains the immediate developer path |
| Telegram delivery truth | Production review notifications persist a unique outbox event and queued marker; `review_notification_sent_at` is written only after Telegram reports success |
| Telegram inbound idempotency | Global `update_id` uniqueness, processing leases, payload hashes, attempt state, and failed-attempt recovery prevent repeated callbacks/messages from applying twice |
| Gmail pagination | Receipt-list and incremental promotion-history page tokens persist between runs; history checkpoints do not advance until the final page succeeds |
| Scheduler truth | Gmail receipt, promotion, and weekly replenishment jobs now fail the process when any tenant fails instead of logging an error and exiting successfully |
| Scheduler overlap | Per-workspace/job database leases skip concurrent invocations and permit safe recovery after expiry |
| Worker command | `python -m app.jobs.outbox` runs continuously; `--once` supports bounded operational checks |
| Regression gate | Backend 545 passed; Ruff passed; migration/model parity passed; frontend unit 20 passed; production build passed; lint had zero errors |

Phase 5 is not complete until Splitwise mutations and inbound Telegram processing use durable workers,
provider `Retry-After` is honored, and the dedicated Railway worker service is configured and observed.

## Phase 6 interim evidence

| Check | Result |
| --- | --- |
| Currency isolation | Current and comparison rows are filtered to one explicit ISO currency; available currencies and excluded other-currency transaction counts are returned; no implicit conversion or aggregation occurs |
| Viewer-relative actual share | The service resolves the authenticated user's enabled, verified Splitwise identity and selects that participant's owed share; it no longer assumes the first payer is the viewer |
| Unknown-share safety | A missing/unverified viewer identity or absent viewer allocation excludes that shared amount and surfaces a warning instead of guessing |
| Accounting equation | `total_cents = personal_cents + shared_cents + unreviewed_cents`; classified, unreviewed, and signed refund totals are returned explicitly |
| Refund semantics | Refunds remain signed credits in totals; category percentages, donut sizing, stacked composition, and time-series scaling use safe magnitude/range calculations without changing displayed signs |
| Reporting UI | Currency selection, excluded-currency scope, exact Total reconciliation, separate Unreviewed KPI, refund disclosure, and viewer-identity guidance are visible in Insights |
| Verified route inputs | Manual and saved addresses are geocoded before routing unless coordinates are already present; generic labels cannot become origins/endpoints; resolved errands require a provider place ID or verified coordinates |
| Route input fingerprint | Each plan snapshots endpoints, available time, included errands, resolved businesses, referenced saved locations, and applicable replenishment inputs in a canonical SHA-256 fingerprint |
| Stale route prevention | Current server state is compared on every plan read; changed or legacy-unversioned plans return `is_stale`, a recovery reason, and no route URL; client-side endpoint edits invalidate the displayed route immediately |
| Route recovery UX | Today and route detail surfaces explain that recalculation is required; Start Route is unavailable while stale |
| Household partial-load resilience | Errands, staples, route, locations, predictions, receipt pages, and Gmail status load independently; a failed section no longer erases successful sections and a persistent partial-refresh warning provides Retry |
| Receipt scale and truth | Active and historical receipt buckets are independently paginated with server-side totals and Load more controls; queue badges and Today recommendations use the complete active count |
| Receipt decision safety | Every receipt exposes tracked, ignored, undecided, and total counts; confirming with undecided lines requires acknowledgement and reports a final decision summary |
| Ignored receipt recovery | Ignore exposes immediate Undo, ignored records remain discoverable in History, and a guarded restore endpoint returns them to the review queue |
| Undecided semantics | Selecting “decide later” now persists `unmatched`; it no longer silently classifies the line as rejected/non-household |
| Deals pagination truth | The API returns total, saved total, limit, offset, and `has_more`; All deals exposes the complete count and a real Load more path instead of presenting the first 100 as complete |
| Reversible deal controls | Save toggles to Unsave; merchant mute requires confirmation, exposes Undo, and has a durable unmute endpoint; dismissed deals retain their existing restore path |
| Destination trust | Offer responses expose the canonical ingestion-time destination domain, trust state, and reason; unverified links remain behind a domain-specific review interstitial |
| Regression gate | Backend 550 passed before Deals slice; focused Household/migration 35, Deals 34, and replenishment 29 passed; Ruff passed; frontend unit 21 passed; production build passed; lint had zero errors (repository-wide pre-existing warnings remain) |

Phase 6 remains in progress until atomic batch receipt submission and the complete product-domain
browser matrix pass their exit gates.

## V1 validation evidence

| Check | Result |
| --- | --- |
| Backend Ruff | Passed |
| Backend regression tests | 522 passed; 5 pre-existing deprecation warnings |
| Frontend lint | Passed with TypeScript/TSX now included; 20 pre-existing cleanup warnings surfaced for later work |
| Frontend unit tests | 15 passed |
| Frontend production build | Passed |
| Playwright visual baselines | Passed on desktop Chromium and Pixel 5 mobile Chromium |
| Responsive overflow gate | Passed at 320, 375, 390, 768, 1024, and 1440 CSS pixels |
| Automated accessibility gate | Zero critical or serious axe violations in the Expense Review baseline |
| Production npm dependency audit | Zero vulnerabilities |
| Complete npm dependency audit | Zero vulnerabilities after explicit Vitest, Vite, PostCSS, and transitive security updates |

The V1 browser gate detected a pre-existing 476px minimum-width navigation failure on phone-sized
viewports. Its first containment prevented the document from expanding beyond the viewport; V2 then
replaced that interim treatment with the dedicated mobile navigation shell recorded below.

## V2 validation evidence

| Check | Result |
| --- | --- |
| Desktop application shell | Expenses → Household → Deals; Settings and Sign out moved into the identity menu |
| Mobile application shell | Compact top identity bar and fixed three-destination bottom navigation |
| Contextual page identity | Expense Review, Spending Insights, Expense Activity, Household, Deals, and Settings use the shared page-header system |
| Navigation E2E | Desktop and mobile primary destinations, account menu, and Expense view identity passed |
| Responsive overflow gate | Passed at 320, 375, 390, 768, 1024, and 1440 CSS pixels |
| Automated accessibility gate | Zero critical or serious axe violations in the updated Expense shell |
| Playwright suite | 22 passed across desktop Chromium and Pixel 5 mobile Chromium |
| Frontend unit tests | 15 passed |
| Frontend production build | Passed |
| Frontend dependency audit | Zero vulnerabilities |

Playwright now starts a fresh, dedicated server on port 4173 for every run. This prevents a stale
developer server from producing misleading screenshots or accessibility results.

## V3 validation evidence

| Check | Result |
| --- | --- |
| Review controls | Large filter card replaced with a compact toolbar, responsive filter sheet, active-filter count, removable chips, and clear-all action |
| Transaction hierarchy | Merchant initials, merchant, amount, date, verified institution/category/channel, source, currency, one settlement state, and one recommendation |
| Decision hierarchy | Personal and Split are the two visible choices; Draft and disclosure moved to a labeled overflow menu |
| Split flow | Visible Choose people → Choose split → Review and post sequence; participant initials; allocation validation retained; final action sticky on mobile |
| Amount semantics | Spending uses neutral slate; credits/refunds use emerald; amount size no longer implies warning or error |
| Recent activity | Review page limited to five compact recent rows; full history remains available in Activity |
| Backend response contract | Transaction output now exposes Plaid institution, category, and payment channel; account/card values remain absent rather than fabricated |
| Backend regression tests | 523 passed; 5 pre-existing deprecation warnings |
| Frontend unit tests | 15 passed |
| Playwright review workflow | Desktop and mobile card flow passed, with focused transaction-card visual baselines |
| Full Playwright suite | 24 passed across desktop Chromium and Pixel 5 mobile Chromium |

## V4 validation evidence

| Check | Result |
| --- | --- |
| Reporting scope | Exact current and comparison ranges are visible; bank-pending exclusions and classified Personal/Shared scope are disclosed |
| Currency safety | Superseded by Phase 6: values are now isolated to the selected currency, with other-currency exclusions disclosed and no implicit conversion |
| KPI hierarchy | Total Spend is the dominant analytical KPI; Personal, Shared, Unreviewed, and Transactions are secondary |
| Narrative order | What changed follows the KPI tier, then trend, category composition, merchants, category trend, and shared-spend detail |
| Filter density | Date presets remain available; account/category/merchant/type/basis controls collapse under Refine view with active-count and removable chips |
| Chart integrity | Split trend focus points follow their actual series; explicit legends replace ambiguous color inference; synthetic grouped Other is not exposed as a misleading filter |
| Nonvisual access | Trend and category data have expandable semantic tables; SVG points are keyboard-focusable with meaningful accessible names |
| Mobile behavior | Desktop category chart becomes a readable period summary on phone widths; no fixed-width analytical canvas creates document overflow |
| Error recovery | Retry performs a real refetch; stale last-loaded data remains visible and explicitly marked when a refresh fails |
| Frontend unit tests | 15 passed |
| Frontend production build | Passed; existing bundle-size advisory remains non-blocking and is tracked for later optimization |
| Frontend lint | Zero errors; 20 pre-existing cleanup warnings remain |
| Insights visual baselines | Added full-page desktop Chromium and Pixel 5 mobile Chromium snapshots using deterministic spending fixtures |
| Insights accessibility | Zero critical or serious axe violations |
| Full Playwright suite | 28 passed across desktop Chromium and Pixel 5 mobile Chromium |

V4 established the visual and interaction layer. The Phase 6 evidence above now covers currency
isolation, viewer-relative share, refunds, and summary reconciliation; broader product-domain and
full GA validation gates remain open.

## V5 validation evidence

| Check | Result |
| --- | --- |
| Household identity | Errand-specific hero replaced with the household-wide “Household operations” page identity |
| Today hierarchy | Exactly one recommended next action is selected in order: receipt review → unresolved location → replenishment estimate → active route work |
| Empty state | Four large zero cards replaced by one compact, explicit all-clear state |
| Loading truth | Initial load keeps skeletons visible instead of briefly rendering false zeroes |
| Route placement | Today shows only a compact latest-route summary; the complete route builder remains under Errands |
| Route comprehension | Route summaries render a semantic Start → Stops → End sequence at desktop and as a vertical sequence on mobile |
| Receipt density | Only needs-review/failed receipts occupy the active queue; confirmed/ignored receipts remain under History |
| Mobile navigation | Household tabs remain horizontally scrollable inside their container with edge fades; no document-level overflow at 320px |
| Duplicate actions | Repeated route-detail action removed when the recommended next action already opens the route |
| Frontend unit tests | 15 passed |
| Frontend production build | Passed; existing bundle-size advisory remains non-blocking and tracked |
| Household visual baselines | Added full-page desktop Chromium and Pixel 5 mobile Chromium snapshots using deterministic errands and a concrete route |
| Household accessibility | Zero critical or serious axe violations |
| Full Playwright suite | 34 passed across desktop Chromium and Pixel 5 mobile Chromium |

V5 does not certify provider location verification, route-plan freshness, independent panel loading,
receipt pagination, or batch receipt decisions. Those product-domain and resilience invariants remain
assigned to the later backend/UX phases in the remediation plan.

## V6 validation evidence

| Check | Result |
| --- | --- |
| Desktop information architecture | Sticky category navigation on the left and one selected settings destination on the right |
| Mobile information architecture | Labeled 44px settings selector exposes all eight destinations without a horizontal tab strip |
| Settings destinations | Account; Workspace and members; Personal connections; Workspace connections; Expense preferences; Splitwise groups; Learned behavior; Privacy and account actions |
| Onboarding density | Setup checklist is hidden when the backend reports onboarding complete; incomplete setup shows explicit completed-step progress |
| Identity | Account view shows exact signed-in name/email/account ID and current workspace/role; provider rows disclose when exact connected identity is absent from the current API |
| Connection scope | Telegram is labeled Personal; Gmail, Plaid, and Splitwise are labeled Workspace; Maps and OpenAI are labeled Application |
| Role-aware presentation | Member view hides rename, invitation, and workspace-provider disconnect controls and labels them owner managed |
| Workspace invitations | Owner path to Members and the seven-day invitation control is directly navigable and browser-tested |
| Splitwise discoverability | Dedicated destination exposes group creation, friend selection, email invitation, link invitation, and participant management |
| Learned behavior | Saved friend/group preferences and fallback memories have a dedicated destination independent of Splitwise administration |
| Privacy and danger | Data-boundary copy and Leave workspace are separated into a dedicated danger treatment; absent self-service deletion is disclosed as an open launch blocker |
| Frontend production build | Passed; existing bundle-size advisory remains tracked |
| Frontend unit tests | 15 passed |
| Frontend lint | Zero errors; 19 pre-existing cleanup warnings remain |
| Settings visual baselines | Added full-page desktop Chromium and Pixel 5 mobile Chromium Account snapshots |
| Settings accessibility | Splitwise group workflow and navigation have zero critical or serious axe violations; unlabeled friend selector fixed |
| Full Playwright suite | 38 passed across desktop Chromium and Pixel 5 mobile Chromium |

V6 intentionally does not claim that backend authorization is complete. API-enforced permission
matrices, exact provider identity contracts, member removal/ownership transfer, and self-service data
deletion remain assigned to the identity/tenancy and security phases.

## V7 validation evidence

| Check | Result |
| --- | --- |
| Offer hierarchy | Concrete percentage or amount-off value is the strongest card content, followed by headline and supporting description |
| Merchant recognition | Every featured and compact deal uses the shared merchant-initial avatar |
| Primary actions | Open and Save remain visible; Dismiss, Not relevant, and Mute merchant move into a labeled overflow menu |
| Destination disclosure | Every actionable link displays its destination domain before the user leaves ExpenseOps |
| Trust presentation | Backend-trusted links receive the primary Open deal action; review-status links use a neutral Review link action and a domain-disclosing warning dialog |
| Expiry semantics | Neutral treatment is used for distant or missing expiry, amber within seven days, and rose for today, tomorrow, or expired offers |
| Empty and disconnected state | An empty feed plus disconnected Gmail resolves to one purposeful Connect Gmail state instead of stacked alerts and empty panels |
| Failure distinction | A failed feed load is shown as an explicit recoverable error and does not overwrite previously loaded deals with an empty state |
| Loading state | Card-shaped skeletons replace bare or misleading empty content during initial loading |
| Responsive behavior | Featured cards stack cleanly; deal action rows wrap; no document-level overflow at 320px |
| Deals visual baselines | Added populated desktop Chromium, Pixel 5 mobile Chromium, and explicit 320px snapshots |
| Deals accessibility | Trusted-link, review-dialog, overflow-menu, and populated-feed flow has zero critical or serious axe violations |
| Frontend unit tests | 15 passed |
| Frontend production build | Passed; existing bundle-size advisory remains tracked |
| Frontend lint | Zero errors; 19 pre-existing cleanup warnings remain |
| Full Playwright suite | 44 passed across desktop Chromium and Pixel 5 mobile Chromium after deterministic snapshot correction |

V7 does not claim complete Deals domain correctness. Save/Unsave, reversible merchant mute,
pagination and complete totals, backend trust reasons, durable sync status, and failure-safe mutations
remain assigned to Phases 2 and 6.

## V8 validation evidence

| Check | Result |
| --- | --- |
| Viewport matrix | No document-level overflow at 320, 375, 390, 768, 1024, and 1440 CSS pixels |
| Touch targets | Runtime audit verifies visible interactive controls are at least 44×44px on touch layouts across Expenses, expanded Insights filters, every Household tab, Deals terms/dialog, Settings, and Splitwise group management |
| Defects found and fixed | Expense tabs, shared small buttons, date presets, Insight selects/toggles/split bars, Household disclosure/icon controls, Splitwise group controls, and transaction split controls were enlarged; WebKit-native select collapse was corrected |
| Keyboard navigation | A visible-on-focus skip link targets the main content; the unverified-link dialog traps focus and returns it to its trigger after close/Escape |
| Reduced motion | Application animation and transition durations collapse under `prefers-reduced-motion: reduce` and are browser-tested |
| Zoom/reflow | Primary Expenses → Household → Deals journey remains usable without horizontal overflow at a 640px layout viewport, equivalent to 200% zoom on a 1280px display |
| Mobile fixed navigation | End-of-page content clears the fixed bottom navigation and remains reachable |
| Edge-state coverage | Disconnected/empty Deals, loaded/error preservation, all-clear Household, available route, expanded split workflow, every deal expiry urgency, long merchant text, six-figure amount, and refund display are covered |
| Chart access | Insights charts retain keyboard-focusable data points and expandable semantic data tables |
| Automated accessibility | Axe reports zero critical or serious WCAG A/AA violations in the covered Expense, Insights, Household, Settings, Splitwise, and Deals journeys |
| Cross-browser projects | Desktop Chromium, Pixel 5 Chromium, Desktop Firefox, and Desktop WebKit are configured in Playwright |
| Visual regression | Browser-specific baselines pass in Chromium, mobile Chromium, Firefox, and WebKit |
| Final browser gate | 116 passed; `test-results/.last-run.json` reports `passed` with no failed tests |
| Frontend unit tests | 15 passed |
| Frontend production build | Passed; existing bundle-size advisory remains tracked |
| Frontend lint | Zero errors; 19 pre-existing warnings remain and are not represented as a clean-warning gate |

The automated V8 gate is complete. Manual VoiceOver/NVDA journeys, physical-device validation,
and formal human contrast review remain release evidence to collect in Phase 8; they are not
represented as completed by Axe or browser emulation.

## Phase 2 validation evidence

| Check | Result |
| --- | --- |
| API response handling | Successful `204`, `205`, and empty `2xx` responses resolve without JSON parse failures; malformed successful responses fail explicitly |
| Structured errors | HTTP status, customer-safe detail, retryability, error kind, and response/request correlation ID are retained in one `ApiError` contract |
| Failure distinctions | Offline, network, expired session, slow request, provider outage, malformed response, and stale last-loaded data have distinct application states |
| Correlation transport | The browser sends `X-Request-ID`; FastAPI CORS accepts it; the server response ID is shown as a support ID on contextual failures |
| Crash containment | A top-level React error boundary replaces a blank screen with a safe reload path and does not claim data loss |
| Financial action scope | Personal, Draft, Split, custom preview/post, friend/group lookup, group-member loading, and Undo are guarded per transaction instead of freezing the review queue |
| Financial retry safety | Failed financial actions remain on their transaction with the provider reason; no automatic Retry control was introduced before Phase 4 idempotency |
| Settings and onboarding actions | Workspace create/switch/rename/leave, invitations, integration connect/disconnect, and clipboard operations expose progress, terminal feedback, and duplicate guards |
| Splitwise administration | Group creation, participant add/remove, email invite, directory refresh, member loading, and link copying expose scoped progress and persistent outcomes |
| Deals actions | Save, dismiss, not-relevant, mute, restore, sync, and clipboard failures no longer disappear silently; last-loaded offers remain visible on refresh failure |
| Household empty-delete handling | Household `DELETE` calls now use the shared empty-response-safe API contract |
| API unit tests | 20 passed across five files, including new empty/malformed/offline/session/provider/correlation cases |
| Browser failure isolation | A delayed failed transaction action leaves another transaction operable, preserves the failed row, and displays its support ID |
| Cross-browser gate | 120 passed across desktop Chromium, mobile Chromium, Firefox, and WebKit |
| Frontend production build | Passed; existing bundle-size advisory remains tracked |
| Frontend lint | Zero errors; existing cleanup warnings remain tracked separately |

Phase 2 does not make provider mutations intrinsically safe to retry. Durable financial
idempotency and recovery remain Phase 4 work; worker-level retry/lease behavior remains Phase 5.

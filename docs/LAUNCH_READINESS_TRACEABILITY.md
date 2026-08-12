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
| V6 — Settings information architecture | Not started | — |
| V7 — Deals hierarchy | Not started | — |
| V8 — visual/accessibility validation | Not started | — |
| Phase 2 — UX action integrity | Not started | — |
| Phase 3 — identity and tenancy | Not started | — |
| Phase 4 — financial correctness | Not started | — |
| Phase 5 — durable workers | Not started | — |
| Phase 6 — product-domain correctness | Not started | — |
| Phase 7 — security and operations | Not started | — |
| Phase 8 — GA validation and re-audit | Not started | — |

## Release rule

No item is considered resolved merely because its code was changed. Resolution requires a linked
implementation commit, automated tests, applicable screenshots or operational evidence, and a
passing phase exit gate.

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
| Currency safety | UI explicitly says values are displayed as USD and that no currency conversion is applied; multi-currency correctness remains assigned to Phase 6 |
| KPI hierarchy | Total Spend is the dominant analytical KPI; Personal, Shared, Transactions, and Average are secondary |
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

V4 changes only presentation, interaction, and reporting-scope disclosure. It does not certify the
underlying accounting model. Insights remains non-releasable for general availability until the
Phase 6 currency, viewer-share, refund, deduplication, and reconciliation invariants pass.

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

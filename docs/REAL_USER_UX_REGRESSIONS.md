# Real-user UX regressions

This file records production-discovered UX failures that must remain covered by permanent tests.

## Receipt photo required manual PDF conversion

Status: resolved in the Day 15 local branch; not yet deployed.

Observed behavior:

- A user photographed a readable physical receipt and sent it through Telegram.
- ExpenseOps returned a review with an unknown merchant and zero line items, making PDF conversion appear necessary.

Root cause:

- Image bytes did reach the multimodal parser, but the combined photo-selection, image-detail/model, validation, orientation, output-quality, and retry boundaries could accept a valid-empty result as ready. Gmail image attachments and a web camera/upload path were also absent.

Permanent acceptance:

- select the highest-resolution useful Telegram photo variant;
- send immediate acknowledgment;
- download actual JPEG bytes;
- validate and normalize them through the common `ReceiptArtifact` path;
- send direct image input to the receipt model;
- never present an empty/non-receipt extraction as ready;
- keep useful partial lines for review;
- require no user-side PDF conversion;
- create no external financial write.

Evidence on 2026-08-17:

- deterministic Telegram regression passed with actual JPEG bytes and the largest pixel-area variant;
- 20-case deterministic media/quality benchmark passed;
- final synthetic OpenAI image smoke passed 4/4 (normal, blurred, rotated, restaurant), one request each and zero retries;
- Day 15 Playwright passed across Chromium, Firefox, WebKit, and 320px mobile;
- the full regression result is recorded in `IMAGE_FIRST_RECEIPT_INGESTION.md` and the Day 15 completion report.

The issue is marked resolved in code, not in production. It must be reopened if the deployed environment keeps the old receipt-model override or the production photo regression fails after rollout.

## Ordinary receipt learning required manual setup and review

Status: resolved in the Day 16 local branch; not yet deployed or enabled.

Observed burden:

- a new user could have to create a staple before an ordinary receipt line contributed learning;
- a receipt in review could appear to block categorization and acquisition learning;
- cadence could appear to require a user-entered guess;
- receipt lines and obvious transactions could remain uncategorized;
- a uniquely supported receipt/Plaid pair could remain visibly disconnected.

Day 16 code behavior:

- every meaningful receipt line receives a controlled category or Other / Uncertain;
- every eligible transaction receives a controlled category or Other / Uncertain;
- high-confidence, verified replenishable lines can create a Learning HouseholdItem and acquisition
  without a prior manual Add Item or receipt confirmation;
- category/model priors are explicitly estimated, and observed history replaces them; the user is
  not asked to enter cadence;
- receipt review is retrospective and does not block safe reversible bookkeeping;
- a same-workspace, currency-compatible, amount/date/merchant-supported unique Plaid candidate
  auto-links, while near ties remain ambiguous;
- users can inspect what ExpenseOps categorized and correct receipt lines or transactions later;
- concept rename/merge is owner controlled, while HouseholdItem/history merge remains a separate
  explicitly unsupported operation.

Permanent acceptance evidence on 2026-08-17:

- the fixed Day 16 benchmark categorized 18/18 receipt lines and 7/7 transactions in a realistic
  zero-staple synthetic week;
- it created ten Learning HouseholdItems and ten acquisitions with zero false staples;
- category priors covered ten first purchases and a second Eggs acquisition replaced the prior with
  an observed 10-day cadence at zero error in that exact synthetic case;
- the Trader Joe's unique match and the nine-case generic reconciliation matrix passed with 100%
  outcome accuracy, 100% auto-match precision/recall, and zero false auto-matches;
- the manual-work proxy fell from 49 required setup/review actions to zero required actions, with
  two honest Other / Uncertain rows left for optional review;
- the exact 30-scenario chaos manifest resolves to executable assertions; the focused benchmark and
  traceability tests are permanent test files.

These numbers are fixed-corpus regression evidence, not production accuracy or correction-rate
claims. The issue is resolved in code only. Reopen it if a staged deployment requires confirmation
for ordinary internal learning, creates a false staple from dining/one-time activity, leaves a
meaningful line null, asks the user to invent cadence, or fails the generic unique-match regression.

## Day 16 release caveats visible to users

The following are intentional boundaries, not hidden setup requirements:

- Other / Uncertain is a completed safe classification and may be corrected later.
- An ambiguous receipt/Plaid match remains unlinked rather than guessed.
- Ignoring a receipt reverses only unconfirmed autonomous learning; user-confirmed history remains.
- Concept merge does not combine HouseholdItems, cadence, acquisitions, or purchase history.
- The global rollout flag defaults off and each workspace must opt in, so production behavior does
  not change merely because the code exists.

## Natural-language Agent answers did not match the requested objective

Status: resolved in Day 17 code and the final frozen-tree live matrix; not deployed.

Observed behavior:

- top category and top-five-merchant questions could return a generic spending total;
- “this month” could depend on model-supplied dates rather than calendar-month semantics;
- a typical restaurant check could be unsupported or label an average ambiguously;
- restaurant increases repeated current/prior totals without explaining count, average, or
  merchant changes;
- recent staple candidates could route to the unrelated list of items predicted due;
- normal grammar mistakes could turn a supported week comparison into an unsupported response;
- coffee, uncertainty, typo-heavy, and contextual follow-up questions were provider-dependent;
- valid replenishment and learning rows became unreadable inside the narrow desktop companion.

Root cause:

- the model could retrieve valid canonical evidence, but the exact analytical objective did not
  survive through deterministic composition;
- most temporal language and user-local today were not owned by one bounded resolver, while
  Insights presets still used the browser clock;
- tool exposure was narrow for only five of the thirteen exact prompt variants;
- classification activity had no recent local-date staple-candidate/alias projection;
- lifestyle output did not expose canonical merchant deltas;
- the renderer silently sliced ranked rows and forced content, badge, and action into one narrow
  flex row with character-level wrapping.

Permanent acceptance evidence on 2026-08-18:

- `scripts/benchmark_agent_day17.py` keeps all 13 exact variants separately: the 12 requested
  scenarios plus both required week-comparison phrasings;
- all 13/13 exact rows pass objective, dates, one-tool exposure/selection, arguments, direct answer,
  structured block, one canonical call, unsupported-response, and read-only assertions;
- nine close paraphrases and the exact four-turn dining chain bring routing to 26/26 with zero
  wrong-domain routes, zero unnecessary clarifications, and zero unsupported deterministic
  responses;
- this month (Aug 1–17), last month (Jul 1–31), and last 30 days (Jul 19–Aug 17) are distinct for
  the pinned Phoenix instant;
- the Agent and every non-custom Insights preset now share the backend temporal service and exact
  authenticated workspace/user timezone preference, with tested UTC fallback and no preference
  write; preset resolution fails visibly instead of reverting to browser-local dates;
- top category names Food & Dining with amount/share; top merchants returns exactly five ordered
  rows; restaurant average says “average”; restaurant change includes total, count, average, and
  measured merchant contributors;
- staple candidates use classification activity, explicitly say they are not predicted due, and
  render “Household item created” or “No household item created” with learning state/confidence;
  learning and uncertainty answers use durable classification-ledger facts;
- inert hostile merchant/category strings cannot change the objective, date range, allowlist, or
  canonical total;
- current registered read metadata is 15,022 bytes across the unchanged nine read tools; total
  metadata is 20,199 bytes across the unchanged 13 read/action tools. Both grew 2,972 bytes from
  the Day 16 checkpoint, while a supported exact turn exposes one read tool averaging 2,292.5
  bytes, an 84.7% reduction versus exposing the complete current read surface;
- local route-plus-canonical-composition latency measured 0.0593 ms median / 0.0720 ms p95 over
  250 repetitions after 25 warmups, excluding provider, database, and browser work;
- the focused Day 17 Playwright gate passed the 1024px/approximately-22-rem companion and
  320/375/390px mobile widths (4 applicable passes, 12 intentional project skips), including long
  labels, six ranked merchants, visible actions, no horizontal overflow, and no serious/critical
  WCAG A/AA axe violations;
- the deterministic benchmark makes no model call, uses no production data, and reports provider
  tokens/cost as zero rather than inventing a live measurement;
- the final frozen-tree opt-in synthetic, read-only paid matrix passed all 13 exact variants, a
  separate rolling-30-day calendar control, and the exact four-turn follow-up: 18 read calls, zero
  failures, 36 provider requests, 74,399 input / 1,654 output tokens, and $0.032409 estimated cost
  using the dated official `gpt-4.1-mini` price snapshot. Median end-to-end latency was 2,908.5 ms;
  p95/max was 17,077 ms and aggregate turn latency was 78,031 ms. The settled Lifestyle v1.3 /
  transaction-search v1.2 registry used only synthetic data and created no write proposal or
  financial operation.

Detailed architecture, per-case before/after results, measurement boundaries, platform decisions,
and validation commands are in `AGENT_INTELLIGENCE_UX_CLEANUP.md`.

The answer and narrow-card regressions are marked resolved because their exact permanent tests pass.
Reopen them if `frontend/e2e/agent-day17-frontend.spec.ts` fails at the 22-rem companion or
320/375/390px mobile widths, if a ranked result is silently truncated, or if a protocol error
exposes internal details. This is a code-local status only; it says nothing about the currently
deployed production version.

## Review decisions were hidden and disconnected across channels

Status: resolved in Day 18 code; not deployed.

Observed behavior:

- the web Agent did not proactively surface Plaid transactions requiring review;
- a supported terse split prompt could falsely return a generic read-only refusal;
- the user had to hunt for the right transaction and establish context manually;
- Telegram and web displayed the same decision without durable shared seen/resolved identity;
- Gmail itemized-split functionality was available only after manually finding receipt history;
- Split versus Customize and pending-posting behavior were difficult to understand.

Root cause:

- transaction status was the correct domain authority, but no user-owned presentation projection
  could hold one public identity plus unread/seen/stale state;
- web data loaded from independent transaction queries and Agent conversation context;
- the action recognizer did not include the normal terse phrases, so the generic safety fallback
  ran before the proposal path;
- receipt parsing/classification/reconciliation did not publish itemized readiness into an
  actionable discovery surface.

Day 18 code behavior:

- Plaid upsert synchronously ensures one tenant/user/source review projection in the same commit;
- web Review, its badge, and the proactive Agent region read one strict API;
- Telegram callbacks revalidate the same authoritative transaction and become inert after web or
  Agent resolution;
- pending replacements retain the same public review identity and final amount;
- transaction cards show Personal, recommended/neutral Split, and explicit Customize controls;
- pending cards allow preparation but disable provider posting until the final charge;
- all six required split phrases and both Personal phrases route to only the existing proposal tool;
- `me` is the authenticated payer, not a friend lookup;
- multiple active purchases produce a clarification without a model call;
- Gmail dining receipts become itemized-ready only after exact parse, arithmetic, classification,
  and Plaid-match checks; ambiguity remains an explicit match-needed task;
- every consequential split remains an immutable proposal with a separate model-free confirmation.

Permanent acceptance evidence on 2026-08-18:

- the five-transaction benchmark creates exactly two actionable tasks from two review-required
  transactions, requires zero searches/prompts, resolves one through the web service and one through
  the Telegram service boundary, ends at zero, and makes zero provider calls;
- the benchmark's explicitly local in-process SQLite boundary measured 11.948 ms from projection to
  page read and 6.293 ms for the two resolutions; browser freshness is separately bounded by the
  visible-page 10-second poll plus at most two seconds jitter and API latency;
- the synthetic Gmail regression uses a hostile email subject/line as inert data, auto-matches the
  exact dining receipt, creates one itemized-ready task, and posts nothing;
- strict backend ownership tests cover foreign workspace and a different user in the same workspace;
- strict frontend parsing rejects extra keys, invalid UUID/count/state combinations, and missing or
  conflicting source summaries;
- the Day 18 browser spec covers recommendation prefill, itemized readiness, idempotent seen state,
  proactive Agent rendering, pending-post blocking, Axe, and no overflow at 320/375/390/1024px;
- migration/drift/bootstrap/readiness tests cover the new table, linear head, grants, indexes, and
  exact FORCE RLS policy.

The detailed architecture, failure traceability, metrics boundary, and limitations are in
`UNIFIED_REVIEW_INBOX.md`. Reopen this regression if a source event can create duplicate active
tasks, the Agent needs a prompt to reveal current tasks, a stale Telegram callback mutates state,
pending charges can post by default, or Gmail readiness again requires receipt-history discovery.

## Agent transaction review left the Agent panel and could not complete a decision

Status: resolved in Day 19 code; not deployed.

Observed behavior:

- clicking a transaction the Agent surfaced navigated the user away to the web Expense Review UI,
  closing the Agent's own context;
- the Agent had no way to walk a user through Personal/Split for more than one transaction without a
  fresh prompt for each item;
- `me`/`myself` could, in principle, remain in a split's participant list alongside the payer;
- a model-supplied Splitwise participant name had no check that it was actually present in the
  user's current message, unlike the equivalent itemized-receipt tool;
- a transaction that was split and then undone could be permanently unable to be re-split through
  the Agent, because the operation lookup used to detect "already handled" ignored the split's
  generation and matched a stale, already-undone operation row.

Root cause:

- `TransactionListCard`'s only click behavior was `onNavigate`; no in-panel selection/session state
  existed anywhere in `app/agent/`;
- `propose_post_splitwise_expense` already stripped the payer from resolved participants, but had no
  `_validate_itemized_user_provenance`-equivalent check against the user's literal message;
- `AgentActionExecutor._splitwise_operation` filtered `FinancialOperation` rows by
  `action == "splitwise_create"` only, taking the highest-generation row as a tie-break rather than
  filtering by the transaction's current generation.

Day 19 code behavior:

- a new `AgentReviewSession` (one active session per workspace/owner/conversation) tracks an ordered,
  frozen candidate queue and current position, resumable across refresh/reopen;
- clicking a transaction in the Agent now starts/resumes that session and stays in the Agent panel —
  no `onNavigate` call on that path;
- Personal and recommended-Split are one deterministic click each, routed through the existing
  action-proposal/confirmation pipeline with no OpenAI call;
- a candidate resolved elsewhere (web or Telegram) while the session is open is detected via
  fingerprint/state revalidation and skipped as stale, not acted on twice;
- `_normalize_post_splitwise` now requires every named participant/group to be grounded in the
  current message (or, for a click, in the click's own synthetic grounding text) before building a
  proposal;
- `_splitwise_operation`'s lookup is now scoped to the transaction's current `splitwise_generation`,
  so a completed undo can never be mistaken for a live conflicting split.

Permanent acceptance evidence on 2026-08-19:

- `test_day19_split_then_undo_then_split_again_creates_new_generation` (split → undo → split again
  succeeds and creates a distinct generation-1 operation) in `tests/test_agent_runtime.py`;
- `test_day19_post_splitwise_rejects_participant_not_in_current_message` (a model-invented
  participant matching a real friend is rejected with no proposal created);
- `tests/test_agent_review_session.py` (9 tests): tiered candidate ordering, session resume,
  empty-queue completion, Personal and Split proposal-to-advance flows, skip, stop, external
  resolution marking a candidate stale, and cross-workspace session access denial;
- full backend suite (1853 tests) and frontend build/lint/typecheck pass unchanged, confirming web
  and Telegram behavior is untouched.

Full design detail, Build-vs-Integrate decisions, and limitations (itemized-in-session assignment,
arbitrary Customize, structured-memory learning hook) are in
`AGENT_NATIVE_TRANSACTION_REVIEW.md`. Reopen this regression if a review-session click ever
navigates away by default, a stale candidate is acted on after external resolution, an ungrounded
participant reaches a proposal, or a post-undo re-split is blocked again.

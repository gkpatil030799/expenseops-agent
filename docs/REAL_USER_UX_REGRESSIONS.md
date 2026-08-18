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

# Zero-Setup Receipt Learning (Day 9)

## Outcome and build-vs-integrate decision

Day 9 removes the new-user requirement to create every staple and guess a cadence before a
receipt can teach ExpenseOps. It integrates with the existing receipt parser, receipt review,
alias matcher, acquisition ledger, replenishment predictions, Day 8 durable Agent proposals,
Telegram, and Gmail. It does not introduce another model pass, queue, model, dependency, or
Lifestyle/Dining feature.

The one existing OpenAI Responses request now returns a closed line classification, confidence,
and optional canonical household concept alongside the existing receipt fields. Those values are
evidence only. A code-owned resolver decides whether a line may be proposed, and confirmation
executes the existing domain batch without another model call.

## Classification and safe canonicalization

The only classifications are:

- `replenishable_household`
- `perishable_grocery`
- `routine_consumption`
- `dining_or_experience`
- `one_time_purchase`
- `non_product_line`
- `uncertain`

Known product rules are deliberately small and separate concepts that should not be merged:
almond milk, oat milk, dairy milk, paper towels, toilet paper, laundry detergent, dishwasher
tablets, and dish soap. Tax, discount, tip, restaurant/experience, routine coffee, apparel, and
hostile instruction-like text cannot become automatic candidates. Unknown model suggestions are
never treated as authority, and uncertainty stays visible for review.

An exact, confirmed workspace alias can auto-match. A canonical match at another merchant is only
a suggestion until confirmed. Similarity needs a configured threshold and an unambiguous margin;
conflicting aliases fail closed. A correction voids the wrong evidence and learns only the newly
confirmed alias. Every item and alias lookup includes the current workspace.

## Candidate review and cold start

High-confidence replenishable or perishable lines with no safe match default to a new tracked-item
candidate. Known aliases default to their existing item. Possible matches stay explicit and easy to
correct. Routine, dining, one-time, and non-product lines default to Do not track. Uncertain lines
stay undecided. One receipt is submitted as one atomic, timestamp-guarded batch; no line is silently
confirmed, and a database error rolls back the entire batch.

New items start with `cadence_source=learning` and `cadence_days=null`. They are not shown as due
after one purchase. Two confirmed purchases establish an observed median interval; three or more
use the recent robust adaptive interval. A user-configured cadence remains authoritative. Undo and
correction rebuild the learning state rather than leaving a fabricated default.

Quantities continue through the existing quantity-aware acquisition path. Known units are used
only above the existing confidence boundary. Ambiguous packages remain unknown; Day 9 does not
invent package consumption or convert arbitrary units.

## Agent workflow and execution boundary

On a selected receipt, `Learn the useful household items from this receipt` can prepare the
confirmation-required `propose_receipt_learning_batch` action. The server owns classification,
canonical names, defaults, workspace resolution, and a complete preview of at most 20 receipt
lines. Larger receipts direct the user to the ordinary receipt-review screen instead of presenting
an incomplete Agent proposal.

The frozen proposal records the receipt ID, status, update timestamp, and every decision. No
mutation happens during proposal generation. Confirmation reacquires and locks the workspace
receipt, rechecks the write flag/status/timestamp, and calls the existing receipt batch,
acquisition, and alias services. Confirmation is model-free. Stale, expired, cancelled, ignored,
cross-tenant, duplicate, concurrent, or failed proposals cannot produce partial learning. A lost
success response reconciles to the previously completed proposal without a second mutation.

## Source compatibility, identity, and privacy

Telegram and Gmail use the same ingestion service. Exact source IDs and content hashes retain their
existing idempotency behavior. Reconciled Telegram/Gmail/Plaid representations use the existing
logical-purchase key, so one tracked item receives one acquisition for one purchase.

Receipt text is untrusted data. Neither receipt instructions nor model text can confirm an Agent
proposal. General telemetry contains aggregate line/candidate/correction counts and timings, never
product names, receipt contents, provider payloads, user identity, or workspace identity. Live
tests use synthetic receipts and isolated local data; they do not mutate production.

## Measurements

The deterministic four-receipt cold-start benchmark runs the production classifier, matcher,
batch confirmation, alias learning, and acquisition services against an isolated database:

| Metric | Observation |
|---|---:|
| Pre-Day-9 line decisions | 5 |
| Day-9 line decisions | 1 |
| Decisions avoided | 4 (80%) |
| Manual cadence entries | 4 -> 0 |
| Explicit batch confirmations | 2 |
| Subsequent automatic alias hits | 3 |
| Cross-merchant suggestions still requiring review | 1 |
| Tracked items / confirmed acquisitions | 4 / 8 |
| Parser invocations | 4 (one per receipt) |
| Local candidate-generation median | 5.684 ms |
| Local batch-confirmation median | 11.656 ms |

Local service timings are development-machine observations, not production SLOs. Reproduce them
with:

```bash
.venv/bin/python scripts/benchmark_receipt_learning_day9.py --format markdown
```

The final bounded live observation used two synthetic receipts with the configured
`gpt-4.1-mini`: two Requests API calls, nine extracted lines, 15,660 ms combined parser latency,
1,199 input tokens, and 812 output tokens. At the official current $0.40/M input and $1.60/M output
rates, that observation is approximately $0.001779 total, or $0.000889 per receipt. This is an
individual observation, not an average or SLO. The provider-request count remains one per receipt;
Day 9 adds strict schema/prompt fields but no second classification request, and confirmation uses
zero model calls. The compact receipt item schema projection grows from 1,216 to 1,574 bytes
(+358 bytes, +29.44%); the live token observation above includes that final schema/prompt rather
than estimating a token conversion from bytes.

## Validation record

- Backend: 1,266 passed, 15 environment-gated skips; the complete Days 1-8, receipt,
  replenishment, Telegram/Gmail, Agent-action, tenancy, security, and migration suite is included.
- Live synthetic receipt smoke: 1 passed in 15.99 seconds; no production data or mutation.
- Frontend unit: 100 passed; TypeScript/production build passed; ESLint had zero errors and 20
  pre-existing warnings.
- Playwright: 274 passed and 138 intentional project/viewport skips across Chromium, mobile
  Chromium, Firefox, and WebKit, including 320/375/390 widths and accessibility checks.
- Fresh migration and incremental `0029 -> 0030`: single head and no Alembic drift.
- Ruff, touched-file formatting, and `git diff --check`: clean.

## Schema and operations

Migration `20260817_0030`:

- makes learned-item cadence nullable and records `configured|learning|observed|adaptive` source;
- stores the closed classification, confidence, and canonical-name evidence on receipt lines;
- adds state and confidence checks plus a review index; and
- updates the production recovery inventory for revision `0030`.

Fresh installation and incremental `0029 -> 0030` both reach the single Alembic head with no drift.
The downgrade refuses to fabricate a cadence when learning rows still contain null cadence. No
package, lockfile, secret, service, cron, or Railway topology changes are required.

## Limitations and next boundary

- Canonicalization is a bounded policy, not a universal retail taxonomy. Novel products and close
  concepts can remain uncertain or suggested.
- Cross-merchant canonical matches require confirmation before teaching an alias.
- A complete Agent preview is bounded to 20 lines; larger receipts use direct receipt review.
- Two purchases provide an observed interval, not a confident consumption model. Quantity/package
  semantics remain intentionally conservative.
- The UI keeps the existing receipt review screen; it is not a general-purpose catalog editor.
- Live latency and token results are two synthetic calls and do not establish percentile targets.
- Lifestyle/Dining intelligence, coffee habits, restaurant analytics, purchase actions, proactive
  notifications, vector memory, and multi-agent orchestration remain out of scope.

The recommended next product phase is **Lifestyle & Dining Intelligence**, but it should begin only
under a separate reviewed brief. Day 9 must not silently reinterpret excluded dining/routine lines
as household inventory.

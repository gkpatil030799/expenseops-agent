# Itemized Restaurant Receipt Splitting (Day 11)

## Outcome and build-vs-integrate decision

Day 11 adds one web-Agent workflow for assigning priced restaurant receipt lines to people and
preparing an exact Splitwise proposal. It integrates the existing receipt parser and Day 9 line
classification, selected-receipt context, Splitwise identity/group resolution, share calculator,
Day 8 `AgentActionProposal`, shared confirmation renderer, `FinancialOperation`, outbox, and
provider reconciliation. It does not add a second split engine, provider client, confirmation
framework, queue, database table, package, or model call after confirmation.

The current OpenAI Agents SDK is retained for bounded semantic interpretation of item and person
phrases. ExpenseOps code remains the authority for receipt/entity resolution, arithmetic,
rounding, proposal persistence, confirmation, and execution. Adopting another workflow or HITL
platform would duplicate mature durable state and weaken the existing model-free execution
boundary, so a thin proposal tool over the current platform is the smaller and safer choice.

## Controlled workflow

```text
selected tenant-owned restaurant receipt
  -> model supplies only latest-turn line/person phrases and explicit tax/tip methods
  -> server resolves exact receipt lines, payer, friends, and optional group
  -> server calculates every item, tax, tip, and final share in integer cents
  -> immutable proposal + code-owned preview
  -> explicit proposal-version confirmation
  -> AgentActionExecutor (no model)
  -> existing Splitwise FinancialOperation/outbox/reconciliation path
```

`propose_itemized_receipt_split` is an `EXTERNAL_ACTION`, is unavailable unless the normal Agent
write-action flag is enabled, and can only produce an `awaiting_confirmation` proposal. The
browser receives the preview and proposal identity/version, never the frozen provider payload or
provider user IDs. Confirmation sends only `{"proposal_version": N}`.

## Assignment and math authority

The strict input supports these semantic assignment states:

- `PERSON`: exactly one explicitly named person;
- `SHARED_AMONG`: at least two explicitly named people;
- `ALL_PARTICIPANTS`: all people explicitly present in the bounded proposal;
- `UNASSIGNED`: clarification only; it cannot produce an executable proposal.

Every positive item line must be assigned exactly once. More than one matching receipt line,
missing or nonpositive prices, unreconciled line/subtotal totals, missing transaction linkage,
currency/amount disagreement, non-restaurant evidence, unresolved people/groups, and participants
without an assigned item all fail before proposal creation.

Shared items use the existing stable equal-cent allocator. Tax and tip must each be explicitly
`equal` or `proportional_to_item_subtotal`; positive overhead cannot remain unassigned. The
proportional allocator uses integer cents, floor allocation, and stable largest-remainder
distribution. Code validates all of these invariants:

- assigned line amounts sum to the receipt subtotal;
- participant item subtotals reproduce the frozen line assignments;
- participant tax shares sum to receipt tax;
- participant tip shares sum to receipt tip;
- each participant final equals item subtotal plus tax plus tip;
- final owed shares and payer paid shares each reconcile to the exact receipt/transaction total.

The model never calculates or supplies cents, shares, provider IDs, or a provider payload.

## Frozen proposal and execution safety

The proposal freezes the receipt ID/status/update time/content hash, linked transaction
ID/status/update time, resolved Splitwise integration/payer/group/membership, line assignments,
allocation methods, exact participant totals, and canonical provider payload. The receipt row is
locked while checking for another active logical itemized split, preventing two concurrently
prepared logical splits in PostgreSQL. The existing transaction and financial-operation boundary
prevents the same linked purchase from being posted twice even across retries or another channel.

Immediately before provider work, the executor rechecks the write flag, proposal ownership and
version, frozen receipt and every line, linked transaction, integration identity, destination
members, and payload. A stale receipt or transaction fails with zero provider calls. A correlated
existing financial operation is reconciled before any retry. A possible timeout-after-send becomes
`ambiguous`/`needs_reconciliation` and is never blindly retried. Repeated confirmations return the
same terminal proposal and confirmation performs no SDK/model call.

## Tenancy, injection, and channel boundaries

Receipt and transaction selection is workspace-scoped; proposal/conversation ownership is also
user-scoped. A cross-workspace receipt fails before the model or Splitwise provider is invoked.
Only line and participant phrases present in the latest user turn may enter semantic resolution;
receipt text, model output, or older conversation text cannot silently add a person or item. All
receipt/provider content remains inert display data in a code-owned preview.

The established simple Splitwise web and Telegram flows are unchanged. Telegram does not receive
the rich itemized interaction in Day 11. No real provider write is permitted in automated tests;
provider execution uses the existing deterministic fake and ambiguity/reconciliation seams.

## UX and limitations

The existing `action_confirmation` card renders each person’s assigned items, item subtotal,
tax, tip, final owed amount, destination, and effect. Confirm/cancel remain explicit 44-pixel
controls with single-flight protection on desktop and mobile.

Current bounded limitations:

- at most 20 priced receipt lines and nine participants;
- one linked transaction and one currency, with exact receipt/transaction total agreement;
- equal sharing for a shared item; custom per-person item percentages are not supported;
- tax/tip support only equal or proportional-to-item-subtotal allocation;
- discounts or incomplete receipt arithmetic must be corrected in receipt review first;
- group-wide assignment still requires explicit people in the current request;
- web Agent is the primary itemized surface; Telegram retains the established simple split flow;
- no automated test performs a real Splitwise production write.

## Validation gate

Day 11's stop/go gate includes exact two- and three-person math/rounding, ambiguous and missing
lines, latest-turn phrase provenance, active/stale/cross-tenant proposals, duplicate confirmation,
ambiguous provider outcomes, unchanged simple Splitwise/Telegram behavior, backend regression,
frontend unit/type/lint/build, desktop/mobile multi-browser confirmation UX, accessibility,
migrations, dependency audits, and diff/static checks. Exact settled totals are recorded in the
final Days 9–13 report after the full gate completes.

Settled Day 11 evidence:

- backend: 1,299 passed, 17 expected opt-in/environment skips;
- focused Day 11 runtime/security/math: 11 passed;
- synthetic OpenAI proposal smoke: 1 passed in 5.54 seconds, with one proposed tool call,
  4,440 ms run latency, 1,867 input tokens, 165 output tokens, exact USD 25.20/USD 64.80
  shares, zero confirmation, zero `FinancialOperation`, and zero Splitwise create calls;
- frontend unit: 102 passed; TypeScript/production build passed; ESLint had zero errors and 20
  pre-existing warnings;
- full Playwright: 282 passed and 154 configured project/viewport skips across Chromium, mobile
  Chromium, Firefox, and WebKit, including the visual/accessibility suite;
- fresh SQLite migration to the single `20260817_0030` head and `alembic check`: clean;
- Python lockfile audit: no known vulnerabilities; production npm audit: zero vulnerabilities;
- repository Ruff, touched-file format, and `git diff --check`: clean.

The live cost field was correctly unavailable because the local configured pricing snapshot did
not match. At the current official GPT-4.1 mini list rates of $0.40/M input and $1.60/M output,
the single proposal observation is approximately $0.001011. It is one synthetic observation, not
an average, percentile, or SLO, and it did not contact the Splitwise create endpoint.

# Controlled Agent actions

ExpenseOps Day 8 adds two narrowly scoped, confirmation-gated Agent actions:

- `mark_transaction_personal` — a local `WRITE` that reuses the canonical transaction service.
- `post_splitwise_expense` — an `EXTERNAL_ACTION` that reuses canonical equal-split calculation and the existing financial-operation/outbox/Splitwise path.

The feature is off by default. It is available only when the existing Agent read flags and `AGENT_WRITE_ACTIONS_ENABLED=true` are all enabled. Purchasing, proactive work, custom splits, and every other write remain unavailable.

## Build-versus-integrate assessment

ExpenseOps already had the durable foundations that matter for consequential work:

- tenant- and owner-scoped `AgentActionProposal` records;
- immutable normalized parameters, hashes, action fingerprints, expiry, versions, lifecycle states, and idempotency keys;
- effect/capability policy in `AgentToolRegistry`;
- the canonical transaction state machine and activity/audit events;
- canonical entity resolution, payer verification, equal-share calculation, and Splitwise payload construction;
- `FinancialOperation`, transactional outbox delivery, recovery markers, ambiguous-provider handling, and reconciliation;
- mature manual Review and Telegram flows that already use those domain services.

The missing layer was small but important: concrete proposal-only action tools, a no-model confirmation executor, confirmation/cancellation endpoints, and one shared confirmation renderer. Day 8 builds only that layer. It does not add a second financial engine, action queue, workflow framework, schema, or dependency.

### OpenAI Agents SDK human-in-the-loop decision

The current official Agents SDK HITL design can pause a tool call as an interruption, serialize a `RunState`, approve or reject it, and resume the original runner. See the [official Agents SDK HITL guide](https://openai.github.io/openai-agents-python/human_in_the_loop/). The repository-pinned `openai-agents==0.20.0` does not expose that current approval API.

ExpenseOps does not use SDK HITL for Day 8. Adopting it would require a dependency upgrade and, more importantly, would resume the model after approval. Day 8's controlling invariant is stricter: confirmation must execute the frozen, durable ExpenseOps proposal with no second SDK/model run. ExpenseOps therefore remains authoritative for ownership, preview facts, validation, confirmation, idempotency, execution, and provider reconciliation.

## Shared architecture

```text
user request
  -> bounded model tool selection
  -> tenant-bound server resolution
  -> canonical domain validation/calculation
  -> immutable AgentActionProposal + code-owned preview
  -> user confirms proposal_id + proposal_version only
  -> AgentActionExecutor (no model)
  -> canonical local service OR financial operation/outbox
  -> canonical reload/reconciliation
  -> code-owned terminal action block + audit/activity
```

The model-facing tools can only propose. Their handlers cannot perform a mutation or provider operation. `AgentToolRegistry.prepare()` validates strict input and proposal schemas, recursively rejects credential/secret keys, calculates the code-owned preview, and issues an integrity-bound dispatch. `UnifiedAgentService` persists that dispatch as the immutable proposal authority.

The browser never receives or resubmits normalized parameters, transaction IDs, provider IDs, shares, or provider payloads. Confirmation and cancellation bodies contain only:

```json
{"proposal_version": 1}
```

The proposal ID is the URL resource identifier.

### Lifecycle

| State | Meaning | User action allowed |
| --- | --- | --- |
| `awaiting_confirmation` | Frozen preview is waiting for an explicit decision | Confirm or cancel |
| `confirmed` | The versioned confirmation CAS succeeded | None |
| `executing` | Exactly one executor won the execution claim | Observe/reload only |
| `completed` | Canonical local/provider result is proven | None |
| `cancelled` / `expired` | No action was performed | None |
| `failed` | Definite safe failure | None |
| `ambiguous` | Provider outcome cannot be asserted safely | Reconciliation only; no blind retry |

Proposals expire after 15 minutes. Hash/fingerprint validation happens again before confirmation. A compare-and-swap on status, version, owner, workspace, expiry, and integrity makes double-clicks and concurrent confirmations single-winner. A request that loses the execution claim can only observe or reconcile an already-recorded result; it cannot perform the action.

## Day 8A: mark transaction personal

`propose_mark_transaction_personal` is a confirmation-required local `WRITE`.

Target resolution accepts an exact validated transaction page context or an unambiguous canonical merchant/date/id match. The proposal freezes the transaction ID, expected status, and expected update timestamp. The preview reloads canonical merchant, date, currency, amount, and consequence.

After confirmation, `AgentActionExecutor` rechecks tenant/owner access, proposal integrity/state/version/expiry, the write kill switch, transaction existence, expected status/timestamp, and `TransactionService.validate_mark_personal()`. It then calls `TransactionService.mark_personal()` with the `agent` channel and proposal correlation ID. No Agent runtime or OpenAI client is used.

Double confirm, refresh, and a lost success response return the already-completed proposal without another transition. Stale, removed, pending/illegal, expired, cancelled, disabled, or inaccessible targets perform zero mutation. Existing Expense Activity receives the actor, action, outcome, transaction, `channel=agent`, proposal ID, and request correlation; model prose is never copied into financial audit records.

## Day 8B: equal Splitwise expense

`propose_post_splitwise_expense` is a confirmation-required `EXTERNAL_ACTION`.

The model supplies names, not provider identifiers. The server:

1. re-resolves the exact tenant-owned transaction;
2. requires the current user's verified, enabled personal Splitwise integration;
3. loads friends/groups from `SplitwiseService`;
4. resolves names through `EntityResolutionService` and rejects missing or ambiguous matches;
5. validates group membership when a group is selected;
6. calls `TransactionService.prepare_equal_split_expense()` and the existing share calculator;
7. freezes payer, participants, provider IDs, group membership, exact paid/owed cents, and the canonical Splitwise payload;
8. builds a code-owned preview from that frozen result.

The model never calculates money. Equal-share rounding and reconciliation are canonical, and the sum of owed shares must equal the transaction total. Day 8 intentionally defers custom and itemized splits.

### Confirmation and provider execution

Confirmation revalidates the proposal plus transaction state, posting restrictions, integration identity, current friend/group availability, group membership, payer, and the frozen provider payload. A disconnected integration, removed friend, changed group, pending transaction, incompatible manual post, or other stale destination fails before provider creation.

`TransactionService.post_prepared_splitwise_expense()` is shared with the mature transaction flow. In production it creates/reuses one correlated `FinancialOperation`, records one outbox event, marks the transaction `posting`, and returns the proposal as `executing`. The worker performs the existing provider operation and reconciliation. Conversation hydration maps the correlated operation back to `completed`, `failed`, or `ambiguous`. The browser performs bounded read-only polling after confirmation and never reposts the action.

In deterministic non-production tests, the same service path executes against a mock provider synchronously. This permits exact provider-call and split-math assertions without touching a real Splitwise account.

### Ambiguous outcomes and reconciliation

A pre-send provider lookup failure is a definite unavailable failure and creates no expense. A timeout after a possible POST is not retried blindly: the financial operation enters `needs_reconciliation`, the proposal becomes `ambiguous`, and the UI says ExpenseOps could not safely verify the outcome. Existing marker-based reconciliation can later prove success, prove absence, or remain ambiguous. Only a correlated operation belonging to the same proposal, workspace, and actor can complete the proposal; a manual UI or Telegram operation is never adopted as Agent success.

## Compound read plus action

An explicit request such as “How much did I spend at Costco and split yesterday's Costco purchase with Gunjan” may use the existing read composer and exactly one proposal tool. Canonical read blocks and the code-owned proposal preview appear together. Confirmation still executes only the frozen proposal and does not reinterpret the read answer.

## Security and tenancy

- Every proposal, run, tool call, conversation, target, integration, and financial operation is queried by authenticated workspace and owner.
- Transaction context is treated as an untrusted hint and re-resolved server-side.
- Provider friend/group IDs are server-owned and never accepted from the client/model.
- Proposal and tool schemas are strict; credential, token, password, secret, cookie, server-context, SQL, shell, URL, and purchasing authority are absent.
- Recursive proposal scanning rejects sensitive keys even inside nested provider payloads.
- Hostile merchant and participant strings remain inert display data.
- `AGENT_WRITE_ACTIONS_ENABLED=false` prevents new proposals and blocks pending confirmations. The existing Agent/read flags remain independent.
- `AGENT_PURCHASING_ENABLED` remains false and no purchasing tool exists.

## User experience and accessibility

The existing lazy Agent renderer now supports both action types with one discriminated `action_confirmation` block. It shows the canonical consequence, transaction facts, payer/participants/shares, expiry-derived state, and explicit Cancel/Confirm controls.

Controls are button elements with at least 44 px targets, single-flight loading protection, focus-safe status/error copy, and no composer-Enter confirmation path. Read-only context hides action controls even if historical action blocks exist. Desktop and 320/375/390 px mobile layouts are bounded without horizontal overflow. Queued external operations poll the conversation status for approximately four seconds; if still executing, later conversation reloads continue canonical reconciliation.

## Monitoring and privacy

Structured logs and audit events record safe identifiers and aggregate timing only:

- proposal created and proposal latency;
- confirmed and confirmation latency;
- cancelled, stale/failed, or ambiguous;
- external operation queued;
- completed/reconciled and execution/provider-operation latency.

General telemetry does not contain model prose, participant lists, shares, provider payloads, credentials, or transaction content.

## Validation record

Day 8A passed its critical stop/go gate before Splitwise implementation began. The settled Day 8 focused backend suite covers proposal-only behavior, canonical preview, no-model confirmation, one mutation, concurrent confirmation, lost response, DB failure, expiry/cancel/stale/kill-switch/access isolation, audit correlation, and hostile data.

Day 8B coverage includes equal person/group splits, exact rounding, participant ambiguity, disconnected integration, destination changes, pending/cross-workspace targets, compound read+action, no pre-confirm provider call, one correlated operation/outbox, provider 4xx/5xx/malformed/ambiguous results, manual-post races, concurrent confirmation, polling, and hostile instructions/data.

The settled implementation passed the following gates:

- backend: 1,249 passed, 14 opt-in/environment-gated tests skipped;
- Day 8 focused backend: 33 passed, including local and external concurrency races;
- manual Review, Telegram, outbox, transaction, reconciliation, API, and runtime regression selection: 439 passed;
- frontend unit: 100 passed;
- Day 8 Playwright: 34 passed, 22 intentional project/viewport skips;
- full configured Playwright matrix: 266 passed, 134 intentional skips across Chromium, mobile Chromium, Firefox, WebKit, visual regression, and accessibility;
- frontend lint: zero errors (20 pre-existing warnings), TypeScript and production build passed;
- Ruff, touched-file format checks, and `git diff --check`: passed.

The isolated live OpenAI smoke passed both proposal-selection turns using synthetic data and a provider-create trap. It created no financial operation, did not change the transaction, and never called a provider create method:

| Observation | Run latency | Input | Output | Total | Application cost |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mark personal proposal | 3,719 ms | 1,093 | 51 | 1,144 | unavailable (pricing snapshot unset) |
| Splitwise proposal | 2,171 ms | 1,196 | 64 | 1,260 | unavailable (pricing snapshot unset) |

At the official GPT-4.1 mini list rates recorded for this review ($0.40/M input and $1.60/M output), those two observations would be approximately $0.0005188 and $0.0005808. These are informational calculations only; ExpenseOps correctly persisted cost as unavailable because no matching pricing snapshot was configured.

One local deterministic observation measured mark-personal confirmation at 5.823 ms, production-style Splitwise enqueue at 10.640 ms, and synchronous mock-provider confirmation at 12.311 ms. These are single development-machine samples, not SLOs. Both confirmation paths are structurally model-free.

An earlier attempt to append Day 8 assertions to the older combined live smoke stopped before any action prompt because a legacy Day 6 model-selection assertion varied its transaction filters. It performed no action and no mutation. The final isolated Day 8 smoke removes that unrelated coupling while retaining strict proposal-tool, no-mutation, and no-provider-create assertions.

## Dependencies, migrations, and coexistence

Day 8 adds no package, lockfile, database migration, new queue, or deployment configuration. It reuses the existing proposal schema and external-operation infrastructure. The normal Review UI and Telegram keep their existing interfaces and share the same transaction/Splitwise services.

## Current limitations and next phase

- Day 8 implemented equal splits and deferred custom/itemized modes. Day 11 later added the
  separately reviewed, receipt-bound itemized flow documented in
  `ITEMIZED_RECEIPT_SPLITTING.md`; free-form percentage and exotic modes remain deferred.
- The proposal expiry is fixed at 15 minutes.
- Browser polling is deliberately bounded; a long-running provider operation may require a later reload.
- A process termination in the very small interval after winning the proposal execution claim but before recording a local result or durable financial operation can leave a proposal in `executing`; safe automatic takeover is intentionally not attempted because it could duplicate an in-flight action.
- No real Splitwise provider mutation is performed by automated live tests.

After Day 8 is reviewed, the recommended next phase is the separately scoped zero-setup receipt/staple learning work. It should not reuse the action flag as blanket write authority and should begin only after an explicit new brief.

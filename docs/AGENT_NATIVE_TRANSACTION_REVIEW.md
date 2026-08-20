# Day 19: Agent-Native Transaction Review

## Product problem

Before this change, the Agent could find and list transactions needing review, but clicking one
took the user out of the Agent entirely — to the normal web Expense Review UI. There was no way to
complete a Personal/Split decision, see the result, and move to the next item without leaving the
chat panel. This closes that gap: the Agent now runs its own bounded, one-by-one review session —
list candidates, pick one, decide (Personal / recommended Split / Skip), confirm the exact frozen
proposal, see the result, and automatically advance — entirely inside the Agent panel.

## Scope of this pass

Built:
- Agent-owned review-session queue (list → select → decide → confirm → advance → complete).
- Personal and "recommended split" (one click, from structured memory) fully in-Agent.
- Deterministic, model-free click path for every button in the session.
- Two confirmed correctness bugs fixed (below), with regression tests.
- Full tenancy/staleness handling: a candidate resolved elsewhere (web or Telegram) while the
  session is open is detected and skipped as stale, not acted on twice.

Explicitly deferred (see **Limitations**): itemized-receipt splitting inside the session loop,
arbitrary friend/group "Customize," and wiring session outcomes into the structured-memory
learning system. None of these are silently broken — see below for what happens instead.

## Independent-channel architecture (unchanged, verified)

Web CRUD, the Agent, and Telegram remain three independent callers of the same domain services.
Nothing in this change makes any of the three call into another's route or controller code:

- The Agent's new code lives in `app/agent/review_session.py` and new endpoints in
  `app/api/agent_routes.py` under `/api/agent/review-session/*`.
- Every mutation still goes through the **existing** action-proposal pipeline
  (`app/agent/action_tools.py`, `app/agent/actions.py`, `UnifiedAgentService.create_action_proposal`
  / `confirm_action_proposal`) and from there into `TransactionService` — the same code path the
  Agent's chat flow already used. Nothing new talks to Splitwise directly.
- Web (`app/api/transaction_routes.py`) and Telegram (`app/api/telegram_routes.py`) are untouched.
  The full backend test suite (1853 tests, minus two pre-existing unrelated live-network failures
  in `test_location_aware_household_ops.py`) passes unchanged.

## Build vs. integrate decisions

| Need | Decision | Why |
|---|---|---|
| Candidate discovery | **Reuse** `ReviewInboxService` (`app/services/review_inbox_service.py`), add one new method `list_agent_candidates()` | The eligibility/staleness logic (`_reconcile_open_items`, actionable-transaction rules) already exists and is shared with the web Review inbox and the chat runtime's implicit-reference resolution. No new eligibility rules were invented. |
| Session/queue state | **New, minimal model** `AgentReviewSession` | Nothing existing models "an ordered queue with a current position." `AgentActionProposal` models one action, not a session. Kept to 8 columns: `status`, a frozen `candidates_json` snapshot, `current_index`, and `results_json` outcomes. No workflow engine. |
| Split/Personal execution | **Reuse, 100%** — `propose_mark_transaction_personal` / `propose_post_splitwise_expense` tools, `AgentActionExecutor.confirm_and_execute`, `TransactionService.mark_personal` / `post_prepared_splitwise_expense`, the FinancialOperation journal, the outbox | This is the exact machinery the spec requires reusing. The review session's `propose_action` call constructs the same `AgentToolContext` → `registry.prepare` → `record_tool_call` → `create_action_proposal` sequence the LLM path uses, just without an LLM call (see below). |
| Confirm/cancel UI | **Reuse, 100%** — `ActionConfirmationCard`, `useAgentController.confirmAction` / `cancelAction`, `/api/agent/proposals/{id}/confirm|cancel` | Unmodified. The review session's proposal renders in the identical confirmation card the chat flow already uses. |
| Friend/group picker data | **Reuse** existing `GET /api/splitwise/friends` / `/groups` (frontend-only, read-only) | Same endpoints `GroupManagementPanel.tsx` already calls. Not built this pass into the session UI (see Limitations). |

## The two confirmed bugs (fixed)

### 1. Completed undo could block a new split

`AgentActionExecutor._splitwise_operation` (`app/agent/actions.py`) looked up the most recent
`FinancialOperation` for a transaction filtered only by `action == "splitwise_create"`, ignoring
`generation`. After a split was undone (which bumps `ExpenseTransaction.splitwise_generation`), a
new split attempt found the *old*, already-completed generation-0 `splitwise_create` row, saw its
`correlation_id` didn't match the new proposal, and failed with `incompatible_financial_operation`
— even though the domain journal itself (`TransactionService._claim_financial_operation`) was
already correctly generation-scoped and would have created a fresh row.

**Fix:** `_splitwise_operation` now takes an explicit `generation` and filters on it, matching the
transaction's *current* `splitwise_generation` at each call site. Regression test:
`test_day19_split_then_undo_then_split_again_creates_new_generation` in `tests/test_agent_runtime.py`.

### 2. Missing message-provenance check on the main split tool

`propose_itemized_receipt_split` already validated that every participant/group name the model
supplied was a literal phrase in the user's current message
(`_validate_itemized_user_provenance`). `propose_post_splitwise_expense` — the tool used for a
plain "split with Alex" — had no equivalent check, so a model-invented name that happened to
fuzzy-match a real Splitwise friend would not have been blocked.

**Fix:** added `_validate_post_splitwise_user_provenance` (same substring-in-message pattern,
`app/agent/action_tools.py`), wired into `_normalize_post_splitwise`. Payer self-duplication
("split with me and Alex") was already correctly handled elsewhere in this function and was not
touched. Regression test: `test_day19_post_splitwise_rejects_participant_not_in_current_message`.

Grounding for the new in-Agent review session's clicks (not typed text) works differently and
safely: the click already carries a resolved, server-known name (from structured-memory
recommendations or, in future, an explicit picker selection), so `propose_action` builds a
synthetic `latest_user_text` containing exactly that name before calling the tool. The check still
runs — it just always passes for a genuine click, and still rejects anything ungrounded on the
typed-text path. No bypass flag was added.

## Data model

```
agent_review_sessions
  id, workspace_id, public_id, owner_user_id, conversation_id
  status              active | completed | cancelled
  candidates_json      [{review_item_public_id, kind, source_type, source_entity_id, source_fingerprint}, ...]
  current_index        int
  results_json          [{review_item_public_id, kind, source_entity_id, outcome, proposal_public_id}, ...]
  created_at, updated_at, completed_at
```

One partial unique index enforces at most one **active** session per `(workspace, owner, conversation)`
— asking "what needs review" twice resumes the same session rather than duplicating it. RLS is
enabled the same way as every other tenant table (`alembic/versions/20260819_0035_*.py`).

`candidates_json` is a frozen snapshot taken at session start — it does not silently reorder if new
transactions arrive mid-session (per spec). Each candidate is *revalidated* against live state
(`ReviewInboxService.get_agent_candidate`, comparing `state` and `source_fingerprint`) the moment it
becomes current; if it changed or was resolved elsewhere, it's marked `stale` and skipped
automatically, with the next real candidate shown instead.

## Ordering

`ReviewInboxService.list_agent_candidates()`: tier 1 = non-pending transactions needing a
Personal/Split decision, tier 2 = itemized-split-ready receipts, tier 3 = pending transactions
(still classifiable, but Splitwise posting follows the existing `_ensure_can_post` policy same as
today). Oldest-first within a tier, by the `ReviewItem`'s `created_at`. `receipt_match_needed` and
`financial_reconciliation` items are excluded — no Agent action exists for those yet; they remain
in the web Review page's existing recovery UI, unchanged.

## API surface (new)

All under `/api/agent/review-session/`, all deterministic (no OpenAI call):

- `POST /start` `{conversation_public_id}` → start or resume the active session for that conversation.
- `GET /{id}` → current state (used to resume after refresh/reopen).
- `POST /{id}/propose` `{action: mark_personal|post_splitwise_expense, participant_names?, group_name?}`
  → creates a frozen `AgentActionProposal`, returned as the same `AgentActionConfirmationBlock` the
  chat flow renders.
- `POST /{id}/advance` `{proposal_public_id}` → records the outcome of a *completed* proposal and
  moves to the next candidate. Only advances on `status == "completed"` and only when the proposal's
  frozen `transaction_id` matches the session's current candidate — a stray or unrelated proposal id
  cannot advance someone else's session.
- `POST /{id}/skip`, `POST /{id}/stop`.

Every handler re-derives workspace/user/session/candidate from server state
(`app/api/deps.py`'s `CurrentUser`/`CurrentWorkspace`, same as every other route) — a client never
authorizes anything by what it renders or sends.

## Frontend

- `TransactionListCard`'s default click no longer navigates away
  (`frontend/src/agent/AgentExperience.tsx`); clicking a "Needs decision" item now starts/resumes
  the review session in-panel. No `AgentNavigationBlock`/`onNavigate` call happens on that path.
- `ReviewSessionCard` (new, in `AgentExperience.tsx`) renders progress ("Review purchase 2 of 3"),
  the current candidate, Personal / recommended-Split / Skip / Stop, and — when a proposal exists —
  reuses the existing `ActionConfirmationCard` unmodified for the confirm step.
- Clicks call typed REST functions in `frontend/src/agent/api.ts`
  (`startAgentReviewSession`, `proposeAgentReviewAction`, `advanceAgentReviewSession`,
  `skipAgentReviewCandidate`, `stopAgentReviewSession`) — never `streamAgentTurn` — so no model call
  happens for a deterministic click. Typed natural-language commands continue to go through the
  existing chat/turn path unchanged.
- All server responses are runtime-validated (`parseAgentReviewSession` in
  `frontend/src/agent/validation.ts`), matching this codebase's existing fail-closed pattern for
  every other Agent response type.
- A secondary "Customize in full Expense Review" button remains as an explicit escape hatch to the
  unchanged web Review UI, per the spec's allowance for currently-unsupported customization.

## Security / tenancy

- Every review-session endpoint re-validates `workspace_id` + `owner_user_id` from the authenticated
  session, never from the request body.
- `ReviewSessionService.get_session` raises `AgentNotFoundError` (404) for a session belonging to
  another workspace — covered by
  `test_foreign_workspace_cannot_read_another_workspaces_review_session`.
- No new provider credentials, callbacks, or arbitrary payloads are exposed to the client or the
  model; `list_agent_candidates()` returns only bounded, typed fields.

## Learning / structured memory — deliberately not wired this pass

The existing "Day 12" learning write path (`AIInterpretationMemoryService.record_ai_interpretation_memory`
/ `record_button_fallback_memory`) is gated on a `PendingTelegramSplit` object and specifically
detects *AI-interpretation corrections* in Telegram's free-text-then-correct conversational flow —
it has no channel-neutral form today. The Agent's review session doesn't have an equivalent
"free-text interpretation to correct" step (its tool calls are already structured), so there is no
matching signal to record. Rather than construct a synthetic `PendingTelegramSplit` or duplicate the
write logic (risking drift from the real thing), this is left out and documented here. A future pass
should extract a channel-neutral write path from `AIInterpretationMemoryService` first.

## Limitations

- Clicking a specific transaction in a chat-rendered list starts/resumes the queue from its current
  position; it does not deep-link to that exact transaction. The queue order is otherwise
  deterministic and documented above.
- Itemized-receipt candidates surface in the queue (tier 2) but the full in-Agent line-item
  assignment flow is not wired into the session loop this pass — the session shows the candidate and
  lets the user skip it; the existing `propose_itemized_receipt_split` tool still works via typed
  chat.
- "Customize" (arbitrary friend/group selection beyond the one-click recommendation) is not built
  this pass; it falls back to the unchanged web Review UI via an explicit secondary button.
- No dedicated "undo" Agent tool was added — not required: the bug fix ensures a fresh split after
  an undo (done via web or Telegram, which are unchanged) works correctly; building an in-Agent undo
  affordance was out of scope for this pass.
- No Playwright/axe accessibility run was performed in this environment (no browser tooling
  available); ARIA/focus patterns were reviewed statically against the existing confirmation-card
  conventions, which are already accessible.
- Observability events (`agent_review_session_started`, etc.) were not added as a separate
  enumerated list this pass; existing `log_event` calls inside the reused services still fire.

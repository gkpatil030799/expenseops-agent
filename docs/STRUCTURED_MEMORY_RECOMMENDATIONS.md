# Structured memory and recommendations

## Decision

Day 12 integrates with ExpenseOps' existing structured state rather than adding generic LLM
memory, embeddings, a vector database, or a second recommendation platform. Household item
preferences, saved places, Promotion Intelligence feedback, Splitwise groups, and confirmed
interpretation records remain authoritative in their existing domains.

Transaction recommendations reuse `AIInterpretationMemory`, `DataConsent`, and `AuditEvent`.
New transaction preferences are exact-merchant records owned by the authenticated user inside
the active workspace. The recommendation is advisory only: it never changes a transaction,
creates a Splitwise expense, or bypasses the existing proposal and confirmation boundary.

## Memory policy

A record may be created by an explicit settings action or by an existing confirmed/corrected
transaction flow. A model sentence by itself is never memory. New records retain only bounded
structured fields: merchant, personal/shared outcome, participant display names, optional group,
split mode, source, and usage timestamps. Arbitrary chat text is neither persisted nor returned to
the model.

Migration `20260817_0031` adds nullable user ownership and irreversibly replaces legacy raw
`original_message` content with a code-owned label. Legacy rows remain workspace evidence for the
existing Telegram correction path, but new exact-merchant recommendations require the current
user's ownership. Downgrade does not reconstruct deleted transcripts.

Users can view the deterministic label and rationale, add a preference, correct it, delete it,
rate it, and disable transaction learning. Disabling learning prevents both new interpretation
records and recommendation retrieval; it does not delete already saved preferences.

## Recommendation and telemetry rules

An exact normalized merchant match may replace the old deterministic heuristic with
`likely_personal` or `likely_shared` plus a factual source rationale. It is still only a suggestion.
Editing the saved preference changes the next response. No fuzzy match, psychological inference,
participant invention, or automatic action is allowed.

`AuditEvent` records aggregate `shown`, `accepted`, `edited`, and `rejected` outcomes without raw
transaction or chat payloads. A shown event is deduplicated for a preference/transaction pair.
Settings reports agreement and correction rates only after an outcome exists; no quality threshold
is invented before evidence is collected.

## Security and tenancy

Workspace RLS remains the first boundary. Service queries also require the current `owner_user_id`
for new preferences, and every endpoint depends on authenticated user and workspace resolution.
Cross-user records in a shared workspace cannot drive, edit, rate, or delete another user's new
preference. Audit metadata contains only IDs, domain, and source.

## Accepted limitations

- Exact merchant equality is intentionally conservative; aliases remain a future structured-field
  improvement rather than fuzzy model memory.
- Existing household, deal, and location preferences continue in their mature domain stores; this
  UI does not duplicate them into transaction memory.
- Legacy ownerless correction rows remain available to the pre-existing Telegram relevance path,
  but are not promoted into user-owned exact recommendations without an explicit save or edit.
- Quality rates are descriptive beta telemetry, not an automated rollout or execution threshold.

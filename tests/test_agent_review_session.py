"""Day 19: Agent-native one-by-one transaction review session."""

from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy import select

from app.agent.action_tools import register_action_tools
from app.agent.actions import AgentActionExecutor
from app.agent.read_tools import build_read_tool_registry
from app.agent.review_session import ReviewSessionService
from app.agent.service import AgentNotFoundError
from app.agent.tooling import AgentActionClarificationRequired
from app.models import (
    AgentActionProposal,
    AgentReviewSession,
    AIInterpretationMemory,
    ExpenseTransaction,
    TransactionStatus,
)
from app.services.ai_intent_extraction_service import AIIntentExtractionService, ExtractedAIIntent
from app.services.review_inbox_service import ReviewInboxService
from app.services.splitwise_service import SplitwiseAPIError, SplitwiseService
from app.services.transaction_service import TransactionService
from tests.test_agent_runtime import (
    RuntimeFixture,
    _conversation,
    _install_splitwise_provider,
    _scoped,
    _settings,
    agent_runtime_db,
)

__all__ = ["agent_runtime_db"]


def _sync(db, tx, *, owner_user_id: int) -> None:
    ReviewInboxService(db).sync_transaction(tx, owner_user_id=owner_user_id, commit=True)


def _stub_intent_extraction(monkeypatch, intent: ExtractedAIIntent) -> None:
    """Stub the LLM extraction stage only -- resolution still runs for real
    against the mocked Splitwise provider, exactly like Telegram's AI chat
    mode split between an LLM extraction call and deterministic resolution."""
    monkeypatch.setattr(
        AIIntentExtractionService, "extract", lambda _self, **_kwargs: intent
    )


def _posted_split(
    *,
    workspace_id: int,
    plaid_item_id: int,
    provider_id: str,
    merchant: str,
    payer_id: int,
    partner_id: int,
) -> ExpenseTransaction:
    """A completed Splitwise split, shaped like the real post_prepared_splitwise_expense
    payload (users__N__user_id / paid_share / owed_share), for frequent-partner tests."""
    return ExpenseTransaction(
        workspace_id=workspace_id,
        plaid_transaction_id=provider_id,
        plaid_item_id=plaid_item_id,
        account_id="runtime-checking",
        merchant_name=merchant,
        name=merchant,
        amount_cents=4000,
        iso_currency_code="USD",
        date=date(2026, 8, 1),
        pending=False,
        category="Restaurants",
        status=TransactionStatus.POSTED.value,
        splitwise_expense_id=f"splitwise-{provider_id}",
        splitwise_payload_json=json.dumps(
            {
                "cost": "40.00",
                "users__0__user_id": payer_id,
                "users__0__paid_share": "40.00",
                "users__0__owed_share": "20.00",
                "users__1__user_id": partner_id,
                "users__1__paid_share": "0.00",
                "users__1__owed_share": "20.00",
            }
        ),
    )


def test_start_review_session_excludes_pending_transactions(
    agent_runtime_db: RuntimeFixture,
):
    """A pending amount can still be revised, so the Agent never offers a
    decision on one -- the same rule the Telegram notification path applies.
    """

    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        settings = _settings(writes=True)
        pending_tx = db.get(
            ExpenseTransaction,
            agent_runtime_db.transaction_ids["pending"],
        )
        unreviewed_tx = db.get(
            ExpenseTransaction,
            agent_runtime_db.transaction_ids["unreviewed"],
        )
        pending_tx.status = TransactionStatus.ASK_USER.value
        db.commit()
        _sync(db, pending_tx, owner_user_id=tenant.user_id)
        _sync(db, unreviewed_tx, owner_user_id=tenant.user_id)

        conversation = _conversation(db, tenant, settings)
        service = ReviewSessionService(db, settings)
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)
        session, live = service.current_candidate(session)

        assert session.status == "active"
        # Only the settled transaction is a candidate; the pending one is absent
        # entirely rather than merely ranked lower.
        assert len(session.candidates_json) == 1
        assert live is not None
        assert live["transaction"]["id"] == unreviewed_tx.id
        assert live["transaction"]["pending"] is False
        assert all(
            candidate["source_entity_id"] != pending_tx.id
            for candidate in session.candidates_json
        )


def test_start_review_session_resumes_existing_active_session(
    agent_runtime_db: RuntimeFixture,
):
    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        settings = _settings(writes=True)
        tx = db.get(
            ExpenseTransaction,
            agent_runtime_db.transaction_ids["unreviewed"],
        )
        _sync(db, tx, owner_user_id=tenant.user_id)
        conversation = _conversation(db, tenant, settings)
        service = ReviewSessionService(db, settings)
        first = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)
        second = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)
        assert first.public_id == second.public_id
        assert db.scalar(select(AgentReviewSession).where(AgentReviewSession.id == first.id))


def test_start_review_session_with_no_candidates_completes_immediately(
    agent_runtime_db: RuntimeFixture,
):
    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        settings = _settings(writes=True)
        conversation = _conversation(db, tenant, settings)
        service = ReviewSessionService(db, settings)
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)
        session, live = service.current_candidate(session)
        assert session.status == "completed"
        assert live is None
        assert ReviewSessionService.summary(session)["remaining"] == 0


def test_propose_mark_personal_then_advance_moves_to_next_candidate(
    agent_runtime_db: RuntimeFixture,
):
    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        settings = _settings(writes=True)
        unreviewed = db.get(
            ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"]
        )
        adversarial = db.get(
            ExpenseTransaction, agent_runtime_db.transaction_ids["adversarial"]
        )
        adversarial.status = TransactionStatus.ASK_USER.value
        db.commit()
        _sync(db, unreviewed, owner_user_id=tenant.user_id)
        _sync(db, adversarial, owner_user_id=tenant.user_id)

        conversation = _conversation(db, tenant, settings)
        service = ReviewSessionService(db, settings)
        registry = build_read_tool_registry(settings)
        register_action_tools(registry)
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)
        session, live = service.current_candidate(session)
        first_transaction_id = live["transaction"]["id"]

        proposal = service.propose_action(session, registry=registry, action="mark_personal")
        assert proposal.tool_name == "propose_mark_transaction_personal"

        executor = AgentActionExecutor(db, registry=registry, settings=settings)
        completed = executor.confirm_and_execute(
            proposal.public_id,
            owner_user_id=tenant.user_id,
            expected_version=proposal.version,
        )
        assert completed.status == "completed"

        session = service.advance_after_proposal(session, proposal_public_id=proposal.public_id)
        summary = ReviewSessionService.summary(session)
        assert summary["personal"] == 1
        assert summary["remaining"] == 1

        session, live = service.current_candidate(session)
        assert live is not None
        assert live["transaction"]["id"] != first_transaction_id


def test_advance_moves_forward_on_a_still_queued_split_not_yet_completed(
    agent_runtime_db: RuntimeFixture,
):
    """Splitwise posts are handed to the durable outbox worker in production,
    so advance_after_proposal routinely runs while the proposal is still
    "confirmed" or "executing" rather than "completed" -- the frontend no
    longer blocks the review queue on completion. The queue must still move
    forward: the user's decision is made at confirmation time, and a provider
    failure surfaces through the separate recovery flow rather than by
    reopening this transaction as an actionable review candidate.
    """

    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        settings = _settings(writes=True)
        unreviewed = db.get(ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"])
        adversarial = db.get(ExpenseTransaction, agent_runtime_db.transaction_ids["adversarial"])
        adversarial.status = TransactionStatus.ASK_USER.value
        db.commit()
        _sync(db, unreviewed, owner_user_id=tenant.user_id)
        _sync(db, adversarial, owner_user_id=tenant.user_id)

        conversation = _conversation(db, tenant, settings)
        service = ReviewSessionService(db, settings)
        registry = build_read_tool_registry(settings)
        register_action_tools(registry)
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)
        session, live = service.current_candidate(session)
        first_transaction_id = live["transaction"]["id"]

        proposal = service.propose_action(session, registry=registry, action="mark_personal")

        # Simulates the outbox worker not having claimed the job yet, exactly
        # as happens in production -- deliberately not driven through the
        # executor, which would complete it inline in this test environment.
        stored = db.get(AgentActionProposal, proposal.id)
        stored.status = "executing"
        db.commit()

        session = service.advance_after_proposal(session, proposal_public_id=proposal.public_id)
        summary = ReviewSessionService.summary(session)
        assert summary["personal"] == 1
        assert summary["remaining"] == 1

        session, live = service.current_candidate(session)
        assert live is not None
        assert live["transaction"]["id"] != first_transaction_id


def test_advance_still_holds_on_a_terminal_failure(agent_runtime_db: RuntimeFixture):
    """A proposal that failed outright (as opposed to one still in flight) must
    not advance the queue -- there is no successful decision to record.
    """

    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        settings = _settings(writes=True)
        unreviewed = db.get(ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"])
        _sync(db, unreviewed, owner_user_id=tenant.user_id)

        conversation = _conversation(db, tenant, settings)
        service = ReviewSessionService(db, settings)
        registry = build_read_tool_registry(settings)
        register_action_tools(registry)
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)

        proposal = service.propose_action(session, registry=registry, action="mark_personal")
        stored = db.get(AgentActionProposal, proposal.id)
        stored.status = "failed"
        db.commit()

        session = service.advance_after_proposal(session, proposal_public_id=proposal.public_id)
        assert ReviewSessionService.summary(session)["remaining"] == 1
        assert ReviewSessionService.summary(session)["reviewed"] == 0


def test_propose_split_grounds_participants_from_the_click_not_free_text(
    agent_runtime_db: RuntimeFixture, monkeypatch
):
    _install_splitwise_provider(monkeypatch)
    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        settings = _settings(writes=True)
        unreviewed = db.get(
            ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"]
        )
        _sync(db, unreviewed, owner_user_id=tenant.user_id)

        conversation = _conversation(db, tenant, settings)
        service = ReviewSessionService(db, settings)
        registry = build_read_tool_registry(settings)
        register_action_tools(registry)
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)

        # No free-text message exists anywhere in this flow -- the clicked
        # participant name is the only source of grounding, and it must
        # still pass the tool's message-provenance check.
        proposal = service.propose_action(
            session,
            registry=registry,
            action="post_splitwise_expense",
            participant_names=["Gunjan"],
        )
        assert proposal.tool_name == "propose_post_splitwise_expense"
        assert proposal.normalized_parameters_json["transaction_id"] == unreviewed.id

        executor = AgentActionExecutor(db, registry=registry, settings=settings)
        completed = executor.confirm_and_execute(
            proposal.public_id,
            owner_user_id=tenant.user_id,
            expected_version=proposal.version,
        )
        assert completed.status == "completed"

        session = service.advance_after_proposal(session, proposal_public_id=proposal.public_id)
        assert ReviewSessionService.summary(session)["split"] == 1


def test_skip_records_outcome_and_does_not_count_as_reviewed_learning_signal(
    agent_runtime_db: RuntimeFixture,
):
    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        settings = _settings(writes=True)
        unreviewed = db.get(
            ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"]
        )
        _sync(db, unreviewed, owner_user_id=tenant.user_id)
        conversation = _conversation(db, tenant, settings)
        service = ReviewSessionService(db, settings)
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)
        session = service.skip_current(session)
        summary = ReviewSessionService.summary(session)
        assert summary["skipped"] == 1
        assert summary["personal"] == 0
        assert summary["split"] == 0
        assert session.status == "completed"


def test_stop_marks_session_cancelled_and_freezes_progress(
    agent_runtime_db: RuntimeFixture,
):
    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        settings = _settings(writes=True)
        unreviewed = db.get(
            ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"]
        )
        _sync(db, unreviewed, owner_user_id=tenant.user_id)
        conversation = _conversation(db, tenant, settings)
        service = ReviewSessionService(db, settings)
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)
        session = service.stop(session)
        assert session.status == "cancelled"
        assert session.completed_at is not None
        again = service.stop(session)
        assert again.status == "cancelled"


def test_transaction_resolved_elsewhere_marks_candidate_stale_and_advances(
    agent_runtime_db: RuntimeFixture,
):
    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        settings = _settings(writes=True)
        unreviewed = db.get(
            ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"]
        )
        aldi = db.get(ExpenseTransaction, agent_runtime_db.transaction_ids["aldi"])
        aldi.status = TransactionStatus.ASK_USER.value
        aldi.splitwise_expense_id = None
        db.commit()
        _sync(db, unreviewed, owner_user_id=tenant.user_id)
        _sync(db, aldi, owner_user_id=tenant.user_id)

        conversation = _conversation(db, tenant, settings)
        service = ReviewSessionService(db, settings)
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)
        session, live = service.current_candidate(session)
        first_id = live["transaction"]["id"]

        # Simulate the web UI (or Telegram) marking this transaction personal
        # while the Agent review session is still open.
        TransactionService(db, settings).mark_personal(first_id)
        db.commit()
        stale_tx = db.get(ExpenseTransaction, first_id)
        _sync(db, stale_tx, owner_user_id=tenant.user_id)

        session, live = service.current_candidate(session)
        summary = ReviewSessionService.summary(session)
        assert summary["stale"] == 1
        if live is not None:
            assert live["transaction"]["id"] != first_id


def test_foreign_workspace_cannot_read_another_workspaces_review_session(
    agent_runtime_db: RuntimeFixture,
):
    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        settings = _settings(writes=True)
        unreviewed = db.get(
            ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"]
        )
        _sync(db, unreviewed, owner_user_id=tenant.user_id)
        conversation = _conversation(db, tenant, settings)
        service = ReviewSessionService(db, settings)
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)
        session_public_id = session.public_id

    with _scoped(agent_runtime_db, actor="outsider") as db:
        settings = _settings(writes=True)
        service = ReviewSessionService(db, settings)
        with pytest.raises(AgentNotFoundError):
            service.get_session(
                session_public_id,
                owner_user_id=agent_runtime_db.contexts["outsider"].user_id,
            )


def test_no_merchant_recommendation_falls_back_to_frequent_splitwise_partner(
    agent_runtime_db: RuntimeFixture,
    monkeypatch,
):
    _install_splitwise_provider(
        monkeypatch,
        friends=[{"id": 200, "first_name": "Janhavi", "last_name": "Patil"}],
    )
    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        unreviewed = db.get(ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"])
        item_id = unreviewed.plaid_item_id
        # Two past posted splits with the same partner, at unrelated merchants, so this
        # can only be a frequency signal — not the per-merchant AIInterpretationMemory path.
        db.add_all(
            [
                _posted_split(
                    workspace_id=tenant.workspace_id,
                    plaid_item_id=item_id,
                    provider_id="runtime-frequent-partner-1",
                    merchant="Trader Joe's",
                    payer_id=100,
                    partner_id=200,
                ),
                _posted_split(
                    workspace_id=tenant.workspace_id,
                    plaid_item_id=item_id,
                    provider_id="runtime-frequent-partner-2",
                    merchant="Olive Garden",
                    payer_id=100,
                    partner_id=200,
                ),
            ]
        )
        db.commit()
        _sync(db, unreviewed, owner_user_id=tenant.user_id)

        conversation = _conversation(db, tenant, _settings(writes=True))
        service = ReviewSessionService(db, _settings(writes=True))
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)
        _session, live = service.current_candidate(session)

        assert live is not None
        assert live["recommendation"] is not None
        assert live["recommendation"]["participant_names"] == ["Janhavi Patil"]
        assert "often split with" in live["recommendation"]["reason"]


def test_single_past_split_is_not_frequent_enough_for_a_recommendation(
    agent_runtime_db: RuntimeFixture,
    monkeypatch,
):
    _install_splitwise_provider(
        monkeypatch,
        friends=[{"id": 200, "first_name": "Janhavi", "last_name": "Patil"}],
    )
    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        unreviewed = db.get(ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"])
        db.add(
            _posted_split(
                workspace_id=tenant.workspace_id,
                plaid_item_id=unreviewed.plaid_item_id,
                provider_id="runtime-single-partner",
                merchant="Trader Joe's",
                payer_id=100,
                partner_id=200,
            )
        )
        db.commit()
        _sync(db, unreviewed, owner_user_id=tenant.user_id)

        conversation = _conversation(db, tenant, _settings(writes=True))
        service = ReviewSessionService(db, _settings(writes=True))
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)
        _session, live = service.current_candidate(session)

        assert live is not None
        assert live["recommendation"] is None


def test_frequent_partner_recommendation_ignores_other_workspaces_history(
    agent_runtime_db: RuntimeFixture,
    monkeypatch,
):
    _install_splitwise_provider(
        monkeypatch,
        friends=[{"id": 200, "first_name": "Janhavi", "last_name": "Patil"}],
    )
    with _scoped(agent_runtime_db, actor="outsider") as db:
        other_tx = db.get(ExpenseTransaction, agent_runtime_db.transaction_ids["other_workspace"])
        other_tenant = agent_runtime_db.contexts["outsider"]
        db.add_all(
            [
                _posted_split(
                    workspace_id=other_tenant.workspace_id,
                    plaid_item_id=other_tx.plaid_item_id,
                    provider_id="runtime-other-workspace-partner-1",
                    merchant="Trader Joe's",
                    payer_id=100,
                    partner_id=200,
                ),
                _posted_split(
                    workspace_id=other_tenant.workspace_id,
                    plaid_item_id=other_tx.plaid_item_id,
                    provider_id="runtime-other-workspace-partner-2",
                    merchant="Olive Garden",
                    payer_id=100,
                    partner_id=200,
                ),
            ]
        )
        db.commit()

    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        unreviewed = db.get(ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"])
        _sync(db, unreviewed, owner_user_id=tenant.user_id)

        conversation = _conversation(db, tenant, _settings(writes=True))
        service = ReviewSessionService(db, _settings(writes=True))
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)
        _session, live = service.current_candidate(session)

        assert live is not None
        assert live["recommendation"] is None


def test_frequent_partner_lookup_is_cached_and_fails_closed_on_splitwise_error(
    agent_runtime_db: RuntimeFixture,
    monkeypatch,
):
    calls: list[None] = []

    def failing_get_friends(_self):
        calls.append(None)
        raise SplitwiseAPIError("Splitwise is unavailable")

    monkeypatch.setattr(SplitwiseService, "get_friends", failing_get_friends)
    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        unreviewed = db.get(ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"])
        # Settled, so it qualifies as a second candidate. This test is about
        # caching the frequent-partner lookup across candidates; pending
        # transactions are excluded from review entirely.
        second = db.get(ExpenseTransaction, agent_runtime_db.transaction_ids["pending"])
        second.status = TransactionStatus.ASK_USER.value
        second.pending = False
        db.add_all(
            [
                _posted_split(
                    workspace_id=tenant.workspace_id,
                    plaid_item_id=unreviewed.plaid_item_id,
                    provider_id="runtime-cache-partner-1",
                    merchant="Trader Joe's",
                    payer_id=100,
                    partner_id=200,
                ),
                _posted_split(
                    workspace_id=tenant.workspace_id,
                    plaid_item_id=unreviewed.plaid_item_id,
                    provider_id="runtime-cache-partner-2",
                    merchant="Olive Garden",
                    payer_id=100,
                    partner_id=200,
                ),
            ]
        )
        db.commit()
        _sync(db, unreviewed, owner_user_id=tenant.user_id)
        _sync(db, second, owner_user_id=tenant.user_id)

        inbox = ReviewInboxService(db)
        candidates = inbox.list_agent_candidates()

        # Both candidates lack a merchant-specific recommendation, so both would
        # need the frequent-partner fallback — it must fail closed (no crash, no
        # recommendation) and only call the Splitwise API once, not once per candidate.
        assert len(candidates) == 2
        assert all(candidate["recommendation"] is None for candidate in candidates)
        assert len(calls) == 1


def test_interpret_typed_split_message_resolves_and_proposes(
    agent_runtime_db: RuntimeFixture, monkeypatch
):
    _install_splitwise_provider(monkeypatch)
    _stub_intent_extraction(
        monkeypatch, ExtractedAIIntent(action="split", person_mentions=["Gunjan"])
    )
    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        settings = _settings(writes=True)
        unreviewed = db.get(ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"])
        _sync(db, unreviewed, owner_user_id=tenant.user_id)

        conversation = _conversation(db, tenant, settings)
        service = ReviewSessionService(db, settings)
        registry = build_read_tool_registry(settings)
        register_action_tools(registry)
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)

        result = service.interpret_typed_message(
            session, registry=registry, text="split this with Gunjan"
        )

        assert result.status == "proposed"
        assert result.proposal is not None
        assert result.proposal.tool_name == "propose_post_splitwise_expense"
        assert result.proposal.normalized_parameters_json["transaction_id"] == unreviewed.id
        assert result.proposal.metadata_json.get("review_source") == "typed"


def test_interpret_typed_personal_message_proposes_mark_personal(
    agent_runtime_db: RuntimeFixture, monkeypatch
):
    _stub_intent_extraction(monkeypatch, ExtractedAIIntent(action="personal"))
    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        settings = _settings(writes=True)
        unreviewed = db.get(ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"])
        _sync(db, unreviewed, owner_user_id=tenant.user_id)

        conversation = _conversation(db, tenant, settings)
        service = ReviewSessionService(db, settings)
        registry = build_read_tool_registry(settings)
        register_action_tools(registry)
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)

        result = service.interpret_typed_message(
            session, registry=registry, text="mark this personal"
        )

        assert result.status == "proposed"
        assert result.proposal.tool_name == "propose_mark_transaction_personal"


def test_interpret_typed_unknown_name_raises_clarification_for_the_route_to_convert(
    agent_runtime_db: RuntimeFixture, monkeypatch
):
    _install_splitwise_provider(monkeypatch)
    _stub_intent_extraction(
        monkeypatch, ExtractedAIIntent(action="split", person_mentions=["Nobody Here"])
    )
    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        settings = _settings(writes=True)
        unreviewed = db.get(ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"])
        _sync(db, unreviewed, owner_user_id=tenant.user_id)

        conversation = _conversation(db, tenant, settings)
        service = ReviewSessionService(db, settings)
        registry = build_read_tool_registry(settings)
        register_action_tools(registry)
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)

        with pytest.raises(AgentActionClarificationRequired):
            service.interpret_typed_message(
                session, registry=registry, text="split this with Nobody Here"
            )


def test_interpret_typed_unclear_message_returns_clarify_without_proposing(
    agent_runtime_db: RuntimeFixture, monkeypatch
):
    _stub_intent_extraction(
        monkeypatch,
        ExtractedAIIntent(action="unknown", clarification_question="Not sure what you mean."),
    )
    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        settings = _settings(writes=True)
        unreviewed = db.get(ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"])
        _sync(db, unreviewed, owner_user_id=tenant.user_id)

        conversation = _conversation(db, tenant, settings)
        service = ReviewSessionService(db, settings)
        registry = build_read_tool_registry(settings)
        register_action_tools(registry)
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)

        result = service.interpret_typed_message(
            session, registry=registry, text="huh what"
        )

        assert result.status == "clarify"
        assert result.proposal is None
        assert result.message == "Not sure what you mean."


def test_confirmed_typed_split_records_agent_interpretation_memory(
    agent_runtime_db: RuntimeFixture, monkeypatch
):
    _install_splitwise_provider(monkeypatch)
    _stub_intent_extraction(
        monkeypatch, ExtractedAIIntent(action="split", person_mentions=["Gunjan"])
    )
    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        settings = _settings(writes=True)
        unreviewed = db.get(ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"])
        _sync(db, unreviewed, owner_user_id=tenant.user_id)

        conversation = _conversation(db, tenant, settings)
        service = ReviewSessionService(db, settings)
        registry = build_read_tool_registry(settings)
        register_action_tools(registry)
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)

        result = service.interpret_typed_message(
            session, registry=registry, text="split this with Gunjan"
        )
        proposal = result.proposal

        executor = AgentActionExecutor(db, registry=registry, settings=settings)
        completed = executor.confirm_and_execute(
            proposal.public_id,
            owner_user_id=tenant.user_id,
            expected_version=proposal.version,
        )
        assert completed.status == "completed"

        service.advance_after_proposal(session, proposal_public_id=proposal.public_id)

        memory = db.scalar(
            select(AIInterpretationMemory).where(
                AIInterpretationMemory.owner_user_id == tenant.user_id,
                AIInterpretationMemory.correction_type == "agent_review_typed",
            )
        )
        assert memory is not None
        assert memory.final_action == "split_equal"
        assert memory.final_split_mode == "equal"
        assert [p["display_name"] for p in memory.final_participants] == ["Gunjan Patil"]


def test_button_driven_proposal_does_not_record_agent_typed_memory(
    agent_runtime_db: RuntimeFixture, monkeypatch
):
    _install_splitwise_provider(monkeypatch)
    with _scoped(agent_runtime_db) as db:
        tenant = agent_runtime_db.contexts["owner"]
        settings = _settings(writes=True)
        unreviewed = db.get(ExpenseTransaction, agent_runtime_db.transaction_ids["unreviewed"])
        _sync(db, unreviewed, owner_user_id=tenant.user_id)

        conversation = _conversation(db, tenant, settings)
        service = ReviewSessionService(db, settings)
        registry = build_read_tool_registry(settings)
        register_action_tools(registry)
        session = service.start_or_resume(conversation.public_id, owner_user_id=tenant.user_id)

        proposal = service.propose_action(
            session,
            registry=registry,
            action="post_splitwise_expense",
            participant_names=["Gunjan"],
        )
        assert proposal.metadata_json.get("review_source") is None

        executor = AgentActionExecutor(db, registry=registry, settings=settings)
        completed = executor.confirm_and_execute(
            proposal.public_id,
            owner_user_id=tenant.user_id,
            expected_version=proposal.version,
        )
        assert completed.status == "completed"

        service.advance_after_proposal(session, proposal_public_id=proposal.public_id)

        memory = db.scalar(
            select(AIInterpretationMemory).where(
                AIInterpretationMemory.correction_type == "agent_review_typed",
            )
        )
        assert memory is None

"""Day 19: Agent-native one-by-one transaction review session."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.agent.action_tools import register_action_tools
from app.agent.actions import AgentActionExecutor
from app.agent.read_tools import build_read_tool_registry
from app.agent.review_session import ReviewSessionService
from app.agent.service import AgentNotFoundError
from app.models import AgentReviewSession, ExpenseTransaction, TransactionStatus
from app.services.review_inbox_service import ReviewInboxService
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


def test_start_review_session_orders_non_pending_before_pending(
    agent_runtime_db: RuntimeFixture,
):
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
        assert len(session.candidates_json) == 2
        # Non-pending ("unreviewed") must be tier 1, ordered before the pending one.
        assert live is not None
        assert live["transaction"]["id"] == unreviewed_tx.id
        assert live["transaction"]["pending"] is False


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

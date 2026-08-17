from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.contracts import AgentActionConfirmationBlock, AgentPageContext
from app.agent.runtime import ReadOnlyAgentOrchestrator
from app.agent.service import UnifiedAgentService
from app.config import Settings
from app.db import Base
from app.models import (
    AgentActionProposal,
    AgentRun,
    AgentToolCall,
    ExpenseTransaction,
    FinancialOperation,
    PlaidItem,
    SplitwiseIntegration,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.services.splitwise_service import SplitwiseService
from app.tenancy import TenantContext, set_session_tenant

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_AGENT_ACTION_SMOKE") != "1",
    reason="Live OpenAI controlled-action smoke is opt-in and never part of deterministic CI",
)


def test_live_openai_selects_both_controlled_action_proposals_without_execution(
    monkeypatch,
    request: pytest.FixtureRequest,
) -> None:
    base_settings = Settings()
    if not base_settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY is not configured")
    settings = base_settings.model_copy(
        update={
            "agent_enabled": True,
            "agent_read_tools_enabled": True,
            "agent_write_actions_enabled": True,
            "agent_proactive_enabled": False,
            "agent_purchasing_enabled": False,
        }
    )
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    provider_create_calls: list[dict] = []
    monkeypatch.setattr(
        SplitwiseService,
        "get_friends",
        lambda _self: [
            {
                "id": 200,
                "first_name": "Gunjan",
                "last_name": "Patil",
                "email": "gunjan@example.test",
            }
        ],
    )
    monkeypatch.setattr(SplitwiseService, "get_groups", lambda _self: [])

    def forbid_provider_create(_self, payload):
        provider_create_calls.append(dict(payload))
        raise AssertionError("proposal smoke must never execute a provider mutation")

    monkeypatch.setattr(SplitwiseService, "create_expense", forbid_provider_create)

    try:
        with factory() as db:
            user = User(email="live-day8@example.test", display_name="Live Day 8")
            db.add(user)
            db.flush()
            workspace = Workspace(name="Live Day 8 workspace", created_by_user_id=user.id)
            db.add(workspace)
            db.flush()
            db.add(
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    role="owner",
                    is_default=True,
                )
            )
            item = PlaidItem(
                workspace_id=workspace.id,
                item_id="live-day8-item",
                owner_user_id=user.id,
            )
            db.add(item)
            db.flush()
            transaction = ExpenseTransaction(
                workspace_id=workspace.id,
                plaid_transaction_id="live-day8-transaction",
                plaid_item_id=item.id,
                merchant_name="Synthetic Costco",
                name="Synthetic Costco",
                amount_cents=8_420,
                iso_currency_code="USD",
                date=date(2026, 8, 16),
                pending=False,
                category="General Merchandise",
                status="ask_user",
            )
            db.add(transaction)
            db.add(
                SplitwiseIntegration(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    credentials_encrypted="synthetic-not-a-provider-secret",
                    splitwise_user_id="100",
                    display_name="Live Day 8",
                    email="live-day8@example.test",
                    verified_at=datetime(2026, 8, 1, tzinfo=UTC),
                    enabled=True,
                )
            )
            db.commit()
            set_session_tenant(db, TenantContext(user.id, workspace.id))

            for name, prompt, expected_action, expected_tool in (
                (
                    "personal",
                    "Mark this transaction personal.",
                    "mark_transaction_personal",
                    "propose_mark_transaction_personal",
                ),
                (
                    "splitwise",
                    "Split this transaction with Gunjan.",
                    "post_splitwise_expense",
                    "propose_post_splitwise_expense",
                ),
            ):
                conversation = UnifiedAgentService(db, settings).create_conversation(
                    owner_user_id=user.id,
                    title=f"Live Day 8 {name}",
                )
                turn = asyncio.run(
                    ReadOnlyAgentOrchestrator(
                        db,
                        settings=settings,
                        now=lambda: datetime(2026, 8, 16, 12, tzinfo=UTC),
                    ).run_turn(
                        conversation.public_id,
                        owner_user_id=user.id,
                        text=prompt,
                        client_message_id=f"live-day8-{name}-1",
                        page_context=AgentPageContext.model_validate(
                            {
                                "surface": "expense_review",
                                "entity": {
                                    "kind": "transaction",
                                    "public_id": str(transaction.id),
                                },
                            }
                        ),
                    )
                )

                assert turn.run.status == "completed"
                response = turn.assistant_message.structured_response
                assert response is not None
                blocks = [
                    block
                    for block in response.blocks
                    if isinstance(block, AgentActionConfirmationBlock)
                ]
                assert len(blocks) == 1
                assert blocks[0].action == expected_action
                assert blocks[0].status == "awaiting_confirmation"
                persisted_run = db.scalar(
                    select(AgentRun).where(AgentRun.public_id == turn.run.public_id)
                )
                assert persisted_run is not None
                calls = list(
                    db.scalars(
                        select(AgentToolCall)
                        .where(AgentToolCall.run_id == persisted_run.id)
                        .order_by(AgentToolCall.sequence)
                    )
                )
                assert [(call.tool_name, call.status) for call in calls] == [
                    (expected_tool, "proposed")
                ]
                assert (turn.run.input_tokens or 0) > 0
                assert (turn.run.output_tokens or 0) > 0
                request.node.user_properties.extend(
                    (
                        (f"{name}_proposal_latency_ms", persisted_run.latency_ms),
                        (f"{name}_proposal_input_tokens", turn.run.input_tokens),
                        (f"{name}_proposal_output_tokens", turn.run.output_tokens),
                        (f"{name}_proposal_total_tokens", turn.run.total_tokens),
                        (
                            f"{name}_proposal_estimated_cost_micros",
                            persisted_run.estimated_cost_micros,
                        ),
                    )
                )

            proposals = list(db.scalars(select(AgentActionProposal)))
            assert len(proposals) == 2
            assert {proposal.status for proposal in proposals} == {"awaiting_confirmation"}
            assert transaction.status == "ask_user"
            assert provider_create_calls == []
            assert db.scalar(select(func.count(FinancialOperation.id))) == 0
    finally:
        engine.dispose()

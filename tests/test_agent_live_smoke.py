from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.contracts import AgentReplenishmentSummaryBlock, AgentSpendingSummaryBlock
from app.agent.runtime import ReadOnlyAgentOrchestrator
from app.agent.service import UnifiedAgentService
from app.config import Settings
from app.db import Base
from app.models import (
    AgentRun,
    AgentToolCall,
    ExpenseTransaction,
    HouseholdItem,
    PlaidItem,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.tenancy import TenantContext, set_session_tenant

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_AGENT_SMOKE") != "1",
    reason="Live OpenAI smoke is opt-in and never part of deterministic CI",
)


def test_live_openai_read_only_turns_use_canonical_tools(record_property) -> None:
    base_settings = Settings()
    if not base_settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY is not configured")
    settings = base_settings.model_copy(
        update={
            "agent_enabled": True,
            "agent_read_tools_enabled": True,
            "agent_write_actions_enabled": False,
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

    try:
        with factory() as db:
            user = User(email="live-agent-smoke@example.test", display_name="Live smoke")
            db.add(user)
            db.flush()
            workspace = Workspace(name="Live smoke workspace", created_by_user_id=user.id)
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
                item_id="live-agent-smoke-item",
                owner_user_id=user.id,
            )
            db.add(item)
            db.flush()
            db.add(
                ExpenseTransaction(
                    workspace_id=workspace.id,
                    plaid_transaction_id="live-agent-smoke-transaction",
                    plaid_item_id=item.id,
                    merchant_name="Synthetic Cafe",
                    name="Synthetic Cafe",
                    amount_cents=4_321,
                    iso_currency_code="USD",
                    date=date(2026, 8, 10),
                    pending=False,
                    category="Restaurants",
                    status="personal",
                )
            )
            db.add(
                HouseholdItem(
                    workspace_id=workspace.id,
                    name="Synthetic laundry detergent",
                    quantity="1",
                    unit="bottle",
                    cadence_days=21,
                    last_acquired_at=datetime(2026, 7, 20, 12, tzinfo=UTC),
                )
            )
            db.commit()
            set_session_tenant(db, TenantContext(user.id, workspace.id))
            conversation = UnifiedAgentService(db, settings).create_conversation(
                owner_user_id=user.id,
                title="Live provider smoke",
            )

            turn = asyncio.run(
                ReadOnlyAgentOrchestrator(
                    db,
                    settings=settings,
                    now=lambda: datetime(2026, 8, 14, 12, tzinfo=UTC),
                ).run_turn(
                    conversation.public_id,
                    owner_user_id=user.id,
                    text="How much did I spend from August 1 through August 14, 2026?",
                    client_message_id="live-openai-smoke-1",
                )
            )

            assert turn.run.status == "completed"
            response = turn.assistant_message.structured_response
            assert response is not None
            summaries = [
                block for block in response.blocks if isinstance(block, AgentSpendingSummaryBlock)
            ]
            assert len(summaries) == 1
            assert summaries[0].total_cents == 4_321
            tool_calls = list(db.scalars(select(AgentToolCall).order_by(AgentToolCall.id)))
            assert [(tool_call.tool_name, tool_call.status) for tool_call in tool_calls] == [
                (
                    "get_spending_insights",
                    "completed",
                )
            ]
            persisted_run = db.scalar(
                select(AgentRun).where(AgentRun.public_id == turn.run.public_id)
            )
            assert persisted_run is not None
            assert persisted_run.latency_ms is not None
            assert (turn.run.input_tokens or 0) > 0
            assert (turn.run.output_tokens or 0) > 0
            assert tool_calls[0].latency_ms is not None
            record_property("spending_run_latency_ms", persisted_run.latency_ms)
            record_property("spending_tool_latency_ms", tool_calls[0].latency_ms)
            record_property("spending_input_tokens", turn.run.input_tokens)
            record_property("spending_output_tokens", turn.run.output_tokens)

            household_conversation = UnifiedAgentService(db, settings).create_conversation(
                owner_user_id=user.id,
                title="Live household smoke",
            )
            household_turn = asyncio.run(
                ReadOnlyAgentOrchestrator(
                    db,
                    settings=settings,
                    now=lambda: datetime(2026, 8, 14, 12, tzinfo=UTC),
                ).run_turn(
                    household_conversation.public_id,
                    owner_user_id=user.id,
                    text="What household items am I likely to need this week?",
                    client_message_id="live-openai-smoke-household-1",
                )
            )

            assert household_turn.run.status == "completed"
            household_response = household_turn.assistant_message.structured_response
            assert household_response is not None
            household_blocks = [
                block
                for block in household_response.blocks
                if isinstance(block, AgentReplenishmentSummaryBlock)
            ]
            assert len(household_blocks) == 1
            assert [item.name for item in household_blocks[0].items] == [
                "Synthetic laundry detergent"
            ]
            tool_calls = list(db.scalars(select(AgentToolCall).order_by(AgentToolCall.id)))
            assert [(tool_call.tool_name, tool_call.status) for tool_call in tool_calls] == [
                ("get_spending_insights", "completed"),
                ("get_household_replenishment", "completed"),
            ]
            persisted_household_run = db.scalar(
                select(AgentRun).where(AgentRun.public_id == household_turn.run.public_id)
            )
            assert persisted_household_run is not None
            assert persisted_household_run.latency_ms is not None
            assert (household_turn.run.input_tokens or 0) > 0
            assert (household_turn.run.output_tokens or 0) > 0
            assert tool_calls[1].latency_ms is not None
            record_property(
                "household_run_latency_ms",
                persisted_household_run.latency_ms,
            )
            record_property("household_tool_latency_ms", tool_calls[1].latency_ms)
            record_property("household_input_tokens", household_turn.run.input_tokens)
            record_property("household_output_tokens", household_turn.run.output_tokens)
    finally:
        engine.dispose()

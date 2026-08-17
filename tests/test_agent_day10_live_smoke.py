from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.contracts import AgentLifestyleSummaryBlock
from app.agent.runtime import ReadOnlyAgentOrchestrator
from app.agent.service import UnifiedAgentService
from app.config import Settings
from app.db import Base
from app.models import (
    AgentRun,
    AgentToolCall,
    ExpenseTransaction,
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


def test_live_lifestyle_query_selects_one_canonical_read_tool() -> None:
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
            user = User(email="day10-live@example.test", display_name="Day 10 live")
            db.add(user)
            db.flush()
            workspace = Workspace(name="Day 10 live", created_by_user_id=user.id)
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
                item_id="day10-live-item",
                owner_user_id=user.id,
            )
            db.add(item)
            db.flush()
            for provider_id, amount, occurred_on in (
                ("Live Coffee One", 700, date(2026, 8, 4)),
                ("Live Coffee Two", 900, date(2026, 8, 11)),
                ("Live Coffee Credit", -200, date(2026, 8, 12)),
            ):
                db.add(
                    ExpenseTransaction(
                        workspace_id=workspace.id,
                        plaid_transaction_id=provider_id,
                        plaid_item_id=item.id,
                        account_id="day10-live-card",
                        merchant_name=provider_id,
                        name=provider_id,
                        amount_cents=amount,
                        iso_currency_code="USD",
                        date=occurred_on,
                        pending=False,
                        category="FOOD_AND_DRINK / COFFEE",
                        status="personal",
                    )
                )
            db.commit()
            set_session_tenant(db, TenantContext(user.id, workspace.id))
            conversation = UnifiedAgentService(db, settings).create_conversation(
                owner_user_id=user.id,
                title="Day 10 lifestyle live smoke",
            )
            turn = asyncio.run(
                ReadOnlyAgentOrchestrator(
                    db,
                    settings=settings,
                    now=lambda: datetime(2026, 8, 17, 12, tzinfo=UTC),
                ).run_turn(
                    conversation.public_id,
                    owner_user_id=user.id,
                    text=(
                        "How much did I spend on coffee from 2026-08-01 through "
                        "2026-08-16, and how many coffee purchases were there?"
                    ),
                    client_message_id="day10-live-1",
                )
            )

            assert turn.run.status == "completed"
            response = turn.assistant_message.structured_response
            assert response is not None
            run = db.scalar(select(AgentRun).where(AgentRun.public_id == turn.run.public_id))
            assert run is not None
            calls = list(
                db.scalars(
                    select(AgentToolCall)
                    .where(AgentToolCall.run_id == run.id)
                    .order_by(AgentToolCall.sequence)
                )
            )
            assert [(call.tool_name, call.status) for call in calls] == [
                ("get_lifestyle_dining_insights", "completed")
            ]
            blocks = [
                block for block in response.blocks if isinstance(block, AgentLifestyleSummaryBlock)
            ]
            assert len(blocks) == 1
            block = blocks[0]
            assert block.activity_type == "coffee"
            assert block.total_cents == 1_600
            assert block.credits_cents == 200
            assert block.transaction_count == 2
            assert calls[0].tool_version == "1.0"
            assert calls[0].arguments_json["start_date"] == "2026-08-01"
            assert calls[0].arguments_json["end_date"] == "2026-08-16"
            assert calls[0].arguments_json["activity_type"] == "coffee"
            assert run.input_tokens is not None and run.input_tokens > 0
            assert run.output_tokens is not None and run.output_tokens > 0
    finally:
        engine.dispose()

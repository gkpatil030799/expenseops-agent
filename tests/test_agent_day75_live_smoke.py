from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.contracts import AgentSpendingSummaryBlock
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


def _transaction(
    *,
    workspace_id: int,
    item_id: int,
    provider_id: str,
    amount_cents: int,
    occurred_on: date,
    category: str,
) -> ExpenseTransaction:
    return ExpenseTransaction(
        workspace_id=workspace_id,
        plaid_transaction_id=provider_id,
        plaid_item_id=item_id,
        account_id="day75-live-card",
        merchant_name=provider_id,
        name=provider_id,
        amount_cents=amount_cents,
        iso_currency_code="USD",
        date=occurred_on,
        pending=False,
        category=category,
        status="personal",
    )


def test_live_day75_purchase_spend_comparison_and_category_ranking() -> None:
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
            user = User(email="day75-live@example.test", display_name="Day 7.5 live")
            db.add(user)
            db.flush()
            workspace = Workspace(name="Day 7.5 live", created_by_user_id=user.id)
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
                item_id="day75-live-item",
                owner_user_id=user.id,
            )
            db.add(item)
            db.flush()
            db.add_all(
                [
                    _transaction(
                        workspace_id=workspace.id,
                        item_id=item.id,
                        provider_id="current-food-purchase",
                        amount_cents=50_000,
                        occurred_on=date(2026, 8, 10),
                        category="FOOD_AND_DRINK / FOOD_AND_DRINK_RESTAURANT",
                    ),
                    _transaction(
                        workspace_id=workspace.id,
                        item_id=item.id,
                        provider_id="current-food-credit",
                        amount_cents=-10_000,
                        occurred_on=date(2026, 8, 11),
                        category="FOOD_AND_DRINK / FOOD_AND_DRINK_RESTAURANT",
                    ),
                    _transaction(
                        workspace_id=workspace.id,
                        item_id=item.id,
                        provider_id="current-lifestyle-purchase",
                        amount_cents=20_000,
                        occurred_on=date(2026, 8, 12),
                        category=("GENERAL_MERCHANDISE / GENERAL_MERCHANDISE_ONLINE_MARKETPLACES"),
                    ),
                    _transaction(
                        workspace_id=workspace.id,
                        item_id=item.id,
                        provider_id="previous-food-purchase",
                        amount_cents=40_000,
                        occurred_on=date(2026, 8, 3),
                        category="FOOD_AND_DRINK / FOOD_AND_DRINK_RESTAURANT",
                    ),
                    _transaction(
                        workspace_id=workspace.id,
                        item_id=item.id,
                        provider_id="previous-food-credit",
                        amount_cents=-5_000,
                        occurred_on=date(2026, 8, 4),
                        category="FOOD_AND_DRINK / FOOD_AND_DRINK_RESTAURANT",
                    ),
                ]
            )
            db.commit()
            set_session_tenant(db, TenantContext(user.id, workspace.id))

            comparison_conversation = UnifiedAgentService(db, settings).create_conversation(
                owner_user_id=user.id,
                title="Day 7.5 comparison live smoke",
            )
            comparison_turn = asyncio.run(
                ReadOnlyAgentOrchestrator(
                    db,
                    settings=settings,
                    now=lambda: datetime(2026, 8, 16, 12, tzinfo=UTC),
                ).run_turn(
                    comparison_conversation.public_id,
                    owner_user_id=user.id,
                    text="are my spendings increased compared to last week ?",
                    client_message_id="day75-live-comparison-1",
                )
            )

            assert comparison_turn.run.status == "completed"
            comparison_response = comparison_turn.assistant_message.structured_response
            assert comparison_response is not None
            comparison_blocks = [
                block
                for block in comparison_response.blocks
                if isinstance(block, AgentSpendingSummaryBlock)
            ]
            assert len(comparison_blocks) == 1
            comparison = comparison_blocks[0]
            assert comparison.spend_basis == "card"
            assert comparison.total_cents == 70_000
            assert comparison.previous_total_cents == 40_000
            assert comparison.credits_cents == 10_000
            assert comparison.previous_credits_cents == 5_000
            assert comparison.change_percent == 75.0
            assert comparison.total_cents >= 0
            comparison_run = db.scalar(
                select(AgentRun).where(AgentRun.public_id == comparison_turn.run.public_id)
            )
            assert comparison_run is not None
            comparison_calls = list(
                db.scalars(
                    select(AgentToolCall)
                    .where(AgentToolCall.run_id == comparison_run.id)
                    .order_by(AgentToolCall.sequence)
                )
            )
            assert [(call.tool_name, call.status) for call in comparison_calls] == [
                ("get_spending_insights", "completed")
            ]
            assert comparison_calls[0].tool_version == "1.2"
            comparison_arguments = comparison_calls[0].arguments_json
            assert comparison_arguments["spend_basis"] in {None, "card"}
            assert {
                key: value for key, value in comparison_arguments.items() if key != "spend_basis"
            } == {
                "start_date": "2026-08-10",
                "end_date": "2026-08-16",
                "account_id": None,
                "category": None,
                "merchant": None,
                "review_type": None,
                "comparison_mode": "same_weekdays_last_week",
                "currency_code": None,
            }

            category_conversation = UnifiedAgentService(db, settings).create_conversation(
                owner_user_id=user.id,
                title="Day 7.5 category live smoke",
            )
            category_turn = asyncio.run(
                ReadOnlyAgentOrchestrator(
                    db,
                    settings=settings,
                    now=lambda: datetime(2026, 8, 16, 12, tzinfo=UTC),
                ).run_turn(
                    category_conversation.public_id,
                    owner_user_id=user.id,
                    text="what category did i spend the most on this month?",
                    client_message_id="day75-live-category-1",
                )
            )

            assert category_turn.run.status == "completed"
            category_response = category_turn.assistant_message.structured_response
            assert category_response is not None
            category_blocks = [
                block
                for block in category_response.blocks
                if isinstance(block, AgentSpendingSummaryBlock)
            ]
            assert len(category_blocks) == 1
            category_summary = category_blocks[0]
            assert category_summary.total_cents == 110_000
            assert category_summary.credits_cents == 15_000
            assert category_summary.top_categories[0].name == "Food & Dining"
            assert category_summary.top_categories[0].amount_cents == 90_000
            category_run = db.scalar(
                select(AgentRun).where(AgentRun.public_id == category_turn.run.public_id)
            )
            assert category_run is not None
            category_calls = list(
                db.scalars(
                    select(AgentToolCall)
                    .where(AgentToolCall.run_id == category_run.id)
                    .order_by(AgentToolCall.sequence)
                )
            )
            assert [(call.tool_name, call.status) for call in category_calls] == [
                ("get_spending_insights", "completed")
            ]
            assert category_calls[0].arguments_json["start_date"] == "2026-08-01"
            assert category_calls[0].arguments_json["end_date"] == "2026-08-16"
            assert category_calls[0].arguments_json["spend_basis"] in {None, "card"}
            assert category_calls[0].arguments_json["comparison_mode"] is None
    finally:
        engine.dispose()

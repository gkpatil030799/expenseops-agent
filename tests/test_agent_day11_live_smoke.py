from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.action_tools import ITEMIZED_RECEIPT_SPLIT_TOOL_NAME
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
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReceiptItemMatchStatus,
    ReceiptLineClassification,
    ReceiptParseStatus,
    SplitwiseIntegration,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.services.splitwise_service import SplitwiseService
from app.tenancy import TenantContext, set_session_tenant

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_AGENT_ACTION_SMOKE") != "1",
    reason="Live OpenAI itemized-action smoke is opt-in and never part of deterministic CI",
)


def test_live_openai_prepares_exact_itemized_receipt_proposal_without_execution(
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
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    create_calls: list[dict] = []
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

    def forbid_create(_self, payload):
        create_calls.append(dict(payload))
        raise AssertionError("proposal smoke must never create a Splitwise expense")

    monkeypatch.setattr(SplitwiseService, "create_expense", forbid_create)

    try:
        Base.metadata.create_all(engine)
        with factory() as db:
            user = User(email="day11-live@example.test", display_name="Day 11 live")
            db.add(user)
            db.flush()
            workspace = Workspace(name="Day 11 live", created_by_user_id=user.id)
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
            plaid_item = PlaidItem(
                workspace_id=workspace.id,
                item_id="day11-live-item",
                owner_user_id=user.id,
            )
            db.add(plaid_item)
            db.flush()
            transaction = ExpenseTransaction(
                workspace_id=workspace.id,
                plaid_transaction_id="day11-live-transaction",
                plaid_item_id=plaid_item.id,
                merchant_name="Synthetic Dinner House",
                name="Synthetic Dinner House",
                amount_cents=9_000,
                iso_currency_code="USD",
                date=date(2026, 8, 16),
                pending=False,
                category="FOOD_AND_DRINK / RESTAURANT",
                status="ask_user",
            )
            db.add(transaction)
            db.flush()
            receipt = PurchaseReceipt(
                workspace_id=workspace.id,
                source="manual",
                source_external_id="day11-live-receipt",
                merchant_raw="Synthetic Dinner House",
                merchant_normalized="Synthetic Dinner House",
                purchased_at=datetime(2026, 8, 16, tzinfo=UTC),
                subtotal_cents=7_500,
                tax_cents=600,
                total_cents=9_000,
                currency="USD",
                transaction_id=transaction.id,
                parse_status=ReceiptParseStatus.CONFIRMED.value,
                confirmed_at=datetime(2026, 8, 16, tzinfo=UTC),
            )
            db.add(receipt)
            db.flush()
            receipt.items = [
                PurchaseReceiptItem(
                    raw_name=name,
                    normalized_name=name.casefold(),
                    line_total_cents=amount,
                    classification=ReceiptLineClassification.DINING_OR_EXPERIENCE.value,
                    match_status=ReceiptItemMatchStatus.IRRELEVANT.value,
                )
                for name, amount in (
                    ("Paneer tikka", 1_600),
                    ("Chicken biryani", 2_100),
                    ("Cocktails", 2_800),
                    ("Dessert", 1_000),
                )
            ]
            db.add(
                SplitwiseIntegration(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    credentials_encrypted="synthetic-not-a-provider-secret",
                    splitwise_user_id="100",
                    display_name="Day 11 live",
                    email="day11-live@example.test",
                    verified_at=datetime(2026, 8, 1, tzinfo=UTC),
                    enabled=True,
                )
            )
            db.commit()
            set_session_tenant(db, TenantContext(user.id, workspace.id))
            conversation = UnifiedAgentService(db, settings).create_conversation(
                owner_user_id=user.id,
                title="Day 11 itemized live smoke",
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
                        "On this receipt, Paneer tikka was mine. Chicken biryani and Cocktails "
                        "were Gunjan's. Dessert was shared by both of us. Split both tax and tip "
                        "proportionally to assigned item subtotals. Prepare the itemized split "
                        "for confirmation."
                    ),
                    client_message_id="day11-live-itemized-1",
                    page_context=AgentPageContext.model_validate(
                        {
                            "surface": "household_receipts",
                            "entity": {"kind": "receipt", "public_id": str(receipt.id)},
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
            run = db.scalar(select(AgentRun).where(AgentRun.public_id == turn.run.public_id))
            assert run is not None
            calls = list(
                db.scalars(
                    select(AgentToolCall)
                    .where(AgentToolCall.run_id == run.id)
                    .order_by(AgentToolCall.sequence)
                )
            )
            assert len(blocks) == 1, (
                response.model_dump(mode="json"),
                [
                    (call.tool_name, call.status, call.error_code, call.arguments_json)
                    for call in calls
                ],
            )
            assert blocks[0].action == "post_itemized_receipt_split"
            assert blocks[0].status == "awaiting_confirmation"
            assert [(call.tool_name, call.status) for call in calls] == [
                (ITEMIZED_RECEIPT_SPLIT_TOOL_NAME, "proposed")
            ]
            proposal = db.scalar(
                select(AgentActionProposal).where(AgentActionProposal.run_id == run.id)
            )
            assert proposal is not None
            parameters = proposal.normalized_parameters_json
            assert [person["owed_cents"] for person in parameters["participants"]] == [
                2_520,
                6_480,
            ]
            assert sum(person["owed_cents"] for person in parameters["participants"]) == 9_000
            assert transaction.status == "ask_user"
            assert create_calls == []
            assert db.scalar(select(func.count(FinancialOperation.id))) == 0
            assert (run.input_tokens or 0) > 0
            assert (run.output_tokens or 0) > 0
            request.node.user_properties.extend(
                (
                    ("itemized_proposal_latency_ms", run.latency_ms),
                    ("itemized_proposal_input_tokens", run.input_tokens),
                    ("itemized_proposal_output_tokens", run.output_tokens),
                    ("itemized_proposal_total_tokens", run.total_tokens),
                    ("itemized_proposal_estimated_cost_micros", run.estimated_cost_micros),
                )
            )
    finally:
        engine.dispose()

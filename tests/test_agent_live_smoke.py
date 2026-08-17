from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.contracts import (
    AgentAttentionSummaryBlock,
    AgentDealListBlock,
    AgentPageContext,
    AgentReplenishmentSummaryBlock,
    AgentSpendingSummaryBlock,
    AgentTextBlock,
    AgentTransactionListBlock,
)
from app.agent.runtime import (
    MAX_AGENT_TOOL_CALLS,
    MAX_AGENT_TURNS,
    ReadOnlyAgentOrchestrator,
)
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
            transaction = ExpenseTransaction(
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
                status="ask_user",
            )
            db.add(transaction)
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
                    text="Why did this increase?",
                    client_message_id="live-openai-smoke-1",
                    page_context=AgentPageContext.model_validate(
                        {
                            "surface": "expense_insights",
                            "filters": {
                                "start_date": "2026-08-01",
                                "end_date": "2026-08-14",
                                "date_preset": "custom",
                                "category": "Food & Dining",
                                "spend_basis": "card",
                            },
                        }
                    ),
                )
            )

            assert turn.run.status == "completed"
            response = turn.assistant_message.structured_response
            assert response is not None
            summaries = [
                block for block in response.blocks if isinstance(block, AgentSpendingSummaryBlock)
            ]
            assert len(summaries) == 1
            tool_calls = list(db.scalars(select(AgentToolCall).order_by(AgentToolCall.id)))
            assert [(tool_call.tool_name, tool_call.status) for tool_call in tool_calls] == [
                (
                    "get_spending_insights",
                    "completed",
                )
            ]
            assert tool_calls[0].arguments_json == {
                "start_date": "2026-08-01",
                "end_date": "2026-08-14",
                "account_id": None,
                "category": "Food & Dining",
                "merchant": None,
                "review_type": None,
                "spend_basis": "card",
                "currency_code": None,
            }
            assert summaries[0].total_cents == 4_321, tool_calls[0].arguments_json
            persisted_run = db.scalar(
                select(AgentRun).where(AgentRun.public_id == turn.run.public_id)
            )
            assert persisted_run is not None
            assert persisted_run.latency_ms is not None
            assert persisted_run.page_context_json == {
                "schema_version": "1.0",
                "surface": "expense_insights",
                "filters": {
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-14",
                    "date_preset": "custom",
                    "account_id": None,
                    "category": "Food & Dining",
                    "merchant": None,
                    "status": None,
                    "currency_code": None,
                    "spend_basis": "card",
                    "query": None,
                },
                "entity": None,
            }
            assert (turn.run.input_tokens or 0) > 0
            assert (turn.run.output_tokens or 0) > 0
            assert tool_calls[0].latency_ms is not None
            record_property("spending_run_latency_ms", persisted_run.latency_ms)
            record_property("spending_tool_latency_ms", tool_calls[0].latency_ms)
            record_property("spending_input_tokens", turn.run.input_tokens)
            record_property("spending_output_tokens", turn.run.output_tokens)

            transaction_conversation = UnifiedAgentService(db, settings).create_conversation(
                owner_user_id=user.id,
                title="Live contextual transaction smoke",
            )
            transaction_turn = asyncio.run(
                ReadOnlyAgentOrchestrator(
                    db,
                    settings=settings,
                    now=lambda: datetime(2026, 8, 14, 12, tzinfo=UTC),
                ).run_turn(
                    transaction_conversation.public_id,
                    owner_user_id=user.id,
                    text="Tell me more about this transaction.",
                    client_message_id="live-openai-smoke-transaction-1",
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

            assert transaction_turn.run.status == "completed"
            transaction_response = transaction_turn.assistant_message.structured_response
            assert transaction_response is not None
            transaction_blocks = [
                block
                for block in transaction_response.blocks
                if isinstance(block, AgentTransactionListBlock)
            ]
            assert len(transaction_blocks) == 1
            assert [row.public_id for row in transaction_blocks[0].transactions] == [
                str(transaction.id)
            ]
            transaction_tool_calls = list(
                db.scalars(select(AgentToolCall).order_by(AgentToolCall.id))
            )
            assert len(transaction_tool_calls) == 2
            assert transaction_tool_calls[1].tool_name == "search_transactions"
            assert transaction_tool_calls[1].latency_ms is not None
            persisted_transaction_run = db.scalar(
                select(AgentRun).where(AgentRun.public_id == transaction_turn.run.public_id)
            )
            assert persisted_transaction_run is not None
            assert persisted_transaction_run.latency_ms is not None
            assert (transaction_turn.run.input_tokens or 0) > 0
            assert (transaction_turn.run.output_tokens or 0) > 0
            record_property(
                "transaction_run_latency_ms",
                persisted_transaction_run.latency_ms,
            )
            record_property(
                "transaction_tool_latency_ms",
                transaction_tool_calls[1].latency_ms,
            )
            record_property("transaction_input_tokens", transaction_turn.run.input_tokens)
            record_property("transaction_output_tokens", transaction_turn.run.output_tokens)

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
                ("search_transactions", "completed"),
                ("get_household_replenishment", "completed"),
            ]
            persisted_household_run = db.scalar(
                select(AgentRun).where(AgentRun.public_id == household_turn.run.public_id)
            )
            assert persisted_household_run is not None
            assert persisted_household_run.latency_ms is not None
            assert (household_turn.run.input_tokens or 0) > 0
            assert (household_turn.run.output_tokens or 0) > 0
            assert tool_calls[2].latency_ms is not None
            record_property(
                "household_run_latency_ms",
                persisted_household_run.latency_ms,
            )
            record_property("household_tool_latency_ms", tool_calls[2].latency_ms)
            record_property("household_input_tokens", household_turn.run.input_tokens)
            record_property("household_output_tokens", household_turn.run.output_tokens)

            multi_conversation = UnifiedAgentService(db, settings).create_conversation(
                owner_user_id=user.id,
                title="Live multi-evidence smoke",
            )
            multi_turn = asyncio.run(
                ReadOnlyAgentOrchestrator(
                    db,
                    settings=settings,
                    now=lambda: datetime(2026, 8, 14, 12, tzinfo=UTC),
                ).run_turn(
                    multi_conversation.public_id,
                    owner_user_id=user.id,
                    text=(
                        "I need both parts: compare my USD Restaurants card spending from "
                        "2026-08-01 through 2026-08-14 with its comparable prior period, "
                        "and list the matching non-pending transactions as supporting detail."
                    ),
                    client_message_id="live-openai-smoke-multi-evidence-1",
                )
            )

            assert multi_turn.run.status == "completed"
            multi_response = multi_turn.assistant_message.structured_response
            assert multi_response is not None
            persisted_multi_run = db.scalar(
                select(AgentRun).where(AgentRun.public_id == multi_turn.run.public_id)
            )
            assert persisted_multi_run is not None
            multi_tool_calls = list(
                db.scalars(
                    select(AgentToolCall)
                    .where(AgentToolCall.run_id == persisted_multi_run.id)
                    .order_by(AgentToolCall.sequence)
                )
            )
            assert len(multi_tool_calls) == 2
            assert {call.tool_name for call in multi_tool_calls} == {
                "get_spending_insights",
                "search_transactions",
            }
            assert all(call.status == "completed" for call in multi_tool_calls)
            assert len(multi_tool_calls) <= MAX_AGENT_TOOL_CALLS == 3
            calls_by_name = {call.tool_name: call for call in multi_tool_calls}
            assert calls_by_name["get_spending_insights"].arguments_json == {
                "start_date": "2026-08-01",
                "end_date": "2026-08-14",
                "account_id": None,
                "category": "Restaurants",
                "merchant": None,
                "review_type": None,
                "spend_basis": "card",
                "currency_code": "USD",
            }
            assert calls_by_name["search_transactions"].arguments_json == {
                "start_date": "2026-08-01",
                "end_date": "2026-08-14",
                "transaction_id": None,
                "merchant": None,
                "category": "Restaurants",
                "review_type": None,
                "review_status": None,
                "min_amount_cents": None,
                "max_amount_cents": None,
                "currency_code": "USD",
                "include_pending": False,
                "limit": 20,
            }
            call_diagnostics = [(call.tool_name, call.status) for call in multi_tool_calls]
            spending_blocks = [
                block
                for block in multi_response.blocks
                if isinstance(block, AgentSpendingSummaryBlock)
            ]
            transaction_blocks = [
                block
                for block in multi_response.blocks
                if isinstance(block, AgentTransactionListBlock)
            ]
            assert len(spending_blocks) == 1, call_diagnostics
            assert len(transaction_blocks) == 1, call_diagnostics
            assert not any(isinstance(block, AgentDealListBlock) for block in multi_response.blocks)
            assert any(
                isinstance(block, AgentTextBlock) and "supporting detail" in block.text
                for block in multi_response.blocks
            )

            metrics = persisted_multi_run.metadata_json
            assert metrics["sdk_turn_count"] <= MAX_AGENT_TURNS == 4
            assert metrics["provider_request_count"] > 0
            assert metrics["sdk_runtime_latency_ms"] >= 0
            assert metrics["provider_orchestration_latency_ms_estimate"] >= 0
            assert metrics["total_tool_latency_ms"] == sum(
                call.latency_ms or 0 for call in multi_tool_calls
            )
            assert metrics["tool_call_count"] == 2
            assert metrics["evidence_set_count"] == 2
            assert metrics["failed_tool_call_count"] == 0
            assert metrics["completion_state"] == "complete"
            assert metrics["composition_latency_ms"] >= 0
            assert metrics["canonical_response_bytes"] > 0
            assert metrics["response_payload_bytes"] >= metrics["canonical_response_bytes"]
            assert (multi_turn.run.input_tokens or 0) > 0
            assert (multi_turn.run.output_tokens or 0) > 0
            assert multi_turn.run.total_tokens == (
                (multi_turn.run.input_tokens or 0) + (multi_turn.run.output_tokens or 0)
            )

            # Aggregate observability must not copy synthetic prompts or tool payloads.
            serialized_metrics = json.dumps(metrics, sort_keys=True)
            assert "Synthetic Cafe" not in serialized_metrics
            assert "I need both parts" not in serialized_metrics
            assert not any(
                forbidden in key.casefold()
                for key in metrics
                for forbidden in ("raw_prompt", "raw_payload", "tool_output", "provider_response")
            )

            record_property("multi_run_latency_ms", persisted_multi_run.latency_ms)
            record_property(
                "multi_sdk_runtime_latency_ms",
                metrics["sdk_runtime_latency_ms"],
            )
            record_property(
                "multi_provider_orchestration_latency_ms_estimate",
                metrics["provider_orchestration_latency_ms_estimate"],
            )
            record_property(
                "multi_tool_latency_ms",
                metrics["total_tool_latency_ms"],
            )
            tool_latencies = {call.tool_name: call.latency_ms or 0 for call in multi_tool_calls}
            record_property(
                "multi_spending_tool_latency_ms",
                tool_latencies["get_spending_insights"],
            )
            record_property(
                "multi_transactions_tool_latency_ms",
                tool_latencies["search_transactions"],
            )
            record_property("multi_sdk_turn_count", metrics["sdk_turn_count"])
            record_property(
                "multi_provider_request_count",
                metrics["provider_request_count"],
            )
            record_property("multi_tool_call_count", metrics["tool_call_count"])
            record_property("multi_evidence_set_count", metrics["evidence_set_count"])
            record_property(
                "multi_failed_tool_call_count",
                metrics["failed_tool_call_count"],
            )
            record_property("multi_completion_state", metrics["completion_state"])
            record_property(
                "multi_composition_latency_ms",
                metrics["composition_latency_ms"],
            )
            record_property(
                "multi_canonical_response_bytes",
                metrics["canonical_response_bytes"],
            )
            record_property(
                "multi_response_payload_bytes",
                metrics["response_payload_bytes"],
            )
            record_property("multi_input_tokens", multi_turn.run.input_tokens)
            record_property("multi_output_tokens", multi_turn.run.output_tokens)
            record_property("multi_total_tokens", multi_turn.run.total_tokens)

            attention_conversation = UnifiedAgentService(db, settings).create_conversation(
                owner_user_id=user.id,
                title="Live attention planning smoke",
            )
            attention_turn = asyncio.run(
                ReadOnlyAgentOrchestrator(
                    db,
                    settings=settings,
                    now=lambda: datetime(2026, 8, 14, 12, tzinfo=UTC),
                ).run_turn(
                    attention_conversation.public_id,
                    owner_user_id=user.id,
                    text=(
                        "What needs my attention today? Check transaction reviews, due household "
                        "items, and integration readiness."
                    ),
                    client_message_id="live-openai-smoke-attention-1",
                )
            )

            assert attention_turn.run.status == "completed"
            attention_response = attention_turn.assistant_message.structured_response
            assert attention_response is not None
            attention_blocks = [
                block
                for block in attention_response.blocks
                if isinstance(block, AgentAttentionSummaryBlock)
            ]
            assert len(attention_blocks) == 1
            attention_block = attention_blocks[0]

            persisted_attention_run = db.scalar(
                select(AgentRun).where(AgentRun.public_id == attention_turn.run.public_id)
            )
            assert persisted_attention_run is not None
            assert persisted_attention_run.latency_ms is not None
            attention_tool_calls = list(
                db.scalars(
                    select(AgentToolCall)
                    .where(AgentToolCall.run_id == persisted_attention_run.id)
                    .order_by(AgentToolCall.sequence)
                )
            )
            assert len(attention_tool_calls) == MAX_AGENT_TOOL_CALLS == 3
            assert all(call.status == "completed" for call in attention_tool_calls)
            assert {call.tool_name for call in attention_tool_calls} == {
                "search_transactions",
                "get_household_replenishment",
                "get_integration_status",
            }
            tool_domains = {
                "get_spending_insights": "spending",
                "search_transactions": "transactions",
                "get_household_replenishment": "replenishment",
                "get_receipts": "receipts",
                "get_relevant_deals": "deals",
                "get_errands_and_plan": "errands",
                "get_integration_status": "integrations",
            }
            selected_domains = {tool_domains[call.tool_name] for call in attention_tool_calls}
            assert selected_domains == {"transactions", "replenishment", "integrations"}
            assert set(attention_block.checked_domains) == selected_domains
            assert attention_block.status == "complete"
            assert attention_block.unavailable_domains == []

            attention_metrics = persisted_attention_run.metadata_json
            assert attention_metrics["sdk_turn_count"] <= MAX_AGENT_TURNS == 4
            assert attention_metrics["provider_request_count"] > 0
            assert attention_metrics["tool_call_count"] == len(attention_tool_calls)
            assert attention_metrics["evidence_set_count"] == len(selected_domains)
            assert attention_metrics["failed_tool_call_count"] == 0
            assert attention_metrics["completion_state"] == "complete"
            assert attention_metrics["total_tool_latency_ms"] == sum(
                call.latency_ms or 0 for call in attention_tool_calls
            )
            assert attention_metrics["canonical_response_bytes"] > 0
            assert (
                attention_metrics["response_payload_bytes"]
                >= attention_metrics["canonical_response_bytes"]
            )
            assert (attention_turn.run.input_tokens or 0) > 0
            assert (attention_turn.run.output_tokens or 0) > 0
            assert attention_turn.run.total_tokens == (
                (attention_turn.run.input_tokens or 0) + (attention_turn.run.output_tokens or 0)
            )

            serialized_attention_metrics = json.dumps(attention_metrics, sort_keys=True)
            assert "Synthetic laundry detergent" not in serialized_attention_metrics
            assert "due household items" not in serialized_attention_metrics
            assert not any(
                forbidden in key.casefold()
                for key in attention_metrics
                for forbidden in ("raw_prompt", "raw_payload", "tool_output", "provider_response")
            )

            record_property("attention_run_latency_ms", persisted_attention_run.latency_ms)
            record_property(
                "attention_sdk_runtime_latency_ms",
                attention_metrics["sdk_runtime_latency_ms"],
            )
            record_property(
                "attention_provider_orchestration_latency_ms_estimate",
                attention_metrics["provider_orchestration_latency_ms_estimate"],
            )
            record_property(
                "attention_total_tool_latency_ms",
                attention_metrics["total_tool_latency_ms"],
            )
            record_property(
                "attention_selected_tools",
                ",".join(call.tool_name for call in attention_tool_calls),
            )
            record_property(
                "attention_tool_latencies_ms",
                ",".join(str(call.latency_ms or 0) for call in attention_tool_calls),
            )
            record_property("attention_sdk_turn_count", attention_metrics["sdk_turn_count"])
            record_property(
                "attention_provider_request_count",
                attention_metrics["provider_request_count"],
            )
            record_property(
                "attention_tool_call_count",
                attention_metrics["tool_call_count"],
            )
            record_property(
                "attention_evidence_set_count",
                attention_metrics["evidence_set_count"],
            )
            record_property("attention_item_count", len(attention_block.items))
            record_property(
                "attention_failed_tool_call_count",
                attention_metrics["failed_tool_call_count"],
            )
            record_property(
                "attention_completion_state",
                attention_metrics["completion_state"],
            )
            record_property(
                "attention_composition_latency_ms",
                attention_metrics["composition_latency_ms"],
            )
            record_property(
                "attention_canonical_response_bytes",
                attention_metrics["canonical_response_bytes"],
            )
            record_property(
                "attention_response_payload_bytes",
                attention_metrics["response_payload_bytes"],
            )
            record_property("attention_input_tokens", attention_turn.run.input_tokens)
            record_property("attention_output_tokens", attention_turn.run.output_tokens)
            record_property("attention_total_tokens", attention_turn.run.total_tokens)
    finally:
        engine.dispose()

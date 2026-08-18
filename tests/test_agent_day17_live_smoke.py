from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.contracts import (
    AgentActionConfirmationBlock,
    AgentClassificationActivityBlock,
    AgentErrorBlock,
    AgentLifestyleSummaryBlock,
    AgentSpendingSummaryBlock,
    AgentStructuredResponse,
    AgentTextBlock,
    AgentTransactionListBlock,
    hydrate_persisted_agent_response,
)
from app.agent.query_planning import AgentQueryPlan, QueryObjective, plan_agent_query
from app.agent.runtime import READ_ONLY_PROMPT_VERSION, ReadOnlyAgentOrchestrator
from app.agent.service import UnifiedAgentService
from app.config import Settings
from app.db import Base
from app.models import (
    AgentActionProposal,
    AgentRun,
    AgentToolCall,
    ClassificationDecisionRecord,
    ExpenseTransaction,
    FinancialOperation,
    PlaidItem,
    ProactiveAttentionPreference,
    PurchaseReceipt,
    PurchaseReceiptItem,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.services.classification_activity_service import ClassificationActivityService
from app.services.lifestyle_dining_service import LifestyleDiningService
from app.services.spending_insights_service import SpendingInsightsService
from app.tenancy import TenantContext, set_session_tenant
from scripts.benchmark_agent_day17 import (
    FOLLOW_UP_PROMPTS,
    PINNED_NOW,
    PINNED_TIMEZONE,
    EvaluationCase,
    paraphrase_cases,
    real_user_cases,
)

DAY17_LIVE_MODEL = "gpt-4.1-mini"
DAY17_LIVE_EVAL_VERSION = "day17-live-agent-matrix-v2"
# Official standard API text-token prices checked 2026-08-18. The explicit
# model-matched snapshot keeps runtime estimates fail-closed if the model changes.
DAY17_LIVE_PRICING_MODEL = "gpt-4.1-mini"
DAY17_LIVE_PRICING_AS_OF = "2026-08-18"
DAY17_LIVE_PRICING_SOURCE = "https://developers.openai.com/api/docs/models/gpt-4.1-mini"
DAY17_LIVE_INPUT_USD_PER_MILLION = Decimal("0.40")
DAY17_LIVE_OUTPUT_USD_PER_MILLION = Decimal("1.60")
LAST_30_DAY_CONTROL = next(
    case for case in paraphrase_cases() if case.case_id == "p03_rolling_thirty"
)
LIVE_EVAL_SKIP = pytest.mark.skipif(
    os.environ.get("RUN_DAY17_LIVE_AGENT_EVAL") != "1",
    reason=("set RUN_DAY17_LIVE_AGENT_EVAL=1 for the paid synthetic Day 17 Agent matrix"),
)


def _new_engine() -> Engine:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def _add_transaction(
    db: Session,
    *,
    workspace_id: int,
    plaid_item_id: int,
    external_id: str,
    merchant: str,
    amount_cents: int,
    occurred_on: date,
    category: str,
    pending: bool = False,
) -> ExpenseTransaction:
    normalized_category = category.casefold()
    if "coffee" in normalized_category:
        parent_category, activity_type = "food_dining", "coffee_beverage"
    elif "restaurant" in normalized_category:
        parent_category, activity_type = "food_dining", "restaurant_meal"
    elif "general_merchandise" in normalized_category:
        parent_category, activity_type = "lifestyle_shopping", "one_time_purchase"
    elif "transportation" in normalized_category:
        parent_category, activity_type = "transportation", "transportation"
    else:
        parent_category, activity_type = "other_uncertain", "uncertain"
    uncertain = activity_type == "uncertain"
    classification_at = datetime(2026, 8, 17, 18, 0, tzinfo=UTC)
    transaction = ExpenseTransaction(
        workspace_id=workspace_id,
        plaid_transaction_id=external_id,
        plaid_item_id=plaid_item_id,
        account_id="day17-card",
        merchant_name=merchant,
        name=merchant,
        amount_cents=amount_cents,
        iso_currency_code="USD",
        date=occurred_on,
        pending=pending,
        category=category,
        spending_parent_category=parent_category,
        classification_activity_type=activity_type,
        replenishment_eligibility="uncertain" if uncertain else "not_replenishable",
        classification_confidence=0.25 if uncertain else 0.95,
        classification_confidence_band="low" if uncertain else "high",
        classification_authority="fallback" if uncertain else "provider_evidence",
        classification_provenance_json=["synthetic_live_eval"],
        classification_decision_state="provisional" if uncertain else "final",
        classification_applied_at=classification_at,
        classification_auto_finalize_at=(
            datetime(2026, 8, 19, 18, 0, tzinfo=UTC) if uncertain else None
        ),
        classification_finalized_at=None if uncertain else classification_at,
        status="personal",
    )
    db.add(transaction)
    db.flush()
    return transaction


def _seed_synthetic_workspace(db: Session) -> tuple[User, Workspace]:
    user = User(email="day17-live@example.test", display_name="Day 17 live")
    db.add(user)
    db.flush()
    workspace = Workspace(name="Day 17 synthetic", created_by_user_id=user.id)
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
    db.add(
        ProactiveAttentionPreference(
            workspace_id=workspace.id,
            user_id=user.id,
            timezone=PINNED_TIMEZONE,
        )
    )
    item = PlaidItem(
        workspace_id=workspace.id,
        item_id="day17-live-item",
        owner_user_id=user.id,
    )
    db.add(item)
    db.flush()

    # Current-month eligible purchases cover rankings, categories, restaurant
    # metrics, coffee, and the contextual Food & Dining view.
    current_rows = (
        ("Alpha Cafe", 9_000, date(2026, 8, 2), "FOOD_AND_DRINK / RESTAURANT"),
        ("Bravo Market", 7_000, date(2026, 8, 3), "GENERAL_MERCHANDISE"),
        ("Charlie Fuel", 5_000, date(2026, 8, 4), "TRANSPORTATION / GAS"),
        ("Delta Dining", 4_000, date(2026, 8, 5), "FOOD_AND_DRINK / RESTAURANT"),
        ("Echo Shop", 3_000, date(2026, 8, 6), "GENERAL_MERCHANDISE"),
        ("Foxtrot Store", 2_000, date(2026, 8, 7), "GENERAL_MERCHANDISE"),
        ("Mesa Kitchen", 3_000, date(2026, 8, 10), "FOOD_AND_DRINK / RESTAURANT"),
        ("Coffee Roasters", 800, date(2026, 8, 11), "FOOD_AND_DRINK / COFFEE"),
        ("Coffee Roasters", 900, date(2026, 8, 12), "FOOD_AND_DRINK / COFFEE"),
        ("Mesa Kitchen", 6_000, date(2026, 8, 17), "FOOD_AND_DRINK / RESTAURANT"),
    )
    for index, (merchant, amount, occurred_on, category) in enumerate(current_rows, start=1):
        _add_transaction(
            db,
            workspace_id=workspace.id,
            plaid_item_id=item.id,
            external_id=f"day17-current-{index}",
            merchant=merchant,
            amount_cents=amount,
            occurred_on=occurred_on,
            category=category,
        )

    # These two rows are the comparable restaurant period for the recent and
    # month-over-month follow-up questions.
    for index, (merchant, amount, occurred_on) in enumerate(
        (
            ("Mesa Kitchen", 2_500, date(2026, 7, 10)),
            ("Tempe Table", 2_000, date(2026, 7, 15)),
        ),
        start=1,
    ):
        _add_transaction(
            db,
            workspace_id=workspace.id,
            plaid_item_id=item.id,
            external_id=f"day17-previous-{index}",
            merchant=merchant,
            amount_cents=amount,
            occurred_on=occurred_on,
            category="FOOD_AND_DRINK / RESTAURANT",
        )

    # Provider hierarchies sometimes place groceries under FOOD_AND_DRINK. The
    # Lifestyle drill-down must retain the canonical dining scope instead of
    # admitting this row merely because it sits inside the carried date pair.
    _add_transaction(
        db,
        workspace_id=workspace.id,
        plaid_item_id=item.id,
        external_id="day17-grocery-trap",
        merchant="Grocery Trap",
        amount_cents=1_100,
        occurred_on=date(2026, 7, 20),
        category="FOOD_AND_DRINK / GROCERIES",
    )

    # Credits are reported separately and pending rows are ineligible; neither
    # may change the top-merchant purchase ordering.
    _add_transaction(
        db,
        workspace_id=workspace.id,
        plaid_item_id=item.id,
        external_id="day17-credit",
        merchant="Alpha Cafe",
        amount_cents=-8_500,
        occurred_on=date(2026, 8, 12),
        category="FOOD_AND_DRINK / COFFEE",
    )
    _add_transaction(
        db,
        workspace_id=workspace.id,
        plaid_item_id=item.id,
        external_id="day17-pending",
        merchant="Pending Giant",
        amount_cents=99_900,
        occurred_on=date(2026, 8, 15),
        category="GENERAL_MERCHANDISE",
        pending=True,
    )

    mystery = _add_transaction(
        db,
        workspace_id=workspace.id,
        plaid_item_id=item.id,
        external_id="day17-mystery",
        merchant="Mystery Charge",
        amount_cents=100,
        occurred_on=date(2026, 8, 17),
        category="OTHER / UNCERTAIN",
    )
    observed_at = datetime(2026, 8, 17, 18, 0, tzinfo=UTC)
    db.add(
        ClassificationDecisionRecord(
            workspace_id=workspace.id,
            source_type="transaction",
            source_entity_id=mystery.id,
            version=1,
            spending_parent_category="other_uncertain",
            subcategory_name="Other / Uncertain",
            concept_name="Mystery charge",
            item_activity_type="uncertain",
            replenishment_eligibility="uncertain",
            confidence=0.25,
            confidence_band="low",
            authority="fallback",
            provenance_json=["synthetic_live_eval"],
            decision_state="provisional",
            auto_finalize_at=datetime(2026, 8, 19, 18, 0, tzinfo=UTC),
            finalized_at=None,
            created_at=observed_at,
        )
    )

    receipt = PurchaseReceipt(
        workspace_id=workspace.id,
        owner_user_id=user.id,
        source="web",
        source_external_id="day17-live-receipt",
        merchant_raw="Trader Joe's",
        merchant_normalized="trader joe's",
        purchased_at=observed_at,
        total_cents=699,
        currency="USD",
        parse_status="confirmed",
        parse_confidence=0.96,
        confirmed_at=observed_at,
    )
    db.add(receipt)
    db.flush()
    receipt_line = PurchaseReceiptItem(
        receipt_id=receipt.id,
        raw_name="Organic Milk",
        normalized_name="organic milk",
        quantity=1,
        unit="gallon",
        line_total_cents=699,
        classification="replenishable_household",
        classification_confidence=0.82,
        canonical_name="Organic Milk",
        spending_parent_category="household_home",
        classification_subcategory_name="Groceries",
        classification_concept_name="Milk",
        item_activity_type="grocery",
        replenishment_eligibility="potentially_replenishable",
        classification_confidence_band="medium",
        classification_authority="receipt_evidence",
        classification_provenance_json=["synthetic_live_eval"],
        classification_decision_state="final",
        classification_applied_at=observed_at,
        classification_finalized_at=observed_at,
    )
    db.add(receipt_line)
    db.flush()
    db.add(
        ClassificationDecisionRecord(
            workspace_id=workspace.id,
            source_type="receipt_line",
            source_entity_id=receipt_line.id,
            version=1,
            spending_parent_category="household_home",
            subcategory_name="Groceries",
            concept_name="Milk",
            item_activity_type="grocery",
            replenishment_eligibility="potentially_replenishable",
            confidence=0.82,
            confidence_band="medium",
            authority="receipt_evidence",
            provenance_json=["synthetic_live_eval"],
            decision_state="final",
            finalized_at=observed_at,
            created_at=observed_at,
        )
    )
    db.commit()
    set_session_tenant(db, TenantContext(user.id, workspace.id))
    return user, workspace


def _assert_case_plan(case: EvaluationCase, plan: AgentQueryPlan | None) -> AgentQueryPlan:
    assert plan is not None, case.case_id
    assert plan.objective is case.objective, case.case_id
    assert plan.tool_name == case.tool_name, case.case_id
    assert plan.exposed_tools == {case.tool_name}, case.case_id
    assert plan.date_range is not None, case.case_id
    assert (plan.date_range.start_date, plan.date_range.end_date) == (
        case.start_date,
        case.end_date,
    ), case.case_id
    assert plan.date_range.timezone == PINNED_TIMEZONE, case.case_id
    assert plan.top_n == case.top_n, case.case_id
    assert plan.activity_type == case.activity_type, case.case_id
    assert plan.classification_view == case.classification_view, case.case_id
    assert plan.comparison_mode == case.comparison_mode, case.case_id
    return plan


def _assert_tool_call(
    db: Session,
    *,
    run: AgentRun,
    plan: AgentQueryPlan,
    expected_context_category: str | None = None,
) -> AgentToolCall:
    calls = list(
        db.scalars(
            select(AgentToolCall)
            .where(AgentToolCall.run_id == run.id)
            .order_by(AgentToolCall.sequence)
        )
    )
    assert len(calls) == 1
    call = calls[0]
    assert (call.tool_name, call.operation_kind, call.status) == (
        plan.tool_name,
        "read",
        "completed",
    )
    assert call.requires_confirmation is False
    for name, value in plan.tool_arguments().items():
        assert call.arguments_json[name] == value, (name, call.arguments_json)
    if expected_context_category is not None:
        assert call.arguments_json["category"] == expected_context_category
    return call


def _assert_hydrates_for_supported_rendering(response: AgentStructuredResponse) -> None:
    assert not any(isinstance(block, AgentErrorBlock) for block in response.blocks)
    assert not any(isinstance(block, AgentActionConfirmationBlock) for block in response.blocks)
    hydrated = hydrate_persisted_agent_response(response.model_dump(mode="json"))
    assert hydrated.model_dump(mode="json") == response.model_dump(mode="json")


def _assert_direct_response(
    case: EvaluationCase,
    response: AgentStructuredResponse,
) -> None:
    _assert_hydrates_for_supported_rendering(response)
    text = next(block.text for block in response.blocks if isinstance(block, AgentTextBlock))
    lowered = text.casefold()
    assert "unsupported" not in lowered and "not supported" not in lowered

    if (
        case.objective
        in {
            QueryObjective.TOP_CATEGORIES,
            QueryObjective.TOP_MERCHANTS,
            QueryObjective.TOTAL_SPEND,
            QueryObjective.COMPARE_SPENDING,
            QueryObjective.CHANGE_EXPLANATION,
        }
        and case.tool_name == "get_spending_insights"
    ):
        block = next(
            item for item in response.blocks if isinstance(item, AgentSpendingSummaryBlock)
        )
        assert (block.start_date, block.end_date) == (case.start_date, case.end_date)
        assert block.total_cents >= 0
        assert block.credits_cents >= 0
        if case.objective is QueryObjective.TOP_CATEGORIES:
            assert block.focus == "top_categories"
            assert block.requested_limit == 1
            assert len(block.top_categories) == 1
            assert block.top_categories[0].name == "Food & Dining"
            assert text.startswith("Food & Dining was your largest spending category")
            assert block.top_merchants == []
        elif case.objective is QueryObjective.TOP_MERCHANTS:
            assert block.focus == "top_merchants"
            assert block.requested_limit == 5
            assert len(block.top_merchants) == 5
            assert block.top_merchants[0].name == "Alpha Cafe"
            assert block.top_merchants[0].amount_cents == 9_000
            assert "Pending Giant" not in {row.name for row in block.top_merchants}
            assert [row.amount_cents for row in block.top_merchants] == sorted(
                (row.amount_cents for row in block.top_merchants), reverse=True
            )
            assert text.startswith("Alpha Cafe was your top merchant")
            assert block.top_categories == []
        elif case.objective is QueryObjective.TOTAL_SPEND:
            assert block.focus == "summary"
            assert text.startswith("You spent USD")
            assert block.credits_cents == 8_500
        elif case.objective is QueryObjective.COMPARE_SPENDING:
            assert block.focus == "comparison"
            assert text.startswith("Yes.")
            assert "same weekdays last week" in text
        else:
            assert block.focus == "change_explanation"
            assert "Purchase count changed" in text
            assert "average purchase" in lowered
            assert "largest measured increases" in lowered
        return

    if case.tool_name == "get_lifestyle_dining_insights":
        block = next(
            item for item in response.blocks if isinstance(item, AgentLifestyleSummaryBlock)
        )
        assert (block.start_date, block.end_date) == (case.start_date, case.end_date)
        assert block.activity_type == case.activity_type
        assert block.total_cents == (
            block.personal_cents + block.shared_cents + block.unreviewed_cents
        )
        assert block.transaction_count == block.weekday_count + block.weekend_count
        if case.objective is QueryObjective.AVERAGE_CHECK:
            assert text.startswith("Your average restaurant check")
            assert block.average_cents > 0
        elif case.objective is QueryObjective.CHANGE_EXPLANATION:
            assert "spending increased by" in lowered
            assert "Purchase count changed" in text
            assert "average check" in lowered
            assert "largest measured merchant increases" in lowered
        else:
            assert text.startswith("You spent USD") and " on " in text
            if case.activity_type == "coffee":
                assert "coffee" in lowered
                assert block.total_cents == 1_700
            if case.case_id == "11_typo_restaurant_last_month":
                assert "last month" in lowered
                assert block.total_cents == 4_500
        return

    block = next(
        item for item in response.blocks if isinstance(item, AgentClassificationActivityBlock)
    )
    assert (block.start_date, block.end_date) == (case.start_date, case.end_date)
    assert block.timezone == PINNED_TIMEZONE
    assert block.view == case.classification_view
    if case.objective is QueryObjective.RECENT_LEARNING:
        assert len(block.staple_candidates) == 1
        candidate = block.staple_candidates[0]
        assert candidate.name == "Organic Milk"
        assert candidate.created_household_item is False
        assert candidate.learning_state == "candidate"
        assert candidate.confidence_band == "medium"
        assert text.startswith("ExpenseOps found 1 recent purchase")
        assert text.endswith("These are learning candidates, not items predicted due.")
    elif case.objective is QueryObjective.LEARNING_SUMMARY:
        assert block.counts.transactions == 1
        assert block.counts.receipt_items == 1
        assert text.startswith("ExpenseOps recorded 1 transaction classification decision")
    else:
        assert len(block.uncertain) == 1
        assert block.uncertain[0].label == "Mystery Charge"
        assert text.startswith("ExpenseOps recorded 1 uncertain outcome")


def _assert_followup_response(
    *,
    turn_number: int,
    response: AgentStructuredResponse,
) -> None:
    _assert_hydrates_for_supported_rendering(response)
    text = next(block.text for block in response.blocks if isinstance(block, AgentTextBlock))
    if turn_number < 4:
        block = next(
            item for item in response.blocks if isinstance(item, AgentLifestyleSummaryBlock)
        )
        if turn_number == 1:
            assert (block.start_date, block.end_date) == (
                date(2026, 8, 1),
                date(2026, 8, 17),
            )
            assert text.startswith("You spent USD") and "dining this month" in text
        elif turn_number == 2:
            assert (block.start_date, block.end_date) == (
                date(2026, 7, 1),
                date(2026, 7, 31),
            )
            assert "last month" in text
        else:
            assert (block.start_date, block.end_date) == (
                date(2026, 8, 1),
                date(2026, 8, 17),
            )
            assert (block.previous_start_date, block.previous_end_date) == (
                date(2026, 7, 1),
                date(2026, 7, 31),
            )
            assert "largest measured merchant increases" in text.casefold()
        return
    block = next(item for item in response.blocks if isinstance(item, AgentTransactionListBlock))
    assert text.startswith("ExpenseOps found ")
    expected_rows = [
        ("Mesa Kitchen", 6_000, date(2026, 8, 17)),
        ("Coffee Roasters", 900, date(2026, 8, 12)),
        ("Coffee Roasters", 800, date(2026, 8, 11)),
        ("Mesa Kitchen", 3_000, date(2026, 8, 10)),
        ("Delta Dining", 4_000, date(2026, 8, 5)),
        ("Alpha Cafe", 9_000, date(2026, 8, 2)),
        ("Tempe Table", 2_000, date(2026, 7, 15)),
        ("Mesa Kitchen", 2_500, date(2026, 7, 10)),
    ]
    assert [
        (row.merchant, row.amount_cents, row.occurred_on) for row in block.transactions
    ] == expected_rows
    assert block.total_count == len(expected_rows)
    assert sum(row.amount_cents for row in block.transactions) == 28_200
    assert all(not row.pending and row.amount_cents > 0 for row in block.transactions)


def _percentile_95(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _duration_summary(values: list[int]) -> dict[str, int | float]:
    return {
        "total": sum(values),
        "median": round(statistics.median(values), 1),
        "p95": _percentile_95(values),
        "max": max(values),
    }


def _live_metrics(runs: list[AgentRun], calls: list[AgentToolCall]) -> dict[str, Any]:
    latencies = [int(run.latency_ms or 0) for run in runs]
    tool_latencies = [int(call.latency_ms or 0) for call in calls]
    sdk_latencies = [int(run.metadata_json.get("sdk_runtime_latency_ms") or 0) for run in runs]
    orchestration_latencies = [
        int(run.metadata_json.get("provider_orchestration_latency_ms_estimate") or 0)
        for run in runs
    ]
    composition_latencies = [
        int(run.metadata_json.get("composition_latency_ms") or 0) for run in runs
    ]
    costs = [run.estimated_cost_micros for run in runs]
    complete_cost = (
        sum(int(value) for value in costs if value is not None)
        if all(value is not None for value in costs)
        else None
    )
    provider_requests = [int(run.metadata_json.get("provider_request_count") or 0) for run in runs]
    sdk_turns = [int(run.metadata_json.get("sdk_turn_count") or 0) for run in runs]
    return {
        "eval_version": DAY17_LIVE_EVAL_VERSION,
        "production_data_used": False,
        "model": DAY17_LIVE_MODEL,
        "pricing_snapshot": {
            "model": DAY17_LIVE_PRICING_MODEL,
            "as_of": DAY17_LIVE_PRICING_AS_OF,
            "source": DAY17_LIVE_PRICING_SOURCE,
            "input_usd_per_million_tokens": str(DAY17_LIVE_INPUT_USD_PER_MILLION),
            "output_usd_per_million_tokens": str(DAY17_LIVE_OUTPUT_USD_PER_MILLION),
        },
        "prompt_version": READ_ONLY_PROMPT_VERSION,
        "pinned_now": PINNED_NOW.isoformat(),
        "timezone": PINNED_TIMEZONE,
        "exact_prompt_turns": len(real_user_cases()),
        "calendar_control_turns": 1,
        "follow_up_turns": len(FOLLOW_UP_PROMPTS),
        "completed_runs": len(runs),
        "max_tools_exposed_per_turn": 1,
        "tool_calls": len(calls),
        "tool_calls_per_turn": round(len(calls) / len(runs), 3),
        "failed_tool_calls": sum(
            int(run.metadata_json.get("failed_tool_call_count") or 0) for run in runs
        ),
        "provider_requests": sum(provider_requests),
        "provider_requests_per_turn": round(sum(provider_requests) / len(runs), 3),
        "sdk_turns": sum(sdk_turns),
        "sdk_turns_per_turn": round(sum(sdk_turns) / len(runs), 3),
        "input_tokens": sum(int(run.input_tokens or 0) for run in runs),
        "output_tokens": sum(int(run.output_tokens or 0) for run in runs),
        "total_tokens": sum(int(run.total_tokens or 0) for run in runs),
        "estimated_cost_micros": complete_cost,
        "estimated_cost_usd": (
            round(complete_cost / 1_000_000, 6) if complete_cost is not None else None
        ),
        "latency_ms": _duration_summary(latencies),
        "sdk_runtime_latency_ms": _duration_summary(sdk_latencies),
        "provider_orchestration_latency_ms_estimate": _duration_summary(orchestration_latencies),
        "tool_latency_ms": _duration_summary(tool_latencies),
        "canonical_composition_latency_ms": _duration_summary(composition_latencies),
        "cases": [
            {
                "case_id": run.metadata_json.get("day17_live_case_id"),
                "latency_ms": run.latency_ms,
                "provider_requests": run.metadata_json.get("provider_request_count"),
                "sdk_turns": run.metadata_json.get("sdk_turn_count"),
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "estimated_cost_micros": run.estimated_cost_micros,
                "tool_calls": run.metadata_json.get("tool_call_count"),
                "sdk_runtime_latency_ms": run.metadata_json.get("sdk_runtime_latency_ms"),
                "composition_latency_ms": run.metadata_json.get("composition_latency_ms"),
            }
            for run in runs
        ],
    }


def test_day17_synthetic_live_fixture_covers_canonical_financial_and_learning_truth() -> None:
    engine = _new_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as db:
            _user, _workspace = _seed_synthetic_workspace(db)
            spending = SpendingInsightsService(db).build(
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 17),
            )
            assert spending["summary"]["total_cents"] == 40_800
            assert spending["summary"]["credits_cents"] == 8_500
            assert [row["name"] for row in spending["merchant_breakdown"][:2]] == [
                "Alpha Cafe",
                "Mesa Kitchen",
            ]
            assert "Pending Giant" not in {row["name"] for row in spending["merchant_breakdown"]}

            restaurants = LifestyleDiningService(db).build(
                start_date=date(2026, 7, 19),
                end_date=date(2026, 8, 17),
                activity_type="restaurants",
            )
            assert restaurants["summary"]["total_cents"] == 22_000
            assert restaurants["comparison"]["total_cents"] == 4_500
            coffee = LifestyleDiningService(db).build(
                start_date=date(2026, 7, 19),
                end_date=date(2026, 8, 17),
                activity_type="coffee",
            )
            assert coffee["summary"]["total_cents"] == 1_700
            assert coffee["summary"]["credits_cents"] == 8_500

            learning = ClassificationActivityService(db).read_range(
                start_date=date(2026, 8, 17),
                end_date=date(2026, 8, 17),
                timezone=PINNED_TIMEZONE,
                view="summary",
                limit=5,
            )
            assert learning.counts.transactions == 1
            assert learning.counts.receipt_items == 1
            assert learning.counts.staple_candidates == 1
            assert learning.counts.uncertain == 1
            assert learning.staple_candidates[0].name == "Organic Milk"
            assert learning.uncertain[0].label == "Mystery Charge"
    finally:
        engine.dispose()


def test_day17_live_pricing_snapshot_is_explicit_and_model_matched() -> None:
    assert DAY17_LIVE_PRICING_MODEL == DAY17_LIVE_MODEL
    assert DAY17_LIVE_PRICING_AS_OF == "2026-08-18"
    assert DAY17_LIVE_PRICING_SOURCE == (
        "https://developers.openai.com/api/docs/models/gpt-4.1-mini"
    )
    assert DAY17_LIVE_INPUT_USD_PER_MILLION == Decimal("0.40")
    assert DAY17_LIVE_OUTPUT_USD_PER_MILLION == Decimal("1.60")


@LIVE_EVAL_SKIP
def test_live_day17_exact_prompt_matrix_is_direct_bounded_and_read_only() -> None:
    base_settings = Settings()
    if not base_settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY is required for the opt-in live Day 17 eval")
    settings = base_settings.model_copy(
        update={
            "openai_model": DAY17_LIVE_MODEL,
            "openai_pricing_model": DAY17_LIVE_PRICING_MODEL,
            "openai_input_cost_per_million_tokens_usd": (DAY17_LIVE_INPUT_USD_PER_MILLION),
            "openai_output_cost_per_million_tokens_usd": (DAY17_LIVE_OUTPUT_USD_PER_MILLION),
            "agent_enabled": True,
            "agent_read_tools_enabled": True,
            "agent_write_actions_enabled": False,
            "agent_proactive_enabled": False,
            "agent_purchasing_enabled": False,
        }
    )
    engine = _new_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as db:
            user, workspace = _seed_synthetic_workspace(db)
            service = UnifiedAgentService(db, settings)
            orchestrator = ReadOnlyAgentOrchestrator(
                db,
                settings=settings,
                now=lambda: PINNED_NOW,
            )
            observed_run_ids: list[int] = []

            for case in (*real_user_cases(), LAST_30_DAY_CONTROL):
                plan = _assert_case_plan(
                    case,
                    plan_agent_query(
                        case.prompt,
                        now=PINNED_NOW,
                        timezone_name=PINNED_TIMEZONE,
                        page_context=case.page_context,
                    ),
                )
                conversation = service.create_conversation(
                    owner_user_id=user.id,
                    title=f"Day 17 live {case.case_id}",
                )
                turn = asyncio.run(
                    orchestrator.run_turn(
                        conversation.public_id,
                        owner_user_id=user.id,
                        text=case.prompt,
                        client_message_id=f"day17-live-{case.case_id}",
                        page_context=case.page_context,
                    )
                )
                assert turn.run.status == "completed", case.case_id
                response = turn.assistant_message.structured_response
                assert response is not None, case.case_id
                _assert_direct_response(case, response)

                run = db.scalar(select(AgentRun).where(AgentRun.public_id == turn.run.public_id))
                assert run is not None
                run.metadata_json = {
                    **run.metadata_json,
                    "day17_live_case_id": case.case_id,
                }
                db.commit()
                call = _assert_tool_call(
                    db,
                    run=run,
                    plan=plan,
                    expected_context_category=(
                        "Food & Dining" if case.case_id == "12_contextual_food_dining" else None
                    ),
                )
                assert call.workspace_id == workspace.id
                assert run.workspace_id == workspace.id
                assert run.owner_user_id == user.id
                assert run.model_name == DAY17_LIVE_MODEL
                assert run.prompt_version == READ_ONLY_PROMPT_VERSION
                assert run.input_tokens is not None and run.input_tokens > 0
                assert run.output_tokens is not None and run.output_tokens > 0
                assert run.latency_ms is not None and run.latency_ms >= 0
                assert int(run.metadata_json.get("provider_request_count") or 0) == 2
                assert int(run.metadata_json.get("sdk_turn_count") or 0) == 2
                assert int(run.metadata_json.get("tool_call_count") or 0) == 1
                assert int(run.metadata_json.get("failed_tool_call_count") or 0) == 0
                observed_run_ids.append(run.id)

            followup_conversation = service.create_conversation(
                owner_user_id=user.id,
                title="Day 17 live follow-up chain",
            )
            previous_plans: list[AgentQueryPlan] = []
            previous_prompts: list[str] = []
            for turn_number, prompt in enumerate(FOLLOW_UP_PROMPTS, start=1):
                plan = plan_agent_query(
                    prompt,
                    now=PINNED_NOW,
                    timezone_name=PINNED_TIMEZONE,
                    previous_plans=tuple(previous_plans),
                    previous_user_texts=tuple(previous_prompts),
                )
                assert plan is not None
                assert plan.exposed_tools == {plan.tool_name}
                expected = (
                    (QueryObjective.LIFESTYLE_TOTAL, "get_lifestyle_dining_insights"),
                    (QueryObjective.LIFESTYLE_TOTAL, "get_lifestyle_dining_insights"),
                    (QueryObjective.CHANGE_EXPLANATION, "get_lifestyle_dining_insights"),
                    (QueryObjective.TRANSACTION_LIST, "search_transactions"),
                )[turn_number - 1]
                assert (plan.objective, plan.tool_name) == expected
                previous_plans.append(plan)
                previous_prompts.append(prompt)

                turn = asyncio.run(
                    orchestrator.run_turn(
                        followup_conversation.public_id,
                        owner_user_id=user.id,
                        text=prompt,
                        client_message_id=f"day17-live-followup-{turn_number}",
                    )
                )
                assert turn.run.status == "completed", f"followup_{turn_number}"
                response = turn.assistant_message.structured_response
                assert response is not None
                _assert_followup_response(turn_number=turn_number, response=response)
                run = db.scalar(select(AgentRun).where(AgentRun.public_id == turn.run.public_id))
                assert run is not None
                run.metadata_json = {
                    **run.metadata_json,
                    "day17_live_case_id": f"followup_{turn_number}",
                }
                db.commit()
                _assert_tool_call(db, run=run, plan=plan)
                assert run.model_name == DAY17_LIVE_MODEL
                assert run.prompt_version == READ_ONLY_PROMPT_VERSION
                assert run.input_tokens is not None and run.input_tokens > 0
                assert run.output_tokens is not None and run.output_tokens > 0
                assert int(run.metadata_json.get("provider_request_count") or 0) == 2
                assert int(run.metadata_json.get("sdk_turn_count") or 0) == 2
                assert int(run.metadata_json.get("tool_call_count") or 0) == 1
                assert int(run.metadata_json.get("failed_tool_call_count") or 0) == 0
                observed_run_ids.append(run.id)

            assert len(observed_run_ids) == len(real_user_cases()) + 1 + len(FOLLOW_UP_PROMPTS)
            runs = list(
                db.scalars(
                    select(AgentRun).where(AgentRun.id.in_(observed_run_ids)).order_by(AgentRun.id)
                )
            )
            calls = list(
                db.scalars(
                    select(AgentToolCall)
                    .where(AgentToolCall.run_id.in_(observed_run_ids))
                    .order_by(AgentToolCall.run_id, AgentToolCall.sequence)
                )
            )
            assert len(runs) == len(observed_run_ids)
            assert len(calls) == len(runs)
            assert all(call.operation_kind == "read" for call in calls)
            assert all(call.requires_confirmation is False for call in calls)
            assert db.scalar(select(func.count(AgentActionProposal.id))) == 0
            assert db.scalar(select(func.count(FinancialOperation.id))) == 0
            metrics = _live_metrics(runs, calls)
            assert metrics["completed_runs"] == 18
            assert metrics["tool_calls_per_turn"] == 1.0
            assert metrics["failed_tool_calls"] == 0
            assert metrics["provider_requests"] == 36
            assert metrics["provider_requests_per_turn"] == 2.0
            assert metrics["sdk_turns"] == 36
            assert metrics["sdk_turns_per_turn"] == 2.0
            assert metrics["input_tokens"] > 0
            assert metrics["output_tokens"] > 0
            assert metrics["estimated_cost_micros"] is not None
            assert metrics["estimated_cost_micros"] > 0
            print(
                "DAY17_LIVE_AGENT_EVAL_METRICS="
                + json.dumps(metrics, sort_keys=True, separators=(",", ":"))
            )
    finally:
        engine.dispose()

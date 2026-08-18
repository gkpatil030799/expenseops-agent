from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest
from pydantic import ValidationError

import app.agent.runtime as runtime_module
from app.agent.contracts import (
    AgentClassificationActivityBlock,
    AgentEmptyStateBlock,
    AgentLifestyleSummaryBlock,
    AgentSpendingSummaryBlock,
    AgentTextBlock,
    AgentToolStartedEvent,
)
from app.agent.query_planning import (
    AgentQueryPlan,
    QueryDomain,
    QueryObjective,
    ResolvedDateRange,
    TemporalPreset,
)


def _range(
    start: date = date(2026, 8, 1),
    end: date = date(2026, 8, 17),
    *,
    label: str = "this month",
) -> ResolvedDateRange:
    return ResolvedDateRange(
        preset=TemporalPreset.THIS_MONTH,
        start_date=start,
        end_date=end,
        timezone="America/Phoenix",
        label=label,
    )


def _plan(
    objective: QueryObjective,
    domain: QueryDomain,
    tool_name: str,
    *,
    top_n: int | None = None,
    activity_type: str | None = None,
    classification_view: str | None = None,
    comparison_mode: str | None = None,
    date_range: ResolvedDateRange | None = None,
) -> AgentQueryPlan:
    return AgentQueryPlan(
        objective=objective,
        domain=domain,
        tool_name=tool_name,
        date_range=date_range or _range(),
        top_n=top_n,
        activity_type=activity_type,
        classification_view=classification_view,
        comparison_mode=comparison_mode,
    )


def _aggregate(
    *,
    total: int,
    count: int,
    average: int,
    credits: int = 0,
    personal: int | None = None,
    shared: int = 0,
    unreviewed: int = 0,
    unknown_shares: int = 0,
    unknown_credit_shares: int = 0,
) -> dict[str, int]:
    return {
        "total_cents": total,
        "personal_cents": total - shared - unreviewed if personal is None else personal,
        "shared_cents": shared,
        "classified_cents": total - unreviewed,
        "unreviewed_cents": unreviewed,
        "credits_cents": credits,
        "unknown_share_transactions": unknown_shares,
        "unknown_credit_share_transactions": unknown_credit_shares,
        "transaction_count": count,
        "average_cents": average,
    }


def _spending_output(*, spend_basis: str = "card") -> dict:
    return {
        "start_date": "2026-08-01",
        "end_date": "2026-08-17",
        "previous_start_date": "2026-07-15",
        "previous_end_date": "2026-07-31",
        "currency_code": "USD",
        "spend_basis": spend_basis,
        "comparison_mode": "immediately_preceding",
        "summary": _aggregate(total=25_000, count=4, average=6_250, credits=500),
        "comparison": _aggregate(total=16_000, count=3, average=5_333, credits=100),
        "categories": [
            {
                "name": "Food & Dining",
                "amount_cents": 12_000,
                "transaction_count": 2,
                "percentage": 48.0,
                "previous_amount_cents": 5_000,
            },
            {
                "name": "Household & Home",
                "amount_cents": 8_000,
                "transaction_count": 1,
                "percentage": 32.0,
                "previous_amount_cents": 7_000,
            },
            {
                "name": "Transportation",
                "amount_cents": 5_000,
                "transaction_count": 1,
                "percentage": 20.0,
                "previous_amount_cents": 4_000,
            },
        ],
        "merchants": [
            {
                "name": name,
                "amount_cents": amount,
                "transaction_count": count,
                "percentage": percentage,
                "previous_amount_cents": previous,
            }
            for name, amount, count, percentage, previous in (
                ("Alpha Cafe", 7_000, 2, 28.0, 3_000),
                ("Bravo Market", 5_000, 1, 20.0, 4_500),
                ("Charlie Fuel", 4_000, 1, 16.0, 4_000),
                ("Delta Dining", 3_500, 1, 14.0, 2_500),
                ("Echo Shop", 3_000, 1, 12.0, 1_500),
                ("Foxtrot Store", 2_500, 1, 10.0, 500),
            )
        ],
        "notable_changes": [],
    }


def _lifestyle_output() -> dict:
    return {
        "start_date": "2026-08-01",
        "end_date": "2026-08-17",
        "previous_start_date": "2026-07-15",
        "previous_end_date": "2026-07-31",
        "currency_code": "USD",
        "spend_basis": "card",
        "activity_type": "restaurants",
        "summary": {
            **_aggregate(
                total=12_400,
                count=5,
                average=2_480,
                personal=6_200,
                shared=5_000,
                unreviewed=1_200,
            ),
            "weekday_cents": 9_000,
            "weekday_count": 4,
            "weekend_cents": 3_400,
            "weekend_count": 1,
        },
        "comparison": {
            **_aggregate(total=7_800, count=3, average=2_600),
            "weekday_cents": 5_000,
            "weekday_count": 2,
            "weekend_cents": 2_800,
            "weekend_count": 1,
        },
        "activities": [
            {
                "name": "Restaurants",
                "amount_cents": 12_400,
                "transaction_count": 5,
                "percentage": 100.0,
            }
        ],
        "top_merchants": [
            {
                "name": "Mesa Kitchen",
                "amount_cents": 6_000,
                "transaction_count": 2,
                "percentage": 48.4,
            }
        ],
        "merchant_changes": [
            {
                "name": "Mesa Kitchen",
                "current_amount_cents": 6_000,
                "previous_amount_cents": 2_500,
                "delta_cents": 3_500,
                "current_transaction_count": 2,
                "previous_transaction_count": 1,
            },
            {
                "name": "Tempe Table",
                "current_amount_cents": 3_000,
                "previous_amount_cents": 2_000,
                "delta_cents": 1_000,
                "current_transaction_count": 1,
                "previous_transaction_count": 1,
            },
        ],
        "observations": [],
        "uncertain_transaction_count": 0,
    }


def _text_and_spending(output: dict, plan: AgentQueryPlan) -> tuple[str, AgentSpendingSummaryBlock]:
    response = runtime_module._spending_response(output, query_plan=plan)
    text = next(block.text for block in response.blocks if isinstance(block, AgentTextBlock))
    block = next(block for block in response.blocks if isinstance(block, AgentSpendingSummaryBlock))
    return text, block


def test_top_category_is_direct_first_and_suppresses_merchant_ranking() -> None:
    text, block = _text_and_spending(
        _spending_output(),
        _plan(
            QueryObjective.TOP_CATEGORIES,
            QueryDomain.SPENDING,
            "get_spending_insights",
            top_n=1,
        ),
    )

    assert text.startswith("Food & Dining was your largest spending category this month")
    assert "USD 120.00" in text
    assert "48.0%" in text
    assert block.focus == "top_categories"
    assert block.requested_limit == 1
    assert [item.name for item in block.top_categories] == ["Food & Dining"]
    assert block.top_merchants == []


def test_top_five_merchants_returns_exact_bounded_rank_and_no_categories() -> None:
    text, block = _text_and_spending(
        _spending_output(),
        _plan(
            QueryObjective.TOP_MERCHANTS,
            QueryDomain.SPENDING,
            "get_spending_insights",
            top_n=5,
        ),
    )

    assert text.startswith("Alpha Cafe was your top merchant this month")
    assert "5 available top merchants are listed below" in text
    assert block.focus == "top_merchants"
    assert block.requested_limit == 5
    assert [item.name for item in block.top_merchants] == [
        "Alpha Cafe",
        "Bravo Market",
        "Charlie Fuel",
        "Delta Dining",
        "Echo Shop",
    ]
    assert block.top_categories == []


def test_total_spend_is_direct_first_and_keeps_credits_separate() -> None:
    text, block = _text_and_spending(
        _spending_output(),
        _plan(
            QueryObjective.TOTAL_SPEND,
            QueryDomain.SPENDING,
            "get_spending_insights",
        ),
    )

    assert text == (
        "You spent USD 250.00 this month on eligible purchases. "
        "Card credits of USD 5.00 are reported separately."
    )
    assert block.focus == "summary"
    assert block.top_merchants == []
    assert len(block.top_categories) == 3


def test_comparison_uses_canonical_totals_and_same_weekday_label() -> None:
    output = _spending_output()
    output["comparison_mode"] = "same_weekdays_last_week"
    text, block = _text_and_spending(
        output,
        _plan(
            QueryObjective.COMPARE_SPENDING,
            QueryDomain.SPENDING,
            "get_spending_insights",
            comparison_mode="same_weekdays_last_week",
        ),
    )

    assert text == (
        "Yes. Eligible purchase spending is up USD 90.00 (56.2% higher) versus "
        "the same weekdays last week: USD 250.00 compared with USD 160.00."
    )
    assert block.focus == "comparison"
    assert block.change_percent == 56.2
    assert block.top_merchants == []


def test_actual_share_comparison_qualifies_incomplete_data_and_omits_percentage() -> None:
    output = _spending_output(spend_basis="actual_share")
    output["summary"]["unknown_share_transactions"] = 1
    text, block = _text_and_spending(
        output,
        _plan(
            QueryObjective.COMPARE_SPENDING,
            QueryDomain.SPENDING,
            "get_spending_insights",
        ),
    )

    assert text.startswith("Yes. Within confirmed actual-share data")
    assert "up USD 90.00" in text
    assert "Confirmed allocations only; an exact percentage is not shown." in text
    assert "%" not in text
    assert block.change_percent is None
    assert block.unknown_share_transactions == 1


def test_spending_change_explanation_math_is_code_derived_from_tool_output() -> None:
    text, block = _text_and_spending(
        _spending_output(),
        _plan(
            QueryObjective.CHANGE_EXPLANATION,
            QueryDomain.SPENDING,
            "get_spending_insights",
        ),
    )

    assert text.startswith("Eligible purchase spending increased by USD 90.00.")
    assert "Purchase count changed from 3 to 4 (+1)" in text
    assert "USD 53.33 to USD 62.50 (+USD 9.17)" in text
    assert "Food & Dining (+USD 70.00), Alpha Cafe (+USD 40.00)" in text
    assert block.focus == "change_explanation"


def test_typical_restaurant_check_answers_average_before_supporting_detail() -> None:
    response = runtime_module._lifestyle_response(
        _lifestyle_output(),
        query_plan=_plan(
            QueryObjective.AVERAGE_CHECK,
            QueryDomain.LIFESTYLE,
            "get_lifestyle_dining_insights",
            activity_type="restaurants",
        ),
    )
    text = next(block.text for block in response.blocks if isinstance(block, AgentTextBlock))
    block = next(
        block for block in response.blocks if isinstance(block, AgentLifestyleSummaryBlock)
    )

    assert text == "Your average restaurant check this month was USD 24.80 across 5 purchases."
    assert block.average_cents == 2_480
    assert block.total_cents == 12_400


def test_top_five_restaurant_merchants_returns_exact_ranked_rows() -> None:
    output = deepcopy(_lifestyle_output())
    output["top_merchants"] = [
        {
            "name": name,
            "amount_cents": amount,
            "transaction_count": count,
            "percentage": percentage,
        }
        for name, amount, count, percentage in (
            ("Mesa Kitchen", 6_000, 2, 48.4),
            ("Tempe Table", 3_000, 1, 24.2),
            ("Delta Dining", 1_500, 1, 12.1),
            ("Copper Bistro", 900, 1, 7.3),
            ("Cactus Cafe", 600, 1, 4.8),
            ("Desert Grill", 400, 1, 3.2),
        )
    ]

    response = runtime_module._lifestyle_response(
        output,
        query_plan=_plan(
            QueryObjective.TOP_MERCHANTS,
            QueryDomain.LIFESTYLE,
            "get_lifestyle_dining_insights",
            top_n=5,
            activity_type="restaurants",
        ),
    )
    text = next(block.text for block in response.blocks if isinstance(block, AgentTextBlock))
    block = next(
        block for block in response.blocks if isinstance(block, AgentLifestyleSummaryBlock)
    )

    assert text.startswith("Mesa Kitchen was your top restaurant merchant this month at USD 60.00")
    assert "5 available top restaurant merchants are listed below" in text
    assert block.title == "Top restaurant merchants"
    assert [item.name for item in block.top_merchants] == [
        "Mesa Kitchen",
        "Tempe Table",
        "Delta Dining",
        "Copper Bistro",
        "Cactus Cafe",
    ]


def test_lifestyle_change_explanation_uses_canonical_counts_averages_and_merchants() -> None:
    response = runtime_module._lifestyle_response(
        _lifestyle_output(),
        query_plan=_plan(
            QueryObjective.CHANGE_EXPLANATION,
            QueryDomain.LIFESTYLE,
            "get_lifestyle_dining_insights",
            activity_type="restaurants",
        ),
    )
    text = next(block.text for block in response.blocks if isinstance(block, AgentTextBlock))

    assert text.startswith("Restaurant spending increased by USD 46.00.")
    assert "Purchase count changed from 3 to 5 (+2)" in text
    assert "USD 26.00 to USD 24.80 (-USD 1.20)" in text
    assert "Mesa Kitchen (+USD 35.00), Tempe Table (+USD 10.00)" in text


def _range_counts(**overrides: int) -> dict[str, int]:
    counts = {
        "transactions": 0,
        "receipt_items": 0,
        "categories": 0,
        "new_categories": 0,
        "receipt_matches": 0,
        "new_household_items": 0,
        "staple_candidates": 0,
        "aliases": 0,
        "cadence_updates": 0,
        "uncertain": 0,
    }
    counts.update(overrides)
    return counts


def _staple_candidate() -> dict:
    return {
        "decision_public_id": "decision-1",
        "receipt_item_public_id": "receipt-item-1",
        "receipt_public_id": "receipt-1",
        "source_available": True,
        "merchant": "Trader Joe's",
        "name": "Organic Milk",
        "parent_category": "household_home",
        "subcategory": "Groceries",
        "concept": "Milk",
        "activity_type": "grocery",
        "replenishment_eligibility": "replenishable",
        "confidence": 0.91,
        "confidence_band": "high",
        "decision_state": "final",
        "created_household_item": False,
        "household_item_public_id": None,
        "household_item_name": None,
        "learning_state": "candidate",
        "applied_at": "2026-08-17T19:00:00Z",
    }


def _range_activity_output(*, view: str = "staple_candidates") -> dict:
    candidate = _staple_candidate()
    return {
        "schema_version": "1.1",
        "view": view,
        "start_date": "2026-07-19",
        "end_date": "2026-08-17",
        "timezone": "America/Phoenix",
        "as_of": "2026-08-18T02:00:00Z",
        "counts": _range_counts(staple_candidates=1),
        "transactions": [],
        "receipt_items": [],
        "categories": [],
        "new_categories": [],
        "receipt_matches": [],
        "new_household_items": [],
        "staple_candidates": [candidate] if view == "staple_candidates" else [],
        "aliases": [],
        "cadence_updates": [],
        "uncertain": [],
        "truncated_sections": [],
    }


def test_range_classification_response_distinguishes_candidates_from_due_items() -> None:
    response = runtime_module._classification_activity_response(
        _range_activity_output(),
        query_plan=_plan(
            QueryObjective.RECENT_LEARNING,
            QueryDomain.CLASSIFICATION,
            "get_classification_activity",
            classification_view="staple_candidates",
            date_range=_range(date(2026, 7, 19), date(2026, 8, 17), label="recently"),
        ),
    )
    text = next(block.text for block in response.blocks if isinstance(block, AgentTextBlock))
    block = next(
        block for block in response.blocks if isinstance(block, AgentClassificationActivityBlock)
    )

    assert text.startswith("ExpenseOps found 1 recent purchase that could become household staples")
    assert "Organic Milk" in text
    assert text.endswith("These are learning candidates, not items predicted due.")
    assert block.block_version == "1.1"
    assert block.activity_date is None
    assert block.start_date == date(2026, 7, 19)
    assert block.end_date == date(2026, 8, 17)
    assert block.timezone == "America/Phoenix"
    assert block.staple_candidates[0].name == "Organic Milk"


def test_learning_summary_reports_an_alias_only_day_without_a_false_empty_state() -> None:
    output = _range_activity_output(view="summary")
    output["counts"] = _range_counts(aliases=1)
    output["staple_candidates"] = []
    output["aliases"] = [
        {
            "public_id": "alias-1",
            "concept": "Milk",
            "parent_category": "household_home",
            "raw_pattern": "ORGANIC MILK GAL",
            "merchant": "Trader Joe's",
            "confidence": 0.96,
            "authority": "receipt_evidence",
            "active": True,
            "created_at": "2026-08-17T19:00:00Z",
        }
    ]

    response = runtime_module._classification_activity_response(output)
    text = next(block.text for block in response.blocks if isinstance(block, AgentTextBlock))
    block = next(
        block for block in response.blocks if isinstance(block, AgentClassificationActivityBlock)
    )

    assert text == (
        "ExpenseOps recorded 1 learned alias from 2026-07-19 through 2026-08-17 in America/Phoenix."
    )
    assert block.counts.aliases == 1
    assert block.aliases[0].concept == "Milk"


def test_learning_summary_reports_uncertainty_only_without_a_false_empty_state() -> None:
    output = _range_activity_output(view="summary")
    output["counts"] = _range_counts(uncertain=1)
    output["staple_candidates"] = []
    output["uncertain"] = [
        {
            "kind": "receipt_item",
            "public_id": "uncertain-1",
            "receipt_public_id": "receipt-1",
            "label": "Mystery pantry item",
            "reasons": ["low_confidence"],
            "confidence_band": "low",
            "decision_state": "provisional",
            "observed_at": "2026-08-17T19:00:00Z",
        }
    ]

    response = runtime_module._classification_activity_response(output)
    text = next(block.text for block in response.blocks if isinstance(block, AgentTextBlock))
    block = next(
        block for block in response.blocks if isinstance(block, AgentClassificationActivityBlock)
    )

    assert text == (
        "ExpenseOps recorded 1 uncertain outcome from 2026-07-19 through 2026-08-17 "
        "in America/Phoenix."
    )
    assert block.counts.uncertain == 1
    assert block.uncertain[0].label == "Mystery pantry item"


def test_learning_summary_uses_customer_safe_irregular_plurals() -> None:
    output = _range_activity_output(view="summary")
    output["counts"] = _range_counts(new_categories=2, aliases=2)
    output["staple_candidates"] = []
    output["truncated_sections"] = ["new_categories", "aliases"]

    response = runtime_module._classification_activity_response(output)
    text = next(block.text for block in response.blocks if isinstance(block, AgentTextBlock))

    assert "2 new categories" in text
    assert "2 learned aliases" in text
    assert "categorys" not in text
    assert "aliass" not in text


def _legacy_activity_block_payload() -> dict:
    return {
        "type": "classification_activity_summary",
        "title": "Categories used",
        "view": "categories",
        "activity_date": "2026-08-17",
        "counts": {
            "transactions": 0,
            "receipt_items": 0,
            "categories": 1,
            "new_categories": 0,
            "receipt_matches": 0,
            "new_household_items": 0,
            "cadence_updates": 0,
            "uncertain": 0,
        },
        "categories": [
            {
                "parent_category": "food_dining",
                "transaction_count": 1,
                "receipt_item_count": 0,
                "total_count": 1,
            }
        ],
        "truncated_sections": [],
    }


def test_persisted_v1_classification_block_remains_loadable_without_new_fields() -> None:
    block = AgentClassificationActivityBlock.model_validate(_legacy_activity_block_payload())

    assert block.block_version == "1.0"
    assert block.activity_date == date(2026, 8, 17)
    assert block.timezone == "UTC"
    assert block.start_date is None
    assert block.end_date is None
    assert block.staple_candidates == []
    assert block.aliases == []


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(
            {"activity_date": "2026-08-17", "start_date": None},
            id="activity-date-not-allowed",
        ),
        pytest.param({"start_date": None}, id="missing-start"),
        pytest.param({"timezone": "Mars/Olympus_Mons"}, id="invalid-timezone"),
        pytest.param(
            {"start_date": "2026-05-19", "end_date": "2026-08-17"},
            id="over-90-days",
        ),
        pytest.param(
            {
                "counts": {
                    "transactions": 0,
                    "receipt_items": 0,
                    "categories": 0,
                    "new_categories": 0,
                    "receipt_matches": 0,
                    "new_household_items": 0,
                    "cadence_updates": 0,
                    "uncertain": 0,
                }
            },
            id="legacy-counts",
        ),
        pytest.param(
            {
                "view": "staple_candidates",
                "transactions": [
                    {
                        "unexpected": "unrelated rows must never be accepted",
                    }
                ],
            },
            id="unrelated-section",
        ),
        pytest.param(
            {"counts": _range_counts(staple_candidates=0)},
            id="rows-exceed-count",
        ),
        pytest.param(
            {
                "counts": _range_counts(staple_candidates=2),
                "truncated_sections": [],
            },
            id="missing-truncation-marker",
        ),
        pytest.param({"unexpected": "forbidden"}, id="unknown-field"),
    ],
)
def test_v11_classification_block_rejects_ambiguous_or_inconsistent_payloads(
    mutation: dict,
) -> None:
    payload = {
        "type": "classification_activity_summary",
        "block_version": "1.1",
        "title": "Potential household staples",
        **_range_activity_output(),
    }
    payload.pop("schema_version")
    payload.pop("as_of")
    payload.update(deepcopy(mutation))

    with pytest.raises(ValidationError):
        AgentClassificationActivityBlock.model_validate(payload)


def test_v10_classification_block_rejects_v11_only_shape() -> None:
    payload = _legacy_activity_block_payload()
    payload.update(
        {
            "view": "staple_candidates",
            "activity_date": None,
            "start_date": "2026-08-01",
            "end_date": "2026-08-17",
            "timezone": "America/Phoenix",
            "staple_candidates": [_staple_candidate()],
        }
    )

    with pytest.raises(ValidationError):
        AgentClassificationActivityBlock.model_validate(payload)


def test_empty_range_classification_is_an_honest_timezone_aware_empty_state() -> None:
    output = _range_activity_output()
    output["counts"] = _range_counts()
    output["staple_candidates"] = []

    response = runtime_module._classification_activity_response(output)
    empty = next(block for block in response.blocks if isinstance(block, AgentEmptyStateBlock))

    assert empty.title == "No recent staple candidates"
    assert (
        empty.message
        == "ExpenseOps did not record matching classification activity from 2026-07-19 "
        "through 2026-08-17 in America/Phoenix."
    )


def test_lifestyle_tool_progress_uses_its_semantic_activity() -> None:
    activity = runtime_module._tool_activity("get_lifestyle_dining_insights")

    assert activity == (
        "lifestyle",
        "Checking lifestyle and dining activity…",
        "Lifestyle and dining data is ready.",
    )
    event = AgentToolStartedEvent(
        sequence=1,
        run_public_id="run-day17",
        activity=activity[0],
        message=activity[1],
    )
    assert event.activity == "lifestyle"

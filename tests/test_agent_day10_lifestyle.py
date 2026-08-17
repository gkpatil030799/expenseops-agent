from datetime import date

import pytest

from app.agent.context import build_contextual_tool_policy
from app.agent.contracts import AgentLifestyleSummaryBlock, AgentTextBlock
from app.agent.read_tools import LifestyleDiningOutput
from app.agent.runtime import (
    ReadToolEvidence,
    _sdk_tool_exposure,
    build_run_evidence_bundle,
    compose_grounded_response,
)


def _output() -> dict:
    aggregate = {
        "total_cents": 12_000,
        "personal_cents": 4_000,
        "shared_cents": 8_000,
        "unreviewed_cents": 0,
        "credits_cents": 500,
        "transaction_count": 4,
        "average_cents": 3_000,
        "unknown_share_transactions": 0,
        "unknown_credit_share_transactions": 0,
        "weekday_cents": 8_000,
        "weekday_count": 3,
        "weekend_cents": 4_000,
        "weekend_count": 1,
    }
    return LifestyleDiningOutput.model_validate(
        {
            "start_date": "2026-08-01",
            "end_date": "2026-08-16",
            "previous_start_date": "2026-07-16",
            "previous_end_date": "2026-07-31",
            "activity_type": "restaurants",
            "currency_code": "USD",
            "spend_basis": "card",
            "summary": aggregate,
            "comparison": {
                **aggregate,
                "total_cents": 9_000,
                "personal_cents": 3_000,
                "shared_cents": 6_000,
                "transaction_count": 3,
                "average_cents": 3_000,
                "weekday_cents": 6_000,
                "weekday_count": 2,
                "weekend_cents": 3_000,
                "weekend_count": 1,
            },
            "activities": [
                {
                    "name": "restaurants",
                    "amount_cents": 12_000,
                    "transaction_count": 4,
                    "percentage": 100,
                }
            ],
            "top_merchants": [
                {
                    "name": "Local Bistro",
                    "amount_cents": 8_000,
                    "transaction_count": 2,
                    "percentage": 66.7,
                }
            ],
            "uncertain_transaction_count": 1,
            "previous_uncertain_transaction_count": 0,
            "observations": [
                "Restaurant purchases: 4 totaling USD 120.00.",
                "Purchase frequency changed from 3 to 4 (+1); purchase spend "
                "changed by +USD 30.00.",
            ],
            "available_currencies": ["USD"],
            "excluded_other_currency_transactions": 0,
            "pending_transactions_excluded": True,
        }
    ).model_dump(mode="json")


@pytest.mark.parametrize(
    "prompt",
    [
        "How much have I spent on coffee lately?",
        "Am I buying coffee more often?",
        "Which coffee shops do I visit most?",
        "Did I eat out more this month?",
        "Why did restaurant spending increase?",
        "How much of restaurant spending was shared?",
        "What's my typical restaurant check?",
        "How much did I spend on food delivery?",
        "What changed in my dining habits recently?",
    ],
)
def test_natural_lifestyle_queries_render_only_canonical_tool_facts(prompt):
    evidence = ReadToolEvidence(
        tool_name="get_lifestyle_dining_insights",
        tool_version="1.0",
        sequence=0,
        arguments={
            "start_date": "2026-08-01",
            "end_date": "2026-08-16",
            "activity_type": "restaurants",
            "spend_basis": "card",
        },
        output=_output(),
    )
    response = compose_grounded_response(
        build_run_evidence_bundle([evidence], []),
        user_text=prompt,
        current_date=date(2026, 8, 16),
    )

    block = next(
        value for value in response.blocks if isinstance(value, AgentLifestyleSummaryBlock)
    )
    text = " ".join(value.text for value in response.blocks if isinstance(value, AgentTextBlock))
    assert block.total_cents == 12_000
    assert block.transaction_count == 4
    assert block.average_cents == 3_000
    assert block.personal_cents + block.shared_cents + block.unreviewed_cents == block.total_cents
    assert block.top_merchants[0].name == "Local Bistro"
    assert "USD 120.00" in text
    assert "unclassified" in text
    assert not any(
        word in text.casefold()
        for word in ("addict", "problem", "unhealthy", "moral", "relationship", "religion")
    )


def test_lifestyle_tool_evidence_is_a_distinct_bounded_read_domain():
    output = _output()
    bundle = build_run_evidence_bundle(
        [
            ReadToolEvidence(
                tool_name="get_spending_insights",
                tool_version="1.2",
                sequence=0,
                arguments={},
                output={"summary": {}},
            ),
            ReadToolEvidence(
                tool_name="get_lifestyle_dining_insights",
                tool_version="1.0",
                sequence=1,
                arguments={},
                output=output,
            ),
        ],
        [],
    )

    assert bundle.checked_domains[:2] == ("spending", "lifestyle")
    assert len(bundle.evidence_sets) == 2


@pytest.mark.parametrize(
    ("prompt", "activity"),
    [
        ("How much have I spent on coffee lately?", "coffee"),
        ("Did I eat out more this month?", "restaurants"),
        ("How much did I spend on food delivery?", "delivery"),
        ("Did my nightlife spending increase?", "nightlife"),
        ("Compare coffee and restaurant spending", "all"),
    ],
)
def test_clear_single_domain_lifestyle_queries_get_least_authority_tool_exposure(prompt, activity):
    assert _sdk_tool_exposure(prompt, None) == frozenset({"get_lifestyle_dining_insights"})
    policy = build_contextual_tool_policy(text=prompt, page_context=None)
    assert (
        policy.apply(
            "get_lifestyle_dining_insights",
            {"activity_type": None},
        )["activity_type"]
        == activity
    )


def test_lifestyle_exposure_does_not_hide_explicit_transaction_or_deal_domains():
    assert _sdk_tool_exposure("Show the coffee transactions that drove this increase", None) is None
    assert _sdk_tool_exposure("Compare coffee spending and current deals", None) is None

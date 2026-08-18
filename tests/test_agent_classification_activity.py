from datetime import date

import pytest
from pydantic import ValidationError

from app.agent.classification_activity_tool import ClassificationActivityInput
from app.agent.contracts import (
    AgentClassificationActivityBlock,
    AgentEmptyStateBlock,
    AgentReceiptSummaryBlock,
)
from app.agent.read_tools import build_read_tool_registry
from app.agent.runtime import (
    ReadToolEvidence,
    _explicit_retrospective_tool_plan,
    _sdk_tool_exposure,
    build_run_evidence_bundle,
    compose_grounded_response,
)
from app.config import Settings


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        agent_enabled=True,
        agent_read_tools_enabled=True,
        agent_write_actions_enabled=False,
        agent_proactive_enabled=False,
        agent_purchasing_enabled=False,
    )


def _categories_output() -> dict:
    return {
        "schema_version": "1.0",
        "view": "categories",
        "activity_date": "2026-08-17",
        "timezone": "UTC",
        "as_of": "2026-08-17T18:00:00Z",
        "counts": {
            "transactions": 1,
            "receipt_items": 1,
            "categories": 1,
            "new_categories": 0,
            "receipt_matches": 0,
            "new_household_items": 0,
            "cadence_updates": 0,
            "uncertain": 0,
        },
        "transactions": [],
        "receipt_items": [],
        "categories": [
            {
                "parent_category": "household_home",
                "transaction_count": 1,
                "receipt_item_count": 1,
                "total_count": 2,
            }
        ],
        "new_categories": [],
        "receipt_matches": [],
        "new_household_items": [],
        "cadence_updates": [],
        "uncertain": [],
        "truncated_sections": [],
    }


def test_classification_activity_tool_is_strict_bounded_and_read_only() -> None:
    registry = build_read_tool_registry(_settings())
    metadata = registry.get("get_classification_activity")

    assert metadata.effect == "read"
    assert metadata.version == "1.0"
    assert metadata.confirmation_required is False
    assert metadata.input_model.model_json_schema()["additionalProperties"] is False
    assert (
        ClassificationActivityInput.model_validate(
            {"activity_date": "2026-08-17", "view": "uncertain", "limit": 20}
        ).view
        == "uncertain"
    )
    for invalid in (
        {"activity_date": "08/17/2026"},
        {"activity_date": "2026-08-17", "view": "everything"},
        {"activity_date": "2026-08-17", "limit": 21},
        {"activity_date": "2026-08-17", "workspace_id": 99},
    ):
        with pytest.raises(ValidationError):
            ClassificationActivityInput.model_validate(invalid)


@pytest.mark.parametrize(
    ("prompt", "tool_name", "view"),
    [
        ("What did ExpenseOps categorize today?", "get_classification_activity", "summary"),
        ("What did you learn today?", "get_classification_activity", "summary"),
        ("What categories did ExpenseOps use today?", "get_classification_activity", "categories"),
        ("Which categories did you create?", "get_classification_activity", "new_categories"),
        ("Which receipts matched transactions today?", "get_classification_activity", "matches"),
        ("Which receipts matched Plaid transactions?", "get_classification_activity", "matches"),
        ("Which new staples did ExpenseOps add today?", "get_classification_activity", "staples"),
        ("What new staples were created?", "get_classification_activity", "staples"),
        ("What cadence did ExpenseOps learn today?", "get_classification_activity", "cadence"),
        ("What cadence did you estimate?", "get_classification_activity", "cadence"),
        ("What is uncertain today?", "get_classification_activity", "uncertain"),
        ("Anything uncertain?", "get_classification_activity", "uncertain"),
        ("What was the latest receipt?", "get_receipts", "latest"),
        (
            "Show me all items categorized from my latest receipt",
            "get_receipts",
            "latest",
        ),
    ],
)
def test_exact_retrospective_queries_have_code_owned_scope(
    prompt: str,
    tool_name: str,
    view: str,
) -> None:
    plan = _explicit_retrospective_tool_plan(prompt, current_date=date(2026, 8, 17))

    assert len(plan) == 1
    assert plan[0][0] == tool_name
    assert plan[0][1]["view"] == view
    assert _sdk_tool_exposure(prompt, None) == frozenset({tool_name})
    if tool_name == "get_classification_activity":
        assert plan[0][1] == {
            "activity_date": "2026-08-17",
            "view": view,
            "limit": 10,
        }
    else:
        assert plan[0][1] == {"view": "latest", "limit": 1, "line_limit": 25}


def test_classification_activity_composition_is_deterministic_and_structured() -> None:
    output = _categories_output()
    evidence = ReadToolEvidence(
        tool_name="get_classification_activity",
        tool_version="1.0",
        sequence=0,
        arguments={
            "activity_date": "2026-08-17",
            "view": "categories",
            "limit": 10,
        },
        output=output,
    )

    bundle = build_run_evidence_bundle([evidence], [])
    response = compose_grounded_response(
        bundle,
        user_text="What categories did ExpenseOps use today?",
        current_date=date(2026, 8, 17),
    )

    assert bundle.checked_domains == ("classification",)
    block = next(
        item for item in response.blocks if isinstance(item, AgentClassificationActivityBlock)
    )
    assert block.view == "categories"
    assert block.categories[0].total_count == 2
    assert block.transactions == []


def test_empty_classification_activity_is_not_presented_as_setup_failure() -> None:
    output = _categories_output()
    output["counts"] = {key: 0 for key in output["counts"]}
    output["categories"] = []
    evidence = ReadToolEvidence(
        tool_name="get_classification_activity",
        arguments={"activity_date": "2026-08-17", "view": "categories", "limit": 10},
        output=output,
    )

    response = compose_grounded_response(
        build_run_evidence_bundle([evidence], []),
        user_text="What categories did ExpenseOps use today?",
        current_date=date(2026, 8, 17),
    )

    empty = next(item for item in response.blocks if isinstance(item, AgentEmptyStateBlock))
    assert empty.title == "No categories recorded"
    assert "setup" not in empty.message.casefold()


def test_latest_receipt_composition_preserves_canonical_line_taxonomy() -> None:
    output = {
        "view": "latest",
        "receipts": [],
        "receipt": {
            "public_id": "receipt-7",
            "merchant": "Household Store",
            "purchased_at": "2026-08-17T12:00:00Z",
            "ingested_at": "2026-08-17T12:05:00Z",
            "total_cents": 2_499,
            "currency_code": "USD",
            "status": "confirmed",
            "matched_line_count": 1,
            "ignored_line_count": 0,
            "unmatched_line_count": 0,
            "total_line_count": 1,
            "transaction_linked": True,
            "confirmed_household_item_ids": ["item-3"],
            "confirmed_household_item_ids_truncated": False,
            "lines": [
                {
                    "public_id": "line-8",
                    "name": "Paper Towels",
                    "quantity": 1,
                    "unit": "pack",
                    "line_total_cents": 2_499,
                    "match_status": "matched",
                    "household_item_name": "Paper towels",
                    "household_item_public_id": "item-3",
                    "classification": "replenishable_household",
                    "classification_confidence": 0.94,
                    "canonical_name": "Paper towels",
                    "parent_category": "household_home",
                    "subcategory": "Paper goods",
                    "concept": "Paper towels",
                    "activity_type": "household_consumable",
                    "replenishment_eligibility": "replenishable",
                    "confirmed_acquisition": True,
                }
            ],
        },
        "total_count": 1,
        "result_limit": 25,
        "truncated": False,
    }
    evidence = ReadToolEvidence(
        tool_name="get_receipts",
        tool_version="1.3",
        sequence=0,
        arguments={"view": "latest", "limit": 1, "line_limit": 25},
        output=output,
    )

    response = compose_grounded_response(
        build_run_evidence_bundle([evidence], []),
        user_text="Show me all items categorized from my latest receipt",
        current_date=date(2026, 8, 17),
    )

    block = next(item for item in response.blocks if isinstance(item, AgentReceiptSummaryBlock))
    line = block.items[0]
    assert line.parent_category == "household_home"
    assert line.subcategory == "Paper goods"
    assert line.concept == "Paper towels"
    assert line.activity_type == "household_consumable"
    assert line.replenishment_eligibility == "replenishable"

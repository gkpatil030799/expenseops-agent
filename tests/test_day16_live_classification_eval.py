from __future__ import annotations

import os
from dataclasses import asdict
from decimal import Decimal

import pytest

from app.config import Settings
from app.models import (
    ClassificationActivityType,
    ReplenishmentEligibility,
    SpendingParentCategory,
)
from app.services.classification_model_service import (
    ClassificationBatchCandidate,
    ClassificationModelService,
)
from app.services.classification_taxonomy_service import (
    ClassificationSourceType,
    normalize_taxonomy_name,
)

RUN_LIVE = os.getenv("RUN_DAY16_LIVE_MODEL_EVAL") == "1"


@pytest.mark.skipif(
    not RUN_LIVE,
    reason="set RUN_DAY16_LIVE_MODEL_EVAL=1 for one paid synthetic classification batch",
)
def test_live_day16_synthetic_batch_stays_closed_uncertain_and_action_free(
    record_property,
) -> None:
    base_settings = Settings()
    if not base_settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY is required for the opt-in live classification eval")
    settings = base_settings.model_copy(
        update={
            "classification_model": "gpt-5.6-luna",
            "openai_pricing_model": "gpt-5.6-luna",
            "openai_input_cost_per_million_tokens_usd": Decimal("0.20"),
            "openai_output_cost_per_million_tokens_usd": Decimal("1.20"),
        }
    )
    candidates = [
        ClassificationBatchCandidate(
            ClassificationSourceType.RECEIPT_LINE,
            1,
            "Meyer lemons 2 lb",
            merchant="Synthetic Grocery",
            receipt_category="groceries",
        ),
        ClassificationBatchCandidate(
            ClassificationSourceType.RECEIPT_LINE,
            2,
            "Plant-based surface cleaning concentrate",
            merchant="Synthetic Grocery",
            receipt_category="household",
        ),
        ClassificationBatchCandidate(
            ClassificationSourceType.RECEIPT_LINE,
            3,
            "Paneer makhani entree",
            merchant="Synthetic Restaurant",
            receipt_category="restaurant",
        ),
        ClassificationBatchCandidate(
            ClassificationSourceType.RECEIPT_LINE,
            4,
            "Single origin cortado",
            merchant="Synthetic Cafe",
            receipt_category="coffee",
        ),
        ClassificationBatchCandidate(
            ClassificationSourceType.RECEIPT_LINE,
            5,
            "Ultralight rain shell jacket",
            merchant="Synthetic Outfitters",
            receipt_category="apparel",
        ),
        ClassificationBatchCandidate(
            ClassificationSourceType.RECEIPT_LINE,
            6,
            "USB-C dock",
            merchant="Synthetic Electronics",
            receipt_category="electronics",
        ),
        ClassificationBatchCandidate(
            ClassificationSourceType.RECEIPT_LINE,
            7,
            "HOME 24",
            merchant="Synthetic Mixed Retailer",
        ),
        ClassificationBatchCandidate(
            ClassificationSourceType.RECEIPT_LINE,
            8,
            "Reusable silicone freezer pouches",
            merchant="Synthetic Mixed Retailer",
            receipt_category="household",
        ),
        ClassificationBatchCandidate(
            ClassificationSourceType.TRANSACTION,
            9,
            "Neighborhood espresso kiosk",
            provider_category="FOOD_AND_DRINK / COFFEE_SHOP",
        ),
        ClassificationBatchCandidate(
            ClassificationSourceType.TRANSACTION,
            10,
            "Independent monthly service",
            provider_category="SERVICES",
        ),
    ]

    service = ClassificationModelService(settings)
    suggestions = service.classify(candidates)

    assert len(suggestions) == len(candidates)
    assert all(
        isinstance(value.decision.spending_parent_category, SpendingParentCategory)
        for value in suggestions
    )
    assert all(
        isinstance(value.decision.item_activity_type, ClassificationActivityType)
        for value in suggestions
    )
    assert all(
        isinstance(value.decision.replenishment_eligibility, ReplenishmentEligibility)
        for value in suggestions
    )
    assert all(
        value.decision.subcategory_name is None
        or normalize_taxonomy_name(value.decision.subcategory_name)
        for value in suggestions
    )
    assert all(
        value.cadence_min_days is None
        or (
            value.cadence_max_days is not None
            and 1 <= value.cadence_min_days <= value.cadence_max_days <= 730
        )
        for value in suggestions
    )
    assert any(
        value.decision.spending_parent_category is SpendingParentCategory.OTHER_UNCERTAIN
        for value in suggestions
    )
    assert any(
        value.decision.subcategory_name is not None
        for value in suggestions
        if value.decision.source_entity_id == 8
    ) or any(
        value.decision.spending_parent_category is SpendingParentCategory.OTHER_UNCERTAIN
        for value in suggestions
        if value.decision.source_entity_id == 8
    )
    cleaning = next(
        value for value in suggestions if value.decision.source_entity_id == 2
    )
    assert (
        cleaning.decision.spending_parent_category is SpendingParentCategory.OTHER_UNCERTAIN
    ) or (
        cleaning.decision.replenishment_eligibility
        is ReplenishmentEligibility.REPLENISHABLE
        and cleaning.cadence_min_days is not None
        and cleaning.cadence_max_days is not None
    )
    forbidden = {"sql", "url", "workspace_id", "provider_payload", "splitwise", "purchase"}
    assert all(forbidden.isdisjoint(asdict(value.decision)) for value in suggestions)

    observation = service.last_observation
    assert observation is not None
    assert observation.batch_size == len(candidates)
    assert observation.latency_ms >= 0
    assert observation.input_tokens is None or observation.input_tokens > 0
    assert observation.output_tokens is None or observation.output_tokens > 0
    record_property("model", settings.classification_model)
    record_property("batch_size", observation.batch_size)
    record_property("latency_ms", observation.latency_ms)
    record_property("input_tokens", observation.input_tokens)
    record_property("output_tokens", observation.output_tokens)
    record_property("estimated_cost_micros", observation.estimated_cost_micros)

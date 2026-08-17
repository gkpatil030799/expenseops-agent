from __future__ import annotations

import os

import pytest

from app.config import Settings
from app.services.receipt_learning_service import classify_receipt_line
from app.services.receipt_parser_service import OpenAIReceiptParser

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_RECEIPT_SMOKE") != "1",
    reason="set RUN_LIVE_RECEIPT_SMOKE=1 for the bounded paid receipt-model observation",
)


def test_live_synthetic_mixed_and_restaurant_receipt_classification(record_property) -> None:
    settings = Settings(receipt_parser_provider="openai")
    if not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY is not configured")
    parser = OpenAIReceiptParser(settings)
    parsed = parser.parse_text(
        """Synthetic receipt for automated testing only
Costco Wholesale
2026-08-17
KS EGGS 24CT 8.99
ORG 2% MLK GAL 4.49
TIDE PODS SPRING 42CT 18.99
KS PAPER TOWELS 12RL 24.99
FOOD COURT COFFEE 2.49
COTTON T-SHIRT 14.99
SALES TAX 5.21
TOTAL 80.15
"""
    )
    observed = list(parsed.items)
    resolved = [
        classify_receipt_line(
            raw_name=item.name,
            category=item.category,
            model_classification=item.classification,
            model_confidence=item.classification_confidence or 0.0,
            model_canonical_name=item.canonical_name,
            is_household_purchase=False,
        )
        for item in observed
    ]
    classifications = {item.classification for item in resolved}
    assert "perishable_grocery" in classifications
    assert "replenishable_household" in classifications
    assert "routine_consumption" in classifications
    assert "one_time_purchase" in classifications
    assert all(
        item.canonical_name is None
        for item in resolved
        if item.classification
        in {"routine_consumption", "dining_or_experience", "one_time_purchase", "non_product_line"}
    )
    assert (
        sum(
            item.classification in {"perishable_grocery", "replenishable_household"}
            for item in resolved
        )
        >= 4
    )
    mixed_observation = parser.last_observation
    assert mixed_observation is not None
    restaurant = parser.parse_text(
        """Synthetic restaurant receipt for automated testing only
Desert Spice Kitchen
2026-08-17
PANEER TIKKA 13.00
VEGETABLE BIRYANI 16.00
COCKTAIL 11.00
TIP 8.00
TOTAL 48.00
"""
    )
    assert restaurant.items
    restaurant_resolved = [
        classify_receipt_line(
            raw_name=item.name,
            category=item.category,
            model_classification=item.classification,
            model_confidence=item.classification_confidence or 0.0,
            model_canonical_name=item.canonical_name,
            is_household_purchase=False,
        )
        for item in restaurant.items
    ]
    assert all(
        item.classification
        in {
            "dining_or_experience",
            "routine_consumption",
            "non_product_line",
            "uncertain",
        }
        for item in restaurant_resolved
    )
    assert not any(
        item.classification in {"perishable_grocery", "replenishable_household"}
        for item in restaurant_resolved
    )
    restaurant_observation = parser.last_observation
    assert restaurant_observation is not None
    record_property("receipt_model", settings.receipt_parser_model)
    record_property("receipt_request_count", 2)
    record_property(
        "receipt_latency_ms_total",
        mixed_observation.latency_ms + restaurant_observation.latency_ms,
    )
    record_property(
        "receipt_input_tokens_total",
        (mixed_observation.input_tokens or 0) + (restaurant_observation.input_tokens or 0),
    )
    record_property(
        "receipt_output_tokens_total",
        (mixed_observation.output_tokens or 0) + (restaurant_observation.output_tokens or 0),
    )
    record_property("receipt_line_count_total", len(observed) + len(restaurant.items))

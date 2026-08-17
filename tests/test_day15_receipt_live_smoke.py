from __future__ import annotations

import os
from decimal import Decimal

import pytest

from app.config import Settings
from app.services.receipt_parser_service import OpenAIReceiptParser, assess_parsed_receipt
from scripts.benchmark_receipt_day15 import synthetic_receipt_image

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_RECEIPT_IMAGE_SMOKE") != "1",
    reason="set RUN_LIVE_RECEIPT_IMAGE_SMOKE=1 for four bounded synthetic image parses",
)


def test_live_multimodal_receipt_images_are_structured_truthfully(record_property) -> None:
    settings = Settings(
        receipt_parser_provider="openai",
        receipt_parser_model="gpt-5.6-luna",
        receipt_parser_image_detail="auto",
        receipt_parser_max_attempts=2,
        openai_pricing_model="gpt-5.6-luna",
        openai_input_cost_per_million_tokens_usd=Decimal("1.00"),
        openai_output_cost_per_million_tokens_usd=Decimal("6.00"),
    )
    if not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY is not configured")

    cases = [
        (
            "normal_grocery",
            synthetic_receipt_image(),
            "Trader Joe",
            8560,
            2,
        ),
        (
            "blurry_readable",
            synthetic_receipt_image(blur_radius=0.9),
            "Trader Joe",
            8560,
            2,
        ),
        (
            "rotated",
            synthetic_receipt_image(exif_orientation=6),
            "Trader Joe",
            8560,
            2,
        ),
        (
            "restaurant",
            synthetic_receipt_image(merchant="DESERT SPICE", restaurant=True),
            "Desert Spice",
            9000,
            4,
        ),
    ]
    parser = OpenAIReceiptParser(settings)
    observations = []
    for name, image, merchant, total_cents, minimum_items in cases:
        parsed = parser.parse_attachment(image, "image/jpeg", f"{name}.jpg")
        assessment = assess_parsed_receipt(parsed)
        assert assessment.quality in {"complete", "partial"}, name
        assert merchant.casefold() in (parsed.merchant or "").casefold(), name
        assert parsed.purchased_at is not None and parsed.purchased_at.date().isoformat() == (
            "2026-08-17"
        ), name
        assert parsed.total_cents == total_cents, name
        assert len(parsed.items) >= minimum_items, name
        assert assessment.arithmetic_status == "reconciled", name
        assert parser.last_observation is not None
        assert parser.last_observation.request_count <= 2
        observations.append(parser.last_observation)

    record_property("receipt_image_model", settings.receipt_parser_model)
    record_property("receipt_image_cases", len(cases))
    record_property(
        "receipt_image_provider_requests", sum(item.request_count for item in observations)
    )
    record_property(
        "receipt_image_retry_count", sum(item.request_count - 1 for item in observations)
    )
    record_property("receipt_image_latency_ms", sum(item.latency_ms for item in observations))
    record_property(
        "receipt_image_input_tokens", sum(item.input_tokens or 0 for item in observations)
    )
    record_property(
        "receipt_image_output_tokens", sum(item.output_tokens or 0 for item in observations)
    )
    record_property(
        "receipt_image_estimated_cost_micros",
        sum(item.estimated_cost_micros or 0 for item in observations),
    )

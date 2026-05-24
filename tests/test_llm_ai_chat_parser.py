from decimal import Decimal

from app.config import Settings
from app.services.llm_ai_chat_parser import LLMAIChatParser


def test_ai_chat_missing_api_key_returns_clarification():
    result = LLMAIChatParser(Settings(openai_api_key="")).parse(
        user_message="split with Janhavi",
        ai_context={},
    )

    assert result.action == "clarify"
    assert result.errors == ["missing_openai_api_key"]


def test_coerces_group_50_50_intent():
    payload = {
        "action": "split",
        "target_type": "group",
        "group_alias": "g1",
        "participant_aliases": ["me", "g1m1"],
        "include_me": True,
        "split_mode": "percentages",
        "custom_values": [
            {"alias": "me", "amount": None, "percentage": 50, "shares": None},
            {"alias": "g1m1", "amount": None, "percentage": 50, "shares": None},
        ],
        "remaining_split_behavior": "none",
        "clarification_question": None,
        "confidence": 0.92,
        "explanation": "50-50 group split",
        "errors": [],
    }

    result = LLMAIChatParser._coerce_for_test(payload)

    assert result.action == "split"
    assert result.group_alias == "g1"
    assert result.participant_aliases == ["me", "g1m1"]
    assert result.custom_values[0].percentage == Decimal("50")


def test_coerces_exact_amount_equal_remaining_intent():
    payload = {
        "action": "split",
        "target_type": "people",
        "group_alias": None,
        "participant_aliases": ["f1", "f2"],
        "include_me": False,
        "split_mode": "exact_amounts",
        "custom_values": [{"alias": "f1", "amount": 20, "percentage": None, "shares": None}],
        "remaining_split_behavior": "equal_remaining",
        "clarification_question": None,
        "confidence": 0.9,
        "explanation": "Rahul pays 20 and rest equally",
        "errors": [],
    }

    result = LLMAIChatParser._coerce_for_test(payload)

    assert result.split_mode == "exact_amounts"
    assert result.remaining_split_behavior == "equal_remaining"
    assert result.custom_values[0].amount == Decimal("20")

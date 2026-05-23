from decimal import Decimal

from app.config import Settings
from app.services.llm_conversation_parser import LLMConversationParser


class FakeLLMConversationParser(LLMConversationParser):
    def __init__(self, payload):
        super().__init__(Settings(openai_api_key="test-key", openai_model="test-model"))
        self.payload = payload

    def _call_openai(self, **kwargs):
        return self.payload


def test_missing_api_key_returns_clarification():
    parser = LLMConversationParser(Settings(openai_api_key=""))

    result = parser.parse(
        user_message="split this with Rahul",
        transaction={"merchant": "Costco"},
        pending_state={},
        available_actions=["split_people"],
    )

    assert result.action == "clarify"
    assert result.errors == ["missing_openai_api_key"]


def test_llm_parses_people_split():
    parser = FakeLLMConversationParser(
        {
            "action": "split_people",
            "target_type": "people",
            "group_name": None,
            "participant_names": ["Rahul", "Akash"],
            "split_mode": "equal",
            "custom_split_mode": "unknown",
            "remaining_split_behavior": "none",
            "payer_included": True,
            "custom_values_text": None,
            "clarification_question": None,
            "confidence": 0.91,
            "errors": [],
        }
    )

    result = parser.parse(
        user_message="split this equally with Rahul and Akash",
        transaction={"merchant": "Costco"},
        pending_state={},
        available_actions=["split_people"],
    )

    assert result.action == "split_people"
    assert result.participant_names == ["Rahul", "Akash"]
    assert result.confidence == Decimal("0.91")


def test_llm_parses_group_custom_percentage_split():
    parser = FakeLLMConversationParser(
        {
            "action": "custom_split",
            "target_type": "group",
            "group_name": "Mumbai Trip",
            "participant_names": ["Janhavi", "Rahul"],
            "split_mode": "percentages",
            "custom_split_mode": "percentages",
            "remaining_split_behavior": "equal_remaining",
            "payer_included": True,
            "custom_values_text": "Janhavi 50 percent and rest split between me and Rahul",
            "clarification_question": None,
            "confidence": 0.88,
            "errors": [],
        }
    )

    result = parser.parse(
        user_message="Janhavi 50 percent and rest split between me and Rahul",
        transaction={"merchant": "Dinner"},
        pending_state={},
        available_actions=["custom_split"],
    )

    assert result.action == "custom_split"
    assert result.target_type == "group"
    assert result.group_name == "Mumbai Trip"
    assert result.split_mode == "percentages"
    assert result.custom_split_mode == "percentages"
    assert result.remaining_split_behavior == "equal_remaining"


def test_llm_parses_fixed_amount_with_equal_remaining():
    parser = FakeLLMConversationParser(
        {
            "action": "custom_split",
            "target_type": "people",
            "group_name": None,
            "participant_names": ["Rahul"],
            "split_mode": "unknown",
            "custom_split_mode": "exact_amounts",
            "remaining_split_behavior": "equal_remaining",
            "payer_included": True,
            "custom_values_text": "Rahul pays 20 and the rest split equally",
            "clarification_question": None,
            "confidence": 0.9,
            "errors": [],
        }
    )

    result = parser.parse(
        user_message="Rahul pays 20 and rest equally",
        transaction={"merchant": "Costco"},
        pending_state={},
        available_actions=["custom_split"],
    )

    assert result.action == "custom_split"
    assert result.custom_split_mode == "exact_amounts"
    assert result.remaining_split_behavior == "equal_remaining"


def test_llm_parses_percentage_with_equal_remaining():
    parser = FakeLLMConversationParser(
        {
            "action": "custom_split",
            "target_type": "people",
            "group_name": None,
            "participant_names": ["Janhavi"],
            "split_mode": "unknown",
            "custom_split_mode": "percentages",
            "remaining_split_behavior": "equal_remaining",
            "payer_included": True,
            "custom_values_text": "Janhavi 50 percent and rest split equally",
            "clarification_question": None,
            "confidence": 0.92,
            "errors": [],
        }
    )

    result = parser.parse(
        user_message="Janhavi 50 percent and rest split equally",
        transaction={"merchant": "Dinner"},
        pending_state={},
        available_actions=["custom_split"],
    )

    assert result.action == "custom_split"
    assert result.custom_split_mode == "percentages"
    assert result.remaining_split_behavior == "equal_remaining"

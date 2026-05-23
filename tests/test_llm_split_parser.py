import json
from decimal import Decimal

from app.config import Settings
from app.services import llm_split_parser
from app.services.llm_split_parser import LLMSplitParser


class FakeLLMSplitParser(LLMSplitParser):
    def __init__(self, payload):
        super().__init__(Settings(openai_api_key="test-key", openai_model="test-model"))
        self.payload = payload
        self.captured = None

    def _call_openai(self, **kwargs):
        self.captured = kwargs
        return self.payload


def test_missing_openai_key_fails_safely():
    parser = LLMSplitParser(Settings(openai_api_key=""))

    result = parser.parse(
        user_message="50% for Janhavi and rest split across everyone else",
        total_amount_cents=10000,
        currency_code="USD",
        split_mode="percentages",
        payer_included=True,
        selected_participants=[{"user_id": 101, "display_name": "Janhavi"}],
        payer={"user_id": 999, "display_name": "You"},
    )

    assert result.ok is False
    assert result.errors == ["missing_openai_api_key"]


def test_openai_payload_uses_aliases_not_internal_user_ids():
    parser = FakeLLMSplitParser(
        {
            "ok": True,
            "parser_confidence": 0.9,
            "normalized_intent": "Janhavi 50%, Akash 50%",
            "participant_splits": [
                {
                    "alias": "p1",
                    "display_name": "Janhavi",
                    "amount_cents": None,
                    "percentage": 50,
                    "shares": None,
                },
                {
                    "alias": "p2",
                    "display_name": "Akash",
                    "amount_cents": None,
                    "percentage": 50,
                    "shares": None,
                },
            ],
            "clarification_question": None,
            "errors": [],
        }
    )

    result = parser.parse(
        user_message="50% Janhavi, 50% Akash",
        total_amount_cents=10000,
        currency_code="USD",
        split_mode="percentages",
        payer_included=True,
        selected_participants=[
            {"user_id": 123456, "display_name": "Janhavi"},
            {"user_id": 987654, "display_name": "Akash"},
        ],
        payer={"user_id": 555555, "display_name": "Gunjan"},
    )

    participants = parser.captured["alias_participants"]
    prompt_users = [
        {"alias": item.alias, "display_name": item.display_name} for item in participants
    ]
    assert {"alias": "p1", "display_name": "Janhavi"} in prompt_users
    assert {"alias": "p2", "display_name": "Akash"} in prompt_users
    assert {"alias": "me", "display_name": "Gunjan"} in prompt_users
    assert all(item.alias in {"p1", "p2", "me"} for item in participants)
    assert result.participant_splits[0].user_id == 123456
    assert result.participant_splits[1].user_id == 987654


def test_openai_request_payload_does_not_contain_internal_user_ids(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "output": [
                    {
                        "content": [
                            {
                                "text": json.dumps(
                                    {
                                        "ok": True,
                                        "parser_confidence": 0.9,
                                        "normalized_intent": "Janhavi 50%, Akash 50%",
                                        "participant_splits": [
                                            {
                                                "alias": "p1",
                                                "display_name": "Janhavi",
                                                "amount_cents": None,
                                                "percentage": 50,
                                                "shares": None,
                                            },
                                            {
                                                "alias": "p2",
                                                "display_name": "Akash",
                                                "amount_cents": None,
                                                "percentage": 50,
                                                "shares": None,
                                            },
                                        ],
                                        "clarification_question": None,
                                        "errors": [],
                                    }
                                )
                            }
                        ]
                    }
                ]
            }

    class FakeClient:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, json=None, headers=None):
            captured["json"] = json
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(llm_split_parser.httpx, "Client", FakeClient)
    parser = LLMSplitParser(Settings(openai_api_key="test-key", openai_model="test-model"))

    result = parser.parse(
        user_message="50% Janhavi, 50% Akash",
        total_amount_cents=10000,
        currency_code="USD",
        split_mode="percentages",
        payer_included=True,
        selected_participants=[
            {"user_id": 123456, "display_name": "Janhavi"},
            {"user_id": 987654, "display_name": "Akash"},
        ],
        payer={"user_id": 555555, "display_name": "Gunjan"},
    )

    serialized_payload = json.dumps(captured["json"])
    user_payload = json.loads(captured["json"]["input"][1]["content"][0]["text"])
    assert result.ok is True
    assert "123456" not in serialized_payload
    assert "987654" not in serialized_payload
    assert "555555" not in serialized_payload
    assert user_payload["participants"] == [
        {"alias": "p1", "display_name": "Janhavi"},
        {"alias": "p2", "display_name": "Akash"},
        {"alias": "me", "display_name": "Gunjan"},
    ]


def test_percentage_split_maps_aliases_back_to_user_ids():
    parser = FakeLLMSplitParser(
        {
            "ok": True,
            "parser_confidence": 0.9,
            "normalized_intent": "Janhavi 50%, everyone else 50%",
            "participant_splits": [
                {
                    "alias": "p1",
                    "display_name": "Janhavi",
                    "amount_cents": None,
                    "percentage": 50,
                    "shares": None,
                },
                {
                    "alias": "p2",
                    "display_name": "Akash",
                    "amount_cents": None,
                    "percentage": 50,
                    "shares": None,
                },
            ],
            "clarification_question": None,
            "errors": [],
        }
    )

    result = parser.parse(
        user_message="Janhavi gets half, remaining split equally",
        total_amount_cents=10000,
        currency_code="USD",
        split_mode="percentages",
        payer_included=False,
        selected_participants=[
            {"user_id": 7, "display_name": "Janhavi"},
            {"user_id": 9, "display_name": "Akash"},
        ],
        payer={"user_id": 1, "display_name": "You"},
    )

    assert result.ok is True
    assert result.participant_splits[0].user_id == 7
    assert result.participant_splits[0].percentage == Decimal("50")
    assert result.participant_splits[1].user_id == 9


def test_fixed_amount_split_maps_aliases_back_to_user_ids():
    parser = FakeLLMSplitParser(
        {
            "ok": True,
            "parser_confidence": 0.86,
            "normalized_intent": "Rahul owes 20, Akash owes 35",
            "participant_splits": [
                {
                    "alias": "p1",
                    "display_name": "Rahul",
                    "amount_cents": 2000,
                    "percentage": None,
                    "shares": None,
                },
                {
                    "alias": "p2",
                    "display_name": "Akash",
                    "amount_cents": 3500,
                    "percentage": None,
                    "shares": None,
                },
            ],
            "clarification_question": None,
            "errors": [],
        }
    )

    result = parser.parse(
        user_message="Rahul pays 20, Akash pays 35",
        total_amount_cents=5500,
        currency_code="USD",
        split_mode="exact_amounts",
        payer_included=False,
        selected_participants=[
            {"user_id": 7, "display_name": "Rahul"},
            {"user_id": 9, "display_name": "Akash"},
        ],
        payer=None,
    )

    assert result.ok is True
    assert [split.amount_cents for split in result.participant_splits] == [2000, 3500]


def test_share_split_maps_aliases_back_to_user_ids():
    parser = FakeLLMSplitParser(
        {
            "ok": True,
            "parser_confidence": 0.88,
            "normalized_intent": "Akash 2 shares, everyone else 1",
            "participant_splits": [
                {
                    "alias": "p1",
                    "display_name": "Akash",
                    "amount_cents": None,
                    "percentage": None,
                    "shares": 2,
                },
                {
                    "alias": "p2",
                    "display_name": "Rahul",
                    "amount_cents": None,
                    "percentage": None,
                    "shares": 1,
                },
            ],
            "clarification_question": None,
            "errors": [],
        }
    )

    result = parser.parse(
        user_message="Akash 2 shares, everyone else 1",
        total_amount_cents=9000,
        currency_code="USD",
        split_mode="shares",
        payer_included=False,
        selected_participants=[
            {"user_id": 9, "display_name": "Akash"},
            {"user_id": 7, "display_name": "Rahul"},
        ],
        payer=None,
    )

    assert result.ok is True
    assert [split.shares for split in result.participant_splits] == [
        Decimal("2"),
        Decimal("1"),
    ]


def test_unknown_alias_requires_clarification():
    parser = FakeLLMSplitParser(
        {
            "ok": True,
            "parser_confidence": 0.7,
            "normalized_intent": "Unknown person",
            "participant_splits": [
                {
                    "alias": "p99",
                    "display_name": "Someone",
                    "amount_cents": 1000,
                    "percentage": None,
                    "shares": None,
                }
            ],
            "clarification_question": None,
            "errors": [],
        }
    )

    result = parser.parse(
        user_message="Someone pays 10",
        total_amount_cents=1000,
        currency_code="USD",
        split_mode="exact_amounts",
        payer_included=False,
        selected_participants=[{"user_id": 7, "display_name": "Rahul"}],
        payer=None,
    )

    assert result.ok is False
    assert result.errors == ["unknown_participant_alias"]


def test_payer_alias_rejected_when_payer_excluded():
    parser = FakeLLMSplitParser(
        {
            "ok": True,
            "parser_confidence": 0.7,
            "normalized_intent": "me included",
            "participant_splits": [
                {
                    "alias": "me",
                    "display_name": "You",
                    "amount_cents": 1000,
                    "percentage": None,
                    "shares": None,
                }
            ],
            "clarification_question": None,
            "errors": [],
        }
    )

    result = parser.parse(
        user_message="me pays 10",
        total_amount_cents=1000,
        currency_code="USD",
        split_mode="exact_amounts",
        payer_included=False,
        selected_participants=[{"user_id": 7, "display_name": "Rahul"}],
        payer={"user_id": 1, "display_name": "You"},
    )

    assert result.ok is False
    assert result.errors == ["payer_not_included"]

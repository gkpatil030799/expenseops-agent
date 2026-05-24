from app.services.ai_intent_extraction_service import (
    AIIntentExtractionService,
    ExtractedAIIntent,
    _schema,
)


def test_coerce_extracts_equal_split_mentions_cleanly():
    intent = AIIntentExtractionService._coerce_for_test(
        {
            "action": "split",
            "target_type": "people",
            "group_mentions": [],
            "person_mentions": ["me", "janahvi", "yash"],
            "split_mode": "equal",
            "payer_included": True,
            "remaining_split_behavior": "none",
            "custom_values_text": None,
            "confidence_by_slot": {
                "action": 0.95,
                "participants": 0.8,
                "split_mode": 0.95,
            },
            "clarification_question": None,
            "explanation": "User wants an equal split.",
            "errors": [],
        }
    )

    assert intent.action == "split"
    assert intent.target_type == "people"
    assert intent.person_mentions == ["me", "janahvi", "yash"]
    assert intent.split_mode == "equal"
    assert intent.payer_included is True
    assert intent.confidence_by_slot["participants"] == 0.8


def test_coerce_extracts_custom_split_remaining_behavior():
    intent = AIIntentExtractionService._coerce_for_test(
        {
            "action": "split",
            "target_type": "people",
            "group_mentions": [],
            "person_mentions": ["Janhavi", "Rahul"],
            "split_mode": "percentages",
            "payer_included": False,
            "remaining_split_behavior": "equal_remaining",
            "custom_values_text": "Janhavi 50 percent and rest split equally",
            "confidence_by_slot": {"action": 0.95, "split_mode": 0.95},
            "clarification_question": None,
            "explanation": "Custom percentage split.",
            "errors": [],
        }
    )

    assert intent.split_mode == "percentages"
    assert intent.remaining_split_behavior == "equal_remaining"
    assert intent.custom_values_text == "Janhavi 50 percent and rest split equally"


def test_missing_openai_key_returns_safe_clarify():
    class Settings:
        openai_api_key = ""
        openai_model = "gpt-4.1-mini"

    service = AIIntentExtractionService(settings=Settings())

    result = service.extract(user_message="split with Rahul", context={})

    assert isinstance(result, ExtractedAIIntent)
    assert result.action == "clarify"
    assert result.errors == ["missing_openai_api_key"]
    assert result.clarification_question


def test_schema_confidence_by_slot_is_strict_object():
    confidence_schema = _schema()["properties"]["confidence_by_slot"]

    assert confidence_schema["additionalProperties"] is False
    assert confidence_schema["required"] == [
        "action",
        "target_type",
        "group",
        "participants",
        "split_mode",
        "custom_values",
    ]


def test_openai_http_error_returns_safe_status(monkeypatch):
    class Settings:
        openai_api_key = "test-key"
        openai_model = "gpt-4.1-mini"

    class FakeResponse:
        status_code = 400

        def raise_for_status(self):
            import httpx

            raise httpx.HTTPStatusError(
                "bad request",
                request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
                response=self,
            )

    class FakeClient:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("app.services.ai_intent_extraction_service.httpx.Client", FakeClient)

    result = AIIntentExtractionService(settings=Settings()).extract(
        user_message="split with Rahul",
        context={},
    )

    assert result.action == "clarify"
    assert result.errors == ["llm_request_failed", "http_status_400"]

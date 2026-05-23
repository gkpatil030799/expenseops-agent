import json

from app.models import AIInterpretationMemory
from app.services.ai_chat_context_service import AIChatContextService
from app.services.telegram_state_service import PendingTelegramSplit


def test_ai_chat_context_uses_aliases_not_raw_splitwise_ids():
    class FakeSplitwiseService:
        def get_current_user(self):
            return {"id": 111, "first_name": "Gunjan", "last_name": "Patil"}

        def get_friends(self):
            return [
                {
                    "id": 222,
                    "first_name": "Janhavi",
                    "last_name": "",
                    "email": "j@example.com",
                }
            ]

        def get_groups(self):
            return [
                {
                    "id": 333,
                    "name": "Sugar Monkeys",
                    "members": [{"id": 444, "first_name": "Janhavi", "last_name": ""}],
                }
            ]

    tx = type(
        "Tx",
        (),
        {
            "merchant_name": "FUN",
            "name": "FUN",
            "amount_cents": 8940,
            "iso_currency_code": "USD",
            "date": None,
        },
    )()

    context = AIChatContextService(FakeSplitwiseService()).build(
        tx,
        PendingTelegramSplit(transaction_id=123),
    )

    serialized = json.dumps(context.prompt_context)
    assert "111" not in serialized
    assert "222" not in serialized
    assert "333" not in serialized
    assert "444" not in serialized
    assert context.prompt_context["payer"]["alias"] == "me"
    assert context.prompt_context["friends"][0]["alias"] == "f1"
    assert context.prompt_context["groups"][0]["alias"] == "g1"
    assert context.prompt_context["groups"][0]["members"][0]["alias"] == "g1m1"
    assert context.member_aliases_by_group_alias["g1"] == {"g1m1"}


def test_ai_chat_context_preserves_group_member_alias_ownership():
    class FakeSplitwiseService:
        def get_current_user(self):
            return {"id": 1, "first_name": "Gunjan", "last_name": "Patil"}

        def get_friends(self):
            return []

        def get_groups(self):
            return [
                {
                    "id": 10,
                    "name": "A",
                    "members": [{"id": 20, "first_name": "Asha", "last_name": ""}],
                },
                {
                    "id": 11,
                    "name": "B",
                    "members": [{"id": 21, "first_name": "Bina", "last_name": ""}],
                },
            ]

    tx = type(
        "Tx",
        (),
        {
            "merchant_name": "Cafe",
            "name": "Cafe",
            "amount_cents": 1000,
            "iso_currency_code": "USD",
            "date": None,
        },
    )()
    context = AIChatContextService(FakeSplitwiseService()).build(tx, PendingTelegramSplit(1))

    assert context.member_aliases_by_group_alias["g1"] == {"g1m1"}
    assert context.member_aliases_by_group_alias["g2"] == {"g2m1"}
    assert context.member_by_alias["g1m1"]["id"] == 20
    assert context.member_by_alias["g2m1"]["id"] == 21


def test_ai_chat_context_includes_relevant_memories_without_raw_ids():
    class FakeSplitwiseService:
        def get_current_user(self):
            return {"id": 111, "first_name": "Gunjan"}

        def get_friends(self):
            return [{"id": 222, "first_name": "Janhavi"}]

        def get_groups(self):
            return []

    class FakeScalars:
        def __iter__(self):
            return iter(
                [
                    AIInterpretationMemory(
                        id=1,
                        original_message="split like last time",
                        failure_reason="low_confidence",
                        final_action="split_equal",
                        merchant="FUN",
                        final_participants=[{"user_id": 222, "display_name": "Janhavi"}],
                        final_split_mode="equal",
                    )
                ]
            )

    class FakeExecute:
        def scalars(self):
            return FakeScalars()

    class FakeDb:
        def execute(self, _stmt):
            return FakeExecute()

        def commit(self):
            pass

    tx = type(
        "Tx",
        (),
        {
            "merchant_name": "FUN",
            "name": "FUN",
            "amount_cents": 8940,
            "iso_currency_code": "USD",
            "date": None,
        },
    )()

    context = AIChatContextService(FakeSplitwiseService()).build(
        tx,
        PendingTelegramSplit(transaction_id=123),
        db=FakeDb(),
        user_message="split like last time",
    )

    serialized = json.dumps(context.prompt_context)
    assert "222" not in serialized
    assert (
        context.prompt_context["relevant_memories"][0]["original_phrase"]
        == "split like last time"
    )

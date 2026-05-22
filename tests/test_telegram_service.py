from datetime import date

import httpx

from app.config import Settings
from app.models import ExpenseTransaction, TransactionStatus
from app.services.telegram_service import (
    TelegramService,
    build_review_callback_data,
    build_review_inline_keyboard,
    format_ask_user_transaction_message,
    parse_review_callback_data,
)


def make_tx() -> ExpenseTransaction:
    return ExpenseTransaction(
        id=12,
        plaid_transaction_id="tx-1",
        plaid_item_id=1,
        merchant_name="Costco",
        name="Warehouse purchase",
        amount_cents=8734,
        iso_currency_code="USD",
        date=date(2026, 5, 22),
        category="Shops, Groceries",
        status=TransactionStatus.ASK_USER.value,
        agent_question="Is this shared?",
    )


def test_format_ask_user_transaction_message_includes_required_fields():
    message = format_ask_user_transaction_message(make_tx())

    assert "Costco" in message
    assert "USD 87.34" in message
    assert "ask_user" in message
    assert "likely_shared" in message
    assert "Is this shared?" in message


def test_build_review_inline_keyboard_has_safe_callback_data():
    keyboard = build_review_inline_keyboard(12)
    buttons = keyboard["inline_keyboard"][0]

    assert buttons[0]["callback_data"] == "review:personal:12"
    assert buttons[1]["callback_data"] == "review:draft:12"
    assert buttons[2]["callback_data"] == "review:split_equal:12"
    assert buttons[3]["callback_data"] == "review:split_people:12"


def test_parse_review_callback_data():
    parsed = parse_review_callback_data(build_review_callback_data("personal", 42))

    assert parsed.action == "personal"
    assert parsed.transaction_id == 42


def test_telegram_failure_does_not_raise_and_redacts_token(monkeypatch, caplog):
    settings = Settings(telegram_bot_token="secret-token", telegram_chat_id="chat-id")
    service = TelegramService(settings)

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def post(self, url, json):
            raise httpx.ConnectError(f"network down for {url}")

    monkeypatch.setattr("app.services.telegram_service.httpx.Client", FakeClient)

    service.send_ask_user_transaction(make_tx())

    assert "secret-token" not in caplog.text


def test_send_message_accepts_chat_id_override(monkeypatch):
    settings = Settings(telegram_bot_token="secret-token", telegram_chat_id="default-chat")
    service = TelegramService(settings)
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def post(self, url, json):
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("app.services.telegram_service.httpx.Client", FakeClient)

    service.send_message("hello", chat_id="override-chat")

    assert captured["json"]["chat_id"] == "override-chat"

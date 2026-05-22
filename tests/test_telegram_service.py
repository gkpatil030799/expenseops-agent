from datetime import date

import httpx

from app.config import Settings
from app.models import ExpenseTransaction, TransactionStatus
from app.services.telegram_service import TelegramService, format_ask_user_transaction_message


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

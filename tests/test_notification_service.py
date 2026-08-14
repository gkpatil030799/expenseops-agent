from app.config import Settings
from app.models import ExpenseTransaction
from app.services.notification_service import NotificationService


class TelegramRecorder:
    def __init__(self, result: bool = True):
        self.result = result
        self.messages: list[str] = []

    def send_message(self, message: str) -> bool:
        self.messages.append(message)
        return self.result


def test_splitwise_confirmation_uses_telegram_delivery() -> None:
    telegram = TelegramRecorder()
    tx = ExpenseTransaction(
        id=42,
        workspace_id=1,
        plaid_transaction_id="plaid-42",
        plaid_item_id=1,
        name="Aldi",
        amount_cents=4850,
        iso_currency_code="USD",
    )

    delivered = NotificationService(
        Settings(), telegram_service=telegram
    ).notify_splitwise_posted(tx, "splitwise-99")

    assert delivered is True
    assert telegram.messages == [
        "Added Splitwise expense for Aldi (USD 48.50). "
        "Splitwise expense id: splitwise-99."
    ]


def test_splitwise_confirmation_propagates_delivery_failure() -> None:
    telegram = TelegramRecorder(result=False)
    tx = ExpenseTransaction(
        id=42,
        workspace_id=1,
        plaid_transaction_id="plaid-42",
        plaid_item_id=1,
        name="Aldi",
        amount_cents=4850,
        iso_currency_code="USD",
    )

    delivered = NotificationService(
        Settings(), telegram_service=telegram
    ).notify_splitwise_posted(tx, "splitwise-99")

    assert delivered is False

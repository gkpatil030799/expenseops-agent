from __future__ import annotations

import logging

from app.config import Settings, get_settings
from app.logging_config import log_event
from app.models import ExpenseTransaction
from app.services.agent_service import transaction_display_name
from app.services.share_calculator import cents_to_decimal_string
from app.services.telegram_service import TelegramService

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(
        self,
        settings: Settings | None = None,
        telegram_service: TelegramService | None = None,
    ):
        self.settings = settings or get_settings()
        self.telegram_service = telegram_service or TelegramService(self.settings)

    def notify_transaction_needs_review(self, tx: ExpenseTransaction) -> bool:
        log_event(
            logger,
            "transaction_review_notification_started",
            tx_id=tx.id,
            status=tx.status,
            pending=tx.pending,
        )
        sent = self.telegram_service.send_ask_user_transaction(tx)
        log_event(
            logger,
            "transaction_review_notification_completed",
            tx_id=tx.id,
            sent=sent,
        )
        return sent

    def notify_splitwise_posted(
        self, tx: ExpenseTransaction, splitwise_expense_id: str | None
    ) -> None:
        message = (
            f"Added Splitwise expense for {transaction_display_name(tx)} "
            f"({tx.iso_currency_code} {cents_to_decimal_string(abs(tx.amount_cents))}). "
            f"Splitwise expense id: {splitwise_expense_id or 'unknown'}."
        )
        self._send(message)

    def _send(self, message: str) -> None:
        logger.info("ExpenseOps notification: %s", message)

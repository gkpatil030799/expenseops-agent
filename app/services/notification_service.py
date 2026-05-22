from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.models import ExpenseTransaction
from app.services.agent_service import transaction_display_name
from app.services.share_calculator import cents_to_decimal_string

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def notify_transaction_needs_review(self, tx: ExpenseTransaction) -> None:
        message = (
            f"New transaction needs review: {transaction_display_name(tx)} "
            f"{tx.iso_currency_code} {cents_to_decimal_string(abs(tx.amount_cents))}. "
            f"Open /transactions/{tx.id} to classify it."
        )
        self._send(message)

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
        if self.settings.telegram_bot_token and self.settings.telegram_chat_id:
            self._send_telegram(message)
        else:
            logger.info("ExpenseOps notification: %s", message)

    def _send_telegram(self, message: str) -> None:
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"
        payload: dict[str, Any] = {"chat_id": self.settings.telegram_chat_id, "text": message}
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
        except Exception as exc:  # pragma: no cover - notification should not break ingestion
            logger.warning("Telegram notification failed: %s", exc)

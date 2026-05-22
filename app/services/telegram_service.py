from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.models import ExpenseTransaction
from app.services.agent_service import transaction_display_name
from app.services.recommendation_service import classify_transaction_recommendation
from app.services.share_calculator import cents_to_decimal_string

logger = logging.getLogger(__name__)


class TelegramService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.telegram_bot_token and self.settings.telegram_chat_id)

    def send_ask_user_transaction(self, tx: ExpenseTransaction) -> None:
        self.send_message(format_ask_user_transaction_message(tx))

    def send_message(self, message: str) -> None:
        if not self.is_configured:
            logger.info(
                "Telegram notification skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing."
            )
            return

        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": self.settings.telegram_chat_id,
            "text": message,
            "disable_web_page_preview": True,
        }
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
        except Exception as exc:
            logger.warning("Telegram notification failed: %s", self._safe_error(exc))

    def _safe_error(self, exc: Exception) -> str:
        message = str(exc)
        if self.settings.telegram_bot_token:
            message = message.replace(self.settings.telegram_bot_token, "[redacted-bot-token]")
        return message


def format_ask_user_transaction_message(tx: ExpenseTransaction) -> str:
    classification = classify_transaction_recommendation(
        merchant_name=tx.merchant_name,
        name=tx.name,
        amount_cents=tx.amount_cents,
        category=tx.category,
    )
    amount = cents_to_decimal_string(abs(tx.amount_cents))
    question = tx.agent_question or "Review this transaction."

    return "\n".join(
        [
            "ExpenseOps review needed",
            f"Merchant: {transaction_display_name(tx)}",
            f"Amount: {tx.iso_currency_code} {amount}",
            f"Status: {tx.status}",
            f"Recommendation: {classification.suggestion}",
            f"Reason: {classification.reason}",
            f"Question: {question}",
        ]
    )

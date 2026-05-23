from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.models import ExpenseTransaction
from app.services.agent_service import friend_display_name, transaction_display_name
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
        self.send_message(
            format_ask_user_transaction_message(tx),
            reply_markup=build_review_inline_keyboard(tx.id),
        )

    def send_message(
        self,
        message: str,
        reply_markup: dict[str, Any] | None = None,
        chat_id: str | None = None,
    ) -> None:
        if not self.is_configured:
            logger.info(
                "Telegram notification skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing."
            )
            return

        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": chat_id or self.settings.telegram_chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
        except Exception as exc:
            logger.warning("Telegram notification failed: %s", self._safe_error(exc))

    def answer_callback_query(self, callback_query_id: str, text: str) -> None:
        if not self.is_configured:
            logger.info("Telegram callback answer skipped: Telegram is not configured.")
            return

        url = (
            f"https://api.telegram.org/bot{self.settings.telegram_bot_token}"
            "/answerCallbackQuery"
        )
        payload = {
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": False,
        }
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
        except Exception as exc:
            logger.warning("Telegram callback answer failed: %s", self._safe_error(exc))

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
            "🧾 <b>ExpenseOps review needed</b>",
            "",
            f"🏪 <b>Merchant</b>: {html(transaction_display_name(tx))}",
            f"💳 <b>Amount</b>: {html(tx.iso_currency_code)} {html(amount)}",
            f"📌 <b>Status</b>: {html(tx.status)}",
            f"🧠 <b>Recommendation</b>: {html(classification.suggestion)}",
            f"ℹ️ <b>Reason</b>: {html(classification.reason)}",
            "",
            f"❓ <b>Question</b>: {html(question)}",
        ]
    )


def compact_transaction_title(tx: ExpenseTransaction) -> str:
    amount = cents_to_decimal_string(abs(tx.amount_cents))
    name = transaction_display_name(tx)
    if (tx.iso_currency_code or "USD").upper() == "USD":
        return f"${amount} {name}"
    return f"{tx.iso_currency_code} {amount} {name}"


def format_transaction_review_prompt(
    tx: ExpenseTransaction,
    *,
    completed_count: int,
    total_count: int,
) -> str:
    current = min(completed_count + 1, max(total_count, 1))
    return "\n".join(
        [
            f"<b>Transaction {current} of {max(total_count, 1)}</b>",
            f"Done: {completed_count} / {max(total_count, 1)}",
            "",
            f"<b>{html(compact_transaction_title(tx))}</b>",
            "Choose action:",
        ]
    )


@dataclass(frozen=True)
class TelegramReviewCallback:
    action: str
    transaction_id: int


def build_review_callback_data(action: str, transaction_id: int) -> str:
    if action not in {
        "personal",
        "button_mode",
        "ai_chat",
        "draft",
        "split_equal",
        "split_people",
        "split_group",
        "search_friend",
        "search_group",
        "done",
        "confirm",
        "undo",
        "cancel",
    }:
        raise ValueError("Unsupported Telegram review action")
    if transaction_id <= 0:
        raise ValueError("Invalid Telegram transaction id")
    return f"review:{action}:{transaction_id}"


def parse_review_callback_data(data: str) -> TelegramReviewCallback:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "review":
        raise ValueError("Invalid Telegram callback payload")
    action = parts[1]
    if action not in {
        "personal",
        "button_mode",
        "ai_chat",
        "draft",
        "split_equal",
        "split_people",
        "split_group",
        "search_friend",
        "search_group",
        "done",
        "confirm",
        "undo",
        "cancel",
    }:
        raise ValueError("Unsupported Telegram review action")
    try:
        transaction_id = int(parts[2])
    except ValueError as exc:
        raise ValueError("Invalid Telegram transaction id") from exc
    if transaction_id <= 0:
        raise ValueError("Invalid Telegram transaction id")
    return TelegramReviewCallback(action=action, transaction_id=transaction_id)


def build_review_inline_keyboard(transaction_id: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Button mode",
                    "callback_data": build_review_callback_data("button_mode", transaction_id),
                },
                {
                    "text": "AI chat mode",
                    "callback_data": build_review_callback_data("ai_chat", transaction_id),
                },
            ]
        ]
    }


def build_button_mode_keyboard(transaction_id: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Personal",
                    "callback_data": build_review_callback_data("personal", transaction_id),
                },
                {
                    "text": "Create draft only",
                    "callback_data": build_review_callback_data("draft", transaction_id),
                },
            ],
            [
                {
                    "text": "People",
                    "callback_data": build_review_callback_data("split_people", transaction_id),
                },
                {
                    "text": "Group",
                    "callback_data": build_review_callback_data("split_group", transaction_id),
                },
            ]
        ]
    }


def build_friend_choice_callback_data(transaction_id: int, friend_id: int) -> str:
    if transaction_id <= 0 or friend_id <= 0:
        raise ValueError("Invalid Telegram friend choice payload")
    return f"friend:{transaction_id}:{friend_id}"


def parse_friend_choice_callback_data(data: str) -> tuple[int, int]:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "friend":
        raise ValueError("Invalid Telegram friend choice payload")
    try:
        transaction_id = int(parts[1])
        friend_id = int(parts[2])
    except ValueError as exc:
        raise ValueError("Invalid Telegram friend choice payload") from exc
    if transaction_id <= 0 or friend_id <= 0:
        raise ValueError("Invalid Telegram friend choice payload")
    return transaction_id, friend_id


def build_friend_choice_keyboard(transaction_id: int, friends: list[dict]) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": friend_display_name(friend),
                    "callback_data": build_friend_choice_callback_data(
                        transaction_id,
                        int(friend["id"]),
                    ),
                }
            ]
            for friend in friends[:8]
        ]
        + [
            [
                {
                    "text": "Cancel",
                    "callback_data": build_review_callback_data("cancel", transaction_id),
                }
            ]
        ]
    }


def build_friend_select_keyboard(
    transaction_id: int,
    friends: list[dict],
    selected_friend_ids: list[int],
) -> dict[str, Any]:
    return {
        "inline_keyboard": _participant_rows(
            transaction_id,
            friends,
            selected_friend_ids,
        )
        + [
            [
                {
                    "text": "Search by name",
                    "callback_data": build_review_callback_data("search_friend", transaction_id),
                }
            ],
            _done_cancel_row(transaction_id),
        ]
    }


def build_group_choice_callback_data(transaction_id: int, group_id: int) -> str:
    if transaction_id <= 0 or group_id <= 0:
        raise ValueError("Invalid Telegram group choice payload")
    return f"group:{transaction_id}:{group_id}"


def parse_group_choice_callback_data(data: str) -> tuple[int, int]:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "group":
        raise ValueError("Invalid Telegram group choice payload")
    try:
        transaction_id = int(parts[1])
        group_id = int(parts[2])
    except ValueError as exc:
        raise ValueError("Invalid Telegram group choice payload") from exc
    if transaction_id <= 0 or group_id <= 0:
        raise ValueError("Invalid Telegram group choice payload")
    return transaction_id, group_id


def build_group_choice_keyboard(transaction_id: int, groups: list[dict]) -> dict[str, Any]:
    return {
        "inline_keyboard": _group_rows(transaction_id, groups)
        + [
            [
                {
                    "text": "Cancel",
                    "callback_data": build_review_callback_data("cancel", transaction_id),
                }
            ]
        ]
    }


def build_group_select_keyboard(transaction_id: int, groups: list[dict]) -> dict[str, Any]:
    return {
        "inline_keyboard": _group_rows(transaction_id, groups)
        + [
            [
                {
                    "text": "Search group by name",
                    "callback_data": build_review_callback_data("search_group", transaction_id),
                }
            ],
            _done_cancel_row(transaction_id),
        ]
    }


def _group_rows(transaction_id: int, groups: list[dict]) -> list[list[dict[str, str]]]:
    rows: list[list[dict[str, str]]] = []
    for group in groups:
        try:
            group_id = int(group["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if group_id <= 0:
            continue
        rows.append(
            [
                {
                    "text": str(group.get("name") or group_id),
                    "callback_data": build_group_choice_callback_data(transaction_id, group_id),
                }
            ]
        )
        if len(rows) == 8:
            break
    return rows


def build_group_member_select_keyboard(
    transaction_id: int,
    members: list[dict],
    selected_friend_ids: list[int],
    payer_user_id: int | None = None,
) -> dict[str, Any]:
    return {
        "inline_keyboard": _participant_rows(
            transaction_id,
            members,
            selected_friend_ids,
            payer_user_id=payer_user_id,
        )
        + [_done_cancel_row(transaction_id)]
    }


def build_split_flow_keyboard(transaction_id: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Cancel",
                    "callback_data": build_review_callback_data("cancel", transaction_id),
                }
            ]
        ]
    }


def _participant_rows(
    transaction_id: int,
    friends: list[dict],
    selected_friend_ids: list[int],
    payer_user_id: int | None = None,
) -> list[list[dict[str, str]]]:
    selected_ids = set(selected_friend_ids)
    rows = []
    for friend in friends[:8]:
        friend_id = int(friend["id"])
        name = friend_display_name(friend)
        if payer_user_id and friend_id == payer_user_id:
            rows.append([{"text": f"{name} · You / payer", "callback_data": "noop:payer"}])
            continue
        rows.append(
            [
                {
                    "text": f"✅ {name}" if friend_id in selected_ids else name,
                    "callback_data": build_friend_choice_callback_data(
                        transaction_id,
                        friend_id,
                    ),
                }
            ]
        )
    return rows


def _done_cancel_row(transaction_id: int) -> list[dict[str, str]]:
    return [
        {
            "text": "Done",
            "callback_data": build_review_callback_data("done", transaction_id),
        },
        {
            "text": "Cancel",
            "callback_data": build_review_callback_data("cancel", transaction_id),
        },
    ]


def build_split_confirmation_keyboard(transaction_id: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Confirm split",
                    "callback_data": build_review_callback_data("confirm", transaction_id),
                },
                {
                    "text": "Cancel",
                    "callback_data": build_review_callback_data("cancel", transaction_id),
                },
            ]
        ]
    }


def build_undo_keyboard(transaction_id: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Undo",
                    "callback_data": build_review_callback_data("undo", transaction_id),
                }
            ]
        ]
    }


def format_personal_success_message(tx: ExpenseTransaction) -> str:
    return f"✅ <b>{html(compact_transaction_title(tx))}</b>\nMarked personal."


def format_undo_success_message(tx: ExpenseTransaction) -> str:
    merchant = transaction_display_name(tx)
    amount = cents_to_decimal_string(abs(tx.amount_cents))
    currency_code = tx.iso_currency_code or "USD"
    header = f"{merchant} — {currency_code} {amount}"
    return f"↩️ <b>{html(header)}</b> moved back to review."


def format_button_mode_message(transaction_title: str) -> str:
    return "\n".join(
        [
            f"<b>{html(transaction_title)}</b>",
            "Choose action:",
        ]
    )


def format_ai_chat_prompt(transaction_title: str) -> str:
    return "\n".join(
        [
            f"<b>{html(transaction_title)}</b>",
            "Tell me what to do. Examples:",
            "- mark personal",
            "- split with Rahul and Akash",
            "- split in Apartment group with Rahul and Akash",
        ]
    )


def format_split_started_message(
    selected_names: list[str] | None = None,
    transaction_title: str | None = None,
) -> str:
    selected = selected_names or []
    participants = ", ".join(html(name) for name in selected) if selected else "None yet"
    return "\n".join(
        [
            f"<b>{html(transaction_title)}</b>" if transaction_title else "👥 <b>People</b>",
            "",
            "Type person name or select friends below.",
            "<i>Example: Rahul, Akash</i>",
            "",
            f"✅ <b>Selected participants</b>: {participants}",
        ]
    )


def format_group_started_message(transaction_title: str | None = None) -> str:
    return "\n".join(
        [
            f"<b>{html(transaction_title)}</b>" if transaction_title else "🏘️ <b>Group</b>",
            "",
            "Type group name or choose a recent group.",
        ]
    )


def format_group_ambiguity_message(name: str) -> str:
    return "\n".join(
        [
            "🔎 <b>Multiple groups found</b>",
            "",
            f"I found more than one Splitwise group for <b>{html(name)}</b>.",
            "Choose the correct group below.",
        ]
    )


def format_group_members_prompt(
    group_name: str,
    selected_names: list[str] | None = None,
    transaction_title: str | None = None,
) -> str:
    selected = selected_names or []
    participants = ", ".join(html(name) for name in selected) if selected else "None yet"
    return "\n".join(
        [
            (
                f"<b>{html(transaction_title)}</b>"
                if transaction_title
                else f"🏘️ <b>{html(group_name)}</b>"
            ),
            "",
            f"Group: <b>{html(group_name)}</b>",
            "Select group members below or send names separated by commas.",
            "<i>Example: Rahul, Akash</i>",
            "",
            f"✅ <b>Selected participants</b>: {participants}",
        ]
    )


def format_ambiguity_message(name: str, selected_names: list[str]) -> str:
    participants = (
        ", ".join(html(friend) for friend in selected_names) if selected_names else "None yet"
    )
    return "\n".join(
        [
            "🔎 <b>Multiple matches found</b>",
            "",
            f"I found more than one Splitwise friend for <b>{html(name)}</b>.",
            "Choose the correct person below.",
            "",
            f"✅ <b>Already selected</b>: {participants}",
        ]
    )


def format_split_success_message(
    *,
    merchant: str,
    amount: str,
    currency_code: str,
    participant_names: list[str],
    approx_share: str,
) -> str:
    participants = ", ".join(html(name) for name in participant_names)
    return "\n".join(
        [
            "✅ <b>Split posted to Splitwise</b>",
            "",
            f"🏪 <b>Merchant</b>: {html(merchant)}",
            f"💳 <b>Amount</b>: {html(currency_code)} {html(amount)}",
            f"👥 <b>Participants</b>: {participants}",
            f"🧾 <b>Approx. share</b>: {html(currency_code)} {html(approx_share)} each",
        ]
    )


def format_split_confirmation_message(
    *,
    merchant: str,
    amount: str,
    currency_code: str,
    payer_name: str,
    participant_names: list[str],
    approx_share: str,
) -> str:
    participants = ", ".join(html(name) for name in participant_names)
    return "\n".join(
        [
            "🧾 <b>Confirm split</b>",
            "",
            f"<b>{html(currency_code)} {html(amount)} {html(merchant)}</b>",
            f"Payer: {html(payer_name)}",
            f"Participants: {participants}",
            f"Equal split: approx. {html(currency_code)} {html(approx_share)} each",
        ]
    )


def format_completion_summary(completed_titles: list[str]) -> str:
    lines = ["✅ <b>All caught up</b>"]
    if completed_titles:
        lines.extend(["", *[f"✅ {html(title)}" for title in completed_titles]])
    return "\n".join(lines)


def approximate_equal_share_display(amount_cents: int, participant_count: int) -> str:
    if participant_count <= 0:
        return "0.00"
    amount = Decimal(abs(amount_cents)) / Decimal("100")
    share = (amount / Decimal(participant_count)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return f"{share:.2f}"


def html(value: object) -> str:
    return escape(str(value), quote=False)

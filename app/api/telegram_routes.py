from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.api.deps import DbSession
from app.services.splitwise_service import SplitwiseAPIError, SplitwiseService
from app.services.telegram_service import (
    TelegramService,
    build_friend_choice_keyboard,
    parse_friend_choice_callback_data,
    parse_review_callback_data,
)
from app.services.telegram_state_service import (
    PendingTelegramSplit,
    find_friend_matches,
    parse_split_names,
    telegram_split_state_store,
)
from app.services.transaction_service import TransactionError, TransactionService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(request: Request, db: DbSession) -> dict[str, bool]:
    update = await request.json()
    callback_query = update.get("callback_query")
    if callback_query:
        return _handle_callback_query(callback_query, db)

    message = update.get("message")
    if message:
        _handle_text_message(message, db)
    return {"ok": True}


def _handle_callback_query(callback_query: dict, db: DbSession) -> dict[str, bool]:
    callback_query_id = str(callback_query.get("id") or "")
    callback_data = str(callback_query.get("data") or "")
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    from_user = callback_query.get("from") or {}
    chat_id = str(chat.get("id") or "")
    user_id = str(from_user.get("id") or "")
    telegram = TelegramService()

    try:
        callback = parse_review_callback_data(callback_data)
    except ValueError as exc:
        answer = _try_route_friend_choice(callback_data, chat_id, user_id, db, telegram) or str(exc)
    else:
        answer = _route_review_callback(
            callback.action,
            callback.transaction_id,
            chat_id,
            user_id,
            db,
            telegram,
        )

    if callback_query_id:
        telegram.answer_callback_query(callback_query_id, answer)
    return {"ok": True}


def _route_review_callback(
    action: str,
    transaction_id: int,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> str:
    if action == "personal":
        return _mark_personal(transaction_id, db)
    if action == "draft":
        return "Select friends in the dashboard before creating a draft."
    if action == "split_equal":
        return "Select friends in the dashboard before splitting equally."
    if action == "split_people":
        if chat_id and user_id:
            telegram_split_state_store.set_pending(chat_id, user_id, transaction_id)
            telegram.send_message(
                "Send Splitwise friend names separated by commas. Example: Rahul, Akash",
                chat_id=chat_id,
            )
        return "Send friend names in this chat."
    return "Unsupported action."


def _handle_text_message(message: dict, db: DbSession) -> None:
    chat_id = str((message.get("chat") or {}).get("id") or "")
    user_id = str((message.get("from") or {}).get("id") or "")
    text = str(message.get("text") or "").strip()
    telegram = TelegramService()
    pending = telegram_split_state_store.get_pending(chat_id, user_id)
    if not pending or not text:
        return

    names = parse_split_names(text)
    if not names:
        telegram.send_message(
            "Send Splitwise friend names separated by commas. Example: Rahul, Akash",
            chat_id=chat_id,
        )
        return

    try:
        friends = SplitwiseService().get_friends()
    except SplitwiseAPIError:
        telegram.send_message(
            "Could not search Splitwise friends. Try again from the dashboard.",
            chat_id=chat_id,
        )
        return

    pending.selected_friend_ids = []
    pending.remaining_unresolved_names = []
    pending.ambiguous_matches_by_name = {}

    for name in names:
        matches = find_friend_matches(name, friends)
        if not matches:
            telegram.send_message(
                f"No Splitwise friend matched '{name}'. Try again.",
                chat_id=chat_id,
            )
            return
        if len(matches) == 1:
            pending.add_friend_id(int(matches[0]["id"]))
            continue
        pending.remaining_unresolved_names.append(name)
        pending.ambiguous_matches_by_name[name] = matches

    _continue_or_finish_pending_split(pending, chat_id, user_id, db, telegram)


def _try_route_friend_choice(
    callback_data: str,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> str | None:
    try:
        transaction_id, friend_id = parse_friend_choice_callback_data(callback_data)
    except ValueError:
        return None

    pending = telegram_split_state_store.get_pending(chat_id, user_id)
    if not pending or pending.transaction_id != transaction_id:
        return "No pending split found. Start again from the latest transaction message."

    pending.add_friend_id(friend_id)
    if pending.remaining_unresolved_names:
        resolved_name = pending.remaining_unresolved_names.pop(0)
        pending.ambiguous_matches_by_name.pop(resolved_name, None)

    _continue_or_finish_pending_split(pending, chat_id, user_id, db, telegram)
    return "Selection saved."


def _continue_or_finish_pending_split(
    pending: PendingTelegramSplit,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    next_name = pending.next_ambiguous_name()
    if next_name:
        telegram.send_message(
            f"Multiple matches for '{next_name}'. Choose one:",
            reply_markup=build_friend_choice_keyboard(
                pending.transaction_id,
                pending.ambiguous_matches_by_name[next_name],
            ),
            chat_id=chat_id,
        )
        return

    _split_equal_from_telegram(
        pending.transaction_id,
        pending.selected_friend_ids,
        chat_id,
        user_id,
        db,
        telegram,
    )


def _split_equal_from_telegram(
    transaction_id: int,
    friend_user_ids: list[int],
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    try:
        tx = TransactionService(db).get_transaction(transaction_id)
        if tx.splitwise_expense_id:
            telegram.send_message(
                "This transaction was already posted to Splitwise.",
                chat_id=chat_id,
            )
            telegram_split_state_store.clear(chat_id, user_id)
            return
        TransactionService(db).create_equal_split_expense(
            tx_id=transaction_id,
            friend_user_ids=friend_user_ids,
            group_id=None,
            description=None,
            details=None,
            currency_code=None,
            confirm=True,
            post_pending=False,
        )
    except (TransactionError, SplitwiseAPIError, ValueError):
        telegram.send_message(
            "Could not create the split. Open the dashboard to review.",
            chat_id=chat_id,
        )
        return
    telegram_split_state_store.clear(chat_id, user_id)
    telegram.send_message("Split posted to Splitwise.", chat_id=chat_id)


def _mark_personal(transaction_id: int, db: DbSession) -> str:
    try:
        TransactionService(db).mark_personal(transaction_id)
    except TransactionError as exc:
        logger.info("Telegram personal action could not be completed: %s", exc)
        return "Could not mark personal. Open the dashboard to review."
    return "Marked as personal."

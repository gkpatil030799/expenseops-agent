from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from app.api.deps import DbSession
from app.config import get_settings
from app.models import ExpenseTransaction, TransactionStatus
from app.services.agent_service import friend_display_name
from app.services.share_calculator import cents_to_decimal_string
from app.services.splitwise_service import SplitwiseAPIError, SplitwiseService
from app.services.telegram_service import (
    TelegramService,
    approximate_equal_share_display,
    build_friend_choice_keyboard,
    build_friend_select_keyboard,
    build_group_choice_keyboard,
    build_group_member_select_keyboard,
    build_group_select_keyboard,
    build_review_inline_keyboard,
    build_split_confirmation_keyboard,
    build_split_flow_keyboard,
    build_undo_keyboard,
    compact_transaction_title,
    format_ambiguity_message,
    format_completion_summary,
    format_group_ambiguity_message,
    format_group_members_prompt,
    format_group_started_message,
    format_personal_success_message,
    format_split_confirmation_message,
    format_split_started_message,
    format_split_success_message,
    format_transaction_review_prompt,
    format_undo_success_message,
    parse_friend_choice_callback_data,
    parse_group_choice_callback_data,
    parse_review_callback_data,
)
from app.services.telegram_state_service import (
    PendingTelegramSplit,
    find_friend_matches,
    find_group_matches,
    parse_split_names,
    telegram_review_queue_store,
    telegram_split_state_store,
)
from app.services.transaction_service import TransactionError, TransactionService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    db: DbSession,
    secret: str | None = Query(default=None),
) -> dict[str, bool]:
    _verify_webhook_secret(secret)
    update = await request.json()
    callback_query = update.get("callback_query")
    if callback_query:
        return _handle_callback_query(callback_query, db)

    message = update.get("message")
    if message:
        _handle_text_message(message, db)
    return {"ok": True}


def _verify_webhook_secret(incoming_secret: str | None) -> None:
    expected_secret = get_settings().telegram_webhook_secret
    if expected_secret and incoming_secret != expected_secret:
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")


def _handle_callback_query(callback_query: dict, db: DbSession) -> dict[str, bool]:
    callback_query_id = str(callback_query.get("id") or "")
    callback_data = str(callback_query.get("data") or "")
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    from_user = callback_query.get("from") or {}
    chat_id = str(chat.get("id") or "")
    user_id = str(from_user.get("id") or "")
    telegram = TelegramService()

    if callback_data.startswith("noop:"):
        answer = "You are already included as payer."
        if callback_query_id:
            telegram.answer_callback_query(callback_query_id, answer)
        return {"ok": True}

    try:
        callback = parse_review_callback_data(callback_data)
    except ValueError as exc:
        if callback_data.startswith("review:"):
            answer = str(exc)
        else:
            answer = (
                _try_route_group_choice(callback_data, chat_id, user_id, telegram)
                or _try_route_friend_choice(callback_data, chat_id, user_id, db, telegram)
                or str(exc)
            )
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
        return _mark_personal(transaction_id, chat_id, user_id, db, telegram)
    if action == "undo":
        return _undo_transaction(transaction_id, chat_id, user_id, db, telegram)
    if action == "draft":
        return _mark_shared_draft(transaction_id, chat_id, user_id, db, telegram)
    if action == "split_equal":
        return "Select friends in the dashboard before splitting equally."
    if action == "split_people":
        if chat_id and user_id:
            pending = telegram_split_state_store.set_pending(
                chat_id, user_id, transaction_id, mode="people_select"
            )
            title = _compact_title_for_transaction(transaction_id, db)
            pending.transaction_title = title
            pending.friend_options = _recent_friends()
            pending.remember_friends(pending.friend_options)
            telegram.send_message(
                format_split_started_message(_selected_friend_names(pending), title),
                reply_markup=build_friend_select_keyboard(
                    transaction_id,
                    pending.friend_options,
                    pending.selected_friend_ids,
                ),
                chat_id=chat_id,
            )
        return "Type a person name or select friends."
    if action == "split_group":
        if chat_id and user_id:
            pending = telegram_split_state_store.set_pending(
                chat_id,
                user_id,
                transaction_id,
                mode="group_select",
            )
            title = _compact_title_for_transaction(transaction_id, db)
            pending.transaction_title = title
            pending.group_options = _splitwise_groups()
            telegram.send_message(
                format_group_started_message(title),
                reply_markup=build_group_select_keyboard(transaction_id, pending.group_options),
                chat_id=chat_id,
            )
        return "Type a group name or choose a group."
    if action == "search_friend":
        pending = telegram_split_state_store.get_pending(chat_id, user_id)
        if pending and pending.transaction_id == transaction_id:
            pending.mode = "people_search"
            telegram.send_message(
                "\n".join(
                    [
                        f"<b>{_compact_title_for_transaction(transaction_id, db)}</b>",
                        "Type person name:",
                    ]
                ),
                chat_id=chat_id,
            )
            return "Type a friend name."
        return "No pending split found. Start again from the latest transaction message."
    if action == "search_group":
        pending = telegram_split_state_store.get_pending(chat_id, user_id)
        if pending and pending.transaction_id == transaction_id:
            pending.mode = "group_search"
            telegram.send_message(
                "\n".join(
                    [
                        f"<b>{_compact_title_for_transaction(transaction_id, db)}</b>",
                        "Type group name:",
                    ]
                ),
                chat_id=chat_id,
            )
            return "Type a group name."
        return "No pending split found. Start again from the latest transaction message."
    if action == "done":
        pending = telegram_split_state_store.get_pending(chat_id, user_id)
        if not pending or pending.transaction_id != transaction_id:
            return "No pending split found. Start again from the latest transaction message."
        if pending.is_submitting:
            return "Split already being processed."
        if not pending.selected_friend_ids:
            return "Select at least one person before tapping Done."
        _send_split_confirmation(pending, chat_id, db, telegram)
        return "Review and confirm the split."
    if action == "confirm":
        pending = telegram_split_state_store.get_pending(chat_id, user_id)
        if not pending or pending.transaction_id != transaction_id:
            return "No pending split found. Start again from the latest transaction message."
        if pending.is_submitting:
            return "Split already being processed."
        if not pending.selected_friend_ids:
            return "Select at least one person before confirming."
        pending.is_submitting = True
        _split_equal_from_telegram(
            pending.transaction_id,
            _participant_friend_ids(pending),
            _participant_friend_names(pending),
            pending.selected_group_id,
            chat_id,
            user_id,
            db,
            telegram,
        )
        return "Creating split."
    if action == "cancel":
        if chat_id and user_id:
            telegram_split_state_store.clear(chat_id, user_id)
            telegram.send_message("✅ Split flow cancelled.", chat_id=chat_id)
        return "Split flow cancelled."
    return "Unsupported action."


def _handle_text_message(message: dict, db: DbSession) -> None:
    chat_id = str((message.get("chat") or {}).get("id") or "")
    user_id = str((message.get("from") or {}).get("id") or "")
    text = str(message.get("text") or "").strip()
    telegram = TelegramService()
    pending = telegram_split_state_store.get_pending(chat_id, user_id)
    if not pending or not text:
        return

    if pending.mode in {"group_name", "group_search"}:
        _handle_group_name_message(pending, text, chat_id, telegram)
        return
    if pending.mode == "group_members":
        _handle_group_member_names(pending, text, chat_id, user_id, db, telegram)
        return
    if pending.mode == "people_search":
        _handle_friend_search_message(pending, text, chat_id, telegram)
        return

    names = parse_split_names(text)
    if not names:
        telegram.send_message(
            format_split_started_message(
                _selected_friend_names(pending),
                pending.transaction_title
                or _compact_title_for_transaction(pending.transaction_id, db),
            ),
            reply_markup=build_split_flow_keyboard(pending.transaction_id),
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
    pending.remember_friends(friends)

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
            pending.add_friend(int(matches[0]["id"]), friend_display_name(matches[0]))
            continue
        pending.remember_friends(matches)
        pending.remaining_unresolved_names.append(name)
        pending.ambiguous_matches_by_name[name] = matches

    _continue_or_finish_pending_split(pending, chat_id, user_id, db, telegram)


def _handle_group_name_message(
    pending: PendingTelegramSplit,
    text: str,
    chat_id: str,
    telegram: TelegramService,
) -> None:
    try:
        groups = SplitwiseService().get_groups()
    except SplitwiseAPIError:
        telegram.send_message(
            "Could not search Splitwise groups. Try again from the dashboard.",
            chat_id=chat_id,
        )
        return

    matches = find_group_matches(text, groups)
    if not matches:
        telegram.send_message(f"No Splitwise group matched '{text}'. Try again.", chat_id=chat_id)
        return
    if len(matches) > 1:
        pending.ambiguous_groups_by_name[text] = matches
        telegram.send_message(
            format_group_ambiguity_message(text),
            reply_markup=build_group_choice_keyboard(pending.transaction_id, matches),
            chat_id=chat_id,
        )
        return

    _select_group_for_pending_split(pending, matches[0], chat_id, telegram)


def _handle_friend_search_message(
    pending: PendingTelegramSplit,
    text: str,
    chat_id: str,
    telegram: TelegramService,
) -> None:
    try:
        friends = SplitwiseService().get_friends()
    except SplitwiseAPIError:
        telegram.send_message(
            "Could not search Splitwise friends. Try again from the dashboard.",
            chat_id=chat_id,
        )
        return

    matches = find_friend_matches(text, friends)
    if not matches:
        telegram.send_message(f"No Splitwise friend matched '{text}'. Try again.", chat_id=chat_id)
        return
    pending.friend_options = matches
    pending.remember_friends(matches)
    pending.mode = "people_select"
    telegram.send_message(
        format_split_started_message(_selected_friend_names(pending)),
        reply_markup=build_friend_select_keyboard(
            pending.transaction_id,
            pending.friend_options,
            pending.selected_friend_ids,
        ),
        chat_id=chat_id,
    )


def _handle_group_member_names(
    pending: PendingTelegramSplit,
    text: str,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    names = parse_split_names(text)
    if not names:
        telegram.send_message(
            format_group_members_prompt(
                pending.selected_group_name or "Selected group",
                _selected_friend_names(pending),
                pending.transaction_title
                or _compact_title_for_transaction(pending.transaction_id, db),
            ),
            reply_markup=build_split_flow_keyboard(pending.transaction_id),
            chat_id=chat_id,
        )
        return

    pending.selected_friend_ids = []
    pending.selected_friend_names_by_id = {}
    pending.remaining_unresolved_names = []
    pending.ambiguous_matches_by_name = {}

    for name in names:
        matches = find_friend_matches(name, pending.group_members)
        if not matches:
            telegram.send_message(
                f"No group member matched '{name}'. Try again.",
                chat_id=chat_id,
            )
            return
        if len(matches) == 1:
            pending.add_friend(int(matches[0]["id"]), friend_display_name(matches[0]))
            continue
        pending.remember_friends(matches)
        pending.remaining_unresolved_names.append(name)
        pending.ambiguous_matches_by_name[name] = matches

    _continue_or_finish_pending_split(pending, chat_id, user_id, db, telegram)


def _select_group_for_pending_split(
    pending: PendingTelegramSplit,
    group: dict,
    chat_id: str,
    telegram: TelegramService,
) -> None:
    pending.selected_group_id = int(group["id"])
    pending.selected_group_name = str(group.get("name") or group["id"])
    pending.group_members = group.get("members", [])
    pending.remember_friends(pending.group_members)
    try:
        pending.payer_user_id = int(SplitwiseService().get_current_user()["id"])
    except (AttributeError, SplitwiseAPIError, KeyError, TypeError, ValueError):
        pending.payer_user_id = None
    pending.mode = "group_members"
    telegram.send_message(
        format_group_members_prompt(
            pending.selected_group_name,
            _selected_friend_names(pending),
            pending.transaction_title,
        ),
        reply_markup=build_group_member_select_keyboard(
            pending.transaction_id,
            pending.group_members,
            pending.selected_friend_ids,
            pending.payer_user_id,
        ),
        chat_id=chat_id,
    )


def _try_route_group_choice(
    callback_data: str,
    chat_id: str,
    user_id: str,
    telegram: TelegramService,
) -> str | None:
    try:
        transaction_id, group_id = parse_group_choice_callback_data(callback_data)
    except ValueError:
        return None

    pending = telegram_split_state_store.get_pending(chat_id, user_id)
    if not pending or pending.transaction_id != transaction_id:
        return "No pending split found. Start again from the latest transaction message."

    for groups in pending.ambiguous_groups_by_name.values():
        for group in groups:
            if int(group["id"]) == group_id:
                _select_group_for_pending_split(pending, group, chat_id, telegram)
                return "Group selected."
    for group in pending.group_options:
        if int(group["id"]) == group_id:
            _select_group_for_pending_split(pending, group, chat_id, telegram)
            return "Group selected."
    return "Could not find that group choice. Try again."


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

    if pending.mode in {"people_select", "people_search", "group_members"}:
        if pending.payer_user_id and friend_id == pending.payer_user_id:
            return "You are already included as payer."
        display_name = _friend_name_from_pending_choice(pending, friend_id)
        pending.toggle_friend(friend_id, display_name)
        if pending.mode == "group_members":
            telegram.send_message(
                format_group_members_prompt(
                    pending.selected_group_name or "Selected group",
                    _selected_friend_names(pending),
                    pending.transaction_title,
                ),
                reply_markup=build_group_member_select_keyboard(
                    pending.transaction_id,
                    pending.group_members,
                    pending.selected_friend_ids,
                    pending.payer_user_id,
                ),
                chat_id=chat_id,
            )
        else:
            pending.mode = "people_select"
            telegram.send_message(
                format_split_started_message(
                    _selected_friend_names(pending),
                    pending.transaction_title,
                ),
                reply_markup=build_friend_select_keyboard(
                    pending.transaction_id,
                    pending.friend_options,
                    pending.selected_friend_ids,
                ),
                chat_id=chat_id,
            )
        return "Selection updated."

    pending.add_friend(friend_id, _friend_name_from_pending_choice(pending, friend_id))
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
            format_ambiguity_message(next_name, _selected_friend_names(pending)),
            reply_markup=build_friend_choice_keyboard(
                pending.transaction_id,
                pending.ambiguous_matches_by_name[next_name],
            ),
            chat_id=chat_id,
        )
        return

    _send_split_confirmation(pending, chat_id, db, telegram)


def _split_equal_from_telegram(
    transaction_id: int,
    friend_user_ids: list[int],
    friend_names: list[str],
    group_id: int | None,
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
            tx.status = TransactionStatus.POSTED.value
            db.commit()
            _record_completion_and_show_next(tx, chat_id, user_id, db, telegram)
            return
        tx, _response = TransactionService(db).create_equal_split_expense(
            tx_id=transaction_id,
            friend_user_ids=friend_user_ids,
            group_id=group_id,
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
        telegram_split_state_store.clear(chat_id, user_id)
        return
    participant_names = ["You", *friend_names]
    telegram_split_state_store.clear(chat_id, user_id)
    amount = cents_to_decimal_string(abs(tx.amount_cents))
    approx_share = approximate_equal_share_display(tx.amount_cents, len(friend_user_ids) + 1)
    telegram.send_message(
        format_split_success_message(
            merchant=_safe_transaction_display_name(tx),
            amount=amount,
            currency_code=tx.iso_currency_code,
            participant_names=participant_names,
            approx_share=approx_share,
        ),
        reply_markup=build_undo_keyboard(tx.id),
        chat_id=chat_id,
    )
    _record_completion_and_show_next(tx, chat_id, user_id, db, telegram)


def _selected_friend_names(pending: PendingTelegramSplit) -> list[str]:
    return [
        pending.selected_friend_names_by_id.get(friend_id, str(friend_id))
        for friend_id in pending.selected_friend_ids
    ]


def _participant_friend_ids(pending: PendingTelegramSplit) -> list[int]:
    return [
        friend_id
        for friend_id in pending.selected_friend_ids
        if not pending.payer_user_id or friend_id != pending.payer_user_id
    ]


def _participant_friend_names(pending: PendingTelegramSplit) -> list[str]:
    return [
        pending.selected_friend_names_by_id.get(friend_id, str(friend_id))
        for friend_id in _participant_friend_ids(pending)
    ]


def _send_split_confirmation(
    pending: PendingTelegramSplit,
    chat_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    try:
        service = TransactionService(db)
        tx = service.get_transaction(pending.transaction_id)
        splitwise = getattr(service, "splitwise_service", SplitwiseService())
    except (AttributeError, TransactionError):
        telegram.send_message(
            "Could not prepare the split summary. Open the dashboard to review.",
            chat_id=chat_id,
        )
        return
    try:
        payer = splitwise.get_current_user()
        payer_name = friend_display_name(payer)
        pending.payer_user_id = int(payer["id"])
    except (AttributeError, SplitwiseAPIError, KeyError, TypeError, ValueError):
        payer_name = "You"

    friend_ids = _participant_friend_ids(pending)
    friend_names = _participant_friend_names(pending)
    amount_cents = int(getattr(tx, "amount_cents", 0))
    amount = cents_to_decimal_string(abs(amount_cents))
    approx_share = approximate_equal_share_display(amount_cents, len(friend_ids) + 1)
    pending.mode = "confirm"
    telegram.send_message(
        format_split_confirmation_message(
            merchant=_safe_transaction_display_name(tx),
            amount=amount,
            currency_code=getattr(tx, "iso_currency_code", "USD"),
            payer_name=payer_name,
            participant_names=friend_names,
            approx_share=approx_share,
        ),
        reply_markup=build_split_confirmation_keyboard(pending.transaction_id),
        chat_id=chat_id,
    )


def _friend_name_from_pending_choice(pending: PendingTelegramSplit, friend_id: int) -> str:
    if friend_id in pending.friend_lookup_by_id:
        return pending.friend_lookup_by_id[friend_id]
    for friend in [*pending.friend_options, *pending.group_members]:
        if int(friend["id"]) == friend_id:
            return friend_display_name(friend)
    for matches in pending.ambiguous_matches_by_name.values():
        for friend in matches:
            if int(friend["id"]) == friend_id:
                return friend_display_name(friend)
    return str(friend_id)


def _recent_friends() -> list[dict]:
    try:
        return SplitwiseService().get_friends()[:8]
    except SplitwiseAPIError:
        return []


def _splitwise_groups() -> list[dict]:
    try:
        return SplitwiseService().get_groups()[:8]
    except SplitwiseAPIError:
        return []


def _mark_personal(
    transaction_id: int,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> str:
    try:
        tx = TransactionService(db).mark_personal(transaction_id)
    except TransactionError as exc:
        logger.info("Telegram personal action could not be completed: %s", exc)
        return "Could not mark personal. Open the dashboard to review."
    telegram.send_message(
        format_personal_success_message(tx),
        reply_markup=build_undo_keyboard(tx.id),
        chat_id=chat_id,
    )
    _record_completion_and_show_next(tx, chat_id, user_id, db, telegram)
    return "Marked as personal."


def _undo_transaction(
    transaction_id: int,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> str:
    try:
        tx = TransactionService(db).undo_transaction(transaction_id)
    except TransactionError as exc:
        logger.info("Telegram undo action could not be completed: %s", exc)
        telegram.send_message(
            "Could not undo this transaction. Open the dashboard to review.",
            chat_id=chat_id,
        )
        return "Could not undo this transaction."

    telegram_review_queue_store.clear(chat_id, user_id)
    telegram_split_state_store.clear(chat_id, user_id)
    telegram.send_message(
        format_undo_success_message(tx),
        reply_markup=build_review_inline_keyboard(tx.id),
        chat_id=chat_id,
    )
    return "Transaction moved back to review."


def _mark_shared_draft(
    transaction_id: int,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> str:
    try:
        tx = TransactionService(db).mark_shared_draft(transaction_id)
    except TransactionError as exc:
        logger.info("Telegram draft action could not be completed: %s", exc)
        return "Could not create draft. Open the dashboard to review."
    _record_completion_and_show_next(tx, chat_id, user_id, db, telegram)
    return "Draft saved."


def _record_completion_and_show_next(
    tx: ExpenseTransaction,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    if not chat_id or not user_id:
        return
    queue = telegram_review_queue_store.get(chat_id, user_id)
    queue.mark_completed(compact_transaction_title(tx))
    telegram_split_state_store.clear(chat_id, user_id)
    _send_next_pending_transaction(chat_id, user_id, db, telegram)


def _send_next_pending_transaction(
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    queue = telegram_review_queue_store.get(chat_id, user_id)
    pending_transactions = _pending_review_transactions(db)
    queue.ensure_total(len(pending_transactions))
    if not pending_transactions:
        telegram.send_message(format_completion_summary(queue.completed_titles), chat_id=chat_id)
        telegram_review_queue_store.clear(chat_id, user_id)
        return

    next_tx = pending_transactions[0]
    telegram.send_message(
        format_transaction_review_prompt(
            next_tx,
            completed_count=len(queue.completed_titles),
            total_count=queue.initial_total,
        ),
        reply_markup=build_review_inline_keyboard(next_tx.id),
        chat_id=chat_id,
    )


def _pending_review_transactions(db: DbSession) -> list[ExpenseTransaction]:
    return list(
        db.execute(
            select(ExpenseTransaction)
            .where(ExpenseTransaction.status == TransactionStatus.ASK_USER.value)
            .order_by(ExpenseTransaction.created_at, ExpenseTransaction.id)
        ).scalars()
    )


def _compact_title_for_transaction(transaction_id: int, db: DbSession | None) -> str:
    if db is None:
        return f"Transaction {transaction_id}"
    try:
        tx = TransactionService(db).get_transaction(transaction_id)
    except (AttributeError, TransactionError):
        return f"Transaction {transaction_id}"
    try:
        return compact_transaction_title(tx)
    except AttributeError:
        return f"Transaction {transaction_id}"


def _safe_transaction_display_name(tx: ExpenseTransaction) -> str:
    return getattr(tx, "merchant_name", None) or getattr(tx, "name", None) or "Transaction"

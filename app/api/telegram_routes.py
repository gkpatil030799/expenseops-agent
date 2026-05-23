from __future__ import annotations

import json
import logging
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from app.api.deps import DbSession
from app.config import get_settings
from app.models import ExpenseTransaction, TransactionStatus
from app.services.agent_service import friend_display_name
from app.services.conversational_split_parser import parse_conversational_split
from app.services.custom_split_parser import ParsedCustomSplit, parse_custom_split_text
from app.services.share_calculator import (
    CustomSplitInput,
    build_custom_split_shares_by_mode,
    cents_to_decimal_string,
    decimal_to_cents,
)
from app.services.splitwise_service import SplitwiseAPIError, SplitwiseService
from app.services.telegram_service import (
    TelegramService,
    approximate_equal_share_display,
    build_button_mode_keyboard,
    build_custom_split_confirmation_keyboard,
    build_friend_choice_keyboard,
    build_friend_select_keyboard,
    build_group_choice_keyboard,
    build_group_member_select_keyboard,
    build_group_select_keyboard,
    build_review_inline_keyboard,
    build_split_confirmation_keyboard,
    build_split_flow_keyboard,
    build_split_target_keyboard,
    build_split_value_mode_keyboard,
    build_undo_keyboard,
    compact_transaction_title,
    format_ai_chat_prompt,
    format_ambiguity_message,
    format_button_mode_message,
    format_completion_summary,
    format_custom_split_confirmation_message,
    format_custom_split_success_message,
    format_custom_values_prompt,
    format_group_ambiguity_message,
    format_group_members_prompt,
    format_group_started_message,
    format_personal_success_message,
    format_split_confirmation_message,
    format_split_mode_prompt,
    format_split_started_message,
    format_split_success_message,
    format_split_target_prompt,
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
    message_id = message.get("message_id")
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
                _try_route_group_choice(callback_data, chat_id, user_id, db, telegram)
                or _try_route_friend_choice(
                    callback_data,
                    chat_id,
                    user_id,
                    db,
                    telegram,
                    int(message_id) if message_id else None,
                )
                or str(exc)
            )
    else:
        answer = _route_review_callback(
            callback.action,
            callback.transaction_id,
            chat_id,
            user_id,
            int(message_id) if message_id else None,
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
    message_id: int | None,
    db: DbSession,
    telegram: TelegramService,
) -> str:
    if action == "button_mode":
        title = _compact_title_for_transaction(transaction_id, db)
        _edit_or_send(
            telegram,
            format_button_mode_message(title),
            reply_markup=build_button_mode_keyboard(transaction_id),
            chat_id=chat_id,
            message_id=message_id,
        )
        return "Button mode selected."
    if action == "ai_chat":
        if chat_id and user_id:
            pending = telegram_split_state_store.set_pending(
                chat_id,
                user_id,
                transaction_id,
                mode="ai_chat",
            )
            pending.transaction_title = _compact_title_for_transaction(transaction_id, db)
            telegram.send_message(
                format_ai_chat_prompt(pending.transaction_title),
                reply_markup=build_split_flow_keyboard(transaction_id),
                chat_id=chat_id,
            )
        return "AI chat mode selected."
    if action == "personal":
        return _mark_personal(transaction_id, chat_id, user_id, db, telegram)
    if action == "undo":
        return _undo_transaction(transaction_id, chat_id, user_id, db, telegram)
    if action == "draft":
        return _mark_shared_draft(transaction_id, chat_id, user_id, db, telegram)
    if action == "split":
        if not chat_id or not user_id:
            return "This split session expired. Please start again from the transaction."
        pending = telegram_split_state_store.set_pending(
            chat_id,
            user_id,
            transaction_id,
            mode="split_target",
        )
        pending.transaction_title = _compact_title_for_transaction(transaction_id, db)
        _edit_or_send(
            telegram,
            format_split_target_prompt(pending.transaction_title),
            reply_markup=build_split_target_keyboard(transaction_id),
            chat_id=chat_id,
            message_id=message_id,
        )
        return "Choose people or group."
    if action == "split_equal":
        return "Select friends in the dashboard before splitting equally."
    if action == "split_people":
        if chat_id and user_id:
            pending = telegram_split_state_store.get_pending(chat_id, user_id)
            if not pending or pending.transaction_id != transaction_id:
                pending = telegram_split_state_store.set_pending(
                    chat_id,
                    user_id,
                    transaction_id,
                    mode="split_mode",
                )
            title = _compact_title_for_transaction(transaction_id, db)
            pending.transaction_title = title
            pending.split_target_mode = "people"
            pending.mode = "split_mode"
            _edit_or_send(
                telegram,
                format_split_mode_prompt(title, "people", pending.custom_payer_included),
                reply_markup=build_split_value_mode_keyboard(
                    transaction_id,
                    pending.custom_payer_included,
                ),
                chat_id=chat_id,
                message_id=message_id,
            )
        return "Choose split mode."
    if action == "split_group":
        if chat_id and user_id:
            pending = telegram_split_state_store.get_pending(chat_id, user_id)
            if not pending or pending.transaction_id != transaction_id:
                pending = telegram_split_state_store.set_pending(
                    chat_id,
                    user_id,
                    transaction_id,
                    mode="split_mode",
                )
            title = _compact_title_for_transaction(transaction_id, db)
            pending.transaction_title = title
            pending.split_target_mode = "group"
            pending.mode = "split_mode"
            _edit_or_send(
                telegram,
                format_split_mode_prompt(title, "group", pending.custom_payer_included),
                reply_markup=build_split_value_mode_keyboard(
                    transaction_id,
                    pending.custom_payer_included,
                ),
                chat_id=chat_id,
                message_id=message_id,
            )
        return "Choose split mode."
    if action.startswith("split_mode_"):
        pending = _require_pending(chat_id, user_id, transaction_id)
        if pending is None:
            return "This split session expired. Please start again from the transaction."
        pending.split_value_mode = {
            "split_mode_equal": "equal",
            "split_mode_amounts": "exact_amounts",
            "split_mode_percentages": "percentages",
            "split_mode_shares": "shares",
        }[action]
        return _start_participant_selection(pending, chat_id, db, telegram, message_id)
    if action == "toggle_payer_included":
        pending = _require_pending(chat_id, user_id, transaction_id)
        if pending is None:
            return "This split session expired. Please start again from the transaction."
        pending.custom_payer_included = not pending.custom_payer_included
        _edit_or_send(
            telegram,
            format_split_mode_prompt(
                pending.transaction_title or _compact_title_for_transaction(transaction_id, db),
                pending.split_target_mode or "people",
                pending.custom_payer_included,
            ),
            reply_markup=build_split_value_mode_keyboard(
                transaction_id,
                pending.custom_payer_included,
            ),
            chat_id=chat_id,
            message_id=message_id,
        )
        return "Payer setting updated."
    if action == "custom_split":
        if chat_id and user_id:
            pending = telegram_split_state_store.set_pending(
                chat_id,
                user_id,
                transaction_id,
                mode="custom_split",
            )
            pending.transaction_title = _compact_title_for_transaction(transaction_id, db)
            telegram.send_message(
                "\n".join(
                    [
                        f"<b>{pending.transaction_title}</b>",
                        "Type a custom split, for example:",
                        "split 20 with Rahul and 35 with Akash",
                        "split 60% Rahul 40% Akash",
                        "split shares Rahul 2 Akash 1 me 1",
                        "split with Rahul and Akash, exclude me",
                    ]
                ),
                reply_markup=build_split_flow_keyboard(transaction_id),
                chat_id=chat_id,
            )
        return "Type a custom split."
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
        pending = _require_pending(chat_id, user_id, transaction_id)
        if pending is None:
            return "This split session expired. Please start again from the transaction."
        if pending.is_submitting:
            return "Split already being processed."
        if not pending.selected_friend_ids:
            return "Select at least one person before tapping Done."
        if pending.split_value_mode == "equal":
            _send_split_confirmation(pending, chat_id, db, telegram)
            return "Review and confirm the split."
        pending.mode = "custom_values"
        telegram.send_message(
            format_custom_values_prompt(
                pending.transaction_title or _compact_title_for_transaction(transaction_id, db),
                pending.split_value_mode,
                _selected_friend_names(pending),
            ),
            reply_markup=build_split_flow_keyboard(transaction_id),
            chat_id=chat_id,
        )
        return "Send custom values."
    if action == "confirm":
        pending = _require_pending(chat_id, user_id, transaction_id)
        if pending is None:
            return "This split session expired. Please start again from the transaction."
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
    if action == "confirm_custom":
        pending = _require_pending(chat_id, user_id, transaction_id)
        if pending is None:
            return "This split session expired. Please start again from the transaction."
        if pending.is_submitting:
            return "Split already being processed."
        pending.is_submitting = True
        _post_custom_split_from_telegram(pending, chat_id, user_id, db, telegram)
        return "Creating custom split."
    if action == "cancel":
        pending = _require_pending(chat_id, user_id, transaction_id)
        if pending is None:
            return "This split session expired. Please start again from the transaction."
        telegram_split_state_store.clear(chat_id, user_id)
        telegram.send_message("✅ Split flow cancelled.", chat_id=chat_id)
        return "Split flow cancelled."
    return "Unsupported action."


def _edit_or_send(
    telegram: TelegramService,
    message: str,
    *,
    chat_id: str,
    message_id: int | None,
    reply_markup: dict | None = None,
) -> None:
    if message_id and hasattr(telegram, "edit_message"):
        telegram.edit_message(
            message,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
        )
        return
    telegram.send_message(message, reply_markup=reply_markup, chat_id=chat_id)


def _require_pending(
    chat_id: str,
    user_id: str,
    transaction_id: int,
) -> PendingTelegramSplit | None:
    pending = telegram_split_state_store.get_pending(chat_id, user_id)
    if not pending or pending.transaction_id != transaction_id:
        return None
    return pending


def _start_participant_selection(
    pending: PendingTelegramSplit,
    chat_id: str,
    db: DbSession,
    telegram: TelegramService,
    message_id: int | None,
) -> str:
    transaction_id = pending.transaction_id
    if pending.split_target_mode == "people":
        pending.mode = "people_select"
        pending.friend_options = _recent_friends()
        pending.remember_friends(pending.friend_options)
        _edit_or_send(
            telegram,
            format_split_started_message(
                _selected_friend_names(pending),
                pending.transaction_title or _compact_title_for_transaction(transaction_id, db),
            ),
            reply_markup=build_friend_select_keyboard(
                transaction_id,
                pending.friend_options,
                pending.selected_friend_ids,
            ),
            chat_id=chat_id,
            message_id=message_id,
        )
        return "Select people."

    if pending.split_target_mode == "group":
        pending.mode = "group_select"
        pending.group_options = _splitwise_groups()
        _edit_or_send(
            telegram,
            format_group_started_message(
                pending.transaction_title or _compact_title_for_transaction(transaction_id, db)
            ),
            reply_markup=build_group_select_keyboard(transaction_id, pending.group_options),
            chat_id=chat_id,
            message_id=message_id,
        )
        return "Choose group."

    return "This split session expired. Please start again from the transaction."


def _handle_text_message(message: dict, db: DbSession) -> None:
    chat_id = str((message.get("chat") or {}).get("id") or "")
    user_id = str((message.get("from") or {}).get("id") or "")
    text = str(message.get("text") or "").strip()
    telegram = TelegramService()
    pending = telegram_split_state_store.get_pending(chat_id, user_id)
    if not pending or not text:
        return

    if pending.mode == "ai_chat":
        _handle_ai_chat_message(pending, text, chat_id, user_id, db, telegram)
        return
    if pending.mode == "custom_split":
        _handle_custom_split_message(pending, text, chat_id, user_id, db, telegram)
        return
    if pending.mode == "custom_values":
        _handle_button_custom_values_message(pending, text, chat_id, user_id, db, telegram)
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


def _handle_ai_chat_message(
    pending: PendingTelegramSplit,
    text: str,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    custom = parse_custom_split_text(text)
    if custom.action == "custom_split" and (
        custom.split_mode != "equal" or custom.payer_included is False
    ):
        _prepare_custom_split_confirmation(pending, custom, chat_id, user_id, db, telegram)
        return
    parsed = parse_conversational_split(text)
    pending.ai_intent_action = parsed.action
    if parsed.action == "personal":
        _mark_personal(pending.transaction_id, chat_id, user_id, db, telegram)
        return
    if parsed.action == "split_people":
        pending.mode = "ai_chat"
        pending.ai_participant_names = parsed.participant_names
        _resolve_ai_people_split(pending, parsed.participant_names, chat_id, user_id, db, telegram)
        return
    if parsed.action == "split_group":
        pending.mode = "ai_chat"
        pending.ai_group_name = parsed.group_name
        pending.ai_participant_names = parsed.participant_names
        _resolve_ai_group_split(pending, chat_id, user_id, db, telegram)
        return
    telegram.send_message(
        "I could not understand that yet. Try: split with Rahul and Akash.",
        reply_markup=build_split_flow_keyboard(pending.transaction_id),
        chat_id=chat_id,
    )


def _handle_custom_split_message(
    pending: PendingTelegramSplit,
    text: str,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    parsed = parse_custom_split_text(text)
    if parsed.action != "custom_split":
        telegram.send_message(
            "I could not parse that custom split yet. "
            "Use the web dashboard for advanced custom splits.",
            reply_markup=build_split_flow_keyboard(pending.transaction_id),
            chat_id=chat_id,
        )
        return
    _prepare_custom_split_confirmation(pending, parsed, chat_id, user_id, db, telegram)


def _handle_button_custom_values_message(
    pending: PendingTelegramSplit,
    text: str,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    if not _require_pending(chat_id, user_id, pending.transaction_id):
        telegram.send_message(
            "This split session expired. Please start again from the transaction.",
            chat_id=chat_id,
        )
        return
    if pending.custom_payer_included and not pending.payer_user_id:
        try:
            pending.payer_user_id = int(SplitwiseService().get_current_user()["id"])
        except (AttributeError, SplitwiseAPIError, KeyError, TypeError, ValueError):
            pending.payer_user_id = None

    parsed = _parse_selected_custom_values(pending, text)
    if not parsed:
        telegram.send_message(
            "I could not parse those values. Try the example format, or use the dashboard.",
            chat_id=chat_id,
        )
        return
    pending.custom_split_mode = pending.split_value_mode
    pending.custom_participant_splits = parsed
    _send_custom_split_confirmation(pending, chat_id, db, telegram)


def _parse_selected_custom_values(
    pending: PendingTelegramSplit,
    text: str,
) -> list[dict] | None:
    values_by_label: dict[str, Decimal] = {}
    for raw_part in text.split(","):
        if "=" not in raw_part:
            return None
        label, raw_value = raw_part.split("=", 1)
        label = label.strip().lower()
        raw_value = raw_value.strip().rstrip("%")
        try:
            value = Decimal(raw_value)
        except Exception:
            return None
        values_by_label[label] = value

    output = []
    for friend_id in pending.selected_friend_ids:
        display_name = pending.selected_friend_names_by_id.get(friend_id, str(friend_id))
        value = values_by_label.get(display_name.lower())
        if value is None:
            return None
        split_data = {"user_id": friend_id, "display_name": display_name}
        if pending.split_value_mode == "exact_amounts":
            split_data["amount_cents"] = decimal_to_cents(value)
        elif pending.split_value_mode == "percentages":
            split_data["percentage"] = value
        elif pending.split_value_mode == "shares":
            split_data["shares"] = value
        else:
            return None
        output.append(split_data)

    if pending.custom_payer_included and "me" in values_by_label and pending.payer_user_id:
        split_data = {"user_id": pending.payer_user_id, "display_name": "You"}
        value = values_by_label["me"]
        if pending.split_value_mode == "exact_amounts":
            split_data["amount_cents"] = decimal_to_cents(value)
        elif pending.split_value_mode == "percentages":
            split_data["percentage"] = value
        elif pending.split_value_mode == "shares":
            split_data["shares"] = value
        else:
            return None
        output.append(split_data)

    return output


def _send_custom_split_confirmation(
    pending: PendingTelegramSplit,
    chat_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    try:
        service = TransactionService(db)
        tx = service.get_transaction(pending.transaction_id)
        splitwise = getattr(service, "splitwise_service", SplitwiseService())
        payer = splitwise.get_current_user()
        payer_user_id = int(payer["id"])
        payer_name = friend_display_name(payer)
    except (AttributeError, KeyError, TypeError, ValueError, TransactionError, SplitwiseAPIError):
        telegram.send_message(
            "Could not prepare the custom split. Use the web dashboard to review.",
            chat_id=chat_id,
        )
        return

    pending.payer_user_id = payer_user_id
    split_inputs = [
        CustomSplitInput(
            user_id=split["user_id"],
            amount_cents=split.get("amount_cents"),
            percentage=split.get("percentage"),
            shares=split.get("shares"),
        )
        for split in pending.custom_participant_splits
    ]
    try:
        preview_shares = build_custom_split_shares_by_mode(
            total_cents=abs(tx.amount_cents),
            payer_user_id=payer_user_id,
            payer_included=pending.custom_payer_included,
            split_mode=pending.split_value_mode,
            participant_splits=split_inputs,
        )
    except ValueError:
        telegram.send_message(
            "That custom split does not add up. Use the web dashboard to adjust it.",
            chat_id=chat_id,
        )
        return

    pending.mode = "awaiting_custom_confirmation"
    participant_lines = _custom_participant_lines(
        preview_shares,
        pending.custom_participant_splits,
        payer_name,
    )
    telegram.send_message(
        format_custom_split_confirmation_message(
            merchant=_safe_transaction_display_name(tx),
            amount=cents_to_decimal_string(abs(tx.amount_cents)),
            currency_code=tx.iso_currency_code or "USD",
            payer_name=payer_name,
            payer_included=pending.custom_payer_included,
            participant_lines=participant_lines,
        ),
        reply_markup=build_custom_split_confirmation_keyboard(pending.transaction_id),
        chat_id=chat_id,
    )


def _prepare_custom_split_confirmation(
    pending: PendingTelegramSplit,
    parsed: ParsedCustomSplit,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    try:
        service = TransactionService(db)
        tx = service.get_transaction(pending.transaction_id)
        splitwise = getattr(service, "splitwise_service", SplitwiseService())
        payer = splitwise.get_current_user()
        payer_user_id = int(payer["id"])
        payer_name = friend_display_name(payer)
        friends = splitwise.get_friends()
    except (AttributeError, KeyError, TypeError, ValueError, TransactionError, SplitwiseAPIError):
        telegram.send_message(
            "Could not prepare the custom split. Use the web dashboard to review.",
            chat_id=chat_id,
        )
        return

    pending.payer_user_id = payer_user_id
    pending.remember_friends(friends)
    participant_splits: list[dict] = []
    for name in parsed.participant_names:
        if name.strip().lower() in {"me", "you"}:
            user_id_value = payer_user_id
            display_name = payer_name
        else:
            matches = find_friend_matches(name, friends)
            if len(matches) != 1:
                telegram.send_message(
                    "I could not resolve every person uniquely. "
                    "Use the web dashboard for this custom split.",
                    chat_id=chat_id,
                )
                return
            user_id_value = int(matches[0]["id"])
            display_name = friend_display_name(matches[0])

        split_data: dict = {"user_id": user_id_value, "display_name": display_name}
        value = parsed.values_by_name.get(name)
        if parsed.split_mode == "exact_amounts":
            split_data["amount_cents"] = int(value * 100) if value is not None else None
        elif parsed.split_mode == "percentages":
            split_data["percentage"] = value
        elif parsed.split_mode == "shares":
            split_data["shares"] = value
        participant_splits.append(split_data)

    split_inputs = [
        CustomSplitInput(
            user_id=split["user_id"],
            amount_cents=split.get("amount_cents"),
            percentage=split.get("percentage"),
            shares=split.get("shares"),
        )
        for split in participant_splits
    ]
    try:
        preview_shares = build_custom_split_shares_by_mode(
            total_cents=abs(tx.amount_cents),
            payer_user_id=payer_user_id,
            payer_included=parsed.payer_included,
            split_mode=parsed.split_mode or "equal",
            participant_splits=split_inputs,
        )
    except ValueError:
        telegram.send_message(
            "That custom split does not add up. Use the web dashboard to adjust it.",
            chat_id=chat_id,
        )
        return

    pending.mode = "awaiting_custom_confirmation"
    pending.custom_split_mode = parsed.split_mode
    pending.custom_payer_included = parsed.payer_included
    pending.custom_participant_splits = participant_splits
    pending.selected_group_id = None

    participant_lines = _custom_participant_lines(preview_shares, participant_splits, payer_name)
    telegram.send_message(
        format_custom_split_confirmation_message(
            merchant=_safe_transaction_display_name(tx),
            amount=cents_to_decimal_string(abs(tx.amount_cents)),
            currency_code=tx.iso_currency_code or "USD",
            payer_name=payer_name,
            payer_included=parsed.payer_included,
            participant_lines=participant_lines,
        ),
        reply_markup=build_custom_split_confirmation_keyboard(pending.transaction_id),
        chat_id=chat_id,
    )


def _resolve_ai_people_split(
    pending: PendingTelegramSplit,
    names: list[str],
    chat_id: str,
    user_id: str,
    db: DbSession,
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
    pending.remember_friends(friends)
    if not _resolve_friend_names_from_pool(pending, names, friends, chat_id, telegram):
        return
    _continue_or_finish_pending_split(pending, chat_id, user_id, db, telegram)


def _resolve_ai_group_split(
    pending: PendingTelegramSplit,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    if not pending.ai_group_name:
        telegram.send_message("Which Splitwise group should I use?", chat_id=chat_id)
        return
    try:
        groups = SplitwiseService().get_groups()
    except SplitwiseAPIError:
        telegram.send_message(
            "Could not search Splitwise groups. Try again from the dashboard.",
            chat_id=chat_id,
        )
        return
    matches = find_group_matches(pending.ai_group_name, groups)
    if not matches:
        telegram.send_message(
            f"No Splitwise group matched '{pending.ai_group_name}'. Try again.",
            chat_id=chat_id,
        )
        return
    if len(matches) > 1:
        pending.mode = "ai_group_choice"
        pending.ambiguous_groups_by_name[pending.ai_group_name] = matches
        telegram.send_message(
            format_group_ambiguity_message(pending.ai_group_name),
            reply_markup=build_group_choice_keyboard(pending.transaction_id, matches),
            chat_id=chat_id,
        )
        return
    _select_group_for_pending_split(pending, matches[0], chat_id, telegram, prompt=False)
    _resolve_ai_group_members(pending, chat_id, user_id, db, telegram)


def _resolve_ai_group_members(
    pending: PendingTelegramSplit,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    if not _resolve_friend_names_from_pool(
        pending,
        pending.ai_participant_names,
        pending.group_members,
        chat_id,
        telegram,
        no_match_label="group member",
    ):
        return
    _continue_or_finish_pending_split(pending, chat_id, user_id, db, telegram)


def _resolve_friend_names_from_pool(
    pending: PendingTelegramSplit,
    names: list[str],
    friends: list[dict],
    chat_id: str,
    telegram: TelegramService,
    no_match_label: str = "Splitwise friend",
) -> bool:
    pending.selected_friend_ids = []
    pending.selected_friend_names_by_id = {}
    pending.remaining_unresolved_names = []
    pending.ambiguous_matches_by_name = {}

    for name in names:
        matches = find_friend_matches(name, friends)
        if not matches:
            telegram.send_message(
                f"No {no_match_label} matched '{name}'. Try again.",
                chat_id=chat_id,
            )
            return False
        if len(matches) == 1:
            pending.add_friend(int(matches[0]["id"]), friend_display_name(matches[0]))
            continue
        pending.remember_friends(matches)
        pending.remaining_unresolved_names.append(name)
        pending.ambiguous_matches_by_name[name] = matches
    return True


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
    prompt: bool = True,
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
    if not prompt:
        return
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
    db: DbSession,
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
                ai_group_choice = pending.mode == "ai_group_choice"
                _select_group_for_pending_split(
                    pending,
                    group,
                    chat_id,
                    telegram,
                    prompt=not ai_group_choice,
                )
                if ai_group_choice:
                    _resolve_ai_group_members(pending, chat_id, user_id, db, telegram)
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
    message_id: int | None = None,
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
            _edit_or_send(
                telegram,
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
                message_id=message_id,
            )
        else:
            pending.mode = "people_select"
            _edit_or_send(
                telegram,
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
                message_id=message_id,
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


def _post_custom_split_from_telegram(
    pending: PendingTelegramSplit,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    try:
        tx = TransactionService(db).get_transaction(pending.transaction_id)
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

        split_inputs = [
            CustomSplitInput(
                user_id=split["user_id"],
                amount_cents=split.get("amount_cents"),
                percentage=split.get("percentage"),
                shares=split.get("shares"),
            )
            for split in pending.custom_participant_splits
        ]
        tx, _response = TransactionService(db).create_custom_split_expense(
            tx_id=pending.transaction_id,
            participant_splits=split_inputs,
            split_mode=pending.custom_split_mode or "equal",
            payer_included=pending.custom_payer_included,
            payer_user_id=pending.payer_user_id,
            owed_by_user_id=None,
            group_id=pending.selected_group_id,
            description=None,
            details=None,
            currency_code=None,
            confirm=True,
            post_pending=False,
        )
    except (TransactionError, SplitwiseAPIError, ValueError):
        telegram.send_message(
            "Could not create the custom split. Open the dashboard to review.",
            chat_id=chat_id,
        )
        telegram_split_state_store.clear(chat_id, user_id)
        return

    try:
        payload = json.loads(tx.splitwise_payload_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    participant_lines = _custom_participant_lines_from_payload(
        payload,
        pending.custom_participant_splits,
    )
    telegram_split_state_store.clear(chat_id, user_id)
    telegram.send_message(
        format_custom_split_success_message(
            merchant=_safe_transaction_display_name(tx),
            amount=cents_to_decimal_string(abs(tx.amount_cents)),
            currency_code=tx.iso_currency_code or "USD",
            participant_lines=participant_lines,
        ),
        reply_markup=build_undo_keyboard(tx.id),
        chat_id=chat_id,
    )
    _record_completion_and_show_next(tx, chat_id, user_id, db, telegram)


def _custom_participant_lines(shares, participant_splits: list[dict], payer_name: str) -> list[str]:
    names_by_id = {split["user_id"]: split.get("display_name") for split in participant_splits}
    lines = []
    for share in shares:
        name = names_by_id.get(share.user_id) or payer_name
        if share.owed_cents > 0:
            lines.append(f"{name}: {cents_to_decimal_string(share.owed_cents)}")
    return lines


def _custom_participant_lines_from_payload(
    payload: dict,
    participant_splits: list[dict],
) -> list[str]:
    names_by_id = {str(split["user_id"]): split.get("display_name") for split in participant_splits}
    lines = []
    index = 0
    while f"users__{index}__user_id" in payload:
        user_id = str(payload[f"users__{index}__user_id"])
        owed_share = str(payload.get(f"users__{index}__owed_share") or "0.00")
        if owed_share != "0.00":
            lines.append(f"{names_by_id.get(user_id, user_id)}: {owed_share}")
        index += 1
    return lines


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

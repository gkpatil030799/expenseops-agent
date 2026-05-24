from __future__ import annotations

import json
import logging
import re
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import DbSession
from app.config import get_settings
from app.logging_config import log_event
from app.models import ExpenseTransaction, TransactionStatus
from app.services.agent_service import friend_display_name
from app.services.ai_chat_context_service import AIChatContext, AIChatContextService
from app.services.ai_intent_extraction_service import AIIntentExtractionService, ExtractedAIIntent
from app.services.ai_memory_service import AIInterpretationMemoryService, memory_prompt_context
from app.services.ai_prompt_guardrails import validate_ai_chat_message
from app.services.conversational_split_parser import parse_conversational_split
from app.services.custom_split_parser import ParsedCustomSplit, parse_custom_split_text
from app.services.entity_resolution_service import (
    EntityResolutionResult,
    EntityResolutionService,
)
from app.services.llm_ai_chat_parser import AIChatIntent, AICustomValue, LLMAIChatParser
from app.services.llm_conversation_parser import LLMConversationIntent, LLMConversationParser
from app.services.llm_split_parser import LLMSplitParser
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
    build_ai_fallback_keyboard,
    build_ai_group_confirmation_keyboard,
    build_ai_participant_ambiguity_keyboard,
    build_ai_split_confirmation_keyboard,
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
    format_ai_fallback_message,
    format_ai_group_confirmation_message,
    format_ai_split_confirmation_message,
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
    with telegram_split_state_store.use_db(db):
        log_event(
            logger,
            "telegram_webhook_received",
            has_callback=bool(update.get("callback_query")),
            has_message=bool(update.get("message")),
        )
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
        log_event(
            logger,
            "telegram_callback_received",
            action=callback.action,
            tx_id=callback.transaction_id,
        )
        try:
            answer = _route_review_callback(
                callback.action,
                callback.transaction_id,
                chat_id,
                user_id,
                int(message_id) if message_id else None,
                db,
                telegram,
            )
        except Exception as exc:
            log_event(
                logger,
                "telegram_callback_failed",
                level=logging.WARNING,
                action=callback.action,
                tx_id=callback.transaction_id,
                reason="unexpected_error",
                error_type=type(exc).__name__,
            )
            answer = "Could not complete that action. Please try again or use the dashboard."
            if chat_id:
                telegram.send_message(
                    "Could not complete that Telegram action. Open the dashboard to review.",
                    chat_id=chat_id,
                )

    if callback_query_id and hasattr(telegram, "answer_callback_query"):
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
        log_event(logger, "telegram_button_mode_opened", tx_id=transaction_id)
        pending = telegram_split_state_store.get_pending(chat_id, user_id)
        if pending and pending.transaction_id == transaction_id and pending.failed_ai_message:
            pending.button_fallback_active = True
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
        previous_pending = telegram_split_state_store.get_pending(chat_id, user_id)
        pending = telegram_split_state_store.set_pending(
            chat_id,
            user_id,
            transaction_id,
            mode="split_target",
        )
        _carry_button_fallback_context(previous_pending, pending, transaction_id)
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
            previous_pending = telegram_split_state_store.get_pending(chat_id, user_id)
            pending = telegram_split_state_store.set_pending(
                chat_id,
                user_id,
                transaction_id,
                mode="custom_split",
            )
            _carry_button_fallback_context(previous_pending, pending, transaction_id)
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
    if action == "ai_group_yes":
        pending = _require_pending(chat_id, user_id, transaction_id)
        if pending is None:
            return "This split session expired. Please start again from the transaction."
        return _confirm_ai_slot_group(pending, chat_id, db, telegram)
    if action == "ai_group_no":
        pending = _require_pending(chat_id, user_id, transaction_id)
        if pending is None:
            return "This split session expired. Please start again from the transaction."
        pending.ai_correction_type = "corrected_group"
        pending.ai_waiting_for = "group"
        pending.ai_slots["ai_waiting_for"] = "group"
        telegram.send_message("Which group should I use?", chat_id=chat_id)
        return "Type a group name."
    if action == "ai_split_people":
        pending = _require_pending(chat_id, user_id, transaction_id)
        if pending is None:
            return "This split session expired. Please start again from the transaction."
        pending.ai_correction_type = "switched_to_people"
        pending.selected_group_id = None
        pending.selected_group_name = None
        pending.group_members = []
        pending.ai_slots["target_type"] = "people"
        pending.ai_slots["resolved_group_id"] = None
        pending.ai_slots["resolved_group_name"] = None
        pending.ai_slots["group_confirmed"] = False
        return _resolve_ai_slot_participants(pending, chat_id, db, telegram)
    if action == "ai_change_people":
        pending = _require_pending(chat_id, user_id, transaction_id)
        if pending is None:
            return "This split session expired. Please start again from the transaction."
        pending.ai_correction_type = "corrected_people"
        pending.ai_waiting_for = "participants"
        pending.ai_slots["ai_waiting_for"] = "participants"
        telegram.send_message("Who should I include?", chat_id=chat_id)
        return "Type participant names."
    if action == "ai_change_group":
        pending = _require_pending(chat_id, user_id, transaction_id)
        if pending is None:
            return "This split session expired. Please start again from the transaction."
        pending.ai_correction_type = "corrected_group"
        pending.ai_waiting_for = "group"
        pending.ai_slots["ai_waiting_for"] = "group"
        telegram.send_message("Which group should I use?", chat_id=chat_id)
        return "Type a group name."
    if action == "ai_change_split":
        pending = _require_pending(chat_id, user_id, transaction_id)
        if pending is None:
            return "This split session expired. Please start again from the transaction."
        pending.ai_correction_type = "corrected_split"
        telegram.send_message(
            "For now, AI chat supports equal split confirmation here.",
            chat_id=chat_id,
        )
        return "Equal split is selected."
    if action == "done":
        pending = _require_pending(chat_id, user_id, transaction_id)
        if pending is None:
            return "This split session expired. Please start again from the transaction."
        if pending.is_submitting:
            return "Split already being processed."
        if not _participant_friend_ids(pending):
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
        if not _participant_friend_ids(pending):
            return "Select at least one person before confirming."
        pending.is_submitting = True
        log_event(logger, "telegram_split_confirmed", tx_id=transaction_id, split_mode="equal")
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
        log_event(logger, "telegram_split_confirmed", tx_id=transaction_id, split_mode="custom")
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


def _carry_button_fallback_context(
    previous_pending: PendingTelegramSplit | None,
    pending: PendingTelegramSplit,
    transaction_id: int,
) -> None:
    if (
        not previous_pending
        or previous_pending.transaction_id != transaction_id
        or not previous_pending.failed_ai_message
    ):
        return
    pending.failed_ai_message = previous_pending.failed_ai_message
    pending.failed_ai_reason = previous_pending.failed_ai_reason
    pending.failed_ai_created_at = previous_pending.failed_ai_created_at
    pending.button_fallback_active = previous_pending.button_fallback_active


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
        log_event(
            logger,
            "ai_entity_resolution_started",
            tx_id=pending.transaction_id,
            entity_type="group",
        )
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
    guardrail = validate_ai_chat_message(text)
    if not guardrail.allowed:
        log_event(
            logger,
            "telegram_ai_fallback",
            tx_id=pending.transaction_id,
            reason=guardrail.reason or "guardrail_rejected",
        )
        telegram.send_message(
            guardrail.user_message
            or "I can only help classify or split this expense. Try: split with Rahul and Akash.",
            chat_id=chat_id,
        )
        return

    text = guardrail.safe_message

    if _is_explicit_personal_command(text):
        _mark_personal(pending.transaction_id, chat_id, user_id, db, telegram)
        return
    lowered = " ".join(text.strip().lower().split())
    if lowered in {"draft", "draft this", "create draft", "create draft only"}:
        _mark_shared_draft(pending.transaction_id, chat_id, user_id, db, telegram)
        return
    if lowered == "cancel":
        telegram_split_state_store.clear(chat_id, user_id)
        telegram.send_message("✅ Split flow cancelled.", chat_id=chat_id)
        return

    if pending.ai_waiting_for and not _is_top_level_ai_command(text):
        if _continue_enterprise_ai_slots(pending, text, chat_id, db, telegram):
            return
        _continue_ai_chat_pending_flow(pending, text, chat_id, user_id, db, telegram)
        return

    _handle_enterprise_ai_chat_message(pending, text, chat_id, user_id, db, telegram)


def _handle_enterprise_ai_chat_message(
    pending: PendingTelegramSplit,
    text: str,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    pending.last_ai_message = text
    pending.ai_slots = {
        "original_user_message": text,
        "ai_waiting_for": None,
    }
    log_event(logger, "telegram_ai_started", tx_id=pending.transaction_id)
    try:
        tx = TransactionService(db).get_transaction(pending.transaction_id)
    except TransactionError:
        _record_ai_failure_and_prompt_button_mode(
            pending,
            text,
            "validation_failed",
            chat_id,
            telegram,
        )
        return

    ai_context = {
        "transaction": {
            "merchant": _safe_transaction_display_name(tx),
            "amount_cents": abs(tx.amount_cents),
            "currency": tx.iso_currency_code or "USD",
        },
        "current_slots": pending.ai_slots,
    }
    try:
        relevant_memory_rows = AIInterpretationMemoryService(db).relevant_memories(
            merchant=tx.merchant_name or tx.name,
            message=text,
        )
        memories = [memory_prompt_context(memory) for memory in relevant_memory_rows]
        log_event(logger, "ai_memory_retrieved", tx_id=pending.transaction_id, count=len(memories))
    except (AttributeError, SQLAlchemyError, TypeError, ValueError) as exc:
        log_event(
            logger,
            "ai_memory_retrieved",
            level=logging.WARNING,
            tx_id=pending.transaction_id,
            count=0,
            reason="db_error",
            error_type=type(exc).__name__,
        )
        memories = []
    ai_context["relevant_memories"] = memories

    memory_intent = _intent_from_matching_memory(text, memories)
    if memory_intent:
        log_event(logger, "ai_memory_shortcut_used", tx_id=pending.transaction_id)
        _store_ai_intent_slots(pending, memory_intent, text)
        if memory_intent.target_type == "group" or memory_intent.group_mentions:
            _resolve_ai_slot_group(pending, memory_intent.group_mentions, chat_id, telegram)
            return
        _resolve_ai_slot_participants(pending, chat_id, db, telegram)
        return

    intent = AIIntentExtractionService().extract(
        user_message=text,
        context=ai_context,
    )
    _store_ai_intent_slots(pending, intent, text)

    if intent.action in {"clarify", "unknown"}:
        confidence_values = list(intent.confidence_by_slot.values())
        failure_reason = (
            "low_confidence"
            if confidence_values and max(confidence_values) < 0.75
            else "parse_failed"
        )
        _record_ai_failure_and_prompt_button_mode(
            pending,
            text,
            failure_reason,
            chat_id,
            telegram,
        )
        return
    if intent.action == "personal":
        _mark_personal(pending.transaction_id, chat_id, user_id, db, telegram)
        return
    if intent.action == "draft":
        _mark_shared_draft(pending.transaction_id, chat_id, user_id, db, telegram)
        return
    if intent.action == "cancel":
        telegram_split_state_store.clear(chat_id, user_id)
        telegram.send_message("✅ Split flow cancelled.", chat_id=chat_id)
        return
    if intent.action != "split":
        _record_ai_failure_and_prompt_button_mode(pending, text, "parse_failed", chat_id, telegram)
        return

    if intent.split_mode == "unknown":
        pending.ai_waiting_for = "split_mode"
        pending.ai_slots["ai_waiting_for"] = "split_mode"
        telegram.send_message(
            "Should this be equal, amounts, percentages, or shares?",
            chat_id=chat_id,
        )
        return

    if intent.target_type == "group" or intent.group_mentions:
        _resolve_ai_slot_group(pending, intent.group_mentions, chat_id, telegram)
        return

    _resolve_ai_slot_participants(pending, chat_id, db, telegram)


def _intent_from_matching_memory(
    text: str,
    memories: list[dict],
) -> ExtractedAIIntent | None:
    message_tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    for memory in memories:
        phrase = str(memory.get("original_phrase") or "")
        phrase_tokens = set(re.findall(r"[a-z0-9]+", phrase.lower()))
        if not phrase_tokens:
            continue
        overlap = len(message_tokens & phrase_tokens) / len(phrase_tokens)
        if overlap < 0.8:
            continue
        if memory.get("correct_interpretation") not in {"split_equal", "custom_split"}:
            continue
        split_mode = str(memory.get("split_mode") or "equal")
        if split_mode == "equal" and _message_has_custom_split_values(text):
            continue
        if split_mode not in {"equal", "exact_amounts", "percentages", "shares"}:
            split_mode = "equal"
        group = memory.get("group")
        return ExtractedAIIntent(
            action="split",
            target_type="group" if group else "people",
            group_mentions=[str(group)] if group else [],
            person_mentions=[
                str(participant)
                for participant in memory.get("participants", [])
                if str(participant).strip()
            ],
            split_mode=split_mode,
            payer_included=memory.get("payer_included"),
            remaining_split_behavior="none",
            custom_values_text=None,
            confidence_by_slot={"memory": 1.0},
            explanation="Matched prior corrected AI memory.",
        )
    return None


def _message_has_custom_split_values(text: str) -> bool:
    lowered = text.lower()
    return bool(
        re.search(r"\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?", lowered)
        or re.search(r"\d+(?:\.\d+)?\s*%", lowered)
        or re.search(
            r"\b\d+(?:\.\d+)?\s*(?:percent|percentage|shares?|dollars?|usd|bucks)\b",
            lowered,
        )
        or re.search(r"\b(?:pays?|owes?|gets?|covers?|remaining|rest)\b", lowered)
    )


def _store_ai_intent_slots(
    pending: PendingTelegramSplit,
    intent: ExtractedAIIntent,
    original_message: str,
) -> None:
    pending.ai_slots.update(
        {
            "action": intent.action,
            "target_type": intent.target_type,
            "group_mentions": list(intent.group_mentions),
            "participant_mentions": list(intent.person_mentions),
            "split_mode": intent.split_mode,
            "payer_included": intent.payer_included,
            "remaining_split_behavior": intent.remaining_split_behavior,
            "custom_values_text": intent.custom_values_text,
            "parsed_custom_values": [],
            "missing_custom_values": [],
            "custom_validation_status": None,
            "original_user_message": original_message,
            "last_ai_explanation": intent.explanation,
            "confidence_by_slot": dict(intent.confidence_by_slot),
            "errors": list(intent.errors),
            "resolved_group_id": None,
            "resolved_group_name": None,
            "resolved_participants": [],
            "unresolved_participants": [],
            "ambiguous_participants": [],
            "group_confirmed": False,
        }
    )
    pending.ai_participant_names = list(intent.person_mentions)
    pending.ai_target_type = intent.target_type
    pending.ai_split_mode = intent.split_mode
    pending.custom_payer_included = intent.payer_included is not False
    pending.split_value_mode = (
        intent.split_mode if intent.split_mode != "unknown" else pending.split_value_mode
    )


def _continue_enterprise_ai_slots(
    pending: PendingTelegramSplit,
    text: str,
    chat_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> bool:
    if not pending.ai_slots:
        return False
    if pending.ai_waiting_for == "group":
        pending.ai_correction_type = pending.ai_correction_type or "corrected_group"
        pending.ai_slots["group_mentions"] = [text]
        pending.ai_slots["ai_waiting_for"] = None
        _resolve_ai_slot_group(pending, [text], chat_id, telegram)
        return True
    if pending.ai_waiting_for == "participants":
        pending.ai_correction_type = pending.ai_correction_type or "corrected_people"
        names = parse_split_names(text) or [text]
        pending.ai_slots["participant_mentions"] = names
        pending.ai_participant_names = names
        pending.ai_slots["ai_waiting_for"] = None
        _resolve_ai_slot_participants(pending, chat_id, db, telegram)
        return True
    if pending.ai_waiting_for == "custom_values":
        pending.ai_slots["custom_values_text"] = text
        pending.ai_slots["ai_waiting_for"] = None
        pending.ai_waiting_for = None
        _prepare_enterprise_ai_custom_split_confirmation(pending, chat_id, db, telegram)
        return True
    return False


def _resolve_ai_slot_group(
    pending: PendingTelegramSplit,
    group_mentions: list[str],
    chat_id: str,
    telegram: TelegramService,
) -> None:
    if not group_mentions:
        pending.ai_waiting_for = "group"
        pending.ai_slots["ai_waiting_for"] = "group"
        telegram.send_message("Which group should I use?", chat_id=chat_id)
        return
    try:
        groups = SplitwiseService().get_groups()
    except (AttributeError, SplitwiseAPIError):
        _record_ai_failure_and_prompt_button_mode(
            pending,
            pending.last_ai_message or "",
            "unknown_group",
            chat_id,
            telegram,
        )
        return
    result = EntityResolutionService().resolve_group_mentions(group_mentions, groups)
    if result.ambiguous:
        log_event(
            logger,
            "ai_entity_resolution_ambiguous",
            tx_id=pending.transaction_id,
            entity_type="group",
            mention=group_mentions[0],
            candidate_count=len(result.ambiguous[0].candidates),
            reason="ambiguous_group",
        )
        pending.remember_failed_ai_attempt(pending.last_ai_message or "", "ambiguous_group")
        pending.mode = "ai_group_choice"
        pending.ambiguous_groups_by_name[group_mentions[0]] = [
            candidate.entity for candidate in result.ambiguous[0].candidates
        ]
        telegram.send_message(
            format_group_ambiguity_message(group_mentions[0]),
            reply_markup=build_group_choice_keyboard(
                pending.transaction_id,
                pending.ambiguous_groups_by_name[group_mentions[0]],
            ),
            chat_id=chat_id,
        )
        return
    if result.unresolved or not result.resolved:
        log_event(
            logger,
            "ai_entity_resolution_failed",
            tx_id=pending.transaction_id,
            entity_type="group",
            reason="unknown_group",
        )
        _record_ai_failure_and_prompt_button_mode(
            pending,
            pending.last_ai_message or "",
            "unknown_group",
            chat_id,
            telegram,
        )
        return

    group = result.resolved[0].entity
    pending.ai_slots["resolved_group_id"] = int(group["id"])
    pending.ai_slots["resolved_group_name"] = str(group.get("name") or group["id"])
    pending.ai_slots["resolved_group"] = group
    pending.ai_slots["ai_waiting_for"] = "group_confirmation"
    pending.ai_waiting_for = "group_confirmation"
    log_event(
        logger,
        "ai_entity_resolution_success",
        tx_id=pending.transaction_id,
        entity_type="group",
        group_id=group["id"],
    )
    telegram.send_message(
        format_ai_group_confirmation_message(
            pending.transaction_title or f"Transaction {pending.transaction_id}",
            pending.ai_slots["resolved_group_name"],
        ),
        reply_markup=build_ai_group_confirmation_keyboard(pending.transaction_id),
        chat_id=chat_id,
    )


def _confirm_ai_slot_group(
    pending: PendingTelegramSplit,
    chat_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> str:
    group = pending.ai_slots.get("resolved_group")
    if not group:
        return "This split session expired. Please start again from the transaction."
    pending.selected_group_id = int(group["id"])
    pending.selected_group_name = str(group.get("name") or group["id"])
    pending.group_members = list(group.get("members", []))
    pending.remember_friends(pending.group_members)
    pending.ai_slots["group_confirmed"] = True
    log_event(
        logger,
        "telegram_group_confirmed",
        tx_id=pending.transaction_id,
        group_id=pending.selected_group_id,
    )
    pending.ai_slots["ai_waiting_for"] = None
    pending.ai_waiting_for = None
    _resolve_ai_slot_participants(pending, chat_id, db, telegram)
    return "Group confirmed."


def _resolve_ai_slot_participants(
    pending: PendingTelegramSplit,
    chat_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> str:
    mentions = list(pending.ai_slots.get("participant_mentions") or [])
    if not mentions:
        if pending.selected_group_id and pending.group_members:
            _prompt_ai_group_member_selection(pending, chat_id, db, telegram)
            pending.ai_waiting_for = "participants"
            pending.ai_slots["ai_waiting_for"] = "participants"
            return "Choose group members."
        pending.ai_waiting_for = "participants"
        pending.ai_slots["ai_waiting_for"] = "participants"
        telegram.send_message("Who should I include?", chat_id=chat_id)
        return "Type participant names."
    try:
        splitwise = SplitwiseService()
        try:
            friends = splitwise.get_friends()
        except (AttributeError, SplitwiseAPIError):
            friends = []
        try:
            payer = splitwise.get_current_user()
        except (AttributeError, SplitwiseAPIError):
            payer = None
    except (AttributeError, SplitwiseAPIError):
        _record_ai_failure_and_prompt_button_mode(
            pending,
            pending.last_ai_message or "",
            "unknown_person",
            chat_id,
            telegram,
        )
        return "Use Button mode."

    log_event(
        logger,
        "ai_entity_resolution_started",
        tx_id=pending.transaction_id,
        entity_type="person",
        mention_count=len(mentions),
    )
    resolver = EntityResolutionService()
    if pending.selected_group_id:
        result = resolver.resolve_people_within_group(
            mentions,
            pending.group_members,
            payer=payer,
            all_friends=friends,
        )
    else:
        result = resolver.resolve_person_mentions(mentions, friends, payer=payer)
    pending.remember_friends([*friends, *pending.group_members])
    _apply_ai_participant_resolution(pending, result)

    if result.unresolved:
        log_event(
            logger,
            "ai_entity_resolution_failed",
            tx_id=pending.transaction_id,
            entity_type="person",
            reason="unknown_person",
            unresolved_count=len(result.unresolved),
        )
        pending.remember_failed_ai_attempt(pending.last_ai_message or "", "unknown_person")
        pending.ai_waiting_for = "participants"
        pending.ai_slots["ai_waiting_for"] = "participants"
        telegram.send_message(
            f"I couldn't find {', '.join(result.unresolved)}. Use Button mode for this one.",
            reply_markup=build_ai_fallback_keyboard(pending.transaction_id),
            chat_id=chat_id,
        )
        return "Participant not found."
    if result.ambiguous:
        pending.remember_failed_ai_attempt(pending.last_ai_message or "", "ambiguous_person")
        pending.ai_waiting_for = "participant_disambiguation"
        pending.ai_slots["ai_waiting_for"] = "participant_disambiguation"
        ambiguous = result.ambiguous[0]
        log_event(
            logger,
            "ai_entity_resolution_ambiguous",
            tx_id=pending.transaction_id,
            entity_type="person",
            mention=ambiguous.mention,
            candidate_count=len(ambiguous.candidates),
            reason="ambiguous_person",
        )
        pending.remaining_unresolved_names = [ambiguous.mention]
        pending.ambiguous_matches_by_name = {
            ambiguous.mention: [candidate.entity for candidate in ambiguous.candidates]
        }
        pending.remember_friends(pending.ambiguous_matches_by_name[ambiguous.mention])
        telegram.send_message(
            format_ambiguity_message(ambiguous.mention, _selected_friend_names(pending)),
            reply_markup=build_ai_participant_ambiguity_keyboard(
                pending.transaction_id,
                pending.ambiguous_matches_by_name[ambiguous.mention],
            ),
            chat_id=chat_id,
        )
        return "Choose a participant."

    log_event(
        logger,
        "ai_entity_resolution_success",
        tx_id=pending.transaction_id,
        entity_type="person",
        resolved_count=len(result.resolved),
    )
    return _continue_after_enterprise_ai_participants(pending, chat_id, db, telegram)


def _apply_ai_participant_resolution(
    pending: PendingTelegramSplit,
    result: EntityResolutionResult,
) -> None:
    pending.selected_friend_ids = []
    pending.selected_friend_names_by_id = {}
    pending.friend_lookup_by_id = {}
    resolved_participants = []
    for entity in result.resolved:
        user_id = int(entity.entity_id)
        pending.add_friend(user_id, entity.display_name)
        if entity.source == "payer":
            pending.payer_user_id = user_id
        resolved_participants.append(
            {
                "user_id": user_id,
                "display_name": entity.display_name,
                "mention": entity.mention,
                "source": entity.source,
            }
        )
    pending.ai_slots["resolved_participants"] = resolved_participants
    pending.ai_slots["unresolved_participants"] = list(result.unresolved)
    pending.ai_slots["ambiguous_participants"] = [
        {
            "mention": ambiguous.mention,
            "candidates": [
                {
                    "user_id": int(candidate.entity_id),
                    "display_name": candidate.display_name,
                    "source": candidate.source,
                }
                for candidate in ambiguous.candidates
            ],
        }
        for ambiguous in result.ambiguous
    ]


def _continue_after_enterprise_ai_participants(
    pending: PendingTelegramSplit,
    chat_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> str:
    split_mode = pending.ai_slots.get("split_mode") or "equal"
    if split_mode in {"exact_amounts", "percentages", "shares"}:
        return _prepare_enterprise_ai_custom_split_confirmation(pending, chat_id, db, telegram)
    return _send_enterprise_ai_confirmation(pending, chat_id, db, telegram)


def _send_enterprise_ai_confirmation(
    pending: PendingTelegramSplit,
    chat_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> str:
    error = _ai_confirmation_blocker(pending)
    if error:
        telegram.send_message(
            error,
            reply_markup=build_ai_fallback_keyboard(pending.transaction_id),
            chat_id=chat_id,
        )
        return "Use Button mode."
    try:
        tx = TransactionService(db).get_transaction(pending.transaction_id)
    except TransactionError:
        telegram.send_message(
            "Could not prepare the split summary. Open the dashboard to review.",
            chat_id=chat_id,
        )
        return "Open dashboard."

    friend_ids = _participant_friend_ids(pending)
    participant_names = _selected_friend_names(pending)
    approx_share = approximate_equal_share_display(
        int(getattr(tx, "amount_cents", 0)),
        len(friend_ids) + 1,
    )
    pending.mode = "confirm"
    pending.ai_waiting_for = "final_confirmation"
    pending.ai_slots["ai_waiting_for"] = "final_confirmation"
    telegram.send_message(
        format_ai_split_confirmation_message(
            transaction_title=pending.transaction_title
            or _compact_title_for_transaction(pending.transaction_id, db),
            group_name=pending.selected_group_name,
            participant_names=participant_names,
            split_mode=pending.ai_slots.get("split_mode") or "equal",
            payer_included=pending.custom_payer_included,
            approx_share=approx_share,
            currency_code=getattr(tx, "iso_currency_code", "USD") or "USD",
        ),
        reply_markup=build_ai_split_confirmation_keyboard(
            pending.transaction_id,
            include_split_as_people=bool(pending.selected_group_id),
        ),
        chat_id=chat_id,
    )
    return "Review and confirm the split."


def _ai_confirmation_blocker(pending: PendingTelegramSplit) -> str | None:
    mentions = list(pending.ai_slots.get("participant_mentions") or [])
    if not mentions:
        return "I need to know who should be included before confirming."
    if pending.ai_slots.get("split_mode") != "equal":
        return "I need a complete custom split before confirming."
    if pending.ai_slots.get("target_type") == "group" and not pending.ai_slots.get(
        "group_confirmed"
    ):
        return "Please confirm the group before confirming the split."
    if pending.ai_slots.get("unresolved_participants"):
        return "I could not resolve every participant."
    if pending.ai_slots.get("ambiguous_participants"):
        return "Please choose the correct participant before confirming."
    if len(set(pending.selected_friend_ids)) != len(pending.selected_friend_ids):
        return "I found duplicate participants. Please review in Button mode."
    if len(pending.selected_friend_ids) < len(set(mentions)):
        return "I could not resolve every requested participant."
    if pending.selected_group_id:
        member_ids = {int(member["id"]) for member in pending.group_members}
        missing = [
            friend_id
            for friend_id in pending.selected_friend_ids
            if friend_id not in member_ids
        ]
        if missing:
            return "One selected participant is not in the confirmed group."
    return None


def _prepare_enterprise_ai_custom_split_confirmation(
    pending: PendingTelegramSplit,
    chat_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> str:
    split_mode = str(pending.ai_slots.get("split_mode") or "unknown")
    if split_mode not in {"exact_amounts", "percentages", "shares"}:
        pending.ai_waiting_for = "split_mode"
        pending.ai_slots["ai_waiting_for"] = "split_mode"
        telegram.send_message("Should this be amounts, percentages, or shares?", chat_id=chat_id)
        return "Need split mode."

    values_text = str(pending.ai_slots.get("custom_values_text") or pending.last_ai_message or "")
    if not values_text.strip():
        pending.ai_waiting_for = "custom_values"
        pending.ai_slots["ai_waiting_for"] = "custom_values"
        telegram.send_message(
            format_custom_values_prompt(
                pending.transaction_title
                or _compact_title_for_transaction(pending.transaction_id, db),
                split_mode,
                _selected_friend_names(pending),
            ),
            chat_id=chat_id,
        )
        return "Need custom values."

    try:
        tx = TransactionService(db).get_transaction(pending.transaction_id)
        splitwise = SplitwiseService()
        payer = splitwise.get_current_user()
        payer_user_id = int(payer["id"])
        payer_name = friend_display_name(payer)
    except (AttributeError, KeyError, TypeError, ValueError, TransactionError, SplitwiseAPIError):
        telegram.send_message(
            "Could not prepare the custom split. Use the web dashboard to review.",
            chat_id=chat_id,
        )
        return "Open dashboard."

    pending.payer_user_id = payer_user_id
    pending.custom_split_mode = split_mode
    pending.split_value_mode = split_mode
    pending.custom_payer_included = pending.ai_slots.get("payer_included") is not False

    participant_splits = _build_enterprise_ai_custom_participant_splits(
        pending=pending,
        tx_amount_cents=abs(int(tx.amount_cents)),
        currency_code=tx.iso_currency_code or "USD",
        payer_user_id=payer_user_id,
        payer_name=payer_name,
        values_text=values_text,
    )
    if participant_splits is None:
        pending.ai_waiting_for = "custom_values"
        pending.ai_slots["ai_waiting_for"] = "custom_values"
        pending.ai_slots["custom_validation_status"] = "missing_or_invalid"
        telegram.send_message(
            "I need valid values for everyone in this custom split. "
            "Try: Janhavi=50, Rahul=50, or use Button mode.",
            reply_markup=build_ai_fallback_keyboard(pending.transaction_id),
            chat_id=chat_id,
        )
        return "Need valid custom values."

    pending.custom_participant_splits = participant_splits
    pending.ai_slots["parsed_custom_values"] = participant_splits
    split_inputs = [_custom_split_input_from_pending_split(split) for split in participant_splits]
    try:
        preview_shares = build_custom_split_shares_by_mode(
            total_cents=abs(int(tx.amount_cents)),
            payer_user_id=payer_user_id,
            payer_included=pending.custom_payer_included,
            split_mode=split_mode,
            participant_splits=split_inputs,
        )
    except ValueError as exc:
        log_event(
            logger,
            "ai_custom_split_validation_failed",
            level=logging.WARNING,
            tx_id=pending.transaction_id,
            reason="split_validation_failed",
            error_type=type(exc).__name__,
        )
        pending.ai_waiting_for = "custom_values"
        pending.ai_slots["ai_waiting_for"] = "custom_values"
        pending.ai_slots["custom_validation_status"] = "invalid"
        pending.remember_failed_ai_attempt(pending.last_ai_message or "", "validation_failed")
        telegram.send_message(
            f"That custom split does not add up: {_compact_error_value(exc)}",
            reply_markup=build_ai_fallback_keyboard(pending.transaction_id),
            chat_id=chat_id,
        )
        return "Invalid custom split."

    pending.mode = "awaiting_custom_confirmation"
    pending.ai_waiting_for = "final_confirmation"
    pending.ai_slots["ai_waiting_for"] = "final_confirmation"
    pending.ai_slots["custom_validation_status"] = "valid"
    telegram.send_message(
        format_custom_split_confirmation_message(
            merchant=_safe_transaction_display_name(tx),
            amount=cents_to_decimal_string(abs(int(tx.amount_cents))),
            currency_code=tx.iso_currency_code or "USD",
            payer_name=payer_name,
            payer_included=pending.custom_payer_included,
            participant_lines=_custom_participant_lines(
                preview_shares,
                participant_splits,
                payer_name,
            ),
        ),
        reply_markup=build_custom_split_confirmation_keyboard(pending.transaction_id),
        chat_id=chat_id,
    )
    return "Review and confirm the custom split."


def _build_enterprise_ai_custom_participant_splits(
    *,
    pending: PendingTelegramSplit,
    tx_amount_cents: int,
    currency_code: str,
    payer_user_id: int,
    payer_name: str,
    values_text: str,
) -> list[dict] | None:
    split_mode = str(pending.ai_slots.get("split_mode") or pending.split_value_mode)
    participants = _enterprise_ai_custom_participants(
        pending,
        payer_user_id=payer_user_id,
        payer_name=payer_name,
    )
    if not participants:
        return None

    explicit = _parse_enterprise_ai_custom_values(
        values_text,
        split_mode=split_mode,
        participants=participants,
    )
    if explicit is None:
        explicit_splits = _parse_enterprise_ai_custom_values_with_llm(
            pending=pending,
            values_text=values_text,
            split_mode=split_mode,
            total_amount_cents=tx_amount_cents,
            currency_code=currency_code,
            participants=participants,
            payer_user_id=payer_user_id,
            payer_name=payer_name,
        )
        if explicit_splits is not None:
            return explicit_splits
        return None

    remaining_behavior = pending.ai_slots.get("remaining_split_behavior") or "unknown"
    missing = [
        participant for participant in participants if participant["user_id"] not in explicit
    ]
    if missing:
        if remaining_behavior != "equal_remaining":
            pending.ai_slots["missing_custom_values"] = [
                participant["display_name"] for participant in missing
            ]
            return None
        explicit = _fill_equal_remaining_custom_values(
            split_mode=split_mode,
            total_amount_cents=tx_amount_cents,
            explicit=explicit,
            missing=missing,
        )
        if explicit is None:
            return None

    output: list[dict] = []
    for participant in participants:
        user_id_value = int(participant["user_id"])
        value = explicit.get(user_id_value)
        if value is None:
            return None
        split_data = {
            "user_id": user_id_value,
            "display_name": participant["display_name"],
        }
        if split_mode == "exact_amounts":
            split_data["amount_cents"] = decimal_to_cents(value)
        elif split_mode == "percentages":
            split_data["percentage"] = value
        elif split_mode == "shares":
            split_data["shares"] = value
        else:
            return None
        output.append(split_data)
    return output


def _parse_enterprise_ai_custom_values_with_llm(
    *,
    pending: PendingTelegramSplit,
    values_text: str,
    split_mode: str,
    total_amount_cents: int,
    currency_code: str,
    participants: list[dict],
    payer_user_id: int,
    payer_name: str,
) -> list[dict] | None:
    if split_mode not in {"exact_amounts", "percentages", "shares"}:
        return None

    selected_participants = [
        {
            "user_id": int(participant["user_id"]),
            "display_name": str(participant["display_name"]),
        }
        for participant in participants
        if int(participant["user_id"]) != payer_user_id
    ]
    payer = None
    if pending.custom_payer_included:
        payer = {"user_id": payer_user_id, "display_name": payer_name}

    result = LLMSplitParser().parse(
        user_message=values_text,
        total_amount_cents=total_amount_cents,
        currency_code=currency_code,
        split_mode=split_mode,  # type: ignore[arg-type]
        payer_included=pending.custom_payer_included,
        selected_participants=selected_participants,
        payer=payer,
    )
    if not result.ok:
        pending.ai_slots["custom_value_parse_errors"] = list(result.errors)
        if result.clarification_question:
            pending.ai_slots["custom_value_clarification"] = result.clarification_question
        return None

    allowed_ids = {int(participant["user_id"]) for participant in participants}
    output: list[dict] = []
    seen_ids: set[int] = set()
    for split in result.participant_splits:
        user_id_value = int(split.user_id)
        if user_id_value not in allowed_ids or user_id_value in seen_ids:
            return None
        seen_ids.add(user_id_value)
        split_data = {
            "user_id": user_id_value,
            "display_name": split.display_name,
        }
        if split_mode == "exact_amounts":
            split_data["amount_cents"] = split.amount_cents
        elif split_mode == "percentages":
            split_data["percentage"] = split.percentage
        elif split_mode == "shares":
            split_data["shares"] = split.shares
        output.append(split_data)

    return output if seen_ids == allowed_ids else None


def _enterprise_ai_custom_participants(
    pending: PendingTelegramSplit,
    *,
    payer_user_id: int,
    payer_name: str,
) -> list[dict]:
    participants = [
        {
            "user_id": friend_id,
            "display_name": pending.selected_friend_names_by_id.get(friend_id)
            or pending.friend_lookup_by_id.get(friend_id)
            or str(friend_id),
        }
        for friend_id in pending.selected_friend_ids
    ]
    if pending.custom_payer_included and payer_user_id not in {
        int(participant["user_id"]) for participant in participants
    }:
        participants.insert(0, {"user_id": payer_user_id, "display_name": payer_name})
    seen: set[int] = set()
    unique = []
    for participant in participants:
        user_id_value = int(participant["user_id"])
        if user_id_value in seen:
            continue
        seen.add(user_id_value)
        unique.append(participant)
    return unique


def _parse_enterprise_ai_custom_values(
    text: str,
    *,
    split_mode: str,
    participants: list[dict],
) -> dict[int, Decimal] | None:
    numbers = [Decimal(match) for match in re.findall(r"\d+(?:\.\d+)?", text)]
    if len(numbers) == len(participants):
        return {
            int(participant["user_id"]): numbers[index]
            for index, participant in enumerate(participants)
        }

    values: dict[int, Decimal] = {}
    for participant in participants:
        value = _explicit_custom_value_for_participant(
            text,
            split_mode,
            participant["display_name"],
        )
        if value is not None:
            values[int(participant["user_id"])] = value
    return values if values else None


def _explicit_custom_value_for_participant(
    text: str,
    split_mode: str,
    display_name: str,
) -> Decimal | None:
    lowered = text.lower()
    tokens = [token for token in re.split(r"\s+", display_name.lower()) if token]
    aliases = {display_name.lower(), *(token for token in tokens if len(token) > 1)}
    if display_name.lower() in {"you", "gunjan patil"}:
        aliases.update({"me", "you"})
    value_pattern = r"(\d+(?:\.\d+)?)"
    unit_pattern = {
        "exact_amounts": r"(?:dollars?|bucks?|usd)?",
        "percentages": r"(?:%|percent|percentage)",
        "shares": r"(?:shares?|share)?",
    }.get(split_mode, "")
    for alias in aliases:
        alias_pattern = re.escape(alias)
        patterns = [
            rf"{alias_pattern}[^0-9]{{0,40}}{value_pattern}\s*{unit_pattern}",
            rf"{value_pattern}\s*{unit_pattern}[^A-Za-z0-9]{{0,40}}{alias_pattern}",
        ]
        for pattern in patterns:
            match = re.search(pattern, lowered, flags=re.IGNORECASE)
            if match:
                return Decimal(match.group(1))
    return None


def _fill_equal_remaining_custom_values(
    *,
    split_mode: str,
    total_amount_cents: int,
    explicit: dict[int, Decimal],
    missing: list[dict],
) -> dict[int, Decimal] | None:
    values = dict(explicit)
    if not missing:
        return values
    if split_mode == "percentages":
        remaining = Decimal("100") - sum(values.values(), Decimal("0"))
    elif split_mode == "exact_amounts":
        remaining = Decimal(total_amount_cents) / Decimal("100") - sum(
            values.values(),
            Decimal("0"),
        )
    elif split_mode == "shares":
        for participant in missing:
            values[int(participant["user_id"])] = Decimal("1")
        return values
    else:
        return None
    if remaining < 0:
        return None
    each = remaining / Decimal(len(missing))
    for participant in missing:
        values[int(participant["user_id"])] = each
    return values


def _try_context_grounded_ai_chat(
    pending: PendingTelegramSplit,
    text: str,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> bool:
    try:
        tx = TransactionService(db).get_transaction(pending.transaction_id)
        pending.last_ai_message = text
        context_service = AIChatContextService()
        try:
            context = context_service.build(tx, pending, db=db, user_message=text)
        except TypeError:
            context = context_service.build(tx, pending)
    except (AttributeError, KeyError, TypeError, ValueError, TransactionError, SplitwiseAPIError):
        return False

    intent = LLMAIChatParser().parse(
        user_message=text,
        ai_context=context.prompt_context,
    )
    if "missing_openai_api_key" in intent.errors:
        return False
    if intent.action == "clarify" or intent.confidence < Decimal("0.75"):
        reason = "low_confidence" if intent.confidence < Decimal("0.75") else "parse_failed"
        _record_ai_failure_and_prompt_button_mode(pending, text, reason, chat_id, telegram)
        return True

    _route_context_grounded_ai_intent(pending, intent, context, chat_id, user_id, db, telegram)
    return True


def _record_ai_failure_and_prompt_button_mode(
    pending: PendingTelegramSplit,
    original_message: str,
    failure_reason: str,
    chat_id: str,
    telegram: TelegramService,
) -> None:
    log_event(
        logger,
        "telegram_ai_fallback",
        tx_id=pending.transaction_id,
        reason=failure_reason,
    )
    pending.remember_failed_ai_attempt(original_message, failure_reason)
    title = pending.transaction_title or f"Transaction {pending.transaction_id}"
    telegram.send_message(
        format_ai_fallback_message(title),
        reply_markup=build_ai_fallback_keyboard(pending.transaction_id),
        chat_id=chat_id,
    )


def _route_context_grounded_ai_intent(
    pending: PendingTelegramSplit,
    intent: AIChatIntent,
    context: AIChatContext,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    if intent.action == "personal":
        _mark_personal(pending.transaction_id, chat_id, user_id, db, telegram)
        return
    if intent.action == "draft":
        _mark_shared_draft(pending.transaction_id, chat_id, user_id, db, telegram)
        return
    if intent.action == "cancel":
        telegram_split_state_store.clear(chat_id, user_id)
        telegram.send_message("✅ Split flow cancelled.", chat_id=chat_id)
        return
    if intent.action != "split":
        telegram.send_message(
            "I need a split, draft, personal, or cancel instruction.",
            chat_id=chat_id,
        )
        return

    if not _resolve_context_ai_aliases(intent, context, pending, chat_id, telegram):
        return

    if intent.split_mode == "equal":
        _send_split_confirmation(pending, chat_id, db, telegram)
        return

    participant_splits = _build_context_ai_custom_splits(
        intent,
        context,
        pending,
        chat_id,
        db,
        telegram,
    )
    if participant_splits is None:
        return

    pending.custom_split_mode = intent.split_mode
    pending.split_value_mode = intent.split_mode
    pending.custom_participant_splits = participant_splits
    _send_custom_split_confirmation(pending, chat_id, db, telegram)


def _resolve_context_ai_aliases(
    intent: AIChatIntent,
    context: AIChatContext,
    pending: PendingTelegramSplit,
    chat_id: str,
    telegram: TelegramService,
) -> bool:
    pending.selected_friend_ids = []
    pending.selected_friend_names_by_id = {}
    pending.friend_lookup_by_id = {}
    pending.selected_group_id = None
    pending.selected_group_name = None
    pending.group_members = []

    group = None
    allowed_member_aliases: set[str] = set()
    if intent.target_type == "group":
        group = context.group_by_alias.get(intent.group_alias or "")
        if not group:
            _record_ai_failure_and_prompt_button_mode(
                pending,
                pending.last_ai_message or "",
                "unknown_group",
                chat_id,
                telegram,
            )
            return False
        pending.selected_group_id = int(group["id"])
        pending.selected_group_name = str(group.get("name") or group["id"])
        pending.group_members = group.get("members", [])
        pending.remember_friends(pending.group_members)
        allowed_member_aliases = context.member_aliases_by_group_alias.get(
            intent.group_alias or "",
            set(),
        )

    pending.custom_payer_included = intent.include_me is not False
    if "me" in intent.participant_aliases:
        if intent.include_me is False:
            telegram.send_message("Should I include you in this split?", chat_id=chat_id)
            return False
        payer = context.payer_by_alias["me"]
        pending.payer_user_id = int(payer["id"])
        pending.add_friend(int(payer["id"]), friend_display_name(payer))

    for alias in intent.participant_aliases:
        if alias == "me":
            continue
        if group and alias not in allowed_member_aliases:
            telegram.send_message("That person is not in the selected group.", chat_id=chat_id)
            return False
        person = context.member_by_alias.get(alias) or context.friend_by_alias.get(alias)
        if not person:
            _record_ai_failure_and_prompt_button_mode(
                pending,
                pending.last_ai_message or "",
                "unknown_person",
                chat_id,
                telegram,
            )
            return False
        pending.add_friend(int(person["id"]), friend_display_name(person))

    if not _participant_friend_ids(pending):
        _record_ai_failure_and_prompt_button_mode(
            pending,
            pending.last_ai_message or "",
            "unknown_person",
            chat_id,
            telegram,
        )
        return False
    return True


def _build_context_ai_custom_splits(
    intent: AIChatIntent,
    context: AIChatContext,
    pending: PendingTelegramSplit,
    chat_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> list[dict] | None:
    if intent.split_mode not in {"exact_amounts", "percentages", "shares"}:
        telegram.send_message("Should this be amounts, percentages, or shares?", chat_id=chat_id)
        return None

    try:
        tx = TransactionService(db).get_transaction(pending.transaction_id)
    except TransactionError:
        telegram.send_message(
            "I could not load this transaction. Try button mode.",
            chat_id=chat_id,
        )
        return None

    alias_to_user = {"me": context.payer_by_alias.get("me")}
    alias_to_user.update(context.friend_by_alias)
    alias_to_user.update(context.member_by_alias)

    participant_aliases = [alias for alias in intent.participant_aliases if alias in alias_to_user]
    explicit = {value.alias: value for value in intent.custom_values}
    values = _expand_context_ai_equal_remaining_values(
        intent=intent,
        explicit=explicit,
        participant_aliases=participant_aliases,
        total_amount_cents=abs(tx.amount_cents),
    )
    if values is None:
        telegram.send_message(
            "I could not validate that split. Try button mode.",
            chat_id=chat_id,
        )
        pending.remember_failed_ai_attempt(pending.last_ai_message or "", "validation_failed")
        return None

    splits = []
    for alias in participant_aliases:
        person = alias_to_user.get(alias)
        value = values.get(alias)
        if not person or not value:
            telegram.send_message("I need values for every selected participant.", chat_id=chat_id)
            return None
        split_data = {"user_id": int(person["id"]), "display_name": friend_display_name(person)}
        if intent.split_mode == "exact_amounts":
            split_data["amount_cents"] = decimal_to_cents(value.amount or Decimal("0"))
        elif intent.split_mode == "percentages":
            split_data["percentage"] = value.percentage
        elif intent.split_mode == "shares":
            split_data["shares"] = value.shares
        splits.append(split_data)

    try:
        build_custom_split_shares_by_mode(
            total_cents=abs(tx.amount_cents),
            payer_user_id=pending.payer_user_id or int(context.payer_by_alias["me"]["id"]),
            payer_included=pending.custom_payer_included,
            split_mode=intent.split_mode,
            participant_splits=[
                CustomSplitInput(
                    user_id=split["user_id"],
                    amount_cents=split.get("amount_cents"),
                    percentage=split.get("percentage"),
                    shares=split.get("shares"),
                )
                for split in splits
            ],
        )
    except (KeyError, TypeError, ValueError):
        telegram.send_message(
            "That split does not add up. Try button mode or the dashboard.",
            chat_id=chat_id,
        )
        pending.remember_failed_ai_attempt(pending.last_ai_message or "", "validation_failed")
        return None

    return splits


def _expand_context_ai_equal_remaining_values(
    *,
    intent: AIChatIntent,
    explicit: dict[str, AICustomValue],
    participant_aliases: list[str],
    total_amount_cents: int,
) -> dict[str, AICustomValue] | None:
    values = dict(explicit)
    missing = [alias for alias in participant_aliases if alias not in values]
    if not missing:
        return values
    if intent.remaining_split_behavior != "equal_remaining":
        return None

    if intent.split_mode == "percentages":
        used = sum((value.percentage or Decimal("0")) for value in values.values())
        remaining = Decimal("100") - used
        if remaining < 0:
            return None
        each = remaining / Decimal(len(missing))
        for alias in missing:
            values[alias] = AICustomValue(alias=alias, percentage=each)
        return values

    if intent.split_mode == "exact_amounts":
        total = Decimal(total_amount_cents) / Decimal("100")
        used = sum((value.amount or Decimal("0")) for value in values.values())
        remaining = total - used
        if remaining < 0:
            return None
        each = remaining / Decimal(len(missing))
        for alias in missing:
            values[alias] = AICustomValue(alias=alias, amount=each)
        return values

    if intent.split_mode == "shares":
        for alias in missing:
            values[alias] = AICustomValue(alias=alias, shares=Decimal("1"))
        return values

    return None


def _deterministic_ai_chat_intent(text: str) -> LLMConversationIntent | None:
    lowered = " ".join(text.strip().lower().split())
    if lowered == "cancel":
        return LLMConversationIntent(action="cancel", confidence=Decimal("1"))
    if _is_explicit_personal_command(text):
        return LLMConversationIntent(action="personal", confidence=Decimal("1"))
    if lowered in {"draft", "draft this", "create draft", "create draft only"}:
        return LLMConversationIntent(action="draft", confidence=Decimal("1"))

    parsed = parse_conversational_split(text)
    if parsed.action == "personal":
        return LLMConversationIntent(action="personal", confidence=Decimal("1"))
    if parsed.action == "split_people":
        return LLMConversationIntent(
            action="split_people",
            target_type="people",
            participant_names=parsed.participant_names,
            split_mode="equal",
            remaining_split_behavior="none",
            confidence=Decimal("1"),
        )
    if parsed.action == "split_group":
        return LLMConversationIntent(
            action="split_group",
            target_type="group",
            group_name=parsed.group_name,
            participant_names=parsed.participant_names,
            split_mode="equal",
            remaining_split_behavior="none",
            confidence=Decimal("1"),
        )
    return None


def _parse_ai_chat_with_llm(
    pending: PendingTelegramSplit,
    text: str,
    chat_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> LLMConversationIntent | None:
    try:
        tx = TransactionService(db).get_transaction(pending.transaction_id)
    except TransactionError:
        telegram.send_message(
            "I could not load this transaction. Try button mode from the transaction message.",
            chat_id=chat_id,
        )
        return None

    result = LLMConversationParser().parse(
        user_message=text,
        transaction={
            "merchant": _safe_transaction_display_name(tx),
            "amount_cents": abs(tx.amount_cents),
            "currency_code": tx.iso_currency_code or "USD",
            "date": str(tx.date) if getattr(tx, "date", None) else None,
        },
        pending_state={
            "mode": pending.mode,
            "transaction_title": pending.transaction_title,
            "selected_group_name": pending.selected_group_name,
            "selected_participants": _selected_friend_names(pending),
            "split_target_mode": pending.split_target_mode,
            "split_value_mode": pending.split_value_mode,
            "ai_waiting_for": pending.ai_waiting_for,
            "payer_included": pending.custom_payer_included,
        },
        available_actions=[
            "personal",
            "draft",
            "split_people",
            "split_group",
            "custom_split",
            "clarify",
            "cancel",
        ],
    )
    if result.action == "clarify" or result.confidence < Decimal("0.75"):
        pending.ai_waiting_for = _ai_waiting_for_from_intent(result)
        telegram.send_message(
            result.clarification_question
            or "Can you say that another way? For example: split with Rahul and Akash.",
            chat_id=chat_id,
        )
        return None
    return result


def _route_ai_chat_intent(
    pending: PendingTelegramSplit,
    intent: LLMConversationIntent,
    original_text: str,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    pending.ai_intent_action = intent.action
    pending.ai_target_type = intent.target_type
    pending.ai_split_mode = intent.split_mode
    pending.ai_custom_values_text = intent.custom_values_text
    pending.ai_waiting_for = None

    if intent.action == "cancel":
        telegram_split_state_store.clear(chat_id, user_id)
        telegram.send_message("✅ Split flow cancelled.", chat_id=chat_id)
        return
    if intent.action == "personal":
        _mark_personal(pending.transaction_id, chat_id, user_id, db, telegram)
        return
    if intent.action == "draft":
        _mark_shared_draft(pending.transaction_id, chat_id, user_id, db, telegram)
        return

    split_mode = intent.split_mode if intent.split_mode != "unknown" else "equal"
    pending.custom_payer_included = (
        intent.payer_included
        if intent.payer_included is not None
        else pending.custom_payer_included
    )

    if intent.action == "split_people" and split_mode == "equal":
        if not intent.participant_names:
            pending.ai_waiting_for = "participants"
            telegram.send_message("Who should I split this with?", chat_id=chat_id)
            return
        pending.mode = "ai_chat"
        pending.ai_participant_names = intent.participant_names
        _resolve_ai_people_split(pending, intent.participant_names, chat_id, user_id, db, telegram)
        return

    if intent.action == "split_group" and split_mode == "equal":
        if not intent.group_name:
            pending.ai_waiting_for = "target"
            telegram.send_message("Which Splitwise group should I use?", chat_id=chat_id)
            return
        if not intent.participant_names:
            pending.ai_group_name = intent.group_name
            pending.ai_waiting_for = "participants"
            _prompt_ai_group_member_selection(pending, chat_id, db, telegram)
            return
        pending.mode = "ai_chat"
        pending.ai_group_name = intent.group_name
        pending.ai_participant_names = intent.participant_names
        _resolve_ai_group_split(pending, chat_id, user_id, db, telegram)
        return

    if intent.action == "split_group" and split_mode != "equal" and not intent.participant_names:
        if intent.group_name:
            pending.ai_group_name = intent.group_name
            pending.ai_waiting_for = "participants"
            _prompt_ai_group_member_selection(pending, chat_id, db, telegram)
            return

    if intent.action in {"custom_split", "split_people", "split_group"}:
        _start_ai_custom_split(pending, intent, original_text, chat_id, db, telegram)
        return

    telegram.send_message(
        "I could not understand that yet. Try: split with Rahul and Akash.",
        reply_markup=build_split_flow_keyboard(pending.transaction_id),
        chat_id=chat_id,
    )


def _start_ai_custom_split(
    pending: PendingTelegramSplit,
    intent: LLMConversationIntent,
    original_text: str,
    chat_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    split_mode = intent.custom_split_mode
    if split_mode == "unknown":
        pending.ai_waiting_for = "clarification"
        telegram.send_message(
            "Should this be amounts, percentages, or shares?",
            chat_id=chat_id,
        )
        return
    pending.split_value_mode = split_mode
    pending.custom_split_mode = split_mode
    pending.custom_payer_included = (
        intent.payer_included
        if intent.payer_included is not None
        else pending.custom_payer_included
    )

    if not intent.participant_names and not pending.selected_friend_ids:
        if intent.target_type == "group" or intent.group_name:
            pending.ai_group_name = intent.group_name
            pending.ai_waiting_for = "participants"
            _prompt_ai_group_member_selection(pending, chat_id, db, telegram)
            return
        pending.ai_waiting_for = "participants"
        telegram.send_message("Who should be included in this split?", chat_id=chat_id)
        return

    resolved = _resolve_ai_custom_participants(pending, intent, chat_id, telegram)
    if not resolved:
        return

    values_text = intent.custom_values_text or original_text
    if not intent.custom_values_text:
        pending.ai_waiting_for = "values"
        pending.mode = "ai_chat"
        telegram.send_message(
            format_custom_values_prompt(
                pending.transaction_title
                or _compact_title_for_transaction(pending.transaction_id, db),
                pending.split_value_mode,
                _selected_friend_names(pending),
            ),
            chat_id=chat_id,
        )
        return

    parsed = _parse_selected_custom_values(pending, values_text)
    if not parsed:
        parsed = _parse_custom_values_with_llm(pending, values_text, chat_id, db, telegram)
        if not parsed:
            return

    pending.custom_participant_splits = parsed
    pending.ai_waiting_for = None
    _send_custom_split_confirmation(pending, chat_id, db, telegram)


def _continue_ai_chat_pending_flow(
    pending: PendingTelegramSplit,
    text: str,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    if pending.ai_waiting_for == "target":
        pending.ai_group_name = text.strip()
        pending.ai_waiting_for = "participants"
        pending.ai_target_type = "group"
        _prompt_ai_group_member_selection(pending, chat_id, db, telegram)
        return

    if pending.ai_waiting_for == "participants":
        if _is_group_target_reply(text):
            pending.ai_target_type = "group"
            pending.ai_waiting_for = "target"
            telegram.send_message(
                "Which group would you like to split the expense in?",
                chat_id=chat_id,
            )
            return

        names = _parse_ai_name_list(text)
        if not names:
            telegram.send_message("Who should be included in this split?", chat_id=chat_id)
            return
        pending.ai_participant_names = names
        if pending.ai_target_type == "group" or pending.ai_group_name or pending.selected_group_id:
            if pending.selected_group_id and pending.group_members:
                if not _resolve_friend_names_from_pool(
                    pending,
                    names,
                    pending.group_members,
                    chat_id,
                    telegram,
                    no_match_label="group member",
                ):
                    return
                _continue_after_ai_participants(pending, chat_id, user_id, db, telegram)
                return
            if pending.ai_group_name:
                _resolve_ai_group_split(pending, chat_id, user_id, db, telegram)
                if _participant_friend_ids(pending) and pending.ai_waiting_for == "participants":
                    _continue_after_ai_participants(pending, chat_id, user_id, db, telegram)
                return
            pending.ai_waiting_for = "target"
            telegram.send_message("Which Splitwise group should I use?", chat_id=chat_id)
            return

        if not _resolve_ai_people_names(pending, names, chat_id, telegram):
            return
        _continue_after_ai_participants(pending, chat_id, user_id, db, telegram)
        return

    if pending.ai_waiting_for == "values":
        parsed = _parse_selected_custom_values(pending, text)
        if not parsed:
            parsed = _parse_custom_values_with_llm(pending, text, chat_id, db, telegram)
            if not parsed:
                return
        pending.custom_participant_splits = parsed
        pending.ai_waiting_for = None
        _send_custom_split_confirmation(pending, chat_id, db, telegram)
        return

    telegram.send_message(
        "Can you say that another way? For example: split with Rahul and Akash.",
        chat_id=chat_id,
    )


def _continue_after_ai_participants(
    pending: PendingTelegramSplit,
    chat_id: str,
    user_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    if pending.custom_split_mode in {"exact_amounts", "percentages", "shares"}:
        pending.ai_waiting_for = "values"
        telegram.send_message(
            format_custom_values_prompt(
                pending.transaction_title
                or _compact_title_for_transaction(pending.transaction_id, db),
                pending.split_value_mode,
                _selected_friend_names(pending),
            ),
            chat_id=chat_id,
        )
        return
    pending.ai_waiting_for = None
    _continue_or_finish_pending_split(pending, chat_id, user_id, db, telegram)


def _resolve_ai_people_names(
    pending: PendingTelegramSplit,
    names: list[str],
    chat_id: str,
    telegram: TelegramService,
) -> bool:
    try:
        friends = SplitwiseService().get_friends()
    except SplitwiseAPIError:
        telegram.send_message(
            "Could not search Splitwise friends. Try again from the dashboard.",
            chat_id=chat_id,
        )
        return False
    pending.remember_friends(friends)
    return _resolve_friend_names_from_pool(pending, names, friends, chat_id, telegram)


def _prompt_ai_group_member_selection(
    pending: PendingTelegramSplit,
    chat_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> None:
    if pending.selected_group_id and pending.group_members:
        if pending.payer_user_id is None:
            try:
                current_user = SplitwiseService().get_current_user()
                pending.payer_user_id = int(current_user["id"])
            except (KeyError, TypeError, ValueError, AttributeError, SplitwiseAPIError):
                pass
        _preselect_payer_if_group_member(pending)
        pending.mode = "group_members"
        telegram.send_message(
            format_group_members_prompt(
                pending.selected_group_name or "Selected group",
                _selected_friend_names(pending),
                pending.transaction_title
                or _compact_title_for_transaction(pending.transaction_id, db),
            ),
            reply_markup=build_group_member_select_keyboard(
                pending.transaction_id,
                pending.group_members,
                pending.selected_friend_ids,
                pending.payer_user_id,
            ),
            chat_id=chat_id,
        )
        return

    if not pending.ai_group_name:
        pending.ai_waiting_for = "target"
        telegram.send_message("Which Splitwise group should I use?", chat_id=chat_id)
        return

    try:
        groups = SplitwiseService().get_groups()
    except SplitwiseAPIError:
        telegram.send_message(
            "Could not search Splitwise groups. Try button mode or the dashboard.",
            chat_id=chat_id,
        )
        return

    group_matches = find_group_matches(pending.ai_group_name, groups)
    if not group_matches:
        telegram.send_message(
            f"No Splitwise group matched '{pending.ai_group_name}'. Try again.",
            chat_id=chat_id,
        )
        return
    if len(group_matches) > 1:
        pending.mode = "ai_group_choice"
        pending.ambiguous_groups_by_name[pending.ai_group_name] = group_matches
        telegram.send_message(
            format_group_ambiguity_message(pending.ai_group_name),
            reply_markup=build_group_choice_keyboard(pending.transaction_id, group_matches),
            chat_id=chat_id,
        )
        return

    _select_group_for_pending_split(pending, group_matches[0], chat_id, telegram, prompt=False)
    _preselect_payer_if_group_member(pending)
    pending.mode = "group_members"
    telegram.send_message(
        format_group_members_prompt(
            pending.selected_group_name or "Selected group",
            _selected_friend_names(pending),
            pending.transaction_title
            or _compact_title_for_transaction(pending.transaction_id, db),
        ),
        reply_markup=build_group_member_select_keyboard(
            pending.transaction_id,
            pending.group_members,
            pending.selected_friend_ids,
            pending.payer_user_id,
        ),
        chat_id=chat_id,
    )


def _preselect_payer_if_group_member(pending: PendingTelegramSplit) -> None:
    if not pending.custom_payer_included or not pending.payer_user_id:
        return
    for member in pending.group_members:
        if int(member["id"]) == pending.payer_user_id:
            pending.add_friend(pending.payer_user_id, friend_display_name(member))
            return


def _resolve_ai_custom_participants(
    pending: PendingTelegramSplit,
    intent: LLMConversationIntent,
    chat_id: str,
    telegram: TelegramService,
) -> bool:
    if intent.target_type == "group" or intent.group_name:
        if not intent.group_name:
            telegram.send_message("Which Splitwise group should I use?", chat_id=chat_id)
            return False
        try:
            groups = SplitwiseService().get_groups()
        except SplitwiseAPIError:
            telegram.send_message(
                "Could not search Splitwise groups. Try button mode or the dashboard.",
                chat_id=chat_id,
            )
            return False
        group_matches = find_group_matches(intent.group_name, groups)
        if len(group_matches) != 1:
            pending.mode = "ai_group_choice"
            pending.ambiguous_groups_by_name[intent.group_name] = group_matches
            telegram.send_message(
                format_group_ambiguity_message(intent.group_name),
                reply_markup=build_group_choice_keyboard(pending.transaction_id, group_matches),
                chat_id=chat_id,
            )
            return False
        _select_group_for_pending_split(pending, group_matches[0], chat_id, telegram, prompt=False)
        return _resolve_friend_names_from_pool(
            pending,
            intent.participant_names,
            pending.group_members,
            chat_id,
            telegram,
            no_match_label="group member",
        )

    try:
        friends = SplitwiseService().get_friends()
    except SplitwiseAPIError:
        telegram.send_message(
            "Could not search Splitwise friends. Try button mode or the dashboard.",
            chat_id=chat_id,
        )
        return False
    pending.remember_friends(friends)
    return _resolve_friend_names_from_pool(
        pending,
        intent.participant_names,
        friends,
        chat_id,
        telegram,
    )


def _is_explicit_personal_command(text: str) -> bool:
    lowered = " ".join(text.strip().lower().split())
    return lowered in {"personal", "mark personal", "mine", "mark as personal"}


def _is_top_level_ai_command(text: str) -> bool:
    lowered = " ".join(text.strip().lower().split())
    return (
        _is_explicit_personal_command(text)
        or lowered in {"cancel", "draft", "draft this", "create draft", "create draft only"}
        or lowered.startswith("split ")
    )


def _is_group_target_reply(text: str) -> bool:
    lowered = " ".join(text.strip().lower().split())
    return lowered in {"group", "a group", "in group", "split in group"}


def _parse_ai_name_list(text: str) -> list[str]:
    normalized = re.sub(r"\s+and\s+", ",", text.strip(), flags=re.IGNORECASE)
    return [part.strip() for part in normalized.split(",") if part.strip()]


def _ai_waiting_for_from_intent(intent: LLMConversationIntent) -> str:
    question = (intent.clarification_question or "").lower()
    if "who" in question or "participant" in question or "person" in question:
        return "participants"
    if "group" in question or "people" in question:
        return "target"
    if "amount" in question or "percentage" in question or "share" in question:
        return "values"
    return "clarification"


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
        parsed = _parse_custom_values_with_llm(pending, text, chat_id, db, telegram)
        if not parsed:
            return
    pending.custom_split_mode = pending.split_value_mode
    pending.custom_participant_splits = parsed
    _send_custom_split_confirmation(pending, chat_id, db, telegram)


def _parse_custom_values_with_llm(
    pending: PendingTelegramSplit,
    text: str,
    chat_id: str,
    db: DbSession,
    telegram: TelegramService,
) -> list[dict] | None:
    try:
        tx = TransactionService(db).get_transaction(pending.transaction_id)
    except TransactionError:
        telegram.send_message(
            "I could not load this transaction. Use the dashboard to review it.",
            chat_id=chat_id,
        )
        return None

    selected_participants = [
        {
            "user_id": friend_id,
            "display_name": pending.selected_friend_names_by_id.get(friend_id)
            or pending.friend_lookup_by_id.get(friend_id)
            or str(friend_id),
        }
        for friend_id in pending.selected_friend_ids
    ]
    payer = None
    if pending.custom_payer_included and pending.payer_user_id:
        payer = {
            "user_id": pending.payer_user_id,
            "display_name": "You",
        }

    result = LLMSplitParser().parse(
        user_message=text,
        total_amount_cents=abs(tx.amount_cents),
        currency_code=tx.iso_currency_code or "USD",
        split_mode=pending.split_value_mode,
        payer_included=pending.custom_payer_included,
        selected_participants=selected_participants,
        payer=payer,
    )
    if not result.ok:
        telegram.send_message(
            result.clarification_question
            or "I could not parse that split. Try Rahul=20, Akash=35, or use the dashboard.",
            chat_id=chat_id,
        )
        return None

    participant_splits: list[dict] = []
    for split in result.participant_splits:
        split_data: dict = {
            "user_id": split.user_id,
            "display_name": split.display_name,
        }
        if pending.split_value_mode == "exact_amounts":
            split_data["amount_cents"] = split.amount_cents
        elif pending.split_value_mode == "percentages":
            split_data["percentage"] = split.percentage
        elif pending.split_value_mode == "shares":
            split_data["shares"] = split.shares
        else:
            telegram.send_message(
                "I could not parse that split mode. Use the dashboard to review it.",
                chat_id=chat_id,
            )
            return None
        participant_splits.append(split_data)

    return participant_splits


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
                    if pending.ai_slots:
                        pending.ai_slots["resolved_group_id"] = int(group["id"])
                        pending.ai_slots["resolved_group_name"] = str(
                            group.get("name") or group["id"]
                        )
                        pending.ai_slots["resolved_group"] = group
                        pending.ai_slots["ai_waiting_for"] = "group_confirmation"
                        pending.ai_waiting_for = "group_confirmation"
                        telegram.send_message(
                            format_ai_group_confirmation_message(
                                pending.transaction_title
                                or _compact_title_for_transaction(transaction_id, db),
                                pending.ai_slots["resolved_group_name"],
                            ),
                            reply_markup=build_ai_group_confirmation_keyboard(transaction_id),
                            chat_id=chat_id,
                        )
                    else:
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

    if pending.ai_waiting_for == "participant_disambiguation" and pending.ai_slots:
        pending.add_friend(friend_id, _friend_name_from_pending_choice(pending, friend_id))
        if pending.remaining_unresolved_names:
            resolved_name = pending.remaining_unresolved_names.pop(0)
            pending.ambiguous_matches_by_name.pop(resolved_name, None)
        pending.ai_slots["resolved_participants"] = [
            {
                "user_id": selected_id,
                "display_name": pending.selected_friend_names_by_id.get(
                    selected_id,
                    str(selected_id),
                ),
            }
            for selected_id in pending.selected_friend_ids
        ]
        pending.ai_slots["ambiguous_participants"] = []
        pending.ai_waiting_for = None
        pending.ai_slots["ai_waiting_for"] = None
        _send_enterprise_ai_confirmation(pending, chat_id, db, telegram)
        return "Selection saved."

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
    pending = telegram_split_state_store.get_pending(chat_id, user_id)
    final_participants = [
        {"user_id": friend_id, "display_name": name}
        for friend_id, name in zip(friend_user_ids, friend_names, strict=False)
    ]
    _record_ai_interpretation_memory_if_needed(
        tx,
        pending,
        db,
        final_action="split_equal",
        final_group_id=group_id,
        final_group_name=pending.selected_group_name if pending else None,
        final_participants=final_participants,
        final_split_mode="equal",
        payer_included=True,
    )
    _record_button_fallback_memory_if_needed(
        tx,
        pending,
        db,
        final_action="split_equal",
        final_group_id=group_id,
        final_group_name=pending.selected_group_name if pending else None,
        final_participants=final_participants,
        final_split_mode="equal",
        payer_included=True,
    )
    participant_names = ["You", *friend_names]
    telegram_split_state_store.clear(chat_id, user_id)
    log_event(
        logger,
        "telegram_split_posted",
        tx_id=tx.id,
        splitwise_expense_id=tx.splitwise_expense_id,
    )
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
            _custom_split_input_from_pending_split(split)
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
    except (KeyError, TypeError, TransactionError, SplitwiseAPIError, ValueError) as exc:
        db.rollback()
        pending.is_submitting = False
        telegram_split_state_store.update_pending(chat_id, user_id, pending)
        log_event(
            logger,
            "telegram_custom_split_failed",
            level=logging.WARNING,
            tx_id=pending.transaction_id,
            reason=_safe_custom_split_failure_reason(exc),
            error_type=type(exc).__name__,
        )
        telegram.send_message(
            "\n".join(
                [
                    "Could not create the custom split.",
                    _safe_custom_split_failure_message(exc),
                    "You can adjust and try again, or open the dashboard to review.",
                ]
            ),
            chat_id=chat_id,
        )
        return

    final_participants = [
        {
            "user_id": split["user_id"],
            "display_name": split.get("display_name") or str(split["user_id"]),
        }
        for split in pending.custom_participant_splits
    ]
    _record_ai_interpretation_memory_if_needed(
        tx,
        pending,
        db,
        final_action="custom_split",
        final_group_id=pending.selected_group_id,
        final_group_name=pending.selected_group_name,
        final_participants=final_participants,
        final_split_mode=pending.custom_split_mode,
        payer_included=pending.custom_payer_included,
        custom_values=pending.custom_participant_splits,
    )
    _record_button_fallback_memory_if_needed(
        tx,
        pending,
        db,
        final_action="custom_split",
        final_group_id=pending.selected_group_id,
        final_group_name=pending.selected_group_name,
        final_participants=final_participants,
        final_split_mode=pending.custom_split_mode,
        payer_included=pending.custom_payer_included,
        custom_values=pending.custom_participant_splits,
    )
    try:
        payload = json.loads(tx.splitwise_payload_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    participant_lines = _custom_participant_lines_from_payload(
        payload,
        pending.custom_participant_splits,
    )
    telegram_split_state_store.clear(chat_id, user_id)
    log_event(
        logger,
        "telegram_custom_split_posted",
        tx_id=tx.id,
        splitwise_expense_id=tx.splitwise_expense_id,
    )
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


def _safe_custom_split_failure_reason(exc: Exception) -> str:
    if isinstance(exc, SplitwiseAPIError):
        errors = exc.response_data.get("errors")
        if errors:
            return "splitwise_errors"
        return "splitwise_api_error"
    if isinstance(exc, TransactionError):
        return str(exc)[:160]
    if isinstance(exc, ValueError):
        return str(exc)[:160]
    return type(exc).__name__


def _custom_split_input_from_pending_split(split: dict) -> CustomSplitInput:
    return CustomSplitInput(
        user_id=int(split["user_id"]),
        amount_cents=_optional_int(split.get("amount_cents")),
        percentage=_optional_decimal(split.get("percentage")),
        shares=_optional_decimal(split.get("shares")),
    )


def _optional_int(value) -> int | None:
    if value in {None, ""}:
        return None
    return int(value)


def _optional_decimal(value) -> Decimal | None:
    if value in {None, ""}:
        return None
    return Decimal(str(value))


def _safe_custom_split_failure_message(exc: Exception) -> str:
    if isinstance(exc, SplitwiseAPIError):
        errors = exc.response_data.get("errors")
        if errors:
            return f"Splitwise rejected it: {_compact_error_value(errors)}"
        return "Splitwise rejected the expense request."
    if isinstance(exc, TransactionError | ValueError):
        return str(exc)
    return "The saved Telegram split state was incomplete or expired."


def _compact_error_value(value) -> str:
    text = str(value)
    return text if len(text) <= 220 else f"{text[:217]}..."


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
        log_event(
            logger,
            "telegram_personal_failed",
            level=logging.WARNING,
            tx_id=transaction_id,
            reason="validation_failed",
            error_type=type(exc).__name__,
        )
        return "Could not mark personal. Open the dashboard to review."
    log_event(logger, "telegram_split_confirmed", tx_id=transaction_id, action="personal")
    pending = telegram_split_state_store.get_pending(chat_id, user_id)
    _record_button_fallback_memory_if_needed(
        tx,
        pending,
        db,
        final_action="personal",
    )
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
        log_event(
            logger,
            "telegram_undo",
            level=logging.WARNING,
            tx_id=transaction_id,
            reason="validation_failed",
            error_type=type(exc).__name__,
        )
        telegram.send_message(
            "Could not undo this transaction. Open the dashboard to review.",
            chat_id=chat_id,
        )
        return "Could not undo this transaction."

    telegram_review_queue_store.clear(chat_id, user_id)
    telegram_split_state_store.clear(chat_id, user_id)
    log_event(logger, "telegram_undo", tx_id=tx.id)
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
        log_event(
            logger,
            "telegram_draft_failed",
            level=logging.WARNING,
            tx_id=transaction_id,
            reason="validation_failed",
            error_type=type(exc).__name__,
        )
        return "Could not create draft. Open the dashboard to review."
    log_event(logger, "telegram_split_confirmed", tx_id=transaction_id, action="draft")
    pending = telegram_split_state_store.get_pending(chat_id, user_id)
    _record_button_fallback_memory_if_needed(
        tx,
        pending,
        db,
        final_action="draft",
        final_split_mode="equal",
    )
    _record_completion_and_show_next(tx, chat_id, user_id, db, telegram)
    return "Draft saved."


def _record_button_fallback_memory_if_needed(
    tx: ExpenseTransaction,
    pending: PendingTelegramSplit | None,
    db: DbSession,
    *,
    final_action: str,
    final_group_id: int | None = None,
    final_group_name: str | None = None,
    final_participants: list[dict] | None = None,
    final_split_mode: str | None = None,
    payer_included: bool = True,
    custom_values: list[dict] | None = None,
) -> None:
    if not pending or not pending.button_fallback_active:
        return
    try:
        AIInterpretationMemoryService(db).record_button_fallback_memory(
            tx=tx,
            pending=pending,
            final_action=final_action,
            final_group_id=final_group_id,
            final_group_name=final_group_name,
            final_participants=final_participants,
            final_split_mode=final_split_mode,
            payer_included=payer_included,
            custom_values=custom_values,
        )
    except (SQLAlchemyError, TypeError, ValueError) as exc:
        log_event(
            logger,
            "ai_memory_recorded",
            level=logging.WARNING,
            tx_id=tx.id,
            correction_type="button_fallback_learned",
            reason="db_error",
            error_type=type(exc).__name__,
        )
        return
    log_event(
        logger,
        "ai_memory_recorded",
        tx_id=tx.id,
        correction_type="button_fallback_learned",
    )


def _record_ai_interpretation_memory_if_needed(
    tx: ExpenseTransaction,
    pending: PendingTelegramSplit | None,
    db: DbSession,
    *,
    final_action: str,
    final_group_id: int | None = None,
    final_group_name: str | None = None,
    final_participants: list[dict] | None = None,
    final_split_mode: str | None = None,
    payer_included: bool = True,
    custom_values: list[dict] | None = None,
) -> None:
    if not pending or pending.button_fallback_active:
        return
    correction_type = pending.ai_correction_type or "ai_confirmed"
    try:
        AIInterpretationMemoryService(db).record_ai_interpretation_memory(
            tx=tx,
            pending=pending,
            final_action=final_action,
            final_group_id=final_group_id,
            final_group_name=final_group_name,
            final_participants=final_participants,
            final_split_mode=final_split_mode,
            payer_included=payer_included,
            custom_values=custom_values,
            correction_type=correction_type,
        )
    except (SQLAlchemyError, TypeError, ValueError) as exc:
        log_event(
            logger,
            "ai_memory_recorded",
            level=logging.WARNING,
            tx_id=tx.id,
            correction_type=correction_type,
            reason="db_error",
            error_type=type(exc).__name__,
        )
        return
    log_event(
        logger,
        "ai_memory_recorded",
        tx_id=tx.id,
        correction_type=correction_type,
    )


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

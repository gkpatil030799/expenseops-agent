from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass
from datetime import timedelta

from app.config import get_settings
from app.db import SessionLocal
from app.job_tenancy import telegram_settings_for_workspace
from app.logging_config import log_event
from app.models import (
    ExpenseTransaction,
    FinancialOperation,
    OutboxEvent,
    PlaidWebhookEvent,
    TelegramWebhookUpdate,
    TransactionStatus,
    utc_now,
)
from app.services.notification_service import NotificationService
from app.services.outbox_service import (
    claim_outbox_batch,
    complete_outbox_event,
    fail_outbox_event,
    replay_dead_outbox_events,
)
from app.tenancy import clear_session_tenant, set_active_user, set_trusted_workspace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimedEvent:
    id: int
    workspace_id: int
    event_type: str
    lease_token: str


def run_once(max_events: int = 25) -> dict[str, int]:
    get_settings().validate_worker_runtime()
    with SessionLocal() as db:
        clear_session_tenant(db)
        claimed = [
            ClaimedEvent(event.id, event.workspace_id, event.event_type, str(event.lease_token))
            for event in claim_outbox_batch(db, limit=max_events)
        ]
    result = {"claimed": len(claimed), "succeeded": 0, "retried": 0, "dead": 0}
    for claimed_event in claimed:
        with SessionLocal() as db:
            clear_session_tenant(db)
            try:
                _handle_event(db, claimed_event)
                clear_session_tenant(db)
                if complete_outbox_event(db, claimed_event.id, claimed_event.lease_token):
                    result["succeeded"] += 1
            except Exception as exc:
                db.rollback()
                clear_session_tenant(db)
                state = fail_outbox_event(
                    db,
                    claimed_event.id,
                    claimed_event.lease_token,
                    exc,
                    retry_after_seconds=getattr(exc, "retry_after_seconds", None),
                )
                if state == "retry":
                    result["retried"] += 1
                elif state == "dead":
                    result["dead"] += 1
                log_event(
                    logger,
                    "outbox_event_failed",
                    level=logging.ERROR,
                    outbox_event_id=claimed_event.id,
                    event_type=claimed_event.event_type,
                    workspace_id=claimed_event.workspace_id,
                    state=state,
                    error_type=type(exc).__name__,
                )
    return result


def run_forever(*, max_events: int = 25, poll_seconds: float = 2.0) -> None:
    while True:
        outcome = run_once(max_events)
        if outcome["claimed"] == 0:
            time.sleep(poll_seconds)


def _handle_event(db, claimed: ClaimedEvent) -> None:
    event = db.get(
        OutboxEvent,
        claimed.id,
        execution_options={"skip_tenant_scope": True},
    )
    if event is None:
        raise RuntimeError("outbox_event_missing")
    set_trusted_workspace(db, claimed.workspace_id)
    if event.event_type == "plaid.sync_item":
        _handle_plaid_sync(event)
        return
    if event.event_type == "telegram.review_transaction":
        _handle_telegram_review(db, event)
        return
    if event.event_type == "telegram.splitwise_posted":
        _handle_telegram_splitwise_posted(db, event)
        return
    if event.event_type == "splitwise.execute_operation":
        _handle_splitwise_operation(db, event)
        return
    if event.event_type == "telegram.process_update":
        _handle_telegram_update(db, event)
        return
    raise RuntimeError(f"unsupported_outbox_event:{event.event_type}")


def _handle_plaid_sync(event: OutboxEvent) -> None:
    from app.api.plaid_routes import _sync_item_by_db_id

    item_id = int(event.payload_json["item_id"])
    webhook_event_id = int(event.payload_json["webhook_event_id"])
    with SessionLocal() as verification_db:
        webhook = verification_db.get(PlaidWebhookEvent, webhook_event_id)
        if webhook is not None and webhook.processing_status == "processed":
            return
    _sync_item_by_db_id(item_id, webhook_event_id)
    with SessionLocal() as verification_db:
        webhook = verification_db.get(PlaidWebhookEvent, webhook_event_id)
        if webhook is None or webhook.processing_status != "processed":
            reason = webhook.error_message if webhook is not None else "webhook_event_missing"
            raise RuntimeError(f"plaid_sync_not_processed:{reason}")


def _handle_telegram_review(db, event: OutboxEvent) -> None:
    tx = db.get(ExpenseTransaction, int(event.payload_json["transaction_id"]))
    if tx is None:
        raise RuntimeError("transaction_missing")
    if tx.review_notification_sent_at is not None:
        return
    item = tx.plaid_item
    settings = telegram_settings_for_workspace(
        db,
        tx.workspace_id,
        get_settings(),
        user_id=item.owner_user_id if item else None,
    )
    if not settings.telegram_chat_id:
        raise RuntimeError("telegram_recipient_not_connected")
    if NotificationService(settings).notify_transaction_needs_review(tx) is False:
        raise RuntimeError("telegram_delivery_failed")
    tx.review_notification_sent_at = utc_now()
    tx.review_notification_queued_at = None
    tx.updated_at = utc_now()
    db.commit()


def _handle_telegram_splitwise_posted(db, event: OutboxEvent) -> None:
    tx = db.get(ExpenseTransaction, int(event.payload_json["transaction_id"]))
    if tx is None:
        raise RuntimeError("transaction_missing")
    expense_id = str(event.payload_json.get("splitwise_expense_id") or "")
    if not expense_id:
        raise RuntimeError("splitwise_expense_id_missing")
    if tx.splitwise_expense_id != expense_id:
        # The confirmation became stale before delivery (for example, the user
        # already removed the split). Completing it silently avoids misleading
        # the recipient about the current state.
        return
    recipient_user_id = int(event.payload_json["recipient_user_id"])
    settings = telegram_settings_for_workspace(
        db,
        tx.workspace_id,
        get_settings(),
        user_id=recipient_user_id,
    )
    if not settings.telegram_chat_id:
        raise RuntimeError("telegram_recipient_not_connected")
    if NotificationService(settings).notify_splitwise_posted(tx, expense_id) is False:
        raise RuntimeError("telegram_delivery_failed")


def _handle_splitwise_operation(db, event: OutboxEvent) -> None:
    from app.services.transaction_service import TransactionService

    operation = db.get(FinancialOperation, int(event.payload_json["operation_id"]))
    if operation is None:
        raise RuntimeError("financial_operation_missing")
    tx = db.get(ExpenseTransaction, operation.transaction_id)
    if tx is None:
        raise RuntimeError("transaction_missing")
    if operation.state == "succeeded":
        return
    recipient_user_id = operation.actor_user_id
    if recipient_user_id is None and tx.plaid_item is not None:
        recipient_user_id = tx.plaid_item.owner_user_id
    if recipient_user_id is None:
        raise RuntimeError("splitwise_actor_missing")
    db.info["user_id"] = int(recipient_user_id)
    set_active_user(int(recipient_user_id))
    db.info["durable_worker"] = True
    db.info["interaction_channel"] = "worker"
    service = TransactionService(db)
    request = dict(operation.request_json or {})
    if operation.action == "splitwise_create":
        service._execute_splitwise_create(tx, request, operation=operation)  # noqa: SLF001
        return
    if operation.action == "splitwise_update":
        payload = dict(request.get("payload") or {})
        if not payload:
            raise RuntimeError("splitwise_update_payload_missing")
        service._execute_splitwise_update(tx, payload, operation=operation)  # noqa: SLF001
        return
    if operation.action in {"splitwise_delete", "splitwise_delete_removed"}:
        service._execute_splitwise_delete(  # noqa: SLF001
            tx,
            operation=operation,
            action=operation.action,
            result_status=str(request.get("result_status") or TransactionStatus.ASK_USER.value),
        )
        return
    raise RuntimeError(f"unsupported_financial_operation:{operation.action}")


def _handle_telegram_update(db, event: OutboxEvent) -> None:
    from app.api.telegram_routes import _fail_telegram_update, _process_telegram_update

    record = db.get(
        TelegramWebhookUpdate,
        int(event.payload_json["update_record_id"]),
        execution_options={"skip_tenant_scope": True},
    )
    if record is None:
        raise RuntimeError("telegram_update_missing")
    if record.state == "processed":
        return
    record.state = "processing"
    record.attempt_count = max(1, record.attempt_count)
    record.lease_expires_at = utc_now() + timedelta(seconds=180)
    record.last_error = None
    db.commit()
    try:
        _process_telegram_update(db, dict(event.payload_json.get("update") or {}), record)
    except Exception as exc:
        _fail_telegram_update(db, record, exc)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Drain durable ExpenseOps outbox events")
    parser.add_argument("--max-events", type=int, default=25, metavar="1-250")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0, metavar="0.25-60")
    parser.add_argument(
        "--replay-dead",
        action="store_true",
        help="Requeue dead-letter events before draining the worker.",
    )
    parser.add_argument(
        "--event-type",
        help="Limit --replay-dead to one event type.",
    )
    args = parser.parse_args()
    if not 1 <= args.max_events <= 250:
        parser.error("--max-events must be between 1 and 250")
    if not 0.25 <= args.poll_seconds <= 60:
        parser.error("--poll-seconds must be between 0.25 and 60")
    if args.once:
        if args.replay_dead:
            with SessionLocal() as db:
                clear_session_tenant(db)
                replayed = replay_dead_outbox_events(
                    db,
                    event_type=args.event_type,
                    limit=args.max_events,
                )
                print({"replayed": replayed})
        outcome = run_once(args.max_events)
        print(outcome)
        if outcome["dead"]:
            raise SystemExit(1)
    else:
        run_forever(max_events=args.max_events, poll_seconds=args.poll_seconds)

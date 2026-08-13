from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass

from app.config import get_settings
from app.db import SessionLocal
from app.job_tenancy import telegram_settings_for_workspace
from app.logging_config import log_event
from app.models import ExpenseTransaction, OutboxEvent, PlaidWebhookEvent, utc_now
from app.services.notification_service import NotificationService
from app.services.outbox_service import (
    claim_outbox_batch,
    complete_outbox_event,
    fail_outbox_event,
)
from app.tenancy import clear_session_tenant, set_trusted_workspace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimedEvent:
    id: int
    workspace_id: int
    event_type: str
    lease_token: str


def run_once(max_events: int = 25) -> dict[str, int]:
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Drain durable ExpenseOps outbox events")
    parser.add_argument("--max-events", type=int, default=25, metavar="1-250")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0, metavar="0.25-60")
    args = parser.parse_args()
    if not 1 <= args.max_events <= 250:
        parser.error("--max-events must be between 1 and 250")
    if not 0.25 <= args.poll_seconds <= 60:
        parser.error("--poll-seconds must be between 0.25 and 60")
    if args.once:
        outcome = run_once(args.max_events)
        print(outcome)
        if outcome["dead"]:
            raise SystemExit(1)
    else:
        run_forever(max_events=args.max_events, poll_seconds=args.poll_seconds)

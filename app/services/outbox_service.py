from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.logging_config import get_trace_id
from app.models import OutboxEvent, utc_now


def enqueue_outbox_event(
    db: Session,
    *,
    workspace_id: int,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str | int,
    dedupe_key: str,
    payload: dict[str, Any],
) -> OutboxEvent:
    existing = db.scalar(
        select(OutboxEvent)
        .execution_options(skip_tenant_scope=True)
        .where(
            OutboxEvent.workspace_id == workspace_id,
            OutboxEvent.dedupe_key == dedupe_key,
        )
    )
    if existing is not None:
        return existing
    event = OutboxEvent(
        workspace_id=workspace_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        dedupe_key=dedupe_key,
        payload_json=payload,
        correlation_id=get_trace_id(),
    )
    try:
        with db.begin_nested():
            db.add(event)
            db.flush()
    except IntegrityError:
        existing = db.scalar(
            select(OutboxEvent)
            .execution_options(skip_tenant_scope=True)
            .where(
                OutboxEvent.workspace_id == workspace_id,
                OutboxEvent.dedupe_key == dedupe_key,
            )
        )
        if existing is None:
            raise
        return existing
    return event


def claim_outbox_batch(
    db: Session,
    *,
    limit: int = 25,
    lease_seconds: int = 180,
) -> list[OutboxEvent]:
    now = utc_now()
    candidates = list(
        db.scalars(
            select(OutboxEvent)
            .execution_options(skip_tenant_scope=True)
            .where(
                OutboxEvent.state.in_(("pending", "retry", "in_flight")),
                OutboxEvent.available_at <= now,
                or_(
                    OutboxEvent.lease_expires_at.is_(None),
                    OutboxEvent.lease_expires_at <= now,
                ),
            )
            .order_by(OutboxEvent.available_at, OutboxEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    claimed: list[OutboxEvent] = []
    for event in candidates:
        event.state = "in_flight"
        event.lease_token = secrets.token_hex(16)
        event.lease_expires_at = now + timedelta(seconds=lease_seconds)
        event.attempt_count += 1
        event.updated_at = now
        claimed.append(event)
    db.commit()
    return claimed


def complete_outbox_event(db: Session, event_id: int, lease_token: str) -> bool:
    event = _leased_event(db, event_id, lease_token)
    if event is None:
        return False
    now = utc_now()
    event.state = "succeeded"
    event.completed_at = now
    event.updated_at = now
    event.lease_token = None
    event.lease_expires_at = None
    event.last_error = None
    # Telegram updates may contain message text, profile metadata, receipt file
    # identifiers, and one-time connection codes. They are only needed while the
    # durable handler is pending; the audit event and webhook-update row retain
    # the non-sensitive delivery outcome after successful processing.
    if event.event_type == "telegram.process_update":
        event.payload_json = {}
    db.commit()
    return True


def replay_dead_outbox_events(
    db: Session,
    *,
    event_type: str | None = None,
    limit: int = 25,
) -> int:
    stmt = (
        select(OutboxEvent)
        .execution_options(skip_tenant_scope=True)
        .where(OutboxEvent.state == "dead")
        .order_by(OutboxEvent.updated_at, OutboxEvent.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    if event_type:
        stmt = stmt.where(OutboxEvent.event_type == event_type)
    events = list(db.scalars(stmt))
    now = utc_now()
    for event in events:
        event.state = "pending"
        event.attempt_count = 0
        event.available_at = now
        event.lease_token = None
        event.lease_expires_at = None
        event.last_error = None
        event.completed_at = None
        event.updated_at = now
    db.commit()
    return len(events)


def fail_outbox_event(
    db: Session,
    event_id: int,
    lease_token: str,
    error: Exception,
    *,
    max_attempts: int = 8,
    retry_after_seconds: int | None = None,
) -> str:
    event = _leased_event(db, event_id, lease_token)
    if event is None:
        return "lease_lost"
    now = utc_now()
    terminal = event.attempt_count >= max_attempts
    event.state = "dead" if terminal else "retry"
    delay_seconds = _retry_delay_seconds(event.attempt_count)
    if retry_after_seconds is not None:
        delay_seconds = max(delay_seconds, min(86_400, max(0, retry_after_seconds)))
    event.available_at = now + timedelta(seconds=delay_seconds)
    event.updated_at = now
    event.lease_token = None
    event.lease_expires_at = None
    event.last_error = f"{type(error).__name__}: {error}"[:2000]
    db.commit()
    return event.state


def _leased_event(db: Session, event_id: int, lease_token: str) -> OutboxEvent | None:
    return db.scalar(
        select(OutboxEvent)
        .execution_options(skip_tenant_scope=True)
        .where(
            OutboxEvent.id == event_id,
            OutboxEvent.state == "in_flight",
            OutboxEvent.lease_token == lease_token,
        )
    )


def _retry_delay_seconds(attempt_count: int) -> int:
    # Bounded jitter prevents a provider recovery from waking every tenant at
    # once. The provider's Retry-After value, when present, is applied as the
    # lower bound by ``fail_outbox_event``.
    base = min(3600, 5 * (2 ** max(0, attempt_count - 1)))
    jitter = secrets.randbelow(max(2, (base // 4) + 1))
    return min(3600, base + jitter)

from __future__ import annotations

import json
import logging
from hashlib import sha256

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sqlalchemy import select

from app.api.deps import DbSession
from app.config import get_settings
from app.logging_config import log_event
from app.models import PlaidItem, PlaidWebhookEvent, utc_now
from app.schemas import (
    LinkTokenResponse,
    PublicTokenExchangeRequest,
    PublicTokenExchangeResponse,
    WebhookAck,
)
from app.security import encrypt_secret
from app.services.plaid_service import (
    PlaidConfigurationError,
    PlaidRequestError,
    PlaidService,
    PlaidWebhookVerificationError,
)
from app.services.transaction_service import TransactionService
from sandbox.backend.event_store import SandboxEventStore
from sandbox.backend.webhook_hooks import (
    maybe_log_sandbox_webhook,
    maybe_log_sandbox_webhook_verification_event,
    sandbox_sync_guard_finish,
    sandbox_sync_guard_start,
)

router = APIRouter(prefix="/plaid", tags=["plaid"])
logger = logging.getLogger(__name__)


@router.post("/link-token", response_model=LinkTokenResponse)
def create_link_token() -> LinkTokenResponse:
    try:
        data = PlaidService().create_link_token(client_user_id="gunjan")
    except PlaidConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PlaidRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return LinkTokenResponse(**data)


@router.post("/exchange-public-token", response_model=PublicTokenExchangeResponse)
def exchange_public_token(
    payload: PublicTokenExchangeRequest, db: DbSession
) -> PublicTokenExchangeResponse:
    try:
        plaid_data = PlaidService().exchange_public_token(payload.public_token)
    except PlaidConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PlaidRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    item_id = plaid_data["item_id"]
    access_token_encrypted = encrypt_secret(plaid_data["access_token"])
    item = db.execute(select(PlaidItem).where(PlaidItem.item_id == item_id)).scalar_one_or_none()
    if item is None:
        item = PlaidItem(
            item_id=item_id,
            access_token_encrypted=access_token_encrypted,
            institution_name=payload.institution_name,
        )
        db.add(item)
    else:
        item.access_token_encrypted = access_token_encrypted
        item.institution_name = payload.institution_name or item.institution_name
    db.commit()
    db.refresh(item)
    try:
        TransactionService(db).sync_item(item)
    except Exception as exc:
        log_event(
            logger,
            "plaid_sync_failed",
            level=logging.WARNING,
            plaid_item_db_id=item.id,
            source="item_exchange",
            reason="unexpected_error",
            error_type=type(exc).__name__,
        )
    return PublicTokenExchangeResponse(item_id=item.item_id, plaid_item_db_id=item.id)


@router.post("/sync")
def sync_all_items(db: DbSession) -> dict:
    try:
        return TransactionService(db).sync_all_items()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/webhook", response_model=WebhookAck)
async def plaid_webhook(
    request: Request, background_tasks: BackgroundTasks, db: DbSession
) -> WebhookAck:
    raw_body = await request.body()
    _verify_plaid_webhook_if_enabled(request, raw_body, db)
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid Plaid webhook JSON") from exc

    webhook_type = payload.get("webhook_type")
    webhook_code = payload.get("webhook_code")
    item_id = payload.get("item_id")
    maybe_log_sandbox_webhook(payload)
    event = _create_plaid_webhook_event(
        db,
        webhook_type=str(webhook_type or "unknown"),
        webhook_code=str(webhook_code or "unknown"),
        plaid_item_id=str(item_id) if item_id else None,
        payload_hash=sha256(raw_body).hexdigest(),
    )
    log_event(
        logger,
        "plaid_webhook_received",
        webhook_type=webhook_type,
        webhook_code=webhook_code,
    )

    if webhook_type != "TRANSACTIONS" or webhook_code != "SYNC_UPDATES_AVAILABLE":
        log_event(
            logger,
            "plaid_webhook_ignored",
            webhook_type=webhook_type,
            webhook_code=webhook_code,
        )
        _mark_webhook_event_ignored(db, event)
        return WebhookAck(ok=True, message=f"Webhook ignored: {webhook_type}/{webhook_code}")

    if not item_id:
        _mark_webhook_event_failed(db, event, "missing_item_id")
        log_event(
            logger,
            "plaid_webhook_sync_failed",
            level=logging.WARNING,
            reason="missing_item_id",
        )
        return WebhookAck(ok=True, message="Webhook accepted, but item_id is missing.")

    item = db.execute(select(PlaidItem).where(PlaidItem.item_id == item_id)).scalar_one_or_none()
    if item is None:
        _mark_webhook_event_failed(db, event, "unknown_item_id")
        log_event(
            logger,
            "plaid_webhook_sync_failed",
            level=logging.WARNING,
            reason="unknown_item_id",
        )
        return WebhookAck(ok=True, message="Webhook accepted, but item is not linked in this app.")

    event.item_id = item.id
    event.processing_status = "queued"
    _safe_commit(db)

    background_tasks.add_task(_sync_item_by_db_id, item.id, event.id)
    return WebhookAck(ok=True, message="Queued transactions sync")


def _sync_item_by_db_id(item_db_id: int, webhook_event_id: int | None = None) -> None:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        event = db.get(PlaidWebhookEvent, webhook_event_id) if webhook_event_id else None
        item = db.get(PlaidItem, item_db_id)
        if item:
            if event:
                event.processing_status = "syncing"
                event.sync_started_at = utc_now()
                db.commit()
            skipped, sync_guard = sandbox_sync_guard_start(
                item.item_id,
                source_action="webhook_handler",
            )
            if skipped:
                if event:
                    event.processing_status = "ignored"
                    event.processed_at = utc_now()
                    db.commit()
                return
            if sync_guard:
                SandboxEventStore().append(
                    trace_id=sync_guard.trace_id,
                    event_type="plaid_transactions_sync_started",
                    status="started",
                    payload={
                        "source_action": "webhook_handler",
                        "item_id": sync_guard.plaid_item_id,
                        "source": "plaid_webhook",
                    },
                    plaid_item_id=sync_guard.plaid_item_id,
                )
            log_event(
                logger,
                "plaid_webhook_sync_started",
                plaid_item_db_id=item.id,
                webhook_event_id=webhook_event_id,
            )
            try:
                result = TransactionService(db).sync_item(item)
            finally:
                sandbox_sync_guard_finish(sync_guard)
            if sync_guard:
                SandboxEventStore().append(
                    trace_id=sync_guard.trace_id,
                    event_type="plaid_transactions_sync_completed",
                    status="succeeded",
                    payload={
                        "source_action": "webhook_handler",
                        "item_id": sync_guard.plaid_item_id,
                        "source": "plaid_webhook",
                        "added_count": result.get("added", 0),
                        "modified_count": result.get("modified", 0),
                        "removed_count": result.get("removed", 0),
                    },
                    plaid_item_id=sync_guard.plaid_item_id,
                )
            if event:
                event.processing_status = "processed"
                event.sync_completed_at = utc_now()
                event.processed_at = event.sync_completed_at
                db.commit()
            log_event(
                logger,
                "plaid_webhook_sync_completed",
                plaid_item_db_id=item.id,
                webhook_event_id=webhook_event_id,
                added=result.get("added", 0),
                modified=result.get("modified", 0),
                removed=result.get("removed", 0),
                notification_eligible=result.get("notification_eligible", 0),
                notification_sent=result.get("notification_sent", 0),
                notification_skipped=result.get("notification_skipped", 0),
            )
        else:
            if event:
                event.processing_status = "failed"
                event.error_message = "unknown_item_id"
                event.processed_at = utc_now()
                db.commit()
            log_event(
                logger,
                "plaid_webhook_sync_failed",
                level=logging.WARNING,
                plaid_item_db_id=item_db_id,
                webhook_event_id=webhook_event_id,
                reason="unknown_item_id",
            )
    except Exception as exc:
        if webhook_event_id:
            event = db.get(PlaidWebhookEvent, webhook_event_id)
            if event:
                event.processing_status = "failed"
                event.error_message = type(exc).__name__
                event.processed_at = utc_now()
                db.commit()
        log_event(
            logger,
            "plaid_webhook_sync_failed",
            level=logging.WARNING,
            plaid_item_db_id=item_db_id,
            webhook_event_id=webhook_event_id,
            reason="unexpected_error",
            error_type=type(exc).__name__,
        )
    finally:
        db.close()


def _create_plaid_webhook_event(
    db: DbSession,
    *,
    webhook_type: str,
    webhook_code: str,
    plaid_item_id: str | None,
    payload_hash: str,
) -> PlaidWebhookEvent:
    event = PlaidWebhookEvent(
        webhook_type=webhook_type,
        webhook_code=webhook_code,
        plaid_item_id=plaid_item_id,
        payload_hash=payload_hash,
    )
    if not all(hasattr(db, attr) for attr in ("add", "commit", "refresh")):
        return event
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def _mark_webhook_event_ignored(db: DbSession, event: PlaidWebhookEvent) -> None:
    event.processing_status = "ignored"
    event.processed_at = utc_now()
    _safe_commit(db)


def _mark_webhook_event_failed(
    db: DbSession,
    event: PlaidWebhookEvent,
    error_message: str,
) -> None:
    event.processing_status = "failed"
    event.error_message = error_message
    event.processed_at = utc_now()
    _safe_commit(db)


def _mark_webhook_event_verification_failed(
    db: DbSession,
    event: PlaidWebhookEvent,
    error_message: str,
) -> None:
    event.processing_status = "verification_failed"
    event.error_message = error_message
    event.processed_at = utc_now()
    _safe_commit(db)


def _safe_commit(db: DbSession) -> None:
    if hasattr(db, "commit"):
        db.commit()


def _verify_plaid_webhook_if_enabled(request: Request, raw_body: bytes, db: DbSession) -> None:
    settings = get_settings()
    if not settings.plaid_webhook_verification_required:
        return

    metadata = _safe_webhook_metadata(raw_body)
    verification_header = request.headers.get("Plaid-Verification", "")
    verification_metadata = {
        "plaid_env": settings.plaid_env,
        "verification_required": True,
        "header_present": bool(verification_header),
        "kid_present": _plaid_verification_kid_present(verification_header),
    }
    _log_plaid_webhook_verification_event(
        "plaid_webhook_verification_started",
        status="started",
        metadata=metadata,
        payload=verification_metadata,
    )
    if not verification_header:
        _handle_plaid_webhook_verification_failure(
            db,
            raw_body,
            reason="missing_plaid_verification_header",
            settings=settings,
            header_present=verification_metadata["header_present"],
            kid_present=verification_metadata["kid_present"],
        )
        return

    try:
        PlaidService(settings=settings).verify_webhook_signature(
            raw_body=raw_body,
            verification_header=verification_header,
        )
        _log_plaid_webhook_verification_event(
            "plaid_webhook_verification_succeeded",
            status="succeeded",
            metadata=metadata,
            payload=verification_metadata,
        )
        log_event(logger, "plaid_webhook_verified")
    except PlaidWebhookVerificationError as exc:
        _handle_plaid_webhook_verification_failure(
            db,
            raw_body,
            reason=exc.reason,
            settings=settings,
            header_present=verification_metadata["header_present"],
            kid_present=verification_metadata["kid_present"],
        )
        return
    except (PlaidConfigurationError, PlaidRequestError) as exc:
        _handle_plaid_webhook_verification_failure(
            db,
            raw_body,
            reason="webhook_key_fetch_failed",
            settings=settings,
            error_type=type(exc).__name__,
            header_present=verification_metadata["header_present"],
            kid_present=verification_metadata["kid_present"],
        )
        return


def _handle_plaid_webhook_verification_failure(
    db: DbSession,
    raw_body: bytes,
    *,
    reason: str,
    settings,
    error_type: str | None = None,
    header_present: bool | None = None,
    kid_present: bool | None = None,
) -> None:
    metadata = _safe_webhook_metadata(raw_body)
    verification_payload = {
        "plaid_env": settings.plaid_env,
        "verification_required": True,
        "header_present": bool(header_present),
        "kid_present": bool(kid_present),
        "reason": reason,
    }
    if _allow_unverified_plaid_webhook_for_local_test(settings, reason):
        _log_plaid_webhook_verification_event(
            "plaid_webhook_verification_bypassed_for_local_test",
            status="warning",
            metadata=metadata,
            payload=verification_payload,
        )
        return

    _log_plaid_webhook_verification_event(
        "plaid_webhook_verification_failed",
        status="failed",
        metadata=metadata,
        payload=verification_payload,
    )
    event = _create_plaid_webhook_event(
        db,
        webhook_type=metadata["webhook_type"],
        webhook_code=metadata["webhook_code"],
        plaid_item_id=metadata["item_id"],
        payload_hash=sha256(raw_body).hexdigest(),
    )
    _mark_webhook_event_verification_failed(db, event, reason)
    log_kwargs = {
        "reason": reason,
        "webhook_type": metadata["webhook_type"],
        "webhook_code": metadata["webhook_code"],
        "plaid_item_id": metadata["item_id"],
    }
    if error_type:
        log_kwargs["error_type"] = error_type
    log_event(
        logger,
        "plaid_webhook_verification_failed",
        level=logging.WARNING,
        **log_kwargs,
    )
    raise HTTPException(status_code=403, detail="Plaid webhook verification failed")


def _allow_unverified_plaid_webhook_for_local_test(settings, verification_reason: str) -> bool:
    if not settings.allow_plaid_webhook_verification_bypass_for_local_test:
        if settings.allow_unverified_plaid_webhooks_for_local_test:
            log_event(
                logger,
                "plaid_webhook_verification_bypass_denied",
                level=logging.WARNING,
                reason="local_test_bypass_requested_outside_local_environment",
                plaid_env=settings.plaid_env,
                environment=settings.environment,
                verification_reason=verification_reason,
            )
        return False
    log_event(
        logger,
        "plaid_webhook_verification_bypassed_for_local_test",
        level=logging.WARNING,
        reason="verification_bypassed_for_local_test",
        plaid_env=settings.plaid_env,
        environment=settings.environment,
        verification_reason=verification_reason,
    )
    return True


def _safe_webhook_metadata(raw_body: bytes) -> dict[str, str | None]:
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return {
            "webhook_type": "unknown",
            "webhook_code": "unknown",
            "item_id": None,
        }
    return {
        "webhook_type": str(payload.get("webhook_type") or "unknown"),
        "webhook_code": str(payload.get("webhook_code") or "unknown"),
        "item_id": str(payload.get("item_id")) if payload.get("item_id") else None,
    }


def _plaid_verification_kid_present(verification_header: str) -> bool:
    if not verification_header:
        return False
    try:
        from jose import jwt

        return bool(jwt.get_unverified_header(verification_header).get("kid"))
    except Exception:
        return False


def _log_plaid_webhook_verification_event(
    event_type: str,
    *,
    status: str,
    metadata: dict[str, str | None],
    payload: dict[str, object],
) -> None:
    safe_payload = {
        "plaid_env": payload.get("plaid_env"),
        "verification_required": payload.get("verification_required"),
        "header_present": payload.get("header_present"),
        "kid_present": payload.get("kid_present"),
    }
    if payload.get("reason"):
        safe_payload["reason"] = payload["reason"]
    log_event(
        logger,
        event_type,
        webhook_type=metadata["webhook_type"],
        webhook_code=metadata["webhook_code"],
        plaid_item_id=metadata["item_id"],
        **safe_payload,
    )
    maybe_log_sandbox_webhook_verification_event(
        event_type=event_type,
        status=status,
        webhook_type=metadata["webhook_type"],
        webhook_code=metadata["webhook_code"],
        item_id=metadata["item_id"],
        payload=safe_payload,
    )

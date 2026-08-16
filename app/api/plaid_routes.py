from __future__ import annotations

import json
import logging
from hashlib import sha256

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.api.deps import CurrentUser, CurrentWorkspaceOwner, DbSession
from app.config import get_settings
from app.logging_config import log_event
from app.models import (
    PlaidItem,
    PlaidWebhookEvent,
    User,
    Workspace,
    WorkspaceMembership,
    utc_now,
)
from app.rate_limit import rate_limiter
from app.schemas import (
    LinkTokenResponse,
    PublicTokenExchangeRequest,
    PublicTokenExchangeResponse,
    WebhookAck,
)
from app.security import encrypt_secret
from app.services.managed_auth_service import record_audit
from app.services.outbox_service import enqueue_outbox_event
from app.services.plaid_service import (
    PLAID_WEBHOOK_MAX_KEY_ID_CHARS,
    PLAID_WEBHOOK_MAX_VERIFICATION_HEADER_BYTES,
    PlaidConfigurationError,
    PlaidRequestError,
    PlaidService,
    PlaidWebhookVerificationError,
)
from app.services.transaction_service import TransactionService
from app.tenant_routing import route_plaid_item
from sandbox.backend.event_store import SandboxEventStore
from sandbox.backend.webhook_hooks import (
    maybe_log_sandbox_webhook,
    maybe_log_sandbox_webhook_verification_event,
    sandbox_sync_guard_finish,
    sandbox_sync_guard_start,
)

router = APIRouter(prefix="/plaid", tags=["plaid"])
logger = logging.getLogger(__name__)
PLAID_WEBHOOK_MAX_BODY_BYTES = 1024 * 1024
PLAID_WEBHOOK_RATE_LIMIT = 120
PLAID_WEBHOOK_RATE_WINDOW_SECONDS = 60


def _plaid_membership_active(db, item: PlaidItem) -> bool:
    if not hasattr(db, "scalar") or not hasattr(db, "get_bind"):
        return True
    statement = (
        select(WorkspaceMembership.id)
        .join(User, User.id == WorkspaceMembership.user_id)
        .where(
            WorkspaceMembership.workspace_id == item.workspace_id,
            User.status == "active",
        )
    )
    if item.owner_user_id is not None:
        statement = statement.where(WorkspaceMembership.user_id == item.owner_user_id)
    if db.scalar(statement) is not None:
        return True
    bind = db.get_bind()
    return bind.dialect.name != "postgresql" and db.get(Workspace, item.workspace_id) is None


@router.post("/link-token", response_model=LinkTokenResponse)
def create_link_token(
    request: Request,
    _user: CurrentUser,
) -> LinkTokenResponse:
    try:
        user_id = getattr(request.state, "user_id", "legacy")
        data = PlaidService().create_link_token(client_user_id=f"expenseops-user-{user_id}")
    except PlaidConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PlaidRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return LinkTokenResponse(**data)


@router.post("/exchange-public-token", response_model=PublicTokenExchangeResponse)
def exchange_public_token(
    payload: PublicTokenExchangeRequest,
    request: Request,
    db: DbSession,
    user: CurrentUser,
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
            owner_user_id=user.id,
            ownership_verified_at=utc_now(),
            access_token_encrypted=access_token_encrypted,
            institution_name=payload.institution_name,
        )
        db.add(item)
    else:
        if item.owner_user_id is not None and item.owner_user_id != user.id:
            raise HTTPException(
                status_code=409,
                detail="This bank connection already belongs to another workspace member",
            )
        item.owner_user_id = user.id
        item.ownership_verified_at = utc_now()
        item.access_token_encrypted = access_token_encrypted
        item.enabled = True
        item.institution_name = payload.institution_name or item.institution_name
    db.flush()
    workspace_id = db.info.get("workspace_id") or getattr(request.state, "workspace_id", 1)
    user_id = db.info.get("user_id") or getattr(request.state, "user_id", None)
    record_audit(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        event_type="plaid_connected",
        resource_type="plaid_item",
        resource_id=str(item.id) if item.id else None,
    )
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
def sync_all_items(
    db: DbSession,
    _user: CurrentUser,
    _owner: CurrentWorkspaceOwner,
) -> dict:
    try:
        return TransactionService(db).sync_all_items()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/webhook", response_model=WebhookAck)
async def plaid_webhook(
    request: Request, background_tasks: BackgroundTasks, db: DbSession
) -> WebhookAck:
    from app.tenancy import clear_session_tenant

    await run_in_threadpool(
        rate_limiter.check,
        _plaid_webhook_rate_limit_key(request),
        limit=PLAID_WEBHOOK_RATE_LIMIT,
        window_seconds=PLAID_WEBHOOK_RATE_WINDOW_SECONDS,
    )
    raw_body = await _read_bounded_plaid_webhook_body(request)
    delivery_fingerprint = await _verify_plaid_webhook_if_enabled(request, raw_body)
    # This verified provider endpoint must resolve an external item identifier
    # before its workspace is known. The narrow router returns only internal
    # IDs and leaves the session scoped to the matched workspace.
    clear_session_tenant(db)
    webhook_type, webhook_code, item_id = _parse_plaid_webhook_payload(raw_body)
    is_sync_webhook = webhook_type == "TRANSACTIONS" and webhook_code == "SYNC_UPDATES_AVAILABLE"
    item_route = (
        route_plaid_item(db, item_id)
        if item_id and is_sync_webhook and hasattr(db, "get_bind")
        else None
    )
    maybe_log_sandbox_webhook(
        {
            "webhook_type": webhook_type,
            "webhook_code": webhook_code,
            "item_id": item_id,
        }
    )
    event, replayed = _accept_plaid_webhook_event(
        db,
        webhook_type=webhook_type,
        webhook_code=webhook_code,
        plaid_item_id=item_id,
        payload_hash=sha256(raw_body).hexdigest(),
        delivery_fingerprint=delivery_fingerprint,
    )
    log_event(
        logger,
        "plaid_webhook_received",
        webhook_type=webhook_type,
        webhook_code=webhook_code,
    )
    if replayed:
        log_event(
            logger,
            "plaid_webhook_replay_ignored",
            webhook_type=webhook_type,
            webhook_code=webhook_code,
            webhook_event_id=event.id,
        )
        return WebhookAck(ok=True, message="Webhook already accepted")

    if not is_sync_webhook:
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

    if item_route is not None:
        item = db.get(PlaidItem, item_route.plaid_item_id)
    elif hasattr(db, "execute") and not hasattr(db, "get_bind"):
        # Lightweight test doubles exercise verification and acknowledgement
        # behavior without a SQLAlchemy Session. Production sessions always
        # use the narrow tenant router above.
        item = db.execute(
            select(PlaidItem).where(PlaidItem.item_id == item_id)
        ).scalar_one_or_none()
    else:
        item = None
    if item is not None and item.enabled is False:
        item = None
    if item is not None and not _plaid_membership_active(db, item):
        item = None
    if item is None:
        _mark_webhook_event_failed(db, event, "unknown_item_id")
        log_event(
            logger,
            "plaid_webhook_sync_failed",
            level=logging.WARNING,
            reason="unknown_item_id",
        )
        return WebhookAck(ok=True, message="Webhook accepted, but item is not linked in this app.")

    from app.tenancy import set_trusted_workspace

    set_trusted_workspace(db, item.workspace_id)

    event.item_id = item.id
    event.processing_status = "queued"
    if get_settings().is_production_mode and event.id is not None:
        enqueue_outbox_event(
            db,
            workspace_id=item.workspace_id,
            event_type="plaid.sync_item",
            aggregate_type="plaid_item",
            aggregate_id=item.id,
            dedupe_key=f"plaid-webhook:{event.id}",
            payload={"item_id": item.id, "webhook_event_id": event.id},
        )
        _safe_commit(db)
    else:
        _safe_commit(db)
        if item.workspace_id is None:
            # Only possible for legacy/pre-migration test data. Persisted
            # tenant-owned Plaid items always carry a workspace.
            background_tasks.add_task(_sync_item_by_db_id, item.id, event.id)
        else:
            background_tasks.add_task(
                _sync_item_by_db_id,
                item.id,
                event.id,
                workspace_id=item.workspace_id,
            )
    return WebhookAck(ok=True, message="Queued transactions sync")


def _sync_item_by_db_id(
    item_db_id: int,
    webhook_event_id: int | None = None,
    *,
    workspace_id: int | None = None,
) -> None:
    from app.db import SessionLocal
    from app.tenancy import reset_active_user, set_active_user, set_trusted_workspace

    db = SessionLocal()
    try:
        # Durable workers and background tasks open a fresh session. Establish
        # the trusted workspace before the first tenant-scoped lookup so a
        # restricted PostgreSQL role can see the intended Plaid item under RLS.
        if workspace_id is not None:
            set_trusted_workspace(db, workspace_id)
        item = db.get(PlaidItem, item_db_id)
        if item:
            if workspace_id is not None and item.workspace_id != workspace_id:
                item = None
        if item:
            set_trusted_workspace(db, item.workspace_id)
        event = db.get(PlaidWebhookEvent, webhook_event_id) if webhook_event_id else None
        if item:
            if item.owner_user_id is None:
                raise RuntimeError("plaid_item_owner_missing")
            owner_user_id = int(item.owner_user_id)
            if not _plaid_membership_active(db, item):
                raise RuntimeError("plaid_item_owner_not_member")
            session_info = getattr(db, "info", None)
            missing_db_user = object()
            previous_db_user_id = (
                session_info.get("user_id", missing_db_user)
                if session_info is not None
                else missing_db_user
            )
            owner_token = set_active_user(owner_user_id)
            try:
                if session_info is not None:
                    session_info["user_id"] = owner_user_id
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
            finally:
                if session_info is not None:
                    if previous_db_user_id is missing_db_user:
                        session_info.pop("user_id", None)
                    else:
                        session_info["user_id"] = previous_db_user_id
                reset_active_user(owner_token)
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
    delivery_fingerprint: str | None = None,
) -> PlaidWebhookEvent:
    linked_item = None
    if plaid_item_id and hasattr(db, "execute"):
        linked_item = db.execute(
            select(PlaidItem).where(PlaidItem.item_id == plaid_item_id)
        ).scalar_one_or_none()
    event = PlaidWebhookEvent(
        webhook_type=webhook_type,
        webhook_code=webhook_code,
        plaid_item_id=plaid_item_id,
        payload_hash=payload_hash,
        delivery_fingerprint=delivery_fingerprint,
        workspace_id=linked_item.workspace_id if linked_item else None,
    )
    if not all(hasattr(db, attr) for attr in ("add", "flush")):
        return event
    db.add(event)
    db.flush()
    return event


def _accept_plaid_webhook_event(
    db: DbSession,
    *,
    webhook_type: str,
    webhook_code: str,
    plaid_item_id: str | None,
    payload_hash: str,
    delivery_fingerprint: str | None,
) -> tuple[PlaidWebhookEvent, bool]:
    if not delivery_fingerprint or not all(
        hasattr(db, attr) for attr in ("execute", "begin_nested")
    ):
        return (
            _create_plaid_webhook_event(
                db,
                webhook_type=webhook_type,
                webhook_code=webhook_code,
                plaid_item_id=plaid_item_id,
                payload_hash=payload_hash,
                delivery_fingerprint=delivery_fingerprint,
            ),
            False,
        )

    existing = db.execute(
        select(PlaidWebhookEvent).where(
            PlaidWebhookEvent.delivery_fingerprint == delivery_fingerprint
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, True

    try:
        with db.begin_nested():
            event = _create_plaid_webhook_event(
                db,
                webhook_type=webhook_type,
                webhook_code=webhook_code,
                plaid_item_id=plaid_item_id,
                payload_hash=payload_hash,
                delivery_fingerprint=delivery_fingerprint,
            )
    except IntegrityError:
        existing = db.execute(
            select(PlaidWebhookEvent).where(
                PlaidWebhookEvent.delivery_fingerprint == delivery_fingerprint
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        return existing, True
    return event, False


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


def _safe_commit(db: DbSession) -> None:
    if hasattr(db, "commit"):
        db.commit()


async def _read_bounded_plaid_webhook_body(request: Request) -> bytes:
    content_length = request.headers.get("Content-Length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
        if declared_length < 0:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if declared_length > PLAID_WEBHOOK_MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Plaid webhook payload too large")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > PLAID_WEBHOOK_MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Plaid webhook payload too large")
        body.extend(chunk)
    return bytes(body)


def _plaid_webhook_rate_limit_key(request: Request) -> str:
    client_host = request.client.host if request.client else "unknown"
    client_fingerprint = sha256(client_host.encode("utf-8", errors="replace")).hexdigest()[:32]
    return f"plaid-webhook:{client_fingerprint}"


def _parse_plaid_webhook_payload(raw_body: bytes) -> tuple[str, str, str | None]:
    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Plaid webhook JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid Plaid webhook JSON")

    webhook_type = _validated_plaid_webhook_string(
        payload,
        "webhook_type",
        max_length=64,
        required=True,
    )
    webhook_code = _validated_plaid_webhook_string(
        payload,
        "webhook_code",
        max_length=128,
        required=True,
    )
    item_id = _validated_plaid_webhook_string(
        payload,
        "item_id",
        max_length=128,
        required=False,
    )
    assert webhook_type is not None
    assert webhook_code is not None
    return webhook_type, webhook_code, item_id


def _validated_plaid_webhook_string(
    payload: dict[str, object],
    field_name: str,
    *,
    max_length: int,
    required: bool,
) -> str | None:
    value = payload.get(field_name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or len(value) > max_length or "\x00" in value:
        raise HTTPException(status_code=400, detail="Invalid Plaid webhook fields")
    return value


async def _verify_plaid_webhook_if_enabled(
    request: Request,
    raw_body: bytes,
) -> str | None:
    settings = get_settings()
    if not settings.plaid_webhook_verification_required:
        return None

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
            metadata=metadata,
            reason="missing_plaid_verification_header",
            settings=settings,
            header_present=verification_metadata["header_present"],
            kid_present=verification_metadata["kid_present"],
        )
        return None

    try:
        service = PlaidService(settings=settings)
        await run_in_threadpool(
            service.verify_webhook_signature,
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
        return sha256(verification_header.encode("utf-8") + b"\x00" + raw_body).hexdigest()
    except PlaidWebhookVerificationError as exc:
        _handle_plaid_webhook_verification_failure(
            metadata=metadata,
            reason=exc.reason,
            settings=settings,
            header_present=verification_metadata["header_present"],
            kid_present=verification_metadata["kid_present"],
        )
        return None
    except (PlaidConfigurationError, PlaidRequestError) as exc:
        _handle_plaid_webhook_verification_failure(
            metadata=metadata,
            reason="webhook_key_fetch_failed",
            settings=settings,
            error_type=type(exc).__name__,
            header_present=verification_metadata["header_present"],
            kid_present=verification_metadata["kid_present"],
        )
        return None


def _handle_plaid_webhook_verification_failure(
    *,
    metadata: dict[str, str | None],
    reason: str,
    settings,
    error_type: str | None = None,
    header_present: bool | None = None,
    kid_present: bool | None = None,
) -> None:
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
    log_kwargs = {
        "reason": reason,
        "webhook_type": metadata["webhook_type"],
        "webhook_code": metadata["webhook_code"],
        "plaid_item_id_present": bool(metadata["item_id"]),
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
    except (ValueError, UnicodeDecodeError, RecursionError):
        payload = None
    if not isinstance(payload, dict):
        return {
            "webhook_type": "unknown",
            "webhook_code": "unknown",
            "item_id": None,
        }
    return {
        "webhook_type": _bounded_webhook_metadata_value(
            payload.get("webhook_type"),
            default="unknown",
            max_length=64,
        ),
        "webhook_code": _bounded_webhook_metadata_value(
            payload.get("webhook_code"),
            default="unknown",
            max_length=128,
        ),
        "item_id": _bounded_webhook_metadata_value(
            payload.get("item_id"),
            default=None,
            max_length=128,
        ),
    }


def _bounded_webhook_metadata_value(
    value: object,
    *,
    default: str | None,
    max_length: int,
) -> str | None:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return default
    text = str(value).strip()
    return text[:max_length] if text else default


def _plaid_verification_kid_present(verification_header: str) -> bool:
    if not verification_header:
        return False
    if len(verification_header.encode("utf-8")) > PLAID_WEBHOOK_MAX_VERIFICATION_HEADER_BYTES:
        return False
    try:
        import jwt

        key_id = jwt.get_unverified_header(verification_header).get("kid")
        return bool(
            isinstance(key_id, str)
            and key_id.isascii()
            and len(key_id) <= PLAID_WEBHOOK_MAX_KEY_ID_CHARS
            and all(character.isalnum() or character in "-_" for character in key_id)
        )
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
        plaid_item_id_present=bool(metadata["item_id"]),
        **safe_payload,
    )
    maybe_log_sandbox_webhook_verification_event(
        event_type=event_type,
        status=status,
        webhook_type=metadata["webhook_type"],
        webhook_code=metadata["webhook_code"],
        item_id=None,
        payload=safe_payload,
    )

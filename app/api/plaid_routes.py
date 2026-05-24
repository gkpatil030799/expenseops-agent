from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sqlalchemy import select

from app.api.deps import DbSession
from app.config import get_settings
from app.logging_config import log_event
from app.models import PlaidItem
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
    _verify_plaid_webhook_if_enabled(request, raw_body)
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid Plaid webhook JSON") from exc

    webhook_type = payload.get("webhook_type")
    webhook_code = payload.get("webhook_code")
    item_id = payload.get("item_id")
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
        return WebhookAck(ok=True, message=f"Webhook ignored: {webhook_type}/{webhook_code}")

    if not item_id:
        log_event(logger, "plaid_sync_failed", level=logging.WARNING, reason="missing_item_id")
        return WebhookAck(ok=True, message="Webhook accepted, but item_id is missing.")

    item = db.execute(select(PlaidItem).where(PlaidItem.item_id == item_id)).scalar_one_or_none()
    if item is None:
        log_event(logger, "plaid_sync_failed", level=logging.WARNING, reason="unknown_item_id")
        return WebhookAck(ok=True, message="Webhook accepted, but item is not linked in this app.")

    background_tasks.add_task(_sync_item_by_db_id, item.id)
    return WebhookAck(ok=True, message="Queued transactions sync")


def _sync_item_by_db_id(item_db_id: int) -> None:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        item = db.get(PlaidItem, item_db_id)
        if item:
            TransactionService(db).sync_item(item)
        else:
            log_event(
                logger,
                "plaid_sync_failed",
                level=logging.WARNING,
                plaid_item_db_id=item_db_id,
                source="webhook",
                reason="unknown_item_id",
            )
    except Exception as exc:
        log_event(
            logger,
            "plaid_sync_failed",
            level=logging.WARNING,
            plaid_item_db_id=item_db_id,
            source="webhook",
            reason="unexpected_error",
            error_type=type(exc).__name__,
        )
    finally:
        db.close()


def _verify_plaid_webhook_if_enabled(request: Request, raw_body: bytes) -> None:
    settings = get_settings()
    if not settings.plaid_verify_webhooks:
        return

    verification_header = request.headers.get("Plaid-Verification", "")
    if not verification_header:
        raise HTTPException(status_code=401, detail="Missing Plaid webhook verification")

    try:
        PlaidService(settings=settings).verify_webhook_signature(
            raw_body=raw_body,
            verification_header=verification_header,
        )
        log_event(logger, "plaid_webhook_verified")
    except PlaidWebhookVerificationError as exc:
        log_event(
            logger,
            "plaid_webhook_verification_failed",
            level=logging.WARNING,
            reason="plaid_verification_failed",
            verification_reason=exc.reason,
        )
        raise HTTPException(status_code=401, detail="Invalid Plaid webhook verification") from exc
    except (PlaidConfigurationError, PlaidRequestError) as exc:
        log_event(
            logger,
            "plaid_webhook_verification_failed",
            level=logging.WARNING,
            reason="plaid_verification_failed",
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=403, detail="Plaid webhook verification failed") from exc

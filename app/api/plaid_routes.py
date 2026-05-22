from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sqlalchemy import select

from app.api.deps import DbSession
from app.models import PlaidItem
from app.schemas import (
    LinkTokenResponse,
    PublicTokenExchangeRequest,
    PublicTokenExchangeResponse,
    WebhookAck,
)
from app.security import encrypt_secret
from app.services.plaid_service import PlaidConfigurationError, PlaidRequestError, PlaidService
from app.services.transaction_service import TransactionService

router = APIRouter(prefix="/plaid", tags=["plaid"])


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
    payload = await request.json()
    webhook_type = payload.get("webhook_type")
    webhook_code = payload.get("webhook_code")
    item_id = payload.get("item_id")

    # For local development this receiver is intentionally lightweight. For production,
    # enable Plaid JWT webhook verification before calling TransactionService.
    if webhook_type == "TRANSACTIONS" and item_id:
        item = db.execute(
            select(PlaidItem).where(PlaidItem.item_id == item_id)
        ).scalar_one_or_none()
        if item:
            background_tasks.add_task(_sync_item_by_db_id, item.id)
            return WebhookAck(ok=True, message=f"Queued sync for {webhook_code}")
        return WebhookAck(ok=True, message="Webhook accepted, but item is not linked in this app.")

    return WebhookAck(ok=True, message=f"Webhook ignored: {webhook_type}/{webhook_code}")


def _sync_item_by_db_id(item_db_id: int) -> None:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        item = db.get(PlaidItem, item_db_id)
        if item:
            TransactionService(db).sync_item(item)
    finally:
        db.close()

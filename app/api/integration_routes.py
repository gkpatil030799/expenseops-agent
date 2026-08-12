from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, CurrentWorkspace, DbSession
from app.config import get_settings
from app.logging_config import log_event
from app.models import (
    GmailAccount,
    PlaidItem,
    SplitwiseIntegration,
    TelegramIdentity,
    TelegramLinkCode,
    utc_now,
)
from app.rate_limit import rate_limiter
from app.security import encrypt_secret
from app.services.managed_auth_service import record_audit, record_audit_once
from app.services.oauth_state_service import (
    OAuthStateError,
    consume_oauth_state,
    create_oauth_state,
)
from app.tenancy import hash_api_token

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
logger = logging.getLogger(__name__)


@router.get("")
def statuses(db: DbSession) -> dict:
    plaid = list(
        db.scalars(select(PlaidItem).where(PlaidItem.enabled.is_(True)).order_by(PlaidItem.id))
    )
    return {
        "gmail": {"connected": db.scalar(select(GmailAccount.id)) is not None},
        "plaid": {
            "connected": bool(plaid),
            "institutions": [
                {"id": item.id, "name": item.institution_name or "Connected bank"} for item in plaid
            ],
        },
        "telegram": {"connected": db.scalar(select(TelegramIdentity.id)) is not None},
        "splitwise": {"connected": db.scalar(select(SplitwiseIntegration.id)) is not None},
        "google_maps": {"connected": True, "managed_by": "application"},
        "openai": {"connected": True, "managed_by": "application"},
    }


@router.get("/onboarding")
def onboarding(db: DbSession, user: CurrentUser, workspace: CurrentWorkspace) -> dict:
    values = statuses(db)
    return {
        "account_created": True,
        "workspace_created": True,
        "user": {"display_name": user.display_name},
        "workspace": {"name": workspace.name},
        "integrations": values,
        "complete": any(
            values[key]["connected"] for key in ("gmail", "plaid", "telegram", "splitwise")
        ),
    }


@router.post("/gmail/connect")
def connect_gmail(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> dict:
    settings = get_settings()
    if not settings.gmail_client_id or not settings.gmail_client_secret:
        raise HTTPException(status_code=400, detail="Gmail OAuth is not configured")
    rate_limiter.check(f"gmail-oauth:{user.id}", limit=10, window_seconds=600)
    record_audit(
        db,
        workspace_id=workspace.id,
        user_id=user.id,
        event_type="gmail_connect_started",
        resource_type="gmail_account",
    )
    state = create_oauth_state(
        db,
        provider="gmail",
        workspace_id=workspace.id,
        user_id=user.id,
        redirect_after="/?workspace=settings",
    )
    redirect_uri = f"{settings.app_public_url.rstrip('/')}/api/integrations/gmail/callback"
    query = urlencode(
        {
            "client_id": settings.gmail_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email https://www.googleapis.com/auth/gmail.readonly",
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    return {"authorization_url": f"https://accounts.google.com/o/oauth2/v2/auth?{query}"}


@router.get("/gmail/callback")
def gmail_callback(
    code: str,
    state: str,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> RedirectResponse:
    settings = get_settings()
    try:
        consume_oauth_state(
            db,
            state,
            provider="gmail",
            user_id=user.id,
            workspace_id=workspace.id,
        )
        redirect_uri = f"{settings.app_public_url.rstrip('/')}/api/integrations/gmail/callback"
        response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.gmail_client_id,
                "client_secret": settings.gmail_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        response.raise_for_status()
        token_data = response.json()
        refresh_token = str(token_data.get("refresh_token") or "")
        if not refresh_token:
            raise ValueError("Gmail did not return a refresh token")
        account = db.scalar(select(GmailAccount).where(GmailAccount.workspace_id == workspace.id))
        if account is None:
            account = GmailAccount(
                user_id=user.id,
                google_user_id="me",
                refresh_token_encrypted=encrypt_secret(refresh_token),
            )
            db.add(account)
        else:
            account.user_id = user.id
            account.refresh_token_encrypted = encrypt_secret(refresh_token)
            account.enabled = True
            account.updated_at = utc_now()
        record_audit(
            db,
            workspace_id=workspace.id,
            user_id=user.id,
            event_type="gmail_connected",
            resource_type="gmail_account",
        )
        _record_onboarding_complete_if_ready(db, user.id, workspace.id)
        db.commit()
    except (OAuthStateError, httpx.HTTPError, ValueError) as exc:
        record_audit(
            db,
            workspace_id=workspace.id,
            user_id=user.id,
            event_type="gmail_connect_failed",
            resource_type="gmail_account",
            metadata={"error_class": type(exc).__name__},
        )
        db.commit()
        log_event(
            logger,
            "gmail_connect_failed",
            level=logging.WARNING,
            error_class=type(exc).__name__,
            request_id=request.headers.get("X-Request-ID"),
        )
        raise HTTPException(status_code=400, detail="Gmail connection failed") from exc
    return RedirectResponse(url="/?workspace=settings", status_code=303)


@router.delete("/gmail", status_code=204)
def disconnect_gmail(db: DbSession, user: CurrentUser, workspace: CurrentWorkspace) -> None:
    account = db.scalar(select(GmailAccount))
    if account is not None:
        db.delete(account)
    record_audit(db, workspace_id=workspace.id, user_id=user.id, event_type="gmail_disconnected")
    db.commit()


@router.post("/telegram/link-code")
def telegram_link_code(
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> dict:
    rate_limiter.check(f"telegram-link:{user.id}", limit=5, window_seconds=600)
    raw = secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:10].upper()
    code = TelegramLinkCode(
        user_id=user.id,
        code_hash=hash_api_token(raw),
        expires_at=utc_now() + timedelta(minutes=10),
    )
    db.add(code)
    record_audit(
        db,
        workspace_id=workspace.id,
        user_id=user.id,
        event_type="telegram_connect_started",
        resource_type="telegram_identity",
    )
    db.commit()
    return {"code": raw, "command": f"/connect {raw}", "expires_at": code.expires_at}


@router.delete("/telegram", status_code=204)
def disconnect_telegram(db: DbSession, user: CurrentUser, workspace: CurrentWorkspace) -> None:
    for identity in db.scalars(select(TelegramIdentity)):
        db.delete(identity)
    record_audit(db, workspace_id=workspace.id, user_id=user.id, event_type="telegram_disconnected")
    db.commit()


@router.delete("/plaid/{item_id}", status_code=204)
def disconnect_plaid(
    item_id: int,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> None:
    item = db.get(PlaidItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Plaid connection not found")
    item.enabled = False
    item.access_token_encrypted = None
    record_audit(
        db,
        workspace_id=workspace.id,
        user_id=user.id,
        event_type="plaid_disconnected",
        resource_type="plaid_item",
        resource_id=str(item.id),
    )
    db.commit()


@router.delete("/splitwise", status_code=204)
def disconnect_splitwise(db: DbSession, user: CurrentUser, workspace: CurrentWorkspace) -> None:
    integration = db.scalar(select(SplitwiseIntegration))
    if integration is not None:
        db.delete(integration)
    record_audit(
        db, workspace_id=workspace.id, user_id=user.id, event_type="splitwise_disconnected"
    )
    db.commit()


def _record_onboarding_complete_if_ready(db: DbSession, user_id: int, workspace_id: int) -> None:
    gmail_connected = db.scalar(select(GmailAccount.id)) is not None
    telegram_connected = db.scalar(select(TelegramIdentity.id)) is not None
    if gmail_connected and telegram_connected:
        record_audit_once(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            event_type="onboarding_completed",
        )

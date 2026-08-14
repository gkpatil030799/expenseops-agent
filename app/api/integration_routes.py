from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, CurrentWorkspace, CurrentWorkspaceOwner, DbSession
from app.config import get_settings
from app.logging_config import log_event
from app.models import (
    GmailAccount,
    PlaidItem,
    SplitwiseIntegration,
    TelegramIdentity,
    TelegramLinkCode,
    User,
    WorkspaceMembership,
    utc_now,
)
from app.rate_limit import rate_limiter
from app.security import encrypt_secret
from app.services.data_lifecycle_service import DataLifecycleService
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
def statuses(db: DbSession, user: CurrentUser) -> dict:
    settings = get_settings()
    gmail = db.scalar(select(GmailAccount).where(GmailAccount.enabled.is_(True)))
    telegram = db.scalar(
        select(TelegramIdentity).where(
            TelegramIdentity.user_id == user.id,
            TelegramIdentity.enabled.is_(True),
        )
    )
    splitwise = db.scalar(
        select(SplitwiseIntegration).where(
            SplitwiseIntegration.user_id == user.id,
            SplitwiseIntegration.enabled.is_(True),
        )
    )
    plaid = list(
        db.scalars(select(PlaidItem).where(PlaidItem.enabled.is_(True)).order_by(PlaidItem.id))
    )
    owner_ids = {item.owner_user_id for item in plaid if item.owner_user_id is not None}
    owners = {
        value.id: value
        for value in db.scalars(select(User).where(User.id.in_(owner_ids)))
    } if owner_ids else {}
    return {
        "gmail": {
            "connected": gmail is not None,
            "identity": gmail.google_user_id if gmail is not None else None,
        },
        "plaid": {
            "connected": bool(plaid),
            "institutions": [
                {
                    "id": item.id,
                    "name": item.institution_name or "Connected bank",
                    "owner_user_id": item.owner_user_id,
                    "owner_name": owners[item.owner_user_id].display_name
                    if item.owner_user_id in owners
                    else None,
                    "ownership_verified": item.ownership_verified_at is not None,
                    "is_mine": item.owner_user_id == user.id,
                }
                for item in plaid
            ],
        },
        "telegram": {
            "connected": telegram is not None,
            "telegram_user_id": telegram.telegram_user_id if telegram is not None else None,
            "chat_id": telegram.chat_id if telegram is not None else None,
        },
        "splitwise": {
            "connected": splitwise is not None,
            "available": settings.has_splitwise_oauth1_consumer,
            "identity": splitwise.display_name if splitwise is not None else None,
            "email": splitwise.email if splitwise is not None else None,
            "verified": splitwise.verified_at is not None if splitwise is not None else False,
        },
        "google_maps": {"connected": True, "managed_by": "application"},
        "openai": {"connected": True, "managed_by": "application"},
    }


@router.get("/onboarding")
def onboarding(db: DbSession, user: CurrentUser, workspace: CurrentWorkspace) -> dict:
    values = statuses(db, user)
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
    _owner: CurrentWorkspaceOwner,
) -> dict:
    settings = get_settings()
    if not settings.gmail_client_id or not settings.gmail_client_secret:
        raise HTTPException(status_code=400, detail="Gmail OAuth is not configured")
    consent = DataLifecycleService(db, settings).consent_status(
        workspace_id=workspace.id,
        user_id=user.id,
    )
    required = {"gmail_receipts"}
    if settings.promotions_enabled:
        required.add("gmail_promotions")
    if settings.receipt_parser_provider == "openai" or (
        settings.promotions_enabled and settings.promotions_llm_fallback_enabled
    ):
        required.add("model_receipt_processing")
    missing = sorted(purpose for purpose in required if not consent.get(purpose))
    if missing:
        raise HTTPException(
            status_code=409,
            detail="Review and accept the Gmail data-use choices before connecting.",
        )
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
    _owner: CurrentWorkspaceOwner,
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
        access_token = str(token_data.get("access_token") or "")
        if not refresh_token or not access_token:
            raise ValueError("Gmail did not return complete OAuth credentials")
        identity_response = httpx.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        identity_response.raise_for_status()
        identity_data = identity_response.json()
        google_identity = str(identity_data.get("email") or identity_data.get("sub") or "")
        if not google_identity or identity_data.get("email_verified") is False:
            raise ValueError("Gmail did not return a verified account identity")
        account = db.scalar(select(GmailAccount).where(GmailAccount.workspace_id == workspace.id))
        if account is None:
            account = GmailAccount(
                user_id=user.id,
                google_user_id=google_identity,
                refresh_token_encrypted=encrypt_secret(refresh_token),
            )
            db.add(account)
        else:
            account.user_id = user.id
            account.google_user_id = google_identity
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
def disconnect_gmail(
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    _owner: CurrentWorkspaceOwner,
) -> None:
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
    settings = get_settings()
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
    bot_username = settings.telegram_bot_username.strip().lstrip("@")
    connect_url = f"https://t.me/{bot_username}?start=connect_{raw}" if bot_username else None
    return {
        "code": raw,
        "command": f"/connect {raw}",
        "connect_url": connect_url,
        "expires_at": code.expires_at,
    }


@router.delete("/telegram", status_code=204)
def disconnect_telegram(db: DbSession, user: CurrentUser, workspace: CurrentWorkspace) -> None:
    for identity in db.scalars(select(TelegramIdentity).where(TelegramIdentity.user_id == user.id)):
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
    membership = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace.id,
            WorkspaceMembership.user_id == user.id,
        )
    )
    if item.owner_user_id != user.id and (membership is None or membership.role != "owner"):
        raise HTTPException(
            status_code=403,
            detail="Only the bank owner or workspace owner can disconnect it",
        )
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


@router.post("/plaid/{item_id}/claim")
def claim_legacy_plaid_item(
    item_id: int,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> dict:
    """Explicitly attribute a pre-ownership bank connection to the signed-in user."""
    item = db.get(PlaidItem, item_id)
    if item is None or not item.enabled:
        raise HTTPException(status_code=404, detail="Plaid connection not found")
    if item.owner_user_id is not None and item.owner_user_id != user.id:
        raise HTTPException(status_code=409, detail="This bank is already owned by another member")
    item.owner_user_id = user.id
    item.ownership_verified_at = utc_now()
    record_audit(
        db,
        workspace_id=workspace.id,
        user_id=user.id,
        event_type="plaid_ownership_confirmed",
        resource_type="plaid_item",
        resource_id=str(item.id),
    )
    db.commit()
    return {"ok": True, "owner_user_id": user.id}


@router.delete("/splitwise", status_code=204)
def disconnect_splitwise(
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> None:
    integration = db.scalar(
        select(SplitwiseIntegration).where(SplitwiseIntegration.user_id == user.id)
    )
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

from __future__ import annotations

import logging
import posixpath
from urllib.parse import unquote, urlencode, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import CurrentUser, CurrentWorkspace, DbSession
from app.config import get_settings
from app.logging_config import log_event
from app.models import AuthSession, utc_now
from app.rate_limit import rate_limiter
from app.services.managed_auth_service import (
    OIDCValidationError,
    OIDCVerifier,
    create_auth_session,
    provision_oidc_identity,
    record_audit,
)
from app.services.oauth_state_service import (
    OAuthStateError,
    consume_oauth_state,
    create_oauth_state,
)

router = APIRouter(prefix="/auth", tags=["authentication"])
logger = logging.getLogger(__name__)

_MAX_REDIRECT_AFTER_LENGTH = 500


def _safe_redirect_after(value: str | None) -> str:
    """Return a normalized same-origin redirect path, or the application root."""
    if not value or len(value) > _MAX_REDIRECT_AFTER_LENGTH or value != value.strip():
        return "/"
    candidate = value
    for _ in range(4):
        if any(ord(char) < 32 or ord(char) == 127 for char in candidate):
            return "/"
        if "\\" in candidate or not candidate.startswith("/") or candidate.startswith("//"):
            return "/"
        parsed = urlsplit(candidate)
        path = parsed.path
        if parsed.scheme or parsed.netloc or not path.startswith("/") or path.startswith("//"):
            return "/"
        normalized_path = posixpath.normpath(path)
        if path.endswith("/") and normalized_path != "/":
            normalized_path += "/"
        if normalized_path != path:
            return "/"
        decoded = unquote(candidate)
        if decoded == candidate:
            return value
        candidate = decoded
    return "/"


@router.get("/login")
def login(request: Request, db: DbSession, redirect_after: str = "/") -> RedirectResponse:
    settings = get_settings()
    safe_redirect = _safe_redirect_after(redirect_after)
    if settings.auth_mode != "oidc":
        return RedirectResponse(url=safe_redirect)
    rate_limiter.check(
        f"auth-login:{request.client.host if request.client else 'unknown'}",
        limit=20,
        window_seconds=60,
    )
    state = create_oauth_state(
        db,
        provider="oidc",
        workspace_id=None,
        user_id=None,
        redirect_after=safe_redirect,
    )
    discovery = OIDCVerifier(settings).discovery()
    query = urlencode(
        {
            "client_id": settings.oidc_client_id,
            "response_type": "code",
            "scope": settings.oidc_scopes,
            "redirect_uri": settings.oidc_redirect_uri,
            "state": state,
            "audience": settings.oidc_audience,
        }
    )
    return RedirectResponse(f"{discovery['authorization_endpoint']}?{query}")


@router.get("/callback")
def callback(request: Request, code: str, state: str, db: DbSession) -> RedirectResponse:
    settings = get_settings()
    rate_limiter.check(
        f"auth-callback:{request.client.host if request.client else 'unknown'}",
        limit=30,
        window_seconds=300,
    )
    try:
        stored, _payload = consume_oauth_state(db, state, provider="oidc")
        discovery = OIDCVerifier(settings).discovery()
        token_response = httpx.post(
            discovery["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.oidc_client_id,
                "client_secret": settings.oidc_client_secret,
                "redirect_uri": settings.oidc_redirect_uri,
            },
            timeout=15,
        )
        token_response.raise_for_status()
        token_payload = token_response.json()
        id_token = str(token_payload.get("id_token") or "")
        access_token = str(token_payload.get("access_token") or "")
        claims = OIDCVerifier(settings).validate(
            id_token,
            access_token=access_token or None,
        )
        user, workspace, _created = provision_oidc_identity(
            db,
            claims,
            provider=settings.oidc_issuer.rstrip("/"),
            settings=settings,
        )
        raw_session, _session = create_auth_session(db, user.id, workspace.id, settings)
    except (OAuthStateError, OIDCValidationError, httpx.HTTPError, SQLAlchemyError) as exc:
        db.rollback()
        log_event(
            logger,
            "oidc_login_failed",
            level=logging.WARNING,
            error_class=type(exc).__name__,
        )
        raise HTTPException(status_code=400, detail="Authentication failed") from exc
    response = RedirectResponse(_safe_redirect_after(stored.redirect_after))
    response.set_cookie(
        settings.auth_session_cookie_name,
        raw_session,
        httponly=True,
        secure=settings.is_production_mode,
        samesite="lax",
        max_age=settings.auth_session_hours * 3600,
    )
    return response


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> Response:
    session_id = getattr(request.state, "auth_session_id", None)
    if session_id is not None:
        session = db.get(AuthSession, session_id)
        if session is not None and session.user_id == user.id:
            session.revoked_at = utc_now()
    record_audit(
        db,
        workspace_id=workspace.id,
        user_id=user.id,
        event_type="logout",
    )
    db.commit()
    response = Response(status_code=204)
    response.delete_cookie(get_settings().auth_session_cookie_name)
    return response

from __future__ import annotations

from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.api.deps import CurrentUser, CurrentWorkspace, DbSession
from app.config import get_settings
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


@router.get("/login")
def login(request: Request, db: DbSession, redirect_after: str = "/") -> RedirectResponse:
    settings = get_settings()
    if settings.auth_mode != "oidc":
        return RedirectResponse(url=redirect_after if redirect_after.startswith("/") else "/")
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
        redirect_after=redirect_after if redirect_after.startswith("/") else "/",
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
        id_token = str(token_response.json().get("id_token") or "")
        claims = OIDCVerifier(settings).validate(id_token)
        user, workspace, _created = provision_oidc_identity(
            db,
            claims,
            provider=settings.oidc_issuer.rstrip("/"),
            settings=settings,
        )
        raw_session, _session = create_auth_session(db, user.id, workspace.id, settings)
    except (OAuthStateError, OIDCValidationError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=400, detail="Authentication failed") from exc
    response = RedirectResponse(stored.redirect_after or "/")
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

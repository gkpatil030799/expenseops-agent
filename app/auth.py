from __future__ import annotations

import base64
import secrets
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.db import SessionLocal
from app.logging_config import reset_tenant_log_context, set_tenant_log_context
from app.rate_limit import rate_limiter
from app.services.managed_auth_service import (
    OIDCValidationError,
    OIDCVerifier,
    provision_oidc_identity,
    resolve_auth_session,
)
from app.tenancy import (
    TenantContext,
    ensure_default_tenancy,
    reset_active_user,
    reset_active_workspace,
    resolve_user_token,
    set_active_user,
    set_active_workspace,
)

PUBLIC_EXACT_PATHS = {
    "/",
    "/health",
    "/readiness",
    "/telegram/webhook",
    "/plaid/webhook",
    "/auth/login",
    "/auth/callback",
    "/legal/privacy",
    "/legal/terms",
}


def install_dashboard_auth(app: FastAPI, settings: Settings) -> None:
    @app.middleware("http")
    async def dashboard_auth_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable],
    ):
        if _is_public_request(request):
            return await call_next(request)
        if settings.auth_mode == "oidc":
            if not _csrf_origin_allowed(request, settings):
                return JSONResponse(status_code=403, content={"detail": "Invalid request origin"})
            resolved = _managed_auth_context(request, settings)
            if resolved is not None:
                context, auth_session_id = resolved
                request.state.auth_session_id = auth_session_id
                return await _call_with_context(request, call_next, context)
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        if _auth_not_required(settings):
            return await _call_with_context(request, call_next, _default_context(settings))
        if _is_authorized(request, settings):
            return await _call_with_context(request, call_next, _default_context(settings))
        context = _token_context(request)
        if context is not None:
            return await _call_with_context(request, call_next, context)
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
            headers={"WWW-Authenticate": "Basic"},
        )


def _default_context(settings: Settings) -> TenantContext:
    try:
        with SessionLocal() as db:
            return ensure_default_tenancy(db)
    except OperationalError:
        if settings.environment == "production":
            raise
        # Some route unit tests intentionally run without application startup.
        # Never mutate an unmigrated developer database from authentication.
        return TenantContext(user_id=1, workspace_id=1)


def _token_context(request: Request) -> TenantContext | None:
    authorization = _header_value(request, "authorization")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        return None
    with SessionLocal() as db:
        return resolve_user_token(db, token)


def _managed_auth_context(
    request: Request, settings: Settings
) -> tuple[TenantContext, int | None] | None:
    cookie = request.cookies.get(settings.auth_session_cookie_name)
    if cookie:
        with SessionLocal() as db:
            resolved = resolve_auth_session(db, cookie)
            if resolved is not None:
                context, session = resolved
                return context, session.id
    authorization = _header_value(request, "authorization")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = OIDCVerifier(settings).validate(token)
        with SessionLocal() as db:
            user, workspace, _created = provision_oidc_identity(
                db,
                claims,
                provider=settings.oidc_issuer.rstrip("/"),
                settings=settings,
            )
            return TenantContext(user.id, workspace.id), None
    except OIDCValidationError:
        return None


def _set_request_context(request: Request, context: TenantContext) -> None:
    request.state.user_id = context.user_id
    request.state.workspace_id = context.workspace_id


async def _call_with_context(request: Request, call_next, context: TenantContext):
    _set_request_context(request, context)
    tokens = set_tenant_log_context(context.user_id, context.workspace_id)
    workspace_token = set_active_workspace(context.workspace_id)
    user_token = set_active_user(context.user_id)
    try:
        if request.method == "POST" and request.url.path in {
            "/transactions/interpret",
            "/plaid/sync",
            "/api/promotions/sync",
            "/api/replenishment/gmail/sync",
            "/api/replenishment/weekly-run",
            "/splitwise/oauth/authorize",
            "/splitwise/oauth/callback",
        }:
            try:
                rate_limiter.check(
                    f"expensive:{context.user_id}:{request.url.path}",
                    limit=10,
                    window_seconds=60,
                )
            except HTTPException:
                return JSONResponse(status_code=429, content={"detail": "Too many requests"})
        return await call_next(request)
    finally:
        reset_active_workspace(workspace_token)
        reset_active_user(user_token)
        reset_tenant_log_context(tokens)


def _is_public_request(request: Request) -> bool:
    if request.method == "OPTIONS":
        return True
    return request.url.path in PUBLIC_EXACT_PATHS or request.url.path.startswith("/static/")


def _csrf_origin_allowed(request: Request, settings: Settings) -> bool:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return True
    if not request.cookies.get(settings.auth_session_cookie_name):
        return True
    origin = _header_value(request, "origin").rstrip("/")
    if not origin:
        return not settings.is_production_mode
    allowed = {value.rstrip("/") for value in settings.frontend_origin}
    if settings.app_public_url:
        allowed.add(settings.app_public_url.rstrip("/"))
    request_origin = f"{request.url.scheme}://{request.url.netloc}".rstrip("/")
    allowed.add(request_origin)
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return bool(parsed.scheme in {"http", "https"} and parsed.netloc and origin in allowed)


def _auth_not_required(settings: Settings) -> bool:
    if settings.environment == "production":
        return False
    return not (
        settings.dashboard_api_token
        or (settings.dashboard_username and settings.dashboard_password)
    )


def _is_authorized(request: Request, settings: Settings) -> bool:
    if settings.auth_mode != "local":
        return False
    authorization = _header_value(request, "authorization")
    if settings.dashboard_api_token and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        return secrets.compare_digest(token, settings.dashboard_api_token)
    if (
        settings.dashboard_username
        and settings.dashboard_password
        and authorization.startswith("Basic ")
    ):
        return _valid_basic_auth(authorization, settings)
    return False


def _header_value(request: Request, name: str) -> str:
    value = request.headers.get(name)
    if value is not None:
        return value
    return request.headers.get(name.title(), "")


def _valid_basic_auth(authorization: str, settings: Settings) -> bool:
    try:
        decoded = base64.b64decode(
            authorization.removeprefix("Basic ").strip(),
            validate=True,
        ).decode("utf-8")
    except Exception:
        return False
    username, separator, password = decoded.partition(":")
    if not separator:
        return False
    return secrets.compare_digest(username, settings.dashboard_username) and secrets.compare_digest(
        password,
        settings.dashboard_password,
    )

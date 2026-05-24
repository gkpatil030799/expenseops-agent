from __future__ import annotations

import base64
import secrets
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import Settings

PUBLIC_EXACT_PATHS = {"/health", "/telegram/webhook", "/plaid/webhook"}


def install_dashboard_auth(app: FastAPI, settings: Settings) -> None:
    @app.middleware("http")
    async def dashboard_auth_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable],
    ):
        if _is_public_request(request):
            return await call_next(request)
        if _auth_not_required(settings):
            return await call_next(request)
        if _is_authorized(request, settings):
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
            headers={"WWW-Authenticate": "Basic"},
        )


def _is_public_request(request: Request) -> bool:
    if request.method == "OPTIONS":
        return True
    return request.url.path in PUBLIC_EXACT_PATHS


def _auth_not_required(settings: Settings) -> bool:
    if settings.environment == "production":
        return False
    return not (
        settings.dashboard_api_token
        or (settings.dashboard_username and settings.dashboard_password)
    )


def _is_authorized(request: Request, settings: Settings) -> bool:
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

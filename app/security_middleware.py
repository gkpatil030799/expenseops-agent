from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import Settings, get_settings
from app.logging_config import get_trace_id, log_event

logger = logging.getLogger(__name__)

_HTTPS_EXEMPT_PATHS = {"/health", "/readiness", "/telegram/webhook", "/plaid/webhook"}


class SecurityHeadersMiddleware:
    def __init__(self, app, *, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        if self._must_redirect_https(request):
            forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get(
                "host", ""
            )
            target = request.url.replace(scheme="https", netloc=forwarded_host)
            await RedirectResponse(str(target), status_code=308)(scope, receive, send)
            return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(self._headers(request))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)

    def _must_redirect_https(self, request: Request) -> bool:
        if not self.settings.is_production_mode or not self.settings.enforce_https:
            return False
        if request.url.path in _HTTPS_EXEMPT_PATHS:
            return False
        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
        return (forwarded_proto or request.url.scheme).lower() != "https"

    def _headers(self, request: Request) -> list[tuple[bytes, bytes]]:
        values = {
            "content-security-policy": (
                "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
                "form-action 'self'; object-src 'none'; img-src 'self' data: https:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                "connect-src 'self' https://api.openai.com https://*.googleapis.com"
            ),
            "permissions-policy": "camera=(), microphone=(), geolocation=(self)",
            "referrer-policy": "strict-origin-when-cross-origin",
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
        }
        if self.settings.is_production_mode:
            values["strict-transport-security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith(("/api/", "/auth/")):
            values["cache-control"] = "no-store"
        return [(name.encode("ascii"), value.encode("ascii")) for name, value in values.items()]


def install_safe_exception_handler(app) -> None:
    @app.exception_handler(Exception)
    async def safe_unhandled_error(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        correlation_id = get_trace_id()
        log_event(
            logger,
            "unhandled_request_error",
            level=logging.ERROR,
            error_class=type(exc).__name__,
            method=request.method,
            path=request.url.path,
        )
        response = JSONResponse(
            status_code=500,
            content={
                "detail": "ExpenseOps could not complete this request.",
                "code": "internal_error",
                "correlation_id": correlation_id,
            },
        )
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        if get_settings().is_production_mode:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

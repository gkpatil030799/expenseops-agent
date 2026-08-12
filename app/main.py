from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.responses import Response

from app.api import (
    admin_routes,
    ai_memory_routes,
    auth_routes,
    context_routes,
    household_routes,
    integration_routes,
    plaid_routes,
    promotion_routes,
    replenishment_routes,
    splitwise_routes,
    telegram_routes,
    transaction_routes,
    workspace_routes,
)
from app.auth import install_dashboard_auth
from app.config import get_settings
from app.db import engine, init_db
from app.logging_config import configure_logging, new_trace_id, reset_trace_id, set_trace_id
from sandbox.backend.router import router as sandbox_router

settings = get_settings()
configure_logging(settings)
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origin,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

install_dashboard_auth(app, settings)


@app.middleware("http")
async def request_trace_middleware(request: Request, call_next) -> Response:
    trace_id = request.headers.get("X-Request-ID") or new_trace_id()
    token = set_trace_id(trace_id)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = trace_id
        return response
    finally:
        reset_trace_id(token)


@app.on_event("startup")
def startup() -> None:
    init_db()


app.include_router(plaid_routes.router)
app.include_router(admin_routes.router)
app.include_router(auth_routes.router)
app.include_router(context_routes.router)
app.include_router(integration_routes.router)
app.include_router(workspace_routes.router)
app.include_router(splitwise_routes.router)
app.include_router(telegram_routes.router)
app.include_router(transaction_routes.router)
app.include_router(ai_memory_routes.router)
app.include_router(household_routes.router)
app.include_router(replenishment_routes.router)
app.include_router(promotion_routes.router)
app.include_router(sandbox_router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def root() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


@app.get("/readiness")
def readiness() -> dict:
    checks: dict[str, object] = {
        "database": "unknown",
        "auth_mode": settings.auth_mode,
        "oidc_configured": bool(
            settings.oidc_issuer
            and settings.oidc_audience
            and settings.oidc_client_id
            and settings.oidc_redirect_uri
        ),
        "gmail_configured": bool(settings.gmail_client_id and settings.gmail_client_secret),
        "telegram_configured": bool(settings.telegram_bot_token),
        "plaid_configured": bool(settings.plaid_client_id and settings.plaid_secret),
        "splitwise_configured": bool(
            settings.splitwise_api_key or settings.has_splitwise_oauth1_consumer
        ),
        "openai_configured": bool(settings.openai_api_key),
        "google_maps_configured": bool(settings.google_maps_api_key),
        "migration_revision": None,
        "migration_current": False,
    }
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    checks["database"] = "ok"
    checks["migration_revision"] = revision
    checks["migration_current"] = revision == "20260811_0014"
    return {"status": "ready", "checks": checks}

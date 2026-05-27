from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from app.api import (
    ai_memory_routes,
    plaid_routes,
    splitwise_routes,
    telegram_routes,
    transaction_routes,
)
from app.auth import install_dashboard_auth
from app.config import get_settings
from app.db import init_db
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
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
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
app.include_router(splitwise_routes.router)
app.include_router(telegram_routes.router)
app.include_router(transaction_routes.router)
app.include_router(ai_memory_routes.router)
app.include_router(sandbox_router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def root() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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

settings = get_settings()
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


@app.on_event("startup")
def startup() -> None:
    init_db()


app.include_router(plaid_routes.router)
app.include_router(splitwise_routes.router)
app.include_router(telegram_routes.router)
app.include_router(transaction_routes.router)
app.include_router(ai_memory_routes.router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def root() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}

from __future__ import annotations

from contextlib import asynccontextmanager
from html import escape

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from app.api import (
    admin_routes,
    ai_memory_routes,
    auth_routes,
    context_routes,
    household_routes,
    insights_routes,
    integration_routes,
    plaid_routes,
    privacy_routes,
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
from app.models import TenantScoped
from app.security_middleware import SecurityHeadersMiddleware, install_safe_exception_handler
from sandbox.backend.router import router as sandbox_router

settings = get_settings()
settings.validate_web_runtime()
configure_logging(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origin,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
app.add_middleware(SecurityHeadersMiddleware, settings=settings)

install_dashboard_auth(app, settings)
install_safe_exception_handler(app)


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
app.include_router(insights_routes.router)
app.include_router(replenishment_routes.router)
app.include_router(promotion_routes.router)
app.include_router(privacy_routes.router)
app.include_router(sandbox_router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def root() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


@app.get("/readiness")
def readiness() -> JSONResponse:
    expected_revision = ScriptDirectory.from_config(AlembicConfig("alembic.ini")).get_current_head()
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
        "expected_migration_revision": expected_revision,
        "shared_rate_limit": settings.rate_limit_backend == "postgres",
        "database_rls": False,
        "trusted_hosts_configured": bool(
            settings.trusted_hosts and "*" not in settings.trusted_hosts
        ),
        "https_enforced": settings.enforce_https,
    }
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            if engine.dialect.name == "postgresql":
                rls_rows = connection.execute(
                    text(
                        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = current_schema() AND c.relname = ANY(:tables)"
                    ),
                    {
                        "tables": sorted(
                            model.__tablename__ for model in TenantScoped.__subclasses__()
                        )
                    },
                ).mappings()
                rls_values = list(rls_rows)
                role_bypasses_rls = bool(
                    connection.scalar(
                        text(
                            "SELECT rolsuper OR rolbypassrls FROM pg_roles "
                            "WHERE rolname = current_user"
                        )
                    )
                )
                checks["database_rls"] = bool(
                    len(rls_values) == len(TenantScoped.__subclasses__())
                    and all(
                        value["relrowsecurity"] and value["relforcerowsecurity"]
                        for value in rls_values
                    )
                    and not role_bypasses_rls
                )
            else:
                checks["database_rls"] = settings.enable_postgres_rls
    except Exception:
        checks["database"] = "unavailable"
        return JSONResponse(status_code=503, content={"status": "not_ready", "checks": checks})
    checks["database"] = "ok"
    checks["migration_revision"] = revision
    checks["migration_current"] = revision == expected_revision
    critical = [
        checks["database"] == "ok",
        not settings.is_production_mode or bool(checks["migration_current"]),
        not settings.is_production_mode or settings.auth_mode == "oidc",
        not settings.is_production_mode or bool(checks["oidc_configured"]),
        not settings.is_production_mode or bool(checks["shared_rate_limit"]),
        not settings.is_production_mode or bool(checks["database_rls"]),
        not settings.is_production_mode or bool(checks["trusted_hosts_configured"]),
        not settings.is_production_mode or bool(checks["https_enforced"]),
    ]
    ready = all(critical)
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )


@app.get("/legal/privacy", response_class=HTMLResponse)
def privacy_policy() -> str:
    return _legal_page(
        "ExpenseOps Privacy Policy",
        (
            (
                "Information we process",
                "ExpenseOps processes the profile and workspace details you provide; bank "
                "transaction data from accounts you connect; expense decisions and Splitwise "
                "actions; Telegram identifiers and messages sent to the bot; Gmail receipt or "
                "promotion content you authorize; household records; and operational security "
                "events. Provider credentials are stored encrypted.",
            ),
            (
                "How we use information",
                "We use this information to provide the workflows you request, keep financial "
                "actions recoverable, improve your replenishment estimates from confirmed "
                "feedback, secure the service, and diagnose failures. ExpenseOps does not sell "
                "personal information or use connected mailbox or bank data for advertising.",
            ),
            (
                "Providers and model processing",
                "Connected providers receive only the requests needed for their integration. "
                "Relevant receipt or promotion text is sent to the configured model provider only "
                "when model processing is enabled and you have granted that consent. Each "
                "integration can be disconnected from Settings.",
            ),
            (
                "Workspace visibility",
                "Personal connections are tied to your identity. Workspace records can be visible "
                "to other members of that workspace according to their role. Leaving or deleting "
                "your account does not delete records already shared with remaining members.",
            ),
            (
                "Retention and deletion",
                "Settings lists the current maximum operational retention periods. Account "
                "deletion revokes sessions and provider credentials, removes content only you "
                "could access, and anonymizes your identity. Minimized financial and security "
                "audit records may be retained for integrity, fraud prevention, and legal "
                "obligations.",
            ),
            (
                "Your choices and contact",
                "You can grant or revoke Gmail and model-processing consent, disconnect providers, "
                "leave a workspace, or request account deletion from Settings. For access, "
                "correction, deletion, or privacy questions, contact "
                f"<a href='mailto:{escape(settings.support_email, quote=True)}'>"
                f"{escape(settings.support_email)}</a>.",
            ),
        ),
    )


@app.get("/legal/terms", response_class=HTMLResponse)
def terms_of_service() -> str:
    return _legal_page(
        "ExpenseOps Terms of Service",
        (
            (
                "Using ExpenseOps",
                "You must provide accurate account information, protect access to your account, "
                "and use ExpenseOps only for lawful purposes. You may connect only accounts and "
                "workspaces you are authorized to access.",
            ),
            (
                "Review before acting",
                "ExpenseOps assists with expense review, shared-expense preparation, household "
                "planning, and deal discovery. You remain responsible for confirming financial "
                "actions before posting them and for verifying route, merchant, offer, and "
                "participant details. ExpenseOps is not financial, tax, or legal advice.",
            ),
            (
                "Third-party services",
                "Plaid, Splitwise, Google, Telegram, OpenAI, and other connected services operate "
                "under their own terms and privacy policies. Their availability and results can "
                "change, and ExpenseOps cannot guarantee a third-party service will be available.",
            ),
            (
                "Service changes and account termination",
                "Features may change to improve safety or reliability. You can disconnect "
                "providers "
                "or delete your account from Settings. Access may be suspended to protect users, "
                "providers, or the service from abuse or security threats.",
            ),
            (
                "Availability and liability",
                "The service is provided on an as-available basis. To the extent permitted by law, "
                "ExpenseOps is not responsible for indirect losses, missed promotions, provider "
                "outages, or actions you approve using incorrect information. Nothing here limits "
                "rights that cannot legally be limited.",
            ),
            (
                "Questions",
                "Questions about these terms can be sent to "
                f"<a href='mailto:{escape(settings.support_email, quote=True)}'>"
                f"{escape(settings.support_email)}</a>.",
            ),
        ),
    )


def _legal_page(title: str, sections: tuple[tuple[str, str], ...]) -> str:
    content = "".join(
        f"<section><h2>{heading}</h2><p>{body}</p></section>"
        for heading, body in sections
    )
    return (
        "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' "
        "content='width=device-width,initial-scale=1'><title>" + title + "</title>"
        "<style>body{font:16px/1.7 system-ui;margin:0;background:#f6f7fb;color:#172033}"
        "main{max-width:760px;margin:48px auto;padding:32px;background:white;border:1px solid "
        "#dfe3ec;border-radius:20px}h1{line-height:1.15}h2{font-size:1.15rem;margin-top:2rem}"
        "a{color:#4338ca}section{max-width:68ch}</style>"
        f"<main><p><a href='/'>← ExpenseOps</a></p><h1>{title}</h1>{content}"
        "<p><strong>Effective date:</strong> August 13, 2026</p></main></html>"
    )

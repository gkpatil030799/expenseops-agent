from __future__ import annotations

import csv
import re
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
    agent_routes,
    ai_memory_routes,
    attention_routes,
    auth_routes,
    classification_routes,
    context_routes,
    household_routes,
    insights_routes,
    integration_routes,
    plaid_routes,
    privacy_routes,
    promotion_routes,
    replenishment_routes,
    review_inbox_routes,
    splitwise_routes,
    telegram_routes,
    transaction_routes,
    workspace_routes,
)
from app.auth import install_dashboard_auth
from app.config import get_settings
from app.db import Base, engine, init_db
from app.logging_config import (
    configure_logging,
    normalize_external_trace_id,
    reset_trace_id,
    set_trace_id,
)
from app.models import TenantScoped
from app.security_middleware import SecurityHeadersMiddleware, install_safe_exception_handler
from sandbox.backend.router import router as sandbox_router

settings = get_settings()
settings.validate_web_runtime()
configure_logging(settings)

_WORKSPACE_SETTING_EXPRESSION = (
    "NULLIF(current_setting('expenseops.workspace_id',true),'')::integer"
)
_DIRECT_WORKSPACE_POLICY_EXPRESSION = f"workspace_id={_WORKSPACE_SETTING_EXPRESSION}"
_CHILD_WORKSPACE_POLICY_EXPRESSIONS = {
    "purchase_receipt_items": (
        "EXISTS (SELECT 1 FROM public.purchase_receipts AS receipt "
        "WHERE receipt.id = purchase_receipt_items.receipt_id AND "
        f"receipt.workspace_id = {_WORKSPACE_SETTING_EXPRESSION}) "
        "AND (household_item_id IS NULL OR EXISTS ("
        "SELECT 1 FROM public.household_items AS item WHERE "
        "item.id = purchase_receipt_items.household_item_id AND "
        f"item.workspace_id = {_WORKSPACE_SETTING_EXPRESSION})) "
        "AND (classification_subcategory_id IS NULL OR EXISTS ("
        "SELECT 1 FROM public.classification_subcategories AS subcategory WHERE "
        "subcategory.id = purchase_receipt_items.classification_subcategory_id AND "
        f"subcategory.workspace_id = {_WORKSPACE_SETTING_EXPRESSION})) "
        "AND (classification_concept_id IS NULL OR EXISTS ("
        "SELECT 1 FROM public.classification_concepts AS concept WHERE "
        "concept.id = purchase_receipt_items.classification_concept_id AND "
        f"concept.workspace_id = {_WORKSPACE_SETTING_EXPRESSION}))"
    ),
    "household_item_aliases": (
        "EXISTS (SELECT 1 FROM public.household_items AS item WHERE "
        "item.id = household_item_aliases.household_item_id AND "
        f"item.workspace_id = {_WORKSPACE_SETTING_EXPRESSION})"
    ),
    "promotion_feedback": (
        "EXISTS (SELECT 1 FROM public.promotion_offers AS offer WHERE "
        "offer.id = promotion_feedback.promotion_offer_id AND "
        f"offer.workspace_id = {_WORKSPACE_SETTING_EXPRESSION})"
    ),
    "errand_household_items": (
        "EXISTS (SELECT 1 FROM public.errands AS errand WHERE "
        "errand.id = errand_household_items.errand_id AND "
        f"errand.workspace_id = {_WORKSPACE_SETTING_EXPRESSION}) "
        "AND EXISTS (SELECT 1 FROM public.household_items AS item WHERE "
        "item.id = errand_household_items.household_item_id AND "
        f"item.workspace_id = {_WORKSPACE_SETTING_EXPRESSION})"
    ),
    "errand_plan_stops": (
        "EXISTS (SELECT 1 FROM public.errand_plans AS plan WHERE "
        "plan.id = errand_plan_stops.plan_id AND "
        f"plan.workspace_id = {_WORKSPACE_SETTING_EXPRESSION})"
    ),
    "errand_plan_stop_errands": (
        "EXISTS (SELECT 1 FROM public.errand_plan_stops AS stop JOIN "
        "public.errand_plans AS plan ON plan.id = stop.plan_id WHERE "
        "stop.id = errand_plan_stop_errands.stop_id AND "
        f"plan.workspace_id = {_WORKSPACE_SETTING_EXPRESSION}) "
        "AND EXISTS (SELECT 1 FROM public.errands AS errand WHERE "
        "errand.id = errand_plan_stop_errands.errand_id AND "
        f"errand.workspace_id = {_WORKSPACE_SETTING_EXPRESSION})"
    ),
    "errand_plan_stop_household_items": (
        "EXISTS (SELECT 1 FROM public.errand_plan_stops AS stop JOIN "
        "public.errand_plans AS plan ON plan.id = stop.plan_id WHERE "
        "stop.id = errand_plan_stop_household_items.stop_id AND "
        f"plan.workspace_id = {_WORKSPACE_SETTING_EXPRESSION}) "
        "AND EXISTS (SELECT 1 FROM public.household_items AS item WHERE "
        "item.id = errand_plan_stop_household_items.household_item_id AND "
        f"item.workspace_id = {_WORKSPACE_SETTING_EXPRESSION})"
    ),
}
_MIGRATION_ROLE_NAME = "expenseops_migrator"
_RUNTIME_ROLE_NAME = "expenseops_runtime"
_REQUIRED_ROUTING_FUNCTIONS = frozenset(
    {
        "public.expenseops_route_plaid_item(text)",
        "public.expenseops_route_telegram_identity(text, text)",
        "public.expenseops_route_active_telegram_identity_by_link_code(text)",
        "public.expenseops_route_telegram_link_code(text)",
        "public.expenseops_route_workspace_invitation(text)",
    }
)
_ROUTING_FUNCTION_CONTRACTS = {
    "public.expenseops_route_plaid_item(text)": {
        "result": "TABLE(workspace_id integer, plaid_item_id integer)",
        "source": (
            "SELECT item.workspace_id, item.id FROM public.plaid_items AS item "
            "JOIN public.workspace_memberships AS membership "
            "ON membership.workspace_id = item.workspace_id "
            "AND (item.owner_user_id IS NULL "
            "OR membership.user_id = item.owner_user_id) "
            "JOIN public.users AS member_user "
            "ON member_user.id = membership.user_id "
            "AND member_user.status = 'active' "
            "WHERE item.item_id = $1 ORDER BY item.id LIMIT 1"
        ),
    },
    "public.expenseops_route_telegram_identity(text, text)": {
        "result": ("TABLE(workspace_id integer, telegram_identity_id integer, user_id integer)"),
        "source": (
            "SELECT identity.workspace_id, identity.id, identity.user_id "
            "FROM public.telegram_identities AS identity "
            "JOIN public.workspace_memberships AS membership "
            "ON membership.workspace_id = identity.workspace_id "
            "AND membership.user_id = identity.user_id "
            "JOIN public.users AS member_user "
            "ON member_user.id = membership.user_id "
            "AND member_user.status = 'active' "
            "WHERE identity.telegram_user_id = $1 AND identity.chat_id = $2 "
            "ORDER BY identity.id LIMIT 1"
        ),
    },
    "public.expenseops_route_active_telegram_identity_by_link_code(text)": {
        "result": "TABLE(workspace_id integer, telegram_identity_id integer)",
        "source": (
            "SELECT identity.workspace_id, identity.id "
            "FROM public.telegram_link_codes AS link "
            "JOIN public.workspace_memberships AS link_membership "
            "ON link_membership.workspace_id = link.workspace_id "
            "AND link_membership.user_id = link.user_id "
            "JOIN public.users AS link_user ON link_user.id = link_membership.user_id "
            "AND link_user.status = 'active' "
            "JOIN public.telegram_identities AS identity ON identity.user_id = link.user_id "
            "JOIN public.workspace_memberships AS identity_membership "
            "ON identity_membership.workspace_id = identity.workspace_id "
            "AND identity_membership.user_id = identity.user_id "
            "JOIN public.users AS identity_user "
            "ON identity_user.id = identity_membership.user_id "
            "AND identity_user.status = 'active' "
            "WHERE link.code_hash = $1 AND identity.enabled IS TRUE "
            "ORDER BY identity.id LIMIT 1"
        ),
    },
    "public.expenseops_route_telegram_link_code(text)": {
        "result": ("TABLE(workspace_id integer, telegram_link_code_id integer, user_id integer)"),
        "source": (
            "SELECT link.workspace_id, link.id, link.user_id "
            "FROM public.telegram_link_codes AS link "
            "JOIN public.workspace_memberships AS membership "
            "ON membership.workspace_id = link.workspace_id "
            "AND membership.user_id = link.user_id "
            "JOIN public.users AS member_user "
            "ON member_user.id = membership.user_id "
            "AND member_user.status = 'active' "
            "WHERE link.code_hash = $1 ORDER BY link.id LIMIT 1"
        ),
    },
    "public.expenseops_route_workspace_invitation(text)": {
        "result": "TABLE(workspace_id integer, workspace_invitation_id integer)",
        "source": (
            "SELECT invitation.workspace_id, invitation.id "
            "FROM public.workspace_invitations AS invitation "
            "JOIN public.workspace_memberships AS membership "
            "ON membership.workspace_id = invitation.workspace_id "
            "AND membership.user_id = invitation.invited_by_user_id "
            "JOIN public.users AS member_user "
            "ON member_user.id = membership.user_id "
            "AND member_user.status = 'active' "
            "WHERE invitation.token_hash = $1 ORDER BY invitation.id LIMIT 1"
        ),
    },
}


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

install_dashboard_auth(app, settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origin,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
app.add_middleware(SecurityHeadersMiddleware, settings=settings)

install_safe_exception_handler(app)


@app.middleware("http")
async def request_trace_middleware(request: Request, call_next) -> Response:
    trace_id = normalize_external_trace_id(request.headers.get("X-Request-ID"))
    token = set_trace_id(trace_id)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = trace_id
        return response
    finally:
        reset_trace_id(token)


app.include_router(plaid_routes.router)
app.include_router(admin_routes.router)
app.include_router(agent_routes.router)
app.include_router(auth_routes.router)
app.include_router(classification_routes.router)
app.include_router(context_routes.router)
app.include_router(integration_routes.router)
app.include_router(workspace_routes.router)
app.include_router(splitwise_routes.router)
app.include_router(telegram_routes.router)
app.include_router(transaction_routes.router)
app.include_router(ai_memory_routes.router)
app.include_router(attention_routes.router)
app.include_router(household_routes.router)
app.include_router(insights_routes.router)
app.include_router(replenishment_routes.router)
app.include_router(review_inbox_routes.router)
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
        "receipt_parser_provider": settings.receipt_parser_provider,
        "receipt_parser_configured": bool(
            settings.receipt_parser_provider == "openai" and settings.openai_api_key
        ),
        "receipt_intake_active": bool(
            settings.telegram_bot_token or settings.gmail_receipt_sync_enabled
        ),
        "google_maps_configured": bool(settings.google_maps_api_key),
        "migration_revision": None,
        "migration_current": False,
        "expected_migration_revision": expected_revision,
        "shared_rate_limit": settings.rate_limit_backend == "postgres",
        "database_rls": False,
        "tenant_rls_enabled": False,
        "tenant_rls_forced": False,
        "tenant_rls_policies_hardened": False,
        "tenant_routing_functions_hardened": False,
        "runtime_role_superuser": None,
        "runtime_role_expected": None,
        "runtime_role_login": None,
        "runtime_role_bypassrls": None,
        "runtime_role_createdb": None,
        "runtime_role_createrole": None,
        "runtime_role_replication": None,
        "runtime_role_inherit": None,
        "runtime_role_has_memberships": None,
        "runtime_role_owns_application_tables": None,
        "runtime_role_owns_routing_functions": None,
        "runtime_role_database_create": None,
        "runtime_role_database_temporary": None,
        "runtime_role_database_connect": None,
        "runtime_role_schema_create": None,
        "runtime_role_schema_usage": None,
        "runtime_role_excess_table_privileges": None,
        "runtime_role_missing_table_privileges": None,
        "runtime_role_unexpected_table_privileges": None,
        "runtime_role_sequence_privileges_unsafe": None,
        "runtime_role_unexpected_sequence_privileges": None,
        "runtime_role_unexpected_function_execute": None,
        "runtime_role_public_type_usage": None,
        "runtime_role_alembic_write": None,
        "trusted_hosts_configured": bool(
            settings.trusted_hosts and "*" not in settings.trusted_hosts
        ),
        "https_enforced": settings.enforce_https,
    }
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            revision = connection.scalar(
                text(
                    "SELECT version_num FROM public.alembic_version"
                    if engine.dialect.name == "postgresql"
                    else "SELECT version_num FROM alembic_version"
                )
            )
            if engine.dialect.name == "postgresql":
                direct_tenant_tables = sorted(
                    {model.__tablename__ for model in TenantScoped.__subclasses__()}
                )
                policy_expressions = {
                    **{
                        table: _DIRECT_WORKSPACE_POLICY_EXPRESSION for table in direct_tenant_tables
                    },
                    **_CHILD_WORKSPACE_POLICY_EXPRESSIONS,
                }
                protected_tables = sorted(policy_expressions)
                application_tables = sorted(Base.metadata.tables)
                append_only_tables = [
                    table for table in ("classification_decisions",) if table in application_tables
                ]
                mutable_application_tables = [
                    table for table in application_tables if table not in append_only_tables
                ]
                allowed_runtime_relations = [*application_tables, "alembic_version"]
                rls_rows = connection.execute(
                    text(
                        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relname = ANY(:tables)"
                    ),
                    {"tables": protected_tables},
                ).mappings()
                rls_values = list(rls_rows)
                policy_values = list(
                    connection.execute(
                        text(
                            "SELECT tablename, policyname, permissive, roles, cmd, qual, "
                            "with_check FROM pg_policies WHERE schemaname = 'public' "
                            "AND tablename = ANY(:tables)"
                        ),
                        {"tables": protected_tables},
                    ).mappings()
                )
                routing_values = list(
                    connection.execute(
                        text(
                            "SELECT 'public.' || p.proname || '(' || "
                            "pg_catalog.oidvectortypes(p.proargtypes) || ')' AS signature, "
                            "p.prosecdef AS security_definer, p.proconfig, p.prosrc, "
                            "pg_catalog.pg_get_function_result(p.oid) AS function_result, "
                            "language.lanname AS language_name, "
                            "p.provolatile AS volatility, p.proisstrict AS is_strict, "
                            "has_function_privilege(current_user, p.oid, 'EXECUTE') "
                            "AS runtime_execute, "
                            "EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE("
                            "p.proacl, pg_catalog.acldefault('f', p.proowner))) AS acl "
                            "WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE') "
                            "AS public_execute, "
                            "p.proowner = (SELECT oid FROM pg_roles "
                            "WHERE rolname = current_user) AS runtime_owned, "
                            "owner_role.rolname AS owner_name, "
                            "owner_role.rolsuper AS owner_superuser, "
                            "owner_role.rolbypassrls AS owner_bypassrls, "
                            "owner_role.rolcreatedb AS owner_createdb, "
                            "owner_role.rolcreaterole AS owner_createrole, "
                            "owner_role.rolreplication AS owner_replication, "
                            "owner_role.rolcanlogin AS owner_login, "
                            "owner_role.rolinherit AS owner_inherit, "
                            "EXISTS (SELECT 1 FROM pg_auth_members owner_membership "
                            "WHERE owner_membership.member = owner_role.oid OR "
                            "owner_membership.roleid = owner_role.oid) "
                            "AS owner_has_memberships "
                            "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                            "JOIN pg_language language ON language.oid = p.prolang "
                            "JOIN pg_roles owner_role ON owner_role.oid = p.proowner "
                            "WHERE n.nspname = 'public' AND p.proname = ANY(:names)"
                        ),
                        {
                            "names": sorted(
                                signature.split("(", maxsplit=1)[0].removeprefix("public.")
                                for signature in _REQUIRED_ROUTING_FUNCTIONS
                            )
                        },
                    ).mappings()
                )
                role = (
                    connection.execute(
                        text(
                            "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, "
                            "rolcreatedb, rolcreaterole, rolreplication, rolinherit "
                            "FROM pg_roles "
                            "WHERE rolname = current_user"
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                checks["tenant_rls_enabled"] = bool(
                    len(rls_values) == len(protected_tables)
                    and all(value["relrowsecurity"] for value in rls_values)
                )
                checks["tenant_rls_forced"] = bool(
                    len(rls_values) == len(protected_tables)
                    and all(value["relforcerowsecurity"] for value in rls_values)
                )
                checks["tenant_rls_policies_hardened"] = _policies_are_hardened(
                    policy_values,
                    policy_expressions,
                )
                checks["tenant_routing_functions_hardened"] = _routing_functions_are_hardened(
                    routing_values
                )
                checks["runtime_role_expected"] = (
                    str(role["rolname"]) == _RUNTIME_ROLE_NAME if role is not None else None
                )
                checks["runtime_role_login"] = (
                    bool(role["rolcanlogin"]) if role is not None else None
                )
                checks["runtime_role_superuser"] = (
                    bool(role["rolsuper"]) if role is not None else None
                )
                checks["runtime_role_bypassrls"] = (
                    bool(role["rolbypassrls"]) if role is not None else None
                )
                checks["runtime_role_createdb"] = (
                    bool(role["rolcreatedb"]) if role is not None else None
                )
                checks["runtime_role_createrole"] = (
                    bool(role["rolcreaterole"]) if role is not None else None
                )
                checks["runtime_role_replication"] = (
                    bool(role["rolreplication"]) if role is not None else None
                )
                checks["runtime_role_inherit"] = (
                    bool(role["rolinherit"]) if role is not None else None
                )
                checks["runtime_role_has_memberships"] = bool(
                    connection.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_auth_members membership "
                            "WHERE membership.member = (SELECT oid FROM pg_roles "
                            "WHERE rolname = current_user) OR membership.roleid = "
                            "(SELECT oid FROM pg_roles WHERE rolname = current_user))"
                        )
                    )
                )
                checks["runtime_role_owns_application_tables"] = bool(
                    connection.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_class c "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'public' "
                            "AND c.relname = ANY(:tables) "
                            "AND c.relowner = (SELECT oid FROM pg_roles "
                            "WHERE rolname = current_user))"
                        ),
                        {"tables": allowed_runtime_relations},
                    )
                )
                checks["runtime_role_owns_routing_functions"] = any(
                    bool(value["runtime_owned"]) for value in routing_values
                )
                checks["runtime_role_database_create"] = bool(
                    connection.scalar(
                        text(
                            "SELECT has_database_privilege(current_user, "
                            "current_database(), 'CREATE')"
                        )
                    )
                )
                checks["runtime_role_database_temporary"] = bool(
                    connection.scalar(
                        text(
                            "SELECT has_database_privilege(current_user, "
                            "current_database(), 'TEMPORARY')"
                        )
                    )
                )
                checks["runtime_role_database_connect"] = bool(
                    connection.scalar(
                        text(
                            "SELECT has_database_privilege(current_user, "
                            "current_database(), 'CONNECT')"
                        )
                    )
                )
                checks["runtime_role_schema_create"] = bool(
                    connection.scalar(
                        text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
                    )
                )
                checks["runtime_role_schema_usage"] = bool(
                    connection.scalar(
                        text("SELECT has_schema_privilege(current_user, 'public', 'USAGE')")
                    )
                )
                checks["runtime_role_alembic_write"] = bool(
                    connection.scalar(
                        text(
                            "SELECT has_table_privilege(current_user, "
                            "'public.alembic_version', 'INSERT') OR "
                            "has_table_privilege(current_user, "
                            "'public.alembic_version', 'UPDATE') OR "
                            "has_table_privilege(current_user, "
                            "'public.alembic_version', 'DELETE')"
                        )
                    )
                )
                checks["runtime_role_excess_table_privileges"] = bool(
                    connection.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_class c "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'public' "
                            "AND c.relname = ANY(:tables) AND ("
                            "has_table_privilege(current_user, c.oid, 'TRUNCATE') OR "
                            "has_table_privilege(current_user, c.oid, 'REFERENCES') OR "
                            "has_table_privilege(current_user, c.oid, 'TRIGGER')))"
                        ),
                        {"tables": application_tables},
                    )
                ) or bool(
                    append_only_tables
                    and connection.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_class c "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'public' "
                            "AND c.relname = ANY(:tables) AND ("
                            "has_table_privilege(current_user, c.oid, 'UPDATE') OR "
                            "has_table_privilege(current_user, c.oid, 'DELETE')))"
                        ),
                        {"tables": append_only_tables},
                    )
                )
                checks["runtime_role_missing_table_privileges"] = bool(
                    connection.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_class c "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'public' "
                            "AND c.relname = ANY(:tables) AND ("
                            "NOT has_table_privilege(current_user, c.oid, 'SELECT') OR "
                            "NOT has_table_privilege(current_user, c.oid, 'INSERT') OR "
                            "NOT has_table_privilege(current_user, c.oid, 'UPDATE') OR "
                            "NOT has_table_privilege(current_user, c.oid, 'DELETE')))"
                        ),
                        {"tables": mutable_application_tables},
                    )
                ) or bool(
                    append_only_tables
                    and connection.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_class c "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'public' "
                            "AND c.relname = ANY(:tables) AND ("
                            "NOT has_table_privilege(current_user, c.oid, 'SELECT') OR "
                            "NOT has_table_privilege(current_user, c.oid, 'INSERT')))"
                        ),
                        {"tables": append_only_tables},
                    )
                )
                checks["runtime_role_unexpected_table_privileges"] = bool(
                    connection.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_class c "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'public' "
                            "AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
                            "AND c.relname <> ALL(:tables) AND ("
                            "has_table_privilege(current_user, c.oid, 'SELECT') OR "
                            "has_table_privilege(current_user, c.oid, 'INSERT') OR "
                            "has_table_privilege(current_user, c.oid, 'UPDATE') OR "
                            "has_table_privilege(current_user, c.oid, 'DELETE') OR "
                            "has_table_privilege(current_user, c.oid, 'TRUNCATE') OR "
                            "has_table_privilege(current_user, c.oid, 'REFERENCES') OR "
                            "has_table_privilege(current_user, c.oid, 'TRIGGER')))"
                        ),
                        {"tables": allowed_runtime_relations},
                    )
                )
                checks["runtime_role_sequence_privileges_unsafe"] = bool(
                    connection.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_class sequence_object "
                            "JOIN pg_namespace sequence_namespace "
                            "ON sequence_namespace.oid = sequence_object.relnamespace "
                            "JOIN pg_depend dependency ON dependency.classid = "
                            "'pg_class'::regclass AND dependency.objid = sequence_object.oid "
                            "AND dependency.deptype IN ('a', 'i') "
                            "JOIN pg_class table_object ON table_object.oid = dependency.refobjid "
                            "JOIN pg_namespace table_namespace "
                            "ON table_namespace.oid = table_object.relnamespace "
                            "WHERE sequence_object.relkind = 'S' "
                            "AND sequence_namespace.nspname = 'public' "
                            "AND table_namespace.nspname = 'public' "
                            "AND table_object.relname = ANY(:tables) AND ("
                            "NOT has_sequence_privilege(current_user, sequence_object.oid, "
                            "'USAGE') OR NOT has_sequence_privilege(current_user, "
                            "sequence_object.oid, 'SELECT') OR "
                            "has_sequence_privilege(current_user, sequence_object.oid, "
                            "'UPDATE')))"
                        ),
                        {"tables": application_tables},
                    )
                )
                checks["runtime_role_unexpected_sequence_privileges"] = bool(
                    connection.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_class sequence_object "
                            "JOIN pg_namespace sequence_namespace "
                            "ON sequence_namespace.oid = sequence_object.relnamespace "
                            "WHERE sequence_namespace.nspname = 'public' "
                            "AND sequence_object.relkind = 'S' AND NOT EXISTS ("
                            "SELECT 1 FROM pg_depend dependency "
                            "JOIN pg_class table_object "
                            "ON table_object.oid = dependency.refobjid "
                            "JOIN pg_namespace table_namespace "
                            "ON table_namespace.oid = table_object.relnamespace "
                            "WHERE dependency.classid = 'pg_class'::regclass "
                            "AND dependency.objid = sequence_object.oid "
                            "AND dependency.deptype IN ('a', 'i') "
                            "AND table_namespace.nspname = 'public' "
                            "AND table_object.relname = ANY(:tables)) AND ("
                            "has_sequence_privilege(current_user, sequence_object.oid, "
                            "'USAGE') OR has_sequence_privilege(current_user, "
                            "sequence_object.oid, 'SELECT') OR "
                            "has_sequence_privilege(current_user, sequence_object.oid, "
                            "'UPDATE')))"
                        ),
                        {"tables": application_tables},
                    )
                )
                checks["runtime_role_unexpected_function_execute"] = bool(
                    connection.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_proc function_object "
                            "JOIN pg_namespace function_namespace "
                            "ON function_namespace.oid = function_object.pronamespace "
                            "WHERE function_namespace.nspname = 'public' "
                            "AND ('public.' || function_object.proname || '(' || "
                            "pg_catalog.oidvectortypes(function_object.proargtypes) || ')') "
                            "<> ALL(:signatures) "
                            "AND has_function_privilege(current_user, "
                            "function_object.oid, 'EXECUTE'))"
                        ),
                        {"signatures": sorted(_REQUIRED_ROUTING_FUNCTIONS)},
                    )
                )
                checks["runtime_role_public_type_usage"] = bool(
                    connection.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_type type_object "
                            "JOIN pg_namespace type_namespace "
                            "ON type_namespace.oid = type_object.typnamespace "
                            "WHERE type_namespace.nspname = 'public' "
                            "AND NOT (type_object.typelem <> 0 AND "
                            "type_object.typsubscript = "
                            "'pg_catalog.array_subscript_handler'::regproc) "
                            "AND type_object.typtype <> 'm' "
                            "AND has_type_privilege(current_user, type_object.oid, 'USAGE'))"
                        )
                    )
                )
                checks["database_rls"] = bool(
                    checks["tenant_rls_enabled"]
                    and checks["tenant_rls_forced"]
                    and checks["tenant_rls_policies_hardened"]
                    and checks["tenant_routing_functions_hardened"]
                    and role is not None
                    and checks["runtime_role_expected"]
                    and checks["runtime_role_login"]
                    and not checks["runtime_role_superuser"]
                    and not checks["runtime_role_bypassrls"]
                    and not checks["runtime_role_createdb"]
                    and not checks["runtime_role_createrole"]
                    and not checks["runtime_role_replication"]
                    and not checks["runtime_role_inherit"]
                    and not checks["runtime_role_has_memberships"]
                    and not checks["runtime_role_owns_application_tables"]
                    and not checks["runtime_role_owns_routing_functions"]
                    and not checks["runtime_role_database_create"]
                    and not checks["runtime_role_database_temporary"]
                    and checks["runtime_role_database_connect"]
                    and not checks["runtime_role_schema_create"]
                    and checks["runtime_role_schema_usage"]
                    and not checks["runtime_role_excess_table_privileges"]
                    and not checks["runtime_role_missing_table_privileges"]
                    and not checks["runtime_role_unexpected_table_privileges"]
                    and not checks["runtime_role_sequence_privileges_unsafe"]
                    and not checks["runtime_role_unexpected_sequence_privileges"]
                    and not checks["runtime_role_unexpected_function_execute"]
                    and not checks["runtime_role_public_type_usage"]
                    and not checks["runtime_role_alembic_write"]
                )
            else:
                checks["database_rls"] = settings.enable_postgres_rls
                checks["tenant_rls_enabled"] = settings.enable_postgres_rls
                checks["tenant_rls_forced"] = settings.enable_postgres_rls
                checks["tenant_rls_policies_hardened"] = settings.enable_postgres_rls
                checks["tenant_routing_functions_hardened"] = settings.enable_postgres_rls
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
        not settings.is_production_mode
        or not bool(checks["receipt_intake_active"])
        or bool(checks["receipt_parser_configured"]),
    ]
    ready = all(critical)
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )


def _policies_are_hardened(
    policy_rows,
    expected_policies: list[str] | dict[str, str],
) -> bool:
    expressions = (
        {table: _DIRECT_WORKSPACE_POLICY_EXPRESSION for table in expected_policies}
        if isinstance(expected_policies, list)
        else expected_policies
    )
    if len(policy_rows) != len(expressions):
        return False
    by_table = {row["tablename"]: row for row in policy_rows}
    if set(by_table) != set(expressions):
        return False
    for table, row in by_table.items():
        roles = _normalize_policy_roles(row["roles"])
        expected_expression = expressions[table]
        if (
            row["policyname"] != "expenseops_workspace_isolation"
            or str(row["permissive"]).casefold() != "permissive"
            or roles != {"public"}
            or str(row["cmd"]).casefold() != "all"
            or _normalize_policy_expression(row["qual"])
            != _normalize_policy_expression(expected_expression)
            or _normalize_policy_expression(row["with_check"])
            != _normalize_policy_expression(expected_expression)
        ):
            return False
    return True


def _normalize_policy_roles(value: object) -> set[str]:
    """Normalize psycopg arrays and PostgreSQL's text-array representation."""
    values = _postgres_array_values(value)
    return {str(role).strip().strip('"').casefold() for role in values if str(role).strip()}


def _routing_functions_are_hardened(function_rows) -> bool:
    if len(function_rows) != len(_REQUIRED_ROUTING_FUNCTIONS):
        return False
    by_signature = {str(row["signature"]): row for row in function_rows}
    if set(by_signature) != _REQUIRED_ROUTING_FUNCTIONS:
        return False
    for signature, row in by_signature.items():
        contract = _ROUTING_FUNCTION_CONTRACTS[signature]
        if not (
            bool(row["security_definer"])
            and _has_safe_routing_search_path(row["proconfig"])
            and str(row["language_name"]).casefold() == "sql"
            and str(row["volatility"]).casefold() == "s"
            and bool(row["is_strict"])
            and _normalize_sql_contract(row["prosrc"])
            == _normalize_sql_contract(contract["source"])
            and _normalize_sql_contract(row["function_result"])
            == _normalize_sql_contract(contract["result"])
            and bool(row["runtime_execute"])
            and not bool(row["public_execute"])
            and not bool(row["runtime_owned"])
            and str(row["owner_name"]) == _MIGRATION_ROLE_NAME
            and not bool(row["owner_superuser"])
            and bool(row["owner_bypassrls"])
            and not bool(row["owner_createdb"])
            and not bool(row["owner_createrole"])
            and not bool(row["owner_replication"])
            and bool(row["owner_login"])
            and not bool(row["owner_inherit"])
            and not bool(row["owner_has_memberships"])
        ):
            return False
    return True


def _has_safe_routing_search_path(value: object) -> bool:
    values = _postgres_array_values(value)
    normalized = {re.sub(r"\s+", "", str(setting)).casefold() for setting in values}
    return "search_path=pg_catalog,pg_temp" in normalized


def _postgres_array_values(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not (raw.startswith("{") and raw.endswith("}")):
            return [raw]
        inner = raw[1:-1]
        if not inner:
            return []
        return list(next(csv.reader([inner], skipinitialspace=True)))
    try:
        return list(value)  # type: ignore[arg-type]
    except TypeError:
        return [value]


def _normalize_policy_expression(value: object) -> str:
    normalized = str(value or "").casefold().replace("::text", "")
    # pg_get_expr omits a schema that is visible in search_path and normally
    # drops the optional AS keyword for relation aliases.  Treat only those
    # catalog-rendering differences as equivalent; identifiers, joins,
    # predicates, operators, and values remain part of the exact contract.
    normalized = normalized.replace("public.", "")
    normalized = re.sub(r"\bas\b", "", normalized)
    flattened = re.sub(r"[\s()]", "", normalized)
    return f"{flattened}|{_policy_boolean_shape(normalized)}"


def _policy_boolean_shape(expression: str) -> str:
    """Preserve AND/OR precedence while ignoring harmless catalog grouping."""
    expression = _strip_outer_policy_parentheses(expression.strip())
    for operator in ("or", "and"):
        parts = _split_top_level_policy_operator(expression, operator)
        if len(parts) > 1:
            children = ",".join(_policy_boolean_shape(part) for part in parts)
            return f"{operator}({children})"
    return "atom"


def _strip_outer_policy_parentheses(expression: str) -> str:
    while (
        len(expression) >= 2
        and expression[0] == "("
        and _matching_policy_parenthesis(expression, 0) == len(expression) - 1
    ):
        expression = expression[1:-1].strip()
    return expression


def _matching_policy_parenthesis(expression: str, opening_index: int) -> int | None:
    depth = 0
    quote: str | None = None
    index = opening_index
    while index < len(expression):
        character = expression[index]
        if quote is not None:
            if character == quote:
                if index + 1 < len(expression) and expression[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif character in {"'", '"'}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _split_top_level_policy_operator(expression: str, operator: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    start = 0
    index = 0
    while index < len(expression):
        character = expression[index]
        if quote is not None:
            if character == quote:
                if index + 1 < len(expression) and expression[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if character in {"'", '"'}:
            quote = character
            index += 1
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif depth == 0 and expression[index : index + len(operator)].casefold() == operator:
            before = expression[index - 1] if index else " "
            after_index = index + len(operator)
            after = expression[after_index] if after_index < len(expression) else " "
            if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                parts.append(expression[start:index].strip())
                start = after_index
                index = after_index
                continue
        index += 1
    if parts:
        parts.append(expression[start:].strip())
    return parts or [expression]


def _normalize_sql_contract(value: object) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    normalized = re.sub(r"\(\s+", "(", normalized)
    return re.sub(r"\s+\)", ")", normalized)


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
                "Relevant receipt email or promotion text, and receipt photo or PDF bytes you "
                "submit, are sent to the configured model provider only when receipt model "
                "processing is enabled and you have granted that consent. Bounded unresolved "
                "transaction merchant, description, and provider-category evidence is sent only "
                "when you separately enable model-assisted transaction categories. Each "
                "integration and consent can be disconnected or withdrawn from Settings.",
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
        f"<section><h2>{heading}</h2><p>{body}</p></section>" for heading, body in sections
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

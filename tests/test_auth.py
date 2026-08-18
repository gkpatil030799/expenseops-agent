from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.auth_routes import _safe_redirect_after
from app.config import Settings
from app.main import app
from app.security_middleware import SecurityHeadersMiddleware
from app.tenancy import TenantContext


def _safe_production_settings(**overrides):
    values = {
        "environment": "production",
        "app_secret_key": "configured-fernet-key",
        "telegram_webhook_secret": "configured-telegram-secret",
        "telegram_allowed_user_id": "12345",
        "dashboard_api_token": "configured-dashboard-token",
        "plaid_env": "production",
        "database_url": "postgresql://expenseops@db.example/expenseops",
        "enable_postgres_rls": True,
        "rate_limit_backend": "postgres",
        "support_email": "support@example.com",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def _assert_security_headers(response) -> None:
    assert response.headers["content-security-policy"].startswith("default-src 'self'")
    assert response.headers["permissions-policy"]
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_health_route_is_public():
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("/", "/"),
        ("/settings", "/settings"),
        ("/settings/", "/settings/"),
        ("/?workspace=settings", "/?workspace=settings"),
        ("//attacker.example", "/"),
        ("///attacker.example", "/"),
        ("/\\attacker.example", "/"),
        ("/%5cattacker.example", "/"),
        ("/%2f%2fattacker.example", "/"),
        ("/%252f%252fattacker.example", "/"),
        ("https://attacker.example", "/"),
        ("javascript:alert(1)", "/"),
        ("/safe/../admin", "/"),
        ("/safe//admin", "/"),
        (" /safe", "/"),
        ("/safe\r\nX-Injected: true", "/"),
    ],
)
def test_login_redirects_allow_only_normalized_same_origin_paths(candidate, expected):
    assert _safe_redirect_after(candidate) == expected


def test_https_redirect_uses_canonical_origin_and_ignores_forwarded_host():
    application = FastAPI()
    settings = Settings(
        environment="production",
        app_secret_key="configured-fernet-key",
        app_public_url="https://expenseops.example",
        trusted_hosts=["expenseops.example"],
        _env_file=None,
    )
    application.add_middleware(SecurityHeadersMiddleware, settings=settings)

    @application.get("/dashboard")
    def dashboard():
        return {"ok": True}

    client = TestClient(application)
    response = client.get(
        "/dashboard?tab=review",
        headers={
            "host": "expenseops.example",
            "x-forwarded-proto": "http",
            "x-forwarded-host": "attacker.example",
        },
        follow_redirects=False,
    )

    assert response.status_code == 308
    assert response.headers["location"] == "https://expenseops.example/dashboard?tab=review"
    assert response.headers["x-content-type-options"] == "nosniff"
    proxied_https = client.get(
        "/dashboard",
        headers={"host": "expenseops.example", "x-forwarded-proto": "https"},
    )
    assert proxied_https.status_code == 200


def test_https_redirect_without_canonical_origin_requires_a_trusted_host():
    application = FastAPI()
    settings = Settings(
        environment="production",
        app_secret_key="configured-fernet-key",
        trusted_hosts=["expenseops.example"],
        _env_file=None,
    )
    application.add_middleware(SecurityHeadersMiddleware, settings=settings)

    @application.get("/dashboard")
    def dashboard():
        return {"ok": True}

    client = TestClient(application)
    allowed = client.get(
        "/dashboard",
        headers={"host": "expenseops.example", "x-forwarded-proto": "http"},
        follow_redirects=False,
    )
    rejected = client.get(
        "/dashboard",
        headers={"host": "attacker.example", "x-forwarded-proto": "http"},
        follow_redirects=False,
    )

    assert allowed.headers["location"] == "https://expenseops.example/dashboard"
    assert rejected.status_code == 400
    assert "attacker.example" not in rejected.text
    assert rejected.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize(
    "invalid_public_url",
    [
        "https://[broken",
        "https://expenseops.example:bad",
        "https://expenseops.example/unexpected-path",
        "https://expenseops.example?unexpected=query",
        "https://expenseops.example#unexpected-fragment",
    ],
)
def test_https_redirect_ignores_malformed_canonical_origin(invalid_public_url):
    application = FastAPI()
    settings = Settings(
        environment="production",
        app_secret_key="configured-fernet-key",
        app_public_url=invalid_public_url,
        trusted_hosts=["trusted.expenseops.example"],
        _env_file=None,
    )
    application.add_middleware(SecurityHeadersMiddleware, settings=settings)

    @application.get("/dashboard")
    def dashboard():
        return {"ok": True}

    response = TestClient(application).get(
        "/dashboard",
        headers={"host": "trusted.expenseops.example", "x-forwarded-proto": "http"},
        follow_redirects=False,
    )

    assert response.status_code == 308
    assert response.headers["location"] == "https://trusted.expenseops.example/dashboard"
    _assert_security_headers(response)


def test_legal_pages_are_public_and_explain_customer_data_controls():
    client = TestClient(app)

    privacy = client.get("/legal/privacy")
    terms = client.get("/legal/terms")

    assert privacy.status_code == 200
    assert "Information we process" in privacy.text
    assert "does not sell" in privacy.text
    assert "Retention and deletion" in privacy.text
    assert "receipt photo or PDF bytes" in privacy.text
    assert "transaction merchant, description, and provider-category evidence" in privacy.text
    assert terms.status_code == 200
    assert "Third-party services" in terms.text
    assert terms.headers["x-content-type-options"] == "nosniff"


def test_readiness_reports_database_and_optional_configuration_without_secrets():
    response = TestClient(app).get("/readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["checks"]["database"] == "ok"
    assert "client_secret" not in str(payload).casefold()
    assert "api_key" not in str(payload).casefold()


def test_readiness_reports_receipt_parser_state_without_secrets(monkeypatch):
    import app.main as main

    monkeypatch.setattr(
        main,
        "settings",
        Settings(
            receipt_parser_provider="openai",
            openai_api_key="configured-secret-key",
            telegram_bot_token="configured-telegram-bot",
            _env_file=None,
        ),
    )

    response = TestClient(app).get("/readiness")

    assert response.status_code == 200
    checks = response.json()["checks"]
    assert checks["receipt_parser_provider"] == "openai"
    assert checks["receipt_parser_configured"] is True
    assert "configured-secret-key" not in str(response.json())


def test_production_readiness_fails_closed_when_active_receipt_parser_is_unavailable(
    monkeypatch,
):
    import app.main as main

    monkeypatch.setattr(
        main,
        "settings",
        _safe_production_settings(
            telegram_bot_token="configured-telegram-bot",
            receipt_parser_provider="fallback",
            openai_api_key="configured-key",
            auth_mode="oidc",
            oidc_issuer="https://identity.example",
            oidc_audience="expenseops",
            oidc_client_id="client-id",
            oidc_redirect_uri="https://expenseops.example/auth/callback",
            trusted_hosts=["testserver"],
        ),
    )

    response = TestClient(app).get("/readiness", headers={"host": "testserver"})

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    checks = response.json()["checks"]
    assert checks["receipt_intake_active"] is True
    assert checks["receipt_parser_configured"] is False


def test_production_readiness_returns_503_when_schema_is_stale(monkeypatch):
    import app.main as main

    monkeypatch.setattr(
        main,
        "settings",
        _safe_production_settings(
            auth_mode="oidc",
            oidc_issuer="https://identity.example",
            oidc_audience="expenseops",
            oidc_client_id="client-id",
            oidc_redirect_uri="https://expenseops.example/auth/callback",
            trusted_hosts=["expenseops.example"],
        ),
    )
    monkeypatch.setattr(
        main.ScriptDirectory,
        "from_config",
        lambda _config: type("Script", (), {"get_current_head": lambda _self: "future-head"})(),
    )

    response = TestClient(app).get("/readiness", headers={"host": "testserver"})

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["migration_current"] is False


def test_dashboard_api_requires_auth_in_production(monkeypatch):
    import app.auth as auth
    import app.main as main

    monkeypatch.setattr(auth, "_auth_not_required", lambda _settings: False)
    monkeypatch.setattr(auth, "_is_authorized", lambda request, _settings: False)

    origin = main.settings.frontend_origin[0]
    response = TestClient(app).get("/api/workspaces", headers={"Origin": origin})

    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["cache-control"] == "no-store"
    _assert_security_headers(response)


def test_cookie_csrf_rejection_receives_security_headers(monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.settings, "auth_mode", "oidc")
    client = TestClient(app)
    client.cookies.set(main.settings.auth_session_cookie_name, "session")

    response = client.post(
        "/api/promotions/sync",
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid request origin"}
    assert response.headers["cache-control"] == "no-store"
    _assert_security_headers(response)


def test_auth_rate_limit_rejection_receives_security_and_cors_headers(monkeypatch):
    import app.auth as auth
    import app.main as main

    monkeypatch.setattr(auth, "_auth_not_required", lambda _settings: True)
    monkeypatch.setattr(
        auth,
        "_default_context",
        lambda _settings: TenantContext(user_id=1, workspace_id=1),
    )

    def reject_rate_limit(*_args, **_kwargs):
        raise HTTPException(status_code=429, detail="Too many requests")

    monkeypatch.setattr(auth.rate_limiter, "check", reject_rate_limit)
    origin = main.settings.frontend_origin[0]

    response = TestClient(app).post(
        "/api/promotions/sync",
        headers={"Origin": origin},
    )

    assert response.status_code == 429
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["cache-control"] == "no-store"
    _assert_security_headers(response)


def test_dashboard_api_allows_basic_auth():
    import app.auth as auth

    settings = _safe_production_settings(
        environment="local",
        dashboard_username="beta",
        dashboard_password="secret",
    )
    credentials = base64.b64encode(b"beta:secret").decode()
    request_headers = {"Authorization": f"Basic {credentials}"}

    assert auth._is_authorized(
        type("Request", (), {"headers": request_headers})(),
        settings,
    )


def test_dashboard_api_allows_bearer_auth():
    import app.auth as auth

    settings = _safe_production_settings(environment="local", dashboard_api_token="token-123")

    assert auth._is_authorized(
        type("Request", (), {"headers": {"Authorization": "Bearer token-123"}})(),
        settings,
    )


def test_production_oidc_mode_rejects_static_dev_token():
    import app.auth as auth

    settings = _safe_production_settings(
        auth_mode="oidc",
        oidc_issuer="https://identity.example",
        oidc_audience="expenseops",
        oidc_client_id="client-id",
        oidc_redirect_uri="https://expenseops.example/auth/callback",
        dashboard_api_token="dev-token",
    )

    assert not auth._is_authorized(
        type("Request", (), {"headers": {"Authorization": "Bearer dev-token"}})(),
        settings,
    )


def test_public_webhook_paths_are_not_auth_protected():
    import app.auth as auth

    for path in ["/telegram/webhook", "/plaid/webhook", "/health", "/readiness"]:
        request = type(
            "Request",
            (),
            {"method": "POST", "url": type("Url", (), {"path": path})()},
        )()
        assert auth._is_public_request(request)


def test_button_mode_routes_keep_valid_financial_actions_with_auth_helpers():
    from app.services.telegram_service import build_button_mode_keyboard

    keyboard = build_button_mode_keyboard(12)

    assert keyboard["inline_keyboard"][0][0]["text"] == "Personal"
    assert keyboard["inline_keyboard"][1][0]["text"] == "Split"


def test_production_cookie_writes_require_allowed_origin():
    import app.auth as auth

    settings = _safe_production_settings(
        auth_mode="oidc",
        oidc_issuer="https://identity.example",
        oidc_audience="expenseops",
        oidc_client_id="client-id",
        oidc_redirect_uri="https://expenseops.example/auth/callback",
        app_public_url="https://expenseops.example",
        frontend_origin=["https://expenseops.example"],
    )

    def request(origin: str):
        return type(
            "Request",
            (),
            {
                "method": "POST",
                "cookies": {settings.auth_session_cookie_name: "session"},
                "headers": {"origin": origin},
                "url": type(
                    "Url",
                    (),
                    {
                        "scheme": "https",
                        "netloc": "expenseops.example",
                    },
                )(),
            },
        )()

    assert auth._csrf_origin_allowed(request("https://expenseops.example"), settings)
    assert not auth._csrf_origin_allowed(request("https://attacker.example"), settings)


def test_app_env_production_disables_unauthenticated_local_fallback(monkeypatch):
    import app.auth as auth

    monkeypatch.setenv("APP_ENV", "production")
    settings = Settings(
        environment="local",
        app_secret_key="configured-fernet-key",
        _env_file=None,
    )

    assert settings.is_production_mode is True
    assert auth._auth_not_required(settings) is False


def test_app_env_production_does_not_hide_default_tenancy_database_failures(monkeypatch):
    import app.auth as auth

    monkeypatch.setenv("APP_ENV", "production")
    settings = Settings(
        environment="local",
        app_secret_key="configured-fernet-key",
        _env_file=None,
    )

    def fail_to_open_session():
        raise OperationalError("SELECT 1", {}, RuntimeError("database unavailable"))

    monkeypatch.setattr(auth, "SessionLocal", fail_to_open_session)

    with pytest.raises(OperationalError):
        auth._default_context(settings)


def test_app_env_production_requires_origin_for_cookie_authenticated_writes(monkeypatch):
    import app.auth as auth

    monkeypatch.setenv("APP_ENV", "production")
    settings = Settings(
        environment="local",
        app_secret_key="configured-fernet-key",
        _env_file=None,
    )
    request = type(
        "Request",
        (),
        {
            "method": "POST",
            "cookies": {settings.auth_session_cookie_name: "session"},
            "headers": {},
            "url": type(
                "Url",
                (),
                {"scheme": "https", "netloc": "expenseops.example"},
            )(),
        },
    )()

    assert auth._csrf_origin_allowed(request, settings) is False

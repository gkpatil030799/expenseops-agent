import pytest

from app.config import Settings


def _safe_production_settings(**overrides):
    values = {
        "environment": "production",
        "app_secret_key": "configured-fernet-key",
        "telegram_webhook_secret": "configured-telegram-secret",
        "telegram_allowed_user_id": "12345",
        "dashboard_api_token": "configured-dashboard-token",
        "plaid_env": "production",
        "allow_unverified_plaid_webhooks_for_local_test": False,
        "auth_mode": "oidc",
        "oidc_issuer": "https://identity.example",
        "oidc_audience": "expenseops",
        "oidc_client_id": "client-id",
        "oidc_redirect_uri": "https://expenseops.example/auth/callback",
        "database_url": "postgresql://expenseops@db.example/expenseops",
        "enable_postgres_rls": True,
        "rate_limit_backend": "postgres",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_frontend_origin_parses_csv():
    settings = Settings(frontend_origin="https://a.example,https://b.example")

    assert settings.frontend_origin == ["https://a.example", "https://b.example"]


def test_admin_user_emails_parse_csv():
    settings = Settings(admin_user_emails="a@example.test,b@example.test")

    assert settings.admin_user_emails == ["a@example.test", "b@example.test"]


@pytest.mark.parametrize("scheme", ["postgres://", "postgresql://"])
def test_database_url_uses_installed_psycopg3_driver(scheme):
    settings = Settings(database_url=f"{scheme}user:secret@db.example/expenseops")

    assert settings.database_url.startswith("postgresql+psycopg://")


def test_docs_disabled_by_default_in_production():
    settings = _safe_production_settings(enable_docs=False)

    assert settings.docs_enabled is False


def test_docs_can_be_enabled_in_production():
    settings = _safe_production_settings(enable_docs=True)

    assert settings.docs_enabled is True


def test_production_config_rejects_missing_telegram_secret():
    settings = _safe_production_settings(telegram_webhook_secret="")

    with pytest.raises(ValueError, match="TELEGRAM_WEBHOOK_SECRET"):
        settings.validate_web_runtime()


def test_production_config_rejects_local_plaid_webhook_bypass():
    with pytest.raises(ValueError, match="ALLOW_UNVERIFIED_PLAID_WEBHOOKS"):
        _safe_production_settings(allow_unverified_plaid_webhooks_for_local_test=True)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"database_url": "sqlite:///unsafe.db"}, "DATABASE_URL"),
        ({"enable_postgres_rls": False}, "ENABLE_POSTGRES_RLS"),
        ({"rate_limit_backend": "memory"}, "RATE_LIMIT_BACKEND"),
    ],
)
def test_production_config_requires_shared_database_security(override, message):
    settings = _safe_production_settings(**override)
    with pytest.raises(ValueError, match=message):
        settings.validate_web_runtime()


def test_production_config_rejects_enabled_sandbox_lab(monkeypatch):
    monkeypatch.setenv("ENABLE_EXPENSEOPS_SANDBOX_LAB", "true")

    settings = _safe_production_settings()

    with pytest.raises(ValueError, match="ENABLE_EXPENSEOPS_SANDBOX_LAB"):
        settings.validate_web_runtime()


def test_production_config_rejects_local_auth_mode():
    settings = _safe_production_settings(auth_mode="local")

    with pytest.raises(ValueError, match="AUTH_MODE"):
        settings.validate_web_runtime()


def test_production_worker_does_not_require_any_web_only_settings():
    settings = _safe_production_settings(
        auth_mode="local",
        oidc_issuer="",
        oidc_audience="",
        oidc_client_id="",
        oidc_redirect_uri="",
        telegram_webhook_secret="",
        plaid_verify_webhooks=False,
    )

    settings.validate_worker_runtime()

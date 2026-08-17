import pytest
from pydantic import ValidationError

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
        "support_email": "support@example.com",
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


def test_unified_agent_capabilities_default_safely_off():
    settings = Settings(_env_file=None)

    assert settings.agent_enabled is False
    assert settings.agent_read_tools_enabled is False
    assert settings.agent_write_actions_enabled is False
    assert settings.agent_proactive_enabled is False
    assert settings.agent_purchasing_enabled is False


def test_production_rejects_agent_purchasing_rollout():
    with pytest.raises(ValidationError, match="disabled in production"):
        _safe_production_settings(agent_purchasing_enabled=True)


def test_proactive_attention_requires_the_read_agent_in_every_environment():
    with pytest.raises(ValidationError, match="requires Agent and read tools"):
        Settings(_env_file=None, agent_proactive_enabled=True)


def test_production_may_prepare_proactive_read_only_attention_behind_its_flag():
    settings = _safe_production_settings(
        agent_enabled=True,
        agent_read_tools_enabled=True,
        agent_proactive_enabled=True,
    )

    assert settings.agent_proactive_enabled is True
    assert settings.agent_purchasing_enabled is False


def test_production_allows_confirmation_gated_agent_write_actions():
    settings = _safe_production_settings(agent_write_actions_enabled=True)

    assert settings.agent_write_actions_enabled is True


def test_settings_repr_redacts_credentials_and_private_identifiers():
    sentinel = "UNIT_TEST_CREDENTIAL_MUST_NOT_BE_RENDERED"
    settings = Settings(
        database_url=f"postgresql://expenseops:{sentinel}@db.example/expenseops",
        app_secret_key=sentinel,
        app_secret_key_previous=[sentinel],
        dashboard_username=sentinel,
        dashboard_password=sentinel,
        dashboard_api_token=sentinel,
        oidc_client_id=sentinel,
        oidc_client_secret=sentinel,
        oidc_bootstrap_email=sentinel,
        admin_user_emails=[sentinel],
        plaid_client_id=sentinel,
        plaid_secret=sentinel,
        splitwise_api_key=sentinel,
        splitwise_access_token=sentinel,
        splitwise_consumer_key=sentinel,
        splitwise_consumer_secret=sentinel,
        splitwise_oauth_token=sentinel,
        splitwise_oauth_token_secret=sentinel,
        telegram_bot_token=sentinel,
        telegram_chat_id=sentinel,
        telegram_webhook_secret=sentinel,
        telegram_allowed_user_id=sentinel,
        openai_api_key=sentinel,
        gmail_client_id=sentinel,
        gmail_client_secret=sentinel,
        gmail_refresh_token=sentinel,
        gmail_user_id=sentinel,
        household_base_location=sentinel,
        google_maps_api_key=sentinel,
        _env_file=None,
    )

    assert sentinel not in repr(settings)
    assert settings.openai_api_key == sentinel
    assert sentinel in settings.database_url


def test_settings_validation_errors_hide_sensitive_input():
    sentinel = "UNIT_TEST_INVALID_CREDENTIAL_MUST_NOT_BE_RENDERED"

    with pytest.raises(ValidationError) as caught:
        Settings(openai_api_key={"credential": sentinel}, _env_file=None)

    assert sentinel not in str(caught.value)
    assert sentinel not in repr(caught.value)


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


def test_app_env_production_disables_docs_when_environment_field_is_local(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    settings = Settings(
        environment="local",
        app_secret_key="configured-fernet-key",
        enable_docs=False,
        _env_file=None,
    )

    assert settings.is_production_mode is True
    assert settings.docs_enabled is False


def test_app_env_production_keeps_database_initialization_migration_managed(monkeypatch):
    import app.db as app_db
    import app.tenancy as tenancy

    monkeypatch.setenv("APP_ENV", "production")
    settings = Settings(
        environment="local",
        app_secret_key="configured-fernet-key",
        _env_file=None,
    )
    create_all_calls = []
    bootstrap_calls = []
    monkeypatch.setattr(app_db, "settings", settings)
    monkeypatch.setattr(
        app_db.Base.metadata,
        "create_all",
        lambda **kwargs: create_all_calls.append(kwargs),
    )
    monkeypatch.setattr(
        tenancy,
        "ensure_default_tenancy",
        lambda db: bootstrap_calls.append(db),
    )

    app_db.init_db()

    assert create_all_calls == []
    assert bootstrap_calls == []


def test_production_config_rejects_missing_telegram_secret():
    settings = _safe_production_settings(telegram_webhook_secret="")

    with pytest.raises(ValueError, match="TELEGRAM_WEBHOOK_SECRET"):
        settings.validate_web_runtime()


def test_production_config_requires_openai_key_when_read_agent_is_enabled():
    settings = _safe_production_settings(
        agent_enabled=True,
        agent_read_tools_enabled=True,
        openai_api_key="",
    )

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
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


def test_production_config_requires_a_real_support_address():
    settings = _safe_production_settings(support_email="support@expenseops.invalid")

    with pytest.raises(ValueError, match="SUPPORT_EMAIL"):
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


def test_production_migration_settings_do_not_require_application_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    settings = Settings(
        database_url="postgresql://expenseops_migrator:test@db/expenseops",
        app_secret_key="",
        app_secret_key_version="",
    )

    assert settings.is_production_mode


@pytest.mark.parametrize("runtime_validator", ["validate_web_runtime", "validate_worker_runtime"])
def test_production_runtimes_require_application_secret(monkeypatch, runtime_validator):
    monkeypatch.setenv("APP_ENV", "production")
    settings = Settings(
        database_url="postgresql://expenseops_runtime:test@db/expenseops",
        app_secret_key="",
        app_secret_key_version="",
        enable_postgres_rls=True,
        rate_limit_backend="postgres",
    )

    with pytest.raises(ValueError, match="APP_SECRET_KEY"):
        getattr(settings, runtime_validator)()

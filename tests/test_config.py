import pytest
from pydantic import ValidationError

from app.config import Settings


def _safe_production_settings(**overrides):
    values = {
        "environment": "production",
        "app_secret_key": "configured-fernet-key",
        "telegram_webhook_secret": "configured-telegram-secret",
        "telegram_allowed_user_id": "12345",
        "plaid_env": "production",
        "allow_unverified_plaid_webhooks_for_local_test": False,
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_frontend_origin_parses_csv():
    settings = Settings(frontend_origin="https://a.example,https://b.example")

    assert settings.frontend_origin == ["https://a.example", "https://b.example"]


def test_docs_disabled_by_default_in_production():
    settings = _safe_production_settings(enable_docs=False)

    assert settings.docs_enabled is False


def test_docs_can_be_enabled_in_production():
    settings = _safe_production_settings(enable_docs=True)

    assert settings.docs_enabled is True


def test_production_config_rejects_missing_telegram_secret():
    with pytest.raises(ValidationError, match="TELEGRAM_WEBHOOK_SECRET"):
        _safe_production_settings(telegram_webhook_secret="")


def test_production_config_rejects_local_plaid_webhook_bypass():
    with pytest.raises(ValidationError, match="ALLOW_UNVERIFIED_PLAID_WEBHOOKS"):
        _safe_production_settings(allow_unverified_plaid_webhooks_for_local_test=True)


def test_production_config_rejects_enabled_sandbox_lab(monkeypatch):
    monkeypatch.setenv("ENABLE_EXPENSEOPS_SANDBOX_LAB", "true")

    with pytest.raises(ValidationError, match="ENABLE_EXPENSEOPS_SANDBOX_LAB"):
        _safe_production_settings()

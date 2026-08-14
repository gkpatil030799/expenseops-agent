from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _csv(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return value
    return [part.strip() for part in value.split(",") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ExpenseOps Agent"
    environment: Literal["local", "production"] = "local"
    enable_docs: bool = False
    frontend_origin: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    database_url: str = "sqlite:///./expenseops.db"
    app_secret_key: str = ""
    app_secret_key_version: str = "v1"
    app_secret_key_previous: Annotated[list[str], NoDecode] = Field(default_factory=list)
    trusted_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver"]
    )
    enforce_https: bool = True
    support_email: str = "support@expenseops.invalid"
    dashboard_username: str = ""
    dashboard_password: str = ""
    dashboard_api_token: str = ""
    app_public_url: str = ""
    auth_mode: Literal["local", "oidc"] = "local"
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = ""
    oidc_scopes: str = "openid profile email"
    oidc_algorithms: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["RS256"])
    oidc_bootstrap_email: str = ""
    auth_session_cookie_name: str = "expenseops_session"
    auth_session_hours: int = Field(default=168, ge=1, le=720)
    admin_user_emails: Annotated[list[str], NoDecode] = Field(default_factory=list)
    rate_limit_backend: Literal["memory", "postgres"] = "memory"
    enable_postgres_rls: bool = False
    database_pool_size: int = Field(default=5, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: int = Field(default=15, ge=1, le=120)
    database_pool_recycle_seconds: int = Field(default=900, ge=30, le=86400)
    database_statement_timeout_ms: int = Field(default=15_000, ge=1000, le=300_000)
    database_lock_timeout_ms: int = Field(default=5_000, ge=100, le=60_000)
    retention_auth_session_days: int = Field(default=30, ge=1, le=365)
    retention_webhook_days: int = Field(default=30, ge=1, le=365)
    retention_completed_outbox_days: int = Field(default=30, ge=1, le=365)
    retention_promotion_message_days: int = Field(default=180, ge=30, le=730)
    retention_ignored_receipt_days: int = Field(default=365, ge=30, le=2555)
    retention_audit_event_days: int = Field(default=2555, ge=365, le=3650)

    allow_posting_pending_transactions: bool = False

    plaid_client_id: str = ""
    plaid_secret: str = ""
    plaid_env: Literal["sandbox", "development", "production"] = "sandbox"
    plaid_webhook_url: str = ""
    plaid_country_codes: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["US"])
    plaid_products: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["transactions"])
    plaid_days_requested: int = 30
    plaid_verify_webhooks: bool = False
    plaid_verify_webhooks_in_sandbox: bool = False
    allow_unverified_plaid_webhooks_for_local_test: bool = False

    splitwise_base_url: str = "https://secure.splitwise.com/api/v3.0"
    splitwise_api_key: str = ""
    splitwise_access_token: str = ""
    splitwise_auth_scheme: str = "Bearer"
    splitwise_consumer_key: str = ""
    splitwise_consumer_secret: str = ""
    splitwise_oauth_token: str = ""
    splitwise_oauth_token_secret: str = ""
    splitwise_oauth_callback_url: str = ""

    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    telegram_chat_id: str = ""
    telegram_webhook_secret: str = ""
    telegram_allowed_user_id: str = ""

    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"

    receipt_parser_provider: Literal["fallback", "openai"] = "fallback"
    receipt_parser_model: str = "gpt-4.1-mini"
    receipt_max_attachment_bytes: int = Field(default=10_000_000, ge=100_000, le=25_000_000)
    receipt_auto_match_confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    receipt_possible_match_confidence: float = Field(default=0.65, ge=0.0, le=1.0)
    gmail_receipt_sync_enabled: bool = False
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    gmail_user_id: str = "me"
    gmail_receipt_query: str = (
        "newer_than:30d (subject:(receipt OR order OR purchase) "
        "OR from:(walmart.com target.com costco.com instacart.com amazon.com))"
    )
    promotions_enabled: bool = False
    promotions_initial_lookback_days: int = Field(default=30, ge=1, le=365)
    promotions_max_messages_per_sync: int = Field(default=100, ge=1, le=500)
    promotions_llm_fallback_enabled: bool = True
    promotions_max_llm_body_chars: int = Field(default=12000, ge=1000, le=50000)
    promotions_min_score: float = Field(default=50.0, ge=0.0, le=100.0)
    promotions_digest_enabled: bool = False
    promotions_digest_cadence: Literal["daily", "weekly"] = "weekly"
    promotions_digest_max_deals: int = Field(default=8, ge=1, le=20)
    promotions_digest_timezone: str = "UTC"
    promotions_digest_local_hour: int = Field(default=17, ge=0, le=23)
    promotions_sync_schedule: str = "0 */6 * * *"
    gmail_push_enabled: bool = False
    gmail_pubsub_topic: str = ""
    replenishment_ml_min_rows: int = Field(default=30, ge=10, le=10000)
    replenishment_ml_min_validation_rows: int = Field(default=8, ge=3, le=1000)
    replenishment_walk_forward_min_rows: int = Field(default=60, ge=20, le=10000)
    replenishment_model_min_mae_improvement_pct: float = Field(default=10.0, ge=0.0, le=100.0)
    replenishment_model_min_mae_improvement_days: float = Field(default=1.0, ge=0.0, le=365.0)
    replenishment_max_feedback_cadence_adjustment_pct: float = Field(default=25.0, ge=0.0, le=100.0)
    replenishment_weekly_schedule: str = "0 9 * * 0"

    household_base_location: str = ""
    household_snooze_days: int = Field(default=7, ge=1, le=90)
    household_routing_provider: Literal["fallback", "google_maps"] = "fallback"
    household_place_search_provider: Literal["fallback", "google_places"] = "fallback"
    google_maps_api_key: str = ""
    household_max_incremental_minutes: int = Field(default=10, ge=0, le=120)
    household_probably_due_incremental_minutes: int = Field(default=5, ge=0, le=60)
    household_place_candidates_per_errand: int = Field(default=3, ge=1, le=8)
    household_max_place_combinations: int = Field(default=27, ge=1, le=200)
    household_preferred_place_bias_minutes: int = Field(default=3, ge=0, le=30)
    household_provider_cache_ttl_seconds: int = Field(default=900, ge=0, le=86400)

    @field_validator(
        "frontend_origin",
        "plaid_country_codes",
        "plaid_products",
        "oidc_algorithms",
        "admin_user_emails",
        "app_secret_key_previous",
        "trusted_hosts",
        mode="before",
    )
    @classmethod
    def parse_csv(cls, value: str | list[str]) -> list[str]:
        return _csv(value)

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    @model_validator(mode="after")
    def validate_production_safety(self) -> Settings:
        if not self.is_production_mode:
            return self

        errors: list[str] = []
        if not self.app_secret_key or self.app_secret_key == "paste-a-generated-fernet-key-here":
            errors.append("APP_SECRET_KEY must be configured for production.")
        if not self.app_secret_key_version.strip():
            errors.append("APP_SECRET_KEY_VERSION must be configured for production.")
        if self.allow_unverified_plaid_webhooks_for_local_test:
            errors.append(
                "ALLOW_UNVERIFIED_PLAID_WEBHOOKS_FOR_LOCAL_TEST must be false in production."
            )
        if errors:
            raise ValueError("Unsafe production configuration: " + " ".join(errors))
        return self

    def validate_web_runtime(self) -> None:
        """Validate settings used only by the public HTTP application."""
        if not self.is_production_mode:
            return

        errors: list[str] = []
        errors.extend(self._production_database_errors())
        if not self.trusted_hosts or "*" in self.trusted_hosts:
            errors.append("TRUSTED_HOSTS must explicitly list production hosts.")
        if not self.enforce_https:
            errors.append("ENFORCE_HTTPS must be true in production.")
        if not self.support_email.strip() or self.support_email.endswith(".invalid"):
            errors.append("SUPPORT_EMAIL must be a monitored address in production.")
        if not self.telegram_webhook_secret:
            errors.append("TELEGRAM_WEBHOOK_SECRET must be configured for production.")
        if self.auth_mode != "oidc":
            errors.append("AUTH_MODE must be oidc in production.")
        if not all(
            [
                self.oidc_issuer,
                self.oidc_audience,
                self.oidc_client_id,
                self.oidc_redirect_uri,
            ]
        ):
            errors.append(
                "OIDC_ISSUER, OIDC_AUDIENCE, OIDC_CLIENT_ID, and OIDC_REDIRECT_URI "
                "must be configured for production."
            )
        if _env_bool("ENABLE_EXPENSEOPS_SANDBOX_LAB"):
            errors.append("ENABLE_EXPENSEOPS_SANDBOX_LAB must be false for production deploys.")
        if not self.plaid_webhook_verification_required:
            errors.append("Plaid webhook verification must be enabled for production.")
        if errors:
            raise ValueError("Unsafe production web configuration: " + " ".join(errors))

    def validate_worker_runtime(self) -> None:
        if not self.is_production_mode:
            return
        errors = self._production_database_errors()
        if errors:
            raise ValueError("Unsafe production worker configuration: " + " ".join(errors))

    def _production_database_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.database_url.startswith("postgresql"):
            errors.append("DATABASE_URL must use PostgreSQL in production.")
        if not self.enable_postgres_rls:
            errors.append("ENABLE_POSTGRES_RLS must be true in production.")
        if self.rate_limit_backend != "postgres":
            errors.append("RATE_LIMIT_BACKEND must be postgres in production.")
        return errors

    @property
    def is_production_mode(self) -> bool:
        app_env = os.environ.get("APP_ENV", "").strip().lower()
        return app_env == "production" or self.environment.strip().lower() == "production"

    @property
    def docs_enabled(self) -> bool:
        return self.environment != "production" or self.enable_docs

    @property
    def uses_splitwise_oauth1(self) -> bool:
        return all(
            [
                self.splitwise_consumer_key,
                self.splitwise_consumer_secret,
                self.splitwise_oauth_token,
                self.splitwise_oauth_token_secret,
            ]
        )

    @property
    def has_splitwise_oauth1_consumer(self) -> bool:
        return bool(self.splitwise_consumer_key and self.splitwise_consumer_secret)

    @property
    def plaid_webhook_verification_required(self) -> bool:
        return (
            self.plaid_env == "production"
            or self.plaid_verify_webhooks
            or (self.plaid_env == "sandbox" and self.plaid_verify_webhooks_in_sandbox)
        )

    @property
    def allow_plaid_webhook_verification_bypass_for_local_test(self) -> bool:
        app_env = os.environ.get("APP_ENV", "").strip().lower()
        environment = self.environment.strip().lower()
        if app_env == "production" or environment == "production":
            return False
        return (
            self.plaid_env == "production"
            and self.allow_unverified_plaid_webhooks_for_local_test
            and (app_env == "local" or environment == "local")
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}

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
    dashboard_username: str = ""
    dashboard_password: str = ""
    dashboard_api_token: str = ""

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
    telegram_chat_id: str = ""
    telegram_webhook_secret: str = ""
    telegram_allowed_user_id: str = ""

    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"

    @field_validator("frontend_origin", "plaid_country_codes", "plaid_products", mode="before")
    @classmethod
    def parse_csv(cls, value: str | list[str]) -> list[str]:
        return _csv(value)

    @model_validator(mode="after")
    def validate_production_safety(self) -> Settings:
        if not self.is_production_mode:
            return self

        errors: list[str] = []
        if not self.app_secret_key or self.app_secret_key == "paste-a-generated-fernet-key-here":
            errors.append("APP_SECRET_KEY must be configured for production.")
        if not self.telegram_webhook_secret:
            errors.append("TELEGRAM_WEBHOOK_SECRET must be configured for production.")
        if not self.telegram_allowed_user_id:
            errors.append("TELEGRAM_ALLOWED_USER_ID must be configured for production.")
        if self.allow_unverified_plaid_webhooks_for_local_test:
            errors.append(
                "ALLOW_UNVERIFIED_PLAID_WEBHOOKS_FOR_LOCAL_TEST must be false in production."
            )
        if _env_bool("ENABLE_EXPENSEOPS_SANDBOX_LAB"):
            errors.append("ENABLE_EXPENSEOPS_SANDBOX_LAB must be false for production deploys.")
        if not self.plaid_webhook_verification_required:
            errors.append("Plaid webhook verification must be enabled for production.")
        if errors:
            raise ValueError("Unsafe production configuration: " + " ".join(errors))
        return self

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

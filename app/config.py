from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _csv(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return value
    return [part.strip() for part in value.split(",") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ExpenseOps Agent"
    environment: str = "local"
    database_url: str = "sqlite:///./expenseops.db"
    app_secret_key: str = ""

    allow_posting_pending_transactions: bool = False

    plaid_client_id: str = ""
    plaid_secret: str = ""
    plaid_env: Literal["sandbox", "development", "production"] = "sandbox"
    plaid_webhook_url: str = ""
    plaid_country_codes: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["US"])
    plaid_products: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["transactions"])
    plaid_days_requested: int = 30
    plaid_verify_webhooks: bool = False

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

    @field_validator("plaid_country_codes", "plaid_products", mode="before")
    @classmethod
    def parse_csv(cls, value: str | list[str]) -> list[str]:
        return _csv(value)

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import get_settings

SANDBOX_ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = SANDBOX_ROOT / "state" / "sandbox_state.local.json"
EVENT_LOG_PATH = SANDBOX_ROOT / "logs" / "sandbox_events.jsonl"
SCENARIO_RUN_LOG_PATH = SANDBOX_ROOT / "logs" / "scenario_runs.jsonl"
SCENARIOS_PATH = SANDBOX_ROOT / "scenarios"


class SandboxSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    enable_expenseops_sandbox_lab: bool = False
    sandbox_public_webhook_url: str = ""
    sandbox_scenario_run_all_delay_seconds: int = 8

    @property
    def app_settings(self):
        return get_settings()

    @property
    def plaid_env(self) -> str:
        return self.app_settings.plaid_env

    @property
    def webhook_url(self) -> str:
        return (
            self.app_settings.plaid_webhook_url
            or self.sandbox_public_webhook_url
        ).strip()

    @property
    def enabled(self) -> bool:
        return self.enable_expenseops_sandbox_lab

    @property
    def sandbox_ready(self) -> bool:
        return self.enabled and self.plaid_env == "sandbox"


@lru_cache(maxsize=1)
def get_sandbox_settings() -> SandboxSettings:
    return SandboxSettings()

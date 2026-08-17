from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent.contracts import AgentStructuredResponse

AttentionCategory = Literal[
    "transactions",
    "receipts",
    "integrations",
    "replenishment",
    "deals",
    "errands",
]


class AttentionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AttentionPreferenceOut(AttentionModel):
    enabled: bool
    categories: list[AttentionCategory]
    in_app_enabled: bool
    telegram_enabled: bool
    delivery_mode: Literal["immediate", "digest"]
    quiet_start_hour: int = Field(ge=0, le=23)
    quiet_end_hour: int = Field(ge=0, le=23)
    timezone: str = Field(min_length=1, max_length=64)
    max_alerts_per_day: int = Field(ge=1, le=10)
    cooldown_minutes: int = Field(ge=15, le=1_440)

    @field_validator("categories")
    @classmethod
    def unique_categories(cls, values: list[AttentionCategory]) -> list[AttentionCategory]:
        if not values or len(values) != len(set(values)):
            raise ValueError("categories must be non-empty and unique")
        return values


class AttentionPreferencePatch(AttentionModel):
    enabled: bool | None = None
    categories: list[AttentionCategory] | None = None
    in_app_enabled: bool | None = None
    telegram_enabled: bool | None = None
    delivery_mode: Literal["immediate", "digest"] | None = None
    quiet_start_hour: int | None = Field(default=None, ge=0, le=23)
    quiet_end_hour: int | None = Field(default=None, ge=0, le=23)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    max_alerts_per_day: int | None = Field(default=None, ge=1, le=10)
    cooldown_minutes: int | None = Field(default=None, ge=15, le=1_440)

    @field_validator("categories")
    @classmethod
    def unique_categories(
        cls, values: list[AttentionCategory] | None
    ) -> list[AttentionCategory] | None:
        if values is not None and (not values or len(values) != len(set(values))):
            raise ValueError("categories must be non-empty and unique")
        return values


class AttentionCenterOut(AttentionModel):
    enabled: bool
    generated_at: datetime | None
    response: AgentStructuredResponse | None
    preferences: AttentionPreferenceOut


class AttentionDeliveryOut(AttentionModel):
    status: Literal[
        "sent",
        "skipped_disabled",
        "skipped_channel_disabled",
        "skipped_channel_unavailable",
        "skipped_delivery_mode",
        "skipped_quiet_hours",
        "skipped_duplicate",
        "skipped_cooldown",
        "skipped_daily_cap",
        "skipped_empty",
        "failed",
    ]
    attention_count: int = Field(ge=0)

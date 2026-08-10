from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PromotionSyncRequest(BaseModel):
    max_results: int | None = Field(default=None, ge=1, le=500)


class PromotionFeedbackRequest(BaseModel):
    feedback_type: Literal[
        "saved",
        "dismissed",
        "not_relevant",
        "used",
        "wrong_category",
        "wrong_expiry",
        "mute_merchant",
        "more_like_this",
        "less_like_this",
    ]
    metadata: dict = Field(default_factory=dict)


class PromotionSettingsPatch(BaseModel):
    preferred_categories: list[str] | None = None
    muted_categories: list[str] | None = None
    preferred_merchants: list[str] | None = None
    muted_merchants: list[str] | None = None
    minimum_score: float | None = Field(default=None, ge=0, le=100)
    maximum_deals_per_digest: int | None = Field(default=None, ge=1, le=20)
    digest_cadence: Literal["daily", "weekly"] | None = None
    digest_time: int | None = Field(default=None, ge=0, le=23)
    timezone: str | None = None
    include_minimum_spend: bool | None = None

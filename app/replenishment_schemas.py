from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ReceiptLineOut(BaseModel):
    id: int
    raw_name: str
    normalized_name: str
    quantity: float | None
    unit: str | None
    line_total_cents: int | None
    household_item_id: int | None
    household_item_name: str | None
    acquisition_id: int | None
    match_status: str
    match_confidence: float | None


class ReceiptOut(BaseModel):
    id: int
    source: str
    merchant: str | None
    purchased_at: datetime | None
    total_cents: int | None
    currency: str
    parse_status: str
    parse_confidence: float | None
    failure_code: str | None
    transaction_id: int | None
    created_at: datetime
    items: list[ReceiptLineOut]


class ReceiptLineMatchRequest(BaseModel):
    household_item_id: int | None = None
    rejected: bool = False


class FeedbackRequest(BaseModel):
    feedback_type: Literal["still_have", "skipped", "too_early", "too_late", "correct"]
    prediction_id: int | None = None
    metadata: dict = Field(default_factory=dict)


class AcquisitionCorrectionRequest(BaseModel):
    household_item_id: int | None = None
    acquired_at: datetime | None = None
    quantity: float | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, max_length=64)


class WeeklyRunRequest(BaseModel):
    run_key: str | None = Field(default=None, min_length=3, max_length=100)


class GmailSyncRequest(BaseModel):
    max_results: int = Field(default=25, ge=1, le=100)

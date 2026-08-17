from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ReceiptLineClassificationValue = Literal[
    "replenishable_household",
    "perishable_grocery",
    "routine_consumption",
    "dining_or_experience",
    "one_time_purchase",
    "non_product_line",
    "uncertain",
]


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
    classification: ReceiptLineClassificationValue | None
    classification_confidence: float | None = Field(default=None, ge=0, le=1)
    canonical_name: str | None


class ReceiptDecisionSummary(BaseModel):
    tracked: int
    ignored: int
    undecided: int
    total: int


class ReceiptOut(BaseModel):
    id: int
    source: str
    merchant: str | None
    purchased_at: datetime | None
    total_cents: int | None
    currency: str
    parse_status: str
    parse_quality: Literal["complete", "partial", "failed"]
    quality_message: str | None
    parse_confidence: float | None
    failure_code: str | None
    transaction_id: int | None
    created_at: datetime
    updated_at: datetime
    decision_summary: ReceiptDecisionSummary
    items: list[ReceiptLineOut]


class ReceiptLineMatchRequest(BaseModel):
    household_item_id: int | None = None
    rejected: bool = False


class ReceiptLineNewHouseholdItemRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    cadence_days: int | None = Field(default=None, ge=1, le=3650)
    replenishment_mode: Literal["errand", "delivery", "either"] = "either"


class ReceiptLineDecisionRequest(BaseModel):
    line_id: int = Field(..., ge=1)
    decision: Literal["match", "unmatched", "reject", "create"]
    household_item_id: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    cadence_days: int | None = Field(default=None, ge=1, le=3650)
    replenishment_mode: Literal["errand", "delivery", "either"] = "either"


class ReceiptBatchDecisionRequest(BaseModel):
    expected_updated_at: datetime
    decisions: list[ReceiptLineDecisionRequest] = Field(default_factory=list, max_length=500)
    finalize: Literal["save", "confirm"] = "save"
    acknowledge_undecided: bool = False


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

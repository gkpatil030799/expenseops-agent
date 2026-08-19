from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReviewInboxModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewTransactionSummary(ReviewInboxModel):
    id: int
    merchant_name: str | None
    name: str
    amount_cents: int
    currency: str
    date: date | None
    pending: bool
    status: str
    institution_name: str | None


class ReviewReceiptSummary(ReviewInboxModel):
    id: int
    merchant_name: str | None
    total_cents: int | None
    currency: str
    purchased_at: datetime | None
    parse_status: str
    transaction_match_status: str
    transaction_id: int | None
    line_count: int = Field(ge=0, le=250)


class ReviewRecommendation(ReviewInboxModel):
    suggestion: Literal["likely_personal", "likely_shared"]
    reason: str = Field(min_length=1, max_length=500)
    memory_id: int = Field(gt=0)
    participant_names: list[str] = Field(default_factory=list, max_length=8)
    group_name: str | None = Field(default=None, max_length=255)
    split_mode: str | None = Field(default=None, max_length=64)


class ReviewItemOut(ReviewInboxModel):
    public_id: str = Field(min_length=36, max_length=36)
    kind: Literal[
        "transaction_review",
        "itemized_split_ready",
        "receipt_match_needed",
        "financial_reconciliation",
    ]
    state: Literal["open", "resolved", "stale"]
    unread: bool
    seen_at: datetime | None
    created_at: datetime
    updated_at: datetime
    available_actions: list[
        Literal[
            "personal",
            "recommended_split",
            "customize",
            "itemized_split",
            "open_receipt",
            "open_receipt_match",
        ]
    ] = Field(max_length=6)
    transaction: ReviewTransactionSummary | None
    receipt: ReviewReceiptSummary | None
    recommendation: ReviewRecommendation | None

    @model_validator(mode="after")
    def validate_semantics(self) -> ReviewItemOut:
        if self.unread != (self.seen_at is None):
            raise ValueError("unread must agree with seen_at")
        if self.kind == "transaction_review":
            if self.transaction is None or self.receipt is not None:
                raise ValueError("transaction review requires exactly one transaction")
        elif self.kind in {"itemized_split_ready", "receipt_match_needed"}:
            if self.receipt is None or self.transaction is not None:
                raise ValueError("receipt review requires exactly one receipt")
            if self.recommendation is not None:
                raise ValueError("receipt review cannot carry a transaction recommendation")
        return self


class ReviewInboxPageOut(ReviewInboxModel):
    items: list[ReviewItemOut] = Field(max_length=100)
    total_open: int = Field(ge=0)
    unread_count: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> ReviewInboxPageOut:
        if self.unread_count > self.total_open:
            raise ValueError("unread_count cannot exceed total_open")
        if len(self.items) > self.total_open:
            raise ValueError("page rows cannot exceed total_open")
        if any(item.state != "open" for item in self.items):
            raise ValueError("the open inbox cannot include closed rows")
        return self


class ReviewInboxBadgeOut(ReviewInboxModel):
    open_count: int = Field(ge=0)
    unread_count: int = Field(ge=0)


class ReviewItemSeenOut(ReviewInboxModel):
    public_id: str
    seen_at: datetime

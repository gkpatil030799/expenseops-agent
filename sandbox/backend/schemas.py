from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class StepStatus(BaseModel):
    name: str
    status: Literal["success", "fallback", "unknown", "failed"]
    detail: str | None = None


class SandboxStatusResponse(BaseModel):
    enabled: bool
    plaid_env: str
    webhook_url_configured: bool
    webhook_url: str | None = None
    state_exists: bool
    item_id: str | None = None
    item_db_id: int | None = None
    access_token_exists: bool
    access_token_redacted: str | None = None
    transactions_cursor_exists: bool
    latest_event_timestamp: str | None = None
    latest_known_trace_id: str | None = None


class CreateItemResponse(BaseModel):
    trace_id: str
    item_id: str
    access_token: str
    webhook_url: str
    steps: list[StepStatus]


class InitSyncResponse(BaseModel):
    trace_id: str
    item_id: str | None
    added_count: int
    modified_count: int
    removed_count: int
    has_cursor: bool
    cursor_present: bool
    cursor_updated: bool
    next_cursor_present: bool


class CreateTransactionRequest(BaseModel):
    description: str = "ExpenseOps Sandbox Coffee"
    amount: Decimal = Decimal("12.34")
    iso_currency_code: str = "USD"
    date_transacted: date | None = None
    date_posted: date | None = None
    auto_fire_webhook: bool = False
    auto_sync_after: bool = False

    @field_validator("iso_currency_code")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class CreateTransactionResponse(BaseModel):
    trace_id: str
    transaction: dict[str, Any]
    plaid_request_id: str | None = None
    created: bool
    steps: list[StepStatus]


class FireWebhookRequest(BaseModel):
    webhook_type: str = "TRANSACTIONS"
    webhook_code: str = "SYNC_UPDATES_AVAILABLE"
    trace_id: str | None = None


class FireWebhookResponse(BaseModel):
    trace_id: str
    webhook_type: str
    webhook_code: str
    webhook_fired: bool
    plaid_request_id: str | None = None


class SyncNowRequest(BaseModel):
    trace_id: str | None = None


class SyncNowResponse(BaseModel):
    trace_id: str
    added_count: int
    modified_count: int
    removed_count: int
    cursor_updated: bool
    cursor_present: bool
    next_cursor_present: bool
    added_transactions: list[dict[str, Any]] = Field(default_factory=list)


class RunE2EResponse(BaseModel):
    trace_id: str
    status: Literal["completed", "partial", "failed"]
    steps: list[StepStatus]
    details: dict[str, Any] = Field(default_factory=dict)


class EventsResponse(BaseModel):
    events: list[dict[str, Any]]


class ResetEventsResponse(BaseModel):
    cleared: bool

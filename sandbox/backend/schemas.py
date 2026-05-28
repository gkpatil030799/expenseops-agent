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


class ScenarioTransaction(BaseModel):
    description: str
    amount: Decimal
    iso_currency_code: str = "USD"
    date_transacted: date | None = None
    date_posted: date | None = None

    @field_validator("iso_currency_code", mode="before")
    @classmethod
    def normalize_scenario_currency(cls, value: str | None) -> str:
        return (value or "USD").upper()


class ScenarioExpectations(BaseModel):
    transaction_created: bool | None = None
    webhook_fired: bool | None = None
    webhook_received: bool | None = None
    sync_completed: bool | None = None
    imported_transaction_visible: bool | None = None
    review_needed_visible: bool | None = None
    telegram_sent_min: int | None = None
    telegram_sent_max: int | None = None
    no_integrity_error: bool | None = None
    no_loop_guard_triggered: bool | None = None
    no_boundary_violation: bool | None = None


class ScenarioDefinition(BaseModel):
    id: str
    name: str
    description: str
    flow: Literal["create_only", "manual_sync", "webhook", "e2e"]
    transaction: ScenarioTransaction | None = None
    expectations: ScenarioExpectations = Field(default_factory=ScenarioExpectations)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True


class ScenarioAssertionResult(BaseModel):
    name: str
    status: Literal["passed", "failed", "skipped"]
    expected: Any = None
    actual: Any = None
    message: str = ""


class ScenarioResult(BaseModel):
    scenario_id: str
    scenario_name: str
    scenario_run_id: str
    trace_id: str
    status: Literal["passed", "failed", "partial", "error"]
    started_at: str
    completed_at: str
    duration_ms: int
    flow: Literal["create_only", "manual_sync", "webhook", "e2e"]
    transaction_summary: dict[str, Any] = Field(default_factory=dict)
    assertions: list[ScenarioAssertionResult] = Field(default_factory=list)
    events_summary: dict[str, Any] = Field(default_factory=dict)
    raw_events: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None
    error_details: dict[str, Any] = Field(default_factory=dict)


class ScenarioRunsResponse(BaseModel):
    results: list[ScenarioResult]


class ScenarioRunAggregateResponse(BaseModel):
    status: Literal["passed", "failed", "partial", "error"]
    total: int
    passed: int
    failed: int
    partial: int
    errors: int
    rate_limit_errors: int = 0
    passed_count: int = 0
    failed_count: int = 0
    error_count: int = 0
    rate_limit_error_count: int = 0
    results: list[ScenarioResult]


class ReliabilityTransaction(BaseModel):
    description: str
    amount: Decimal
    iso_currency_code: str = "USD"
    date_transacted: date | None = None
    date_posted: date | None = None

    @field_validator("iso_currency_code", mode="before")
    @classmethod
    def normalize_reliability_currency(cls, value: str | None) -> str:
        return (value or "USD").upper()


class ReliabilityExpectations(BaseModel):
    telegram_sent_max: int | None = None
    no_integrity_error: bool | None = None
    sync_attempts_bounded: bool | None = None
    no_loop_runaway: bool | None = None
    loop_guard_triggered: bool | None = None
    webhook_received: bool | None = None
    webhook_timeout_reported: bool | None = None
    failure_logged: bool | None = None
    cursor_not_corrupted: bool | None = None
    no_duplicate_transaction: bool | None = None


class ReliabilityDefinition(BaseModel):
    id: str
    name: str
    description: str
    type: Literal[
        "duplicate_webhook",
        "repeated_manual_sync",
        "concurrent_sync",
        "webhook_observation_timeout",
        "telegram_failure_simulation",
        "plaid_sync_failure_simulation",
        "cursor_missing_recovery",
        "loop_guard",
    ]
    transaction: ReliabilityTransaction | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    expectations: ReliabilityExpectations = Field(default_factory=ReliabilityExpectations)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    tags: list[str] = Field(default_factory=list)
    uses_real_plaid: bool = True
    uses_telegram: bool = False
    uses_fault_injection: bool = False
    enabled: bool = True


class ReliabilityAssertionResult(BaseModel):
    name: str
    status: Literal["passed", "failed", "skipped"]
    expected: Any = None
    actual: Any = None
    message: str = ""


class ReliabilityResult(BaseModel):
    reliability_run_id: str
    test_id: str
    test_name: str
    trace_id: str
    status: Literal["passed", "failed", "partial", "error"]
    started_at: str
    completed_at: str
    duration_ms: int
    assertions: list[ReliabilityAssertionResult] = Field(default_factory=list)
    event_summary: dict[str, Any] = Field(default_factory=dict)
    raw_events: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None
    error_details: dict[str, Any] = Field(default_factory=dict)


class ReliabilityRunsResponse(BaseModel):
    results: list[ReliabilityResult]


class ReliabilityRunAggregateResponse(BaseModel):
    status: Literal["passed", "failed", "partial", "error"]
    total: int
    passed: int
    failed: int
    partial: int
    errors: int
    rate_limit_errors: int = 0
    passed_count: int = 0
    failed_count: int = 0
    partial_count: int = 0
    error_count: int = 0
    rate_limit_error_count: int = 0
    results: list[ReliabilityResult]

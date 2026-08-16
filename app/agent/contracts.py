from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AGENT_CONTRACT_VERSION = "1.0"


class StrictAgentModel(BaseModel):
    """Base for contracts shared by the web and future native clients."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class AgentCapabilities(StrictAgentModel):
    schema_version: Literal["1.0"] = AGENT_CONTRACT_VERSION
    enabled: bool = False
    read_tools_enabled: bool = False
    write_actions_enabled: bool = False
    proactive_enabled: bool = False
    purchasing_enabled: bool = False


class AgentConversationCreate(StrictAgentModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)


class AgentMessageCreate(StrictAgentModel):
    text: str = Field(min_length=1, max_length=4_000)
    client_message_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


class AgentSurface(StrEnum):
    HOME = "home"
    EXPENSE_REVIEW = "expense_review"
    EXPENSE_INSIGHTS = "expense_insights"
    EXPENSE_ACTIVITY = "expense_activity"
    HOUSEHOLD_TODAY = "household_today"
    HOUSEHOLD_ERRANDS = "household_errands"
    HOUSEHOLD_RECEIPTS = "household_receipts"
    HOUSEHOLD_STAPLES = "household_staples"
    HOUSEHOLD_HISTORY = "household_history"
    DEALS = "deals"
    SETTINGS = "settings"
    INTEGRATIONS = "integrations"


class AgentPageFilters(StrictAgentModel):
    start_date: date | None = None
    end_date: date | None = None
    date_preset: str | None = Field(default=None, min_length=1, max_length=32)
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    merchant: str | None = Field(default=None, min_length=1, max_length=255)
    status: str | None = Field(default=None, min_length=1, max_length=64)
    currency_code: str | None = Field(
        default=None,
        min_length=3,
        max_length=8,
        pattern=r"^[A-Za-z]{3,8}$",
    )
    spend_basis: Literal["card", "actual_share"] | None = None
    query: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_date_range(self) -> AgentPageFilters:
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class AgentPageEntity(StrictAgentModel):
    kind: Literal[
        "transaction",
        "deal",
        "receipt",
        "errand",
        "household_item",
        "integration",
    ]
    public_id: str = Field(min_length=1, max_length=128)


class AgentPageContext(StrictAgentModel):
    schema_version: Literal["1.0"] = AGENT_CONTRACT_VERSION
    surface: AgentSurface
    filters: AgentPageFilters = Field(default_factory=AgentPageFilters)
    entity: AgentPageEntity | None = None


class AgentTurnCreate(StrictAgentModel):
    text: str = Field(min_length=1, max_length=4_000)
    client_message_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    page_context: AgentPageContext | None = None


class AgentResponseBlockBase(StrictAgentModel):
    block_id: str | None = Field(default=None, min_length=1, max_length=100)


class AgentTextBlock(AgentResponseBlockBase):
    type: Literal["text"] = "text"
    text: str = Field(min_length=1, max_length=8_000)


class AgentTransactionSummary(StrictAgentModel):
    public_id: str = Field(min_length=1, max_length=128)
    merchant: str = Field(min_length=1, max_length=255)
    amount_cents: int
    currency_code: str = Field(min_length=3, max_length=8, pattern=r"^[A-Za-z]{3,8}$")
    occurred_on: date | None = None
    category: str | None = Field(default=None, max_length=255)
    status: str = Field(min_length=1, max_length=64)
    pending: bool = False


class AgentTransactionListBlock(AgentResponseBlockBase):
    type: Literal["transaction_list"] = "transaction_list"
    title: str = Field(min_length=1, max_length=160)
    transactions: list[AgentTransactionSummary] = Field(default_factory=list, max_length=50)
    total_count: int = Field(ge=0)


class AgentSpendingBreakdownItem(StrictAgentModel):
    name: str = Field(min_length=1, max_length=255)
    amount_cents: int
    transaction_count: int = Field(ge=0)
    percentage: float = Field(ge=0, le=100)
    previous_amount_cents: int | None = None


class AgentSpendingSummaryBlock(AgentResponseBlockBase):
    type: Literal["spending_summary"] = "spending_summary"
    title: str = Field(min_length=1, max_length=160)
    start_date: date
    end_date: date
    currency_code: str = Field(min_length=3, max_length=8, pattern=r"^[A-Za-z]{3,8}$")
    total_cents: int
    previous_total_cents: int | None = None
    change_percent: float | None = None
    highlights: list[str] = Field(default_factory=list, max_length=10)
    top_categories: list[AgentSpendingBreakdownItem] = Field(default_factory=list, max_length=10)
    top_merchants: list[AgentSpendingBreakdownItem] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_date_range(self) -> AgentSpendingSummaryBlock:
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class AgentReplenishmentItem(StrictAgentModel):
    public_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    predicted_due_on: date | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_level: Literal["insufficient", "low", "medium", "high"] = "insufficient"
    evidence_basis: Literal[
        "configured_cadence",
        "purchase_pattern",
        "validated_model",
        "insufficient_history",
    ] = "insufficient_history"
    due_state: Literal["likely_due", "probably_due", "not_due", "learning"] = "learning"
    reason: str | None = Field(default=None, min_length=1, max_length=500)
    quantity: str | None = Field(default=None, min_length=1, max_length=64)
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    last_acquired_on: date | None = None
    confirmed_acquisition_count: int = Field(default=0, ge=0)


class AgentAcquisitionSummary(StrictAgentModel):
    acquired_on: date
    merchant: str | None = Field(default=None, min_length=1, max_length=255)
    quantity: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    evidence_type: Literal["manual", "receipt", "transaction", "imported", "correction"]


class AgentReplenishmentSummaryBlock(AgentResponseBlockBase):
    type: Literal["replenishment_summary"] = "replenishment_summary"
    title: str = Field(min_length=1, max_length=160)
    # Keep the original v1.0 acceptance bound for persisted historical messages;
    # current read tools still emit at most 20 rows.
    items: list[AgentReplenishmentItem] = Field(default_factory=list, max_length=50)
    acquisition_history: list[AgentAcquisitionSummary] = Field(default_factory=list, max_length=20)
    acquisition_history_truncated: bool = False
    total_count: int = Field(default=0, ge=0)
    items_truncated: bool = False

    @model_validator(mode="after")
    def preserve_legacy_item_count(self) -> AgentReplenishmentSummaryBlock:
        self.total_count = max(self.total_count, len(self.items))
        return self


class AgentDealSummary(StrictAgentModel):
    public_id: str = Field(min_length=1, max_length=128)
    merchant: str = Field(min_length=1, max_length=255)
    headline: str = Field(min_length=1, max_length=500)
    expires_at: datetime | None = None
    score: float | None = Field(default=None, ge=0, le=100)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    offer_type: str | None = Field(default=None, min_length=1, max_length=32)
    percent_off: float | None = Field(default=None, ge=0, le=100)
    amount_off_cents: int | None = Field(default=None, ge=0)
    currency_code: str | None = Field(
        default=None,
        min_length=3,
        max_length=8,
        pattern=r"^[A-Za-z]{3,8}$",
    )
    minimum_spend_cents: int | None = Field(default=None, ge=0)
    promo_code: str | None = Field(default=None, min_length=1, max_length=128)
    trust_status: Literal["trusted", "review"] = "review"
    saved: bool = False
    relevant_to_need: bool = False
    relevance_reasons: list[str] = Field(default_factory=list, max_length=5)


class AgentDealListBlock(AgentResponseBlockBase):
    type: Literal["deal_list"] = "deal_list"
    title: str = Field(min_length=1, max_length=160)
    deals: list[AgentDealSummary] = Field(default_factory=list, max_length=50)
    total_count: int = Field(ge=0)


class AgentReceiptLineSummary(StrictAgentModel):
    name: str = Field(min_length=1, max_length=500)
    quantity: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    line_total_cents: int | None = None
    match_status: Literal["matched", "possible", "unmatched", "ignored"] = "unmatched"
    household_item_name: str | None = Field(default=None, min_length=1, max_length=255)
    confirmed_acquisition: bool = False


class AgentReceiptSummaryBlock(AgentResponseBlockBase):
    type: Literal["receipt_summary"] = "receipt_summary"
    public_id: str = Field(min_length=1, max_length=128)
    merchant: str | None = Field(default=None, min_length=1, max_length=255)
    purchased_at: datetime | None = None
    ingested_at: datetime | None = None
    total_cents: int | None = None
    currency_code: str = Field(
        default="USD",
        min_length=3,
        max_length=8,
        pattern=r"^[A-Za-z]{3,8}$",
    )
    status: str = Field(min_length=1, max_length=64)
    transaction_linked: bool = False
    matched_line_count: int = Field(default=0, ge=0)
    ignored_line_count: int = Field(default=0, ge=0)
    unmatched_line_count: int = Field(default=0, ge=0)
    total_line_count: int = Field(default=0, ge=0)
    # v1.0 previously allowed 100 lines. New tool output remains capped at 25.
    items: list[AgentReceiptLineSummary] = Field(default_factory=list, max_length=100)
    items_truncated: bool = False

    @model_validator(mode="after")
    def preserve_legacy_line_count(self) -> AgentReceiptSummaryBlock:
        self.total_line_count = max(self.total_line_count, len(self.items))
        return self


class AgentErrandItem(StrictAgentModel):
    public_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    status: str = Field(min_length=1, max_length=64)
    priority: str = Field(default="normal", min_length=1, max_length=32)
    errand_type: str = Field(default="other", min_length=1, max_length=32)
    due_on: date | None = None
    place_name: str | None = Field(default=None, min_length=1, max_length=255)
    place_resolution_status: str = Field(default="unresolved", min_length=1, max_length=32)
    included_in_next_plan: bool = False
    household_items: list[str] = Field(default_factory=list, max_length=20)


class AgentErrandPlanStop(StrictAgentModel):
    order: int = Field(ge=1)
    place_name: str = Field(min_length=1, max_length=255)
    errands: list[str] = Field(default_factory=list, max_length=20)
    errands_truncated: bool = False
    household_items: list[str] = Field(default_factory=list, max_length=20)
    household_items_truncated: bool = False


class AgentErrandPlanSummary(StrictAgentModel):
    public_id: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=32)
    planned_for: datetime | None = None
    is_stale: bool
    stale_reason: str | None = Field(default=None, min_length=1, max_length=255)
    estimated_stop_minutes: int = Field(default=0, ge=0)
    travel_duration_minutes: int | None = Field(default=None, ge=0)
    distance_meters: int | None = Field(default=None, ge=0)
    stops: list[AgentErrandPlanStop] = Field(default_factory=list, max_length=12)
    total_stop_count: int = Field(default=0, ge=0)
    stops_truncated: bool = False

    @model_validator(mode="after")
    def preserve_stop_count(self) -> AgentErrandPlanSummary:
        self.total_stop_count = max(self.total_stop_count, len(self.stops))
        return self


class AgentErrandSummaryBlock(AgentResponseBlockBase):
    type: Literal["errand_summary"] = "errand_summary"
    title: str = Field(min_length=1, max_length=160)
    # Preserve the original v1.0 persisted-payload bound; new output is capped at 25.
    errands: list[AgentErrandItem] = Field(default_factory=list, max_length=50)
    total_count: int = Field(default=0, ge=0)
    errands_truncated: bool = False
    plan: AgentErrandPlanSummary | None = None

    @model_validator(mode="after")
    def preserve_legacy_errand_count(self) -> AgentErrandSummaryBlock:
        self.total_count = max(self.total_count, len(self.errands))
        return self


class AgentIntegrationStatusItem(StrictAgentModel):
    # Provider was an arbitrary bounded string in v1.0. The live status tool has a
    # closed enum, while this response contract remains able to hydrate old rows.
    provider: str = Field(min_length=1, max_length=64)
    scope: Literal["personal", "workspace", "application"] | None = None
    status: Literal[
        "connected",
        "ready",
        "attention_required",
        "disconnected",
        "disabled",
        "unavailable",
    ]
    message: str | None = Field(default=None, min_length=1, max_length=500)
    last_successful_sync_at: datetime | None = None


class AgentIntegrationStatusBlock(AgentResponseBlockBase):
    type: Literal["integration_status"] = "integration_status"
    title: str = Field(min_length=1, max_length=160)
    integrations: list[AgentIntegrationStatusItem] = Field(default_factory=list, max_length=25)


class AgentNavigationBlock(AgentResponseBlockBase):
    type: Literal["navigation"] = "navigation"
    label: str = Field(min_length=1, max_length=120)
    target_surface: AgentSurface
    entity: AgentPageEntity | None = None


class AgentLabelValue(StrictAgentModel):
    label: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=500)


class AgentActionPreview(StrictAgentModel):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1_000)
    details: list[AgentLabelValue] = Field(default_factory=list, max_length=25)
    confirm_label: str = Field(default="Confirm", min_length=1, max_length=80)
    cancel_label: str = Field(default="Cancel", min_length=1, max_length=80)


class AgentActionConfirmationBlock(AgentActionPreview):
    block_id: str | None = Field(default=None, min_length=1, max_length=100)
    type: Literal["action_confirmation"] = "action_confirmation"
    proposal_id: str = Field(min_length=1, max_length=128)
    proposal_version: int = Field(ge=1)
    status: Literal[
        "awaiting_confirmation",
        "confirmed",
        "executing",
        "completed",
        "cancelled",
        "expired",
        "failed",
        "ambiguous",
    ]
    expires_at: datetime


class AgentErrorBlock(AgentResponseBlockBase):
    type: Literal["error"] = "error"
    code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=1_000)
    retryable: bool = False


class AgentEmptyStateBlock(AgentResponseBlockBase):
    type: Literal["empty"] = "empty"
    title: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=1_000)
    suggested_navigation: AgentNavigationBlock | None = None


AgentResponseBlock = Annotated[
    AgentTextBlock
    | AgentTransactionListBlock
    | AgentSpendingSummaryBlock
    | AgentReplenishmentSummaryBlock
    | AgentDealListBlock
    | AgentReceiptSummaryBlock
    | AgentErrandSummaryBlock
    | AgentIntegrationStatusBlock
    | AgentNavigationBlock
    | AgentActionConfirmationBlock
    | AgentErrorBlock
    | AgentEmptyStateBlock,
    Field(discriminator="type"),
]


class AgentStructuredResponse(StrictAgentModel):
    schema_version: Literal["1.0"] = AGENT_CONTRACT_VERSION
    blocks: list[AgentResponseBlock] = Field(min_length=1, max_length=50)


class AgentConversationOut(StrictAgentModel):
    public_id: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AgentMessageOut(StrictAgentModel):
    public_id: str = Field(min_length=1, max_length=128)
    conversation_public_id: str = Field(min_length=1, max_length=128)
    role: Literal["user", "assistant"]
    text: str | None = Field(default=None, min_length=1, max_length=8_000)
    structured_response: AgentStructuredResponse | None = None
    client_message_id: str | None = Field(default=None, min_length=1, max_length=64)
    created_at: datetime

    @model_validator(mode="after")
    def validate_content(self) -> AgentMessageOut:
        if self.text is None and self.structured_response is None:
            raise ValueError("message must include text or a structured response")
        return self


class AgentRunOut(StrictAgentModel):
    public_id: str = Field(min_length=1, max_length=128)
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    model_name: str | None = Field(default=None, min_length=1, max_length=128)
    prompt_version: str | None = Field(default=None, min_length=1, max_length=64)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    error_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AgentTurnOut(StrictAgentModel):
    schema_version: Literal["1.0"] = AGENT_CONTRACT_VERSION
    run: AgentRunOut
    user_message: AgentMessageOut
    assistant_message: AgentMessageOut


class AgentStreamEventBase(StrictAgentModel):
    """Platform-owned stream envelope shared by web and future native clients."""

    schema_version: Literal["1.0"] = AGENT_CONTRACT_VERSION
    sequence: int = Field(ge=0)
    run_public_id: str | None = Field(default=None, min_length=1, max_length=128)


class AgentRunStartedEvent(AgentStreamEventBase):
    type: Literal["run_started"] = "run_started"
    resumed: bool = False


class AgentAssistantDeltaEvent(AgentStreamEventBase):
    # Stream fragments are the one contract where boundary whitespace is data:
    # stripping it would corrupt the canonical persisted answer when chunks are
    # joined by web or native clients.
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=False,
        allow_inf_nan=False,
    )

    type: Literal["assistant_delta"] = "assistant_delta"
    delta: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_visible_delta(self) -> AgentAssistantDeltaEvent:
        if not self.delta.strip():
            raise ValueError("delta must include visible text")
        return self


class AgentToolStartedEvent(AgentStreamEventBase):
    type: Literal["tool_started"] = "tool_started"
    activity: Literal[
        "spending",
        "transactions",
        "replenishment",
        "receipts",
        "deals",
        "errands",
        "integrations",
    ]
    message: str = Field(min_length=1, max_length=160)


class AgentToolCompletedEvent(AgentStreamEventBase):
    type: Literal["tool_completed"] = "tool_completed"
    activity: Literal[
        "spending",
        "transactions",
        "replenishment",
        "receipts",
        "deals",
        "errands",
        "integrations",
    ]
    message: str = Field(min_length=1, max_length=160)


class AgentStructuredResponseEvent(AgentStreamEventBase):
    type: Literal["structured_response"] = "structured_response"
    response: AgentStructuredResponse


class AgentAssistantCompletedEvent(AgentStreamEventBase):
    type: Literal["assistant_completed"] = "assistant_completed"
    message: AgentMessageOut


class AgentRunCompletedEvent(AgentStreamEventBase):
    type: Literal["run_completed"] = "run_completed"
    run: AgentRunOut


class AgentRunFailedEvent(AgentStreamEventBase):
    type: Literal["run_failed"] = "run_failed"
    run: AgentRunOut | None = None
    code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(min_length=1, max_length=1_000)
    retryable: bool = False


AgentStreamEvent = Annotated[
    AgentRunStartedEvent
    | AgentAssistantDeltaEvent
    | AgentToolStartedEvent
    | AgentToolCompletedEvent
    | AgentStructuredResponseEvent
    | AgentAssistantCompletedEvent
    | AgentRunCompletedEvent
    | AgentRunFailedEvent,
    Field(discriminator="type"),
]


class AgentConversationDetail(StrictAgentModel):
    conversation: AgentConversationOut
    messages: list[AgentMessageOut] = Field(default_factory=list, max_length=500)
    messages_total: int = Field(ge=0)
    messages_offset: int = Field(ge=0)
    messages_has_more: bool

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
    reason: str | None = Field(default=None, min_length=1, max_length=500)


class AgentReplenishmentSummaryBlock(AgentResponseBlockBase):
    type: Literal["replenishment_summary"] = "replenishment_summary"
    title: str = Field(min_length=1, max_length=160)
    items: list[AgentReplenishmentItem] = Field(default_factory=list, max_length=50)


class AgentDealSummary(StrictAgentModel):
    public_id: str = Field(min_length=1, max_length=128)
    merchant: str = Field(min_length=1, max_length=255)
    headline: str = Field(min_length=1, max_length=500)
    expires_at: datetime | None = None
    score: float | None = Field(default=None, ge=0, le=100)


class AgentDealListBlock(AgentResponseBlockBase):
    type: Literal["deal_list"] = "deal_list"
    title: str = Field(min_length=1, max_length=160)
    deals: list[AgentDealSummary] = Field(default_factory=list, max_length=50)
    total_count: int = Field(ge=0)


class AgentReceiptLineSummary(StrictAgentModel):
    name: str = Field(min_length=1, max_length=255)
    quantity: float | None = Field(default=None, ge=0)
    line_total_cents: int | None = None


class AgentReceiptSummaryBlock(AgentResponseBlockBase):
    type: Literal["receipt_summary"] = "receipt_summary"
    public_id: str = Field(min_length=1, max_length=128)
    merchant: str | None = Field(default=None, min_length=1, max_length=255)
    purchased_at: datetime | None = None
    total_cents: int | None = None
    currency_code: str = Field(
        default="USD",
        min_length=3,
        max_length=8,
        pattern=r"^[A-Za-z]{3,8}$",
    )
    status: str = Field(min_length=1, max_length=64)
    items: list[AgentReceiptLineSummary] = Field(default_factory=list, max_length=100)


class AgentErrandItem(StrictAgentModel):
    public_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    status: str = Field(min_length=1, max_length=64)
    due_on: date | None = None
    place_name: str | None = Field(default=None, min_length=1, max_length=255)


class AgentErrandSummaryBlock(AgentResponseBlockBase):
    type: Literal["errand_summary"] = "errand_summary"
    title: str = Field(min_length=1, max_length=160)
    errands: list[AgentErrandItem] = Field(default_factory=list, max_length=50)


class AgentIntegrationStatusItem(StrictAgentModel):
    provider: str = Field(min_length=1, max_length=64)
    status: Literal["connected", "attention_required", "disconnected", "unavailable"]
    message: str | None = Field(default=None, min_length=1, max_length=500)


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


class AgentConversationDetail(StrictAgentModel):
    conversation: AgentConversationOut
    messages: list[AgentMessageOut] = Field(default_factory=list, max_length=500)
    messages_total: int = Field(ge=0)
    messages_offset: int = Field(ge=0)
    messages_has_more: bool

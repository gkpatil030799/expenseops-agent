from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select

from app.agent.deals_errands_tools import register_deals_errands_tools
from app.agent.household_receipt_tools import register_household_receipt_tools
from app.agent.integration_read_tool import register_integration_read_tool
from app.agent.tooling import AgentTool, AgentToolContext, AgentToolRegistry, ToolEffect
from app.config import Settings
from app.models import ExpenseTransaction, TransactionStatus
from app.services.agent_service import transaction_display_name
from app.services.lifestyle_dining_service import LifestyleDiningService
from app.services.spending_insights_service import SpendingInsightsService

MAX_DATE_RANGE_DAYS = 730
MAX_SPENDING_BREAKDOWN_ITEMS = 10
MAX_NOTABLE_CHANGES = 4
MAX_AVAILABLE_CURRENCIES = 16
MAX_TRANSACTION_RESULTS = 25
MAX_TRANSACTION_ENTITY_ID = 2_147_483_647

_SHARED_TRANSACTION_STATUSES = (
    TransactionStatus.SHARED_DRAFT.value,
    TransactionStatus.POSTED.value,
)
_SEARCHABLE_TRANSACTION_STATUSES = frozenset(
    status.value for status in TransactionStatus if status is not TransactionStatus.REMOVED
)


class ReadToolModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class SpendingInsightsInput(ReadToolModel):
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    merchant: str | None = Field(default=None, min_length=1, max_length=255)
    review_type: Literal["all", "personal", "shared"] | None = Field(
        default=None,
        description=(
            "Use null unless the user explicitly requests a review scope; the server may apply "
            "the current page scope."
        ),
    )
    spend_basis: Literal["card", "actual_share"] | None = Field(
        default=None,
        description=(
            "Use null unless the user explicitly requests a spend basis; the server may apply "
            "the current page basis."
        ),
    )
    currency_code: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Za-z]{3}$",
    )
    comparison_mode: Literal["same_weekdays_last_week"] | None = Field(
        default=None,
        description=(
            "Server-owned weekly comparison scope. Use null; ExpenseOps may set the closed "
            "same-weekdays-last-week mode for an exact supported comparison request."
        ),
    )

    @model_validator(mode="after")
    def validate_range(self) -> SpendingInsightsInput:
        start, end = self.date_range()
        _validate_date_range(start, end)
        return self

    def date_range(self) -> tuple[date, date]:
        return date.fromisoformat(self.start_date), date.fromisoformat(self.end_date)


class SpendingAggregate(ReadToolModel):
    total_cents: int = Field(ge=0)
    personal_cents: int = Field(ge=0)
    shared_cents: int = Field(ge=0)
    classified_cents: int = Field(ge=0)
    unreviewed_cents: int = Field(ge=0)
    credits_cents: int = Field(ge=0)
    unknown_share_transactions: int = Field(ge=0)
    unknown_credit_share_transactions: int = Field(ge=0)
    transaction_count: int = Field(ge=0)
    average_cents: int = Field(ge=0)


class SpendingBreakdownItem(ReadToolModel):
    name: str = Field(min_length=1, max_length=255)
    amount_cents: int = Field(ge=0)
    transaction_count: int = Field(ge=0)
    percentage: float = Field(ge=0, le=100)
    previous_amount_cents: int | None = Field(default=None, ge=0)


class SpendingChange(ReadToolModel):
    kind: Literal["category", "merchant", "mix"]
    direction: Literal["up", "down", "neutral"]
    label: str = Field(min_length=1, max_length=255)
    amount_cents: int
    detail: str = Field(min_length=1, max_length=500)


class SpendingInsightsOutput(ReadToolModel):
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    previous_start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    previous_end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    currency_code: str = Field(min_length=3, max_length=8, pattern=r"^[A-Z]{3,8}$")
    spend_basis: Literal["card", "actual_share"]
    comparison_mode: Literal["immediately_preceding", "same_weekdays_last_week"]
    summary: SpendingAggregate
    comparison: SpendingAggregate
    categories: list[SpendingBreakdownItem] = Field(max_length=MAX_SPENDING_BREAKDOWN_ITEMS)
    merchants: list[SpendingBreakdownItem] = Field(max_length=MAX_SPENDING_BREAKDOWN_ITEMS)
    notable_changes: list[SpendingChange] = Field(max_length=MAX_NOTABLE_CHANGES)
    available_currencies: list[str] = Field(max_length=MAX_AVAILABLE_CURRENCIES)
    excluded_other_currency_transactions: int = Field(ge=0)
    pending_transactions_excluded: bool

    @model_validator(mode="after")
    def validate_credit_share_quality(self) -> SpendingInsightsOutput:
        if self.spend_basis == "card" and (
            self.summary.unknown_share_transactions
            or self.comparison.unknown_share_transactions
            or self.summary.unknown_credit_share_transactions
            or self.comparison.unknown_credit_share_transactions
        ):
            raise ValueError("card-basis amounts cannot have unknown share allocations")
        return self


class LifestyleDiningInput(ReadToolModel):
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    activity_type: Literal["all", "coffee", "restaurants", "delivery", "nightlife"] | None = None
    merchant: str | None = Field(default=None, min_length=1, max_length=255)
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    review_type: Literal["all", "personal", "shared"] | None = None
    spend_basis: Literal["card", "actual_share"] | None = None
    currency_code: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Za-z]{3}$",
    )
    include_comparison: bool = True

    @model_validator(mode="after")
    def validate_range(self) -> LifestyleDiningInput:
        _validate_date_range(date.fromisoformat(self.start_date), date.fromisoformat(self.end_date))
        return self


class LifestyleAggregate(ReadToolModel):
    total_cents: int = Field(ge=0)
    personal_cents: int = Field(ge=0)
    shared_cents: int = Field(ge=0)
    unreviewed_cents: int = Field(ge=0)
    credits_cents: int = Field(ge=0)
    transaction_count: int = Field(ge=0)
    average_cents: int = Field(ge=0)
    unknown_share_transactions: int = Field(ge=0)
    unknown_credit_share_transactions: int = Field(ge=0)
    weekday_cents: int = Field(ge=0)
    weekday_count: int = Field(ge=0)
    weekend_cents: int = Field(ge=0)
    weekend_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_reconciliation(self) -> LifestyleAggregate:
        if self.total_cents != self.personal_cents + self.shared_cents + self.unreviewed_cents:
            raise ValueError("lifestyle spend components must reconcile to total_cents")
        if self.total_cents != self.weekday_cents + self.weekend_cents:
            raise ValueError("weekday and weekend spend must reconcile to total_cents")
        if self.transaction_count != self.weekday_count + self.weekend_count:
            raise ValueError("weekday and weekend counts must reconcile")
        return self


class LifestyleBreakdownItem(ReadToolModel):
    name: str = Field(min_length=1, max_length=255)
    amount_cents: int = Field(ge=0)
    transaction_count: int = Field(ge=0)
    percentage: float = Field(ge=0, le=100)


class LifestyleDiningOutput(ReadToolModel):
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    previous_start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    previous_end_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    activity_type: Literal["all", "coffee", "restaurants", "delivery", "nightlife"]
    currency_code: str = Field(min_length=3, max_length=8, pattern=r"^[A-Z]{3,8}$")
    spend_basis: Literal["card", "actual_share"]
    summary: LifestyleAggregate
    comparison: LifestyleAggregate | None
    activities: list[LifestyleBreakdownItem] = Field(max_length=4)
    top_merchants: list[LifestyleBreakdownItem] = Field(max_length=8)
    uncertain_transaction_count: int = Field(ge=0)
    previous_uncertain_transaction_count: int = Field(ge=0)
    observations: list[str] = Field(max_length=6)
    available_currencies: list[str] = Field(max_length=16)
    excluded_other_currency_transactions: int = Field(ge=0)
    pending_transactions_excluded: bool

    @model_validator(mode="after")
    def validate_basis_quality(self) -> LifestyleDiningOutput:
        aggregates = [self.summary, *([self.comparison] if self.comparison else [])]
        if self.spend_basis == "card" and any(
            value.unknown_share_transactions or value.unknown_credit_share_transactions
            for value in aggregates
        ):
            raise ValueError("card-basis lifestyle amounts cannot have unknown share allocations")
        if (self.comparison is None) != (self.previous_start_date is None):
            raise ValueError("comparison dates must match comparison presence")
        if (self.comparison is None) != (self.previous_end_date is None):
            raise ValueError("comparison dates must match comparison presence")
        return self


class TransactionSearchInput(ReadToolModel):
    transaction_id: int | None = Field(
        default=None,
        ge=1,
        le=MAX_TRANSACTION_ENTITY_ID,
    )
    merchant: str | None = Field(default=None, min_length=1, max_length=255)
    start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    category: str | None = Field(default=None, min_length=1, max_length=100)
    review_type: Literal["all", "personal", "shared", "unreviewed"] | None = Field(
        default=None,
        description=(
            "Use null unless the user explicitly requests a review scope; the server may apply "
            "the current page scope."
        ),
    )
    review_status: str | None = Field(default=None, min_length=1, max_length=32)
    min_amount_cents: int | None = None
    max_amount_cents: int | None = None
    currency_code: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Za-z]{3}$",
    )
    include_pending: bool = True
    limit: int = Field(default=20, ge=1, le=MAX_TRANSACTION_RESULTS)

    @model_validator(mode="after")
    def validate_filters(self) -> TransactionSearchInput:
        start = date.fromisoformat(self.start_date) if self.start_date else None
        end = date.fromisoformat(self.end_date) if self.end_date else None
        if start and end:
            _validate_date_range(start, end)
        if (
            self.min_amount_cents is not None
            and self.max_amount_cents is not None
            and self.min_amount_cents > self.max_amount_cents
        ):
            raise ValueError("min_amount_cents must not exceed max_amount_cents")
        if self.review_status and self.review_status not in _SEARCHABLE_TRANSACTION_STATUSES:
            raise ValueError("review_status is not supported")
        if self.review_status and self.review_type not in {None, "all"}:
            raise ValueError("review_status and review_type cannot both be specified")
        return self


class TransactionSearchItem(ReadToolModel):
    public_id: str = Field(min_length=1, max_length=128)
    merchant: str = Field(min_length=1, max_length=255)
    occurred_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    amount_cents: int
    currency_code: str = Field(min_length=3, max_length=8, pattern=r"^[A-Z]{3,8}$")
    category: str | None = Field(default=None, max_length=255)
    status: str = Field(min_length=1, max_length=32)
    pending: bool


class TransactionSearchOutput(ReadToolModel):
    transactions: list[TransactionSearchItem] = Field(max_length=MAX_TRANSACTION_RESULTS)
    total_count: int = Field(ge=0)
    result_limit: int = Field(ge=1, le=MAX_TRANSACTION_RESULTS)
    truncated: bool


def build_read_tool_registry(settings: Settings) -> AgentToolRegistry:
    registry = AgentToolRegistry(settings)
    registry.register(
        AgentTool(
            name="get_spending_insights",
            description=(
                "Return canonical ExpenseOps eligible purchase-spend totals with credits "
                "reported separately, comparable-period totals, top categories, top merchants, "
                "and deterministic notable changes for an explicit date range."
            ),
            effect=ToolEffect.READ,
            input_model=SpendingInsightsInput,
            output_model=SpendingInsightsOutput,
            handler=_get_spending_insights,
            version="1.2",
        )
    )
    registry.register(
        AgentTool(
            name="get_lifestyle_dining_insights",
            description=(
                "Return canonical read-only coffee, restaurant, food-delivery, or reliably "
                "categorized nightlife purchase frequency and spend for an explicit date range. "
                "Use this for lifestyle/dining behavior questions, not household replenishment."
            ),
            effect=ToolEffect.READ,
            input_model=LifestyleDiningInput,
            output_model=LifestyleDiningOutput,
            handler=_get_lifestyle_dining_insights,
            version="1.0",
        )
    )
    registry.register(
        AgentTool(
            name="search_transactions",
            description=(
                "Return bounded authenticated ExpenseOps transaction rows when the latest user "
                "message explicitly asks to list, find, show, or identify transaction/charge "
                "rows, or use unreviewed scope for expense attention."
            ),
            effect=ToolEffect.READ,
            input_model=TransactionSearchInput,
            output_model=TransactionSearchOutput,
            handler=_search_transactions,
        )
    )
    register_household_receipt_tools(registry)
    register_deals_errands_tools(registry, settings)
    register_integration_read_tool(registry, settings)
    return registry


def _get_spending_insights(
    context: AgentToolContext,
    values: SpendingInsightsInput,
) -> dict:
    start_date, end_date = values.date_range()
    result = SpendingInsightsService(context.db).build(
        start_date=start_date,
        end_date=end_date,
        account_id=values.account_id,
        category=values.category,
        merchant=values.merchant,
        review_type=values.review_type or "all",
        spend_basis=values.spend_basis or "card",
        currency_code=values.currency_code,
        comparison_mode=values.comparison_mode or "immediately_preceding",
    )
    scope = result["scope"]
    range_value = result["range"]
    return {
        "start_date": range_value["start_date"],
        "end_date": range_value["end_date"],
        "previous_start_date": range_value["previous_start_date"],
        "previous_end_date": range_value["previous_end_date"],
        "currency_code": scope["currency"],
        "spend_basis": scope["spend_basis"],
        "comparison_mode": range_value["comparison_mode"],
        "summary": result["summary"],
        "comparison": result["comparison"],
        "categories": result["category_breakdown"][:MAX_SPENDING_BREAKDOWN_ITEMS],
        "merchants": result["merchant_breakdown"][:MAX_SPENDING_BREAKDOWN_ITEMS],
        "notable_changes": result["notable_changes"][:MAX_NOTABLE_CHANGES],
        "available_currencies": scope["available_currencies"][:MAX_AVAILABLE_CURRENCIES],
        "excluded_other_currency_transactions": scope["excluded_other_currency_transactions"],
        "pending_transactions_excluded": scope["pending_transactions_excluded"],
    }


def _get_lifestyle_dining_insights(
    context: AgentToolContext,
    values: LifestyleDiningInput,
) -> dict:
    return LifestyleDiningService(context.db).build(
        start_date=date.fromisoformat(values.start_date),
        end_date=date.fromisoformat(values.end_date),
        activity_type=values.activity_type or "all",
        merchant=values.merchant,
        account_id=values.account_id,
        review_type=values.review_type or "all",
        spend_basis=values.spend_basis or "card",
        currency_code=values.currency_code,
        include_comparison=values.include_comparison,
    )


def _search_transactions(
    context: AgentToolContext,
    values: TransactionSearchInput,
) -> dict:
    criteria = [
        ExpenseTransaction.workspace_id == context.workspace_id,
        ExpenseTransaction.status != TransactionStatus.REMOVED.value,
    ]
    if values.transaction_id is not None:
        criteria.append(ExpenseTransaction.id == values.transaction_id)
        rows = list(context.db.scalars(select(ExpenseTransaction).where(*criteria).limit(1)))
        return _transaction_search_output(rows, result_limit=1, total_count=len(rows))

    if values.merchant:
        merchant_query = f"%{_escape_like(values.merchant.casefold())}%"
        criteria.append(
            or_(
                func.lower(func.coalesce(ExpenseTransaction.merchant_name, "")).like(
                    merchant_query,
                    escape="\\",
                ),
                func.lower(ExpenseTransaction.name).like(merchant_query, escape="\\"),
            )
        )
    if values.start_date:
        criteria.append(ExpenseTransaction.date >= date.fromisoformat(values.start_date))
    if values.end_date:
        criteria.append(ExpenseTransaction.date <= date.fromisoformat(values.end_date))
    if values.category:
        criteria.append(
            func.lower(func.coalesce(ExpenseTransaction.category, "")).like(
                f"%{_escape_like(values.category.casefold())}%",
                escape="\\",
            )
        )
    if values.review_status:
        criteria.append(ExpenseTransaction.status == values.review_status)
    elif values.review_type == "personal":
        criteria.append(ExpenseTransaction.status == TransactionStatus.PERSONAL.value)
    elif values.review_type == "shared":
        criteria.append(ExpenseTransaction.status.in_(_SHARED_TRANSACTION_STATUSES))
    elif values.review_type == "unreviewed":
        criteria.append(ExpenseTransaction.status == TransactionStatus.ASK_USER.value)
    if values.min_amount_cents is not None:
        criteria.append(ExpenseTransaction.amount_cents >= values.min_amount_cents)
    if values.max_amount_cents is not None:
        criteria.append(ExpenseTransaction.amount_cents <= values.max_amount_cents)
    if values.currency_code:
        criteria.append(
            func.upper(ExpenseTransaction.iso_currency_code) == values.currency_code.upper()
        )
    if not values.include_pending:
        criteria.append(ExpenseTransaction.pending.is_(False))

    total_count = int(
        context.db.scalar(select(func.count(ExpenseTransaction.id)).where(*criteria)) or 0
    )
    rows = list(
        context.db.scalars(
            select(ExpenseTransaction)
            .where(*criteria)
            .order_by(
                ExpenseTransaction.date.desc().nullslast(),
                ExpenseTransaction.id.desc(),
            )
            .limit(values.limit)
        )
    )
    return _transaction_search_output(
        rows,
        result_limit=values.limit,
        total_count=total_count,
    )


def _transaction_search_output(
    rows: list[ExpenseTransaction],
    *,
    result_limit: int,
    total_count: int,
) -> dict:
    return {
        "transactions": [
            {
                "public_id": str(row.id),
                "merchant": transaction_display_name(row).strip() or "Unknown merchant",
                "occurred_on": row.date.isoformat() if row.date else None,
                "amount_cents": row.amount_cents,
                "currency_code": (row.iso_currency_code or "USD").upper(),
                "category": row.category,
                "status": row.status,
                "pending": row.pending,
            }
            for row in rows
        ],
        "total_count": total_count,
        "result_limit": result_limit,
        "truncated": total_count > len(rows),
    }


def _validate_date_range(start: date, end: date) -> None:
    if start > end:
        raise ValueError("start_date must not be after end_date")
    if (end - start).days > MAX_DATE_RANGE_DAYS:
        raise ValueError("date range must be two years or less")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

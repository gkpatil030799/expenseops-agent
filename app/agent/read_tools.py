from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, func, or_, select

from app.agent.classification_activity_tool import register_classification_activity_tool
from app.agent.deals_errands_tools import register_deals_errands_tools
from app.agent.household_receipt_tools import register_household_receipt_tools
from app.agent.integration_read_tool import register_integration_read_tool
from app.agent.tooling import AgentTool, AgentToolContext, AgentToolRegistry, ToolEffect
from app.config import Settings
from app.models import ExpenseTransaction, SpendingParentCategory, TransactionStatus
from app.services.agent_service import transaction_display_name
from app.services.lifestyle_dining_service import (
    MAX_LIFESTYLE_MERCHANTS,
    LifestyleDiningService,
    is_lifestyle_purchase_row,
    select_lifestyle_currency,
)
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
    comparison_start_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description=("Optional exact comparison start; provide together with comparison_end_date."),
    )
    comparison_end_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description=("Optional exact comparison end; provide together with comparison_start_date."),
    )
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
        comparison = self.comparison_date_range()
        if comparison is not None:
            _validate_date_range(*comparison)
            if self.comparison_mode is not None:
                raise ValueError(
                    "explicit comparison dates cannot be combined with same_weekdays_last_week"
                )
        return self

    def date_range(self) -> tuple[date, date]:
        return date.fromisoformat(self.start_date), date.fromisoformat(self.end_date)

    def comparison_date_range(self) -> tuple[date, date] | None:
        if (self.comparison_start_date is None) != (self.comparison_end_date is None):
            raise ValueError(
                "comparison_start_date and comparison_end_date must be provided together"
            )
        if self.comparison_start_date is None or self.comparison_end_date is None:
            return None
        return (
            date.fromisoformat(self.comparison_start_date),
            date.fromisoformat(self.comparison_end_date),
        )


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
    comparison_start_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description=("Optional exact comparison start; provide together with comparison_end_date."),
    )
    comparison_end_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description=("Optional exact comparison end; provide together with comparison_start_date."),
    )
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
    merchant_limit: int = Field(
        default=MAX_LIFESTYLE_MERCHANTS,
        ge=1,
        le=MAX_LIFESTYLE_MERCHANTS,
        description=(
            "Maximum canonical merchant ranking rows. Use 8 unless ExpenseOps carries an "
            "explicit typed top-merchant request."
        ),
    )

    @model_validator(mode="after")
    def validate_range(self) -> LifestyleDiningInput:
        _validate_date_range(date.fromisoformat(self.start_date), date.fromisoformat(self.end_date))
        comparison = self.comparison_date_range()
        if comparison is not None:
            _validate_date_range(*comparison)
            if not self.include_comparison:
                raise ValueError("explicit comparison dates require include_comparison")
        return self

    def comparison_date_range(self) -> tuple[date, date] | None:
        if (self.comparison_start_date is None) != (self.comparison_end_date is None):
            raise ValueError(
                "comparison_start_date and comparison_end_date must be provided together"
            )
        if self.comparison_start_date is None or self.comparison_end_date is None:
            return None
        return (
            date.fromisoformat(self.comparison_start_date),
            date.fromisoformat(self.comparison_end_date),
        )


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


class LifestyleMerchantChange(ReadToolModel):
    name: str = Field(min_length=1, max_length=255)
    current_amount_cents: int = Field(ge=0)
    previous_amount_cents: int = Field(ge=0)
    delta_cents: int
    current_transaction_count: int = Field(ge=0)
    previous_transaction_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_delta(self) -> LifestyleMerchantChange:
        if self.delta_cents != self.current_amount_cents - self.previous_amount_cents:
            raise ValueError("merchant delta must reconcile to current and previous amounts")
        if self.delta_cents == 0:
            raise ValueError("merchant changes must have a nonzero spend delta")
        return self


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
    merchant_changes: list[LifestyleMerchantChange] = Field(max_length=8)
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
        if self.comparison is None and self.merchant_changes:
            raise ValueError("merchant changes require a comparison period")
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
    comparison_start_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description=(
            "Server-owned exact Lifestyle comparison start. Use null unless ExpenseOps "
            "carries a typed Lifestyle follow-up scope."
        ),
    )
    comparison_end_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description=(
            "Server-owned exact Lifestyle comparison end. Use null unless ExpenseOps carries "
            "a typed Lifestyle follow-up scope."
        ),
    )
    category: str | None = Field(default=None, min_length=1, max_length=100)
    lifestyle_activity_type: (
        Literal["all", "coffee", "restaurants", "delivery", "nightlife"] | None
    ) = Field(
        default=None,
        description=(
            "Server-owned canonical Lifestyle row scope. Use null; ExpenseOps may set this "
            "closed selector for a typed transaction follow-up."
        ),
    )
    review_type: Literal["all", "personal", "shared", "unreviewed", "attention"] | None = Field(
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
        comparison = self.comparison_date_range()
        if comparison is not None:
            _validate_date_range(*comparison)
            if self.lifestyle_activity_type is None:
                raise ValueError(
                    "comparison dates require the canonical Lifestyle transaction scope"
                )
        if self.lifestyle_activity_type is not None:
            if start is None or end is None:
                raise ValueError(
                    "canonical Lifestyle transaction scope requires start_date and end_date"
                )
            if self.include_pending:
                raise ValueError(
                    "canonical Lifestyle transaction scope must exclude pending transactions"
                )
            if self.transaction_id is not None or self.category is not None:
                raise ValueError(
                    "canonical Lifestyle transaction scope cannot be combined with an exact "
                    "transaction or category filter"
                )
            if self.review_status is not None or self.review_type == "attention":
                raise ValueError(
                    "canonical Lifestyle transaction scope cannot use a recovery status scope"
                )
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

    def comparison_date_range(self) -> tuple[date, date] | None:
        if (self.comparison_start_date is None) != (self.comparison_end_date is None):
            raise ValueError(
                "comparison_start_date and comparison_end_date must be provided together"
            )
        if self.comparison_start_date is None or self.comparison_end_date is None:
            return None
        return (
            date.fromisoformat(self.comparison_start_date),
            date.fromisoformat(self.comparison_end_date),
        )


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
    register_classification_activity_tool(registry)
    registry.register(
        AgentTool(
            name="get_spending_insights",
            description=(
                "USE for canonical ExpenseOps aggregate purchase-spend totals, period "
                "comparisons, category or merchant rankings, and deterministic change "
                "explanations; credits remain separate. Optionally pass both exact comparison "
                "dates, otherwise ExpenseOps derives the immediately preceding period. "
                "DO NOT USE to list transaction rows, classify lifestyle subtypes, or answer "
                "household replenishment questions."
            ),
            effect=ToolEffect.READ,
            input_model=SpendingInsightsInput,
            output_model=SpendingInsightsOutput,
            handler=_get_spending_insights,
            version="1.3",
        )
    )
    registry.register(
        AgentTool(
            name="get_lifestyle_dining_insights",
            description=(
                "USE for canonical read-only coffee, restaurant, food-delivery, or reliably "
                "categorized nightlife frequency, average, spend, and merchant changes. "
                "Optionally pass both exact comparison dates for a user-selected second period. "
                "DO NOT USE for generic transaction rows, groceries, household replenishment, "
                "or uncertain activity classification."
            ),
            effect=ToolEffect.READ,
            input_model=LifestyleDiningInput,
            output_model=LifestyleDiningOutput,
            handler=_get_lifestyle_dining_insights,
            version="1.3",
        )
    )
    registry.register(
        AgentTool(
            name="search_transactions",
            description=(
                "Return bounded authenticated ExpenseOps transaction rows when the latest user "
                "message explicitly asks to list, find, show, or identify transaction/charge "
                "rows. USE with review_type=unreviewed (or attention) for any request about "
                "which transactions/purchases/charges need review, a decision, or attention. "
                "DO NOT USE get_receipts for that wording; get_receipts is itemized receipt "
                "documents only."
            ),
            effect=ToolEffect.READ,
            input_model=TransactionSearchInput,
            output_model=TransactionSearchOutput,
            handler=_search_transactions,
            version="1.2",
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
    comparison_range = values.comparison_date_range()
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
        comparison_start_date=comparison_range[0] if comparison_range else None,
        comparison_end_date=comparison_range[1] if comparison_range else None,
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
    comparison_range = values.comparison_date_range()
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
        comparison_start_date=comparison_range[0] if comparison_range else None,
        comparison_end_date=comparison_range[1] if comparison_range else None,
        merchant_limit=values.merchant_limit,
    )


def _search_transactions(
    context: AgentToolContext,
    values: TransactionSearchInput,
) -> dict:
    if values.lifestyle_activity_type is not None:
        return _search_lifestyle_transactions(context, values)

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
        normalized_category = " ".join(
            values.category.casefold().replace("&", " and ").replace("_", " ").split()
        )
        if normalized_category == "food and dining":
            raw_category_fields = (
                func.lower(func.coalesce(ExpenseTransaction.provider_category, "")),
                func.lower(func.coalesce(ExpenseTransaction.category, "")),
            )
            raw_food_terms = (
                "food_and_drink",
                "food and drink",
                "food & dining",
                "restaurant",
                "dining",
                "grocery",
                "coffee",
                "fast food",
                "food delivery",
            )
            criteria.append(
                or_(
                    and_(
                        ExpenseTransaction.classification_applied_at.is_not(None),
                        ExpenseTransaction.spending_parent_category
                        == SpendingParentCategory.FOOD_DINING.value,
                    ),
                    *(
                        field.like(f"%{_escape_like(term)}%", escape="\\")
                        for field in raw_category_fields
                        for term in raw_food_terms
                    ),
                )
            )
        else:
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
    elif values.review_type == "attention":
        criteria.append(
            ExpenseTransaction.status.in_(
                (
                    TransactionStatus.ASK_USER.value,
                    TransactionStatus.POST_AMBIGUOUS.value,
                    TransactionStatus.UNDO_AMBIGUOUS.value,
                    TransactionStatus.RECONCILIATION_REQUIRED.value,
                    TransactionStatus.ERROR.value,
                )
            )
        )
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


def _search_lifestyle_transactions(
    context: AgentToolContext,
    values: TransactionSearchInput,
) -> dict:
    """Return the exact canonical purchase rows behind a typed Lifestyle follow-up."""

    activity_type = values.lifestyle_activity_type
    if activity_type is None or values.start_date is None or values.end_date is None:
        raise ValueError("canonical Lifestyle transaction scope requires an exact activity range")
    ranges = [
        (date.fromisoformat(values.start_date), date.fromisoformat(values.end_date)),
    ]
    comparison = values.comparison_date_range()
    if comparison is not None:
        ranges.append(comparison)

    # Query each bounded interval separately. This preserves an exact pair even
    # when the periods are non-adjacent and avoids admitting rows from the gap.
    canonical_by_id = {}
    service = SpendingInsightsService(context.db)
    for start_date, end_date in ranges:
        for row in service.canonical_rows(
            start_date=start_date,
            end_date=end_date,
            spend_basis="card",
        ):
            if row.tx.workspace_id == context.workspace_id:
                canonical_by_id[row.tx.id] = row

    merchant_query = (values.merchant or "").casefold()
    scoped = [
        row
        for row in canonical_by_id.values()
        if (not merchant_query or merchant_query in row.merchant.casefold())
        and (values.review_type in {None, "all"} or row.review_type == values.review_type)
        and (values.min_amount_cents is None or row.tx.amount_cents >= values.min_amount_cents)
        and (values.max_amount_cents is None or row.tx.amount_cents <= values.max_amount_cents)
    ]
    available_currencies = sorted({row.currency_code for row in scoped})
    selected_currency = select_lifestyle_currency(
        values.currency_code,
        available_currencies,
    )

    eligible = []
    for row in scoped:
        if row.currency_code != selected_currency or not is_lifestyle_purchase_row(
            row, activity_type
        ):
            continue
        eligible.append(row.tx)

    eligible.sort(
        key=lambda row: (row.date or date.min, row.id),
        reverse=True,
    )
    total_count = len(eligible)
    return _transaction_search_output(
        eligible[: values.limit],
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

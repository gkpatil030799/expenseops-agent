from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Literal

from sqlalchemy.orm import Session

from app.models import ClassificationActivityType
from app.services.spending_insights_service import (
    SpendClassification,
    SpendingInsightsService,
    SpendRow,
)

LifestyleActivity = Literal["all", "coffee", "restaurants", "delivery", "nightlife"]
LifestyleSubtype = Literal["coffee", "restaurants", "delivery", "nightlife", "uncertain"]
MAX_COMPARISON_RANGE_DAYS = 730
MAX_LIFESTYLE_MERCHANTS = 8

_KNOWN_SUBTYPES: tuple[Literal["coffee", "restaurants", "delivery", "nightlife"], ...] = (
    "coffee",
    "restaurants",
    "delivery",
    "nightlife",
)


class LifestyleDiningService:
    """Deterministic lifestyle projection over canonical spending rows.

    This is intentionally not a second spending engine. Transaction eligibility,
    credit direction, currency, review state, and actual-share projection all come
    from ``SpendingInsightsService.canonical_rows``.
    """

    def __init__(self, db: Session):
        self.db = db

    def build(
        self,
        *,
        start_date: date,
        end_date: date,
        activity_type: LifestyleActivity = "all",
        merchant: str | None = None,
        account_id: str | None = None,
        review_type: Literal["all", "personal", "shared"] = "all",
        spend_basis: Literal["card", "actual_share"] = "card",
        currency_code: str | None = None,
        include_comparison: bool = True,
        comparison_start_date: date | None = None,
        comparison_end_date: date | None = None,
        merchant_limit: int = MAX_LIFESTYLE_MERCHANTS,
    ) -> dict:
        if not 1 <= merchant_limit <= MAX_LIFESTYLE_MERCHANTS:
            raise ValueError("merchant_limit is outside the supported range")
        previous_start, previous_end = _comparison_range(
            start_date=start_date,
            end_date=end_date,
            include_comparison=include_comparison,
            comparison_start_date=comparison_start_date,
            comparison_end_date=comparison_end_date,
        )
        query_start = min(start_date, previous_start) if include_comparison else start_date
        query_end = max(end_date, previous_end) if include_comparison else end_date
        canonical = SpendingInsightsService(self.db).canonical_rows(
            start_date=query_start,
            end_date=query_end,
            spend_basis=spend_basis,
        )
        scoped = [
            row
            for row in canonical
            if (not account_id or row.tx.account_id == account_id)
            and (not merchant or merchant.casefold() in row.merchant.casefold())
            and (review_type == "all" or row.review_type == review_type)
        ]
        available_currencies = sorted({row.currency_code for row in scoped})
        selected_currency = select_lifestyle_currency(currency_code, available_currencies)
        current_rows = [
            row
            for row in scoped
            if row.tx.date
            and start_date <= row.tx.date <= end_date
            and row.currency_code == selected_currency
        ]
        previous_rows = [
            row
            for row in scoped
            if include_comparison
            and row.tx.date
            and previous_start <= row.tx.date <= previous_end
            and row.currency_code == selected_currency
        ]
        current = _select_activity_rows(current_rows, activity_type)
        previous = _select_activity_rows(previous_rows, activity_type)
        current_uncertain = _uncertain_rows(current_rows)
        previous_uncertain = _uncertain_rows(previous_rows)
        summary = _summary(current)
        comparison = _summary(previous) if include_comparison else None
        observations = _observations(
            activity_type=activity_type,
            summary=summary,
            comparison=comparison,
            uncertain_count=len(current_uncertain),
            currency_code=selected_currency,
        )
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "previous_start_date": previous_start.isoformat() if include_comparison else None,
            "previous_end_date": previous_end.isoformat() if include_comparison else None,
            "activity_type": activity_type,
            "currency_code": selected_currency,
            "spend_basis": spend_basis,
            "summary": summary,
            "comparison": comparison,
            "activities": _activity_breakdown(current),
            "top_merchants": _merchant_breakdown(current, limit=merchant_limit),
            "merchant_changes": (
                _merchant_changes(current, previous) if include_comparison else []
            ),
            "uncertain_transaction_count": len(current_uncertain),
            "previous_uncertain_transaction_count": len(previous_uncertain),
            "observations": observations,
            "available_currencies": available_currencies[:16],
            "excluded_other_currency_transactions": sum(
                1
                for row in scoped
                if row.tx.date
                and start_date <= row.tx.date <= end_date
                and row.currency_code != selected_currency
                and classify_lifestyle_row(row) is not None
            ),
            "pending_transactions_excluded": True,
        }


def _comparison_range(
    *,
    start_date: date,
    end_date: date,
    include_comparison: bool,
    comparison_start_date: date | None,
    comparison_end_date: date | None,
) -> tuple[date, date]:
    """Resolve one bounded comparison period without changing legacy defaults."""

    if (comparison_start_date is None) != (comparison_end_date is None):
        raise ValueError("comparison_start_date and comparison_end_date must be provided together")
    if comparison_start_date is not None and comparison_end_date is not None:
        if not include_comparison:
            raise ValueError("explicit comparison dates require include_comparison")
        _validate_comparison_range(comparison_start_date, comparison_end_date)
        return comparison_start_date, comparison_end_date

    period_days = (end_date - start_date).days + 1
    previous_end = start_date - timedelta(days=1)
    return previous_end - timedelta(days=period_days - 1), previous_end


def _validate_comparison_range(start_date: date, end_date: date) -> None:
    if start_date > end_date:
        raise ValueError("comparison_start_date must not be after comparison_end_date")
    if (end_date - start_date).days > MAX_COMPARISON_RANGE_DAYS:
        raise ValueError("comparison date range must be two years or less")


def classify_lifestyle_row(row: SpendRow) -> LifestyleSubtype | None:
    """Classify only when canonical category or bounded merchant evidence supports it."""

    if row.tx.classification_applied_at is not None:
        canonical_activity = row.tx.classification_activity_type
        canonical = {
            ClassificationActivityType.COFFEE_BEVERAGE.value: "coffee",
            ClassificationActivityType.RESTAURANT_MEAL.value: "restaurants",
            ClassificationActivityType.FOOD_DELIVERY.value: "delivery",
            ClassificationActivityType.NIGHTLIFE.value: "nightlife",
        }.get(canonical_activity)
        if canonical is not None:
            return canonical
        if canonical_activity in {
            ClassificationActivityType.GROCERY.value,
            ClassificationActivityType.HOUSEHOLD_CONSUMABLE.value,
            ClassificationActivityType.ONE_TIME_PURCHASE.value,
            ClassificationActivityType.NON_PRODUCT.value,
            ClassificationActivityType.REFUND.value,
            ClassificationActivityType.TAX.value,
            ClassificationActivityType.TIP.value,
            ClassificationActivityType.DISCOUNT.value,
            ClassificationActivityType.FEE.value,
        }:
            return None
    category = _normalize(row.source_category)
    merchant = _normalize(row.merchant)
    if _has_any(category, "grocery", "groceries", "supermarket"):
        return None
    explicit_lifestyle_category = _has_any(
        category,
        "coffee",
        "coffee shop",
        "cafe",
        "cafes",
        "tea room",
        "food delivery",
        "meal delivery",
        "delivery service",
        "bar",
        "bars",
        "nightlife",
        "pub",
        "pubs",
    )
    if not _is_food_and_dining(category) and not explicit_lifestyle_category:
        return None
    if _has_any(category, "food delivery", "meal delivery", "delivery service"):
        return "delivery"
    if _has_any(category, "coffee", "coffee shop", "cafe", "cafes", "tea room"):
        return "coffee"
    if _has_any(category, "bar", "bars", "nightlife", "pub", "pubs"):
        return "nightlife"
    if _has_any(category, "restaurant", "restaurants", "fast food"):
        return "restaurants"
    # Merchant-language evidence is intentionally small and generic. It helps
    # legacy generic Food & Drink rows without maintaining a merchant catalog.
    if _has_any(merchant, "coffee", "cafe", "espresso"):
        return "coffee"
    return "uncertain"


def _is_food_and_dining(category: str) -> bool:
    return _has_any(category, "food", "food and drink", "dining", "restaurant", "restaurants")


def _normalize(value: str | None) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split())


def _has_any(value: str, *terms: str) -> bool:
    padded = f" {value} "
    return any(f" {term} " in padded for term in terms)


def select_lifestyle_currency(requested: str | None, available: list[str]) -> str:
    """Apply the canonical Lifestyle currency preference to a bounded row set."""

    if requested and requested.strip():
        return requested.strip().upper()
    if "USD" in available:
        return "USD"
    return available[0] if available else "USD"


def _select_activity_rows(
    rows: list[SpendRow], activity_type: LifestyleActivity
) -> list[tuple[SpendRow, LifestyleSubtype]]:
    selected: list[tuple[SpendRow, LifestyleSubtype]] = []
    for row in rows:
        subtype = classify_lifestyle_row(row)
        if subtype is None or subtype == "uncertain":
            continue
        if activity_type != "all" and subtype != activity_type:
            continue
        selected.append((row, subtype))
    return selected


def _uncertain_rows(rows: list[SpendRow]) -> list[SpendRow]:
    return [row for row in rows if classify_lifestyle_row(row) == "uncertain"]


def _purchase_value(row: SpendRow) -> int:
    if row.classification is not SpendClassification.PURCHASE:
        return 0
    return row.selected_cents or 0


def is_lifestyle_purchase_row(row: SpendRow, activity_type: LifestyleActivity) -> bool:
    """Whether one canonical spend row contributes to the Lifestyle purchase summary."""

    subtype = classify_lifestyle_row(row)
    return bool(
        _purchase_value(row) > 0
        and subtype is not None
        and subtype != "uncertain"
        and (activity_type == "all" or subtype == activity_type)
    )


def _summary(rows: list[tuple[SpendRow, LifestyleSubtype]]) -> dict[str, int]:
    purchases = [(row, subtype) for row, subtype in rows if _purchase_value(row) > 0]
    total = sum(_purchase_value(row) for row, _ in purchases)
    personal = sum(_purchase_value(row) for row, _ in purchases if row.review_type == "personal")
    shared = sum(_purchase_value(row) for row, _ in purchases if row.review_type == "shared")
    unreviewed = sum(
        _purchase_value(row) for row, _ in purchases if row.review_type == "unreviewed"
    )
    weekday = [item for item in purchases if item[0].tx.date and item[0].tx.date.weekday() < 5]
    weekend = [item for item in purchases if item[0].tx.date and item[0].tx.date.weekday() >= 5]
    return {
        "total_cents": total,
        "personal_cents": personal,
        "shared_cents": shared,
        "unreviewed_cents": unreviewed,
        "credits_cents": sum(
            row.selected_credit_cents or 0
            for row, _ in rows
            if row.classification is SpendClassification.CREDIT
        ),
        "transaction_count": len(purchases),
        "average_cents": round(total / len(purchases)) if purchases else 0,
        "unknown_share_transactions": sum(
            1
            for row, _ in rows
            if row.classification is SpendClassification.PURCHASE
            and row.review_type == "shared"
            and row.selected_cents is None
        ),
        "unknown_credit_share_transactions": sum(
            1
            for row, _ in rows
            if row.classification is SpendClassification.CREDIT
            and row.review_type == "shared"
            and row.selected_credit_cents is None
        ),
        "weekday_cents": sum(_purchase_value(row) for row, _ in weekday),
        "weekday_count": len(weekday),
        "weekend_cents": sum(_purchase_value(row) for row, _ in weekend),
        "weekend_count": len(weekend),
    }


def _activity_breakdown(
    rows: list[tuple[SpendRow, LifestyleSubtype]],
) -> list[dict[str, int | float | str]]:
    values: dict[str, dict[str, int]] = defaultdict(lambda: {"amount": 0, "count": 0})
    for row, subtype in rows:
        value = _purchase_value(row)
        if value <= 0:
            continue
        values[subtype]["amount"] += value
        values[subtype]["count"] += 1
    total = sum(value["amount"] for value in values.values())
    return [
        {
            "name": subtype,
            "amount_cents": values[subtype]["amount"],
            "transaction_count": values[subtype]["count"],
            "percentage": round(values[subtype]["amount"] / total * 100, 1) if total else 0,
        }
        for subtype in _KNOWN_SUBTYPES
        if subtype in values
    ]


def _merchant_breakdown(
    rows: list[tuple[SpendRow, LifestyleSubtype]],
    *,
    limit: int,
) -> list[dict[str, int | float | str]]:
    values: dict[str, dict[str, int]] = defaultdict(lambda: {"amount": 0, "count": 0})
    for row, _ in rows:
        value = _purchase_value(row)
        if value <= 0:
            continue
        values[row.merchant]["amount"] += value
        values[row.merchant]["count"] += 1
    total = sum(value["amount"] for value in values.values())
    result = [
        {
            "name": name,
            "amount_cents": value["amount"],
            "transaction_count": value["count"],
            "percentage": round(value["amount"] / total * 100, 1) if total else 0,
        }
        for name, value in values.items()
    ]
    result.sort(key=lambda item: (-int(item["amount_cents"]), str(item["name"])))
    return result[:limit]


def _merchant_changes(
    current: list[tuple[SpendRow, LifestyleSubtype]],
    previous: list[tuple[SpendRow, LifestyleSubtype]],
) -> list[dict[str, int | str]]:
    """Return bounded signed merchant deltas over canonical purchase rows.

    Current-only and previous-only merchants are retained so a deterministic
    explanation can identify both new spend and spend that disappeared. Credits
    remain separate because ``_purchase_value`` accepts purchase rows only.
    """

    def amounts(
        rows: list[tuple[SpendRow, LifestyleSubtype]],
    ) -> dict[str, dict[str, int]]:
        values: dict[str, dict[str, int]] = defaultdict(lambda: {"amount": 0, "count": 0})
        for row, _subtype in rows:
            value = _purchase_value(row)
            if value <= 0:
                continue
            values[row.merchant]["amount"] += value
            values[row.merchant]["count"] += 1
        return values

    current_values = amounts(current)
    previous_values = amounts(previous)
    result = []
    for name in current_values.keys() | previous_values.keys():
        current_value = current_values.get(name, {"amount": 0, "count": 0})
        previous_value = previous_values.get(name, {"amount": 0, "count": 0})
        delta = current_value["amount"] - previous_value["amount"]
        if delta == 0:
            continue
        result.append(
            {
                "name": name,
                "current_amount_cents": current_value["amount"],
                "previous_amount_cents": previous_value["amount"],
                "delta_cents": delta,
                "current_transaction_count": current_value["count"],
                "previous_transaction_count": previous_value["count"],
            }
        )
    result.sort(key=lambda item: (-abs(int(item["delta_cents"])), str(item["name"])))
    return result[:8]


def _observations(
    *,
    activity_type: LifestyleActivity,
    summary: dict[str, int],
    comparison: dict[str, int] | None,
    uncertain_count: int,
    currency_code: str,
) -> list[str]:
    label = "Lifestyle dining" if activity_type == "all" else activity_type.capitalize()
    observations = [
        f"{label} purchases: {summary['transaction_count']} totaling "
        f"{_money(currency_code, summary['total_cents'])}."
    ]
    if comparison is not None:
        count_delta = summary["transaction_count"] - comparison["transaction_count"]
        spend_delta = summary["total_cents"] - comparison["total_cents"]
        observations.append(
            f"Purchase frequency changed from {comparison['transaction_count']} to "
            f"{summary['transaction_count']} ({count_delta:+d}); purchase spend changed by "
            f"{_signed_money(currency_code, spend_delta)}."
        )
    if uncertain_count:
        observations.append(
            f"{uncertain_count} Food & Dining transaction"
            f"{' was' if uncertain_count == 1 else 's were'} not assigned a lifestyle subtype."
        )
    return observations[:6]


def _money(currency_code: str, cents: int) -> str:
    return f"{currency_code} {cents / 100:,.2f}"


def _signed_money(currency_code: str, cents: int) -> str:
    sign = "+" if cents > 0 else "-" if cents < 0 else ""
    return f"{sign}{currency_code} {abs(cents) / 100:,.2f}"

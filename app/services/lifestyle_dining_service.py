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
    ) -> dict:
        period_days = (end_date - start_date).days + 1
        previous_end = start_date - timedelta(days=1)
        previous_start = previous_end - timedelta(days=period_days - 1)
        query_start = previous_start if include_comparison else start_date
        canonical = SpendingInsightsService(self.db).canonical_rows(
            start_date=query_start,
            end_date=end_date,
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
        selected_currency = _select_currency(currency_code, available_currencies)
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
            "top_merchants": _merchant_breakdown(current),
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


def _select_currency(requested: str | None, available: list[str]) -> str:
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

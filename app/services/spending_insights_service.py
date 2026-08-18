from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ExpenseTransaction,
    SpendingParentCategory,
    SplitwiseIntegration,
    TransactionStatus,
)

SpendBasis = Literal["card", "actual_share"]
ReviewType = Literal["all", "personal", "shared"]
Granularity = Literal["day", "week", "month"]
ComparisonMode = Literal["immediately_preceding", "same_weekdays_last_week"]


CATEGORY_MAP: dict[str, tuple[str, ...]] = {
    "Food & Dining": (
        "food and drink",
        "food",
        "dining",
        "restaurant",
        "restaurants",
        "grocery",
        "groceries",
        "coffee",
        "fast food",
        "food delivery",
    ),
    # Health precedes Lifestyle so PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS
    # remains health while hair/beauty personal-care rows remain lifestyle.
    "Health": ("health", "medical", "pharmacy", "fitness", "gym", "gyms"),
    "Lifestyle": (
        "general merchandise",
        "personal care",
        "shop",
        "shops",
        "shopping",
        "superstore",
        "superstores",
        "online marketplace",
        "online marketplaces",
        "clothing",
        "electronics",
        "convenience",
        "department store",
        "department stores",
        "hair",
        "beauty",
        "entertainment",
        "recreation",
    ),
    "Home & Bills": (
        "home",
        "rent",
        "utility",
        "utilities",
        "bill",
        "bills",
        "internet",
        "phone",
    ),
    "Transportation": (
        "transport",
        "transportation",
        "taxi",
        "ride",
        "rideshare",
        "rideshares",
        "uber",
        "lyft",
        "gas",
        "parking",
        "auto",
        "automotive",
    ),
    "Travel": ("travel", "airline", "hotel", "lodging"),
    "Subscriptions": ("subscription", "subscriptions", "streaming"),
}

MATERIAL_CHANGE_CENTS = 2_500
MATERIAL_CHANGE_PERCENT = 15
MATERIAL_SHARE_OF_TOTAL_PERCENT = 5


class SpendClassification(StrEnum):
    PURCHASE = "purchase"
    CREDIT = "credit"
    EXCLUDED = "excluded"


@dataclass(frozen=True)
class SpendRow:
    tx: ExpenseTransaction
    classification: SpendClassification
    card_cents: int
    selected_cents: int | None
    selected_credit_cents: int | None
    parent_category: str
    source_category: str
    merchant: str
    review_type: str
    people: tuple[str, ...]
    group: str | None
    currency_code: str


class SpendingInsightsService:
    def __init__(self, db: Session):
        self.db = db

    def build(
        self,
        *,
        start_date: date,
        end_date: date,
        account_id: str | None = None,
        category: str | None = None,
        merchant: str | None = None,
        review_type: ReviewType = "all",
        spend_basis: SpendBasis = "card",
        granularity: Granularity = "day",
        currency_code: str | None = None,
        comparison_mode: ComparisonMode = "immediately_preceding",
    ) -> dict:
        period_days = (end_date - start_date).days + 1
        if comparison_mode == "same_weekdays_last_week":
            previous_start = start_date - timedelta(days=7)
            previous_end = end_date - timedelta(days=7)
        else:
            previous_end = start_date - timedelta(days=1)
            previous_start = previous_end - timedelta(days=period_days - 1)
        viewer_splitwise_user_id = self._viewer_splitwise_user_id()
        rows = self.canonical_rows(
            start_date=previous_start,
            end_date=end_date,
            spend_basis=spend_basis,
        )
        scoped_current = self._filter(
            rows, start_date, end_date, account_id, category, merchant, review_type
        )
        scoped_previous = self._filter(
            rows, previous_start, previous_end, account_id, category, merchant, review_type
        )
        available_currencies = sorted(
            {row.currency_code for row in [*scoped_current, *scoped_previous]}
        )
        selected_currency = _select_currency(currency_code, available_currencies)
        current = [row for row in scoped_current if row.currency_code == selected_currency]
        previous = [row for row in scoped_previous if row.currency_code == selected_currency]
        current_summary = _summary(current)
        previous_summary = _summary(previous)
        categories = _breakdown(current, lambda row: row.parent_category)
        previous_categories = {
            item["name"]: item["amount_cents"]
            for item in _breakdown(previous, lambda row: row.parent_category)
        }
        for item in categories:
            item["previous_amount_cents"] = previous_categories.get(item["name"], 0)
        merchants = _breakdown(current, lambda row: row.merchant)
        personal_shared = {
            name: _sum(row for row in current if row.review_type == name)
            for name in ("personal", "shared")
        }
        unknown_share = current_summary["unknown_share_transactions"]
        unknown_credit_share = current_summary["unknown_credit_share_transactions"]
        unreviewed_cents = sum(
            _value(row) for row in current if row.tx.status == TransactionStatus.ASK_USER.value
        )
        known_purchases = [row for row in current if _has_selected_purchase_spend(row)]
        purchase_comparison_complete = not (
            current_summary["unknown_share_transactions"]
            or previous_summary["unknown_share_transactions"]
        )
        notable_changes = (
            _notable_changes(
                current_summary,
                previous_summary,
                categories,
                merchants,
                personal_shared,
                previous,
                selected_currency,
            )
            if purchase_comparison_complete
            else []
        )

        return {
            "range": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "previous_start_date": previous_start.isoformat(),
                "previous_end_date": previous_end.isoformat(),
                "granularity": granularity,
                "comparison_mode": comparison_mode,
            },
            "scope": {
                "currency": selected_currency,
                "available_currencies": available_currencies,
                "excluded_other_currency_transactions": sum(
                    1 for row in scoped_current if row.currency_code != selected_currency
                ),
                "spend_basis": spend_basis,
                "viewer_share_identity_connected": viewer_splitwise_user_id is not None,
                "pending_transactions_excluded": True,
            },
            "summary": current_summary,
            "comparison": previous_summary,
            "trend": _trend(current, start_date, end_date, granularity),
            "category_breakdown": categories,
            "subcategory_breakdown": _breakdown(current, lambda row: row.source_category),
            "merchant_breakdown": merchants,
            "personal_shared": personal_shared,
            "shared_people": _shared_breakdown(current, "people"),
            "shared_groups": _shared_breakdown(current, "groups"),
            "category_trend": _category_trend(current, start_date, end_date, granularity),
            "notable_changes": notable_changes,
            "accounts": sorted({row.tx.account_id for row in known_purchases if row.tx.account_id}),
            "categories": sorted({row.parent_category for row in known_purchases}),
            "merchants": sorted({row.merchant for row in known_purchases}),
            "data_quality": {
                "unknown_share_transactions": unknown_share,
                "unknown_credit_share_transactions": unknown_credit_share,
                "unreviewed_cents": unreviewed_cents,
                # Kept during the response-contract transition for older clients.
                "pending_review_cents": unreviewed_cents,
                "uncategorized_cents": _sum(
                    row for row in current if row.parent_category == "Uncategorized"
                ),
                "pending_transactions_excluded": True,
            },
        }

    def canonical_rows(
        self,
        *,
        start_date: date,
        end_date: date,
        spend_basis: SpendBasis,
    ) -> list[SpendRow]:
        """Return the one canonical purchase/credit projection used by analytics.

        Lifestyle intelligence deliberately consumes this seam instead of
        reimplementing sign, Splitwise-share, pending, removed, currency, or
        transfer/payment semantics.
        """

        criteria = [
            ExpenseTransaction.date >= start_date,
            ExpenseTransaction.date <= end_date,
            ExpenseTransaction.status != TransactionStatus.REMOVED.value,
            ExpenseTransaction.pending.is_(False),
        ]
        workspace_id = self.db.info.get("workspace_id")
        if workspace_id is not None:
            criteria.append(ExpenseTransaction.workspace_id == int(workspace_id))
        transactions = list(self.db.scalars(select(ExpenseTransaction).where(*criteria)))
        viewer_splitwise_user_id = self._viewer_splitwise_user_id()
        return [
            row
            for tx in transactions
            if (row := _to_spend_row(tx, spend_basis, viewer_splitwise_user_id))
        ]

    def _viewer_splitwise_user_id(self) -> int | None:
        actor_user_id = self.db.info.get("user_id")
        if actor_user_id is None:
            return None
        integration = self.db.scalar(
            select(SplitwiseIntegration).where(
                SplitwiseIntegration.user_id == int(actor_user_id),
                SplitwiseIntegration.enabled.is_(True),
                SplitwiseIntegration.verified_at.is_not(None),
            )
        )
        if not integration or not integration.splitwise_user_id:
            return None
        try:
            return int(integration.splitwise_user_id)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _filter(
        rows: list[SpendRow],
        start_date: date,
        end_date: date,
        account_id: str | None,
        category: str | None,
        merchant: str | None,
        review_type: ReviewType,
    ) -> list[SpendRow]:
        merchant_query = (merchant or "").casefold()
        category_query = _normalize_category(category)
        return [
            row
            for row in rows
            if row.tx.date
            and start_date <= row.tx.date <= end_date
            and (not account_id or row.tx.account_id == account_id)
            and (
                not category_query
                or category_query
                in {
                    _normalize_category(row.parent_category),
                    _normalize_category(row.source_category),
                }
            )
            and (not merchant_query or merchant_query in row.merchant.casefold())
            and (review_type == "all" or row.review_type == review_type)
        ]


def _to_spend_row(
    tx: ExpenseTransaction,
    basis: SpendBasis,
    viewer_splitwise_user_id: int | None,
) -> SpendRow | None:
    provider_category = (tx.provider_category or tx.category or "").strip()
    classification = classify_spend_transaction(tx)
    if classification is SpendClassification.EXCLUDED:
        return None
    card_cents = tx.amount_cents
    review_type = (
        "personal"
        if tx.status == TransactionStatus.PERSONAL.value
        else "shared"
        if tx.status in {TransactionStatus.SHARED_DRAFT.value, TransactionStatus.POSTED.value}
        else "unreviewed"
    )
    selected: int | None = card_cents if classification is SpendClassification.PURCHASE else None
    selected_credit: int | None = (
        abs(card_cents) if classification is SpendClassification.CREDIT else None
    )
    people: tuple[str, ...] = ()
    group = None
    if review_type == "shared" and classification is SpendClassification.PURCHASE:
        share, people, group = _split_details(tx.splitwise_payload_json, viewer_splitwise_user_id)
        if basis == "actual_share":
            selected = share if share is None or share >= 0 else None
    elif (
        review_type == "shared"
        and classification is SpendClassification.CREDIT
        and basis == "actual_share"
    ):
        # Existing Splitwise payloads describe purchase responsibility, not how a
        # later credit should be allocated. Never substitute the whole card credit
        # or a stale positive owed-share value for the viewer's unknown credit share.
        selected_credit = None
    canonical_parent = _canonical_parent_category(tx)
    source_category = _canonical_source_category(tx)
    return SpendRow(
        tx=tx,
        classification=classification,
        card_cents=card_cents,
        selected_cents=selected,
        selected_credit_cents=selected_credit,
        parent_category=canonical_parent or _parent_category(provider_category),
        source_category=source_category or provider_category or "Uncategorized",
        merchant=(tx.merchant_name or tx.name or "Unknown merchant").strip(),
        review_type=review_type,
        people=people,
        group=group,
        currency_code=(tx.iso_currency_code or "USD").upper(),
    )


def _parent_category(category: str) -> str:
    if not category:
        return "Uncategorized"
    normalized = _normalize_category(category)
    if normalized == "uncategorized":
        return "Uncategorized"
    for parent, terms in CATEGORY_MAP.items():
        if any(_category_has_term(normalized, term) for term in terms):
            return parent
    return "Other"


_CANONICAL_PARENT_LABELS: dict[str, str] = {
    SpendingParentCategory.FOOD_DINING.value: "Food & Dining",
    SpendingParentCategory.HOUSEHOLD_HOME.value: "Home & Bills",
    SpendingParentCategory.LIFESTYLE_SHOPPING.value: "Lifestyle",
    SpendingParentCategory.PERSONAL_CARE.value: "Lifestyle",
    SpendingParentCategory.HEALTH.value: "Health",
    SpendingParentCategory.TRANSPORTATION.value: "Transportation",
    SpendingParentCategory.TRAVEL.value: "Travel",
    SpendingParentCategory.ENTERTAINMENT.value: "Lifestyle",
    SpendingParentCategory.SUBSCRIPTIONS.value: "Subscriptions",
    SpendingParentCategory.PETS.value: "Lifestyle",
    SpendingParentCategory.EDUCATION_OFFICE.value: "Lifestyle",
    SpendingParentCategory.SERVICES.value: "Lifestyle",
    SpendingParentCategory.FEES_TAXES_DISCOUNTS.value: "Other",
    SpendingParentCategory.OTHER_UNCERTAIN.value: "Other",
}


def _canonical_parent_category(tx: ExpenseTransaction) -> str | None:
    if tx.classification_applied_at is None:
        return None
    return _CANONICAL_PARENT_LABELS.get(tx.spending_parent_category)


def _canonical_source_category(tx: ExpenseTransaction) -> str | None:
    if tx.classification_applied_at is None:
        return None
    if tx.classification_subcategory_name:
        return tx.classification_subcategory_name
    activity = str(tx.classification_activity_type or "").replace("_", " ").strip()
    return activity.title() if activity and activity != "uncertain" else None


def classify_spend_transaction(tx: ExpenseTransaction) -> SpendClassification:
    """Classify one canonical bank row before any spending/share projection."""

    if tx.pending or tx.status == TransactionStatus.REMOVED.value:
        return SpendClassification.EXCLUDED
    if _is_excluded_category(tx.provider_category or tx.category):
        return SpendClassification.EXCLUDED
    if tx.amount_cents > 0:
        return SpendClassification.PURCHASE
    if tx.amount_cents < 0:
        return SpendClassification.CREDIT
    return SpendClassification.EXCLUDED


def _normalize_category(value: str | None) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split())


def _category_has_term(normalized: str, term: str) -> bool:
    return f" {term} " in f" {normalized} "


def _is_excluded_category(category: str | None) -> bool:
    raw = str(category or "").strip()
    normalized = _normalize_category(category)
    if not normalized:
        return False
    # Plaid's legacy category hierarchy is serialized as ``Primary / Detail``.
    # A primary Payment(s) segment is authoritative, while an arbitrary purchase
    # label that merely contains the word "payment" is not.
    primary_segment = _normalize_category(raw.split("/", 1)[0])
    if primary_segment in {"payment", "payments"}:
        return True
    if primary_segment in {"transfer", "transfers", "transfer in", "transfer out"}:
        return True
    if primary_segment in {"loan payment", "loan payments"}:
        return True
    if re.fullmatch(
        r"(?:payments? (?:credit )?card|(?:credit )?card payments?)",
        normalized,
    ):
        return True
    return normalized in {
        "payment",
        "payments",
        "card payment",
        "card payments",
        "credit card payment",
        "credit card payments",
    }


def _split_details(
    payload_json: str | None,
    viewer_splitwise_user_id: int | None,
) -> tuple[int | None, tuple[str, ...], str | None]:
    if not payload_json:
        return None, (), None
    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError):
        return None, (), None
    shares: list[tuple[int, Decimal, Decimal]] = []
    index = 0
    while f"users__{index}__user_id" in payload:
        try:
            shares.append(
                (
                    int(payload[f"users__{index}__user_id"]),
                    Decimal(str(payload.get(f"users__{index}__paid_share", "0"))),
                    Decimal(str(payload.get(f"users__{index}__owed_share", "0"))),
                )
            )
        except (ValueError, TypeError, InvalidOperation):
            return None, (), None
        index += 1
    viewer_share = next(
        (owed for user_id, _, owed in shares if user_id == viewer_splitwise_user_id), None
    )
    people = ("Unknown Splitwise contact",) if any(paid == 0 for _, paid, _ in shares) else ()
    group_id = payload.get("group_id")
    group = f"Group {group_id}" if group_id not in (None, 0, "0") else None
    return (int(viewer_share * 100) if viewer_share is not None else None), people, group


def _select_currency(requested: str | None, available: list[str]) -> str:
    if requested and requested.strip():
        return requested.strip().upper()
    if "USD" in available:
        return "USD"
    return available[0] if available else "USD"


def _value(row: SpendRow) -> int:
    return row.selected_cents or 0


def _has_selected_purchase_spend(row: SpendRow) -> bool:
    return (
        row.classification is SpendClassification.PURCHASE
        and row.selected_cents is not None
        and row.selected_cents > 0
    )


def _sum(rows) -> int:
    return sum(_value(row) for row in rows)


def _summary(rows: list[SpendRow]) -> dict[str, int]:
    known = [row for row in rows if _has_selected_purchase_spend(row)]
    total = _sum(known)
    personal = _sum(row for row in known if row.review_type == "personal")
    shared = _sum(row for row in known if row.review_type == "shared")
    unreviewed = _sum(row for row in known if row.review_type == "unreviewed")
    return {
        "total_cents": total,
        "personal_cents": personal,
        "shared_cents": shared,
        "classified_cents": personal + shared,
        "unreviewed_cents": unreviewed,
        "credits_cents": sum(
            row.selected_credit_cents or 0
            for row in rows
            if row.classification is SpendClassification.CREDIT
        ),
        "unknown_share_transactions": sum(
            1
            for row in rows
            if row.classification is SpendClassification.PURCHASE
            and row.review_type == "shared"
            and row.selected_cents is None
        ),
        "unknown_credit_share_transactions": sum(
            1
            for row in rows
            if row.classification is SpendClassification.CREDIT
            and row.review_type == "shared"
            and row.selected_credit_cents is None
        ),
        "transaction_count": len(known),
        "average_cents": round(total / len(known)) if known else 0,
    }


def _breakdown(rows: list[SpendRow], key) -> list[dict]:
    values: dict[str, dict[str, int]] = defaultdict(lambda: {"amount": 0, "count": 0})
    for row in rows:
        if not _has_selected_purchase_spend(row):
            continue
        name = key(row)
        values[name]["amount"] += _value(row)
        values[name]["count"] += 1
    magnitude_total = sum(abs(value["amount"]) for value in values.values())
    return sorted(
        [
            {
                "name": name,
                "amount_cents": value["amount"],
                "transaction_count": value["count"],
                "percentage": (
                    round(abs(value["amount"]) / magnitude_total * 100, 1) if magnitude_total else 0
                ),
            }
            for name, value in values.items()
        ],
        key=lambda item: (-item["amount_cents"], item["name"]),
    )


def _bucket_start(value: date, granularity: Granularity) -> date:
    if granularity == "week":
        return value - timedelta(days=value.weekday())
    if granularity == "month":
        return value.replace(day=1)
    return value


def _next_bucket(value: date, granularity: Granularity) -> date:
    if granularity == "week":
        return value + timedelta(days=7)
    if granularity == "month":
        return (value.replace(day=28) + timedelta(days=4)).replace(day=1)
    return value + timedelta(days=1)


def _trend(rows: list[SpendRow], start: date, end: date, granularity: Granularity) -> list[dict]:
    values: dict[date, dict[str, int]] = defaultdict(
        lambda: {"total_cents": 0, "personal_cents": 0, "shared_cents": 0, "transactions": 0}
    )
    for row in rows:
        if not _has_selected_purchase_spend(row) or not row.tx.date:
            continue
        bucket = _bucket_start(row.tx.date, granularity)
        values[bucket]["total_cents"] += _value(row)
        if row.review_type in ("personal", "shared"):
            values[bucket][f"{row.review_type}_cents"] += _value(row)
        values[bucket]["transactions"] += 1
    output = []
    bucket = _bucket_start(start, granularity)
    while bucket <= end:
        output.append({"period": bucket.isoformat(), **values[bucket]})
        bucket = _next_bucket(bucket, granularity)
    return output


def _category_trend(rows, start, end, granularity) -> list[dict]:
    values: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        if _has_selected_purchase_spend(row) and row.tx.date:
            values[_bucket_start(row.tx.date, granularity)][row.parent_category] += _value(row)
    return [
        {"period": period.isoformat(), "categories": dict(categories)}
        for period, categories in sorted(values.items())
    ]


def _shared_breakdown(rows: list[SpendRow], mode: str) -> list[dict]:
    amounts: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.review_type != "shared" or not _has_selected_purchase_spend(row):
            continue
        names = row.people if mode == "people" else ((row.group,) if row.group else ())
        for name in names:
            amounts[name] += _value(row)
    return [
        {"name": name, "amount_cents": amount}
        for name, amount in sorted(amounts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _notable_changes(
    current,
    previous,
    categories,
    merchants,
    personal_shared,
    previous_rows,
    currency_code,
):
    notes: list[dict] = []
    previous_breakdown = _breakdown(previous_rows, lambda row: row.parent_category)
    current_categories = {item["name"]: item["amount_cents"] for item in categories}
    previous_categories = {item["name"]: item["amount_cents"] for item in previous_breakdown}
    category_changes = sorted(
        (
            (
                name,
                current_categories.get(name, 0),
                previous_categories.get(name, 0),
            )
            for name in current_categories.keys() | previous_categories.keys()
        ),
        key=lambda item: (-abs(item[1] - item[2]), item[0]),
    )
    for name, amount, baseline in category_changes:
        delta = amount - baseline
        percent = abs(delta) / abs(baseline) * 100 if baseline else 100
        share = abs(delta) / abs(current["total_cents"]) * 100 if current["total_cents"] else 0
        if (
            abs(delta) >= MATERIAL_CHANGE_CENTS
            and (percent >= MATERIAL_CHANGE_PERCENT or share >= MATERIAL_SHARE_OF_TOTAL_PERCENT)
            and len(notes) < 2
        ):
            notes.append(
                {
                    "kind": "category",
                    "direction": "up" if delta > 0 else "down",
                    "label": name,
                    "amount_cents": delta,
                    "detail": (
                        f"{'+' if delta > 0 else '-'}{currency_code} "
                        f"{abs(delta) / 100:,.0f} "
                        "vs previous period"
                    ),
                }
            )
    if merchants and merchants[0]["amount_cents"] >= MATERIAL_CHANGE_CENTS:
        merchant = merchants[0]
        notes.append(
            {
                "kind": "merchant",
                "direction": "neutral",
                "label": merchant["name"],
                "amount_cents": merchant["amount_cents"],
                "detail": (f"Top merchant · {currency_code} {merchant['amount_cents'] / 100:,.0f}"),
            }
        )

    previous_mix = _summary(previous_rows)
    current_classified = personal_shared["personal"] + personal_shared["shared"]
    previous_classified = previous_mix["personal_cents"] + previous_mix["shared_cents"]
    current_shared_pct = (
        personal_shared["shared"] / current_classified * 100 if current_classified else 0
    )
    previous_shared_pct = (
        previous_mix["shared_cents"] / previous_classified * 100 if previous_classified else 0
    )
    mix_delta = round(current_shared_pct - previous_shared_pct)
    if abs(mix_delta) >= 10 and len(notes) < 4:
        notes.append(
            {
                "kind": "mix",
                "direction": "up" if mix_delta > 0 else "down",
                "label": "Shared spending mix",
                "amount_cents": 0,
                "detail": (
                    f"{abs(mix_delta)} percentage points {'higher' if mix_delta > 0 else 'lower'}"
                ),
            }
        )
    return notes[:4]

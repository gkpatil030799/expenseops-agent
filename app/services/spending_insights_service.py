from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExpenseTransaction, SplitwiseIntegration, TransactionStatus

SpendBasis = Literal["card", "actual_share"]
ReviewType = Literal["all", "personal", "shared"]
Granularity = Literal["day", "week", "month"]


CATEGORY_MAP: dict[str, tuple[str, ...]] = {
    "Food & Dining": ("food", "dining", "restaurant", "grocer", "coffee", "delivery"),
    "Lifestyle": ("shop", "clothing", "beauty", "entertainment", "recreation"),
    "Home & Bills": ("home", "rent", "utility", "bill", "internet", "phone"),
    "Transportation": ("transport", "taxi", "ride", "uber", "lyft", "gas", "parking", "auto"),
    "Travel": ("travel", "airline", "hotel", "lodging"),
    "Health": ("health", "medical", "pharmacy", "fitness"),
    "Subscriptions": ("subscription", "streaming"),
}

EXCLUDED_CATEGORY_TERMS = ("transfer", "credit card payment", "loan payment", "payment")
MATERIAL_CHANGE_CENTS = 2_500
MATERIAL_CHANGE_PERCENT = 15
MATERIAL_SHARE_OF_TOTAL_PERCENT = 5


@dataclass(frozen=True)
class SpendRow:
    tx: ExpenseTransaction
    card_cents: int
    selected_cents: int | None
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
    ) -> dict:
        period_days = (end_date - start_date).days + 1
        previous_end = start_date - timedelta(days=1)
        previous_start = previous_end - timedelta(days=period_days - 1)
        transactions = list(
            self.db.scalars(
                select(ExpenseTransaction).where(
                    ExpenseTransaction.date >= previous_start,
                    ExpenseTransaction.date <= end_date,
                    ExpenseTransaction.status != TransactionStatus.REMOVED.value,
                    ExpenseTransaction.pending.is_(False),
                )
            )
        )
        viewer_splitwise_user_id = self._viewer_splitwise_user_id()
        rows = [
            row
            for tx in transactions
            if (row := _to_spend_row(tx, spend_basis, viewer_splitwise_user_id))
        ]
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
        unknown_share = sum(1 for row in current if row.selected_cents is None)
        unreviewed_cents = sum(
            row.card_cents for row in current if row.tx.status == TransactionStatus.ASK_USER.value
        )

        return {
            "range": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "previous_start_date": previous_start.isoformat(),
                "previous_end_date": previous_end.isoformat(),
                "granularity": granularity,
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
            "notable_changes": _notable_changes(
                current_summary,
                previous_summary,
                categories,
                merchants,
                personal_shared,
                previous,
                selected_currency,
            ),
            "accounts": sorted({row.tx.account_id for row in current if row.tx.account_id}),
            "categories": sorted({row.parent_category for row in current}),
            "merchants": sorted({row.merchant for row in current}),
            "data_quality": {
                "unknown_share_transactions": unknown_share,
                "unreviewed_cents": unreviewed_cents,
                # Kept during the response-contract transition for older clients.
                "pending_review_cents": unreviewed_cents,
                "uncategorized_cents": _sum(
                    row for row in current if row.parent_category == "Uncategorized"
                ),
                "pending_transactions_excluded": True,
            },
        }

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
        return [
            row
            for row in rows
            if row.tx.date
            and start_date <= row.tx.date <= end_date
            and (not account_id or row.tx.account_id == account_id)
            and (not category or row.parent_category == category)
            and (not merchant_query or merchant_query in row.merchant.casefold())
            and (review_type == "all" or row.review_type == review_type)
        ]


def _to_spend_row(
    tx: ExpenseTransaction,
    basis: SpendBasis,
    viewer_splitwise_user_id: int | None,
) -> SpendRow | None:
    category = (tx.category or "").strip()
    if any(term in category.casefold() for term in EXCLUDED_CATEGORY_TERMS):
        return None
    card_cents = tx.amount_cents
    review_type = (
        "personal"
        if tx.status == TransactionStatus.PERSONAL.value
        else "shared"
        if tx.status in {TransactionStatus.SHARED_DRAFT.value, TransactionStatus.POSTED.value}
        else "unreviewed"
    )
    selected = card_cents
    people: tuple[str, ...] = ()
    group = None
    if review_type == "shared":
        share, people, group = _split_details(
            tx.splitwise_payload_json, viewer_splitwise_user_id
        )
        if basis == "actual_share":
            selected = share
    return SpendRow(
        tx=tx,
        card_cents=card_cents,
        selected_cents=selected,
        parent_category=_parent_category(category),
        source_category=category or "Uncategorized",
        merchant=(tx.merchant_name or tx.name or "Unknown merchant").strip(),
        review_type=review_type,
        people=people,
        group=group,
        currency_code=(tx.iso_currency_code or "USD").upper(),
    )


def _parent_category(category: str) -> str:
    if not category:
        return "Uncategorized"
    normalized = category.casefold()
    for parent, terms in CATEGORY_MAP.items():
        if any(term in normalized for term in terms):
            return parent
    return "Other"


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


def _sum(rows) -> int:
    return sum(_value(row) for row in rows)


def _summary(rows: list[SpendRow]) -> dict[str, int]:
    known = [row for row in rows if row.selected_cents is not None]
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
        "refund_cents": _sum(row for row in known if _value(row) < 0),
        "transaction_count": len(known),
        "average_cents": round(total / len(known)) if known else 0,
    }


def _breakdown(rows: list[SpendRow], key) -> list[dict]:
    values: dict[str, dict[str, int]] = defaultdict(lambda: {"amount": 0, "count": 0})
    for row in rows:
        if row.selected_cents is None:
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
                    round(abs(value["amount"]) / magnitude_total * 100, 1)
                    if magnitude_total
                    else 0
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
        if row.selected_cents is None or not row.tx.date:
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
        if row.selected_cents is not None and row.tx.date:
            values[_bucket_start(row.tx.date, granularity)][row.parent_category] += _value(row)
    return [
        {"period": period.isoformat(), "categories": dict(categories)}
        for period, categories in sorted(values.items())
    ]


def _shared_breakdown(rows: list[SpendRow], mode: str) -> list[dict]:
    amounts: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.review_type != "shared" or row.selected_cents is None:
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
    previous_categories = {item["name"]: item["amount_cents"] for item in previous_breakdown}
    for item in categories:
        baseline = previous_categories.get(item["name"], 0)
        delta = item["amount_cents"] - baseline
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
                    "label": item["name"],
                    "amount_cents": delta,
                    "detail": (
                        f'{"+" if delta > 0 else "-"}{currency_code} '
                        f'{abs(delta) / 100:,.0f} '
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
                "detail": (
                    f'Top merchant · {currency_code} '
                    f'{merchant["amount_cents"] / 100:,.0f}'
                ),
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
                    f'{abs(mix_delta)} percentage points '
                    f'{"higher" if mix_delta > 0 else "lower"}'
                ),
            }
        )
    return notes[:4]

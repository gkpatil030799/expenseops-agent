"""Populate the local workspace with deterministic, removable Review-queue demo
transactions (status ask_user / shared_draft) so the Expense Review page and the
Agent's in-panel review session have candidates to click through locally.

Safe to re-run; it only touches rows tagged with DEMO_SOURCE.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import delete, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import ExpenseTransaction, PlaidItem, TransactionStatus
from app.tenancy import ensure_default_tenancy, set_session_tenant

DEMO_ITEM_ID = "expenseops-review-demo-v1"
DEMO_TRANSACTION_PREFIX = "expenseops-review-demo-v1-"
DEMO_SOURCE = "expenseops_review_demo_v1"


@dataclass(frozen=True)
class DemoTransaction:
    days_ago: int
    merchant: str
    amount_cents: int
    category: str
    status: TransactionStatus
    account_id: str = "demo-checking"
    group_id: int | None = None


CANDIDATES = (
    DemoTransaction(0, "Costco Wholesale", 14_218, "Shopping", TransactionStatus.ASK_USER),
    DemoTransaction(1, "REI Co-op", 8_999, "Shopping", TransactionStatus.ASK_USER),
    DemoTransaction(2, "Home Depot", 6_347, "Home Improvement", TransactionStatus.ASK_USER),
    DemoTransaction(2, "AMC Theatres", 3_800, "Entertainment", TransactionStatus.ASK_USER),
    DemoTransaction(3, "Whole Foods Market", 5_420, "Groceries", TransactionStatus.ASK_USER),
    DemoTransaction(4, "Delta Air Lines", 32_150, "Travel", TransactionStatus.ASK_USER),
    DemoTransaction(
        1, "Trader Joe's (shared draft)", 4_610, "Groceries", TransactionStatus.SHARED_DRAFT, group_id=41
    ),
)


def _split_payload(group_id: int) -> str:
    return json.dumps(
        {
            "group_id": group_id,
            "users__0__user_id": 1,
            "users__0__paid_share": "100.00",
            "users__0__owed_share": "50.00",
            "users__1__user_id": 2,
            "users__1__paid_share": "0.00",
            "users__1__owed_share": "50.00",
        }
    )


def seed() -> tuple[int, date]:
    settings = get_settings()
    if settings.is_production_mode or not settings.database_url.startswith("sqlite"):
        raise RuntimeError("The Review demo seeder only runs against the local SQLite database.")

    today = date.today()
    with SessionLocal() as db:
        context = ensure_default_tenancy(db)
        set_session_tenant(db, context)
        db.execute(
            delete(ExpenseTransaction).where(
                ExpenseTransaction.plaid_transaction_id.startswith(DEMO_TRANSACTION_PREFIX)
            )
        )
        item = db.scalar(select(PlaidItem).where(PlaidItem.item_id == DEMO_ITEM_ID))
        if item is None:
            item = PlaidItem(
                workspace_id=context.workspace_id,
                item_id=DEMO_ITEM_ID,
                owner_user_id=context.user_id,
                institution_name="ExpenseOps Demo Bank",
                enabled=True,
            )
            db.add(item)
            db.flush()

        for index, record in enumerate(CANDIDATES, start=1):
            payload = _split_payload(record.group_id) if record.group_id is not None else None
            transaction = ExpenseTransaction(
                workspace_id=context.workspace_id,
                plaid_transaction_id=f"{DEMO_TRANSACTION_PREFIX}{index:03d}",
                plaid_item_id=item.id,
                account_id=record.account_id,
                merchant_name=record.merchant,
                name=record.merchant,
                amount_cents=record.amount_cents,
                iso_currency_code="USD",
                date=today - timedelta(days=record.days_ago),
                authorized_date=today - timedelta(days=record.days_ago),
                pending=False,
                payment_channel="in store",
                category=record.category,
                status=record.status.value,
                splitwise_payload_json=payload,
                raw_json=json.dumps({"source": DEMO_SOURCE, "demo": True}),
            )
            db.add(transaction)
        db.commit()
        return len(CANDIDATES), today


if __name__ == "__main__":
    count, seeded_on = seed()
    print(f"Seeded {count} review-queue demo transactions for {seeded_on.isoformat()}.")

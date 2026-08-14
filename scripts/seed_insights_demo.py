"""Populate the local workspace with a deterministic, removable Insights demo dataset."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import delete, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import ExpenseTransaction, PlaidItem, TransactionStatus
from app.tenancy import ensure_default_tenancy, set_session_tenant

DEMO_ITEM_ID = "expenseops-insights-demo-v1"
DEMO_TRANSACTION_PREFIX = "expenseops-insights-demo-v1-"
DEMO_SOURCE = "expenseops_insights_demo_v1"


@dataclass(frozen=True)
class DemoTransaction:
    days_ago: int
    merchant: str
    amount_cents: int
    category: str
    status: TransactionStatus
    account_id: str = "demo-checking"
    group_id: int | None = None


CURRENT_PERIOD = (
    DemoTransaction(1, "Safeway", 12_842, "Groceries", TransactionStatus.PERSONAL),
    DemoTransaction(3, "Trader Joe's", 8_615, "Groceries", TransactionStatus.POSTED, group_id=41),
    DemoTransaction(5, "Olive Garden", 7_436, "Restaurants", TransactionStatus.POSTED, group_id=41),
    DemoTransaction(7, "APS Energy", 14_255, "Utilities", TransactionStatus.PERSONAL),
    DemoTransaction(9, "Chevron", 6_121, "Gas", TransactionStatus.PERSONAL),
    DemoTransaction(11, "Target", 9_473, "Shopping", TransactionStatus.PERSONAL),
    DemoTransaction(13, "Netflix", 2_299, "Streaming Subscription", TransactionStatus.PERSONAL),
    DemoTransaction(15, "Walgreens", 3_862, "Pharmacy", TransactionStatus.PERSONAL),
    DemoTransaction(17, "Uber", 3_140, "Rideshare", TransactionStatus.POSTED, group_id=52),
    DemoTransaction(19, "LA Fitness", 4_499, "Fitness", TransactionStatus.PERSONAL),
    DemoTransaction(21, "Starbucks", 1_875, "Coffee", TransactionStatus.PERSONAL),
    DemoTransaction(23, "Marriott", 21_900, "Hotel", TransactionStatus.POSTED, group_id=52),
    DemoTransaction(25, "Verizon", 7_850, "Phone Bill", TransactionStatus.PERSONAL),
    DemoTransaction(27, "Target Return", -2_649, "Shopping", TransactionStatus.PERSONAL),
    DemoTransaction(28, "Apple Store", 5_432, "Shopping", TransactionStatus.ASK_USER),
)

PREVIOUS_PERIOD = (
    DemoTransaction(31, "Safeway", 9_510, "Groceries", TransactionStatus.PERSONAL),
    DemoTransaction(34, "Trader Joe's", 7_225, "Groceries", TransactionStatus.POSTED, group_id=41),
    DemoTransaction(37, "Chipotle", 4_210, "Restaurants", TransactionStatus.POSTED, group_id=41),
    DemoTransaction(40, "APS Energy", 12_508, "Utilities", TransactionStatus.PERSONAL),
    DemoTransaction(43, "Chevron", 5_522, "Gas", TransactionStatus.PERSONAL),
    DemoTransaction(46, "Target", 5_280, "Shopping", TransactionStatus.PERSONAL),
    DemoTransaction(49, "Netflix", 2_299, "Streaming Subscription", TransactionStatus.PERSONAL),
    DemoTransaction(52, "CVS Pharmacy", 2_175, "Pharmacy", TransactionStatus.PERSONAL),
    DemoTransaction(55, "Uber", 2_380, "Rideshare", TransactionStatus.POSTED, group_id=52),
    DemoTransaction(58, "Starbucks", 1_425, "Coffee", TransactionStatus.PERSONAL),
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


def seed() -> tuple[int, int, date]:
    settings = get_settings()
    if settings.is_production_mode or not settings.database_url.startswith("sqlite"):
        raise RuntimeError("The Insights demo seeder only runs against the local SQLite database.")

    today = date.today()
    records = (*CURRENT_PERIOD, *PREVIOUS_PERIOD)
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

        for index, record in enumerate(records, start=1):
            payload = _split_payload(record.group_id) if record.group_id is not None else None
            db.add(
                ExpenseTransaction(
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
            )
        db.commit()
        return len(CURRENT_PERIOD), len(PREVIOUS_PERIOD), today


if __name__ == "__main__":
    current, previous, seeded_on = seed()
    print(
        f"Seeded {current} current-period and {previous} comparison-period "
        f"demo transactions for {seeded_on.isoformat()}."
    )

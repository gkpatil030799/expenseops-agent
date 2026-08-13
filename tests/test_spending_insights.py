import json
from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import ExpenseTransaction, PlaidItem, SplitwiseIntegration, User
from app.services.spending_insights_service import SpendingInsightsService


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'insights.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.info["workspace_id"] = 1
    user = User(email="viewer@example.com", display_name="Viewer")
    session.add(user)
    session.flush()
    session.info["user_id"] = user.id
    item = PlaidItem(workspace_id=1, item_id="item", institution_name="Bank")
    session.add(item)
    session.flush()
    return session, item


def _tx(
    item,
    transaction_id,
    amount,
    category,
    status="personal",
    payload=None,
    currency="USD",
):
    return ExpenseTransaction(
        workspace_id=1,
        plaid_transaction_id=transaction_id,
        plaid_item_id=item.id,
        account_id="checking",
        merchant_name=transaction_id,
        name=transaction_id,
        amount_cents=amount,
        iso_currency_code=currency,
        date=date(2026, 8, 10),
        pending=False,
        category=category,
        status=status,
        splitwise_payload_json=json.dumps(payload) if payload else None,
    )


def test_clean_spend_rules_and_totals_reconcile(tmp_path):
    db, item = _db(tmp_path)
    db.add_all(
        [
            _tx(item, "groceries", 10_000, "Groceries"),
            _tx(item, "refund", -2_000, "Groceries"),
            _tx(item, "transfer", 50_000, "Transfer"),
            _tx(item, "removed", 5_000, "Restaurants", status="removed"),
            _tx(item, "uncategorized", 1_000, None),
        ]
    )
    db.commit()

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 31)
    )

    assert result["summary"]["total_cents"] == 9_000
    assert sum(row["amount_cents"] for row in result["category_breakdown"]) == 9_000
    assert sum(row["total_cents"] for row in result["trend"]) == 9_000
    assert result["data_quality"]["uncategorized_cents"] == 1_000


def test_actual_share_uses_signed_in_viewers_owed_share_and_reports_unknown(tmp_path):
    db, item = _db(tmp_path)
    db.add(
        SplitwiseIntegration(
            workspace_id=1,
            user_id=db.info["user_id"],
            credentials_encrypted="encrypted",
            splitwise_user_id="2",
            verified_at=datetime.now(UTC),
        )
    )
    payload = {
        "users__0__user_id": 1,
        "users__0__paid_share": "100.00",
        "users__0__owed_share": "40.00",
        "users__1__user_id": 2,
        "users__1__paid_share": "0.00",
        "users__1__owed_share": "60.00",
    }
    db.add_all(
        [
            _tx(item, "shared-known", 10_000, "Restaurants", "posted", payload),
            _tx(item, "shared-unknown", 5_000, "Restaurants", "posted"),
        ]
    )
    db.commit()

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        spend_basis="actual_share",
    )

    assert result["summary"]["total_cents"] == 6_000
    assert result["data_quality"]["unknown_share_transactions"] == 1
    assert result["scope"]["viewer_share_identity_connected"] is True


def test_actual_share_never_guesses_without_viewer_identity(tmp_path):
    db, item = _db(tmp_path)
    payload = {
        "users__0__user_id": 1,
        "users__0__paid_share": "100.00",
        "users__0__owed_share": "40.00",
        "users__1__user_id": 2,
        "users__1__paid_share": "0.00",
        "users__1__owed_share": "60.00",
    }
    db.add(_tx(item, "shared", 10_000, "Restaurants", "posted", payload))
    db.commit()

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        spend_basis="actual_share",
    )

    assert result["summary"]["total_cents"] == 0
    assert result["data_quality"]["unknown_share_transactions"] == 1
    assert result["scope"]["viewer_share_identity_connected"] is False


def test_currencies_are_never_aggregated_and_can_be_selected(tmp_path):
    db, item = _db(tmp_path)
    db.add_all(
        [
            _tx(item, "usd", 10_000, "Groceries", currency="usd"),
            _tx(item, "eur", 7_500, "Restaurants", currency="EUR"),
        ]
    )
    db.commit()

    default_result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 31)
    )
    eur_result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        currency_code="eur",
    )

    assert default_result["scope"]["currency"] == "USD"
    assert default_result["scope"]["available_currencies"] == ["EUR", "USD"]
    assert default_result["scope"]["excluded_other_currency_transactions"] == 1
    assert default_result["summary"]["total_cents"] == 10_000
    assert eur_result["scope"]["currency"] == "EUR"
    assert eur_result["summary"]["total_cents"] == 7_500


def test_summary_reconciles_classified_unreviewed_and_refunds(tmp_path):
    db, item = _db(tmp_path)
    db.add_all(
        [
            _tx(item, "personal", 10_000, "Groceries"),
            _tx(item, "refund", -2_000, "Groceries"),
            _tx(item, "review", 3_000, "Restaurants", status="ask_user"),
        ]
    )
    db.commit()

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 31)
    )
    summary = result["summary"]

    assert summary["total_cents"] == 11_000
    assert summary["classified_cents"] == 8_000
    assert summary["unreviewed_cents"] == 3_000
    assert summary["refund_cents"] == -2_000
    assert summary["total_cents"] == summary["classified_cents"] + summary["unreviewed_cents"]


def test_pending_transactions_are_explicitly_excluded(tmp_path):
    db, item = _db(tmp_path)
    transaction = _tx(item, "pending", 2_500, "Restaurants")
    transaction.pending = True
    db.add(transaction)
    db.commit()

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 31)
    )

    assert result["summary"]["total_cents"] == 0
    assert result["data_quality"]["pending_transactions_excluded"] is True

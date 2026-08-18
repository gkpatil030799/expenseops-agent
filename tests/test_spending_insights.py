import json
from datetime import UTC, date, datetime

import pytest
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
    *,
    occurred_on=date(2026, 8, 10),
    pending=False,
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
        date=occurred_on,
        pending=pending,
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

    assert result["summary"]["total_cents"] == 11_000
    assert result["summary"]["credits_cents"] == 2_000
    assert sum(row["amount_cents"] for row in result["category_breakdown"]) == 11_000
    assert sum(row["total_cents"] for row in result["trend"]) == 11_000
    assert (
        sum(
            amount
            for bucket in result["category_trend"]
            for amount in bucket["categories"].values()
        )
        == 11_000
    )
    assert [row["name"] for row in result["merchant_breakdown"]] == [
        "groceries",
        "uncategorized",
    ]
    assert [row["name"] for row in result["category_breakdown"]] == [
        "Food & Dining",
        "Uncategorized",
    ]
    assert result["data_quality"]["uncategorized_cents"] == 1_000


def test_current_canonical_category_overrides_raw_provider_with_legacy_fallback(tmp_path):
    db, item = _db(tmp_path)
    canonical = _tx(item, "canonical-coffee", 900, "Travel")
    canonical.provider_category = "Travel / Lodging"
    canonical.spending_parent_category = "food_dining"
    canonical.classification_subcategory_name = "Coffee shops"
    canonical.classification_activity_type = "coffee_beverage"
    canonical.replenishment_eligibility = "not_replenishable"
    canonical.classification_applied_at = datetime.now(UTC)
    legacy = _tx(item, "legacy-travel", 1_100, "Travel")
    db.add_all([canonical, legacy])
    db.commit()

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 31)
    )

    assert result["category_breakdown"] == [
        {
            "name": "Travel",
            "amount_cents": 1_100,
            "transaction_count": 1,
            "percentage": 55.0,
            "previous_amount_cents": 0,
        },
        {
            "name": "Food & Dining",
            "amount_cents": 900,
            "transaction_count": 1,
            "percentage": 45.0,
            "previous_amount_cents": 0,
        },
    ]


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

    card_result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        spend_basis="card",
    )
    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        spend_basis="actual_share",
    )

    assert card_result["summary"]["total_cents"] == 15_000
    assert card_result["summary"]["shared_cents"] == 15_000
    assert result["summary"]["total_cents"] == 6_000
    assert result["summary"]["shared_cents"] == 6_000
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


def test_summary_reconciles_purchases_and_reports_credits_separately(tmp_path):
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

    assert summary["total_cents"] == 13_000
    assert summary["classified_cents"] == 10_000
    assert summary["unreviewed_cents"] == 3_000
    assert summary["credits_cents"] == 2_000
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


def test_known_payment_hierarchies_are_excluded_without_broad_payment_substrings(tmp_path):
    db, item = _db(tmp_path)
    excluded_categories = (
        "Payment / Credit Card",
        "Payments / Credit Card",
        "Payment / Loan",
        "LOAN_PAYMENTS / LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
        "TRANSFER_OUT / TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS",
    )
    db.add_all(
        [
            _tx(item, f"excluded-{index}", 20_000, category)
            for index, category in enumerate(excluded_categories)
        ]
        + [_tx(item, "real-purchase", 1_234, "Payment processing fee")]
    )
    db.commit()

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 31)
    )

    assert result["summary"]["total_cents"] == 1_234
    assert result["summary"]["transaction_count"] == 1
    assert result["merchant_breakdown"][0]["name"] == "real-purchase"


def test_incidental_transfer_detail_does_not_exclude_real_purchases(tmp_path):
    db, item = _db(tmp_path)
    db.add_all(
        [
            _tx(item, "airport", 1_000, "Travel / Airport transfer"),
            _tx(item, "shuttle", 2_000, "Transportation / Shuttle transfer"),
            _tx(item, "service", 3_000, "General Services / Wire transfer fee"),
            _tx(item, "legacy-transfer", 50_000, "Transfer / Bank transfer"),
            _tx(item, "pfc-transfer", 60_000, "TRANSFER_IN / TRANSFER_IN_CASH_ADVANCES"),
        ]
    )
    db.commit()

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 31)
    )

    assert result["summary"]["total_cents"] == 6_000
    assert result["summary"]["transaction_count"] == 3
    assert {row["name"] for row in result["category_breakdown"]} == {
        "Other",
        "Transportation",
        "Travel",
    }


def test_incidental_loan_payment_detail_does_not_exclude_service_purchase(tmp_path):
    db, item = _db(tmp_path)
    db.add_all(
        [
            _tx(item, "service-fee", 1_500, "General Services / Loan payment fee"),
            _tx(item, "exact-loan", 50_000, "Loan payment"),
            _tx(
                item,
                "pfc-loan",
                60_000,
                "LOAN_PAYMENTS / LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
            ),
            _tx(item, "legacy-loan", 70_000, "Payment / Loan"),
        ]
    )
    db.commit()

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 31)
    )

    assert result["summary"]["total_cents"] == 1_500
    assert result["summary"]["transaction_count"] == 1
    assert result["merchant_breakdown"][0]["name"] == "service-fee"


@pytest.mark.parametrize(
    ("category", "expected_parent"),
    [
        ("FOOD_AND_DRINK / FOOD_AND_DRINK_RESTAURANT", "Food & Dining"),
        ("FOOD_AND_DRINK / FOOD_AND_DRINK_GROCERIES", "Food & Dining"),
        ("GENERAL_MERCHANDISE / GENERAL_MERCHANDISE_SUPERSTORES", "Lifestyle"),
        ("GENERAL_MERCHANDISE / GENERAL_MERCHANDISE_ELECTRONICS", "Lifestyle"),
        ("PERSONAL_CARE / PERSONAL_CARE_HAIR_AND_BEAUTY", "Lifestyle"),
        ("PERSONAL_CARE / PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS", "Health"),
        ("ENTERTAINMENT / ENTERTAINMENT_SPORTING_EVENTS_AMUSEMENT_PARKS", "Lifestyle"),
        ("RENT_AND_UTILITIES / RENT_AND_UTILITIES_RENT", "Home & Bills"),
        ("TRANSPORTATION / TRANSPORTATION_GAS", "Transportation"),
        ("TRANSPORTATION / TRANSPORTATION_TAXIS_AND_RIDE_SHARES", "Transportation"),
        ("GENERAL_SERVICES / GENERAL_SERVICES_INSURANCE", "Other"),
        ("Uncategorized", "Uncategorized"),
    ],
)
def test_production_category_taxonomy_is_deterministic(tmp_path, category, expected_parent):
    db, item = _db(tmp_path)
    db.add(_tx(item, "row", 1_000, category))
    db.commit()

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 31)
    )

    assert result["category_breakdown"] == [
        {
            "name": expected_parent,
            "amount_cents": 1_000,
            "transaction_count": 1,
            "percentage": 100.0,
            "previous_amount_cents": 0,
        }
    ]


def test_credit_only_periods_never_create_negative_spending_or_breakdowns(tmp_path):
    db, item = _db(tmp_path)
    db.add_all(
        [
            _tx(item, "current-credit", -2_000, "Groceries"),
            _tx(
                item,
                "previous-credit",
                -3_000,
                "Groceries",
                occurred_on=date(2026, 7, 10),
            ),
        ]
    )
    db.commit()

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 31)
    )

    assert result["summary"]["total_cents"] == 0
    assert result["summary"]["credits_cents"] == 2_000
    assert result["comparison"]["total_cents"] == 0
    assert result["comparison"]["credits_cents"] == 3_000
    assert result["category_breakdown"] == []
    assert all(bucket["total_cents"] == 0 for bucket in result["trend"])


def test_previous_period_purchase_and_credit_use_purchase_only_comparison(tmp_path):
    db, item = _db(tmp_path)
    db.add_all(
        [
            _tx(item, "current-purchase", 6_000, "Groceries"),
            _tx(
                item,
                "previous-purchase",
                5_000,
                "Restaurants",
                occurred_on=date(2026, 7, 10),
            ),
            _tx(
                item,
                "previous-credit",
                -1_000,
                "Restaurants",
                occurred_on=date(2026, 7, 11),
            ),
        ]
    )
    db.commit()

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 31)
    )

    assert result["summary"]["total_cents"] == 6_000
    assert result["summary"]["credits_cents"] == 0
    assert result["comparison"]["total_cents"] == 5_000
    assert result["comparison"]["credits_cents"] == 1_000


def test_actual_share_zero_is_known_but_not_a_purchase_analytics_row(tmp_path):
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
    zero_share = {
        "users__0__user_id": 1,
        "users__0__paid_share": "10.00",
        "users__0__owed_share": "10.00",
        "users__1__user_id": 2,
        "users__1__paid_share": "0.00",
        "users__1__owed_share": "0.00",
    }
    db.add_all(
        [
            _tx(item, "zero-share", 1_000, "Restaurants", "posted", zero_share),
            _tx(item, "personal", 500, "Groceries"),
        ]
    )
    db.commit()

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        spend_basis="actual_share",
    )

    assert result["summary"]["total_cents"] == 500
    assert result["summary"]["transaction_count"] == 1
    assert result["data_quality"]["unknown_share_transactions"] == 0
    assert [row["name"] for row in result["category_breakdown"]] == ["Food & Dining"]
    assert [row["name"] for row in result["merchant_breakdown"]] == ["personal"]


def test_actual_share_omits_unallocatable_shared_credits_in_each_period(tmp_path):
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
    stale_purchase_share = {
        "users__0__user_id": 1,
        "users__0__paid_share": "100.00",
        "users__0__owed_share": "40.00",
        "users__1__user_id": 2,
        "users__1__paid_share": "0.00",
        "users__1__owed_share": "60.00",
    }
    db.add_all(
        [
            _tx(
                item,
                "current-credit",
                -2_000,
                "Restaurants",
                status="posted",
                payload=stale_purchase_share,
            ),
            _tx(
                item,
                "previous-credit",
                -3_000,
                "Restaurants",
                status="posted",
                payload=stale_purchase_share,
                occurred_on=date(2026, 7, 10),
            ),
        ]
    )
    db.commit()

    card = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 31), spend_basis="card"
    )
    actual = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        spend_basis="actual_share",
    )

    assert card["summary"]["credits_cents"] == 2_000
    assert card["comparison"]["credits_cents"] == 3_000
    assert actual["summary"]["credits_cents"] == 0
    assert actual["comparison"]["credits_cents"] == 0
    assert actual["summary"]["unknown_credit_share_transactions"] == 1
    assert actual["comparison"]["unknown_credit_share_transactions"] == 1
    assert actual["data_quality"]["unknown_credit_share_transactions"] == 1
    assert actual["data_quality"]["unknown_share_transactions"] == 0


def test_actual_share_reports_prior_period_purchase_omissions_separately(tmp_path):
    db, item = _db(tmp_path)
    db.add_all(
        [
            _tx(item, "current-personal", 10_000, "Groceries"),
            _tx(
                item,
                "previous-shared-unknown",
                10_000,
                "Restaurants",
                status="posted",
                occurred_on=date(2026, 7, 10),
            ),
        ]
    )
    db.commit()

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        spend_basis="actual_share",
    )

    assert result["summary"]["total_cents"] == 10_000
    assert result["summary"]["unknown_share_transactions"] == 0
    assert result["comparison"]["total_cents"] == 0
    assert result["comparison"]["unknown_share_transactions"] == 1
    assert result["data_quality"]["unknown_share_transactions"] == 0
    assert not any(change["kind"] in {"category", "mix"} for change in result["notable_changes"])
    assert not any("previous period" in change["detail"] for change in result["notable_changes"])


def test_notable_changes_include_previous_only_category_drops(tmp_path):
    db, item = _db(tmp_path)
    db.add_all(
        [
            _tx(item, "current-food", 1_000, "Groceries"),
            _tx(
                item,
                "previous-travel",
                5_000,
                "Travel",
                occurred_on=date(2026, 7, 10),
            ),
        ]
    )
    db.commit()

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 31)
    )

    assert any(
        change["kind"] == "category"
        and change["label"] == "Travel"
        and change["direction"] == "down"
        and change["amount_cents"] == -5_000
        for change in result["notable_changes"]
    )


def test_category_filter_exactly_matches_raw_or_mapped_parent_without_substrings(tmp_path):
    db, item = _db(tmp_path)
    db.add_all(
        [
            _tx(item, "groceries", 10_000, "Groceries"),
            _tx(item, "restaurants", 5_000, "Restaurants"),
            _tx(item, "shopping", 3_000, "Shopping"),
        ]
    )
    db.commit()

    groceries = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        category="Groceries",
    )
    restaurants = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        category="Restaurants",
    )
    parent = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        category="Food & Dining",
    )
    substring = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        category="Restaurant",
    )

    assert groceries["summary"]["total_cents"] == 10_000
    assert restaurants["summary"]["total_cents"] == 5_000
    assert parent["summary"]["total_cents"] == 15_000
    assert substring["summary"]["total_cents"] == 0


@pytest.mark.parametrize(
    ("end_date", "previous_start", "previous_end"),
    [
        (date(2026, 8, 10), "2026-08-03", "2026-08-03"),
        (date(2026, 8, 12), "2026-08-03", "2026-08-05"),
        (date(2026, 8, 16), "2026-08-03", "2026-08-09"),
    ],
    ids=["monday", "midweek", "sunday"],
)
def test_same_weekdays_last_week_comparison_mode_is_calendar_aligned(
    tmp_path,
    end_date,
    previous_start,
    previous_end,
):
    db, _item = _db(tmp_path)

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 10),
        end_date=end_date,
        comparison_mode="same_weekdays_last_week",
    )

    assert result["range"]["comparison_mode"] == "same_weekdays_last_week"
    assert result["range"]["previous_start_date"] == previous_start
    assert result["range"]["previous_end_date"] == previous_end


def test_default_comparison_mode_remains_immediately_preceding(tmp_path):
    db, _item = _db(tmp_path)

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 10),
        end_date=date(2026, 8, 12),
    )

    assert result["range"]["comparison_mode"] == "immediately_preceding"
    assert result["range"]["previous_start_date"] == "2026-08-07"
    assert result["range"]["previous_end_date"] == "2026-08-09"


def test_explicit_spending_comparison_uses_exact_canonical_period(tmp_path):
    db, item = _db(tmp_path)
    current = _tx(
        item,
        "current-bistro",
        3_000,
        "Restaurants",
        occurred_on=date(2026, 8, 10),
    )
    previous = _tx(
        item,
        "previous-bistro",
        1_700,
        "Restaurants",
        occurred_on=date(2026, 6, 15),
    )
    current.merchant_name = previous.merchant_name = "Bistro"
    db.add_all([current, previous])
    db.commit()

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 17),
        comparison_start_date=date(2026, 6, 1),
        comparison_end_date=date(2026, 6, 30),
    )

    assert result["range"]["previous_start_date"] == "2026-06-01"
    assert result["range"]["previous_end_date"] == "2026-06-30"
    assert result["summary"]["total_cents"] == 3_000
    assert result["comparison"]["total_cents"] == 1_700
    assert result["merchant_breakdown"][0]["previous_amount_cents"] == 1_700


@pytest.mark.parametrize(
    "comparison_values",
    [
        {"comparison_start_date": date(2026, 7, 1)},
        {
            "comparison_start_date": date(2026, 7, 31),
            "comparison_end_date": date(2026, 7, 1),
        },
        {
            "comparison_start_date": date(2024, 1, 1),
            "comparison_end_date": date(2026, 1, 2),
        },
        {
            "comparison_start_date": date(2026, 7, 1),
            "comparison_end_date": date(2026, 7, 31),
            "comparison_mode": "same_weekdays_last_week",
        },
    ],
    ids=["partial", "reversed", "over-bound", "mode-conflict"],
)
def test_explicit_spending_comparison_rejects_invalid_contract(
    tmp_path,
    comparison_values,
):
    db, _item = _db(tmp_path)

    with pytest.raises(ValueError):
        SpendingInsightsService(db).build(
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 17),
            **comparison_values,
        )


def test_merchant_breakdown_carries_canonical_previous_period_amounts(tmp_path):
    db, item = _db(tmp_path)
    current = _tx(item, "current-cafe", 2_500, "Restaurants")
    current.merchant_name = "Corner Cafe"
    previous = _tx(
        item,
        "previous-cafe",
        1_600,
        "Restaurants",
        occurred_on=date(2026, 7, 10),
    )
    previous.merchant_name = "Corner Cafe"
    db.add_all([current, previous])
    db.commit()

    result = SpendingInsightsService(db).build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
    )

    assert result["merchant_breakdown"] == [
        {
            "name": "Corner Cafe",
            "amount_cents": 2_500,
            "transaction_count": 1,
            "percentage": 100.0,
            "previous_amount_cents": 1_600,
        }
    ]

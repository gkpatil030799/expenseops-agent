import json
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import ExpenseTransaction, PlaidItem, SplitwiseIntegration, User
from app.services.lifestyle_dining_service import LifestyleDiningService


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'lifestyle.db'}")
    Base.metadata.create_all(engine)
    db = Session(engine)
    user = User(email="lifestyle@example.test", display_name="Lifestyle")
    db.add(user)
    db.flush()
    item = PlaidItem(workspace_id=1, item_id="lifestyle-item", institution_name="Bank")
    other = PlaidItem(workspace_id=2, item_id="other-item", institution_name="Other")
    db.add_all([item, other])
    db.flush()
    db.info.update(workspace_id=1, user_id=user.id)
    return db, item, other


def _tx(
    item,
    provider_id,
    amount,
    category,
    occurred_on,
    *,
    workspace_id=1,
    merchant=None,
    status="personal",
    payload=None,
):
    return ExpenseTransaction(
        workspace_id=workspace_id,
        plaid_transaction_id=provider_id,
        plaid_item_id=item.id,
        account_id="card",
        merchant_name=merchant or provider_id,
        name=merchant or provider_id,
        amount_cents=amount,
        iso_currency_code="USD",
        date=occurred_on,
        pending=False,
        category=category,
        status=status,
        splitwise_payload_json=json.dumps(payload) if payload else None,
    )


def test_lifestyle_uses_purchase_only_canonical_spend_and_preserves_uncertainty(tmp_path):
    db, item, _ = _db(tmp_path)
    db.add_all(
        [
            _tx(item, "coffee-current", 800, "FOOD_AND_DRINK / COFFEE", date(2026, 8, 12)),
            _tx(item, "coffee-prior", 600, "FOOD_AND_DRINK / COFFEE", date(2026, 8, 5)),
            _tx(item, "coffee-credit", -300, "FOOD_AND_DRINK / COFFEE", date(2026, 8, 13)),
            _tx(item, "restaurant", 4_000, "FOOD_AND_DRINK / RESTAURANT", date(2026, 8, 15)),
            _tx(item, "delivery", 2_000, "FOOD_AND_DRINK / FOOD_DELIVERY", date(2026, 8, 16)),
            _tx(item, "nightlife", 3_000, "FOOD_AND_DRINK / BAR", date(2026, 8, 14)),
            _tx(item, "uncertain", 1_100, "FOOD_AND_DRINK", date(2026, 8, 11)),
            _tx(item, "groceries", 9_000, "FOOD_AND_DRINK / GROCERIES", date(2026, 8, 10)),
        ]
    )
    db.commit()

    coffee = LifestyleDiningService(db).build(
        start_date=date(2026, 8, 9),
        end_date=date(2026, 8, 16),
        activity_type="coffee",
    )
    all_lifestyle = LifestyleDiningService(db).build(
        start_date=date(2026, 8, 9),
        end_date=date(2026, 8, 16),
        activity_type="all",
    )

    assert coffee["summary"]["total_cents"] == 800
    assert coffee["summary"]["credits_cents"] == 300
    assert coffee["comparison"]["total_cents"] == 600
    assert coffee["summary"]["transaction_count"] == 1
    assert all_lifestyle["summary"]["total_cents"] == 9_800
    assert sum(row["amount_cents"] for row in all_lifestyle["activities"]) == 9_800
    assert all_lifestyle["uncertain_transaction_count"] == 1
    assert "groceries" not in {row["name"] for row in all_lifestyle["top_merchants"]}


def test_lifestyle_merchant_limit_bounds_canonical_ranking(tmp_path):
    db, item, _ = _db(tmp_path)
    db.add_all(
        [
            _tx(
                item,
                f"restaurant-{index}",
                amount,
                "FOOD_AND_DRINK / RESTAURANT",
                date(2026, 8, 12),
                merchant=merchant,
            )
            for index, (merchant, amount) in enumerate(
                (("Mesa Kitchen", 6_000), ("Tempe Table", 3_000), ("Desert Grill", 1_000)),
                start=1,
            )
        ]
    )
    db.commit()

    result = LifestyleDiningService(db).build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 17),
        activity_type="restaurants",
        include_comparison=False,
        merchant_limit=2,
    )

    assert [row["name"] for row in result["top_merchants"]] == [
        "Mesa Kitchen",
        "Tempe Table",
    ]
    with pytest.raises(ValueError, match="merchant_limit"):
        LifestyleDiningService(db).build(
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 17),
            activity_type="restaurants",
            merchant_limit=9,
        )


def test_lifestyle_prefers_canonical_activity_over_conflicting_provider_label(tmp_path):
    db, item, _ = _db(tmp_path)
    coffee = _tx(
        item,
        "canonical-coffee",
        700,
        "FOOD_AND_DRINK / GROCERIES",
        date(2026, 8, 12),
    )
    coffee.provider_category = "FOOD_AND_DRINK / GROCERIES"
    coffee.spending_parent_category = "food_dining"
    coffee.classification_subcategory_name = "Coffee shops"
    coffee.classification_activity_type = "coffee_beverage"
    coffee.replenishment_eligibility = "not_replenishable"
    coffee.classification_applied_at = datetime.now(UTC)
    grocery = _tx(
        item,
        "canonical-grocery",
        2_000,
        "FOOD_AND_DRINK / RESTAURANT",
        date(2026, 8, 13),
    )
    grocery.provider_category = "FOOD_AND_DRINK / RESTAURANT"
    grocery.spending_parent_category = "food_dining"
    grocery.classification_subcategory_name = "Groceries"
    grocery.classification_activity_type = "grocery"
    grocery.replenishment_eligibility = "replenishable"
    grocery.classification_applied_at = datetime.now(UTC)
    db.add_all([coffee, grocery])
    db.commit()

    result = LifestyleDiningService(db).build(
        start_date=date(2026, 8, 9),
        end_date=date(2026, 8, 16),
        activity_type="coffee",
    )

    assert result["summary"]["total_cents"] == 700
    assert result["summary"]["transaction_count"] == 1
    assert result["top_merchants"] == [
        {
            "name": "canonical-coffee",
            "amount_cents": 700,
            "transaction_count": 1,
            "percentage": 100.0,
        }
    ]


def test_lifestyle_actual_share_reconciles_and_never_guesses_unknown_allocations(tmp_path):
    db, item, _ = _db(tmp_path)
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
            _tx(
                item,
                "known-shared-restaurant",
                10_000,
                "FOOD_AND_DRINK / RESTAURANT",
                date(2026, 8, 10),
                status="posted",
                payload=payload,
            ),
            _tx(
                item,
                "unknown-shared-restaurant",
                5_000,
                "FOOD_AND_DRINK / RESTAURANT",
                date(2026, 8, 11),
                status="posted",
            ),
            _tx(
                item,
                "unknown-shared-credit",
                -1_000,
                "FOOD_AND_DRINK / RESTAURANT",
                date(2026, 8, 12),
                status="posted",
            ),
        ]
    )
    db.commit()

    result = LifestyleDiningService(db).build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 16),
        activity_type="restaurants",
        spend_basis="actual_share",
    )

    assert result["summary"]["total_cents"] == 6_000
    assert result["summary"]["shared_cents"] == 6_000
    assert result["summary"]["credits_cents"] == 0
    assert result["summary"]["unknown_share_transactions"] == 1
    assert result["summary"]["unknown_credit_share_transactions"] == 1


def test_lifestyle_copy_is_factual_and_not_judgmental(tmp_path):
    db, item, _ = _db(tmp_path)
    db.add(_tx(item, "Cafe Local", 500, "FOOD_AND_DRINK / COFFEE", date(2026, 8, 12)))
    db.commit()

    result = LifestyleDiningService(db).build(
        start_date=date(2026, 8, 9), end_date=date(2026, 8, 16), activity_type="coffee"
    )
    text = " ".join(result["observations"]).casefold()

    assert "coffee purchases" in text
    assert not any(
        word in text
        for word in ("addict", "problem", "unhealthy", "relationship", "religion", "moral")
    )


def test_lifestyle_merchant_changes_use_canonical_purchase_deltas(tmp_path):
    db, item, _ = _db(tmp_path)
    db.add_all(
        [
            _tx(
                item,
                "current-bistro-1",
                3_000,
                "FOOD_AND_DRINK / RESTAURANT",
                date(2026, 8, 12),
                merchant="Bistro",
            ),
            _tx(
                item,
                "current-bistro-2",
                2_000,
                "FOOD_AND_DRINK / RESTAURANT",
                date(2026, 8, 13),
                merchant="Bistro",
            ),
            _tx(
                item,
                "current-bistro-credit",
                -1_500,
                "FOOD_AND_DRINK / RESTAURANT",
                date(2026, 8, 14),
                merchant="Bistro",
            ),
            _tx(
                item,
                "prior-bistro",
                2_000,
                "FOOD_AND_DRINK / RESTAURANT",
                date(2026, 8, 5),
                merchant="Bistro",
            ),
            _tx(
                item,
                "prior-diner",
                2_500,
                "FOOD_AND_DRINK / RESTAURANT",
                date(2026, 8, 6),
                merchant="Diner",
            ),
        ]
    )
    db.commit()

    result = LifestyleDiningService(db).build(
        start_date=date(2026, 8, 9),
        end_date=date(2026, 8, 16),
        activity_type="restaurants",
    )

    assert result["summary"]["total_cents"] == 5_000
    assert result["summary"]["credits_cents"] == 1_500
    assert result["comparison"]["total_cents"] == 4_500
    assert result["merchant_changes"] == [
        {
            "name": "Bistro",
            "current_amount_cents": 5_000,
            "previous_amount_cents": 2_000,
            "delta_cents": 3_000,
            "current_transaction_count": 2,
            "previous_transaction_count": 1,
        },
        {
            "name": "Diner",
            "current_amount_cents": 0,
            "previous_amount_cents": 2_500,
            "delta_cents": -2_500,
            "current_transaction_count": 0,
            "previous_transaction_count": 1,
        },
    ]

    without_comparison = LifestyleDiningService(db).build(
        start_date=date(2026, 8, 9),
        end_date=date(2026, 8, 16),
        activity_type="restaurants",
        include_comparison=False,
    )
    assert without_comparison["merchant_changes"] == []


def test_lifestyle_explicit_comparison_uses_exact_canonical_period(tmp_path):
    db, item, _ = _db(tmp_path)
    db.add_all(
        [
            _tx(
                item,
                "current-bistro",
                5_000,
                "FOOD_AND_DRINK / RESTAURANT",
                date(2026, 8, 10),
                merchant="Bistro",
            ),
            _tx(
                item,
                "previous-bistro",
                2_100,
                "FOOD_AND_DRINK / RESTAURANT",
                date(2026, 6, 15),
                merchant="Bistro",
            ),
        ]
    )
    db.commit()

    result = LifestyleDiningService(db).build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 17),
        activity_type="restaurants",
        comparison_start_date=date(2026, 6, 1),
        comparison_end_date=date(2026, 6, 30),
    )

    assert result["previous_start_date"] == "2026-06-01"
    assert result["previous_end_date"] == "2026-06-30"
    assert result["summary"]["total_cents"] == 5_000
    assert result["comparison"]["total_cents"] == 2_100
    assert result["merchant_changes"][0]["delta_cents"] == 2_900


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
            "include_comparison": False,
        },
    ],
    ids=["partial", "reversed", "over-bound", "comparison-disabled"],
)
def test_lifestyle_explicit_comparison_rejects_invalid_contract(
    tmp_path,
    comparison_values,
):
    db, _item, _other = _db(tmp_path)

    with pytest.raises(ValueError):
        LifestyleDiningService(db).build(
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 17),
            **comparison_values,
        )


def test_lifestyle_accepts_bounded_legacy_subtype_categories_without_merchant_catalog(
    tmp_path,
):
    db, item, _ = _db(tmp_path)
    db.add_all(
        [
            _tx(item, "legacy-coffee", 700, "Coffee", date(2026, 8, 12)),
            _tx(item, "legacy-bar", 1_800, "Bars", date(2026, 8, 13)),
            _tx(item, "legacy-grocery", 2_500, "Groceries / Coffee", date(2026, 8, 14)),
        ]
    )
    db.commit()

    result = LifestyleDiningService(db).build(
        start_date=date(2026, 8, 9), end_date=date(2026, 8, 16), activity_type="all"
    )

    assert result["summary"]["total_cents"] == 2_500
    assert {row["name"] for row in result["activities"]} == {"coffee", "nightlife"}

from app.services.share_calculator import (
    build_equal_split_shares,
    build_splitwise_by_shares_payload,
    cents_to_decimal_string,
    decimal_to_cents,
    equal_owed_cents,
)


def test_decimal_to_cents_rounds_to_nearest_cent():
    assert decimal_to_cents("10.235") == 1024
    assert decimal_to_cents("10.234") == 1023


def test_equal_owed_cents_distributes_remainder():
    assert equal_owed_cents(1000, 3) == [334, 333, 333]


def test_build_equal_split_shares_includes_payer_once():
    shares = build_equal_split_shares(
        total_cents=6000, payer_user_id=111, participant_user_ids=[111, 222, 333]
    )
    assert [(s.user_id, s.paid_cents, s.owed_cents) for s in shares] == [
        (111, 6000, 2000),
        (222, 0, 2000),
        (333, 0, 2000),
    ]


def test_build_equal_split_shares_adds_missing_payer():
    shares = build_equal_split_shares(
        total_cents=900, payer_user_id=111, participant_user_ids=[222, 333]
    )
    assert [s.user_id for s in shares] == [111, 222, 333]
    assert sum(s.owed_cents for s in shares) == 900
    assert sum(s.paid_cents for s in shares) == 900


def test_build_splitwise_by_shares_payload():
    shares = build_equal_split_shares(
        total_cents=6000, payer_user_id=111, participant_user_ids=[222, 333]
    )
    payload = build_splitwise_by_shares_payload(
        total_cents=6000,
        description="Dinner",
        details="Created by test",
        date_iso="2026-05-21T00:00:00Z",
        currency_code="USD",
        shares=shares,
    )
    assert payload["cost"] == "60.00"
    assert payload["group_id"] == 0
    assert payload["users__0__user_id"] == 111
    assert payload["users__0__paid_share"] == "60.00"
    assert payload["users__0__owed_share"] == "20.00"
    assert payload["users__2__owed_share"] == "20.00"


def test_cents_to_decimal_string():
    assert cents_to_decimal_string(5) == "0.05"
    assert cents_to_decimal_string(1234) == "12.34"

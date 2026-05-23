from decimal import Decimal

from app.services.share_calculator import (
    CustomSplitInput,
    build_custom_split_shares_by_mode,
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


def test_custom_equal_split_with_payer_included():
    shares = build_custom_split_shares_by_mode(
        total_cents=1000,
        payer_user_id=111,
        payer_included=True,
        split_mode="equal",
        participant_splits=[CustomSplitInput(user_id=222)],
    )

    assert [(share.user_id, share.paid_cents, share.owed_cents) for share in shares] == [
        (111, 1000, 500),
        (222, 0, 500),
    ]


def test_custom_equal_split_with_payer_excluded():
    shares = build_custom_split_shares_by_mode(
        total_cents=1000,
        payer_user_id=111,
        payer_included=False,
        split_mode="equal",
        participant_splits=[CustomSplitInput(user_id=222), CustomSplitInput(user_id=333)],
    )

    assert [(share.user_id, share.paid_cents, share.owed_cents) for share in shares] == [
        (222, 0, 500),
        (333, 0, 500),
        (111, 1000, 0),
    ]


def test_custom_exact_amount_validation_success_and_failure():
    shares = build_custom_split_shares_by_mode(
        total_cents=1000,
        payer_user_id=111,
        payer_included=False,
        split_mode="exact_amounts",
        participant_splits=[
            CustomSplitInput(user_id=222, amount_cents=600),
            CustomSplitInput(user_id=333, amount_cents=400),
        ],
    )
    assert sum(share.owed_cents for share in shares) == 1000

    try:
        build_custom_split_shares_by_mode(
            total_cents=1000,
            payer_user_id=111,
            payer_included=False,
            split_mode="exact_amounts",
            participant_splits=[CustomSplitInput(user_id=222, amount_cents=900)],
        )
    except ValueError as exc:
        assert "exact amounts" in str(exc)
    else:
        raise AssertionError("expected exact amount validation failure")


def test_custom_percentage_validation_success_and_failure():
    shares = build_custom_split_shares_by_mode(
        total_cents=999,
        payer_user_id=111,
        payer_included=False,
        split_mode="percentages",
        participant_splits=[
            CustomSplitInput(user_id=222, percentage=Decimal("60")),
            CustomSplitInput(user_id=333, percentage=Decimal("40")),
        ],
    )
    assert [share.owed_cents for share in shares if share.user_id != 111] == [599, 400]

    try:
        build_custom_split_shares_by_mode(
            total_cents=999,
            payer_user_id=111,
            payer_included=False,
            split_mode="percentages",
            participant_splits=[CustomSplitInput(user_id=222, percentage=Decimal("99"))],
        )
    except ValueError as exc:
        assert "percentages" in str(exc)
    else:
        raise AssertionError("expected percentage validation failure")


def test_custom_share_based_split():
    shares = build_custom_split_shares_by_mode(
        total_cents=1200,
        payer_user_id=111,
        payer_included=True,
        split_mode="shares",
        participant_splits=[
            CustomSplitInput(user_id=111, shares=Decimal("1")),
            CustomSplitInput(user_id=222, shares=Decimal("2")),
            CustomSplitInput(user_id=333, shares=Decimal("1")),
        ],
    )

    assert [(share.user_id, share.owed_cents) for share in shares] == [
        (111, 300),
        (222, 600),
        (333, 300),
    ]


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

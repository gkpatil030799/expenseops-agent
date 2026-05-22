from app.services.recommendation_service import classify_transaction_recommendation


def test_classifies_coffee_as_likely_personal():
    result = classify_transaction_recommendation(
        merchant_name="Starbucks",
        name="Coffee",
        amount_cents=725,
    )

    assert result.suggestion == "likely_personal"


def test_classifies_big_box_grocery_as_likely_shared():
    result = classify_transaction_recommendation(
        merchant_name="Costco",
        name="Warehouse purchase",
        amount_cents=12345,
        category="Shops, Groceries",
    )

    assert result.suggestion == "likely_shared"


def test_classifies_uber_as_likely_shared():
    result = classify_transaction_recommendation(
        merchant_name="Uber",
        name="Trip",
        amount_cents=1875,
    )

    assert result.suggestion == "likely_shared"


def test_classifies_restaurant_over_40_as_likely_shared():
    result = classify_transaction_recommendation(
        merchant_name="Local Grill",
        name="Restaurant",
        amount_cents=4100,
    )

    assert result.suggestion == "likely_shared"


def test_classifies_unknown_merchant_as_unsure():
    result = classify_transaction_recommendation(
        merchant_name=None,
        name="Unknown transaction",
        amount_cents=1299,
    )

    assert result.suggestion == "unsure"

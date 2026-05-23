from app.api import transaction_routes
from app.models import ExpenseTransaction, TransactionStatus, utc_now
from app.schemas import CustomSplitRequest


def _tx(transaction_id: int = 123) -> ExpenseTransaction:
    tx = ExpenseTransaction(
        id=transaction_id,
        plaid_transaction_id=f"plaid-{transaction_id}",
        plaid_item_id=1,
        name="Costco",
        amount_cents=5500,
    )
    tx.iso_currency_code = "USD"
    tx.pending = False
    tx.created_at = utc_now()
    tx.updated_at = utc_now()
    tx.status = TransactionStatus.SHARED_DRAFT.value
    return tx


def test_custom_split_endpoint_preview(monkeypatch):
    calls = {}

    class FakeTransactionService:
        def __init__(self, db):
            pass

        def create_custom_split_expense(self, **kwargs):
            calls.update(kwargs)
            return _tx(kwargs["tx_id"]), {"draft": True, "payload": {"cost": "55.00"}}

    monkeypatch.setattr(transaction_routes, "TransactionService", FakeTransactionService)

    response = transaction_routes.split_custom(
        123,
        CustomSplitRequest.model_validate(
            {
            "payer_user_id": 1,
            "payer_included": False,
            "split_mode": "exact_amounts",
            "participant_splits": [{"user_id": 2, "amount": "55.00"}],
            "confirm": False,
            }
        ),
        db=object(),
    )

    assert calls["split_mode"] == "exact_amounts"
    assert calls["payer_included"] is False
    assert calls["confirm"] is False
    assert response.splitwise_response["draft"] is True


def test_custom_split_endpoint_post(monkeypatch):
    calls = {}

    class FakeTransactionService:
        def __init__(self, db):
            pass

        def create_custom_split_expense(self, **kwargs):
            calls.update(kwargs)
            tx = _tx(kwargs["tx_id"])
            tx.status = TransactionStatus.POSTED.value
            tx.splitwise_expense_id = "expense-1"
            return tx, {"expenses": [{"id": "expense-1"}]}

    monkeypatch.setattr(transaction_routes, "TransactionService", FakeTransactionService)

    response = transaction_routes.split_custom(
        123,
        CustomSplitRequest.model_validate(
            {
            "payer_user_id": 1,
            "payer_included": True,
            "split_mode": "percentages",
            "participant_splits": [
                {"user_id": 1, "percentage": "50"},
                {"user_id": 2, "percentage": "50"},
            ],
            "confirm": True,
            }
        ),
        db=object(),
    )

    assert calls["confirm"] is True
    assert response.splitwise_expense_id == "expense-1"

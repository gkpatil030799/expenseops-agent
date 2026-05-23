import pytest

from app.config import Settings
from app.models import ExpenseTransaction, PlaidItem, TransactionStatus
from app.services import transaction_service
from app.services.splitwise_service import SplitwiseAPIError
from app.services.transaction_service import (
    TransactionError,
    TransactionService,
    _plaid_token_environment,
    can_undo_transaction,
)


def test_plaid_token_environment_is_inferred_from_token_prefix():
    assert _plaid_token_environment("access-sandbox-token") == "sandbox"
    assert _plaid_token_environment("access-production-token") == "production"
    assert _plaid_token_environment("access-development-token") == "development"
    assert _plaid_token_environment("access-custom-token") is None


def test_sync_all_items_skips_items_from_different_plaid_environment(monkeypatch):
    sandbox_item = PlaidItem(
        item_id="sandbox-item",
        access_token_encrypted="encrypted-sandbox",
        institution_name="Sandbox Bank",
    )
    production_item = PlaidItem(
        item_id="production-item",
        access_token_encrypted="encrypted-production",
        institution_name="Production Bank",
    )
    token_by_encrypted_value = {
        "encrypted-sandbox": "access-sandbox-token",
        "encrypted-production": "access-production-token",
    }

    class FakeScalars:
        def __iter__(self):
            return iter([sandbox_item, production_item])

    class FakeExecuteResult:
        def scalars(self):
            return FakeScalars()

    class FakeDb:
        def execute(self, _query):
            return FakeExecuteResult()

        def commit(self):
            pass

    class FakePlaidService:
        def transactions_sync(self, *, access_token, cursor):
            assert access_token == "access-production-token"
            assert cursor is None
            return {
                "added": [],
                "modified": [],
                "removed": [],
                "next_cursor": "cursor",
                "has_more": False,
            }

    monkeypatch.setattr(
        transaction_service,
        "decrypt_secret",
        lambda encrypted: token_by_encrypted_value[encrypted],
    )

    service = TransactionService(
        FakeDb(),
        settings=Settings(plaid_env="production"),
        plaid_service=FakePlaidService(),
        splitwise_service=object(),
    )

    result = service.sync_all_items()

    assert result["sandbox-item"]["skipped"] == 1
    assert "linked in sandbox" in result["sandbox-item"]["reason"]
    assert result["production-item"] == {"added": 0, "modified": 0, "removed": 0}


def make_tx(status: str, splitwise_expense_id: str | None = None) -> ExpenseTransaction:
    tx = ExpenseTransaction(
        id=1,
        plaid_transaction_id="tx-1",
        plaid_item_id=1,
        name="Uber",
        amount_cents=633,
    )
    tx.status = status
    tx.splitwise_expense_id = splitwise_expense_id
    return tx


class FakeDb:
    def __init__(self, tx):
        self.tx = tx
        self.commits = 0

    def get(self, _model, tx_id):
        return self.tx if tx_id == 1 else None

    def commit(self):
        self.commits += 1

    def refresh(self, _tx):
        pass


def test_can_undo_transaction_helper():
    assert can_undo_transaction(make_tx(TransactionStatus.PERSONAL.value)) is True
    assert can_undo_transaction(make_tx(TransactionStatus.POSTED.value)) is True
    assert can_undo_transaction(make_tx(TransactionStatus.SHARED_DRAFT.value)) is True
    assert can_undo_transaction(make_tx(TransactionStatus.ASK_USER.value)) is False


def test_undo_personal_transaction_moves_back_to_review():
    tx = make_tx(TransactionStatus.PERSONAL.value)

    result = TransactionService(FakeDb(tx), splitwise_service=object()).undo_transaction(1)

    assert result.status == TransactionStatus.ASK_USER.value
    assert result.splitwise_expense_id is None


def test_undo_posted_person_split_deletes_splitwise_expense():
    calls = []
    tx = make_tx(TransactionStatus.POSTED.value, splitwise_expense_id="expense-1")

    class FakeSplitwise:
        def delete_expense(self, expense_id):
            calls.append(expense_id)
            return {"success": True}

    result = TransactionService(FakeDb(tx), splitwise_service=FakeSplitwise()).undo_transaction(1)

    assert calls == ["expense-1"]
    assert result.status == TransactionStatus.ASK_USER.value
    assert result.splitwise_expense_id is None


def test_undo_posted_group_split_deletes_group_expense():
    calls = []
    tx = make_tx(TransactionStatus.POSTED.value, splitwise_expense_id="group-expense-1")
    tx.splitwise_payload_json = '{"group_id": 44}'

    class FakeSplitwise:
        def delete_expense(self, expense_id):
            calls.append(expense_id)
            return {"success": True}

    result = TransactionService(FakeDb(tx), splitwise_service=FakeSplitwise()).undo_transaction(1)

    assert calls == ["group-expense-1"]
    assert result.status == TransactionStatus.ASK_USER.value
    assert result.splitwise_expense_id is None


def test_splitwise_delete_failure_preserves_local_state():
    tx = make_tx(TransactionStatus.POSTED.value, splitwise_expense_id="expense-1")
    db = FakeDb(tx)

    class FakeSplitwise:
        def delete_expense(self, expense_id):
            raise SplitwiseAPIError("delete failed")

    with pytest.raises(TransactionError):
        TransactionService(db, splitwise_service=FakeSplitwise()).undo_transaction(1)

    assert tx.status == TransactionStatus.POSTED.value
    assert tx.splitwise_expense_id == "expense-1"
    assert db.commits == 0


def test_undo_ask_user_is_rejected():
    tx = make_tx(TransactionStatus.ASK_USER.value)

    with pytest.raises(TransactionError, match="cannot be undone"):
        TransactionService(FakeDb(tx), splitwise_service=object()).undo_transaction(1)


def test_undo_unknown_transaction_raises_error():
    with pytest.raises(TransactionError, match="not found"):
        TransactionService(FakeDb(None), splitwise_service=object()).undo_transaction(999)


def test_custom_split_duplicate_post_is_rejected():
    tx = make_tx(TransactionStatus.POSTED.value, splitwise_expense_id="expense-1")

    class FakeSplitwise:
        def get_current_user(self):
            return {"id": 111}

    service = TransactionService(FakeDb(tx), splitwise_service=FakeSplitwise())
    with pytest.raises(TransactionError, match="already posted"):
        service.create_custom_split_expense(
            tx_id=1,
            participant_splits=[],
            split_mode="equal",
            payer_included=False,
            payer_user_id=111,
            owed_by_user_id={222: 633},
            group_id=None,
            description=None,
            details=None,
            currency_code=None,
            confirm=True,
            post_pending=False,
        )

import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base
from app.models import ExpenseTransaction, PlaidItem, TransactionStatus
from app.services import transaction_service
from app.services.share_calculator import CustomSplitInput
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

        def all(self):
            return []

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
    assert result["production-item"] == {
        "added": 0,
        "modified": 0,
        "removed": 0,
        "notification_eligible": 0,
        "notification_sent": 0,
        "notification_skipped": 0,
    }


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


class FakeNotificationService:
    def __init__(self):
        self.review_notifications = []
        self.posted_notifications = []

    def notify_transaction_needs_review(self, tx):
        self.review_notifications.append(tx.id)
        return True

    def notify_splitwise_posted(self, tx, expense_id):
        self.posted_notifications.append((tx.id, expense_id))


def _sqlite_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'transaction-service.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    item = PlaidItem(
        id=1,
        item_id="item-1",
        access_token_encrypted="encrypted",
        institution_name="Test Bank",
    )
    db.add(item)
    db.commit()
    return db


def _plaid_tx(transaction_id: str, *, pending: bool, name: str = "Trader Joe's"):
    return {
        "transaction_id": transaction_id,
        "account_id": "account-1",
        "name": name,
        "merchant_name": name,
        "amount": "53.87",
        "iso_currency_code": "USD",
        "date": "2026-05-24",
        "pending": pending,
        "payment_channel": "in store",
        "category": ["Shops"],
    }


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


def test_new_pending_transaction_is_saved_without_review_notification(tmp_path):
    db = _sqlite_session(tmp_path)
    notifications = FakeNotificationService()
    service = TransactionService(
        db,
        splitwise_service=object(),
        notification_service=notifications,
    )

    created = service.upsert_transaction(
        db.get(PlaidItem, 1),
        _plaid_tx("tx-pending", pending=True),
    )
    tx = db.query(ExpenseTransaction).filter_by(plaid_transaction_id="tx-pending").one()

    assert created is True
    assert tx.pending is True
    assert tx.status == TransactionStatus.ASK_USER.value
    assert notifications.review_notifications == []
    db.close()


def test_new_settled_transaction_sends_review_notification(tmp_path):
    db = _sqlite_session(tmp_path)
    notifications = FakeNotificationService()
    service = TransactionService(
        db,
        splitwise_service=object(),
        notification_service=notifications,
    )

    created = service.upsert_transaction(
        db.get(PlaidItem, 1),
        _plaid_tx("tx-settled", pending=False),
    )
    tx = db.query(ExpenseTransaction).filter_by(plaid_transaction_id="tx-settled").one()

    assert created is True
    assert tx.pending is False
    assert tx.status == TransactionStatus.ASK_USER.value
    assert notifications.review_notifications == [tx.id]
    assert tx.review_notification_sent_at is not None
    db.close()


def test_pending_to_settled_transaction_sends_review_notification_once(tmp_path):
    db = _sqlite_session(tmp_path)
    notifications = FakeNotificationService()
    service = TransactionService(
        db,
        splitwise_service=object(),
        notification_service=notifications,
    )

    service.upsert_transaction(db.get(PlaidItem, 1), _plaid_tx("tx-transition", pending=True))
    tx = db.query(ExpenseTransaction).filter_by(plaid_transaction_id="tx-transition").one()
    assert notifications.review_notifications == []

    created = service.upsert_transaction(
        db.get(PlaidItem, 1),
        _plaid_tx("tx-transition", pending=False),
    )
    db.refresh(tx)

    assert created is False
    assert tx.pending is False
    assert notifications.review_notifications == [tx.id]

    service.upsert_transaction(db.get(PlaidItem, 1), _plaid_tx("tx-transition", pending=False))

    assert notifications.review_notifications == [tx.id]
    db.close()


def test_existing_settled_transaction_update_does_not_duplicate_review_notification(tmp_path):
    db = _sqlite_session(tmp_path)
    notifications = FakeNotificationService()
    service = TransactionService(
        db,
        splitwise_service=object(),
        notification_service=notifications,
    )

    service.upsert_transaction(db.get(PlaidItem, 1), _plaid_tx("tx-no-duplicate", pending=False))
    tx = db.query(ExpenseTransaction).filter_by(plaid_transaction_id="tx-no-duplicate").one()
    assert notifications.review_notifications == [tx.id]
    first_notified_at = tx.review_notification_sent_at

    service.upsert_transaction(db.get(PlaidItem, 1), _plaid_tx("tx-no-duplicate", pending=False))
    db.refresh(tx)

    assert notifications.review_notifications == [tx.id]
    assert tx.review_notification_sent_at == first_notified_at
    db.close()


def test_personal_transaction_is_not_notified_again_after_plaid_modified_sync(
    tmp_path,
    monkeypatch,
    caplog,
):
    db = _sqlite_session(tmp_path)
    item = db.get(PlaidItem, 1)
    tx = ExpenseTransaction(
        plaid_transaction_id="tx-personal-modified",
        plaid_item_id=item.id,
        name="Old merchant",
        amount_cents=1200,
        pending=False,
        status=TransactionStatus.PERSONAL.value,
    )
    db.add(tx)
    db.commit()
    notifications = FakeNotificationService()

    class FakePlaidService:
        def transactions_sync(self, *, access_token, cursor):
            return {
                "added": [],
                "modified": [_plaid_tx("tx-personal-modified", pending=False, name="Updated")],
                "removed": [],
                "next_cursor": "cursor",
                "has_more": False,
            }

    monkeypatch.setattr(transaction_service, "decrypt_secret", lambda _encrypted: "access-token")
    service = TransactionService(
        db,
        plaid_service=FakePlaidService(),
        splitwise_service=object(),
        notification_service=notifications,
    )

    with caplog.at_level(logging.INFO):
        result = service.sync_item(item)

    db.refresh(tx)
    assert result["modified"] == 1
    assert result["notification_eligible"] == 0
    assert notifications.review_notifications == []
    assert tx.status == TransactionStatus.PERSONAL.value
    assert tx.name == "Updated"
    assert any(
        getattr(record, "event", None) == "transaction_status_preserved_on_plaid_update"
        and record.log_metadata["status"] == TransactionStatus.PERSONAL.value
        for record in caplog.records
    )
    assert any(
        getattr(record, "event", None) == "transaction_notification_skipped_personal"
        for record in caplog.records
    )
    db.close()


def test_posted_transaction_is_not_notified_again_after_plaid_modified_sync(
    tmp_path,
    monkeypatch,
    caplog,
):
    db = _sqlite_session(tmp_path)
    item = db.get(PlaidItem, 1)
    tx = ExpenseTransaction(
        plaid_transaction_id="tx-posted-modified",
        plaid_item_id=item.id,
        name="Old posted",
        amount_cents=1200,
        pending=False,
        status=TransactionStatus.POSTED.value,
        splitwise_expense_id="splitwise-expense-1",
    )
    db.add(tx)
    db.commit()
    notifications = FakeNotificationService()

    class FakePlaidService:
        def transactions_sync(self, *, access_token, cursor):
            return {
                "added": [],
                "modified": [_plaid_tx("tx-posted-modified", pending=False, name="Updated posted")],
                "removed": [],
                "next_cursor": "cursor",
                "has_more": False,
            }

    monkeypatch.setattr(transaction_service, "decrypt_secret", lambda _encrypted: "access-token")
    service = TransactionService(
        db,
        plaid_service=FakePlaidService(),
        splitwise_service=object(),
        notification_service=notifications,
    )

    with caplog.at_level(logging.INFO):
        result = service.sync_item(item)

    db.refresh(tx)
    assert result["modified"] == 1
    assert result["notification_eligible"] == 0
    assert notifications.review_notifications == []
    assert tx.status == TransactionStatus.POSTED.value
    assert tx.splitwise_expense_id == "splitwise-expense-1"
    assert tx.name == "Updated posted"
    assert any(
        getattr(record, "event", None) == "transaction_notification_skipped_posted"
        for record in caplog.records
    )
    db.close()


def test_pending_to_settled_personal_transaction_does_not_notify(tmp_path):
    db = _sqlite_session(tmp_path)
    notifications = FakeNotificationService()
    service = TransactionService(
        db,
        splitwise_service=object(),
        notification_service=notifications,
    )

    service.upsert_transaction(
        db.get(PlaidItem, 1),
        _plaid_tx("tx-personal-pending-settled", pending=True),
    )
    tx = db.query(ExpenseTransaction).filter_by(
        plaid_transaction_id="tx-personal-pending-settled"
    ).one()
    tx.status = TransactionStatus.PERSONAL.value
    db.commit()

    service.upsert_transaction(
        db.get(PlaidItem, 1),
        _plaid_tx("tx-personal-pending-settled", pending=False),
    )
    db.refresh(tx)

    assert tx.pending is False
    assert tx.status == TransactionStatus.PERSONAL.value
    assert notifications.review_notifications == []
    db.close()


def test_shared_draft_ready_transaction_can_notify_once(tmp_path):
    db = _sqlite_session(tmp_path)
    item = db.get(PlaidItem, 1)
    tx = ExpenseTransaction(
        plaid_transaction_id="tx-shared-draft-ready",
        plaid_item_id=item.id,
        name="Draft merchant",
        amount_cents=2200,
        pending=False,
        status=TransactionStatus.SHARED_DRAFT.value,
        splitwise_payload_json="{}",
    )
    db.add(tx)
    db.commit()
    notifications = FakeNotificationService()
    service = TransactionService(
        db,
        splitwise_service=object(),
        notification_service=notifications,
    )

    first = service._notify_ready_transactions_for_item(item)
    second = service._notify_ready_transactions_for_item(item)
    db.refresh(tx)

    assert first == {"eligible": 1, "sent": 1, "skipped": 0}
    assert second == {"eligible": 0, "sent": 0, "skipped": 0}
    assert notifications.review_notifications == [tx.id]
    assert tx.review_notification_sent_at is not None
    db.close()


def test_duplicate_webhook_does_not_renotify_resolved_transaction(tmp_path, monkeypatch):
    db = _sqlite_session(tmp_path)
    item = db.get(PlaidItem, 1)
    tx = ExpenseTransaction(
        plaid_transaction_id="tx-resolved-repeat",
        plaid_item_id=item.id,
        name="Resolved merchant",
        amount_cents=3300,
        pending=False,
        status=TransactionStatus.POSTED.value,
        splitwise_expense_id="expense-repeat",
    )
    db.add(tx)
    db.commit()
    notifications = FakeNotificationService()

    class FakePlaidService:
        def transactions_sync(self, *, access_token, cursor):
            return {
                "added": [],
                "modified": [_plaid_tx("tx-resolved-repeat", pending=False)],
                "removed": [],
                "next_cursor": "cursor",
                "has_more": False,
            }

    monkeypatch.setattr(transaction_service, "decrypt_secret", lambda _encrypted: "access-token")
    service = TransactionService(
        db,
        plaid_service=FakePlaidService(),
        splitwise_service=object(),
        notification_service=notifications,
    )

    first = service.sync_item(item)
    second = service.sync_item(item)
    db.refresh(tx)

    assert first["modified"] == 1
    assert second["modified"] == 1
    assert first["notification_eligible"] == 0
    assert second["notification_eligible"] == 0
    assert notifications.review_notifications == []
    assert tx.status == TransactionStatus.POSTED.value
    assert tx.splitwise_expense_id == "expense-repeat"
    db.close()


def test_repeated_sync_same_transaction_is_idempotent(tmp_path, monkeypatch):
    db = _sqlite_session(tmp_path)
    item = db.get(PlaidItem, 1)
    notifications = FakeNotificationService()

    class RepeatingPlaidService:
        def __init__(self):
            self.calls = 0

        def transactions_sync(self, *, access_token, cursor):
            self.calls += 1
            return {
                "added": [_plaid_tx("tx-repeat-sync", pending=False)],
                "modified": [],
                "removed": [],
                "next_cursor": f"cursor-{self.calls}",
                "has_more": False,
            }

    monkeypatch.setattr(transaction_service, "decrypt_secret", lambda _encrypted: "access-token")
    plaid = RepeatingPlaidService()
    service = TransactionService(
        db,
        plaid_service=plaid,
        splitwise_service=object(),
        notification_service=notifications,
    )

    first = service.sync_item(item)
    second = service.sync_item(item)

    rows = db.query(ExpenseTransaction).filter_by(plaid_transaction_id="tx-repeat-sync").all()
    assert len(rows) == 1
    assert first["added"] == 1
    assert second["added"] == 0
    assert notifications.review_notifications == [rows[0].id]
    assert rows[0].review_notification_sent_at is not None
    db.close()


def test_same_chase_transaction_across_three_items_sends_one_notification(
    tmp_path,
):
    db = _sqlite_session(tmp_path)
    item_1 = db.get(PlaidItem, 1)
    item_2 = PlaidItem(
        item_id="item-2",
        access_token_encrypted="encrypted-2",
        institution_name="Chase",
    )
    item_3 = PlaidItem(
        item_id="item-3",
        access_token_encrypted="encrypted-3",
        institution_name="Chase",
    )
    item_1.institution_name = "Chase"
    db.add_all([item_2, item_3])
    db.commit()
    notifications = FakeNotificationService()
    service = TransactionService(
        db,
        splitwise_service=object(),
        notification_service=notifications,
    )

    service.upsert_transaction(
        item_1,
        _plaid_tx("tx-chase-duplicate-1", pending=False, name="Circle K"),
    )
    service.upsert_transaction(
        item_2,
        _plaid_tx("tx-chase-duplicate-2", pending=False, name="Circle K"),
    )
    service.upsert_transaction(
        item_3,
        _plaid_tx("tx-chase-duplicate-3", pending=False, name="Circle K"),
    )

    rows = (
        db.query(ExpenseTransaction)
        .filter(ExpenseTransaction.plaid_transaction_id.like("tx-chase-duplicate-%"))
        .order_by(ExpenseTransaction.id)
        .all()
    )
    assert len(rows) == 3
    assert notifications.review_notifications == [rows[0].id]
    assert all(row.review_notification_sent_at is not None for row in rows)
    db.close()


def test_create_only_scenario_transaction_imported_later_skips_notification(
    tmp_path,
    monkeypatch,
    caplog,
):
    db = _sqlite_session(tmp_path)
    item = db.get(PlaidItem, 1)
    notifications = FakeNotificationService()
    trace = "scenario_create_only_no_import_20260527_a1b2c3"

    class FakePlaidService:
        def transactions_sync(self, *, access_token, cursor):
            return {
                "added": [
                    _plaid_tx(
                        "tx-create-only-leaked",
                        pending=False,
                        name=f"Scenario Coffee [trace:{trace}]",
                    )
                ],
                "modified": [],
                "removed": [],
                "next_cursor": "cursor",
                "has_more": False,
            }

    monkeypatch.setattr(transaction_service, "decrypt_secret", lambda _encrypted: "access-token")
    service = TransactionService(
        db,
        plaid_service=FakePlaidService(),
        splitwise_service=object(),
        notification_service=notifications,
    )

    with caplog.at_level(logging.INFO):
        result = service.sync_item(item)

    tx = db.query(ExpenseTransaction).filter_by(plaid_transaction_id="tx-create-only-leaked").one()
    assert result["added"] == 1
    assert notifications.review_notifications == []
    assert tx.review_notification_sent_at is not None
    assert any(
        getattr(record, "event", None) == "scenario_create_only_imported_later_skipped_notification"
        and record.log_metadata["transaction_id"] == tx.id
        and record.log_metadata["plaid_transaction_id"] == "tx-create-only-leaked"
        and record.log_metadata["trace_id"] == trace
        and record.log_metadata["scenario_id"] == "create_only_no_import"
        for record in caplog.records
    )
    db.close()


@pytest.mark.parametrize(
    ("scenario_id", "plaid_transaction_id"),
    [
        ("manual_sync_basic", "tx-manual-scenario"),
        ("webhook_basic", "tx-webhook-scenario"),
    ],
)
def test_non_create_only_scenario_transaction_still_sends_notification(
    tmp_path,
    monkeypatch,
    scenario_id,
    plaid_transaction_id,
):
    db = _sqlite_session(tmp_path)
    item = db.get(PlaidItem, 1)
    notifications = FakeNotificationService()
    trace = f"scenario_{scenario_id}_20260527_a1b2c3"

    class FakePlaidService:
        def transactions_sync(self, *, access_token, cursor):
            return {
                "added": [
                    _plaid_tx(
                        plaid_transaction_id,
                        pending=False,
                        name=f"Scenario Coffee [trace:{trace}]",
                    )
                ],
                "modified": [],
                "removed": [],
                "next_cursor": "cursor",
                "has_more": False,
            }

    monkeypatch.setattr(transaction_service, "decrypt_secret", lambda _encrypted: "access-token")
    TransactionService(
        db,
        plaid_service=FakePlaidService(),
        splitwise_service=object(),
        notification_service=notifications,
    ).sync_item(item)

    tx = db.query(ExpenseTransaction).filter_by(plaid_transaction_id=plaid_transaction_id).one()
    assert notifications.review_notifications == [tx.id]
    assert tx.review_notification_sent_at is not None
    db.close()


def test_notify_ready_transactions_for_item_uses_atomic_claim(tmp_path):
    db = _sqlite_session(tmp_path)
    item = db.get(PlaidItem, 1)
    tx = ExpenseTransaction(
        plaid_transaction_id="tx-unsent-ready",
        plaid_item_id=item.id,
        name="Starbucks",
        merchant_name="Starbucks",
        amount_cents=433,
        pending=False,
        status=TransactionStatus.ASK_USER.value,
    )
    db.add(tx)
    db.commit()
    notifications = FakeNotificationService()
    service = TransactionService(
        db,
        splitwise_service=object(),
        notification_service=notifications,
    )

    first = service._notify_ready_transactions_for_item(item)
    second = service._notify_ready_transactions_for_item(item)
    db.refresh(tx)

    assert first == {"eligible": 1, "sent": 1, "skipped": 0}
    assert second == {"eligible": 0, "sent": 0, "skipped": 0}
    assert notifications.review_notifications == [tx.id]
    assert tx.review_notification_sent_at is not None
    db.close()


def test_duplicate_review_notification_claim_logs_skip(tmp_path, caplog):
    db = _sqlite_session(tmp_path)
    notifications = FakeNotificationService()
    service = TransactionService(
        db,
        splitwise_service=object(),
        notification_service=notifications,
    )
    service.upsert_transaction(db.get(PlaidItem, 1), _plaid_tx("tx-claim-once", pending=False))
    tx = db.query(ExpenseTransaction).filter_by(plaid_transaction_id="tx-claim-once").one()
    assert notifications.review_notifications == [tx.id]

    with caplog.at_level(logging.INFO):
        sent = service._attempt_review_notification(tx)

    assert sent is False
    assert notifications.review_notifications == [tx.id]
    assert any(
        getattr(record, "event", None) == "telegram_notification_skipped_duplicate"
        and record.log_metadata["reason"] == "review_notification_already_claimed"
        for record in caplog.records
    )
    assert any(
        getattr(record, "event", None) == "transaction_notification_claim_skipped_already_sent"
        and record.log_metadata["reason"] == "review_notification_already_sent"
        for record in caplog.records
    )
    db.close()


def test_failed_review_notification_stays_claimed_to_avoid_spam(tmp_path, monkeypatch):
    db = _sqlite_session(tmp_path)
    item = db.get(PlaidItem, 1)

    class FailingNotificationService:
        def __init__(self):
            self.review_notifications = []

        def notify_transaction_needs_review(self, tx):
            self.review_notifications.append(tx.id)
            return False

    class FakePlaidService:
        def transactions_sync(self, *, access_token, cursor):
            return {
                "added": [_plaid_tx("tx-failed-notification", pending=False)],
                "modified": [],
                "removed": [],
                "next_cursor": "cursor",
                "has_more": False,
            }

    monkeypatch.setattr(transaction_service, "decrypt_secret", lambda _encrypted: "access-token")
    notifications = FailingNotificationService()

    result = TransactionService(
        db,
        plaid_service=FakePlaidService(),
        splitwise_service=object(),
        notification_service=notifications,
    ).sync_item(item)
    tx = db.query(ExpenseTransaction).filter_by(plaid_transaction_id="tx-failed-notification").one()

    assert result["notification_eligible"] == 1
    assert result["notification_sent"] == 0
    assert result["notification_skipped"] == 1
    assert notifications.review_notifications == [tx.id]
    assert tx.review_notification_sent_at is not None
    db.close()


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


def test_pending_transaction_equal_split_is_rejected():
    tx = make_tx(TransactionStatus.ASK_USER.value)
    tx.pending = True

    class FakeSplitwise:
        def get_current_user(self):
            return {"id": 111}

    service = TransactionService(FakeDb(tx), splitwise_service=FakeSplitwise())

    with pytest.raises(TransactionError, match="still pending"):
        service.create_equal_split_expense(
            tx_id=1,
            friend_user_ids=[222],
            group_id=None,
            description=None,
            details=None,
            currency_code=None,
            confirm=True,
            post_pending=False,
        )


def test_settled_transaction_equal_split_is_allowed():
    tx = make_tx(TransactionStatus.ASK_USER.value)
    tx.pending = False

    class FakeSplitwise:
        def get_current_user(self):
            return {"id": 111}

        def create_expense(self, payload):
            return {"expenses": [{"id": "expense-1"}], "payload": payload}

    notifications = FakeNotificationService()
    service = TransactionService(
        FakeDb(tx),
        splitwise_service=FakeSplitwise(),
        notification_service=notifications,
    )

    posted_tx, response = service.create_equal_split_expense(
        tx_id=1,
        friend_user_ids=[222],
        group_id=None,
        description=None,
        details=None,
        currency_code=None,
        confirm=True,
        post_pending=False,
    )

    assert posted_tx.status == TransactionStatus.POSTED.value
    assert posted_tx.splitwise_expense_id == "expense-1"
    assert response["expenses"][0]["id"] == "expense-1"
    assert notifications.posted_notifications == [(1, "expense-1")]


def test_pending_transaction_custom_split_is_rejected():
    tx = make_tx(TransactionStatus.ASK_USER.value)
    tx.pending = True

    class FakeSplitwise:
        def get_current_user(self):
            return {"id": 111}

    service = TransactionService(FakeDb(tx), splitwise_service=FakeSplitwise())

    with pytest.raises(TransactionError, match="still pending"):
        service.create_custom_split_expense(
            tx_id=1,
            participant_splits=[CustomSplitInput(user_id=222, amount_cents=633)],
            split_mode="exact_amounts",
            payer_included=False,
            payer_user_id=111,
            owed_by_user_id=None,
            group_id=None,
            description=None,
            details=None,
            currency_code=None,
            confirm=True,
            post_pending=False,
        )


def test_settled_transaction_custom_split_is_allowed():
    tx = make_tx(TransactionStatus.ASK_USER.value)
    tx.pending = False

    class FakeSplitwise:
        def create_expense(self, payload):
            return {"expenses": [{"id": "custom-expense-1"}], "payload": payload}

    service = TransactionService(
        FakeDb(tx),
        splitwise_service=FakeSplitwise(),
        notification_service=FakeNotificationService(),
    )

    posted_tx, response = service.create_custom_split_expense(
        tx_id=1,
        participant_splits=[CustomSplitInput(user_id=222, amount_cents=633)],
        split_mode="exact_amounts",
        payer_included=False,
        payer_user_id=111,
        owed_by_user_id=None,
        group_id=None,
        description=None,
        details=None,
        currency_code=None,
        confirm=True,
        post_pending=False,
    )

    assert posted_tx.status == TransactionStatus.POSTED.value
    assert posted_tx.splitwise_expense_id == "custom-expense-1"
    assert response["expenses"][0]["id"] == "custom-expense-1"

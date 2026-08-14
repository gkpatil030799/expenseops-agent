from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    ExpenseTransaction,
    FinancialOperation,
    OutboxEvent,
    PlaidItem,
    SplitwiseIntegration,
    TelegramIdentity,
    TransactionStatus,
    User,
    Workspace,
    WorkspaceMembership,
    utc_now,
)
from app.services.splitwise_service import SplitwiseAPIError
from app.services.transaction_service import TransactionError, TransactionService
from app.tenancy import TenantContext, set_session_tenant


class NoopNotification:
    def notify_transaction_needs_review(self, _tx):
        return True

    def notify_splitwise_posted(self, _tx, _expense_id):
        return None


def _database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'financial-journal.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        user = User(email="owner@example.test", display_name="Owner")
        db.add(user)
        db.flush()
        workspace = Workspace(name="Household", created_by_user_id=user.id)
        db.add(workspace)
        db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role="owner",
                is_default=True,
            )
        )
        item = PlaidItem(
            workspace_id=workspace.id,
            item_id="plaid-item",
            owner_user_id=user.id,
            ownership_verified_at=utc_now(),
            access_token_encrypted="encrypted",
        )
        db.add(item)
        db.flush()
        db.add(
            SplitwiseIntegration(
                workspace_id=workspace.id,
                user_id=user.id,
                credentials_encrypted="encrypted",
                splitwise_user_id="111",
                display_name="Owner",
                verified_at=utc_now(),
            )
        )
        db.add(
            TelegramIdentity(
                workspace_id=workspace.id,
                user_id=user.id,
                telegram_user_id="telegram-owner",
                chat_id="telegram-chat",
            )
        )
        tx = ExpenseTransaction(
            workspace_id=workspace.id,
            plaid_transaction_id="transaction-1",
            plaid_item_id=item.id,
            name="Aldi",
            amount_cents=2000,
            pending=False,
            status=TransactionStatus.ASK_USER.value,
        )
        db.add(tx)
        db.commit()
        context = TenantContext(user.id, workspace.id)
        tx_id = tx.id
    return engine, factory, context, tx_id


def _service(db, splitwise):
    return TransactionService(
        db,
        splitwise_service=splitwise,
        notification_service=NoopNotification(),
    )


def _post(service, tx_id):
    return service.create_equal_split_expense(
        tx_id=tx_id,
        friend_user_ids=[222],
        group_id=None,
        description=None,
        details=None,
        currency_code=None,
        confirm=True,
        post_pending=False,
    )


def test_successful_create_is_replayed_without_second_provider_call(tmp_path):
    engine, factory, context, tx_id = _database(tmp_path)

    class Splitwise:
        calls = 0

        def create_expense(self, _payload):
            self.calls += 1
            return {"expenses": [{"id": "expense-1"}]}

    splitwise = Splitwise()
    with factory() as db:
        set_session_tenant(db, context)
        posted, _ = _post(_service(db, splitwise), tx_id)
        operation = db.scalar(select(FinancialOperation))
        outbox = db.scalar(select(OutboxEvent))
        assert posted.splitwise_expense_id == "expense-1"
        assert operation.state == "succeeded"
        assert operation.attempt_count == 1
        assert operation.idempotency_key
        assert outbox.event_type == "telegram.splitwise_posted"
        assert outbox.dedupe_key == f"telegram-splitwise-posted:{operation.id}"
        assert outbox.payload_json == {
            "transaction_id": tx_id,
            "splitwise_expense_id": "expense-1",
            "recipient_user_id": context.user_id,
        }

        # Simulate a local caller replay after completion. The durable operation
        # result wins without calling the provider again.
        posted.splitwise_expense_id = None
        posted.status = TransactionStatus.ASK_USER.value
        db.commit()
        replayed, response = _post(_service(db, splitwise), tx_id)
        assert replayed.splitwise_expense_id == "expense-1"
        assert response["replayed"] is True
        assert splitwise.calls == 1
        assert len(list(db.scalars(select(OutboxEvent)))) == 1
    engine.dispose()


def test_timeout_after_remote_create_is_reconciled_by_marker(tmp_path):
    engine, factory, context, tx_id = _database(tmp_path)

    class Splitwise:
        create_calls = 0
        find_calls = 0

        def create_expense(self, payload):
            self.create_calls += 1
            assert "ExpenseOps ref:" in payload["details"]
            raise SplitwiseAPIError("timeout after send", ambiguous=True)

        def find_expense_by_idempotency_key(self, key):
            self.find_calls += 1
            return {"id": "expense-recovered", "details": f"ExpenseOps ref: {key}"}

    splitwise = Splitwise()
    with factory() as db:
        set_session_tenant(db, context)
        try:
            _post(_service(db, splitwise), tx_id)
        except SplitwiseAPIError:
            pass
        tx = db.get(ExpenseTransaction, tx_id)
        operation = db.scalar(select(FinancialOperation))
        assert tx.status == TransactionStatus.POST_AMBIGUOUS.value
        assert operation.state == "needs_reconciliation"

        recovered = _service(db, splitwise).retry_financial_operation(tx_id)
        assert recovered.status == TransactionStatus.POSTED.value
        assert recovered.splitwise_expense_id == "expense-recovered"
        assert splitwise.create_calls == 1
        assert splitwise.find_calls == 1
    engine.dispose()


def test_ambiguous_delete_recovers_when_provider_expense_is_absent(tmp_path):
    engine, factory, context, tx_id = _database(tmp_path)

    class Splitwise:
        def delete_expense(self, _expense_id):
            raise SplitwiseAPIError("timeout after delete", ambiguous=True)

        def expense_exists(self, _expense_id):
            return False

    with factory() as db:
        set_session_tenant(db, context)
        tx = db.get(ExpenseTransaction, tx_id)
        tx.status = TransactionStatus.POSTED.value
        tx.splitwise_expense_id = "expense-1"
        db.commit()
        try:
            _service(db, Splitwise()).undo_transaction(tx_id)
        except TransactionError:
            pass
        assert tx.status == TransactionStatus.UNDO_AMBIGUOUS.value
        recovered = _service(db, Splitwise()).retry_financial_operation(tx_id)
        assert recovered.status == TransactionStatus.ASK_USER.value
        assert recovered.splitwise_expense_id is None
        operation = db.scalar(select(FinancialOperation))
        assert operation.state == "succeeded"
    engine.dispose()


def test_active_lease_prevents_concurrent_second_submission(tmp_path):
    engine, factory, context, tx_id = _database(tmp_path)

    class Splitwise:
        def create_expense(self, _payload):
            raise AssertionError("provider must not be called while another lease is active")

    with factory() as db:
        set_session_tenant(db, context)
        tx = db.get(ExpenseTransaction, tx_id)
        service = _service(db, Splitwise())
        operation = service._claim_financial_operation(  # noqa: SLF001
            tx,
            action="splitwise_create",
            generation=0,
            request_json={"details": "saved"},
        )
        service._acquire_operation_lease(operation)  # noqa: SLF001
        try:
            _post(service, tx_id)
        except TransactionError as exc:
            assert "already being processed" in str(exc)
        else:
            raise AssertionError("concurrent submission should have been blocked")
    engine.dispose()


def test_final_bank_amount_updates_existing_splitwise_expense(tmp_path):
    engine, factory, context, tx_id = _database(tmp_path)

    class Splitwise:
        updated_payload = None

        def create_expense(self, _payload):
            return {"expenses": [{"id": "expense-1"}]}

        def update_expense(self, expense_id, payload):
            assert expense_id == "expense-1"
            self.updated_payload = payload
            return {"expenses": [{"id": expense_id}]}

    splitwise = Splitwise()
    with factory() as db:
        set_session_tenant(db, context)
        service = _service(db, splitwise)
        posted, _ = _post(service, tx_id)
        posted.amount_cents = 2151
        db.commit()

        service._reconcile_posted_amount_change(  # noqa: SLF001
            posted,
            previous_amount_cents=2000,
        )

        assert posted.status == TransactionStatus.POSTED.value
        assert posted.splitwise_amount_cents == 2151
        assert splitwise.updated_payload["cost"] == "21.51"
        owed = sum(
            int(round(float(value) * 100))
            for key, value in splitwise.updated_payload.items()
            if key.endswith("__owed_share")
        )
        assert owed == 2151
        operations = list(db.scalars(select(FinancialOperation).order_by(FinancialOperation.id)))
        assert [operation.action for operation in operations] == [
            "splitwise_create",
            "splitwise_update",
        ]
        assert operations[-1].state == "succeeded"
    engine.dispose()


def test_removed_bank_transaction_deletes_splitwise_before_marking_removed(tmp_path):
    engine, factory, context, tx_id = _database(tmp_path)

    class Splitwise:
        deleted = []

        def create_expense(self, _payload):
            return {"expenses": [{"id": "expense-1"}]}

        def delete_expense(self, expense_id):
            self.deleted.append(expense_id)
            return {"success": True}

    splitwise = Splitwise()
    with factory() as db:
        set_session_tenant(db, context)
        service = _service(db, splitwise)
        posted, _ = _post(service, tx_id)

        service.mark_removed(posted.plaid_transaction_id)

        assert posted.status == TransactionStatus.REMOVED.value
        assert posted.splitwise_expense_id is None
        assert splitwise.deleted == ["expense-1"]
        operation = db.scalar(
            select(FinancialOperation).where(
                FinancialOperation.action == "splitwise_delete_removed"
            )
        )
        assert operation is not None
        assert operation.state == "succeeded"
    engine.dispose()


def test_invalid_shared_draft_without_allocation_is_rejected(tmp_path):
    engine, factory, context, tx_id = _database(tmp_path)
    with factory() as db:
        set_session_tenant(db, context)
        with pytest.raises(TransactionError, match="participant"):
            _service(db, object()).mark_shared_draft(tx_id)
    engine.dispose()


def test_pending_to_posted_replacement_is_linked_and_old_row_removed(tmp_path):
    engine, factory, context, tx_id = _database(tmp_path)
    with factory() as db:
        set_session_tenant(db, context)
        old = db.get(ExpenseTransaction, tx_id)
        old.pending = True
        old.plaid_transaction_id = "pending-transaction"
        db.commit()
        item = db.get(PlaidItem, old.plaid_item_id)
        service = _service(db, object())

        service.upsert_transaction(
            item,
            {
                "transaction_id": "posted-transaction",
                "pending_transaction_id": "pending-transaction",
                "account_id": "account-1",
                "name": "Aldi",
                "merchant_name": "Aldi",
                "amount": "20.50",
                "iso_currency_code": "USD",
                "date": "2026-08-12",
                "pending": False,
            },
        )

        posted = db.scalar(
            select(ExpenseTransaction).where(
                ExpenseTransaction.plaid_transaction_id == "posted-transaction"
            )
        )
        db.refresh(old)
        assert posted.replaces_transaction_id == old.id
        assert old.replaced_by_transaction_id == posted.id
        assert old.status == TransactionStatus.REMOVED.value
    engine.dispose()

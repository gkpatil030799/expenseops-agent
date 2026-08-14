from __future__ import annotations

from datetime import UTC, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base
from app.jobs import outbox as outbox_job
from app.models import (
    ExpenseTransaction,
    FinancialOperation,
    OutboxEvent,
    PlaidItem,
    SplitwiseIntegration,
    TelegramIdentity,
    TelegramWebhookUpdate,
    TransactionStatus,
    User,
    Workspace,
    WorkspaceMembership,
    utc_now,
)
from app.services.outbox_service import (
    claim_outbox_batch,
    enqueue_outbox_event,
    fail_outbox_event,
    replay_dead_outbox_events,
)
from app.services.splitwise_service import SplitwiseAPIError
from app.services.transaction_service import TransactionService
from app.tenancy import TenantContext, set_session_tenant


def _database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'outbox.db'}")
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
        db.add(
            TelegramIdentity(
                workspace_id=workspace.id,
                user_id=user.id,
                telegram_user_id="telegram-user",
                chat_id="telegram-chat",
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


def test_review_notification_is_deduplicated_before_delivery(tmp_path):
    engine, factory, context, tx_id = _database(tmp_path)
    with factory() as db:
        set_session_tenant(db, context)
        tx = db.get(ExpenseTransaction, tx_id)
        service = TransactionService(db, notification_service=object())

        assert service._enqueue_review_notification(tx) is True  # noqa: SLF001
        assert service._enqueue_review_notification(tx) is False  # noqa: SLF001

        events = list(db.scalars(select(OutboxEvent)))
        assert len(events) == 1
        assert events[0].dedupe_key == f"telegram-review:{tx_id}"
        assert tx.review_notification_queued_at is not None
        assert tx.review_notification_sent_at is None
    engine.dispose()


def test_worker_marks_delivery_sent_only_after_success(tmp_path, monkeypatch):
    engine, factory, context, tx_id = _database(tmp_path)
    with factory() as db:
        set_session_tenant(db, context)
        tx = db.get(ExpenseTransaction, tx_id)
        TransactionService(db, notification_service=object())._enqueue_review_notification(  # noqa: SLF001
            tx
        )

    delivered = []

    class Notification:
        def __init__(self, settings):
            assert settings.telegram_chat_id == "telegram-chat"

        def notify_transaction_needs_review(self, tx):
            delivered.append(tx.id)
            return True

    monkeypatch.setattr(outbox_job, "SessionLocal", factory)
    monkeypatch.setattr(outbox_job, "NotificationService", Notification)
    monkeypatch.setattr(outbox_job, "get_settings", lambda: Settings())

    outcome = outbox_job.run_once()

    with factory() as db:
        set_session_tenant(db, context)
        tx = db.get(ExpenseTransaction, tx_id)
        event = db.scalar(select(OutboxEvent))
        assert outcome == {"claimed": 1, "succeeded": 1, "retried": 0, "dead": 0}
        assert delivered == [tx_id]
        assert tx.review_notification_sent_at is not None
        assert tx.review_notification_queued_at is None
        assert event.state == "succeeded"
    engine.dispose()


def test_failed_delivery_retries_and_expired_lease_is_reclaimable(tmp_path):
    engine, factory, context, tx_id = _database(tmp_path)
    with factory() as db:
        set_session_tenant(db, context)
        event = enqueue_outbox_event(
            db,
            workspace_id=context.workspace_id,
            event_type="telegram.review_transaction",
            aggregate_type="expense_transaction",
            aggregate_id=tx_id,
            dedupe_key=f"telegram-review:{tx_id}",
            payload={"transaction_id": tx_id},
        )
        db.commit()
        claimed = claim_outbox_batch(db)
        token = str(claimed[0].lease_token)
        assert fail_outbox_event(db, event.id, token, RuntimeError("temporary")) == "retry"
        db.refresh(event)
        event.available_at = utc_now() - timedelta(seconds=1)
        event.lease_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
        reclaimed = claim_outbox_batch(db)
        assert [value.id for value in reclaimed] == [event.id]
        assert reclaimed[0].attempt_count == 2
        assert reclaimed[0].lease_token != token
    engine.dispose()


def test_provider_retry_after_is_respected(tmp_path):
    engine, factory, context, tx_id = _database(tmp_path)
    with factory() as db:
        set_session_tenant(db, context)
        event = enqueue_outbox_event(
            db,
            workspace_id=context.workspace_id,
            event_type="telegram.review_transaction",
            aggregate_type="expense_transaction",
            aggregate_id=tx_id,
            dedupe_key=f"retry-after:{tx_id}",
            payload={"transaction_id": tx_id},
        )
        db.commit()
        claimed = claim_outbox_batch(db)
        before = utc_now()
        assert (
            fail_outbox_event(
                db,
                event.id,
                str(claimed[0].lease_token),
                RuntimeError("rate limited"),
                retry_after_seconds=300,
            )
            == "retry"
        )
        db.refresh(event)
        available_at = event.available_at
        if available_at.tzinfo is None:
            available_at = available_at.replace(tzinfo=UTC)
        assert available_at >= before + timedelta(seconds=300)
    engine.dispose()


def test_dead_letter_can_be_replayed_by_operator(tmp_path):
    engine, factory, context, tx_id = _database(tmp_path)
    with factory() as db:
        set_session_tenant(db, context)
        event = enqueue_outbox_event(
            db,
            workspace_id=context.workspace_id,
            event_type="telegram.review_transaction",
            aggregate_type="expense_transaction",
            aggregate_id=tx_id,
            dedupe_key=f"dead-letter:{tx_id}",
            payload={"transaction_id": tx_id},
        )
        db.commit()
        claimed = claim_outbox_batch(db)
        assert (
            fail_outbox_event(
                db,
                event.id,
                str(claimed[0].lease_token),
                RuntimeError("terminal"),
                max_attempts=1,
            )
            == "dead"
        )
        assert replay_dead_outbox_events(db, event_type=event.event_type) == 1
        db.refresh(event)
        assert event.state == "pending"
        assert event.attempt_count == 0
        assert event.last_error is None
    engine.dispose()


def test_production_splitwise_create_is_queued_and_crash_reconciled(
    tmp_path, monkeypatch
):
    from app.services import transaction_service as transaction_module

    engine, factory, context, tx_id = _database(tmp_path)

    class Splitwise:
        create_calls = 0
        remote_expense = None

        def create_expense(self, payload):
            self.create_calls += 1
            self.remote_expense = {"id": "expense-durable", "details": payload["details"]}
            raise SplitwiseAPIError(
                "worker crashed after provider accepted the expense",
                ambiguous=True,
            )

        def find_expense_by_idempotency_key(self, key):
            if self.remote_expense and f"ExpenseOps ref: {key}" in self.remote_expense["details"]:
                return self.remote_expense
            return None

    splitwise = Splitwise()
    production = Settings(environment="production")
    monkeypatch.setattr(transaction_module, "get_settings", lambda: production)
    monkeypatch.setattr(transaction_module, "SplitwiseService", lambda _settings: splitwise)
    monkeypatch.setattr(outbox_job, "SessionLocal", factory)
    monkeypatch.setattr(outbox_job, "get_settings", lambda: Settings())

    with factory() as db:
        set_session_tenant(db, context)
        service = TransactionService(db, settings=production)
        queued, response = service.create_equal_split_expense(
            tx_id=tx_id,
            friend_user_ids=[222],
            group_id=None,
            description=None,
            details=None,
            currency_code=None,
            confirm=True,
            post_pending=False,
        )
        operation = db.scalar(select(FinancialOperation))
        event = db.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "splitwise.execute_operation")
        )
        assert response == {"queued": True, "operation_id": operation.id}
        assert queued.status == TransactionStatus.POSTING.value
        assert event.state == "pending"
        assert splitwise.create_calls == 0

    first = outbox_job.run_once()
    assert first["retried"] == 1
    assert splitwise.create_calls == 1
    with factory() as db:
        event = db.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "splitwise.execute_operation")
        )
        event.available_at = utc_now() - timedelta(seconds=1)
        db.commit()

    second = outbox_job.run_once()
    assert second["succeeded"] == 1
    assert splitwise.create_calls == 1
    with factory() as db:
        set_session_tenant(db, context)
        tx = db.get(ExpenseTransaction, tx_id)
        operation = db.scalar(select(FinancialOperation))
        assert tx.status == TransactionStatus.POSTED.value
        assert tx.splitwise_expense_id == "expense-durable"
        assert operation.state == "succeeded"
    engine.dispose()


def test_telegram_update_is_durably_queued_and_acknowledged(tmp_path, monkeypatch):
    from app.api import telegram_routes

    engine, factory, context, _tx_id = _database(tmp_path)
    update = {
        "update_id": 9001,
        "message": {
            "message_id": 1,
            "chat": {"id": "telegram-chat"},
            "from": {"id": "telegram-user"},
            "text": "cancel",
        },
    }
    with factory() as db:
        record = telegram_routes._claim_telegram_update(db, update)  # noqa: SLF001
        assert record is not None
        assert telegram_routes._queue_telegram_update(db, record, update) is True  # noqa: SLF001
        assert record.state == "queued"
        assert telegram_routes._claim_telegram_update(db, update) is None  # noqa: SLF001
        event = db.scalar(select(OutboxEvent))
        assert event.event_type == "telegram.process_update"

    processed = []

    def process(db, payload, record):
        processed.append(payload["update_id"])
        telegram_routes._complete_telegram_update(db, record)  # noqa: SLF001
        return {"ok": True}

    monkeypatch.setattr(telegram_routes, "_process_telegram_update", process)
    monkeypatch.setattr(outbox_job, "SessionLocal", factory)
    monkeypatch.setattr(outbox_job, "get_settings", lambda: Settings())
    outcome = outbox_job.run_once()
    assert outcome == {"claimed": 1, "succeeded": 1, "retried": 0, "dead": 0}
    assert processed == [9001]
    with factory() as db:
        record = db.scalar(select(TelegramWebhookUpdate))
        assert record.state == "processed"
    engine.dispose()


def test_worker_delivers_splitwise_confirmation_to_exact_recipient(tmp_path, monkeypatch):
    engine, factory, context, tx_id = _database(tmp_path)
    with factory() as db:
        set_session_tenant(db, context)
        tx = db.get(ExpenseTransaction, tx_id)
        tx.status = TransactionStatus.POSTED.value
        tx.splitwise_expense_id = "splitwise-expense-42"
        enqueue_outbox_event(
            db,
            workspace_id=context.workspace_id,
            event_type="telegram.splitwise_posted",
            aggregate_type="financial_operation",
            aggregate_id="operation-42",
            dedupe_key="telegram-splitwise-posted:operation-42",
            payload={
                "transaction_id": tx_id,
                "splitwise_expense_id": "splitwise-expense-42",
                "recipient_user_id": context.user_id,
            },
        )
        db.commit()

    delivered = []

    class Notification:
        def __init__(self, settings):
            assert settings.telegram_chat_id == "telegram-chat"

        def notify_splitwise_posted(self, tx, expense_id):
            delivered.append((tx.id, expense_id))
            return True

    monkeypatch.setattr(outbox_job, "SessionLocal", factory)
    monkeypatch.setattr(outbox_job, "NotificationService", Notification)
    monkeypatch.setattr(outbox_job, "get_settings", lambda: Settings())

    outcome = outbox_job.run_once()

    with factory() as db:
        set_session_tenant(db, context)
        event = db.scalar(select(OutboxEvent))
        assert outcome == {"claimed": 1, "succeeded": 1, "retried": 0, "dead": 0}
        assert delivered == [(tx_id, "splitwise-expense-42")]
        assert event.state == "succeeded"
    engine.dispose()


def test_worker_does_not_send_stale_splitwise_confirmation(tmp_path, monkeypatch):
    engine, factory, context, tx_id = _database(tmp_path)
    with factory() as db:
        set_session_tenant(db, context)
        enqueue_outbox_event(
            db,
            workspace_id=context.workspace_id,
            event_type="telegram.splitwise_posted",
            aggregate_type="financial_operation",
            aggregate_id="operation-stale",
            dedupe_key="telegram-splitwise-posted:operation-stale",
            payload={
                "transaction_id": tx_id,
                "splitwise_expense_id": "already-deleted",
                "recipient_user_id": context.user_id,
            },
        )
        db.commit()

    class Notification:
        def __init__(self, _settings):
            raise AssertionError("stale confirmation must not instantiate delivery")

    monkeypatch.setattr(outbox_job, "SessionLocal", factory)
    monkeypatch.setattr(outbox_job, "NotificationService", Notification)
    monkeypatch.setattr(outbox_job, "get_settings", lambda: Settings())

    outcome = outbox_job.run_once()

    with factory() as db:
        set_session_tenant(db, context)
        event = db.scalar(select(OutboxEvent))
        assert outcome == {"claimed": 1, "succeeded": 1, "retried": 0, "dead": 0}
        assert event.state == "succeeded"
    engine.dispose()

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base
from app.jobs import outbox as outbox_job
from app.models import (
    ExpenseTransaction,
    OutboxEvent,
    PlaidItem,
    TelegramIdentity,
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
)
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

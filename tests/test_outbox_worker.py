from __future__ import annotations

import json
from datetime import UTC, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app import db as app_db
from app import security
from app.api import plaid_routes
from app.config import Settings
from app.db import Base
from app.jobs import outbox as outbox_job
from app.models import (
    ExpenseTransaction,
    FinancialOperation,
    OutboxEvent,
    PlaidItem,
    PlaidWebhookEvent,
    SplitwiseIntegration,
    TelegramIdentity,
    TelegramWebhookUpdate,
    TransactionStatus,
    User,
    Workspace,
    WorkspaceMembership,
    utc_now,
)
from app.services import splitwise_service as splitwise_module
from app.services.outbox_service import (
    claim_outbox_batch,
    enqueue_outbox_event,
    fail_outbox_event,
    replay_dead_outbox_events,
)
from app.services.splitwise_service import SplitwiseAPIError
from app.services.transaction_service import TransactionService
from app.tenancy import (
    TenantContext,
    get_active_user_id,
    get_active_workspace_id,
    reset_active_user,
    reset_active_workspace,
    set_active_user,
    set_active_workspace,
    set_session_tenant,
)


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


def test_app_env_production_defers_review_notification_to_outbox(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    effective_production = Settings(
        environment="local",
        app_secret_key="configured-fernet-key",
        _env_file=None,
    )
    engine, factory, context, tx_id = _database(tmp_path)

    with factory() as db:
        set_session_tenant(db, context)
        tx = db.get(ExpenseTransaction, tx_id)
        service = TransactionService(db, settings=effective_production)

        assert service._attempt_review_notification(tx) is True  # noqa: SLF001

        event = db.scalar(select(OutboxEvent))
        assert event is not None
        assert event.event_type == "telegram.review_transaction"
        assert event.state == "pending"
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

    monkeypatch.setenv("APP_ENV", "production")
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
    production = Settings(
        environment="local",
        app_secret_key="configured-fernet-key",
        database_url="postgresql://expenseops@db.example/expenseops",
        enable_postgres_rls=True,
        rate_limit_backend="postgres",
        _env_file=None,
    )
    monkeypatch.setattr(transaction_module, "get_settings", lambda: production)
    monkeypatch.setattr(transaction_module, "SplitwiseService", lambda _settings: splitwise)
    monkeypatch.setattr(outbox_job, "SessionLocal", factory)
    monkeypatch.setattr(outbox_job, "get_settings", lambda: production)

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


def test_definitive_splitwise_rejection_is_not_auto_retried(tmp_path, monkeypatch):
    """A non-ambiguous provider rejection (bad group, invalid payload) will
    never succeed on a blind retry of the same payload -- unlike the
    ambiguous crash case above, where the automatic retry's
    attempt_count > 0 branch is what drives reconciliation. Retrying here
    only wastes a live API call and, before this fix, left a window where an
    unattended retry could complete after the user had already moved on.
    """

    from app.services import transaction_service as transaction_module

    monkeypatch.setenv("APP_ENV", "production")
    engine, factory, context, tx_id = _database(tmp_path)

    class Splitwise:
        create_calls = 0

        def create_expense(self, payload):
            self.create_calls += 1
            raise SplitwiseAPIError("This group does not exist.", ambiguous=False)

        def find_expense_by_idempotency_key(self, key):
            return None

    splitwise = Splitwise()
    production = Settings(
        environment="local",
        app_secret_key="configured-fernet-key",
        database_url="postgresql://expenseops@db.example/expenseops",
        enable_postgres_rls=True,
        rate_limit_backend="postgres",
        _env_file=None,
    )
    monkeypatch.setattr(transaction_module, "get_settings", lambda: production)
    monkeypatch.setattr(transaction_module, "SplitwiseService", lambda _settings: splitwise)
    monkeypatch.setattr(outbox_job, "SessionLocal", factory)
    monkeypatch.setattr(outbox_job, "get_settings", lambda: production)

    with factory() as db:
        set_session_tenant(db, context)
        service = TransactionService(db, settings=production)
        service.create_equal_split_expense(
            tx_id=tx_id,
            friend_user_ids=[222],
            group_id=None,
            description=None,
            details=None,
            currency_code=None,
            confirm=True,
            post_pending=False,
        )

    result = outbox_job.run_once()
    assert result["succeeded"] == 1
    assert result["retried"] == 0
    assert splitwise.create_calls == 1

    with factory() as db:
        set_session_tenant(db, context)
        tx = db.get(ExpenseTransaction, tx_id)
        operation = db.scalar(select(FinancialOperation))
        event = db.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "splitwise.execute_operation")
        )
        assert tx.status == TransactionStatus.ERROR.value
        assert operation.state == "failed"
        # The outbox job itself is done -- no further automatic attempt will
        # fire -- while the financial operation stays failed for explicit
        # POST /recovery/retry, not an unattended background one.
        assert event.state == "succeeded"
    engine.dispose()


def test_worker_scopes_splitwise_credentials_and_restores_context(tmp_path, monkeypatch):
    from app.services import transaction_service as transaction_module

    engine, factory, context, tx_id = _database(tmp_path)
    encryption_settings = Settings(
        app_secret_key=Fernet.generate_key().decode(),
        _env_file=None,
    )
    monkeypatch.setattr(security, "get_settings", lambda: encryption_settings)
    credentials = security.encrypt_secret(
        json.dumps({"splitwise_api_key": "workspace-specific-key"})
    )
    with factory() as db:
        integration = db.scalar(
            select(SplitwiseIntegration).where(
                SplitwiseIntegration.workspace_id == context.workspace_id,
                SplitwiseIntegration.user_id == context.user_id,
            )
        )
        assert integration is not None
        integration.credentials_encrypted = credentials
        db.commit()

    production = Settings(
        environment="production",
        app_secret_key=encryption_settings.app_secret_key,
        _env_file=None,
    )
    monkeypatch.setattr(transaction_module, "get_settings", lambda: production)
    monkeypatch.setattr(splitwise_module, "SessionLocal", factory)
    monkeypatch.setattr(outbox_job, "SessionLocal", factory)
    monkeypatch.setattr(outbox_job, "get_settings", lambda: Settings(_env_file=None))

    requests_seen: list[tuple[str | None, int | None, int | None]] = []

    class Response:
        status_code = 200
        headers: dict[str, str] = {}

        @staticmethod
        def json():
            return {"expenses": [{"id": "expense-tenant-scoped"}]}

    def request(_method, _url, **kwargs):
        requests_seen.append(
            (
                kwargs.get("headers", {}).get("Authorization"),
                get_active_workspace_id(),
                get_active_user_id(),
            )
        )
        return Response()

    monkeypatch.setattr(splitwise_module.requests, "request", request)

    with factory() as db:
        set_session_tenant(db, context)
        service = TransactionService(db, settings=production)
        _tx, response = service.create_equal_split_expense(
            tx_id=tx_id,
            friend_user_ids=[222],
            group_id=None,
            description=None,
            details=None,
            currency_code=None,
            confirm=True,
            post_pending=False,
        )
        assert response["queued"] is True

    stale_workspace_token = set_active_workspace(987_654)
    stale_user_token = set_active_user(876_543)
    try:
        outcome = outbox_job.run_once()
        assert get_active_workspace_id() == 987_654
        assert get_active_user_id() == 876_543
    finally:
        reset_active_user(stale_user_token)
        reset_active_workspace(stale_workspace_token)

    assert outcome == {"claimed": 1, "succeeded": 1, "retried": 0, "dead": 0}
    assert requests_seen == [
        ("Bearer workspace-specific-key", context.workspace_id, context.user_id)
    ]
    with factory() as db:
        set_session_tenant(db, context)
        tx = db.get(ExpenseTransaction, tx_id)
        assert tx is not None
        assert tx.splitwise_expense_id == "expense-tenant-scoped"
        assert tx.status == TransactionStatus.POSTED.value
    engine.dispose()


def test_durable_plaid_worker_scopes_fresh_session_before_item_lookup(
    tmp_path, monkeypatch
):
    engine, factory, context, _tx_id = _database(tmp_path)
    with factory() as db:
        set_session_tenant(db, context)
        item = db.scalar(
            select(PlaidItem).where(PlaidItem.workspace_id == context.workspace_id)
        )
        assert item is not None
        webhook = PlaidWebhookEvent(
            workspace_id=context.workspace_id,
            webhook_type="TRANSACTIONS",
            webhook_code="SYNC_UPDATES_AVAILABLE",
            plaid_item_id=item.item_id,
            item_id=item.id,
            processing_status="queued",
        )
        db.add(webhook)
        db.flush()
        enqueue_outbox_event(
            db,
            workspace_id=context.workspace_id,
            event_type="plaid.sync_item",
            aggregate_type="plaid_item",
            aggregate_id=item.id,
            dedupe_key=f"plaid-webhook:{webhook.id}",
            payload={"item_id": item.id, "webhook_event_id": webhook.id},
        )
        db.commit()
        item_id = item.id
        webhook_id = webhook.id

    class RestrictedSession(Session):
        """Model the visibility rule enforced by PostgreSQL RLS in unit tests."""

        def get(self, entity, ident, **kwargs):
            if entity is PlaidItem and self.info.get("workspace_id") is None:
                return None
            return super().get(entity, ident, **kwargs)

    restricted_factory = sessionmaker(
        bind=engine,
        class_=RestrictedSession,
        expire_on_commit=False,
    )
    synced: list[tuple[int, int | None, int | None, int | None]] = []

    class TransactionServiceForTest:
        def __init__(self, db):
            self.db = db

        def sync_item(self, item):
            synced.append(
                (
                    item.id,
                    self.db.info.get("workspace_id"),
                    self.db.info.get("user_id"),
                    get_active_user_id(),
                )
            )
            return {
                "added": 1,
                "modified": 0,
                "removed": 0,
                "notification_eligible": 0,
                "notification_sent": 0,
                "notification_skipped": 0,
            }

    monkeypatch.setattr(outbox_job, "SessionLocal", restricted_factory)
    monkeypatch.setattr(app_db, "SessionLocal", restricted_factory)
    monkeypatch.setattr(plaid_routes, "TransactionService", TransactionServiceForTest)
    monkeypatch.setattr(
        plaid_routes,
        "sandbox_sync_guard_start",
        lambda *_args, **_kwargs: (False, None),
    )
    monkeypatch.setattr(plaid_routes, "sandbox_sync_guard_finish", lambda _guard: None)
    monkeypatch.setattr(outbox_job, "get_settings", lambda: Settings(_env_file=None))

    outcome = outbox_job.run_once()

    assert outcome == {"claimed": 1, "succeeded": 1, "retried": 0, "dead": 0}
    assert synced == [
        (item_id, context.workspace_id, context.user_id, context.user_id)
    ]
    with factory() as db:
        webhook = db.get(PlaidWebhookEvent, webhook_id)
        event = db.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "plaid.sync_item"))
        assert webhook is not None
        assert webhook.processing_status == "processed"
        assert event is not None
        assert event.state == "succeeded"
    engine.dispose()


@pytest.mark.parametrize("owner_integration_state", ["missing", "invalid"])
def test_durable_plaid_worker_never_uses_another_members_splitwise_credentials(
    tmp_path,
    monkeypatch,
    owner_integration_state,
):
    from app.services import transaction_service as transaction_module

    engine, factory, context, _tx_id = _database(tmp_path)
    encryption_settings = Settings(
        app_secret_key=Fernet.generate_key().decode(),
        _env_file=None,
    )
    monkeypatch.setattr(security, "get_settings", lambda: encryption_settings)
    other_credentials = security.encrypt_secret(
        json.dumps({"splitwise_api_key": "other-members-secret"})
    )
    with factory() as db:
        set_session_tenant(db, context)
        owner_integration = db.scalar(
            select(SplitwiseIntegration).where(
                SplitwiseIntegration.user_id == context.user_id
            )
        )
        assert owner_integration is not None
        if owner_integration_state == "missing":
            db.delete(owner_integration)
        else:
            owner_integration.credentials_encrypted = "invalid-ciphertext"
        other_user = User(email="other-member@example.test", display_name="Other member")
        db.add(other_user)
        db.flush()
        other_user_id = other_user.id
        db.add_all(
            [
                WorkspaceMembership(
                    workspace_id=context.workspace_id,
                    user_id=other_user_id,
                    role="member",
                ),
                SplitwiseIntegration(
                    workspace_id=context.workspace_id,
                    user_id=other_user_id,
                    credentials_encrypted=other_credentials,
                    splitwise_user_id="222",
                    display_name="Other member",
                    verified_at=utc_now(),
                ),
            ]
        )
        item = db.scalar(select(PlaidItem).where(PlaidItem.owner_user_id == context.user_id))
        assert item is not None
        webhook = PlaidWebhookEvent(
            workspace_id=context.workspace_id,
            webhook_type="TRANSACTIONS",
            webhook_code="SYNC_UPDATES_AVAILABLE",
            plaid_item_id=item.item_id,
            item_id=item.id,
            processing_status="queued",
        )
        db.add(webhook)
        db.flush()
        enqueue_outbox_event(
            db,
            workspace_id=context.workspace_id,
            event_type="plaid.sync_item",
            aggregate_type="plaid_item",
            aggregate_id=item.id,
            dedupe_key=f"plaid-webhook:{webhook.id}",
            payload={"item_id": item.id, "webhook_event_id": webhook.id},
        )
        db.commit()

    observed: list[dict[str, int | str | None]] = []

    class InspectingTransactionService(TransactionService):
        def sync_item(self, item):
            owner_service = self._splitwise_service_for_item_owner(item)  # noqa: SLF001
            observed.append(
                {
                    "db_user_id": self.db.info.get("user_id"),
                    "active_user_id": get_active_user_id(),
                    "initial_api_key": self.splitwise_service.settings.splitwise_api_key,
                    "owner_api_key": owner_service.settings.splitwise_api_key,
                }
            )
            return {
                "added": 0,
                "modified": 0,
                "removed": 0,
                "notification_eligible": 0,
                "notification_sent": 0,
                "notification_skipped": 0,
            }

    base_settings = Settings(_env_file=None)
    monkeypatch.setattr(transaction_module, "get_settings", lambda: base_settings)
    monkeypatch.setattr(splitwise_module, "SessionLocal", factory)
    monkeypatch.setattr(outbox_job, "SessionLocal", factory)
    monkeypatch.setattr(app_db, "SessionLocal", factory)
    monkeypatch.setattr(plaid_routes, "TransactionService", InspectingTransactionService)
    monkeypatch.setattr(
        plaid_routes,
        "sandbox_sync_guard_start",
        lambda *_args, **_kwargs: (False, None),
    )
    monkeypatch.setattr(plaid_routes, "sandbox_sync_guard_finish", lambda _guard: None)
    monkeypatch.setattr(outbox_job, "get_settings", lambda: base_settings)

    stale_user_token = set_active_user(987_654)
    try:
        outcome = outbox_job.run_once()
        assert get_active_user_id() == 987_654
    finally:
        reset_active_user(stale_user_token)

    assert outcome == {"claimed": 1, "succeeded": 1, "retried": 0, "dead": 0}
    assert observed == [
        {
            "db_user_id": context.user_id,
            "active_user_id": context.user_id,
            "initial_api_key": "",
            "owner_api_key": "",
        }
    ]
    assert other_user_id != context.user_id
    engine.dispose()


def test_plaid_sync_restores_owner_context_when_transaction_sync_fails(
    tmp_path,
    monkeypatch,
):
    engine, factory, context, _tx_id = _database(tmp_path)
    with factory() as db:
        set_session_tenant(db, context)
        item = db.scalar(select(PlaidItem).where(PlaidItem.owner_user_id == context.user_id))
        assert item is not None
        webhook = PlaidWebhookEvent(
            workspace_id=context.workspace_id,
            webhook_type="TRANSACTIONS",
            webhook_code="SYNC_UPDATES_AVAILABLE",
            plaid_item_id=item.item_id,
            item_id=item.id,
            processing_status="queued",
        )
        db.add(webhook)
        db.commit()
        item_id = item.id
        webhook_id = webhook.id

    closed_session_info: list[dict] = []

    class TrackingSession(Session):
        def close(self):
            closed_session_info.append(dict(self.info))
            super().close()

    tracking_factory = sessionmaker(
        bind=engine,
        class_=TrackingSession,
        expire_on_commit=False,
        info={"user_id": 765_432},
    )
    seen_during_sync: list[tuple[int | None, int | None]] = []

    class FailingTransactionService:
        def __init__(self, db):
            self.db = db

        def sync_item(self, _item):
            seen_during_sync.append(
                (self.db.info.get("user_id"), get_active_user_id())
            )
            raise RuntimeError("forced-sync-failure")

    monkeypatch.setattr(app_db, "SessionLocal", tracking_factory)
    monkeypatch.setattr(plaid_routes, "TransactionService", FailingTransactionService)
    monkeypatch.setattr(
        plaid_routes,
        "sandbox_sync_guard_start",
        lambda *_args, **_kwargs: (False, None),
    )
    monkeypatch.setattr(plaid_routes, "sandbox_sync_guard_finish", lambda _guard: None)

    stale_user_token = set_active_user(654_321)
    try:
        plaid_routes._sync_item_by_db_id(  # noqa: SLF001
            item_id,
            webhook_id,
            workspace_id=context.workspace_id,
        )
        assert get_active_user_id() == 654_321
    finally:
        reset_active_user(stale_user_token)

    assert seen_during_sync == [(context.user_id, context.user_id)]
    assert closed_session_info[-1]["user_id"] == 765_432
    with factory() as db:
        webhook = db.get(PlaidWebhookEvent, webhook_id)
        assert webhook is not None
        assert webhook.processing_status == "failed"
        assert webhook.error_message == "RuntimeError"
    engine.dispose()


def test_plaid_sync_fails_closed_when_item_owner_is_missing(tmp_path, monkeypatch):
    engine, factory, context, _tx_id = _database(tmp_path)
    with factory() as db:
        set_session_tenant(db, context)
        item = db.scalar(select(PlaidItem).where(PlaidItem.owner_user_id == context.user_id))
        assert item is not None
        item.owner_user_id = None
        webhook = PlaidWebhookEvent(
            workspace_id=context.workspace_id,
            webhook_type="TRANSACTIONS",
            webhook_code="SYNC_UPDATES_AVAILABLE",
            plaid_item_id=item.item_id,
            item_id=item.id,
            processing_status="queued",
        )
        db.add(webhook)
        db.commit()
        item_id = item.id
        webhook_id = webhook.id

    service_construction_attempts: list[bool] = []

    class UnexpectedTransactionService:
        def __init__(self, _db):
            service_construction_attempts.append(True)

    monkeypatch.setattr(app_db, "SessionLocal", factory)
    monkeypatch.setattr(plaid_routes, "TransactionService", UnexpectedTransactionService)

    plaid_routes._sync_item_by_db_id(  # noqa: SLF001
        item_id,
        webhook_id,
        workspace_id=context.workspace_id,
    )

    assert service_construction_attempts == []
    with factory() as db:
        webhook = db.get(PlaidWebhookEvent, webhook_id)
        assert webhook is not None
        assert webhook.processing_status == "failed"
        assert webhook.error_message == "RuntimeError"
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

from __future__ import annotations

from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base
from app.models import (
    AgentActionProposal,
    AgentConversation,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    DataConsent,
    GmailAccount,
    OAuthState,
    OutboxEvent,
    PlaidWebhookEvent,
    PurchaseReceipt,
    RateLimitEvent,
    TelegramSession,
    TelegramWebhookUpdate,
    User,
    WorkspaceMembership,
    utc_now,
)
from app.security_middleware import SecurityHeadersMiddleware, install_safe_exception_handler
from app.services.data_lifecycle_service import DataLifecycleService, gmail_consent_granted
from app.services.outbox_service import complete_outbox_event, replay_dead_outbox_events
from app.tenancy import ensure_default_tenancy, set_session_tenant


def test_versioned_encryption_reads_legacy_and_previous_keys(monkeypatch):
    import app.security as security

    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()
    settings = Settings(
        app_secret_key=new,
        app_secret_key_version="v2",
        app_secret_key_previous=[f"v1:{old}"],
        _env_file=None,
    )
    monkeypatch.setattr(security, "get_settings", lambda: settings)

    encrypted = security.encrypt_secret("current secret")
    legacy = Fernet(old.encode()).encrypt(b"legacy secret").decode()
    versioned_old = f"v1:{Fernet(old.encode()).encrypt(b'previous secret').decode()}"

    assert encrypted.startswith("v2:")
    assert security.decrypt_secret(encrypted) == "current secret"
    assert security.decrypt_secret(legacy) == "legacy secret"
    assert security.decrypt_secret(versioned_old) == "previous secret"
    assert security.rotate_secret(versioned_old).startswith("v2:")


def test_security_headers_and_safe_errors_do_not_leak_exception_details():
    application = FastAPI()
    settings = Settings(_env_file=None)
    application.add_middleware(SecurityHeadersMiddleware, settings=settings)
    install_safe_exception_handler(application)

    @application.get("/boom")
    def boom():
        raise RuntimeError("provider token secret should never be public")

    response = TestClient(application, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "provider token" not in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_consent_and_account_deletion_revoke_identity_and_credentials(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'privacy.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        context = ensure_default_tenancy(db)
        set_session_tenant(db, context)
        user = db.get(User, context.user_id)
        service = DataLifecycleService(db, Settings(_env_file=None))

        consent = service.set_consent(
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            purpose="gmail_receipts",
            granted=True,
        )
        assert consent.granted is True
        assert service.consent_status(
            workspace_id=context.workspace_id,
            user_id=context.user_id,
        )["gmail_receipts"] is True

        service.delete_account(user)
        deleted = db.get(User, context.user_id)
        assert deleted.status == "deleted"
        assert deleted.deleted_at is not None
        assert deleted.email == f"deleted-{context.user_id}@expenseops.invalid"


def test_account_deletion_removes_user_owned_agent_history(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agent-privacy.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        context = ensure_default_tenancy(db)
        set_session_tenant(db, context)
        conversation = AgentConversation(owner_user_id=context.user_id, title="Private thread")
        db.add(conversation)
        db.flush()
        message = AgentMessage(
            conversation_id=conversation.id,
            owner_user_id=context.user_id,
            role="user",
            content="Private financial question",
        )
        db.add(message)
        db.flush()
        run = AgentRun(
            conversation_id=conversation.id,
            owner_user_id=context.user_id,
            trigger_message_id=message.id,
        )
        db.add(run)
        db.flush()
        tool_call = AgentToolCall(
            run_id=run.id,
            owner_user_id=context.user_id,
            sequence=0,
            tool_name="read_only_test",
            operation_kind="read",
        )
        db.add(tool_call)
        db.flush()
        proposal = AgentActionProposal(
            owner_user_id=context.user_id,
            conversation_id=conversation.id,
            run_id=run.id,
            tool_call_id=tool_call.id,
            tool_name="write_test",
            operation_kind="write",
            normalized_parameters_json={"transaction_id": "opaque"},
            parameters_hash="a" * 64,
            action_fingerprint="b" * 64,
            idempotency_key="account-deletion-test",
            expires_at=utc_now() + timedelta(minutes=15),
        )
        db.add(proposal)
        db.commit()

        with pytest.raises(ValueError, match="snapshot is immutable"):
            proposal.normalized_parameters_json = {"transaction_id": "changed"}

        DataLifecycleService(db, Settings(_env_file=None)).delete_account(
            db.get(User, context.user_id)
        )

        for model in (
            AgentActionProposal,
            AgentToolCall,
            AgentRun,
            AgentMessage,
            AgentConversation,
        ):
            assert db.scalar(select(model.id)) is None


def test_account_deletion_requires_owner_transfer_for_shared_workspace(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'shared-privacy.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        context = ensure_default_tenancy(db)
        second = User(email="member@example.com", display_name="Member")
        db.add(second)
        db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=context.workspace_id,
                user_id=second.id,
                role="member",
            )
        )
        db.commit()
        set_session_tenant(db, context)

        with pytest.raises(ValueError, match="Transfer ownership"):
            DataLifecycleService(db, Settings(_env_file=None)).delete_account(
                db.get(User, context.user_id)
            )


def test_member_deletion_leaves_shared_workspace_records_for_remaining_members(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'member-privacy.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner_context = ensure_default_tenancy(db)
        member = User(email="departing@example.com", display_name="Departing member")
        db.add(member)
        db.flush()
        membership = WorkspaceMembership(
            workspace_id=owner_context.workspace_id,
            user_id=member.id,
            role="member",
        )
        receipt = PurchaseReceipt(
            workspace_id=owner_context.workspace_id,
            source="gmail",
            source_external_id="shared-receipt",
        )
        db.add_all([membership, receipt])
        db.commit()
        member_context = type(owner_context)(member.id, owner_context.workspace_id)
        set_session_tenant(db, member_context)
        service = DataLifecycleService(db, Settings(_env_file=None))
        service.set_consent(
            workspace_id=owner_context.workspace_id,
            user_id=member.id,
            purpose="gmail_receipts",
            granted=True,
        )

        service.delete_account(member)

        assert db.get(PurchaseReceipt, receipt.id) is not None
        assert db.get(WorkspaceMembership, membership.id) is None
        assert db.scalar(select(DataConsent).where(DataConsent.user_id == member.id)) is None
        assert db.get(User, member.id).status == "deleted"


def test_managed_gmail_consent_revocation_stops_processing(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'gmail-consent.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        context = ensure_default_tenancy(db)
        db.add(
            GmailAccount(
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                google_user_id="owner@example.com",
                refresh_token_encrypted="not-read-by-this-test",
            )
        )
        db.commit()
        set_session_tenant(db, context)
        service = DataLifecycleService(db, Settings(_env_file=None))

        assert gmail_consent_granted(db, "gmail_receipts") is False
        service.set_consent(
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            purpose="gmail_receipts",
            granted=True,
        )
        assert gmail_consent_granted(db, "gmail_receipts") is True
        service.set_consent(
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            purpose="gmail_receipts",
            granted=False,
        )
        assert gmail_consent_granted(db, "gmail_receipts") is False


def test_retention_job_purges_only_expired_ephemeral_rows(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'retention.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        context = ensure_default_tenancy(db)
        old = utc_now() - timedelta(days=3)
        current = utc_now() + timedelta(days=1)
        expired_telegram_update = TelegramWebhookUpdate(
            update_id=7001,
            payload_hash="a" * 64,
            state="processed",
            received_at=old,
            processed_at=old,
        )
        replayable_telegram_update = TelegramWebhookUpdate(
            update_id=7002,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            payload_hash="b" * 64,
            state="failed",
            received_at=old,
        )
        db.add_all([expired_telegram_update, replayable_telegram_update])
        db.flush()
        expired_telegram_update_id = expired_telegram_update.id
        replayable_telegram_update_id = replayable_telegram_update.id
        replayable_telegram_provider_update_id = replayable_telegram_update.update_id
        db.add_all(
            [
                OAuthState(
                    provider="oidc",
                    state_hash="old",
                    expires_at=old,
                    created_at=old,
                ),
                OAuthState(
                    provider="oidc",
                    state_hash="current",
                    expires_at=current,
                    created_at=utc_now(),
                ),
                RateLimitEvent(
                    key="old",
                    window_started_at=old,
                    window_seconds=60,
                    request_count=1,
                    created_at=old,
                ),
                PlaidWebhookEvent(
                    webhook_type="TRANSACTIONS",
                    webhook_code="SYNC_UPDATES_AVAILABLE",
                    processing_status="processed",
                    received_at=old,
                    processed_at=old,
                    created_at=old,
                ),
                PlaidWebhookEvent(
                    webhook_type="TRANSACTIONS",
                    webhook_code="SYNC_UPDATES_AVAILABLE",
                    processing_status="processed",
                    received_at=current,
                    processed_at=current,
                    created_at=old,
                ),
                OutboxEvent(
                    workspace_id=context.workspace_id,
                    event_type="telegram.process_update",
                    aggregate_type="telegram_webhook_update",
                    aggregate_id="expired",
                    dedupe_key="retention-expired",
                    payload_json={"message": "sensitive transient content"},
                    state="succeeded",
                    completed_at=old,
                    created_at=old,
                    updated_at=old,
                ),
                OutboxEvent(
                    workspace_id=context.workspace_id,
                    event_type="telegram.process_update",
                    aggregate_type="telegram_webhook_update",
                    aggregate_id=str(replayable_telegram_update_id),
                    dedupe_key="retention-dead-current",
                    payload_json={
                        "update_record_id": replayable_telegram_update_id,
                        "update": {"update_id": replayable_telegram_provider_update_id},
                    },
                    state="dead",
                    created_at=old,
                    updated_at=current,
                ),
                OutboxEvent(
                    workspace_id=context.workspace_id,
                    event_type="telegram.process_update",
                    aggregate_type="telegram_webhook_update",
                    aggregate_id="current",
                    dedupe_key="retention-current",
                    payload_json={"message": "still within retention"},
                    state="succeeded",
                    completed_at=current,
                    created_at=old,
                    updated_at=current,
                ),
                OutboxEvent(
                    workspace_id=context.workspace_id,
                    event_type="telegram.process_update",
                    aggregate_type="telegram_webhook_update",
                    aggregate_id="dead",
                    dedupe_key="retention-dead",
                    payload_json={"message": "requires operator recovery"},
                    state="dead",
                    completed_at=old,
                    created_at=old,
                    updated_at=old,
                ),
                TelegramSession(
                    workspace_id=context.workspace_id,
                    chat_id="old-chat",
                    user_id="old-user",
                    state_data={"last_ai_message": "expired private conversation"},
                    created_at=old,
                    updated_at=old,
                ),
                TelegramSession(
                    workspace_id=context.workspace_id,
                    chat_id="current-chat",
                    user_id="current-user",
                    state_data={"mode": "people"},
                    created_at=old,
                    updated_at=current,
                ),
            ]
        )
        db.commit()

        counts = DataLifecycleService(
            db,
            Settings(
                retention_completed_outbox_days=1,
                retention_webhook_days=1,
                retention_telegram_session_days=1,
                _env_file=None,
            ),
        ).purge_expired()

        assert counts["oauth_states"] == 1
        assert counts["rate_limits"] == 1
        assert counts["telegram_webhook_updates"] == 1
        assert counts["plaid_webhook_events"] == 1
        assert counts["completed_outbox_events"] == 1
        assert counts["discarded_telegram_outbox_payloads"] == 1
        assert counts["telegram_sessions"] == 1
        assert db.scalar(select(OAuthState).where(OAuthState.state_hash == "current")) is not None
        assert db.scalar(select(PlaidWebhookEvent.id)) is not None
        assert (
            db.scalar(
                select(TelegramWebhookUpdate.id).where(
                    TelegramWebhookUpdate.id == expired_telegram_update_id
                )
            )
            is None
        )
        assert (
            db.scalar(
                select(TelegramWebhookUpdate.id).where(
                    TelegramWebhookUpdate.id == replayable_telegram_update_id
                )
            )
            == replayable_telegram_update_id
        )
        assert (
            db.scalar(select(OutboxEvent).where(OutboxEvent.dedupe_key == "retention-expired"))
            is None
        )
        assert (
            db.scalar(select(OutboxEvent).where(OutboxEvent.dedupe_key == "retention-current"))
            is not None
        )
        discarded = db.scalar(
            select(OutboxEvent).where(OutboxEvent.dedupe_key == "retention-dead")
        )
        assert discarded is not None
        assert discarded.state == "discarded"
        assert discarded.payload_json == {}
        assert discarded.last_error == "retention_scrubbed"
        recoverable = db.scalar(
            select(OutboxEvent).where(OutboxEvent.dedupe_key == "retention-dead-current")
        )
        assert recoverable is not None
        assert recoverable.state == "dead"
        assert recoverable.payload_json == {
            "update_record_id": replayable_telegram_update_id,
            "update": {"update_id": replayable_telegram_provider_update_id},
        }
        assert replay_dead_outbox_events(db, event_type="telegram.process_update") == 1
        db.refresh(recoverable)
        assert recoverable.state == "pending"
        assert db.get(TelegramWebhookUpdate, replayable_telegram_update_id) is not None
        assert (
            db.scalar(select(TelegramSession).where(TelegramSession.chat_id == "old-chat"))
            is None
        )
        assert (
            db.scalar(select(TelegramSession).where(TelegramSession.chat_id == "current-chat"))
            is not None
        )


def test_successful_telegram_outbox_delivery_discards_raw_update_payload(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'telegram-outbox-privacy.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        context = ensure_default_tenancy(db)
        event = OutboxEvent(
            workspace_id=context.workspace_id,
            event_type="telegram.process_update",
            aggregate_type="telegram_webhook_update",
            aggregate_id="42",
            dedupe_key="telegram-update:42",
            payload_json={
                "update_record_id": 42,
                "update": {"message": {"text": "/connect SECRET-CODE"}},
            },
            state="in_flight",
            lease_token="lease-token",
            lease_expires_at=utc_now() + timedelta(minutes=1),
        )
        db.add(event)
        db.commit()

        assert complete_outbox_event(db, event.id, "lease-token") is True
        db.refresh(event)

        assert event.state == "succeeded"
        assert event.payload_json == {}


def test_retention_preserves_plaid_webhook_required_for_dead_job_replay(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'plaid-replay-retention.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        context = ensure_default_tenancy(db)
        old = utc_now() - timedelta(days=3)
        webhook = PlaidWebhookEvent(
            workspace_id=context.workspace_id,
            webhook_type="TRANSACTIONS",
            webhook_code="SYNC_UPDATES_AVAILABLE",
            processing_status="failed",
            received_at=old,
            processed_at=old,
            created_at=old,
        )
        db.add(webhook)
        db.flush()
        outbox = OutboxEvent(
            workspace_id=context.workspace_id,
            event_type="plaid.sync_item",
            aggregate_type="plaid_item",
            aggregate_id="42",
            dedupe_key=f"plaid-webhook:{webhook.id}",
            payload_json={"item_id": 42, "webhook_event_id": webhook.id},
            state="dead",
            completed_at=old,
            created_at=old,
            updated_at=old,
        )
        db.add(outbox)
        db.commit()
        webhook_id = webhook.id
        outbox_id = outbox.id

        service = DataLifecycleService(
            db,
            Settings(
                retention_completed_outbox_days=1,
                retention_webhook_days=1,
                _env_file=None,
            ),
        )
        first_counts = service.purge_expired()

        assert first_counts["plaid_webhook_events"] == 0
        assert db.get(PlaidWebhookEvent, webhook_id) is not None
        assert replay_dead_outbox_events(db, event_type="plaid.sync_item") == 1
        db.refresh(outbox)
        assert outbox.state == "pending"

        outbox.state = "succeeded"
        outbox.completed_at = old
        outbox.updated_at = old
        db.commit()
        second_counts = service.purge_expired()

        assert second_counts["completed_outbox_events"] == 1
        assert second_counts["plaid_webhook_events"] == 1
        assert db.get(OutboxEvent, outbox_id) is None
        assert db.get(PlaidWebhookEvent, webhook_id) is None

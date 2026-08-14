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
    DataConsent,
    GmailAccount,
    OAuthState,
    PurchaseReceipt,
    RateLimitEvent,
    User,
    WorkspaceMembership,
    utc_now,
)
from app.security_middleware import SecurityHeadersMiddleware, install_safe_exception_handler
from app.services.data_lifecycle_service import DataLifecycleService, gmail_consent_granted
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
        ensure_default_tenancy(db)
        old = utc_now() - timedelta(days=3)
        current = utc_now() + timedelta(days=1)
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
            ]
        )
        db.commit()

        counts = DataLifecycleService(db, Settings(_env_file=None)).purge_expired()

        assert counts["oauth_states"] == 1
        assert counts["rate_limits"] == 1
        assert db.scalar(select(OAuthState).where(OAuthState.state_hash == "current")) is not None

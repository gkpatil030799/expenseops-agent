from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.auth as auth
from app.api import admin_routes, integration_routes, plaid_routes
from app.config import Settings
from app.db import Base, get_db
from app.main import app
from app.models import (
    AuditEvent,
    AuthIdentity,
    GmailAccount,
    OAuthState,
    PlaidItem,
    SplitwiseIntegration,
    TelegramIdentity,
    TelegramLinkCode,
    User,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
    utc_now,
)
from app.rate_limit import InMemoryRateLimiter
from app.services.managed_auth_service import (
    OIDCValidationError,
    OIDCVerifier,
    calculate_oidc_at_hash,
    provision_oidc_identity,
    record_audit,
)
from app.services.oauth_state_service import (
    OAuthStateError,
    consume_oauth_state,
    create_oauth_state,
)
from app.tenancy import TenantContext, ensure_default_tenancy, hash_api_token, set_session_tenant

_TEST_SIGNING_KEY = "test-signing-key-that-is-at-least-32-bytes"


@pytest.fixture
def database():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def onboarding_app(database, monkeypatch):
    with database() as db:
        users = [
            User(
                email="owner@example.test",
                display_name="Owner",
                api_token_hash=hash_api_token("owner-token"),
            ),
            User(
                email="guest@example.test",
                display_name="Guest",
                api_token_hash=hash_api_token("guest-token"),
            ),
        ]
        db.add_all(users)
        db.flush()
        workspaces = [
            Workspace(
                name="Owner personal",
                workspace_type="personal",
                created_by_user_id=users[0].id,
            ),
            Workspace(
                name="Guest personal",
                workspace_type="personal",
                created_by_user_id=users[1].id,
            ),
        ]
        db.add_all(workspaces)
        db.flush()
        db.add_all(
            [
                WorkspaceMembership(
                    user_id=user.id,
                    workspace_id=workspace.id,
                    role="owner",
                    is_default=True,
                )
                for user, workspace in zip(users, workspaces, strict=True)
            ]
        )
        db.commit()
        contexts = {
            "owner": TenantContext(users[0].id, workspaces[0].id),
            "guest": TenantContext(users[1].id, workspaces[1].id),
        }

    monkeypatch.setattr(auth, "SessionLocal", database)
    monkeypatch.setattr(auth, "_auth_not_required", lambda _settings: False)

    def override_get_db(request: Request):
        with database() as db:
            set_session_tenant(
                db,
                TenantContext(request.state.user_id, request.state.workspace_id),
            )
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), database, contexts
    finally:
        app.dependency_overrides.clear()


def _oidc_settings() -> Settings:
    return Settings(
        auth_mode="oidc",
        oidc_issuer="https://identity.example",
        oidc_audience="expenseops",
        oidc_client_id="expenseops-client",
        oidc_redirect_uri="http://localhost:8000/auth/callback",
        oidc_algorithms=["HS256"],
        _env_file=None,
    )


def _identity_token(**overrides) -> str:
    claims = {
        "sub": "subject-123",
        "email": "person@example.test",
        "email_verified": True,
        "name": "Person",
        "iss": "https://identity.example",
        "aud": "expenseops",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, _TEST_SIGNING_KEY, algorithm="HS256")


def _verifier() -> OIDCVerifier:
    return OIDCVerifier(_oidc_settings(), key_resolver=lambda _token: _TEST_SIGNING_KEY)


def test_first_login_provisions_user_personal_workspace_and_owner(database):
    claims = _verifier().validate(_identity_token())
    with database() as db:
        user, workspace, created = provision_oidc_identity(
            db, claims, provider="https://identity.example"
        )
        membership = db.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.user_id == user.id,
                WorkspaceMembership.workspace_id == workspace.id,
            )
        )
        assert created is True
        assert workspace.workspace_type == "personal"
        assert membership is not None
        assert membership.role == "owner"
        assert membership.is_default is True


def test_unverified_oidc_email_is_rejected(database):
    claims = _verifier().validate(_identity_token(email_verified=False))
    with database() as db, pytest.raises(OIDCValidationError, match="verify the email"):
        provision_oidc_identity(db, claims, provider="https://identity.example")


def test_oidc_at_hash_is_validated_when_access_token_is_available():
    access_token = "provider-access-token"
    token = _identity_token(at_hash=calculate_oidc_at_hash(access_token, "HS256"))

    claims = _verifier().validate(token, access_token=access_token)

    assert claims["sub"] == "subject-123"
    with pytest.raises(OIDCValidationError):
        _verifier().validate(token, access_token="different-access-token")


def test_repeat_login_is_idempotent_and_email_change_uses_subject(database):
    with database() as db:
        first_user, first_workspace, _ = provision_oidc_identity(
            db, _verifier().validate(_identity_token()), provider="https://identity.example"
        )
        second_user, second_workspace, created = provision_oidc_identity(
            db,
            _verifier().validate(_identity_token(email="renamed@example.test")),
            provider="https://identity.example",
        )
        assert created is False
        assert second_user.id == first_user.id
        assert second_workspace.id == first_workspace.id
        assert second_user.email == "renamed@example.test"
        assert db.scalar(select(func.count(AuthIdentity.id))) == 1
        assert db.scalar(select(func.count(Workspace.id))) == 1
        assert db.scalar(select(func.count(WorkspaceMembership.id))) == 1


def test_verified_bootstrap_owner_claims_migrated_workspace_without_sql(database):
    with database() as db:
        migrated = ensure_default_tenancy(db)
        settings = _oidc_settings().model_copy(
            update={"oidc_bootstrap_email": "person@example.test"}
        )
        user, workspace, created = provision_oidc_identity(
            db,
            _verifier().validate(_identity_token()),
            provider="https://identity.example",
            settings=settings,
        )
        assert created is True
        assert user.id == migrated.user_id
        assert workspace.id == migrated.workspace_id
        assert user.email == "person@example.test"
        assert db.scalar(select(func.count(Workspace.id))) == 1
        assert db.scalar(select(func.count(AuthIdentity.id))) == 1


def test_verified_bootstrap_claims_legacy_integrations_and_default_stays_stable(
    database, monkeypatch
):
    import app.services.managed_auth_service as managed_auth

    monkeypatch.setattr(managed_auth, "encrypt_secret", lambda value: f"encrypted:{value}")
    with database() as db:
        migrated = ensure_default_tenancy(db)
        settings = _oidc_settings().model_copy(
            update={
                "oidc_bootstrap_email": "person@example.test",
                "gmail_refresh_token": "gmail-token",
                "gmail_user_id": "owner@gmail.test",
                "telegram_allowed_user_id": "123",
                "telegram_chat_id": "456",
                "splitwise_api_key": "splitwise-key",
            }
        )
        provision_oidc_identity(
            db,
            _verifier().validate(_identity_token()),
            provider="https://identity.example",
            settings=settings,
        )
        assert ensure_default_tenancy(db) == migrated
        gmail = db.scalar(select(GmailAccount).execution_options(skip_tenant_scope=True))
        telegram = db.scalar(select(TelegramIdentity).execution_options(skip_tenant_scope=True))
        from app.models import SplitwiseIntegration

        splitwise = db.scalar(
            select(SplitwiseIntegration).execution_options(skip_tenant_scope=True)
        )
        assert gmail.workspace_id == migrated.workspace_id
        assert gmail.refresh_token_encrypted == "encrypted:gmail-token"
        assert telegram.telegram_user_id == "123"
        assert splitwise.credentials_encrypted.startswith("encrypted:")
        assert db.scalar(select(func.count(User.id))) == 1
        assert db.scalar(select(func.count(Workspace.id))) == 1


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        ("not-a-jwt", "invalid"),
        (_identity_token(exp=datetime.now(UTC) - timedelta(seconds=1)), "expired"),
        (_identity_token(iss="https://attacker.example"), "issuer"),
        (_identity_token(aud="another-app"), "audience"),
    ],
)
def test_invalid_oidc_tokens_are_rejected(token, reason):
    with pytest.raises(OIDCValidationError):
        _verifier().validate(token)


def test_workspace_create_switch_and_nonmember_rejection(onboarding_app):
    client, database, contexts = onboarding_app
    created = client.post(
        "/api/workspaces",
        headers={"Authorization": "Bearer owner-token"},
        json={"name": "Household"},
    )
    assert created.status_code == 201
    workspace_id = created.json()["id"]
    switched = client.post(
        f"/api/workspaces/{workspace_id}/switch",
        headers={"Authorization": "Bearer owner-token"},
    )
    assert switched.status_code == 200
    denied = client.post(
        f"/api/workspaces/{workspace_id}/switch",
        headers={"Authorization": "Bearer guest-token"},
    )
    assert denied.status_code == 404
    with database() as db:
        default = db.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.user_id == contexts["owner"].user_id,
                WorkspaceMembership.is_default.is_(True),
            )
        )
        assert default.workspace_id == workspace_id


def test_last_owner_cannot_leave(onboarding_app):
    client, _database, contexts = onboarding_app
    client.post(
        "/api/workspaces",
        headers={"Authorization": "Bearer owner-token"},
        json={"name": "Second"},
    )
    response = client.delete(
        f"/api/workspaces/{contexts['owner'].workspace_id}/membership",
        headers={"Authorization": "Bearer owner-token"},
    )
    assert response.status_code == 400
    assert "last owner" in response.json()["detail"].lower()


def _invite(client: TestClient, workspace_id: int, email: str = "guest@example.test") -> str:
    response = client.post(
        f"/api/workspaces/{workspace_id}/invitations",
        headers={"Authorization": "Bearer owner-token"},
        json={"email": email, "role": "member"},
    )
    assert response.status_code == 201
    return response.json()["invite_token"]


def test_request_id_is_sanitized_before_invitation_audit_is_persisted(onboarding_app):
    client, database, contexts = onboarding_app
    untrusted_request_id = "oversized-" + "x" * 100

    response = client.post(
        f"/api/workspaces/{contexts['owner'].workspace_id}/invitations",
        headers={
            "Authorization": "Bearer owner-token",
            "X-Request-ID": untrusted_request_id,
        },
        json={"email": "audit@example.test", "role": "member"},
    )

    assert response.status_code == 201
    sanitized_request_id = response.headers["x-request-id"]
    assert sanitized_request_id != untrusted_request_id
    assert len(sanitized_request_id) <= 64
    with database() as db:
        event = db.scalar(
            select(AuditEvent)
            .execution_options(skip_tenant_scope=True)
            .where(AuditEvent.event_type == "member_invited")
        )
        assert event.request_id == sanitized_request_id


def test_valid_invitation_is_single_use_and_does_not_accept_guessed_workspace(onboarding_app):
    client, database, contexts = onboarding_app
    token = _invite(client, contexts["owner"].workspace_id)
    accepted = client.post(
        "/api/workspaces/invitations/accept",
        headers={"Authorization": "Bearer guest-token"},
        json={"token": token},
    )
    replayed = client.post(
        "/api/workspaces/invitations/accept",
        headers={"Authorization": "Bearer guest-token"},
        json={"token": token},
    )
    assert accepted.status_code == 200
    assert replayed.status_code == 400
    with database() as db:
        assert db.scalar(
            select(WorkspaceMembership.id).where(
                WorkspaceMembership.user_id == contexts["guest"].user_id,
                WorkspaceMembership.workspace_id == contexts["owner"].workspace_id,
            )
        )
        selected = db.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.user_id == contexts["guest"].user_id,
                WorkspaceMembership.is_default.is_(True),
            )
        )
        assert selected.workspace_id == contexts["owner"].workspace_id


def test_workspace_integration_permissions_and_personal_telegram_disconnect(
    onboarding_app,
):
    client, database, contexts = onboarding_app
    token = _invite(client, contexts["owner"].workspace_id)
    assert (
        client.post(
            "/api/workspaces/invitations/accept",
            headers={"Authorization": "Bearer guest-token"},
            json={"token": token},
        ).status_code
        == 200
    )
    with database() as db:
        db.info["workspace_id"] = contexts["owner"].workspace_id
        db.add_all(
            [
                GmailAccount(
                    workspace_id=contexts["owner"].workspace_id,
                    user_id=contexts["owner"].user_id,
                    google_user_id="owner@gmail.test",
                    refresh_token_encrypted="encrypted",
                ),
                PlaidItem(
                    workspace_id=contexts["owner"].workspace_id,
                    item_id="shared-item",
                    access_token_encrypted="encrypted",
                ),
                SplitwiseIntegration(
                    workspace_id=contexts["owner"].workspace_id,
                    user_id=contexts["owner"].user_id,
                    credentials_encrypted="encrypted",
                ),
                TelegramIdentity(
                    workspace_id=contexts["owner"].workspace_id,
                    user_id=contexts["owner"].user_id,
                    telegram_user_id="owner-telegram",
                    chat_id="owner-chat",
                ),
                TelegramIdentity(
                    workspace_id=contexts["owner"].workspace_id,
                    user_id=contexts["guest"].user_id,
                    telegram_user_id="guest-telegram",
                    chat_id="guest-chat",
                ),
            ]
        )
        db.commit()
        plaid_id = db.scalar(select(PlaidItem.id).where(PlaidItem.item_id == "shared-item"))

    member_headers = {"Authorization": "Bearer guest-token"}
    assert client.delete("/api/integrations/gmail", headers=member_headers).status_code == 403
    assert (
        client.delete(f"/api/integrations/plaid/{plaid_id}", headers=member_headers).status_code
        == 403
    )
    # Splitwise is personal: disconnecting as the guest cannot touch the owner's account.
    assert client.delete("/api/integrations/splitwise", headers=member_headers).status_code == 204
    status = client.get("/api/integrations", headers=member_headers)
    assert status.status_code == 200
    assert status.json()["telegram"]["connected"] is True
    assert client.delete("/api/integrations/telegram", headers=member_headers).status_code == 204

    with database() as db:
        identities = list(
            db.scalars(select(TelegramIdentity).execution_options(skip_tenant_scope=True))
        )
        assert [(identity.user_id, identity.telegram_user_id) for identity in identities] == [
            (contexts["owner"].user_id, "owner-telegram")
        ]
        assert db.scalar(select(GmailAccount.id).execution_options(skip_tenant_scope=True))
        assert db.scalar(select(PlaidItem.enabled).execution_options(skip_tenant_scope=True))
        assert db.scalar(select(SplitwiseIntegration.id).execution_options(skip_tenant_scope=True))


def test_owner_can_transfer_ownership_and_new_owner_can_remove_member(onboarding_app):
    client, database, contexts = onboarding_app
    token = _invite(client, contexts["owner"].workspace_id)
    client.post(
        "/api/workspaces/invitations/accept",
        headers={"Authorization": "Bearer guest-token"},
        json={"token": token},
    )
    transfer = client.post(
        f"/api/workspaces/{contexts['owner'].workspace_id}/members/"
        f"{contexts['guest'].user_id}/transfer-ownership",
        headers={"Authorization": "Bearer owner-token"},
    )
    assert transfer.status_code == 200
    with database() as db:
        roles = {
            membership.user_id: membership.role
            for membership in db.scalars(
                select(WorkspaceMembership).where(
                    WorkspaceMembership.workspace_id == contexts["owner"].workspace_id
                )
            )
        }
        assert roles == {
            contexts["owner"].user_id: "member",
            contexts["guest"].user_id: "owner",
        }

    removed = client.delete(
        f"/api/workspaces/{contexts['owner'].workspace_id}/members/{contexts['owner'].user_id}",
        headers={"Authorization": "Bearer guest-token"},
    )
    assert removed.status_code == 204
    with database() as db:
        assert (
            db.scalar(
                select(WorkspaceMembership.id).where(
                    WorkspaceMembership.workspace_id == contexts["owner"].workspace_id,
                    WorkspaceMembership.user_id == contexts["owner"].user_id,
                )
            )
            is None
        )


def test_expired_and_wrong_email_invitations_are_rejected(onboarding_app):
    client, database, contexts = onboarding_app
    wrong_email_token = _invite(client, contexts["owner"].workspace_id, "other@example.test")
    wrong = client.post(
        "/api/workspaces/invitations/accept",
        headers={"Authorization": "Bearer guest-token"},
        json={"token": wrong_email_token},
    )
    assert wrong.status_code == 403

    expired_token = _invite(client, contexts["owner"].workspace_id)
    with database() as db:
        invitation = db.scalar(
            select(WorkspaceInvitation).where(
                WorkspaceInvitation.token_hash == hash_api_token(expired_token)
            )
        )
        invitation.expires_at = utc_now() - timedelta(minutes=1)
        db.commit()
    expired = client.post(
        "/api/workspaces/invitations/accept",
        headers={"Authorization": "Bearer guest-token"},
        json={"token": expired_token},
    )
    assert expired.status_code == 400


def test_oauth_state_is_durable_tenant_bound_and_single_use(database):
    with database() as db:
        raw = create_oauth_state(
            db,
            provider="gmail",
            user_id=1,
            workspace_id=11,
            payload="refresh-secret",
        )
    with database() as restarted_session:
        with pytest.raises(OAuthStateError):
            consume_oauth_state(
                restarted_session,
                raw,
                provider="gmail",
                user_id=2,
                workspace_id=11,
            )
        state, payload = consume_oauth_state(
            restarted_session,
            raw,
            provider="gmail",
            user_id=1,
            workspace_id=11,
        )
        assert state.used_at is not None
        assert payload == "refresh-secret"
        with pytest.raises(OAuthStateError):
            consume_oauth_state(
                restarted_session,
                raw,
                provider="gmail",
                user_id=1,
                workspace_id=11,
            )
        stored = restarted_session.scalar(select(OAuthState))
        assert stored.state_hash == hash_api_token(raw)
        assert raw not in stored.payload_encrypted


def test_gmail_callback_attaches_credentials_to_initiating_workspace(onboarding_app, monkeypatch):
    client, database, contexts = onboarding_app
    with database() as db:
        state = create_oauth_state(
            db,
            provider="gmail",
            user_id=contexts["owner"].user_id,
            workspace_id=contexts["owner"].workspace_id,
        )

    class TokenResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "refresh_token": "gmail-refresh-secret",
                "access_token": "gmail-access-secret",
            }

    class IdentityResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"email": "owner@gmail.test", "email_verified": True}

    monkeypatch.setattr(
        integration_routes,
        "get_settings",
        lambda: Settings(
            app_public_url="http://testserver",
            gmail_client_id="gmail-client",
            gmail_client_secret="gmail-secret",
            _env_file=None,
        ),
    )
    monkeypatch.setattr(integration_routes.httpx, "post", lambda *_args, **_kwargs: TokenResponse())
    monkeypatch.setattr(
        integration_routes.httpx,
        "get",
        lambda *_args, **_kwargs: IdentityResponse(),
    )
    monkeypatch.setattr(integration_routes, "encrypt_secret", lambda value: f"encrypted:{value}")
    response = client.get(
        "/api/integrations/gmail/callback",
        headers={"Authorization": "Bearer owner-token"},
        params={"code": "provider-code", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with database() as db:
        account = db.scalar(select(GmailAccount).execution_options(skip_tenant_scope=True))
        assert account.workspace_id == contexts["owner"].workspace_id
        assert account.user_id == contexts["owner"].user_id
        assert account.google_user_id == "owner@gmail.test"
        assert account.refresh_token_encrypted == "encrypted:gmail-refresh-secret"


def test_plaid_exchange_uses_authenticated_workspace(onboarding_app, monkeypatch):
    client, database, contexts = onboarding_app

    class PlaidService:
        def exchange_public_token(self, _public_token):
            return {"item_id": "plaid-owner-item", "access_token": "plaid-secret"}

    monkeypatch.setattr(plaid_routes, "PlaidService", PlaidService)
    monkeypatch.setattr(plaid_routes, "encrypt_secret", lambda value: f"encrypted:{value}")
    monkeypatch.setattr(
        plaid_routes.TransactionService,
        "sync_item",
        lambda _self, _item: {"added": 0},
    )
    response = client.post(
        "/plaid/exchange-public-token",
        headers={"Authorization": "Bearer owner-token"},
        json={"public_token": "public-token", "institution_name": "Test Bank"},
    )
    assert response.status_code == 200
    with database() as db:
        item = db.scalar(select(PlaidItem).execution_options(skip_tenant_scope=True))
        assert item.workspace_id == contexts["owner"].workspace_id
        assert item.access_token_encrypted == "encrypted:plaid-secret"


def test_telegram_link_code_is_hashed_and_can_only_be_consumed_once(database, monkeypatch):
    from app.api import telegram_routes

    sent: list[str] = []
    monkeypatch.setattr(
        telegram_routes.TelegramService,
        "send_message",
        lambda _self, text, **_kwargs: sent.append(text),
    )
    raw = "ABC123XYZ"
    with database() as db:
        user = User(email="telegram@example.test", display_name="Telegram")
        db.add(user)
        db.flush()
        workspace = Workspace(name="Telegram workspace", created_by_user_id=user.id)
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMembership(user_id=user.id, workspace_id=workspace.id, role="owner"))
        db.add(
            TelegramLinkCode(
                workspace_id=workspace.id,
                user_id=user.id,
                code_hash=hash_api_token(raw),
                expires_at=utc_now() + timedelta(minutes=5),
            )
        )
        db.commit()
        update = {
            "message": {
                "text": f"/start connect_{raw.lower()}",
                "from": {"id": 123},
                "chat": {"id": 456},
            }
        }
        assert telegram_routes._handle_telegram_connect(update, db) is True
        assert telegram_routes._handle_telegram_connect(update, db) is True
        identities = list(
            db.scalars(select(TelegramIdentity).execution_options(skip_tenant_scope=True))
        )
        assert len(identities) == 1
        assert "connected" in sent[0].lower()
        assert "invalid or expired" in sent[1].lower()


def test_telegram_link_code_cannot_add_second_active_identity_for_app_user(database, monkeypatch):
    from app.api import telegram_routes

    sent: list[str] = []
    monkeypatch.setattr(
        telegram_routes.TelegramService,
        "send_message",
        lambda _self, text, **_kwargs: sent.append(text),
    )
    first_code = "FIRSTCODE"
    second_code = "SECONDCODE"
    with database() as db:
        user = User(email="one-telegram@example.test", display_name="One Telegram")
        db.add(user)
        db.flush()
        workspace = Workspace(name="One Telegram", created_by_user_id=user.id)
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMembership(user_id=user.id, workspace_id=workspace.id, role="owner"))
        db.add_all(
            [
                TelegramLinkCode(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    code_hash=hash_api_token(first_code),
                    expires_at=utc_now() + timedelta(minutes=5),
                ),
                TelegramLinkCode(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    code_hash=hash_api_token(second_code),
                    expires_at=utc_now() + timedelta(minutes=5),
                ),
            ]
        )
        db.commit()

        assert telegram_routes._handle_telegram_connect(
            {
                "message": {
                    "text": f"/connect {first_code}",
                    "from": {"id": 111},
                    "chat": {"id": 111, "type": "private"},
                }
            },
            db,
        )
        assert telegram_routes._handle_telegram_connect(
            {
                "message": {
                    "text": f"/connect {second_code}",
                    "from": {"id": 222},
                    "chat": {"id": 222, "type": "private"},
                }
            },
            db,
        )

        identities = list(
            db.scalars(select(TelegramIdentity).execution_options(skip_tenant_scope=True))
        )
        second_link = db.scalar(
            select(TelegramLinkCode)
            .execution_options(skip_tenant_scope=True)
            .where(TelegramLinkCode.code_hash == hash_api_token(second_code))
        )
        assert [(identity.telegram_user_id, identity.chat_id) for identity in identities] == [
            ("111", "111")
        ]
        assert second_link.used_at is None
        assert "already linked" in sent[-1].lower()


def test_telegram_link_code_is_not_consumed_from_group_chat(database, monkeypatch):
    from app.api import telegram_routes

    monkeypatch.setattr(
        telegram_routes.TelegramService,
        "send_message",
        lambda _self, _text, **_kwargs: pytest.fail("group connect must not send a reply"),
    )
    raw = "PRIVATEONLY"
    with database() as db:
        user = User(email="private-telegram@example.test", display_name="Private Telegram")
        db.add(user)
        db.flush()
        workspace = Workspace(name="Private Telegram", created_by_user_id=user.id)
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMembership(user_id=user.id, workspace_id=workspace.id, role="owner"))
        link = TelegramLinkCode(
            workspace_id=workspace.id,
            user_id=user.id,
            code_hash=hash_api_token(raw),
            expires_at=utc_now() + timedelta(minutes=5),
        )
        db.add(link)
        db.commit()

        assert telegram_routes._handle_telegram_connect(
            {
                "message": {
                    "text": f"/connect {raw}",
                    "from": {"id": 333},
                    "chat": {"id": -333, "type": "group"},
                }
            },
            db,
        )

        db.refresh(link)
        assert link.used_at is None
        assert not list(
            db.scalars(select(TelegramIdentity).execution_options(skip_tenant_scope=True))
        )


def test_telegram_link_code_returns_direct_bot_link(database, monkeypatch):
    monkeypatch.setattr(
        integration_routes,
        "get_settings",
        lambda: Settings(telegram_bot_username="@ExpenseOps_bot"),
    )
    with database() as db:
        user = User(email="telegram-link@example.test", display_name="Telegram link")
        db.add(user)
        db.flush()
        workspace = Workspace(name="Telegram link", created_by_user_id=user.id)
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMembership(user_id=user.id, workspace_id=workspace.id, role="owner"))
        db.commit()

        value = integration_routes.telegram_link_code(db, user, workspace)

        assert value["connect_url"] == (
            f"https://t.me/ExpenseOps_bot?start=connect_{value['code']}"
        )
        assert value["command"] == f"/connect {value['code']}"
        stored = db.scalar(select(TelegramLinkCode))
        assert stored is not None
        assert stored.code_hash == hash_api_token(value["code"])
        assert value["code"] not in stored.code_hash


def test_integration_status_reports_splitwise_onboarding_availability(onboarding_app, monkeypatch):
    client, _database, _contexts = onboarding_app
    monkeypatch.setattr(
        integration_routes,
        "get_settings",
        lambda: Settings(
            splitwise_consumer_key="consumer-key",
            splitwise_consumer_secret="consumer-secret",
            _env_file=None,
        ),
    )

    response = client.get(
        "/api/integrations",
        headers={"Authorization": "Bearer owner-token"},
    )

    assert response.status_code == 200
    assert response.json()["splitwise"] == {
        "connected": False,
        "available": True,
        "identity": None,
        "email": None,
        "verified": False,
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/connect abc123", "ABC123"),
        ("/connect@ExpenseOps_bot abc123", "ABC123"),
        ("/start connect_abc123", "ABC123"),
        ("/start@ExpenseOps_bot CONNECT_abc123", "ABC123"),
        ("/start", None),
        ("hello", None),
    ],
)
def test_telegram_connect_code_accepts_deep_link_and_fallback(text, expected):
    from app.api.telegram_routes import _telegram_connect_code

    assert _telegram_connect_code(text) == expected


def test_expired_telegram_link_code_is_rejected(database, monkeypatch):
    from app.api import telegram_routes

    sent: list[str] = []
    monkeypatch.setattr(
        telegram_routes.TelegramService,
        "send_message",
        lambda _self, text, **_kwargs: sent.append(text),
    )
    with database() as db:
        user = User(email="expired@example.test", display_name="Expired")
        db.add(user)
        db.flush()
        workspace = Workspace(name="Expired", created_by_user_id=user.id)
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMembership(user_id=user.id, workspace_id=workspace.id, role="owner"))
        db.add(
            TelegramLinkCode(
                workspace_id=workspace.id,
                user_id=user.id,
                code_hash=hash_api_token("EXPIRED"),
                expires_at=utc_now() - timedelta(seconds=1),
            )
        )
        db.commit()
        telegram_routes._handle_telegram_connect(
            {
                "message": {
                    "text": "/connect EXPIRED",
                    "from": {"id": 123},
                    "chat": {"id": 456},
                }
            },
            db,
        )
        assert not list(
            db.scalars(select(TelegramIdentity).execution_options(skip_tenant_scope=True))
        )
        assert "invalid or expired" in sent[0].lower()


def test_audit_metadata_drops_sensitive_fields(database):
    with database() as db:
        user = User(email="audit@example.test", display_name="Audit")
        db.add(user)
        db.flush()
        workspace = Workspace(name="Audit", created_by_user_id=user.id)
        db.add(workspace)
        db.flush()
        record_audit(
            db,
            workspace_id=workspace.id,
            user_id=user.id,
            event_type="test",
            metadata={
                "provider": "oidc",
                "access_token": "never-store",
                "client_secret": "never-store",
                "email_body": "never-store",
            },
        )
        db.commit()
        event = db.scalar(select(AuditEvent))
        assert event.metadata_json == {"provider": "oidc"}


def test_admin_onboarding_funnel_is_allowlisted_and_contains_no_user_payload(
    onboarding_app, monkeypatch
):
    client, database, contexts = onboarding_app
    with database() as db:
        for event_type in (
            "user_first_login",
            "workspace_created",
            "gmail_connected",
            "telegram_connected",
            "onboarding_completed",
            "first_errand_created",
        ):
            record_audit(
                db,
                workspace_id=contexts["owner"].workspace_id,
                user_id=contexts["owner"].user_id,
                event_type=event_type,
            )
        db.commit()
    monkeypatch.setattr(
        admin_routes,
        "get_settings",
        lambda: Settings(admin_user_emails=["owner@example.test"], _env_file=None),
    )
    allowed = client.get(
        "/api/admin/onboarding-funnel",
        headers={"Authorization": "Bearer owner-token"},
    )
    denied = client.get(
        "/api/admin/onboarding-funnel",
        headers={"Authorization": "Bearer guest-token"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["authenticated_users"] == 1
    assert allowed.json()["users_with_successful_workflow"] == 1
    assert denied.status_code == 404


def test_first_login_records_safe_onboarding_events(database):
    with database() as db:
        user, workspace, _created = provision_oidc_identity(
            db,
            _verifier().validate(_identity_token()),
            provider="https://identity.example",
        )
        events = set(
            db.scalars(
                select(AuditEvent.event_type).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.user_id == user.id,
                )
            )
        )
        assert {"user_first_login", "workspace_created", "onboarding_started"} <= events


def test_in_memory_rate_limit_backend_enforces_window():
    limiter = InMemoryRateLimiter()
    limiter.check("login:client", limit=2, window_seconds=60)
    limiter.check("login:client", limit=2, window_seconds=60)
    with pytest.raises(HTTPException) as error:
        limiter.check("login:client", limit=2, window_seconds=60)
    assert error.value.status_code == 429


def test_local_auth_mode_supports_dev_token_but_oidc_mode_does_not():
    request = type(
        "Request",
        (),
        {"headers": {"Authorization": "Bearer local-token"}},
    )()
    local = Settings(auth_mode="local", dashboard_api_token="local-token", _env_file=None)
    oidc = _oidc_settings().model_copy(update={"dashboard_api_token": "local-token"})
    assert auth._is_authorized(request, local) is True
    assert auth._is_authorized(request, oidc) is False

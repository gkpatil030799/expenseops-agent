from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.auth as auth
import app.job_tenancy as job_tenancy
from app.api import context_routes, plaid_routes, telegram_routes
from app.config import Settings
from app.db import Base, get_db
from app.jobs import weekly_replenishment
from app.main import app
from app.models import (
    Errand,
    ExpenseTransaction,
    GmailAccount,
    GmailSyncCheckpoint,
    HouseholdItem,
    HouseholdItemAcquisition,
    OAuthState,
    PlaidItem,
    PromotionMessage,
    PromotionOffer,
    PromotionSettings,
    PurchaseReceipt,
    ReplenishmentPrediction,
    SavedLocation,
    TelegramIdentity,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.services.oauth_state_service import create_oauth_state
from app.services.receipt_ingestion_service import ReceiptIngestionService
from app.services.transaction_service import TransactionService
from app.tenancy import TenantContext, hash_api_token, set_session_tenant


@pytest.fixture
def tenant_db(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        user_a = User(
            email="a@example.test",
            display_name="User A",
            api_token_hash=hash_api_token("token-a"),
        )
        user_b = User(
            email="b@example.test",
            display_name="User B",
            api_token_hash=hash_api_token("token-b"),
        )
        db.add_all([user_a, user_b])
        db.flush()
        workspace_a = Workspace(name="Workspace A", created_by_user_id=user_a.id)
        workspace_b = Workspace(name="Workspace B", created_by_user_id=user_b.id)
        db.add_all([workspace_a, workspace_b])
        db.flush()
        db.add_all(
            [
                WorkspaceMembership(
                    workspace_id=workspace_a.id,
                    user_id=user_a.id,
                    role="owner",
                    is_default=True,
                ),
                WorkspaceMembership(
                    workspace_id=workspace_b.id,
                    user_id=user_b.id,
                    role="owner",
                    is_default=True,
                ),
            ]
        )
        db.commit()
        contexts = {
            "a": TenantContext(user_a.id, workspace_a.id),
            "b": TenantContext(user_b.id, workspace_b.id),
        }

    monkeypatch.setattr(auth, "SessionLocal", factory)
    monkeypatch.setattr(auth, "_auth_not_required", lambda _settings: False)

    def override_get_db(request: Request):
        with factory() as db:
            set_session_tenant(
                db,
                TenantContext(request.state.user_id, request.state.workspace_id),
            )
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield factory, contexts
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _scoped(factory, context: TenantContext) -> Session:
    db = factory()
    set_session_tenant(db, context)
    return db


def _seed_representative_data(factory, contexts):
    values = {}
    for key, context in contexts.items():
        with _scoped(factory, context) as db:
            plaid = PlaidItem(item_id=f"plaid-{key}", access_token_encrypted=f"encrypted-{key}")
            item = HouseholdItem(name=f"Detergent {key}", cadence_days=30)
            db.add_all([plaid, item])
            db.flush()
            transaction = ExpenseTransaction(
                plaid_transaction_id=f"transaction-{key}",
                plaid_item_id=plaid.id,
                name=f"Merchant {key}",
                amount_cents=1000,
            )
            receipt = PurchaseReceipt(
                source="gmail",
                source_external_id=f"gmail-receipt-{key}",
            )
            acquisition = HouseholdItemAcquisition(
                household_item_id=item.id,
                acquired_at=datetime.now(UTC),
                source="manual",
            )
            prediction = ReplenishmentPrediction(
                household_item_id=item.id,
                generated_at=datetime.now(UTC),
                predicted_need_at=datetime.now(UTC),
                predicted_days_remaining=3,
                due_score=0.5,
                method="baseline",
                confidence=0.5,
            )
            errand = Errand(title=f"Errand {key}")
            location = SavedLocation(label=f"Home {key}", address=f"{key} Main St")
            message = PromotionMessage(
                gmail_message_id=f"promo-message-{key}",
                subject=f"Offer {key}",
                received_at=datetime.now(UTC),
            )
            db.add_all([transaction, receipt, acquisition, prediction, errand, location, message])
            db.flush()
            offer = PromotionOffer(
                promotion_message_id=message.id,
                merchant_raw=f"Store {key}",
                merchant_normalized=f"store-{key}",
                headline=f"Deal {key}",
                campaign_fingerprint=f"fingerprint-{key}",
            )
            settings = PromotionSettings(minimum_score=20 if key == "a" else 80)
            checkpoint = GmailSyncCheckpoint(account_key=f"account-{key}:receipts")
            telegram = TelegramIdentity(
                user_id=context.user_id,
                telegram_user_id=f"telegram-{key}",
                chat_id=f"chat-{key}",
            )
            db.add_all([offer, settings, checkpoint, telegram])
            db.commit()
            values[key] = {
                "plaid": plaid.id,
                "transaction": transaction.id,
                "item": item.id,
                "receipt": receipt.id,
                "acquisition": acquisition.id,
                "prediction": prediction.id,
                "errand": errand.id,
                "location": location.id,
                "promotion": offer.id,
            }
    return values


def test_user_workspace_memberships_and_default_token_resolution(tenant_db):
    factory, contexts = tenant_db
    with factory() as db:
        memberships = list(db.scalars(select(WorkspaceMembership)))
    assert len(memberships) == 2
    assert all(value.is_default and value.role == "owner" for value in memberships)

    response = TestClient(app).get("/api/context", headers={"Authorization": "Bearer token-b"})
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "b@example.test"
    assert response.json()["workspace"]["id"] == contexts["b"].workspace_id


@pytest.mark.parametrize(
    ("agent_enabled", "read_tools_enabled", "expected"),
    [(False, False, False), (True, False, False), (True, True, True)],
)
def test_context_exposes_only_the_effective_read_only_agent_feature(
    tenant_db,
    monkeypatch,
    agent_enabled,
    read_tools_enabled,
    expected,
):
    _factory, _contexts = tenant_db
    monkeypatch.setattr(
        context_routes,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            agent_enabled=agent_enabled,
            agent_read_tools_enabled=read_tools_enabled,
        ),
    )

    response = TestClient(app).get("/api/context", headers={"Authorization": "Bearer token-a"})

    assert response.status_code == 200
    assert response.json()["features"]["agent"] == {
        "enabled": expected,
        "read_only": True,
        "proactive_enabled": False,
    }


@pytest.mark.parametrize(
    "model,key",
    [
        (PlaidItem, "plaid"),
        (ExpenseTransaction, "transaction"),
        (PurchaseReceipt, "receipt"),
        (HouseholdItem, "item"),
        (HouseholdItemAcquisition, "acquisition"),
        (ReplenishmentPrediction, "prediction"),
        (Errand, "errand"),
        (SavedLocation, "location"),
        (PromotionOffer, "promotion"),
    ],
)
def test_workspace_session_blocks_cross_tenant_direct_ids(tenant_db, model, key):
    factory, contexts = tenant_db
    values = _seed_representative_data(factory, contexts)
    with _scoped(factory, contexts["a"]) as db:
        assert db.get(model, values["a"][key]) is not None
        assert db.get(model, values["b"][key]) is None


def test_two_design_users_have_symmetric_data_and_integration_isolation(tenant_db):
    factory, contexts = tenant_db
    values = _seed_representative_data(factory, contexts)
    for key, context in contexts.items():
        with _scoped(factory, context) as db:
            db.add(
                GmailAccount(
                    user_id=context.user_id,
                    google_user_id=f"{key}@gmail.test",
                    refresh_token_encrypted=f"encrypted-gmail-{key}",
                )
            )
            db.commit()

    protected = (
        (PlaidItem, "plaid"),
        (PurchaseReceipt, "receipt"),
        (HouseholdItem, "item"),
        (ReplenishmentPrediction, "prediction"),
        (Errand, "errand"),
        (PromotionOffer, "promotion"),
    )
    for own, other in (("a", "b"), ("b", "a")):
        with _scoped(factory, contexts[own]) as db:
            for model, identifier in protected:
                assert db.get(model, values[own][identifier]) is not None
                assert db.get(model, values[other][identifier]) is None
            account = db.scalar(select(GmailAccount))
            telegram = db.scalar(select(TelegramIdentity))
            assert account.google_user_id == f"{own}@gmail.test"
            assert telegram.telegram_user_id == f"telegram-{own}"

        response = TestClient(app).get(
            "/api/integrations",
            headers={"Authorization": f"Bearer token-{own}"},
        )
        assert response.status_code == 200
        assert response.json()["gmail"]["connected"] is True
        assert response.json()["telegram"]["connected"] is True
        listed = TestClient(app).get(
            "/api/household/items",
            headers={"Authorization": f"Bearer token-{own}"},
        )
        assert {item["id"] for item in listed.json()} == {values[own]["item"]}


def test_transaction_notifications_resolve_telegram_chat_from_active_workspace(
    tenant_db, monkeypatch
):
    factory, contexts = tenant_db
    _seed_representative_data(factory, contexts)
    monkeypatch.setattr(
        "app.services.transaction_service.get_settings",
        lambda: Settings(
            telegram_bot_token="bot-token",
            telegram_chat_id="legacy-chat-must-not-be-used",
            _env_file=None,
        ),
    )

    resolved = {}
    for key, context in contexts.items():
        with _scoped(factory, context) as db:
            service = TransactionService(db)
            resolved[key] = service.settings.telegram_chat_id
            assert service.notification_service.settings.telegram_chat_id == f"chat-{key}"

    assert resolved == {"a": "chat-a", "b": "chat-b"}


def test_two_members_in_one_workspace_resolve_their_own_telegram_recipient(tenant_db):
    factory, contexts = tenant_db
    with factory() as db:
        member = db.get(User, contexts["b"].user_id)
        db.add(
            WorkspaceMembership(
                workspace_id=contexts["a"].workspace_id,
                user_id=member.id,
                role="member",
                is_default=False,
            )
        )
        db.add_all(
            [
                TelegramIdentity(
                    workspace_id=contexts["a"].workspace_id,
                    user_id=contexts["a"].user_id,
                    telegram_user_id="owner-tg",
                    chat_id="owner-chat",
                ),
                TelegramIdentity(
                    workspace_id=contexts["a"].workspace_id,
                    user_id=contexts["b"].user_id,
                    telegram_user_id="member-tg",
                    chat_id="member-chat",
                ),
            ]
        )
        db.commit()
        base = Settings(telegram_chat_id="legacy-chat", _env_file=None)
        owner = job_tenancy.telegram_settings_for_workspace(
            db,
            contexts["a"].workspace_id,
            base,
            user_id=contexts["a"].user_id,
        )
        member_settings = job_tenancy.telegram_settings_for_workspace(
            db,
            contexts["a"].workspace_id,
            base,
            user_id=contexts["b"].user_id,
        )
        ambiguous = job_tenancy.telegram_settings_for_workspace(
            db,
            contexts["a"].workspace_id,
            base,
        )

    assert owner.telegram_chat_id == "owner-chat"
    assert member_settings.telegram_chat_id == "member-chat"
    assert ambiguous.telegram_chat_id == ""


def test_transaction_lists_and_gets_do_not_leak_other_workspace(tenant_db):
    factory, contexts = tenant_db
    values = _seed_representative_data(factory, contexts)
    headers = {"Authorization": "Bearer token-a"}
    listed = TestClient(app).get("/transactions", headers=headers)
    guessed = TestClient(app).get(f"/transactions/{values['b']['transaction']}", headers=headers)
    assert listed.status_code == 200
    assert {value["id"] for value in listed.json()} == {values["a"]["transaction"]}
    assert guessed.status_code == 404


def test_idor_patch_and_delete_cannot_mutate_other_workspace(tenant_db):
    factory, contexts = tenant_db
    values = _seed_representative_data(factory, contexts)
    headers = {"Authorization": "Bearer token-a"}
    patched = TestClient(app).patch(
        f"/household/items/{values['b']['item']}",
        headers=headers,
        json={"name": "Stolen"},
    )
    deleted = TestClient(app).delete(
        f"/household/locations/{values['b']['location']}", headers=headers
    )
    assert patched.status_code == 404
    assert deleted.status_code == 404
    with _scoped(factory, contexts["b"]) as db:
        assert db.get(HouseholdItem, values["b"]["item"]).name == "Detergent b"
        assert db.get(SavedLocation, values["b"]["location"]) is not None


def test_splitwise_oauth_state_cannot_cross_workspaces(tenant_db):
    factory, contexts = tenant_db
    with factory() as db:
        create_oauth_state(
            db,
            provider="splitwise",
            workspace_id=contexts["b"].workspace_id,
            user_id=contexts["b"].user_id,
            payload="request-secret",
            raw_state="workspace-b-token",
        )
    response = TestClient(app).get(
        "/splitwise/oauth/callback",
        params={"oauth_token": "workspace-b-token", "oauth_verifier": "verifier"},
        headers={"Authorization": "Bearer token-a"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Splitwise OAuth state is invalid"
    with factory() as db:
        assert db.scalar(select(OAuthState)).used_at is None


def test_receipt_promotion_and_preferences_are_isolated(tenant_db):
    factory, contexts = tenant_db
    values = _seed_representative_data(factory, contexts)
    headers = {"Authorization": "Bearer token-a"}
    receipt = TestClient(app).post(
        f"/replenishment/receipts/{values['b']['receipt']}/ignore", headers=headers
    )
    promotion = TestClient(app).get(f"/api/promotions/{values['b']['promotion']}", headers=headers)
    settings = TestClient(app).get("/api/promotions/settings", headers=headers)
    assert receipt.status_code == 404
    assert promotion.status_code == 404
    assert settings.status_code == 200
    assert settings.json()["minimum_score"] == 20


def test_gmail_receipt_ingestion_deduplication_is_workspace_scoped(tenant_db):
    factory, contexts = tenant_db
    receipt_ids = []
    for context in contexts.values():
        with _scoped(factory, context) as db:
            receipt = ReceiptIngestionService(db).ingest_text(
                source="gmail",
                source_external_id="same-gmail-message-id",
                text="Receipt\nMilk 2.99\nTotal 2.99",
            )
            receipt_ids.append(receipt.id)
    assert receipt_ids[0] != receipt_ids[1]


def test_gmail_credentials_are_bound_to_the_workspace(tenant_db, monkeypatch):
    factory, contexts = tenant_db
    with _scoped(factory, contexts["b"]) as db:
        db.add(
            GmailAccount(
                user_id=contexts["b"].user_id,
                google_user_id="b@gmail.test",
                refresh_token_encrypted="encrypted-b",
            )
        )
        db.commit()
    monkeypatch.setattr(job_tenancy, "decrypt_secret", lambda _value: "token-b")
    base = Settings(gmail_refresh_token="legacy-default", _env_file=None)

    with _scoped(factory, contexts["a"]) as db:
        settings_a = job_tenancy.gmail_settings_for_session(db, base)
    with _scoped(factory, contexts["b"]) as db:
        settings_b = job_tenancy.gmail_settings_for_session(db, base)

    assert settings_a.gmail_refresh_token == ""
    assert settings_b.gmail_refresh_token == "token-b"
    assert settings_b.gmail_user_id == "b@gmail.test"


def test_trusted_webhooks_resolve_stored_tenant_not_payload_workspace(tenant_db):
    factory, contexts = tenant_db
    _seed_representative_data(factory, contexts)
    with factory() as db:
        event = plaid_routes._create_plaid_webhook_event(
            db,
            webhook_type="TRANSACTIONS",
            webhook_code="SYNC_UPDATES_AVAILABLE",
            plaid_item_id="plaid-b",
            payload_hash="hash",
        )
        assert event.workspace_id == contexts["b"].workspace_id
        assert event.item_id is None

        identity = telegram_routes._resolve_telegram_tenant(
            {
                "message": {
                    "from": {"id": "telegram-b"},
                    "chat": {"id": "chat-b"},
                }
            },
            db,
        )
        assert identity.workspace_id == contexts["b"].workspace_id
        assert db.info["workspace_id"] == contexts["b"].workspace_id


def test_workspace_job_scope_does_not_process_other_workspace(tenant_db):
    factory, contexts = tenant_db
    _seed_representative_data(factory, contexts)
    with _scoped(factory, contexts["a"]) as db:
        names = set(db.scalars(select(HouseholdItem.name)))
    assert names == {"Detergent a"}


def test_background_job_failure_in_one_workspace_does_not_block_another(tenant_db, monkeypatch):
    factory, contexts = tenant_db
    _seed_representative_data(factory, contexts)
    visited = []

    class FakeWeeklyService:
        def __init__(self, db):
            self.db = db

        def run(self):
            workspace_id = self.db.info["workspace_id"]
            visited.append(workspace_id)
            if workspace_id == contexts["a"].workspace_id:
                raise RuntimeError("workspace-a-failed")
            return SimpleNamespace(run_key="test", status="completed")

    monkeypatch.setattr(weekly_replenishment, "SessionLocal", factory)
    monkeypatch.setattr(weekly_replenishment, "WeeklyReplenishmentService", FakeWeeklyService)
    with pytest.raises(RuntimeError, match="failed_for_1_workspace"):
        weekly_replenishment.main()

    assert visited == [contexts["a"].workspace_id, contexts["b"].workspace_id]

import logging
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db as app_db
from app.api import plaid_routes
from app.config import Settings
from app.db import Base, get_db
from app.main import app
from app.models import PlaidItem, PlaidWebhookEvent
from app.services.plaid_service import PlaidRequestError, PlaidWebhookVerificationError


def test_link_token_returns_bad_gateway_for_plaid_request_errors(monkeypatch):
    class FailingPlaidService:
        def create_link_token(self, client_user_id):
            raise PlaidRequestError("Plaid request failed")

    monkeypatch.setattr(plaid_routes, "PlaidService", FailingPlaidService)

    response = TestClient(app).post("/plaid/link-token")

    assert response.status_code == 502
    assert response.json()["detail"] == "Plaid request failed"


def test_link_token_accepts_plaid_datetime_expiration(monkeypatch):
    class SuccessfulPlaidService:
        def create_link_token(self, client_user_id):
            return {
                "link_token": "link-sandbox",
                "expiration": datetime(2026, 5, 22, tzinfo=UTC),
                "request_id": "request-id",
            }

    monkeypatch.setattr(plaid_routes, "PlaidService", SuccessfulPlaidService)

    response = TestClient(app).post("/plaid/link-token")

    assert response.status_code == 200
    assert response.json()["expiration"] == "2026-05-22T00:00:00Z"


def test_plaid_webhook_ignores_unrelated_webhook_types():
    app.dependency_overrides[get_db] = lambda: object()

    try:
        response = TestClient(app).post(
            "/plaid/webhook",
            json={"webhook_type": "ITEM", "webhook_code": "ERROR", "item_id": "item-1"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["message"] == "Webhook ignored: ITEM/ERROR"


def test_plaid_webhook_verification_disabled_allows_webhook(monkeypatch):
    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(plaid_verify_webhooks=False),
    )

    class RaisingPlaidService:
        def __init__(self, settings=None):
            raise AssertionError("PlaidService should not be used when verification is disabled")

    monkeypatch.setattr(plaid_routes, "PlaidService", RaisingPlaidService)
    app.dependency_overrides[get_db] = lambda: object()

    try:
        response = TestClient(app).post(
            "/plaid/webhook",
            json={"webhook_type": "ITEM", "webhook_code": "ERROR", "item_id": "item-1"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["message"] == "Webhook ignored: ITEM/ERROR"


def test_plaid_webhook_verification_enabled_rejects_missing_header(monkeypatch):
    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(
            plaid_verify_webhooks=True,
            plaid_client_id="client-id",
            plaid_secret="secret",
        ),
    )
    app.dependency_overrides[get_db] = lambda: object()

    try:
        response = TestClient(app).post(
            "/plaid/webhook",
            json={"webhook_type": "ITEM", "webhook_code": "ERROR", "item_id": "item-1"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing Plaid webhook verification"


def test_plaid_webhook_verification_enabled_rejects_invalid_header(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(
            plaid_verify_webhooks=True,
            plaid_client_id="client-id",
            plaid_secret="secret",
        ),
    )

    class FailingPlaidService:
        def __init__(self, settings=None):
            pass

        def verify_webhook_signature(self, *, raw_body, verification_header):
            raise PlaidWebhookVerificationError("invalid_signature")

    monkeypatch.setattr(plaid_routes, "PlaidService", FailingPlaidService)
    app.dependency_overrides[get_db] = lambda: object()

    try:
        response = TestClient(app).post(
            "/plaid/webhook",
            headers={"Plaid-Verification": "bad-jwt"},
            json={"webhook_type": "ITEM", "webhook_code": "ERROR", "item_id": "item-1"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid Plaid webhook verification"
    assert any(
        getattr(record, "event", None) == "plaid_webhook_verification_failed"
        for record in caplog.records
    )


def test_plaid_webhook_verification_enabled_accepts_valid_header(monkeypatch):
    calls = []
    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(
            plaid_verify_webhooks=True,
            plaid_client_id="client-id",
            plaid_secret="secret",
        ),
    )

    class SuccessfulPlaidService:
        def __init__(self, settings=None):
            pass

        def verify_webhook_signature(self, *, raw_body, verification_header):
            calls.append((raw_body, verification_header))

    monkeypatch.setattr(plaid_routes, "PlaidService", SuccessfulPlaidService)
    app.dependency_overrides[get_db] = lambda: object()

    try:
        response = TestClient(app).post(
            "/plaid/webhook",
            headers={"Plaid-Verification": "valid-jwt"},
            json={"webhook_type": "ITEM", "webhook_code": "ERROR", "item_id": "item-1"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["message"] == "Webhook ignored: ITEM/ERROR"
    assert calls
    assert calls[0][1] == "valid-jwt"


def test_plaid_webhook_verifies_before_ignoring_unrelated_webhook(monkeypatch):
    calls = []
    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(
            plaid_verify_webhooks=True,
            plaid_client_id="client-id",
            plaid_secret="secret",
        ),
    )

    class SuccessfulPlaidService:
        def __init__(self, settings=None):
            pass

        def verify_webhook_signature(self, *, raw_body, verification_header):
            calls.append(verification_header)

    monkeypatch.setattr(plaid_routes, "PlaidService", SuccessfulPlaidService)
    app.dependency_overrides[get_db] = lambda: object()

    try:
        response = TestClient(app).post(
            "/plaid/webhook",
            headers={"Plaid-Verification": "valid-jwt"},
            json={"webhook_type": "ITEM", "webhook_code": "ERROR", "item_id": "item-1"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["message"] == "Webhook ignored: ITEM/ERROR"
    assert calls == ["valid-jwt"]


def test_plaid_webhook_handles_sync_updates_available(monkeypatch):
    item = PlaidItem(id=123, item_id="item-1", access_token_encrypted="encrypted")
    synced = []

    class FakeResult:
        def scalar_one_or_none(self):
            return item

    class FakeDb:
        def execute(self, _query):
            return FakeResult()

    monkeypatch.setattr(
        plaid_routes,
        "_sync_item_by_db_id",
        lambda item_id, event_id=None: synced.append(item_id),
    )
    app.dependency_overrides[get_db] = lambda: FakeDb()

    try:
        response = TestClient(app).post(
            "/plaid/webhook",
            json={
                "webhook_type": "TRANSACTIONS",
                "webhook_code": "SYNC_UPDATES_AVAILABLE",
                "item_id": "item-1",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["message"] == "Queued transactions sync"
    assert synced == [123]


def test_plaid_webhook_background_sync_reuses_transaction_service(monkeypatch, caplog):
    item = PlaidItem(id=123, item_id="item-1", access_token_encrypted="encrypted")
    calls = []

    class FakeTransactionService:
        def __init__(self, db):
            self.db = db

        def sync_item(self, sync_item):
            calls.append(sync_item.id)
            return {"added": 1, "modified": 2, "removed": 3}

    class FakeDb:
        def get(self, model, item_id):
            assert model is PlaidItem
            assert item_id == 123
            return item

        def close(self):
            pass

    monkeypatch.setattr(app_db, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(plaid_routes, "TransactionService", FakeTransactionService)

    with caplog.at_level(logging.INFO):
        plaid_routes._sync_item_by_db_id(123)

    assert calls == [123]
    assert any(
        getattr(record, "event", None) == "plaid_webhook_sync_started"
        for record in caplog.records
    )
    completed = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "plaid_webhook_sync_completed"
    ]
    assert len(completed) == 1
    assert completed[0].log_metadata["plaid_item_db_id"] == 123
    assert completed[0].log_metadata["added"] == 1
    assert completed[0].log_metadata["modified"] == 2
    assert completed[0].log_metadata["removed"] == 3


def test_plaid_webhook_accepts_missing_or_unknown_item_id():
    class EmptyResult:
        def scalar_one_or_none(self):
            return None

    class FakeDb:
        def execute(self, _query):
            return EmptyResult()

    app.dependency_overrides[get_db] = lambda: FakeDb()

    try:
        missing = TestClient(app).post(
            "/plaid/webhook",
            json={"webhook_type": "TRANSACTIONS", "webhook_code": "SYNC_UPDATES_AVAILABLE"},
        )
        unknown = TestClient(app).post(
            "/plaid/webhook",
            json={
                "webhook_type": "TRANSACTIONS",
                "webhook_code": "SYNC_UPDATES_AVAILABLE",
                "item_id": "unknown-item",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert missing.status_code == 200
    assert missing.json()["message"] == "Webhook accepted, but item_id is missing."
    assert unknown.status_code == 200
    assert unknown.json()["message"] == "Webhook accepted, but item is not linked in this app."


def test_plaid_webhook_event_row_created_for_ignored_webhook(tmp_path):
    db, SessionLocal = _plaid_route_test_db(tmp_path)
    db.close()
    app.dependency_overrides[get_db] = _override_db(SessionLocal)

    try:
        response = TestClient(app).post(
            "/plaid/webhook",
            json={
                "webhook_type": "ITEM",
                "webhook_code": "WEBHOOK_UPDATE_ACKNOWLEDGED",
                "item_id": "item-1",
                "access_token": "must-not-store",
            },
        )
        db = SessionLocal()
        event = db.query(PlaidWebhookEvent).one()
    finally:
        app.dependency_overrides.clear()
        db.close()

    assert response.status_code == 200
    assert event.webhook_type == "ITEM"
    assert event.webhook_code == "WEBHOOK_UPDATE_ACKNOWLEDGED"
    assert event.plaid_item_id == "item-1"
    assert event.processing_status == "ignored"
    assert event.processed_at is not None
    assert event.payload_hash
    assert "must-not-store" not in (event.payload_hash or "")
    assert event.error_message is None


def test_plaid_webhook_event_queued_then_processed(monkeypatch, tmp_path):
    db, SessionLocal = _plaid_route_test_db(tmp_path)
    item = PlaidItem(
        item_id="item-1",
        access_token_encrypted="encrypted",
        institution_name="Test Bank",
    )
    db.add(item)
    db.commit()
    item_db_id = item.id
    db.close()

    class FakeTransactionService:
        def __init__(self, db):
            self.db = db

        def sync_item(self, _item):
            return {"added": 1, "modified": 0, "removed": 0}

    monkeypatch.setattr(app_db, "SessionLocal", SessionLocal)
    monkeypatch.setattr(plaid_routes, "TransactionService", FakeTransactionService)
    app.dependency_overrides[get_db] = _override_db(SessionLocal)

    try:
        response = TestClient(app).post(
            "/plaid/webhook",
            json={
                "webhook_type": "TRANSACTIONS",
                "webhook_code": "SYNC_UPDATES_AVAILABLE",
                "item_id": "item-1",
            },
        )
        db = SessionLocal()
        event = db.query(PlaidWebhookEvent).one()
    finally:
        app.dependency_overrides.clear()
        db.close()

    assert response.status_code == 200
    assert event.item_id == item_db_id
    assert event.processing_status == "processed"
    assert event.sync_started_at is not None
    assert event.sync_completed_at is not None
    assert event.processed_at is not None
    assert event.error_message is None


def test_plaid_webhook_event_failed_sync(monkeypatch, tmp_path):
    db, SessionLocal = _plaid_route_test_db(tmp_path)
    db.add(
        PlaidItem(
            item_id="item-1",
            access_token_encrypted="encrypted",
            institution_name="Test Bank",
        )
    )
    db.commit()
    db.close()

    class FailingTransactionService:
        def __init__(self, db):
            self.db = db

        def sync_item(self, _item):
            raise RuntimeError("boom")

    monkeypatch.setattr(app_db, "SessionLocal", SessionLocal)
    monkeypatch.setattr(plaid_routes, "TransactionService", FailingTransactionService)
    app.dependency_overrides[get_db] = _override_db(SessionLocal)

    try:
        response = TestClient(app).post(
            "/plaid/webhook",
            json={
                "webhook_type": "TRANSACTIONS",
                "webhook_code": "SYNC_UPDATES_AVAILABLE",
                "item_id": "item-1",
            },
        )
        db = SessionLocal()
        event = db.query(PlaidWebhookEvent).one()
    finally:
        app.dependency_overrides.clear()
        db.close()

    assert response.status_code == 200
    assert event.processing_status == "failed"
    assert event.error_message == "RuntimeError"
    assert event.processed_at is not None


def _plaid_route_test_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'plaid-routes.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal(), SessionLocal


def _override_db(SessionLocal):
    def override():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    return override

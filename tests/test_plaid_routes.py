from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api import plaid_routes
from app.db import get_db
from app.main import app
from app.models import PlaidItem
from app.services.plaid_service import PlaidRequestError


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


def test_plaid_webhook_handles_sync_updates_available(monkeypatch):
    item = PlaidItem(id=123, item_id="item-1", access_token_encrypted="encrypted")
    synced = []

    class FakeResult:
        def scalar_one_or_none(self):
            return item

    class FakeDb:
        def execute(self, _query):
            return FakeResult()

    monkeypatch.setattr(plaid_routes, "_sync_item_by_db_id", lambda item_id: synced.append(item_id))
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

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api import plaid_routes
from app.main import app
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

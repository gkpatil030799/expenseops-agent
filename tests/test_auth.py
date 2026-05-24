from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


def test_health_route_is_public():
    response = TestClient(app).get("/health")

    assert response.status_code == 200


def test_dashboard_api_requires_auth_in_production(monkeypatch):
    import app.auth as auth

    monkeypatch.setattr(auth, "_auth_not_required", lambda _settings: False)
    monkeypatch.setattr(auth, "_is_authorized", lambda request, _settings: False)

    response = TestClient(app).get("/transactions")

    assert response.status_code == 401


def test_dashboard_api_allows_basic_auth():
    import app.auth as auth

    settings = Settings(
        environment="production",
        dashboard_username="beta",
        dashboard_password="secret",
    )
    credentials = base64.b64encode(b"beta:secret").decode()
    request_headers = {"Authorization": f"Basic {credentials}"}

    assert auth._is_authorized(
        type("Request", (), {"headers": request_headers})(),
        settings,
    )


def test_dashboard_api_allows_bearer_auth():
    import app.auth as auth

    settings = Settings(environment="production", dashboard_api_token="token-123")

    assert auth._is_authorized(
        type("Request", (), {"headers": {"Authorization": "Bearer token-123"}})(),
        settings,
    )


def test_public_webhook_paths_are_not_auth_protected():
    import app.auth as auth

    for path in ["/telegram/webhook", "/plaid/webhook", "/health"]:
        request = type(
            "Request",
            (),
            {"method": "POST", "url": type("Url", (), {"path": path})()},
        )()
        assert auth._is_public_request(request)


def test_button_mode_routes_are_not_changed_by_auth_helpers():
    from app.services.telegram_service import build_button_mode_keyboard

    keyboard = build_button_mode_keyboard(12)

    assert keyboard["inline_keyboard"][0][0]["text"] == "Personal"
    assert keyboard["inline_keyboard"][0][1]["text"] == "Draft"
    assert keyboard["inline_keyboard"][1][0]["text"] == "Split"

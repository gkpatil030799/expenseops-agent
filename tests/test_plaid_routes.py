import base64
import hashlib
import logging
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db as app_db
from app.api import plaid_routes
from app.config import Settings
from app.db import Base, get_db
from app.main import app
from app.models import PlaidItem, PlaidWebhookEvent
from app.services.plaid_service import (
    PlaidRequestError,
    PlaidService,
    PlaidWebhookVerificationError,
)


@pytest.fixture(autouse=True)
def default_plaid_route_settings(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("ALLOW_UNVERIFIED_PLAID_WEBHOOKS_FOR_LOCAL_TEST", "false")
    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(
            plaid_env="sandbox",
            plaid_verify_webhooks=False,
            plaid_verify_webhooks_in_sandbox=False,
        ),
    )


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


def test_plaid_webhook_signature_verifies_generated_jwt_with_raw_body_hash():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from jose import jwt

    raw_body = b'{"webhook_type":"TRANSACTIONS","webhook_code":"SYNC_UPDATES_AVAILABLE"}'
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "EC",
        "crv": "P-256",
        "kid": "test-kid",
        "use": "sig",
        "alg": "ES256",
        "x": _base64url_uint(public_numbers.x),
        "y": _base64url_uint(public_numbers.y),
    }
    token = jwt.encode(
        {"request_body_sha256": hashlib.sha256(raw_body).hexdigest()},
        private_pem,
        algorithm="ES256",
        headers={"kid": "test-kid"},
    )
    service = object.__new__(PlaidService)
    service.settings = Settings(
        plaid_env="production",
        plaid_client_id="client-id",
        plaid_secret="secret",
    )
    service.get_webhook_verification_key = lambda key_id: {"key": jwk}

    service.verify_webhook_signature(raw_body=raw_body, verification_header=token)


def test_plaid_webhook_signature_rejects_raw_body_hash_mismatch():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from jose import jwt

    raw_body = b'{"webhook_type":"TRANSACTIONS"}'
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "EC",
        "crv": "P-256",
        "kid": "test-kid",
        "use": "sig",
        "alg": "ES256",
        "x": _base64url_uint(public_numbers.x),
        "y": _base64url_uint(public_numbers.y),
    }
    token = jwt.encode(
        {"request_body_sha256": hashlib.sha256(b"different-body").hexdigest()},
        private_pem,
        algorithm="ES256",
        headers={"kid": "test-kid"},
    )
    service = object.__new__(PlaidService)
    service.settings = Settings(
        plaid_env="production",
        plaid_client_id="client-id",
        plaid_secret="secret",
    )
    service.get_webhook_verification_key = lambda key_id: {"key": jwk}

    with pytest.raises(PlaidWebhookVerificationError) as exc:
        service.verify_webhook_signature(raw_body=raw_body, verification_header=token)

    assert exc.value.reason == "request_body_hash_mismatch"


def test_plaid_webhook_sandbox_verification_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PLAID_VERIFY_WEBHOOKS", raising=False)
    monkeypatch.delenv("PLAID_VERIFY_WEBHOOKS_IN_SANDBOX", raising=False)
    settings = Settings(plaid_env="sandbox", _env_file=None)

    assert settings.plaid_verify_webhooks_in_sandbox is False
    assert settings.plaid_webhook_verification_required is False


@pytest.mark.parametrize("value", ["true", "1", "yes", "on"])
def test_plaid_webhook_sandbox_verification_flag_accepts_true_values(
    monkeypatch,
    value,
):
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    monkeypatch.setenv("PLAID_VERIFY_WEBHOOKS_IN_SANDBOX", value)

    settings = Settings(_env_file=None)

    assert settings.plaid_verify_webhooks_in_sandbox is True
    assert settings.plaid_webhook_verification_required is True


def test_plaid_webhook_sandbox_verification_enabled_requires_header(monkeypatch):
    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(
            plaid_env="sandbox",
            plaid_verify_webhooks_in_sandbox=True,
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

    assert response.status_code == 403
    assert response.json()["detail"] == "Plaid webhook verification failed"


def test_plaid_webhook_sandbox_verification_enabled_accepts_valid_jwt(monkeypatch):
    raw_body = b'{"webhook_type":"ITEM","webhook_code":"ERROR","item_id":"item-1"}'
    token, jwk = _signed_webhook_jwt(raw_body)
    service_settings = []

    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(
            plaid_env="sandbox",
            plaid_verify_webhooks_in_sandbox=True,
            plaid_client_id="client-id",
            plaid_secret="secret",
        ),
    )

    class SandboxPlaidService(PlaidService):
        def __init__(self, settings=None):
            self.settings = settings
            service_settings.append(settings)

        def get_webhook_verification_key(self, key_id):
            assert key_id == "test-kid"
            return {"key": jwk}

    monkeypatch.setattr(plaid_routes, "PlaidService", SandboxPlaidService)
    app.dependency_overrides[get_db] = lambda: object()

    try:
        response = TestClient(app).post(
            "/plaid/webhook",
            headers={"Plaid-Verification": token, "Content-Type": "application/json"},
            content=raw_body,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["message"] == "Webhook ignored: ITEM/ERROR"
    assert service_settings
    assert service_settings[0].plaid_env == "sandbox"


def test_plaid_webhook_sandbox_verification_enabled_rejects_invalid_jwt(monkeypatch):
    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(
            plaid_env="sandbox",
            plaid_verify_webhooks_in_sandbox=True,
            plaid_client_id="client-id",
            plaid_secret="secret",
        ),
    )

    class FailingPlaidService:
        def __init__(self, settings=None):
            pass

        def verify_webhook_signature(self, *, raw_body, verification_header):
            raise PlaidWebhookVerificationError("jwt_signature_invalid")

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

    assert response.status_code == 403
    assert response.json()["detail"] == "Plaid webhook verification failed"


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
        lambda: Settings(
            plaid_env="sandbox",
            plaid_verify_webhooks=False,
            plaid_verify_webhooks_in_sandbox=False,
        ),
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

    assert response.status_code == 403
    assert response.json()["detail"] == "Plaid webhook verification failed"


def test_plaid_webhook_verification_enabled_rejects_invalid_header(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    sandbox_events = []
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
            raise PlaidWebhookVerificationError("jwt_signature_invalid")

    monkeypatch.setattr(plaid_routes, "PlaidService", FailingPlaidService)
    monkeypatch.setattr(
        plaid_routes,
        "maybe_log_sandbox_webhook_verification_event",
        lambda **kwargs: sandbox_events.append(kwargs),
    )
    app.dependency_overrides[get_db] = lambda: object()

    try:
        response = TestClient(app).post(
            "/plaid/webhook",
            headers={"Plaid-Verification": "bad-jwt"},
            json={"webhook_type": "ITEM", "webhook_code": "ERROR", "item_id": "item-1"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["detail"] == "Plaid webhook verification failed"
    assert any(
        getattr(record, "event", None) == "plaid_webhook_verification_failed"
        and record.log_metadata["reason"] == "jwt_signature_invalid"
        for record in caplog.records
    )
    assert [event["event_type"] for event in sandbox_events] == [
        "plaid_webhook_verification_started",
        "plaid_webhook_verification_failed",
    ]
    assert sandbox_events[-1]["payload"]["reason"] == "jwt_signature_invalid"


def test_plaid_webhook_production_requires_verification_even_when_flag_false(monkeypatch):
    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(
            plaid_env="production",
            plaid_verify_webhooks=False,
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

    assert response.status_code == 403
    assert response.json()["detail"] == "Plaid webhook verification failed"


def test_plaid_webhook_lowercase_verification_header_is_accepted(monkeypatch):
    calls = []
    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(
            plaid_env="production",
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
            headers={"plaid-verification": "valid-jwt"},
            json={"webhook_type": "ITEM", "webhook_code": "ERROR", "item_id": "item-1"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert calls == ["valid-jwt"]


def test_plaid_webhook_key_fetch_failure_returns_403_with_sanitized_reason(
    monkeypatch,
    caplog,
):
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(
            plaid_env="production",
            plaid_client_id="client-id",
            plaid_secret="secret",
        ),
    )

    class FailingPlaidService:
        def __init__(self, settings=None):
            pass

        def verify_webhook_signature(self, *, raw_body, verification_header):
            raise PlaidRequestError("network secret details should not be logged")

    monkeypatch.setattr(plaid_routes, "PlaidService", FailingPlaidService)
    app.dependency_overrides[get_db] = lambda: object()

    try:
        response = TestClient(app).post(
            "/plaid/webhook",
            headers={"Plaid-Verification": "jwt"},
            json={"webhook_type": "ITEM", "webhook_code": "ERROR", "item_id": "item-1"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert any(
        getattr(record, "event", None) == "plaid_webhook_verification_failed"
        and record.log_metadata["reason"] == "webhook_key_fetch_failed"
        and "network secret details" not in str(record.log_metadata)
        for record in caplog.records
    )


def test_plaid_webhook_verification_failure_is_audited(tmp_path, monkeypatch):
    db, SessionLocal = _plaid_route_test_db(tmp_path)
    db.close()
    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(
            plaid_env="production",
            plaid_client_id="client-id",
            plaid_secret="secret",
        ),
    )
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

    assert response.status_code == 403
    assert event.processing_status == "verification_failed"
    assert event.error_message == "missing_plaid_verification_header"
    assert event.webhook_type == "TRANSACTIONS"
    assert event.webhook_code == "SYNC_UPDATES_AVAILABLE"
    assert event.plaid_item_id == "item-1"


def test_plaid_webhook_verification_enabled_accepts_valid_header(monkeypatch, caplog):
    calls = []
    sandbox_events = []
    caplog.set_level(logging.INFO)
    raw_body = b'{"webhook_type":"ITEM","webhook_code":"ERROR","item_id":"item-1"}'
    token, _jwk = _signed_webhook_jwt(raw_body)
    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(
            plaid_verify_webhooks=True,
            plaid_client_id="client-id",
            plaid_secret="super-secret-value",
        ),
    )

    class SuccessfulPlaidService:
        def __init__(self, settings=None):
            pass

        def verify_webhook_signature(self, *, raw_body, verification_header):
            calls.append((raw_body, verification_header))

    monkeypatch.setattr(plaid_routes, "PlaidService", SuccessfulPlaidService)
    monkeypatch.setattr(
        plaid_routes,
        "maybe_log_sandbox_webhook_verification_event",
        lambda **kwargs: sandbox_events.append(kwargs),
    )
    app.dependency_overrides[get_db] = lambda: object()

    try:
        response = TestClient(app).post(
            "/plaid/webhook",
            headers={"Plaid-Verification": token, "Content-Type": "application/json"},
            content=raw_body,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["message"] == "Webhook ignored: ITEM/ERROR"
    assert calls
    assert calls[0][1] == token
    assert [event["event_type"] for event in sandbox_events] == [
        "plaid_webhook_verification_started",
        "plaid_webhook_verification_succeeded",
    ]
    assert sandbox_events[-1]["payload"] == {
        "plaid_env": "sandbox",
        "verification_required": True,
        "header_present": True,
        "kid_present": True,
    }
    serialized_events = str(sandbox_events)
    serialized_logs = "\n".join(
        str(getattr(record, "log_metadata", "")) for record in caplog.records
    )
    assert token not in serialized_events
    assert token not in serialized_logs
    assert "super-secret-value" not in serialized_events
    assert "super-secret-value" not in serialized_logs


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


def test_plaid_webhook_local_only_bypass_allows_sync_when_app_env_local(
    monkeypatch,
    caplog,
):
    item = PlaidItem(id=123, item_id="item-1", access_token_encrypted="encrypted")
    synced = []
    sandbox_events = []
    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(
            plaid_env="production",
            environment="local",
            allow_unverified_plaid_webhooks_for_local_test=True,
            plaid_client_id="client-id",
            plaid_secret="secret",
        ),
    )

    class FailingPlaidService:
        def __init__(self, settings=None):
            pass

        def verify_webhook_signature(self, *, raw_body, verification_header):
            raise PlaidWebhookVerificationError("jwt_signature_invalid")

    class FakeResult:
        def scalar_one_or_none(self):
            return item

    class FakeDb:
        def execute(self, _query):
            return FakeResult()

    monkeypatch.setattr(plaid_routes, "PlaidService", FailingPlaidService)
    monkeypatch.setattr(
        plaid_routes,
        "maybe_log_sandbox_webhook_verification_event",
        lambda **kwargs: sandbox_events.append(kwargs),
    )
    monkeypatch.setattr(
        plaid_routes,
        "_sync_item_by_db_id",
        lambda item_id, event_id=None: synced.append(item_id),
    )
    app.dependency_overrides[get_db] = lambda: FakeDb()

    try:
        response = TestClient(app).post(
            "/plaid/webhook",
            headers={"Plaid-Verification": "bad-jwt"},
            json={
                "webhook_type": "TRANSACTIONS",
                "webhook_code": "SYNC_UPDATES_AVAILABLE",
                "item_id": "item-1",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert synced == [123]
    assert any(
        getattr(record, "event", None)
        == "plaid_webhook_verification_bypassed_for_local_test"
        for record in caplog.records
    )
    assert [event["event_type"] for event in sandbox_events] == [
        "plaid_webhook_verification_started",
        "plaid_webhook_verification_bypassed_for_local_test",
    ]
    assert sandbox_events[-1]["payload"]["reason"] == "jwt_signature_invalid"


def test_plaid_webhook_local_bypass_denied_when_app_env_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(
            plaid_env="production",
            environment="local",
            allow_unverified_plaid_webhooks_for_local_test=True,
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

    assert response.status_code == 403


def test_plaid_webhook_verification_failure_does_not_run_sync(monkeypatch):
    synced = []
    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(
            plaid_env="production",
            plaid_client_id="client-id",
            plaid_secret="secret",
        ),
    )
    monkeypatch.setattr(
        plaid_routes,
        "_sync_item_by_db_id",
        lambda item_id, event_id=None: synced.append(item_id),
    )
    app.dependency_overrides[get_db] = lambda: object()

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

    assert response.status_code == 403
    assert synced == []


def test_plaid_webhook_verified_sync_uses_raw_body(monkeypatch):
    item = PlaidItem(id=123, item_id="item-1", access_token_encrypted="encrypted")
    body = (
        b'{ "webhook_type":"TRANSACTIONS", '
        b'"webhook_code":"SYNC_UPDATES_AVAILABLE", "item_id":"item-1" }'
    )
    verified_raw_bodies = []
    synced = []
    monkeypatch.setattr(
        plaid_routes,
        "get_settings",
        lambda: Settings(
            plaid_env="production",
            plaid_client_id="client-id",
            plaid_secret="secret",
        ),
    )

    class SuccessfulPlaidService:
        def __init__(self, settings=None):
            pass

        def verify_webhook_signature(self, *, raw_body, verification_header):
            verified_raw_bodies.append(raw_body)

    class FakeResult:
        def scalar_one_or_none(self):
            return item

    class FakeDb:
        def execute(self, _query):
            return FakeResult()

    monkeypatch.setattr(plaid_routes, "PlaidService", SuccessfulPlaidService)
    monkeypatch.setattr(
        plaid_routes,
        "_sync_item_by_db_id",
        lambda item_id, event_id=None: synced.append(item_id),
    )
    app.dependency_overrides[get_db] = lambda: FakeDb()

    try:
        response = TestClient(app).post(
            "/plaid/webhook",
            headers={"Plaid-Verification": "valid-jwt", "Content-Type": "application/json"},
            content=body,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert verified_raw_bodies == [body]
    assert synced == [123]


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


def _signed_webhook_jwt(raw_body: bytes):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from jose import jwt

    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "EC",
        "crv": "P-256",
        "kid": "test-kid",
        "use": "sig",
        "alg": "ES256",
        "x": _base64url_uint(public_numbers.x),
        "y": _base64url_uint(public_numbers.y),
    }
    token = jwt.encode(
        {"request_body_sha256": hashlib.sha256(raw_body).hexdigest()},
        private_pem,
        algorithm="ES256",
        headers={"kid": "test-kid"},
    )
    return token, jwk


def _base64url_uint(value: int) -> str:
    return base64.urlsafe_b64encode(value.to_bytes(32, "big")).rstrip(b"=").decode("ascii")

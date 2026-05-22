import pytest
import requests

from app.config import Settings
from app.services.splitwise_service import SplitwiseAPIError, SplitwiseService


class FakeResponse:
    def __init__(self, text, status_code=200, json_data=None):
        self.text = text
        self.status_code = status_code
        self.json_data = json_data

    def json(self):
        if self.json_data is not None:
            return self.json_data
        raise ValueError


def test_get_oauth_authorize_url_uses_consumer_credentials(monkeypatch):
    settings = Settings(
        splitwise_consumer_key="consumer-key",
        splitwise_consumer_secret="consumer-secret",
    )
    service = SplitwiseService(settings)
    captured = {}

    def fake_post(url, *, auth, timeout):
        captured["url"] = url
        captured["auth"] = auth
        captured["timeout"] = timeout
        return FakeResponse("oauth_token=request-token&oauth_token_secret=request-secret")

    monkeypatch.setattr("app.services.splitwise_service.requests.post", fake_post)

    data = service.get_oauth_authorize_url()

    assert captured["url"] == "https://secure.splitwise.com/oauth/request_token"
    assert data == {
        "authorize_url": "https://secure.splitwise.com/authorize?oauth_token=request-token",
        "oauth_token": "request-token",
        "oauth_token_secret": "request-secret",
    }


def test_get_oauth_authorize_url_wraps_network_errors(monkeypatch):
    settings = Settings(
        splitwise_consumer_key="consumer-key",
        splitwise_consumer_secret="consumer-secret",
    )
    service = SplitwiseService(settings)

    def fake_post(url, *, auth, timeout):
        raise requests.ConnectionError("dns failed")

    monkeypatch.setattr("app.services.splitwise_service.requests.post", fake_post)

    with pytest.raises(SplitwiseAPIError, match="Splitwise request failed"):
        service.get_oauth_authorize_url()


def test_exchange_oauth_verifier_returns_access_token(monkeypatch):
    settings = Settings(
        splitwise_consumer_key="consumer-key",
        splitwise_consumer_secret="consumer-secret",
    )
    service = SplitwiseService(settings)
    captured = {}

    def fake_post(url, *, auth, timeout):
        captured["url"] = url
        return FakeResponse("oauth_token=access-token&oauth_token_secret=access-secret")

    monkeypatch.setattr("app.services.splitwise_service.requests.post", fake_post)

    data = service.exchange_oauth_verifier(
        oauth_token="request-token",
        oauth_token_secret="request-secret",
        oauth_verifier="verifier",
    )

    assert captured["url"] == "https://secure.splitwise.com/oauth/access_token"
    assert data == {"oauth_token": "access-token", "oauth_token_secret": "access-secret"}


def test_splitwise_request_with_consumer_only_prompts_oauth_flow():
    settings = Settings(
        splitwise_api_key="",
        splitwise_access_token="",
        splitwise_consumer_key="consumer-key",
        splitwise_consumer_secret="consumer-secret",
    )
    service = SplitwiseService(settings)

    with pytest.raises(SplitwiseAPIError, match="/splitwise/oauth/authorize"):
        service.get_current_user()


def test_splitwise_api_key_uses_bearer_header_and_skips_oauth(monkeypatch):
    settings = Settings(splitwise_api_key="api-key")
    service = SplitwiseService(settings)
    captured = {}

    def fake_request(method, url, *, params, json, headers, auth, timeout):
        captured["headers"] = headers
        captured["auth"] = auth
        return FakeResponse("", json_data={"user": {"id": 123}})

    monkeypatch.setattr("app.services.splitwise_service.requests.request", fake_request)

    assert service.get_current_user()["id"] == 123
    assert captured["headers"]["Authorization"] == "Bearer api-key"
    assert captured["auth"] is None

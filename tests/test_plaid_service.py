from app.config import Settings
from app.services.plaid_service import PlaidService


def test_plaid_client_builds_for_sandbox_environment():
    settings = Settings(
        plaid_client_id="client-id",
        plaid_secret="secret",
        plaid_env="sandbox",
    )

    service = PlaidService(settings)

    assert service.client is not None


def test_plaid_development_environment_uses_fallback_host():
    settings = Settings(
        plaid_client_id="client-id",
        plaid_secret="secret",
        plaid_env="development",
    )
    service = PlaidService(settings)

    assert service._environment_host(type("Plaid", (), {"Environment": object})()) == (
        "https://development.plaid.com"
    )


def test_create_link_token_uses_installed_plaid_transaction_model():
    settings = Settings(
        plaid_client_id="client-id",
        plaid_secret="secret",
        plaid_env="sandbox",
        plaid_country_codes=["US"],
        plaid_products=["transactions"],
        plaid_days_requested=45,
    )
    service = PlaidService(settings)
    captured = {}

    class FakeResponse:
        def to_dict(self):
            return {"link_token": "link-sandbox", "expiration": "2026-05-22T00:00:00Z"}

    class FakeClient:
        def link_token_create(self, request):
            captured["request"] = request
            return FakeResponse()

    service.client = FakeClient()

    data = service.create_link_token(client_user_id="user-1")

    assert data["link_token"] == "link-sandbox"
    assert captured["request"].transactions.days_requested == 45

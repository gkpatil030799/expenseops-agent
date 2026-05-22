from __future__ import annotations

from typing import Any

from app.config import Settings, get_settings


class PlaidConfigurationError(RuntimeError):
    pass


class PlaidRequestError(RuntimeError):
    pass


class PlaidService:
    """Thin wrapper around Plaid's generated Python client.

    Imports live inside this class so the rest of the app can be tested without
    needing Plaid credentials or network access.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if not self.settings.plaid_client_id or not self.settings.plaid_secret:
            raise PlaidConfigurationError("PLAID_CLIENT_ID and PLAID_SECRET are required")
        self.client = self._build_client()

    def _build_client(self):
        import certifi
        import plaid
        from plaid.api import plaid_api

        configuration = plaid.Configuration(
            host=self._environment_host(plaid),
            api_key={
                "clientId": self.settings.plaid_client_id,
                "secret": self.settings.plaid_secret,
            },
            ssl_ca_cert=certifi.where(),
        )
        api_client = plaid.ApiClient(configuration)
        return plaid_api.PlaidApi(api_client)

    def _environment_host(self, plaid_module) -> str:
        if self.settings.plaid_env == "sandbox":
            return plaid_module.Environment.Sandbox
        if self.settings.plaid_env == "production":
            return plaid_module.Environment.Production
        return getattr(
            plaid_module.Environment,
            "Development",
            "https://development.plaid.com",
        )

    def create_link_token(self, client_user_id: str = "gunjan") -> dict[str, Any]:
        from plaid.model.country_code import CountryCode
        from plaid.model.link_token_create_request import LinkTokenCreateRequest
        from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
        from plaid.model.link_token_transactions import LinkTokenTransactions
        from plaid.model.products import Products

        request_kwargs: dict[str, Any] = {
            "client_name": self.settings.app_name[:30],
            "language": "en",
            "country_codes": [CountryCode(code) for code in self.settings.plaid_country_codes],
            "products": [Products(product) for product in self.settings.plaid_products],
            "user": LinkTokenCreateRequestUser(client_user_id=client_user_id),
        }
        if "transactions" in self.settings.plaid_products:
            request_kwargs["transactions"] = LinkTokenTransactions(
                days_requested=self.settings.plaid_days_requested
            )
        if self.settings.plaid_webhook_url:
            request_kwargs["webhook"] = self.settings.plaid_webhook_url

        try:
            response = self.client.link_token_create(LinkTokenCreateRequest(**request_kwargs))
        except Exception as exc:
            raise PlaidRequestError(_plaid_error_message(exc)) from exc
        return response.to_dict()

    def exchange_public_token(self, public_token: str) -> dict[str, Any]:
        from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest

        try:
            response = self.client.item_public_token_exchange(
                ItemPublicTokenExchangeRequest(public_token=public_token)
            )
        except Exception as exc:
            raise PlaidRequestError(_plaid_error_message(exc)) from exc
        return response.to_dict()

    def transactions_sync(self, *, access_token: str, cursor: str | None) -> dict[str, Any]:
        from plaid.model.transactions_sync_request import TransactionsSyncRequest

        request_kwargs: dict[str, Any] = {"access_token": access_token, "count": 500}
        if cursor:
            request_kwargs["cursor"] = cursor
        try:
            response = self.client.transactions_sync(TransactionsSyncRequest(**request_kwargs))
        except Exception as exc:
            raise PlaidRequestError(_plaid_error_message(exc)) from exc
        return response.to_dict()

    def get_webhook_verification_key(self, key_id: str) -> dict[str, Any]:
        from plaid.model.webhook_verification_key_get_request import (
            WebhookVerificationKeyGetRequest,
        )

        try:
            response = self.client.webhook_verification_key_get(
                WebhookVerificationKeyGetRequest(key_id=key_id)
            )
        except Exception as exc:
            raise PlaidRequestError(_plaid_error_message(exc)) from exc
        return response.to_dict()


def _plaid_error_message(exc: Exception) -> str:
    return (
        "Plaid request failed. Confirm network access, PLAID_ENV, "
        f"PLAID_CLIENT_ID, and PLAID_SECRET. Details: {exc}"
    )

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from plaid.exceptions import ApiException
from plaid.model.custom_sandbox_transaction import CustomSandboxTransaction
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.item_webhook_update_request import ItemWebhookUpdateRequest
from plaid.model.products import Products
from plaid.model.sandbox_item_fire_webhook_request import SandboxItemFireWebhookRequest
from plaid.model.sandbox_public_token_create_request import SandboxPublicTokenCreateRequest
from plaid.model.sandbox_public_token_create_request_options import (
    SandboxPublicTokenCreateRequestOptions,
)
from plaid.model.sandbox_public_token_create_request_options_transactions import (
    SandboxPublicTokenCreateRequestOptionsTransactions,
)
from plaid.model.sandbox_transactions_create_request import SandboxTransactionsCreateRequest
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.webhook_type import WebhookType

from app.services.plaid_service import PlaidService

SANDBOX_INSTITUTION_ID = "ins_109508"
SANDBOX_USERNAME = "user_transactions_dynamic"
SANDBOX_PASSWORD = "pass_good"


class SandboxPlaidError(RuntimeError):
    def __init__(self, message: str, *, request_id: str | None = None):
        super().__init__(message)
        self.request_id = request_id


class PlaidSandboxService:
    def __init__(self, plaid_service: PlaidService | None = None):
        self.plaid_service = plaid_service or PlaidService()
        self.client = self.plaid_service.client

    def create_public_token(self, *, webhook_url: str | None = None) -> dict[str, Any]:
        try:
            options_kwargs: dict[str, Any] = {
                "override_username": SANDBOX_USERNAME,
                "override_password": SANDBOX_PASSWORD,
                "transactions": SandboxPublicTokenCreateRequestOptionsTransactions(
                    days_requested=30
                ),
            }
            if webhook_url:
                options_kwargs["webhook"] = webhook_url
            response = self.client.sandbox_public_token_create(
                SandboxPublicTokenCreateRequest(
                    institution_id=SANDBOX_INSTITUTION_ID,
                    initial_products=[Products("transactions")],
                    options=SandboxPublicTokenCreateRequestOptions(**options_kwargs),
                )
            )
            return response.to_dict()
        except ApiException as exc:
            raise _to_sandbox_error(exc, "Plaid sandbox public token create failed.") from exc

    def exchange_public_token(self, public_token: str) -> dict[str, Any]:
        try:
            response = self.client.item_public_token_exchange(
                ItemPublicTokenExchangeRequest(public_token=public_token)
            )
            return response.to_dict()
        except ApiException as exc:
            raise _to_sandbox_error(exc, "Plaid public token exchange failed.") from exc

    def transactions_sync(self, *, access_token: str, cursor: str | None) -> dict[str, Any]:
        try:
            request_kwargs: dict[str, Any] = {"access_token": access_token, "count": 500}
            if cursor:
                request_kwargs["cursor"] = cursor
            response = self.client.transactions_sync(TransactionsSyncRequest(**request_kwargs))
            return response.to_dict()
        except ApiException as exc:
            raise _to_sandbox_error(exc, "Plaid transactions sync failed.") from exc

    def get_item_webhook(self, *, access_token: str) -> str | None:
        try:
            response = self.client.item_get(ItemGetRequest(access_token=access_token))
            item = response.to_dict().get("item") or {}
            webhook = item.get("webhook")
            return str(webhook) if webhook else None
        except ApiException as exc:
            raise _to_sandbox_error(exc, "Plaid item get failed.") from exc

    def create_transaction(
        self,
        *,
        access_token: str,
        description: str,
        amount: Decimal,
        iso_currency_code: str,
        date_transacted: date,
        date_posted: date,
    ) -> dict[str, Any]:
        try:
            response = self.client.sandbox_transactions_create(
                SandboxTransactionsCreateRequest(
                    access_token=access_token,
                    transactions=[
                        CustomSandboxTransaction(
                            date_transacted=date_transacted,
                            date_posted=date_posted,
                            amount=float(amount),
                            description=description,
                            iso_currency_code=iso_currency_code,
                        )
                    ],
                )
            )
            return response.to_dict()
        except ApiException as exc:
            raise _to_sandbox_error(exc, "Plaid sandbox transaction create failed.") from exc

    def update_webhook(self, *, access_token: str, webhook_url: str | None) -> dict[str, Any]:
        try:
            response = self.client.item_webhook_update(
                ItemWebhookUpdateRequest(access_token=access_token, webhook=webhook_url)
            )
            return response.to_dict()
        except ApiException as exc:
            raise _to_sandbox_error(exc, "Plaid item webhook update failed.") from exc

    def fire_webhook(
        self,
        *,
        access_token: str,
        webhook_type: str,
        webhook_code: str,
    ) -> dict[str, Any]:
        try:
            response = self.client.sandbox_item_fire_webhook(
                SandboxItemFireWebhookRequest(
                    access_token=access_token,
                    webhook_type=WebhookType(webhook_type),
                    webhook_code=webhook_code,
                )
            )
            return response.to_dict()
        except ApiException as exc:
            raise _to_sandbox_error(exc, "Plaid sandbox webhook fire failed.") from exc


def _to_sandbox_error(exc: ApiException, fallback: str) -> SandboxPlaidError:
    request_id = None
    message = fallback
    try:
        import json

        body = json.loads(exc.body or "{}")
        request_id = body.get("request_id")
        message = body.get("error_message") or fallback
    except Exception:
        pass
    return SandboxPlaidError(message, request_id=request_id)

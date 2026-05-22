from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlencode

import requests
from requests_oauthlib import OAuth1

from app.config import Settings, get_settings
from app.services.agent_service import friend_display_name


class SplitwiseAPIError(RuntimeError):
    def __init__(self, message: str, response_data: dict[str, Any] | None = None):
        super().__init__(message)
        self.response_data = response_data or {}


class SplitwiseService:
    authorize_url = "https://secure.splitwise.com/authorize"
    oauth_request_token_url = "https://secure.splitwise.com/oauth/request_token"
    oauth_access_token_url = "https://secure.splitwise.com/oauth/access_token"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.base_url = self.settings.splitwise_base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.settings.splitwise_api_key:
            headers["Authorization"] = f"Bearer {self.settings.splitwise_api_key}"
        elif self.settings.splitwise_access_token:
            headers["Authorization"] = (
                f"{self.settings.splitwise_auth_scheme} {self.settings.splitwise_access_token}"
            )
        return headers

    def _auth(self) -> OAuth1 | None:
        if self.settings.splitwise_api_key or self.settings.splitwise_access_token:
            return None
        if not self.settings.has_splitwise_oauth1_consumer:
            raise SplitwiseAPIError(
                "Configure SPLITWISE_CONSUMER_KEY and SPLITWISE_CONSUMER_SECRET, "
                "or set SPLITWISE_API_KEY."
            )
        if not self.settings.uses_splitwise_oauth1:
            raise SplitwiseAPIError(
                "Splitwise OAuth 1.0 access token is not configured. Start the OAuth flow at "
                "/splitwise/oauth/authorize, then set SPLITWISE_OAUTH_TOKEN and "
                "SPLITWISE_OAUTH_TOKEN_SECRET from the callback response."
            )
        return OAuth1(
            client_key=self.settings.splitwise_consumer_key,
            client_secret=self.settings.splitwise_consumer_secret,
            resource_owner_key=self.settings.splitwise_oauth_token,
            resource_owner_secret=self.settings.splitwise_oauth_token_secret,
        )

    def get_oauth_authorize_url(self) -> dict[str, str]:
        if not self.settings.has_splitwise_oauth1_consumer:
            raise SplitwiseAPIError(
                "SPLITWISE_CONSUMER_KEY and SPLITWISE_CONSUMER_SECRET are required"
            )

        auth = OAuth1(
            client_key=self.settings.splitwise_consumer_key,
            client_secret=self.settings.splitwise_consumer_secret,
            callback_uri=self.settings.splitwise_oauth_callback_url or None,
        )
        try:
            response = requests.post(self.oauth_request_token_url, auth=auth, timeout=20.0)
        except requests.RequestException as exc:
            raise SplitwiseAPIError(_request_error_message(exc)) from exc
        if response.status_code >= 400:
            raise SplitwiseAPIError(
                f"Splitwise OAuth request-token failed: HTTP {response.status_code}",
                _safe_json(response),
            )

        credentials = _parse_oauth_credentials(response.text)
        oauth_token = credentials.get("oauth_token")
        oauth_token_secret = credentials.get("oauth_token_secret")
        if not oauth_token or not oauth_token_secret:
            raise SplitwiseAPIError("Splitwise did not return an OAuth request token")

        return {
            "authorize_url": f"{self.authorize_url}?{urlencode({'oauth_token': oauth_token})}",
            "oauth_token": oauth_token,
            "oauth_token_secret": oauth_token_secret,
        }

    def exchange_oauth_verifier(
        self, *, oauth_token: str, oauth_token_secret: str, oauth_verifier: str
    ) -> dict[str, str]:
        if not self.settings.has_splitwise_oauth1_consumer:
            raise SplitwiseAPIError(
                "SPLITWISE_CONSUMER_KEY and SPLITWISE_CONSUMER_SECRET are required"
            )

        auth = OAuth1(
            client_key=self.settings.splitwise_consumer_key,
            client_secret=self.settings.splitwise_consumer_secret,
            resource_owner_key=oauth_token,
            resource_owner_secret=oauth_token_secret,
            verifier=oauth_verifier,
        )
        try:
            response = requests.post(self.oauth_access_token_url, auth=auth, timeout=20.0)
        except requests.RequestException as exc:
            raise SplitwiseAPIError(_request_error_message(exc)) from exc
        if response.status_code >= 400:
            raise SplitwiseAPIError(
                f"Splitwise OAuth access-token failed: HTTP {response.status_code}",
                _safe_json(response),
            )

        credentials = _parse_oauth_credentials(response.text)
        access_token = credentials.get("oauth_token")
        access_token_secret = credentials.get("oauth_token_secret")
        if not access_token or not access_token_secret:
            raise SplitwiseAPIError("Splitwise did not return an OAuth access token")
        return {"oauth_token": access_token, "oauth_token_secret": access_token_secret}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            response = requests.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=self._headers(),
                auth=self._auth(),
                timeout=20.0,
            )
        except requests.RequestException as exc:
            raise SplitwiseAPIError(_request_error_message(exc)) from exc
        if response.status_code in {401, 403}:
            raise SplitwiseAPIError(
                f"Splitwise authentication/authorization failed: HTTP {response.status_code}",
                _safe_json(response),
            )
        if response.status_code >= 400:
            raise SplitwiseAPIError(
                f"Splitwise request failed: HTTP {response.status_code}", _safe_json(response)
            )
        return _safe_json(response)

    def get_current_user(self) -> dict[str, Any]:
        data = self._request("GET", "/get_current_user")
        return data.get("user", data)

    def get_friends(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/get_friends")
        return data.get("friends", [])

    def search_friends(self, query: str) -> list[dict[str, Any]]:
        query_l = query.strip().lower()
        friends = self.get_friends()
        if not query_l:
            return friends
        return [
            friend
            for friend in friends
            if query_l in f"{friend_display_name(friend)} {friend.get('email') or ''}".lower()
        ]

    def get_groups(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/get_groups")
        return data.get("groups", [])

    def create_expense(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._request("POST", "/create_expense", json_body=payload)
        errors = data.get("errors")
        if errors:
            raise SplitwiseAPIError("Splitwise create_expense returned errors", data)
        expenses = data.get("expenses", [])
        if not expenses:
            raise SplitwiseAPIError("Splitwise create_expense did not return an expense", data)
        return data


def _safe_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {"text": response.text}


def _parse_oauth_credentials(response_text: str) -> dict[str, str]:
    values = parse_qs(response_text)
    return {key: value[0] for key, value in values.items() if value}


def _request_error_message(exc: requests.RequestException) -> str:
    return (
        "Splitwise request failed before a response was received. Confirm network access, "
        f"Splitwise credentials, and callback URL. Details: {exc}"
    )

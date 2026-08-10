from __future__ import annotations

import httpx

from app.config import Settings, get_settings


class GmailClient:
    """Shared read-only Gmail transport for independent message processors."""

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self.client = client

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.gmail_client_id
            and self.settings.gmail_client_secret
            and self.settings.gmail_refresh_token
        )

    def access_token(self) -> str:
        response = self.request(
            "POST",
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": self.settings.gmail_client_id,
                "client_secret": self.settings.gmail_client_secret,
                "refresh_token": self.settings.gmail_refresh_token,
                "grant_type": "refresh_token",
            },
        )
        token = response.json().get("access_token")
        if not token:
            raise ValueError("gmail_token_refresh_failed")
        return str(token)

    def get_message(self, message_id: str, token: str) -> dict:
        return self.request(
            "GET",
            f"https://gmail.googleapis.com/gmail/v1/users/{self.settings.gmail_user_id}/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"format": "full"},
        ).json()

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        if self.client:
            response = self.client.request(method, url, **kwargs)
        else:
            with httpx.Client(timeout=30.0) as client:
                response = client.request(method, url, **kwargs)
        response.raise_for_status()
        return response

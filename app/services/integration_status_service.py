from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    DataConsent,
    GmailAccount,
    GmailSyncCheckpoint,
    PlaidItem,
    SplitwiseIntegration,
    TelegramIdentity,
)

IntegrationProvider = Literal[
    "plaid",
    "gmail",
    "splitwise",
    "telegram",
    "google_maps",
    "openai",
]
IntegrationScope = Literal["personal", "workspace", "application"]
IntegrationState = Literal[
    "connected",
    "ready",
    "attention_required",
    "disconnected",
    "disabled",
    "unavailable",
]

INTEGRATION_PROVIDERS: tuple[IntegrationProvider, ...] = (
    "plaid",
    "gmail",
    "splitwise",
    "telegram",
    "google_maps",
    "openai",
)
MAX_INTEGRATION_STATUSES = len(INTEGRATION_PROVIDERS)


@dataclass(frozen=True, slots=True)
class IntegrationStatus:
    provider: IntegrationProvider
    label: str
    scope: IntegrationScope
    status: IntegrationState
    message: str
    last_successful_sync_at: datetime | None = None


class IntegrationStatusService:
    """Build a safe status snapshot without contacting integration providers."""

    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def get_statuses(
        self,
        *,
        workspace_id: int,
        user_id: int,
        providers: Iterable[IntegrationProvider] | None = None,
    ) -> list[IntegrationStatus]:
        if workspace_id < 1 or user_id < 1:
            raise ValueError("Integration status requires valid tenant identifiers.")
        requested = tuple(providers) if providers is not None else INTEGRATION_PROVIDERS
        if not requested or len(requested) > MAX_INTEGRATION_STATUSES:
            raise ValueError("Between one and six integration providers are required.")
        if len(set(requested)) != len(requested):
            raise ValueError("Integration providers must be unique.")
        if any(provider not in INTEGRATION_PROVIDERS for provider in requested):
            raise ValueError("Unsupported integration provider.")

        builders = {
            "plaid": self._plaid_status,
            "gmail": self._gmail_status,
            "splitwise": self._splitwise_status,
            "telegram": self._telegram_status,
            "google_maps": self._google_maps_status,
            "openai": self._openai_status,
        }
        with self.db.no_autoflush:
            return [builders[provider](workspace_id, user_id) for provider in requested]

    def _plaid_status(self, workspace_id: int, _user_id: int) -> IntegrationStatus:
        rows = self.db.execute(
            select(
                PlaidItem.owner_user_id,
                PlaidItem.enabled,
                PlaidItem.ownership_verified_at,
            )
            .where(PlaidItem.workspace_id == workspace_id)
            .order_by(PlaidItem.id)
        ).all()
        enabled = [row for row in rows if row.enabled]
        application_available = bool(self.settings.plaid_client_id and self.settings.plaid_secret)

        if enabled:
            if not application_available:
                return IntegrationStatus(
                    provider="plaid",
                    label="Plaid",
                    scope="workspace",
                    status="attention_required",
                    message="The workspace bank connection is linked, but Plaid is unavailable.",
                )
            if any(
                row.owner_user_id is None or row.ownership_verified_at is None for row in enabled
            ):
                return IntegrationStatus(
                    provider="plaid",
                    label="Plaid",
                    scope="workspace",
                    status="attention_required",
                    message="A workspace bank connection needs ownership confirmation.",
                )
            return IntegrationStatus(
                provider="plaid",
                label="Plaid",
                scope="workspace",
                status="connected",
                message="The workspace bank connection is connected.",
            )
        if rows:
            return IntegrationStatus(
                provider="plaid",
                label="Plaid",
                scope="workspace",
                status="disabled",
                message="The workspace bank connection is disabled.",
            )
        return IntegrationStatus(
            provider="plaid",
            label="Plaid",
            scope="workspace",
            status="disconnected" if application_available else "unavailable",
            message=(
                "No workspace bank connection is connected."
                if application_available
                else "Plaid is unavailable in this environment."
            ),
        )

    def _gmail_status(self, workspace_id: int, _user_id: int) -> IntegrationStatus:
        rows = self.db.execute(
            select(GmailAccount.user_id, GmailAccount.enabled)
            .where(GmailAccount.workspace_id == workspace_id)
            .order_by(GmailAccount.id)
        ).all()
        enabled = [row for row in rows if row.enabled]
        application_available = bool(
            self.settings.gmail_client_id and self.settings.gmail_client_secret
        )
        last_sync = None
        if enabled:
            last_sync = self.db.scalar(
                select(func.max(GmailSyncCheckpoint.updated_at)).where(
                    GmailSyncCheckpoint.workspace_id == workspace_id
                )
            )
            last_sync = _as_utc(last_sync)

        if enabled:
            if not application_available:
                return IntegrationStatus(
                    provider="gmail",
                    label="Gmail",
                    scope="workspace",
                    status="attention_required",
                    message="Gmail is linked, but Google OAuth is unavailable in this environment.",
                    last_successful_sync_at=last_sync,
                )
            missing_consents = self._missing_gmail_consents(
                workspace_id=workspace_id,
                connector_user_id=enabled[0].user_id,
            )
            if missing_consents:
                return IntegrationStatus(
                    provider="gmail",
                    label="Gmail",
                    scope="workspace",
                    status="attention_required",
                    message=_gmail_consent_message(missing_consents),
                    last_successful_sync_at=last_sync,
                )
            if (
                not self.settings.gmail_receipt_sync_enabled
                and not self.settings.promotions_enabled
            ):
                message = "Gmail is connected; Gmail-powered imports are disabled."
            elif not self.settings.promotions_enabled:
                message = "Gmail is connected; Promotion Intelligence sync is disabled."
            else:
                message = "The workspace Gmail connection is connected."
            return IntegrationStatus(
                provider="gmail",
                label="Gmail",
                scope="workspace",
                status="connected",
                message=message,
                last_successful_sync_at=last_sync,
            )
        if rows:
            return IntegrationStatus(
                provider="gmail",
                label="Gmail",
                scope="workspace",
                status="disabled",
                message="The workspace Gmail connection is disabled.",
            )
        return IntegrationStatus(
            provider="gmail",
            label="Gmail",
            scope="workspace",
            status="disconnected" if application_available else "unavailable",
            message=(
                "No workspace Gmail connection is connected."
                if application_available
                else "Gmail connections are unavailable in this environment."
            ),
        )

    def _missing_gmail_consents(
        self,
        *,
        workspace_id: int,
        connector_user_id: int,
    ) -> set[str]:
        required: set[str] = set()
        if self.settings.gmail_receipt_sync_enabled:
            required.add("gmail_receipts")
        if self.settings.promotions_enabled:
            required.add("gmail_promotions")
        if (
            self.settings.gmail_receipt_sync_enabled
            and self.settings.receipt_parser_provider == "openai"
        ) or (self.settings.promotions_enabled and self.settings.promotions_llm_fallback_enabled):
            required.add("model_receipt_processing")
        if not required:
            return set()
        granted = set(
            self.db.scalars(
                select(DataConsent.purpose).where(
                    DataConsent.workspace_id == workspace_id,
                    DataConsent.user_id == connector_user_id,
                    DataConsent.purpose.in_(required),
                    DataConsent.granted.is_(True),
                )
            )
        )
        return required - granted

    def _splitwise_status(self, workspace_id: int, user_id: int) -> IntegrationStatus:
        rows = self.db.execute(
            select(SplitwiseIntegration.enabled, SplitwiseIntegration.verified_at)
            .where(
                SplitwiseIntegration.workspace_id == workspace_id,
                SplitwiseIntegration.user_id == user_id,
            )
            .order_by(SplitwiseIntegration.id)
        ).all()
        enabled = [row for row in rows if row.enabled]
        application_available = bool(
            self.settings.splitwise_api_key or self.settings.has_splitwise_oauth1_consumer
        )
        if enabled:
            if not application_available:
                return IntegrationStatus(
                    provider="splitwise",
                    label="Splitwise",
                    scope="personal",
                    status="attention_required",
                    message="Splitwise is linked, but sign-in is unavailable in this environment.",
                )
            if any(row.verified_at is None for row in enabled):
                return IntegrationStatus(
                    provider="splitwise",
                    label="Splitwise",
                    scope="personal",
                    status="attention_required",
                    message="Your Splitwise connection needs verification.",
                )
            return IntegrationStatus(
                provider="splitwise",
                label="Splitwise",
                scope="personal",
                status="connected",
                message="Your personal Splitwise connection is connected.",
            )
        if rows:
            return IntegrationStatus(
                provider="splitwise",
                label="Splitwise",
                scope="personal",
                status="disabled",
                message="Your personal Splitwise connection is disabled.",
            )
        return IntegrationStatus(
            provider="splitwise",
            label="Splitwise",
            scope="personal",
            status="disconnected" if application_available else "unavailable",
            message=(
                "No personal Splitwise connection is connected."
                if application_available
                else "Splitwise sign-in is unavailable in this environment."
            ),
        )

    def _telegram_status(self, workspace_id: int, user_id: int) -> IntegrationStatus:
        rows = list(
            self.db.scalars(
                select(TelegramIdentity.enabled)
                .where(
                    TelegramIdentity.workspace_id == workspace_id,
                    TelegramIdentity.user_id == user_id,
                )
                .order_by(TelegramIdentity.id)
            )
        )
        enabled = any(rows)
        application_available = bool(self.settings.telegram_bot_token)
        if enabled:
            return IntegrationStatus(
                provider="telegram",
                label="Telegram",
                scope="personal",
                status="connected" if application_available else "attention_required",
                message=(
                    "Your personal Telegram connection is connected."
                    if application_available
                    else "Telegram is linked, but bot delivery is unavailable in this environment."
                ),
            )
        if rows:
            return IntegrationStatus(
                provider="telegram",
                label="Telegram",
                scope="personal",
                status="disabled",
                message="Your personal Telegram connection is disabled.",
            )
        return IntegrationStatus(
            provider="telegram",
            label="Telegram",
            scope="personal",
            status="disconnected" if application_available else "unavailable",
            message=(
                "No personal Telegram connection is connected."
                if application_available
                else "Telegram connections are unavailable in this environment."
            ),
        )

    def _google_maps_status(self, _workspace_id: int, _user_id: int) -> IntegrationStatus:
        selected = (
            self.settings.household_routing_provider == "google_maps"
            or self.settings.household_place_search_provider == "google_places"
        )
        if not selected:
            return IntegrationStatus(
                provider="google_maps",
                label="Google Maps",
                scope="application",
                status="disabled",
                message="Google Maps routing and place search are disabled.",
            )
        if not self.settings.google_maps_api_key:
            return IntegrationStatus(
                provider="google_maps",
                label="Google Maps",
                scope="application",
                status="attention_required",
                message="Google Maps is enabled but unavailable in this environment.",
            )
        return IntegrationStatus(
            provider="google_maps",
            label="Google Maps",
            scope="application",
            status="ready",
            message="Google Maps routing or place search is ready.",
        )

    def _openai_status(self, _workspace_id: int, _user_id: int) -> IntegrationStatus:
        required = bool(
            (self.settings.agent_enabled and self.settings.agent_read_tools_enabled)
            or self.settings.receipt_parser_provider == "openai"
            or (self.settings.promotions_enabled and self.settings.promotions_llm_fallback_enabled)
        )
        if self.settings.openai_api_key:
            return IntegrationStatus(
                provider="openai",
                label="OpenAI",
                scope="application",
                status="ready",
                message="OpenAI-backed processing is ready.",
            )
        if required:
            return IntegrationStatus(
                provider="openai",
                label="OpenAI",
                scope="application",
                status="attention_required",
                message="OpenAI-backed processing is enabled but unavailable.",
            )
        return IntegrationStatus(
            provider="openai",
            label="OpenAI",
            scope="application",
            status="disabled",
            message="OpenAI-backed processing is disabled.",
        )


def _gmail_consent_message(missing: set[str]) -> str:
    if missing == {"gmail_promotions"}:
        return "Gmail is connected, but promotion-email permission needs attention."
    if missing == {"gmail_receipts"}:
        return "Gmail is connected, but receipt-email permission needs attention."
    if missing == {"model_receipt_processing"}:
        return "Gmail is connected, but model-processing permission needs attention."
    return "Gmail is connected, but required data-use permissions need attention."


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

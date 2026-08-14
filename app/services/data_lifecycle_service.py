from __future__ import annotations

from datetime import timedelta

from sqlalchemy import String, cast, delete, exists, func, literal, select, update
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    AgentActionProposal,
    AgentConversation,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    AuditEvent,
    AuthIdentity,
    AuthSession,
    DataConsent,
    ExpenseTransaction,
    GmailAccount,
    OAuthState,
    OutboxEvent,
    PlaidItem,
    PlaidWebhookEvent,
    PromotionMessage,
    PurchaseReceipt,
    RateLimitEvent,
    SplitwiseIntegration,
    TelegramIdentity,
    TelegramLinkCode,
    TelegramSession,
    TelegramWebhookUpdate,
    User,
    WorkspaceMembership,
    utc_now,
)
from app.tenancy import set_trusted_workspace

CONSENT_PURPOSES = {
    "gmail_receipts",
    "gmail_promotions",
    "model_receipt_processing",
}
POLICY_VERSION = "2026-08-13"


class DataLifecycleService:
    def __init__(self, db: Session, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()

    def set_consent(
        self,
        *,
        workspace_id: int,
        user_id: int,
        purpose: str,
        granted: bool,
    ) -> DataConsent:
        if purpose not in CONSENT_PURPOSES:
            raise ValueError("Unsupported consent purpose.")
        consent = self.db.scalar(
            select(DataConsent).where(
                DataConsent.workspace_id == workspace_id,
                DataConsent.user_id == user_id,
                DataConsent.purpose == purpose,
            )
        )
        now = utc_now()
        if consent is None:
            consent = DataConsent(
                workspace_id=workspace_id,
                user_id=user_id,
                purpose=purpose,
                granted=granted,
                policy_version=POLICY_VERSION,
                created_at=now,
            )
            self.db.add(consent)
        consent.granted = granted
        consent.policy_version = POLICY_VERSION
        consent.granted_at = now if granted else None
        consent.revoked_at = None if granted else now
        consent.updated_at = now
        self.db.flush()
        self.db.refresh(consent)
        return consent

    def consent_status(self, *, workspace_id: int, user_id: int) -> dict[str, bool]:
        values = self.db.scalars(
            select(DataConsent).where(
                DataConsent.workspace_id == workspace_id,
                DataConsent.user_id == user_id,
            )
        )
        result = {purpose: False for purpose in sorted(CONSENT_PURPOSES)}
        result.update({value.purpose: bool(value.granted) for value in values})
        return result

    def delete_account(self, user: User) -> None:
        memberships = list(
            self.db.scalars(
                select(WorkspaceMembership).where(
                    WorkspaceMembership.user_id == user.id,
                )
            )
        )
        exclusive_workspace_ids: list[int] = []
        shared_membership_ids: list[int] = []
        for membership in memberships:
            members = self.db.scalar(
                select(func.count(WorkspaceMembership.id)).where(
                    WorkspaceMembership.workspace_id == membership.workspace_id
                )
            ) or 0
            if membership.role == "owner" and members > 1:
                raise ValueError(
                    "Transfer ownership of shared workspaces before deleting your account."
                )
            if members == 1:
                exclusive_workspace_ids.append(membership.workspace_id)
            else:
                shared_membership_ids.append(membership.id)

        now = utc_now()
        self.delete_user_agent_data(
            user_id=user.id,
            workspace_ids=[membership.workspace_id for membership in memberships],
        )
        plaid_item_ids = list(
            self.db.scalars(select(PlaidItem.id).where(PlaidItem.owner_user_id == user.id))
        )
        self.db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
        self.db.execute(delete(AuthIdentity).where(AuthIdentity.user_id == user.id))
        self.db.execute(delete(DataConsent).where(DataConsent.user_id == user.id))
        self.db.execute(delete(TelegramIdentity).where(TelegramIdentity.user_id == user.id))
        self.db.execute(delete(TelegramLinkCode).where(TelegramLinkCode.user_id == user.id))
        self.db.execute(delete(GmailAccount).where(GmailAccount.user_id == user.id))
        self.db.execute(delete(SplitwiseIntegration).where(SplitwiseIntegration.user_id == user.id))
        self.db.execute(
            update(PlaidItem)
            .where(PlaidItem.owner_user_id == user.id)
            .values(
                owner_user_id=None,
                ownership_verified_at=None,
                access_token_encrypted=None,
                enabled=False,
            )
        )
        if plaid_item_ids:
            self.db.execute(
                update(ExpenseTransaction)
                .where(ExpenseTransaction.plaid_item_id.in_(plaid_item_ids))
                .values(raw_json=None, last_error=None)
            )
        # Workspace records shared with other members remain shared records after
        # departure. Content from workspaces that only this user could access is
        # removed while minimized financial/audit history is retained.
        if exclusive_workspace_ids:
            self.db.execute(
                delete(PromotionMessage).where(
                    PromotionMessage.workspace_id.in_(exclusive_workspace_ids)
                )
            )
            self.db.execute(
                delete(PurchaseReceipt).where(
                    PurchaseReceipt.workspace_id.in_(exclusive_workspace_ids)
                )
            )
        if shared_membership_ids:
            self.db.execute(
                delete(WorkspaceMembership).where(
                    WorkspaceMembership.id.in_(shared_membership_ids)
                )
            )
        user.email = f"deleted-{user.id}@expenseops.invalid"
        user.display_name = "Deleted user"
        user.api_token_hash = None
        user.status = "deleted"
        user.deletion_requested_at = now
        user.deleted_at = now
        user.updated_at = now
        self.db.commit()

    def delete_user_agent_data(self, *, user_id: int, workspace_ids: list[int]) -> None:
        """Delete private agent content in every workspace the departing user belongs to."""
        original_workspace_id = self.db.info.get("workspace_id")
        models = (
            AgentActionProposal,
            AgentToolCall,
            AgentRun,
            AgentMessage,
            AgentConversation,
        )
        if original_workspace_id is None:
            raise ValueError("Agent data deletion requires an authenticated workspace scope")

        try:
            for workspace_id in sorted(set(workspace_ids)):
                set_trusted_workspace(self.db, workspace_id)
                for model in models:
                    self.db.execute(delete(model).where(model.owner_user_id == user_id))
        finally:
            set_trusted_workspace(self.db, original_workspace_id)

    def purge_expired(self) -> dict[str, int]:
        now = utc_now()
        counts: dict[str, int] = {}

        def purge(name: str, statement) -> None:
            counts[name] = int(self.db.execute(statement).rowcount or 0)

        purge(
            "oauth_states",
            delete(OAuthState).where(OAuthState.expires_at < now - timedelta(days=1)),
        )
        purge(
            "telegram_link_codes",
            delete(TelegramLinkCode).where(TelegramLinkCode.expires_at < now - timedelta(days=1)),
        )
        session_cutoff = now - timedelta(days=self.settings.retention_auth_session_days)
        purge(
            "auth_sessions",
            delete(AuthSession).where(
                (AuthSession.expires_at < session_cutoff)
                | (
                    (AuthSession.revoked_at.is_not(None))
                    & (AuthSession.revoked_at < session_cutoff)
                )
            ),
        )
        purge(
            "completed_outbox_events",
            delete(OutboxEvent).where(
                OutboxEvent.state == "succeeded",
                OutboxEvent.completed_at
                < now - timedelta(days=self.settings.retention_completed_outbox_days),
            ),
        )
        replayable_telegram_delivery = exists(
            select(OutboxEvent.id).where(
                OutboxEvent.event_type == "telegram.process_update",
                OutboxEvent.aggregate_type == "telegram_webhook_update",
                OutboxEvent.aggregate_id == cast(TelegramWebhookUpdate.id, String),
                OutboxEvent.state.not_in(("succeeded", "discarded")),
            )
        )
        purge(
            "telegram_webhook_updates",
            delete(TelegramWebhookUpdate).where(
                TelegramWebhookUpdate.received_at
                < now - timedelta(days=self.settings.retention_webhook_days),
                ~replayable_telegram_delivery,
            ),
        )
        replayable_plaid_delivery = exists(
            select(OutboxEvent.id).where(
                OutboxEvent.event_type == "plaid.sync_item",
                OutboxEvent.dedupe_key
                == literal("plaid-webhook:") + cast(PlaidWebhookEvent.id, String),
                OutboxEvent.state.not_in(("succeeded", "discarded")),
            )
        )
        purge(
            "plaid_webhook_events",
            delete(PlaidWebhookEvent).where(
                PlaidWebhookEvent.received_at
                < now - timedelta(days=self.settings.retention_webhook_days),
                ~replayable_plaid_delivery,
            ),
        )
        # Dead Telegram deliveries retain their raw update briefly for manual
        # recovery. Once that recovery window closes, keep only operational
        # metadata and make the event explicitly non-replayable so private
        # messages, file identifiers, and connection codes are not retained
        # indefinitely.
        outbox_cutoff = now - timedelta(days=self.settings.retention_completed_outbox_days)
        purge(
            "discarded_telegram_outbox_payloads",
            update(OutboxEvent)
            .where(
                OutboxEvent.event_type == "telegram.process_update",
                OutboxEvent.state == "dead",
                OutboxEvent.updated_at < outbox_cutoff,
            )
            .values(
                payload_json={},
                state="discarded",
                completed_at=func.coalesce(OutboxEvent.completed_at, OutboxEvent.updated_at),
                updated_at=now,
                last_error="retention_scrubbed",
            ),
        )
        purge(
            "telegram_sessions",
            delete(TelegramSession).where(
                TelegramSession.updated_at
                < now - timedelta(days=self.settings.retention_telegram_session_days)
            ),
        )
        purge(
            "promotion_messages",
            delete(PromotionMessage).where(
                PromotionMessage.received_at
                < now - timedelta(days=self.settings.retention_promotion_message_days)
            ),
        )
        purge(
            "ignored_receipts",
            delete(PurchaseReceipt).where(
                PurchaseReceipt.parse_status == "ignored",
                PurchaseReceipt.ignored_at
                < now - timedelta(days=self.settings.retention_ignored_receipt_days),
            ),
        )
        purge(
            "audit_events",
            delete(AuditEvent).where(
                AuditEvent.created_at
                < now - timedelta(days=self.settings.retention_audit_event_days)
            ),
        )
        purge(
            "rate_limits",
            delete(RateLimitEvent).where(
                RateLimitEvent.created_at < now - timedelta(days=1)
            ),
        )
        self.db.commit()
        return counts


def operational_retention_summary(settings: Settings) -> dict[str, int]:
    return {
        "authentication_sessions_days": settings.retention_auth_session_days,
        "webhook_delivery_metadata_days": settings.retention_webhook_days,
        "completed_delivery_events_days": settings.retention_completed_outbox_days,
        "telegram_conversation_sessions_days": settings.retention_telegram_session_days,
        "promotion_messages_days": settings.retention_promotion_message_days,
        "ignored_receipts_days": settings.retention_ignored_receipt_days,
        "financial_audit_events_days": settings.retention_audit_event_days,
    }


def gmail_consent_granted(db: Session, purpose: str) -> bool:
    """Enforce consent for managed Gmail accounts while preserving local legacy fixtures."""
    if purpose not in CONSENT_PURPOSES:
        return False
    workspace_id = db.info.get("workspace_id")
    if workspace_id is None:
        return True
    account = db.scalar(
        select(GmailAccount).where(
            GmailAccount.workspace_id == workspace_id,
            GmailAccount.enabled.is_(True),
        )
    )
    if account is None:
        # Local/single-user installations may still use the legacy environment token.
        return True
    return bool(
        db.scalar(
            select(DataConsent.id).where(
                DataConsent.workspace_id == workspace_id,
                DataConsent.user_id == account.user_id,
                DataConsent.purpose == purpose,
                DataConsent.granted.is_(True),
            )
        )
    )

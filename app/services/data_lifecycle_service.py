from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, select, update
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
from app.tenancy import clear_session_tenant, set_trusted_workspace, workspace_ids

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
        original_workspace_id = self.db.info.get("workspace_id")
        if original_workspace_id is None:
            raise ValueError("Account deletion requires an authenticated workspace scope")
        user_id = user.id
        memberships = list(
            self.db.scalars(
                select(WorkspaceMembership).where(
                    WorkspaceMembership.user_id == user_id,
                )
            )
        )
        exclusive_workspace_ids: list[int] = []
        membership_ids = [membership.id for membership in memberships]
        for membership in memberships:
            members = (
                self.db.scalar(
                    select(func.count(WorkspaceMembership.id)).where(
                        WorkspaceMembership.workspace_id == membership.workspace_id
                    )
                )
                or 0
            )
            if membership.role == "owner" and members > 1:
                raise ValueError(
                    "Transfer ownership of shared workspaces before deleting your account."
                )
            if members == 1:
                exclusive_workspace_ids.append(membership.workspace_id)

        now = utc_now()
        tenant_workspace_ids = sorted(
            {
                *workspace_ids(self.db),
                *(membership.workspace_id for membership in memberships),
                original_workspace_id,
            }
        )
        self.delete_user_agent_data(
            user_id=user_id,
            workspace_ids=tenant_workspace_ids,
        )

        # Provider credentials and user-owned private rows can exist in more
        # than the workspace selected for this request.  PostgreSQL FORCE RLS
        # deliberately makes a cross-workspace bulk statement impossible, so
        # perform the privacy operation under each concrete workspace scope.
        try:
            for workspace_id in tenant_workspace_ids:
                set_trusted_workspace(self.db, workspace_id)
                self._revoke_user_workspace_credentials(
                    user_id=user_id,
                    workspace_id=workspace_id,
                )
                if workspace_id in exclusive_workspace_ids:
                    self.db.execute(
                        delete(PromotionMessage).where(
                            PromotionMessage.workspace_id == workspace_id
                        )
                    )
                    self.db.execute(
                        delete(PurchaseReceipt).where(PurchaseReceipt.workspace_id == workspace_id)
                    )
        finally:
            set_trusted_workspace(self.db, original_workspace_id)

        # Authentication and membership directories are intentionally global,
        # non-TenantScoped tables.  Process them only after every tenant-scoped
        # credential has been revoked and the original request scope restored.
        self.db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
        self.db.execute(delete(AuthIdentity).where(AuthIdentity.user_id == user_id))
        if membership_ids:
            self.db.execute(
                delete(WorkspaceMembership).where(WorkspaceMembership.id.in_(membership_ids))
            )
        user.email = f"deleted-{user_id}@expenseops.invalid"
        user.display_name = "Deleted user"
        user.api_token_hash = None
        user.status = "deleted"
        user.deletion_requested_at = now
        user.deleted_at = now
        user.updated_at = now
        self.db.commit()

    def deprovision_workspace_access(self, *, user_id: int, workspace_id: int) -> None:
        """Revoke a departing member's private data and provider authority.

        The caller remains responsible for deleting the membership and committing
        the surrounding transaction. Keeping those operations together makes it
        impossible to retain a usable credential after a successful removal.
        """
        original_workspace_id = self.db.info.get("workspace_id")
        if original_workspace_id is None:
            raise ValueError("Workspace deprovisioning requires an authenticated scope")

        self.delete_user_agent_data(user_id=user_id, workspace_ids=[workspace_id])
        try:
            set_trusted_workspace(self.db, workspace_id)
            self._revoke_user_workspace_credentials(
                user_id=user_id,
                workspace_id=workspace_id,
            )
        finally:
            set_trusted_workspace(self.db, original_workspace_id)

    def _revoke_user_workspace_credentials(
        self,
        *,
        user_id: int,
        workspace_id: int,
    ) -> None:
        """Revoke one user's credentials while already scoped to one workspace."""
        telegram_identities = list(
            self.db.scalars(select(TelegramIdentity).where(TelegramIdentity.user_id == user_id))
        )
        plaid_item_ids = list(
            self.db.scalars(select(PlaidItem.id).where(PlaidItem.owner_user_id == user_id))
        )

        # Telegram conversation state uses provider user/chat identifiers, not
        # the internal ExpenseOps user ID, so remove it before the identities.
        for identity in telegram_identities:
            self.db.execute(
                delete(TelegramSession).where(
                    TelegramSession.user_id == identity.telegram_user_id,
                    TelegramSession.chat_id == identity.chat_id,
                )
            )
        self.db.execute(delete(DataConsent).where(DataConsent.user_id == user_id))
        self.db.execute(delete(TelegramIdentity).where(TelegramIdentity.user_id == user_id))
        self.db.execute(delete(TelegramLinkCode).where(TelegramLinkCode.user_id == user_id))
        self.db.execute(delete(GmailAccount).where(GmailAccount.user_id == user_id))
        self.db.execute(delete(SplitwiseIntegration).where(SplitwiseIntegration.user_id == user_id))
        self.db.execute(
            delete(OAuthState).where(
                OAuthState.user_id == user_id,
                OAuthState.workspace_id == workspace_id,
            )
        )
        self.db.execute(
            update(PlaidItem)
            .where(PlaidItem.owner_user_id == user_id)
            .values(
                owner_user_id=None,
                ownership_verified_at=None,
                access_token_encrypted=None,
                enabled=False,
                updated_at=utc_now(),
            )
        )
        if plaid_item_ids:
            self.db.execute(
                update(ExpenseTransaction)
                .where(ExpenseTransaction.plaid_item_id.in_(plaid_item_ids))
                .values(raw_json=None, last_error=None)
            )

    def delete_user_agent_data(self, *, user_id: int, workspace_ids: list[int]) -> None:
        """Delete private agent content under every supplied concrete workspace scope."""
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
        """Purge retention data without opening a cross-tenant RLS bypass."""
        now = utc_now()
        counts: dict[str, int] = {}
        original_workspace_id = self.db.info.get("workspace_id")
        tenant_ids = workspace_ids(self.db)
        if original_workspace_id is not None and original_workspace_id not in tenant_ids:
            tenant_ids.append(original_workspace_id)

        replayable_telegram_ids: set[int] = set()
        replayable_plaid_ids: set[int] = set()
        for workspace_id in tenant_ids:
            set_trusted_workspace(self.db, workspace_id)
            telegram_values = self.db.scalars(
                select(OutboxEvent.aggregate_id).where(
                    OutboxEvent.event_type == "telegram.process_update",
                    OutboxEvent.aggregate_type == "telegram_webhook_update",
                    OutboxEvent.state.not_in(("succeeded", "discarded")),
                )
            )
            for value in telegram_values:
                try:
                    replayable_telegram_ids.add(int(value))
                except (TypeError, ValueError):
                    continue
            plaid_values = self.db.scalars(
                select(OutboxEvent.dedupe_key).where(
                    OutboxEvent.event_type == "plaid.sync_item",
                    OutboxEvent.dedupe_key.like("plaid-webhook:%"),
                    OutboxEvent.state.not_in(("succeeded", "discarded")),
                )
            )
            for value in plaid_values:
                try:
                    replayable_plaid_ids.add(int(str(value).partition(":")[2]))
                except (TypeError, ValueError):
                    continue

        def purge(name: str, statement) -> None:
            counts[name] = counts.get(name, 0) + int(
                self.db.execute(statement.execution_options(synchronize_session=False)).rowcount
                or 0
            )

        for workspace_id in tenant_ids:
            set_trusted_workspace(self.db, workspace_id)
            purge(
                "telegram_link_codes",
                delete(TelegramLinkCode).where(
                    TelegramLinkCode.expires_at < now - timedelta(days=1)
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
            # Dead Telegram deliveries retain their raw update briefly for
            # manual recovery. After the window, scrub the payload and make the
            # event explicitly non-replayable.
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

        # Shared operational rows are not TenantScoped. Their retention pass is
        # global, but references from every workspace were collected first so a
        # replayable delivery can never lose its prerequisite webhook record.
        clear_session_tenant(self.db)

        purge(
            "oauth_states",
            delete(OAuthState).where(OAuthState.expires_at < now - timedelta(days=1)),
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
        telegram_conditions = [
            TelegramWebhookUpdate.received_at
            < now - timedelta(days=self.settings.retention_webhook_days)
        ]
        if replayable_telegram_ids:
            telegram_conditions.append(TelegramWebhookUpdate.id.not_in(replayable_telegram_ids))
        purge(
            "telegram_webhook_updates",
            delete(TelegramWebhookUpdate).where(*telegram_conditions),
        )
        plaid_conditions = [
            PlaidWebhookEvent.received_at
            < now - timedelta(days=self.settings.retention_webhook_days)
        ]
        if replayable_plaid_ids:
            plaid_conditions.append(PlaidWebhookEvent.id.not_in(replayable_plaid_ids))
        purge(
            "plaid_webhook_events",
            delete(PlaidWebhookEvent).where(*plaid_conditions),
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
            delete(RateLimitEvent).where(RateLimitEvent.created_at < now - timedelta(days=1)),
        )
        if original_workspace_id is None:
            clear_session_tenant(self.db)
        else:
            set_trusted_workspace(self.db, original_workspace_id)
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

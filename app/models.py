from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class TenantScoped:
    """Marker used by the database session to enforce workspace isolation."""

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False, default=1, index=True
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        UniqueConstraint("api_token_hash", name="uq_users_api_token_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    api_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    workspace_type: Mapped[str] = mapped_column(String(32), default="personal")
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_workspace_membership"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="member")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class GmailAccount(TenantScoped, Base):
    __tablename__ = "gmail_accounts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "google_user_id", name="uq_gmail_account_workspace_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, default=1, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    google_user_id: Mapped[str] = mapped_column(String(320), default="me")
    refresh_token_encrypted: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TelegramIdentity(TenantScoped, Base):
    __tablename__ = "telegram_identities"
    __table_args__ = (
        UniqueConstraint("telegram_user_id", "chat_id", name="uq_telegram_identity_external"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, default=1, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    telegram_user_id: Mapped[str] = mapped_column(String(128), index=True)
    chat_id: Mapped[str] = mapped_column(String(128), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SplitwiseIntegration(TenantScoped, Base):
    __tablename__ = "splitwise_integrations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_splitwise_workspace_user"),
        UniqueConstraint(
            "workspace_id", "splitwise_user_id", name="uq_splitwise_workspace_external_user"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, default=1, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    credentials_encrypted: Mapped[str] = mapped_column(Text)
    splitwise_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuthIdentity(Base):
    __tablename__ = "auth_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject", name="uq_auth_identity_provider_subject"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(100), index=True)
    provider_subject: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(320), index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    selected_workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WorkspaceInvitation(TenantScoped, Base):
    __tablename__ = "workspace_invitations"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_workspace_invitation_token_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, default=1, index=True
    )
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[str] = mapped_column(String(32), default="member")
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    invited_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OAuthState(Base):
    __tablename__ = "oauth_states"
    __table_args__ = (UniqueConstraint("state_hash", name="uq_oauth_state_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    state_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    redirect_after: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TelegramLinkCode(TenantScoped, Base):
    __tablename__ = "telegram_link_codes"
    __table_args__ = (UniqueConstraint("code_hash", name="uq_telegram_link_code_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, default=1, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TransactionStatus(StrEnum):
    ASK_USER = "ask_user"
    PERSONAL = "personal"
    SHARED_DRAFT = "shared_draft"
    APPROVED = "approved"
    POSTED = "posted"
    POSTING = "posting"
    POST_AMBIGUOUS = "post_ambiguous"
    UNDOING = "undoing"
    UNDO_AMBIGUOUS = "undo_ambiguous"
    ERROR = "error"
    REMOVED = "removed"


class ErrandType(StrEnum):
    PURCHASE = "purchase"
    RETURN = "return"
    PICKUP = "pickup"
    SERVICE = "service"
    OTHER = "other"


class ErrandPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class ErrandStatus(StrEnum):
    OPEN = "open"
    PLANNED = "planned"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class ErrandSource(StrEnum):
    MANUAL = "manual"
    REPLENISHMENT = "replenishment"


class ReplenishmentMode(StrEnum):
    ERRAND = "errand"
    DELIVERY = "delivery"
    EITHER = "either"


class ReceiptSource(StrEnum):
    TELEGRAM = "telegram"
    GMAIL = "gmail"
    MANUAL = "manual"


class ReceiptParseStatus(StrEnum):
    PENDING = "pending"
    PARSED = "parsed"
    NEEDS_REVIEW = "needs_review"
    CONFIRMED = "confirmed"
    IGNORED = "ignored"
    FAILED = "failed"


class ReceiptItemMatchStatus(StrEnum):
    MATCHED = "matched"
    POSSIBLE = "possible"
    UNMATCHED = "unmatched"
    IRRELEVANT = "irrelevant"
    REJECTED = "rejected"


class ErrandPlanStatus(StrEnum):
    PLANNED = "planned"
    STARTED = "started"
    COMPLETED = "completed"


class SavedLocationType(StrEnum):
    HOME = "home"
    WORK = "work"
    CUSTOM = "custom"


class PlaceResolutionStatus(StrEnum):
    UNRESOLVED = "unresolved"
    NEEDS_USER_CHOICE = "needs_user_choice"
    RESOLVED = "resolved"
    FAILED = "failed"


class PlaidItem(TenantScoped, Base):
    __tablename__ = "plaid_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ownership_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    institution_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    transactions: Mapped[list[ExpenseTransaction]] = relationship(
        back_populates="plaid_item", cascade="all, delete-orphan"
    )


class PlaidWebhookEvent(Base):
    __tablename__ = "plaid_webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspaces.id"), nullable=True, index=True
    )
    webhook_type: Mapped[str] = mapped_column(String(64), index=True)
    webhook_code: Mapped[str] = mapped_column(String(128), index=True)
    plaid_item_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("plaid_items.id"),
        nullable=True,
        index=True,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        index=True,
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_status: Mapped[str] = mapped_column(String(32), default="received")
    sync_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    plaid_item: Mapped[PlaidItem | None] = relationship()


class ExpenseTransaction(TenantScoped, Base):
    __tablename__ = "expense_transactions"
    __table_args__ = (UniqueConstraint("plaid_transaction_id", name="uq_plaid_transaction_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plaid_transaction_id: Mapped[str] = mapped_column(String(128), index=True)
    plaid_item_id: Mapped[int] = mapped_column(ForeignKey("plaid_items.id"), index=True)

    account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    merchant_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    amount_cents: Mapped[int] = mapped_column(Integer)
    iso_currency_code: Mapped[str] = mapped_column(String(8), default="USD")
    date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    authorized_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    pending: Mapped[bool] = mapped_column(Boolean, default=False)
    payment_channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(32), default=TransactionStatus.ASK_USER.value)
    agent_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_notification_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    splitwise_expense_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    splitwise_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    splitwise_generation: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    plaid_item: Mapped[PlaidItem] = relationship(back_populates="transactions")


class FinancialOperation(TenantScoped, Base):
    __tablename__ = "financial_operations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "transaction_id",
            "action",
            "generation",
            name="uq_financial_operation_transaction_action_generation",
        ),
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_financial_operation_idempotency"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, default=1, index=True
    )
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("expense_transactions.id", ondelete="CASCADE"), index=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(64), index=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    provider_object_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    request_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AIInterpretationMemory(TenantScoped, Base):
    __tablename__ = "ai_interpretation_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    original_message: Mapped[str] = mapped_column(Text)
    failure_reason: Mapped[str] = mapped_column(String(64))
    final_action: Mapped[str] = mapped_column(String(64))
    final_group_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    final_group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    final_participants: Mapped[list[dict]] = mapped_column(JSON, default=list)
    final_split_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payer_included: Mapped[bool] = mapped_column(Boolean, default=True)
    custom_values: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    correction_type: Mapped[str] = mapped_column(
        String(64),
        default="button_fallback_learned",
    )
    merchant: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TelegramSession(TenantScoped, Base):
    __tablename__ = "telegram_sessions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "chat_id", "user_id", name="uq_telegram_session_workspace_chat_user"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    state_data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HouseholdItem(TenantScoped, Base):
    __tablename__ = "household_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    quantity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preferred_place_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    preferred_place_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    replenishment_mode: Mapped[str] = mapped_column(
        String(32), default=ReplenishmentMode.EITHER.value, index=True
    )
    cadence_days: Mapped[int] = mapped_column(Integer)
    last_acquired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    errand_links: Mapped[list[ErrandHouseholdItem]] = relationship(
        back_populates="household_item", cascade="all, delete-orphan"
    )


class PurchaseReceipt(TenantScoped, Base):
    __tablename__ = "purchase_receipts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source",
            "source_external_id",
            name="uq_receipt_workspace_source_external",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_external_id: Mapped[str] = mapped_column(String(255))
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    merchant_raw: Mapped[str | None] = mapped_column(String(255), nullable=True)
    merchant_normalized: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    subtotal_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tax_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("expense_transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parse_status: Mapped[str] = mapped_column(
        String(32), default=ReceiptParseStatus.PENDING.value, index=True
    )
    parse_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ignored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    items: Mapped[list[PurchaseReceiptItem]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan", order_by="PurchaseReceiptItem.id"
    )


class PurchaseReceiptItem(Base):
    __tablename__ = "purchase_receipt_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_receipts.id", ondelete="CASCADE"), index=True
    )
    raw_name: Mapped[str] = mapped_column(String(500))
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    normalized_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    normalized_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    package_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_total_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    household_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("household_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    match_status: Mapped[str] = mapped_column(
        String(32), default=ReceiptItemMatchStatus.UNMATCHED.value, index=True
    )
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    receipt: Mapped[PurchaseReceipt] = relationship(back_populates="items")
    household_item: Mapped[HouseholdItem | None] = relationship()
    acquisition: Mapped[HouseholdItemAcquisition | None] = relationship(
        back_populates="receipt_item", uselist=False
    )


class HouseholdItemAlias(Base):
    __tablename__ = "household_item_aliases"
    __table_args__ = (
        UniqueConstraint(
            "household_item_id",
            "merchant_normalized",
            "normalized_alias",
            name="uq_household_alias_item_merchant_name",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_item_id: Mapped[int] = mapped_column(
        ForeignKey("household_items.id", ondelete="CASCADE"), index=True
    )
    merchant_normalized: Mapped[str] = mapped_column(String(255), default="")
    raw_pattern: Mapped[str] = mapped_column(String(500))
    normalized_alias: Mapped[str] = mapped_column(String(255), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(32), default="user")
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    household_item: Mapped[HouseholdItem] = relationship()


class HouseholdItemAcquisition(TenantScoped, Base):
    __tablename__ = "household_item_acquisitions"
    __table_args__ = (
        UniqueConstraint("receipt_item_id", name="uq_acquisition_receipt_item"),
        UniqueConstraint(
            "workspace_id", "logical_purchase_key", name="uq_acquisition_workspace_purchase_key"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_item_id: Mapped[int] = mapped_column(
        ForeignKey("household_items.id", ondelete="CASCADE"), index=True
    )
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    normalized_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    normalized_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    package_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    configured_cadence_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    merchant_normalized: Mapped[str | None] = mapped_column(String(255), nullable=True)
    logical_purchase_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    receipt_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_receipt_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("expense_transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    supersedes_acquisition_id: Mapped[int | None] = mapped_column(
        ForeignKey("household_item_acquisitions.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    user_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    household_item: Mapped[HouseholdItem] = relationship()
    receipt_item: Mapped[PurchaseReceiptItem | None] = relationship(back_populates="acquisition")


class ReplenishmentModelVersion(TenantScoped, Base):
    __tablename__ = "replenishment_model_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "version", name="uq_model_version_workspace_version"),
        Index(
            "uq_replenishment_workspace_single_active_model",
            "workspace_id",
            "status",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(100), index=True)
    algorithm: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32), index=True)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    training_rows: Mapped[int] = mapped_column(Integer)
    training_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    training_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReplenishmentPrediction(TenantScoped, Base):
    __tablename__ = "replenishment_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_item_id: Mapped[int] = mapped_column(
        ForeignKey("household_items.id", ondelete="CASCADE"), index=True
    )
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    predicted_need_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    predicted_days_remaining: Mapped[float] = mapped_column(Float)
    due_score: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(64), index=True)
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("replenishment_model_versions.id", ondelete="SET NULL"), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float)
    confidence_level: Mapped[str] = mapped_column(String(32), default="insufficient", index=True)
    feature_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    actual_next_acquisition_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    household_item: Mapped[HouseholdItem] = relationship()
    model_version: Mapped[ReplenishmentModelVersion | None] = relationship()


class ReplenishmentFeedback(TenantScoped, Base):
    __tablename__ = "replenishment_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_item_id: Mapped[int] = mapped_column(
        ForeignKey("household_items.id", ondelete="CASCADE"), index=True
    )
    prediction_id: Mapped[int | None] = mapped_column(
        ForeignKey("replenishment_predictions.id", ondelete="SET NULL"), nullable=True
    )
    feedback_type: Mapped[str] = mapped_column(String(32), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReplenishmentJobRun(TenantScoped, Base):
    __tablename__ = "replenishment_job_runs"
    __table_args__ = (UniqueConstraint("workspace_id", "run_key", name="uq_job_run_workspace_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_key: Mapped[str] = mapped_column(String(100), index=True)
    trigger: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dataset_size: Mapped[int] = mapped_column(Integer, default=0)
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("replenishment_model_versions.id", ondelete="SET NULL"), nullable=True
    )
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PromotionMessage(TenantScoped, Base):
    __tablename__ = "promotion_messages"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "gmail_message_id", name="uq_promotion_message_workspace_gmail"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gmail_message_id: Mapped[str] = mapped_column(String(255), index=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gmail_history_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    sender_domain: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    subject: Mapped[str] = mapped_column(String(1000), default="")
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    gmail_label_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    parse_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    parse_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    offers: Mapped[list[PromotionOffer]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class PromotionOffer(TenantScoped, Base):
    __tablename__ = "promotion_offers"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "campaign_fingerprint", name="uq_offer_workspace_campaign"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    promotion_message_id: Mapped[int] = mapped_column(
        ForeignKey("promotion_messages.id", ondelete="CASCADE"), index=True
    )
    merchant_raw: Mapped[str] = mapped_column(String(255))
    merchant_normalized: Mapped[str] = mapped_column(String(255), index=True)
    primary_category: Mapped[str] = mapped_column(String(64), default="Other", index=True)
    secondary_categories: Mapped[list] = mapped_column(JSON, default=list)
    offer_type: Mapped[str] = mapped_column(String(32), default="other")
    headline: Mapped[str] = mapped_column(String(1000))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    percent_off: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount_off: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    minimum_spend: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    discounted_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    promo_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    expiry_precision: Mapped[str] = mapped_column(String(32), default="unknown")
    destination_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    destination_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    terms_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    loyalty_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    new_customer_only: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    trust_status: Mapped[str] = mapped_column(String(32), default="review", index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    campaign_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    score_breakdown_json: Mapped[dict] = mapped_column(JSON, default=dict)
    saved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    message: Mapped[PromotionMessage] = relationship(back_populates="offers")


class PromotionFeedback(Base):
    __tablename__ = "promotion_feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    promotion_offer_id: Mapped[int] = mapped_column(
        ForeignKey("promotion_offers.id", ondelete="CASCADE"), index=True
    )
    feedback_type: Mapped[str] = mapped_column(String(32), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PromotionDigestRun(TenantScoped, Base):
    __tablename__ = "promotion_digest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    offers_considered: Mapped[int] = mapped_column(Integer, default=0)
    offers_included: Mapped[int] = mapped_column(Integer, default=0)
    delivery_channel: Mapped[str] = mapped_column(String(32), default="telegram")
    delivery_status: Mapped[str] = mapped_column(String(32), default="preview", index=True)
    campaign_fingerprints: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PromotionSettings(TenantScoped, Base):
    __tablename__ = "promotion_settings"
    __table_args__ = (UniqueConstraint("workspace_id", name="uq_promotion_settings_workspace"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    preferred_categories: Mapped[list] = mapped_column(JSON, default=list)
    muted_categories: Mapped[list] = mapped_column(JSON, default=list)
    preferred_merchants: Mapped[list] = mapped_column(JSON, default=list)
    muted_merchants: Mapped[list] = mapped_column(JSON, default=list)
    minimum_score: Mapped[float] = mapped_column(Float, default=50.0)
    maximum_deals_per_digest: Mapped[int] = mapped_column(Integer, default=8)
    digest_cadence: Mapped[str] = mapped_column(String(32), default="weekly")
    digest_time: Mapped[int] = mapped_column(Integer, default=17)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    include_minimum_spend: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class GmailSyncCheckpoint(TenantScoped, Base):
    __tablename__ = "gmail_sync_checkpoints"
    __table_args__ = (
        UniqueConstraint("workspace_id", "account_key", name="uq_gmail_checkpoint_workspace_key"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(255), index=True)
    history_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    backfill_page_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    initial_backfill_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    watch_expiration_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Errand(TenantScoped, Base):
    __tablename__ = "errands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    errand_type: Mapped[str] = mapped_column(String(32), default=ErrandType.OTHER.value, index=True)
    place_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    place_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    place_resolution_status: Mapped[str] = mapped_column(
        String(32), default=PlaceResolutionStatus.UNRESOLVED.value, index=True
    )
    resolved_place_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolved_place_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolved_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolved_provider_place_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolved_open_now: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    resolved_opening_hours: Mapped[list | None] = mapped_column(JSON, nullable=True)
    place_resolution_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[str] = mapped_column(
        String(32), default=ErrandPriority.NORMAL.value, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default=ErrandStatus.OPEN.value, index=True)
    source: Mapped[str] = mapped_column(String(32), default=ErrandSource.MANUAL.value, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    included_in_next_plan: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    household_links: Mapped[list[ErrandHouseholdItem]] = relationship(
        back_populates="errand", cascade="all, delete-orphan"
    )
    plan_links: Mapped[list[ErrandPlanStopErrand]] = relationship(
        back_populates="errand", cascade="all, delete-orphan"
    )


class ErrandHouseholdItem(Base):
    __tablename__ = "errand_household_items"
    __table_args__ = (
        UniqueConstraint("errand_id", "household_item_id", name="uq_errand_household_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    errand_id: Mapped[int] = mapped_column(ForeignKey("errands.id", ondelete="CASCADE"), index=True)
    household_item_id: Mapped[int] = mapped_column(
        ForeignKey("household_items.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    errand: Mapped[Errand] = relationship(back_populates="household_links")
    household_item: Mapped[HouseholdItem] = relationship(back_populates="errand_links")


class ErrandPlan(TenantScoped, Base):
    __tablename__ = "errand_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    planned_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    base_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default=ErrandPlanStatus.PLANNED.value, index=True
    )
    routing_provider: Mapped[str] = mapped_column(String(64), default="deterministic_fallback")
    routing_is_optimized: Mapped[bool] = mapped_column(Boolean, default=False)
    route_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_stop_minutes: Mapped[int] = mapped_column(Integer, default=0)
    travel_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distance_meters: Mapped[int | None] = mapped_column(Integer, nullable=True)
    baseline_travel_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    incremental_travel_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    available_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    planning_mode: Mapped[str] = mapped_column(String(32), default="standard", index=True)
    primary_destination: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_destination: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendation_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    stops: Mapped[list[ErrandPlanStop]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="ErrandPlanStop.stop_order",
    )


class ErrandPlanStop(Base):
    __tablename__ = "errand_plan_stops"
    __table_args__ = (UniqueConstraint("plan_id", "stop_order", name="uq_plan_stop_order"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("errand_plans.id", ondelete="CASCADE"), index=True
    )
    stop_order: Mapped[int] = mapped_column(Integer)
    place_name: Mapped[str] = mapped_column(String(255))
    place_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    plan: Mapped[ErrandPlan] = relationship(back_populates="stops")
    errand_links: Mapped[list[ErrandPlanStopErrand]] = relationship(
        back_populates="stop", cascade="all, delete-orphan"
    )
    household_item_links: Mapped[list[ErrandPlanStopHouseholdItem]] = relationship(
        back_populates="stop", cascade="all, delete-orphan"
    )


class ErrandPlanStopErrand(Base):
    __tablename__ = "errand_plan_stop_errands"
    __table_args__ = (UniqueConstraint("stop_id", "errand_id", name="uq_plan_stop_errand"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stop_id: Mapped[int] = mapped_column(
        ForeignKey("errand_plan_stops.id", ondelete="CASCADE"), index=True
    )
    errand_id: Mapped[int] = mapped_column(ForeignKey("errands.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    stop: Mapped[ErrandPlanStop] = relationship(back_populates="errand_links")
    errand: Mapped[Errand] = relationship(back_populates="plan_links")


class ErrandPlanStopHouseholdItem(Base):
    __tablename__ = "errand_plan_stop_household_items"
    __table_args__ = (
        UniqueConstraint("stop_id", "household_item_id", name="uq_plan_stop_household_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stop_id: Mapped[int] = mapped_column(
        ForeignKey("errand_plan_stops.id", ondelete="CASCADE"), index=True
    )
    household_item_id: Mapped[int] = mapped_column(
        ForeignKey("household_items.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    stop: Mapped[ErrandPlanStop] = relationship(back_populates="household_item_links")
    household_item: Mapped[HouseholdItem] = relationship()


class SavedLocation(TenantScoped, Base):
    __tablename__ = "saved_locations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "location_type",
            "label",
            name="uq_saved_location_workspace_type_label",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(100))
    address: Mapped[str] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_type: Mapped[str] = mapped_column(
        String(32), default=SavedLocationType.CUSTOM.value, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PreferredPlace(TenantScoped, Base):
    __tablename__ = "preferred_places"
    __table_args__ = (
        UniqueConstraint("workspace_id", "preference_key", name="uq_preferred_place_workspace_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    preference_key: Mapped[str] = mapped_column(String(255), index=True)
    canonical_name: Mapped[str] = mapped_column(String(255))
    full_address: Mapped[str] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider_place_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

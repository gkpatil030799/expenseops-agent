from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_agent_public_id() -> str:
    """Return an opaque, platform-neutral identifier for public agent contracts."""
    return str(uuid4())


_AGENT_FIELD_UNSET = object()


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
    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
        Index(
            "uq_telegram_identity_user_active",
            "user_id",
            unique=True,
            postgresql_where=text("enabled IS TRUE"),
            sqlite_where=text("enabled = 1"),
        ),
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


class TelegramWebhookUpdate(Base):
    __tablename__ = "telegram_webhook_updates"
    __table_args__ = (UniqueConstraint("update_id", name="uq_telegram_webhook_update_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    update_id: Mapped[int] = mapped_column(Integer, index=True)
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    payload_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="processing", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
        Index("ix_auth_sessions_user_expiry", "user_id", "revoked_at", "expires_at"),
    )

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
    __table_args__ = (
        Index("ix_audit_events_workspace_created", "workspace_id", "created_at"),
        Index("ix_audit_events_workspace_type_created", "workspace_id", "event_type", "created_at"),
    )

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


class DataConsent(TenantScoped, Base):
    __tablename__ = "data_consents"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "user_id", "purpose", name="uq_data_consent_workspace_user_purpose"
        ),
        Index("ix_data_consents_user_purpose", "user_id", "purpose"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(64))
    granted: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_version: Mapped[str] = mapped_column(String(32), default="2026-08-13")
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


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
    RECONCILIATION_REQUIRED = "reconciliation_required"
    ERROR = "error"
    REMOVED = "removed"


class ReviewItemKind(StrEnum):
    TRANSACTION_REVIEW = "transaction_review"
    ITEMIZED_SPLIT_READY = "itemized_split_ready"
    RECEIPT_MATCH_NEEDED = "receipt_match_needed"
    FINANCIAL_RECONCILIATION = "financial_reconciliation"


class ReviewItemState(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    STALE = "stale"


class ReviewItemSourceType(StrEnum):
    TRANSACTION = "transaction"
    RECEIPT = "receipt"
    FINANCIAL_OPERATION = "financial_operation"


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
    WEB = "web"


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


class ReceiptLineClassification(StrEnum):
    REPLENISHABLE_HOUSEHOLD = "replenishable_household"
    PERISHABLE_GROCERY = "perishable_grocery"
    ROUTINE_CONSUMPTION = "routine_consumption"
    DINING_OR_EXPERIENCE = "dining_or_experience"
    ONE_TIME_PURCHASE = "one_time_purchase"
    NON_PRODUCT_LINE = "non_product_line"
    UNCERTAIN = "uncertain"


class SpendingParentCategory(StrEnum):
    FOOD_DINING = "food_dining"
    HOUSEHOLD_HOME = "household_home"
    LIFESTYLE_SHOPPING = "lifestyle_shopping"
    PERSONAL_CARE = "personal_care"
    HEALTH = "health"
    TRANSPORTATION = "transportation"
    TRAVEL = "travel"
    ENTERTAINMENT = "entertainment"
    SUBSCRIPTIONS = "subscriptions"
    PETS = "pets"
    EDUCATION_OFFICE = "education_office"
    SERVICES = "services"
    FEES_TAXES_DISCOUNTS = "fees_taxes_discounts"
    OTHER_UNCERTAIN = "other_uncertain"


class ClassificationActivityType(StrEnum):
    GROCERY = "grocery"
    HOUSEHOLD_CONSUMABLE = "household_consumable"
    ROUTINE_CONSUMPTION = "routine_consumption"
    ONE_TIME_PURCHASE = "one_time_purchase"
    RESTAURANT_MEAL = "restaurant_meal"
    COFFEE_BEVERAGE = "coffee_beverage"
    FOOD_DELIVERY = "food_delivery"
    NIGHTLIFE = "nightlife"
    APPAREL = "apparel"
    ELECTRONICS = "electronics"
    PHARMACY = "pharmacy"
    PERSONAL_CARE = "personal_care"
    BEAUTY = "beauty"
    PET_SUPPLY = "pet_supply"
    AUTOMOTIVE = "automotive"
    TRANSPORTATION = "transportation"
    TRAVEL = "travel"
    ENTERTAINMENT = "entertainment"
    SUBSCRIPTION = "subscription"
    EDUCATION_OFFICE = "education_office"
    SERVICE = "service"
    TAX = "tax"
    TIP = "tip"
    DISCOUNT = "discount"
    FEE = "fee"
    REFUND = "refund"
    NON_PRODUCT = "non_product"
    UNCERTAIN = "uncertain"


class ReplenishmentEligibility(StrEnum):
    REPLENISHABLE = "replenishable"
    POTENTIALLY_REPLENISHABLE = "potentially_replenishable"
    NOT_REPLENISHABLE = "not_replenishable"
    UNCERTAIN = "uncertain"


class ClassificationConfidenceBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ClassificationDecisionState(StrEnum):
    PROVISIONAL = "provisional"
    FINAL = "final"
    CORRECTED = "corrected"


class ClassificationAuthority(StrEnum):
    FALLBACK = "fallback"
    MODEL_EVIDENCE = "model_evidence"
    PROVIDER_EVIDENCE = "provider_evidence"
    RECEIPT_EVIDENCE = "receipt_evidence"
    DETERMINISTIC_EXACT = "deterministic_exact"
    CONFIRMED_ALIAS = "confirmed_alias"
    USER_CORRECTION = "user_correction"


SPENDING_PARENT_CATEGORY_SQL = (
    "(" + ", ".join(f"'{value.value}'" for value in SpendingParentCategory) + ")"
)
CLASSIFICATION_ACTIVITY_TYPE_SQL = (
    "(" + ", ".join(f"'{value.value}'" for value in ClassificationActivityType) + ")"
)
REPLENISHMENT_ELIGIBILITY_SQL = (
    "(" + ", ".join(f"'{value.value}'" for value in ReplenishmentEligibility) + ")"
)
CLASSIFICATION_CONFIDENCE_BAND_SQL = (
    "(" + ", ".join(f"'{value.value}'" for value in ClassificationConfidenceBand) + ")"
)
CLASSIFICATION_AUTHORITY_SQL = (
    "(" + ", ".join(f"'{value.value}'" for value in ClassificationAuthority) + ")"
)
CLASSIFICATION_DECISION_STATE_SQL = (
    "(" + ", ".join(f"'{value.value}'" for value in ClassificationDecisionState) + ")"
)


class HouseholdCadenceSource(StrEnum):
    CONFIGURED = "configured"
    LEARNING = "learning"
    CATEGORY_PRIOR = "category_prior"
    MODEL_PRIOR = "model_prior"
    OBSERVED = "observed"
    QUANTITY_ADJUSTED = "quantity_adjusted"
    ADAPTIVE = "adaptive"


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
    __table_args__ = (
        Index("ix_plaid_webhook_processing_received", "processing_status", "received_at"),
        Index(
            "ux_plaid_webhook_events_delivery_fingerprint",
            "delivery_fingerprint",
            unique=True,
        ),
    )

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
    delivery_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    plaid_item: Mapped[PlaidItem | None] = relationship()


class ExpenseTransaction(TenantScoped, Base):
    __tablename__ = "expense_transactions"
    __table_args__ = (
        UniqueConstraint("plaid_transaction_id", name="uq_plaid_transaction_id"),
        CheckConstraint(
            "status != 'shared_draft' OR "
            "(splitwise_payload_json IS NOT NULL "
            "AND trim(splitwise_payload_json) NOT IN ('', '{}'))",
            name="ck_expense_transactions_valid_shared_draft",
        ),
        Index(
            "ix_expense_transactions_workspace_review",
            "workspace_id",
            "status",
            "pending",
            "date",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "classification_subcategory_id"],
            ["classification_subcategories.workspace_id", "classification_subcategories.id"],
            name="fk_expense_transaction_classification_subcategory_workspace",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "classification_concept_id"],
            ["classification_concepts.workspace_id", "classification_concepts.id"],
            name="fk_expense_transaction_classification_concept_workspace",
        ),
        CheckConstraint(
            "classification_confidence >= 0 AND classification_confidence <= 1",
            name="ck_expense_transaction_classification_confidence",
        ),
        CheckConstraint(
            "classification_version > 0",
            name="ck_expense_transaction_classification_version",
        ),
        CheckConstraint(
            f"spending_parent_category IN {SPENDING_PARENT_CATEGORY_SQL}",
            name="ck_expense_transaction_spending_parent",
        ),
        CheckConstraint(
            f"classification_activity_type IN {CLASSIFICATION_ACTIVITY_TYPE_SQL}",
            name="ck_expense_transaction_classification_activity",
        ),
        CheckConstraint(
            f"replenishment_eligibility IN {REPLENISHMENT_ELIGIBILITY_SQL}",
            name="ck_expense_transaction_replenishment_eligibility",
        ),
        CheckConstraint(
            f"classification_confidence_band IN {CLASSIFICATION_CONFIDENCE_BAND_SQL}",
            name="ck_expense_transaction_classification_band",
        ),
        CheckConstraint(
            f"classification_authority IN {CLASSIFICATION_AUTHORITY_SQL}",
            name="ck_expense_transaction_classification_authority",
        ),
        CheckConstraint(
            f"classification_decision_state IN {CLASSIFICATION_DECISION_STATE_SQL}",
            name="ck_expense_transaction_classification_state",
        ),
    )

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
    provider_category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    spending_parent_category: Mapped[str] = mapped_column(
        String(64), default=SpendingParentCategory.OTHER_UNCERTAIN.value, index=True
    )
    classification_subcategory_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    classification_subcategory_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    classification_concept_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    classification_concept_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    classification_activity_type: Mapped[str] = mapped_column(
        String(64), default=ClassificationActivityType.UNCERTAIN.value, index=True
    )
    replenishment_eligibility: Mapped[str] = mapped_column(
        String(32), default=ReplenishmentEligibility.UNCERTAIN.value, index=True
    )
    classification_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    classification_confidence_band: Mapped[str] = mapped_column(
        String(16), default=ClassificationConfidenceBand.LOW.value, index=True
    )
    classification_authority: Mapped[str] = mapped_column(
        String(32), default=ClassificationAuthority.FALLBACK.value, index=True
    )
    classification_provenance_json: Mapped[list] = mapped_column(JSON, default=list)
    classification_decision_state: Mapped[str] = mapped_column(
        String(16), default=ClassificationDecisionState.FINAL.value, index=True
    )
    classification_applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    classification_auto_finalize_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    classification_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    classification_finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    classification_corrected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    classification_corrects_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classification_version: Mapped[int] = mapped_column(Integer, default=1)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(32), default=TransactionStatus.ASK_USER.value)
    agent_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_notification_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    review_notification_queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    splitwise_expense_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    splitwise_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    splitwise_amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    splitwise_generation: Mapped[int] = mapped_column(Integer, default=0)
    replaces_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("expense_transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    replaced_by_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("expense_transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    plaid_item: Mapped[PlaidItem] = relationship(back_populates="transactions")


class ReviewItem(TenantScoped, Base):
    """Durable presentation state for one canonical user decision.

    The referenced transaction, receipt, or financial operation remains the domain
    authority. This projection owns only inbox identity and presentation state.
    """

    __tablename__ = "review_items"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_review_items_public_id"),
        UniqueConstraint(
            "workspace_id",
            "owner_user_id",
            "kind",
            "source_type",
            "source_entity_id",
            name="uq_review_item_owner_kind_source",
        ),
        Index(
            "ix_review_items_owner_state_created",
            "workspace_id",
            "owner_user_id",
            "state",
            "created_at",
        ),
        CheckConstraint(
            "kind IN ('transaction_review', 'itemized_split_ready', "
            "'receipt_match_needed', 'financial_reconciliation')",
            name="ck_review_items_kind",
        ),
        CheckConstraint(
            "source_type IN ('transaction', 'receipt', 'financial_operation')",
            name="ck_review_items_source_type",
        ),
        CheckConstraint(
            "state IN ('open', 'resolved', 'stale')",
            name="ck_review_items_state",
        ),
        CheckConstraint("source_entity_id > 0", name="ck_review_items_source_entity_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(36), default=new_agent_public_id, nullable=False, index=True
    )
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    state: Mapped[str] = mapped_column(
        String(16), default=ReviewItemState.OPEN.value, nullable=False, index=True
    )
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    action_codes_json: Mapped[list] = mapped_column(JSON, default=list)
    source_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


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
        Index(
            "ix_financial_operations_workspace_state_updated",
            "workspace_id",
            "state",
            "updated_at",
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


class OutboxEvent(TenantScoped, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "dedupe_key", name="uq_outbox_events_workspace_dedupe"),
        Index(
            "ix_outbox_events_workspace_delivery",
            "workspace_id",
            "state",
            "available_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(100))
    aggregate_id: Mapped[str] = mapped_column(String(128), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(255))
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RateLimitEvent(Base):
    __tablename__ = "rate_limit_events"
    __table_args__ = (
        UniqueConstraint("key", "window_started_at", name="uq_rate_limit_key_window"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(255), index=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    window_seconds: Mapped[int] = mapped_column(Integer)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ScheduledJobLease(TenantScoped, Base):
    __tablename__ = "scheduled_job_leases"
    __table_args__ = (
        UniqueConstraint("workspace_id", "job_name", name="uq_scheduled_job_lease_workspace_job"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_name: Mapped[str] = mapped_column(String(100), index=True)
    lease_token: Mapped[str] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AIInterpretationMemory(TenantScoped, Base):
    __tablename__ = "ai_interpretation_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
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


class ProactiveAttentionPreference(TenantScoped, Base):
    __tablename__ = "proactive_attention_preferences"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_attention_preferences_workspace_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    categories_json: Mapped[list] = mapped_column(JSON, default=list)
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_mode: Mapped[str] = mapped_column(String(16), default="digest")
    quiet_start_hour: Mapped[int] = mapped_column(Integer, default=22)
    quiet_end_hour: Mapped[int] = mapped_column(Integer, default=7)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    max_alerts_per_day: Mapped[int] = mapped_column(Integer, default=3)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=240)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProactiveAttentionDelivery(TenantScoped, Base):
    __tablename__ = "proactive_attention_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            "channel",
            "dedupe_key",
            name="uq_attention_delivery_dedupe",
        ),
        Index(
            "ix_attention_delivery_user_delivered",
            "workspace_id",
            "user_id",
            "delivered_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(16))
    dedupe_key: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    attention_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class ClassificationSubcategory(TenantScoped, Base):
    __tablename__ = "classification_subcategories"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_classification_subcategory_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "parent_category",
            "normalized_name",
            name="uq_classification_subcategory_workspace_parent_name",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "merged_into_id"],
            ["classification_subcategories.workspace_id", "classification_subcategories.id"],
            name="fk_classification_subcategory_merged_workspace",
        ),
        CheckConstraint(
            "parent_category IN ("
            "'food_dining', 'household_home', 'lifestyle_shopping', 'personal_care', "
            "'health', 'transportation', 'travel', 'entertainment', 'subscriptions', "
            "'pets', 'education_office', 'services', 'fees_taxes_discounts', "
            "'other_uncertain')",
            name="ck_classification_subcategory_parent",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_classification_subcategory_confidence",
        ),
        CheckConstraint(
            f"source IN {CLASSIFICATION_AUTHORITY_SQL}",
            name="ck_classification_subcategory_source",
        ),
        CheckConstraint(
            "merged_into_id IS NULL OR merged_into_id != id",
            name="ck_classification_subcategory_not_self_merged",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, default=1, index=True
    )
    parent_category: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(32), default="deterministic_exact")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    merged_into_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClassificationConcept(TenantScoped, Base):
    __tablename__ = "classification_concepts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_classification_concept_workspace_id"),
        UniqueConstraint(
            "workspace_id", "normalized_name", name="uq_classification_concept_workspace_name"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "subcategory_id"],
            ["classification_subcategories.workspace_id", "classification_subcategories.id"],
            name="fk_classification_concept_subcategory_workspace",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "merged_into_id"],
            ["classification_concepts.workspace_id", "classification_concepts.id"],
            name="fk_classification_concept_merged_workspace",
        ),
        CheckConstraint(
            "parent_category IN ("
            "'food_dining', 'household_home', 'lifestyle_shopping', 'personal_care', "
            "'health', 'transportation', 'travel', 'entertainment', 'subscriptions', "
            "'pets', 'education_office', 'services', 'fees_taxes_discounts', "
            "'other_uncertain')",
            name="ck_classification_concept_parent",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_classification_concept_confidence",
        ),
        CheckConstraint(
            f"item_activity_type IN {CLASSIFICATION_ACTIVITY_TYPE_SQL}",
            name="ck_classification_concept_activity",
        ),
        CheckConstraint(
            f"replenishment_eligibility IN {REPLENISHMENT_ELIGIBILITY_SQL}",
            name="ck_classification_concept_replenishment_eligibility",
        ),
        CheckConstraint(
            f"source IN {CLASSIFICATION_AUTHORITY_SQL}",
            name="ck_classification_concept_source",
        ),
        CheckConstraint(
            "merged_into_id IS NULL OR merged_into_id != id",
            name="ck_classification_concept_not_self_merged",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, default=1, index=True
    )
    subcategory_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    parent_category: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    item_activity_type: Mapped[str] = mapped_column(String(64), index=True)
    replenishment_eligibility: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(32), default="deterministic_exact")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    merged_into_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClassificationConceptAlias(TenantScoped, Base):
    __tablename__ = "classification_concept_aliases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "concept_id"],
            ["classification_concepts.workspace_id", "classification_concepts.id"],
            name="fk_classification_concept_alias_workspace",
            ondelete="CASCADE",
        ),
        Index(
            "uq_classification_concept_alias_active",
            "workspace_id",
            "merchant_normalized",
            "normalized_alias",
            unique=True,
            postgresql_where=text("voided_at IS NULL"),
            sqlite_where=text("voided_at IS NULL"),
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_classification_concept_alias_confidence",
        ),
        CheckConstraint(
            f"source IN {CLASSIFICATION_AUTHORITY_SQL}",
            name="ck_classification_concept_alias_source",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, default=1, index=True
    )
    concept_id: Mapped[int] = mapped_column(Integer, index=True)
    merchant_normalized: Mapped[str] = mapped_column(String(255), default="")
    raw_pattern: Mapped[str] = mapped_column(String(500))
    normalized_alias: Mapped[str] = mapped_column(String(255), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(32), default="confirmed_alias")
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClassificationSettings(TenantScoped, Base):
    __tablename__ = "classification_settings"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_classification_settings_workspace"),
        CheckConstraint(
            "grace_hours >= 1 AND grace_hours <= 168",
            name="ck_classification_settings_grace_hours",
        ),
        CheckConstraint(
            "receipt_item_backfill_cursor >= 0 AND transaction_backfill_cursor >= 0 "
            "AND receipt_match_backfill_cursor >= 0",
            name="ck_classification_settings_backfill_cursors",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, default=1, index=True
    )
    autonomous_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    category_creation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cadence_estimation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    grace_hours: Mapped[int] = mapped_column(Integer, default=24)
    receipt_item_backfill_cursor: Mapped[int] = mapped_column(Integer, default=0)
    transaction_backfill_cursor: Mapped[int] = mapped_column(Integer, default=0)
    receipt_match_backfill_cursor: Mapped[int] = mapped_column(Integer, default=0)
    last_backfill_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClassificationDecisionRecord(TenantScoped, Base):
    """Immutable classification ledger; projections remain the read-optimized current state."""

    __tablename__ = "classification_decisions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_classification_decision_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "source_type",
            "source_entity_id",
            "version",
            name="uq_classification_decision_source_version",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "subcategory_id"],
            ["classification_subcategories.workspace_id", "classification_subcategories.id"],
            name="fk_classification_decision_subcategory_workspace",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "concept_id"],
            ["classification_concepts.workspace_id", "classification_concepts.id"],
            name="fk_classification_decision_concept_workspace",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "corrects_decision_id"],
            ["classification_decisions.workspace_id", "classification_decisions.id"],
            name="fk_classification_decision_correction_workspace",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "household_item_id"],
            ["household_items.workspace_id", "household_items.id"],
            name="fk_classification_decision_household_item_workspace",
        ),
        CheckConstraint(
            "source_type IN ('receipt_line', 'transaction')",
            name="ck_classification_decision_source_type",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_classification_decision_confidence",
        ),
        CheckConstraint("version > 0", name="ck_classification_decision_version"),
        CheckConstraint(
            f"spending_parent_category IN {SPENDING_PARENT_CATEGORY_SQL}",
            name="ck_classification_decision_spending_parent",
        ),
        CheckConstraint(
            f"item_activity_type IN {CLASSIFICATION_ACTIVITY_TYPE_SQL}",
            name="ck_classification_decision_activity",
        ),
        CheckConstraint(
            f"replenishment_eligibility IN {REPLENISHMENT_ELIGIBILITY_SQL}",
            name="ck_classification_decision_replenishment_eligibility",
        ),
        CheckConstraint(
            f"confidence_band IN {CLASSIFICATION_CONFIDENCE_BAND_SQL}",
            name="ck_classification_decision_band",
        ),
        CheckConstraint(
            f"authority IN {CLASSIFICATION_AUTHORITY_SQL}",
            name="ck_classification_decision_authority",
        ),
        CheckConstraint(
            f"decision_state IN {CLASSIFICATION_DECISION_STATE_SQL}",
            name="ck_classification_decision_state",
        ),
        CheckConstraint(
            "(decision_state = 'provisional' AND auto_finalize_at IS NOT NULL "
            "AND finalized_at IS NULL) OR "
            "(decision_state IN ('final', 'corrected') AND finalized_at IS NOT NULL)",
            name="ck_classification_decision_finalization",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, default=1, index=True
    )
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    source_entity_id: Mapped[int] = mapped_column(Integer, index=True)
    version: Mapped[int] = mapped_column(Integer)
    spending_parent_category: Mapped[str] = mapped_column(String(64), index=True)
    subcategory_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    subcategory_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    concept_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    concept_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    item_activity_type: Mapped[str] = mapped_column(String(64), index=True)
    replenishment_eligibility: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    confidence_band: Mapped[str] = mapped_column(String(16), index=True)
    authority: Mapped[str] = mapped_column(String(32), index=True)
    provenance_json: Mapped[list] = mapped_column(JSON, default=list)
    decision_state: Mapped[str] = mapped_column(String(16), index=True)
    auto_finalize_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    corrects_decision_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    household_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_subcategory: Mapped[bool] = mapped_column(Boolean, default=False)
    created_concept: Mapped[bool] = mapped_column(Boolean, default=False)
    created_alias: Mapped[bool] = mapped_column(Boolean, default=False)
    created_household_item: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HouseholdItem(TenantScoped, Base):
    __tablename__ = "household_items"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_household_item_workspace_id"),
        CheckConstraint(
            "(cadence_source = 'learning' AND cadence_days IS NULL) OR "
            "(cadence_source IN ('configured', 'category_prior', 'model_prior', 'observed', "
            "'quantity_adjusted', 'adaptive') "
            "AND cadence_days IS NOT NULL AND cadence_days > 0)",
            name="ck_household_items_cadence_state",
        ),
        CheckConstraint(
            "cadence_confidence >= 0 AND cadence_confidence <= 1",
            name="ck_household_items_cadence_confidence",
        ),
        CheckConstraint(
            "(cadence_min_days IS NULL AND cadence_max_days IS NULL) OR "
            "(cadence_days IS NOT NULL AND cadence_min_days > 0 AND "
            "cadence_max_days > 0 AND cadence_min_days <= cadence_days AND "
            "cadence_days <= cadence_max_days)",
            name="ck_household_items_cadence_range",
        ),
        CheckConstraint(
            f"spending_parent_category IN {SPENDING_PARENT_CATEGORY_SQL}",
            name="ck_household_items_spending_parent",
        ),
        CheckConstraint(
            f"replenishment_eligibility IN {REPLENISHMENT_ELIGIBILITY_SQL}",
            name="ck_household_items_replenishment_eligibility",
        ),
        CheckConstraint(
            "classification_confidence >= 0 AND classification_confidence <= 1",
            name="ck_household_items_classification_confidence",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "classification_concept_id"],
            ["classification_concepts.workspace_id", "classification_concepts.id"],
            name="fk_household_item_classification_concept_workspace",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "merged_into_id"],
            ["household_items.workspace_id", "household_items.id"],
            name="fk_household_item_merged_workspace",
        ),
        CheckConstraint(
            "merged_into_id IS NULL OR merged_into_id != id",
            name="ck_household_item_not_self_merged",
        ),
        Index(
            "uq_household_items_workspace_canonical_enabled",
            "workspace_id",
            "canonical_key",
            unique=True,
            postgresql_where=text("canonical_key IS NOT NULL AND enabled IS TRUE"),
            sqlite_where=text("canonical_key IS NOT NULL AND enabled = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    quantity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preferred_place_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    preferred_place_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    replenishment_mode: Mapped[str] = mapped_column(
        String(32), default=ReplenishmentMode.EITHER.value, index=True
    )
    cadence_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cadence_source: Mapped[str] = mapped_column(
        String(32), default=HouseholdCadenceSource.CONFIGURED.value, nullable=False
    )
    cadence_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    cadence_min_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cadence_max_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cadence_provenance_json: Mapped[dict] = mapped_column(JSON, default=dict)
    cadence_estimated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canonical_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    classification_concept_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    spending_parent_category: Mapped[str] = mapped_column(
        String(64), default=SpendingParentCategory.OTHER_UNCERTAIN.value, index=True
    )
    replenishment_eligibility: Mapped[str] = mapped_column(
        String(32), default=ReplenishmentEligibility.REPLENISHABLE.value, index=True
    )
    classification_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    classification_provenance_json: Mapped[list] = mapped_column(JSON, default=list)
    merged_into_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
        Index(
            "ix_purchase_receipts_workspace_queue",
            "workspace_id",
            "parse_status",
            "created_at",
        ),
        CheckConstraint(
            "transaction_match_status IN "
            "('not_attempted', 'auto_matched', 'ambiguous', 'no_match')",
            name="ck_purchase_receipts_transaction_match_status",
        ),
        CheckConstraint(
            "transaction_match_confidence >= 0 AND transaction_match_confidence <= 1",
            name="ck_purchase_receipts_transaction_match_confidence",
        ),
        CheckConstraint(
            "(transaction_match_status != 'auto_matched' OR "
            "(transaction_id IS NOT NULL AND transaction_matched_at IS NOT NULL)) AND "
            "(transaction_match_status = 'auto_matched' OR transaction_matched_at IS NULL)",
            name="ck_purchase_receipts_transaction_match_consistency",
        ),
        CheckConstraint(
            "arithmetic_status IN ('unknown', 'verified', 'mismatch', 'insufficient_data')",
            name="ck_purchase_receipts_arithmetic_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_purchase_receipt_owner_user", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_external_id: Mapped[str] = mapped_column(String(255))
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    merchant_raw: Mapped[str | None] = mapped_column(String(255), nullable=True)
    merchant_normalized: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    subtotal_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tax_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tip_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    line_items_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    quality_warnings_json: Mapped[list] = mapped_column(JSON, default=list)
    arithmetic_status: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("expense_transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    transaction_match_status: Mapped[str] = mapped_column(
        String(32), default="not_attempted", index=True
    )
    transaction_match_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    transaction_match_evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    transaction_match_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    transaction_matched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    __table_args__ = (
        CheckConstraint(
            "classification IS NULL OR classification IN ("
            "'replenishable_household', 'perishable_grocery', 'routine_consumption', "
            "'dining_or_experience', 'one_time_purchase', 'non_product_line', 'uncertain')",
            name="ck_purchase_receipt_items_classification",
        ),
        CheckConstraint(
            "classification_confidence IS NULL OR "
            "(classification_confidence >= 0 AND classification_confidence <= 1)",
            name="ck_purchase_receipt_items_classification_confidence",
        ),
        CheckConstraint(
            "classification_version > 0",
            name="ck_purchase_receipt_items_classification_version",
        ),
        CheckConstraint(
            f"spending_parent_category IN {SPENDING_PARENT_CATEGORY_SQL}",
            name="ck_purchase_receipt_items_spending_parent",
        ),
        CheckConstraint(
            f"item_activity_type IN {CLASSIFICATION_ACTIVITY_TYPE_SQL}",
            name="ck_purchase_receipt_items_classification_activity",
        ),
        CheckConstraint(
            f"replenishment_eligibility IN {REPLENISHMENT_ELIGIBILITY_SQL}",
            name="ck_purchase_receipt_items_replenishment_eligibility",
        ),
        CheckConstraint(
            f"classification_confidence_band IN {CLASSIFICATION_CONFIDENCE_BAND_SQL}",
            name="ck_purchase_receipt_items_classification_band",
        ),
        CheckConstraint(
            f"classification_authority IN {CLASSIFICATION_AUTHORITY_SQL}",
            name="ck_purchase_receipt_items_classification_authority",
        ),
        CheckConstraint(
            f"classification_decision_state IN {CLASSIFICATION_DECISION_STATE_SQL}",
            name="ck_purchase_receipt_items_classification_state",
        ),
        ForeignKeyConstraint(
            ["classification_subcategory_id"],
            ["classification_subcategories.id"],
            name="fk_purchase_receipt_item_classification_subcategory",
        ),
        ForeignKeyConstraint(
            ["classification_concept_id"],
            ["classification_concepts.id"],
            name="fk_purchase_receipt_item_classification_concept",
        ),
    )

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
    classification: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    canonical_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    spending_parent_category: Mapped[str] = mapped_column(
        String(64), default=SpendingParentCategory.OTHER_UNCERTAIN.value, index=True
    )
    classification_subcategory_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    classification_subcategory_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    classification_concept_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    classification_concept_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    item_activity_type: Mapped[str] = mapped_column(
        String(64), default=ClassificationActivityType.UNCERTAIN.value, index=True
    )
    replenishment_eligibility: Mapped[str] = mapped_column(
        String(32), default=ReplenishmentEligibility.UNCERTAIN.value, index=True
    )
    classification_confidence_band: Mapped[str] = mapped_column(
        String(16), default=ClassificationConfidenceBand.LOW.value, index=True
    )
    classification_authority: Mapped[str] = mapped_column(
        String(32), default=ClassificationAuthority.FALLBACK.value, index=True
    )
    classification_provenance_json: Mapped[list] = mapped_column(JSON, default=list)
    classification_decision_state: Mapped[str] = mapped_column(
        String(16), default=ClassificationDecisionState.FINAL.value, index=True
    )
    classification_applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    classification_auto_finalize_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    classification_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    classification_finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    classification_corrected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    classification_corrects_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classification_version: Mapped[int] = mapped_column(Integer, default=1)
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
        UniqueConstraint(
            "workspace_id", "id", name="uq_household_item_acquisition_workspace_id"
        ),
        Index(
            "uq_acquisition_receipt_item_active",
            "receipt_item_id",
            unique=True,
            postgresql_where=text("receipt_item_id IS NOT NULL AND voided_at IS NULL"),
            sqlite_where=text("receipt_item_id IS NOT NULL AND voided_at IS NULL"),
        ),
        UniqueConstraint(
            "workspace_id", "logical_purchase_key", name="uq_acquisition_workspace_purchase_key"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "household_item_id"],
            ["household_items.workspace_id", "household_items.id"],
            name="fk_household_item_acquisition_item_workspace",
            ondelete="CASCADE",
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

    household_item: Mapped[HouseholdItem] = relationship(
        primaryjoin=(
            "and_(HouseholdItemAcquisition.workspace_id == HouseholdItem.workspace_id, "
            "HouseholdItemAcquisition.household_item_id == HouseholdItem.id)"
        ),
        foreign_keys=(
            "[HouseholdItemAcquisition.workspace_id, "
            "HouseholdItemAcquisition.household_item_id]"
        ),
    )
    receipt_item: Mapped[PurchaseReceiptItem | None] = relationship(back_populates="acquisition")


class ReplenishmentModelVersion(TenantScoped, Base):
    __tablename__ = "replenishment_model_versions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "id", name="uq_replenishment_model_version_workspace_id"
        ),
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
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "id", name="uq_replenishment_prediction_workspace_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "household_item_id"],
            ["household_items.workspace_id", "household_items.id"],
            name="fk_replenishment_prediction_item_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "model_version_id"],
            ["replenishment_model_versions.workspace_id", "replenishment_model_versions.id"],
            name="fk_replenishment_prediction_model_workspace",
        ),
    )

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

    household_item: Mapped[HouseholdItem] = relationship(
        primaryjoin=(
            "and_(ReplenishmentPrediction.workspace_id == HouseholdItem.workspace_id, "
            "ReplenishmentPrediction.household_item_id == HouseholdItem.id)"
        ),
        foreign_keys=(
            "[ReplenishmentPrediction.workspace_id, "
            "ReplenishmentPrediction.household_item_id]"
        ),
    )
    model_version: Mapped[ReplenishmentModelVersion | None] = relationship(
        primaryjoin=(
            "and_(ReplenishmentPrediction.workspace_id == "
            "ReplenishmentModelVersion.workspace_id, "
            "ReplenishmentPrediction.model_version_id == ReplenishmentModelVersion.id)"
        ),
        foreign_keys=(
            "[ReplenishmentPrediction.workspace_id, "
            "ReplenishmentPrediction.model_version_id]"
        ),
        overlaps="household_item",
    )


class ReplenishmentFeedback(TenantScoped, Base):
    __tablename__ = "replenishment_feedback"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "household_item_id"],
            ["household_items.workspace_id", "household_items.id"],
            name="fk_replenishment_feedback_item_workspace",
            ondelete="CASCADE",
        ),
    )

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
    __table_args__ = (
        UniqueConstraint("workspace_id", "run_key", name="uq_job_run_workspace_key"),
        ForeignKeyConstraint(
            ["workspace_id", "model_version_id"],
            ["replenishment_model_versions.workspace_id", "replenishment_model_versions.id"],
            name="fk_replenishment_job_run_model_workspace",
        ),
    )

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
        Index(
            "ix_promotion_messages_workspace_processing",
            "workspace_id",
            "parse_status",
            "received_at",
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
        Index(
            "ix_promotion_offers_workspace_active_rank",
            "workspace_id",
            "status",
            "saved",
            "score",
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
    incremental_page_token: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    input_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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


class AgentConversationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class AgentMessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class AgentMessageStatus(StrEnum):
    CREATED = "created"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentToolOperationKind(StrEnum):
    READ = "read"
    WRITE = "write"
    EXTERNAL_ACTION = "external_action"


class AgentToolCallStatus(StrEnum):
    PROPOSED = "proposed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class AgentActionProposalStatus(StrEnum):
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    EXECUTING = "executing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class AgentConversation(TenantScoped, Base):
    __tablename__ = "agent_conversations"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_agent_conversations_public_id"),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_agent_conversations_status",
        ),
        Index(
            "ix_agent_conversations_workspace_owner_status_updated",
            "workspace_id",
            "owner_user_id",
            "status",
            "updated_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    public_id: Mapped[str] = mapped_column(String(36), default=new_agent_public_id, nullable=False)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default=AgentConversationStatus.ACTIVE.value, nullable=False, index=True
    )
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[list[AgentMessage]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    runs: Mapped[list[AgentRun]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    proposals: Mapped[list[AgentActionProposal]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class AgentMessage(TenantScoped, Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_agent_messages_public_id"),
        UniqueConstraint(
            "workspace_id",
            "conversation_id",
            "client_message_id",
            name="uq_agent_messages_workspace_conversation_client",
        ),
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_agent_messages_role",
        ),
        CheckConstraint(
            "status IN ('created', 'completed', 'failed')",
            name="ck_agent_messages_status",
        ),
        Index(
            "ix_agent_messages_workspace_owner_conversation_created",
            "workspace_id",
            "owner_user_id",
            "conversation_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    public_id: Mapped[str] = mapped_column(String(36), default=new_agent_public_id, nullable=False)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32), default=AgentMessageStatus.COMPLETED.value, nullable=False, index=True
    )
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_response_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    conversation: Mapped[AgentConversation] = relationship(back_populates="messages")


class AgentRun(TenantScoped, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_agent_runs_public_id"),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_agent_runs_status",
        ),
        Index(
            "ix_agent_runs_workspace_owner_conversation_created",
            "workspace_id",
            "owner_user_id",
            "conversation_id",
            "created_at",
        ),
        Index(
            "ix_agent_runs_workspace_status_updated",
            "workspace_id",
            "status",
            "updated_at",
        ),
        Index(
            "uq_agent_runs_workspace_owner_trigger",
            "workspace_id",
            "owner_user_id",
            "trigger_message_id",
            unique=True,
            postgresql_where=text("trigger_message_id IS NOT NULL"),
            sqlite_where=text("trigger_message_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    public_id: Mapped[str] = mapped_column(String(36), default=new_agent_public_id, nullable=False)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trigger_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default=AgentRunStatus.QUEUED.value, nullable=False, index=True
    )
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_context_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_schema_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_micros: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    conversation: Mapped[AgentConversation] = relationship(back_populates="runs")
    tool_calls: Mapped[list[AgentToolCall]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class AgentToolCall(TenantScoped, Base):
    __tablename__ = "agent_tool_calls"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_agent_tool_calls_public_id"),
        UniqueConstraint(
            "workspace_id",
            "run_id",
            "sequence",
            name="uq_agent_tool_calls_workspace_run_sequence",
        ),
        CheckConstraint(
            "operation_kind IN ('read', 'write', 'external_action')",
            name="ck_agent_tool_calls_operation_kind",
        ),
        CheckConstraint(
            "capability IN ('standard', 'purchasing')",
            name="ck_agent_tool_calls_capability",
        ),
        CheckConstraint(
            "status IN ('proposed', 'running', 'completed', 'failed', 'blocked')",
            name="ck_agent_tool_calls_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    public_id: Mapped[str] = mapped_column(String(36), default=new_agent_public_id, nullable=False)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tool_version: Mapped[str] = mapped_column(String(32), default="1.0", nullable=False)
    operation_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    capability: Mapped[str] = mapped_column(String(32), default="standard", nullable=False)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=AgentToolCallStatus.PROPOSED.value, nullable=False, index=True
    )
    arguments_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result_metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[AgentRun] = relationship(back_populates="tool_calls")


class AgentActionProposal(TenantScoped, Base):
    __tablename__ = "agent_action_proposals"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_agent_action_proposals_public_id"),
        UniqueConstraint(
            "workspace_id",
            "owner_user_id",
            "idempotency_key",
            name="uq_agent_action_proposals_workspace_owner_idempotency",
        ),
        CheckConstraint(
            "operation_kind IN ('write', 'external_action')",
            name="ck_agent_action_proposals_operation_kind",
        ),
        CheckConstraint(
            "capability IN ('standard', 'purchasing')",
            name="ck_agent_action_proposals_capability",
        ),
        CheckConstraint(
            "status IN ('awaiting_confirmation', 'confirmed', 'executing', 'completed', "
            "'cancelled', 'expired', 'failed', 'ambiguous')",
            name="ck_agent_action_proposals_status",
        ),
        CheckConstraint("version >= 1", name="ck_agent_action_proposals_version"),
        Index(
            "uq_agent_action_proposals_active_fingerprint",
            "workspace_id",
            "owner_user_id",
            "action_fingerprint",
            unique=True,
            postgresql_where=text("status IN ('awaiting_confirmation', 'confirmed', 'executing')"),
            sqlite_where=text("status IN ('awaiting_confirmation', 'confirmed', 'executing')"),
        ),
        Index(
            "ix_agent_action_proposals_workspace_owner_status_expires",
            "workspace_id",
            "owner_user_id",
            "status",
            "expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    public_id: Mapped[str] = mapped_column(String(36), default=new_agent_public_id, nullable=False)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tool_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_tool_calls.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tool_version: Mapped[str] = mapped_column(String(32), default="1.0", nullable=False)
    operation_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    capability: Mapped[str] = mapped_column(String(32), default="standard", nullable=False)
    normalized_parameters_json: Mapped[dict] = mapped_column(
        JSON, nullable=False, active_history=True
    )
    parameters_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    action_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        default=AgentActionProposalStatus.AWAITING_CONFIRMATION.value,
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    confirmed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result_metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped[AgentConversation] = relationship(back_populates="proposals")

    @validates(
        "workspace_id",
        "owner_user_id",
        "conversation_id",
        "run_id",
        "tool_call_id",
        "tool_name",
        "tool_version",
        "operation_kind",
        "capability",
        "normalized_parameters_json",
        "parameters_hash",
        "action_fingerprint",
        "preview_json",
        "idempotency_key",
        "expires_at",
    )
    def _preserve_action_snapshot(self, key: str, value):
        current = self.__dict__.get(key, _AGENT_FIELD_UNSET)
        if current is not _AGENT_FIELD_UNSET and current != value:
            raise ValueError("Agent action proposal snapshot is immutable")
        return value


class AgentReviewSessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AgentReviewSession(TenantScoped, Base):
    """Agent-owned one-by-one review queue.

    The referenced ReviewItem/transaction/receipt rows remain the domain
    authority; this row owns only the Agent's own queue position, so a
    review session can resume across refresh/reopen and revalidate each
    candidate against current state before acting on it.
    """

    __tablename__ = "agent_review_sessions"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_agent_review_sessions_public_id"),
        CheckConstraint(
            "status IN ('active', 'completed', 'cancelled')",
            name="ck_agent_review_sessions_status",
        ),
        Index(
            "uq_agent_review_sessions_active_owner_conversation",
            "workspace_id",
            "owner_user_id",
            "conversation_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index(
            "ix_agent_review_sessions_workspace_owner_status",
            "workspace_id",
            "owner_user_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    public_id: Mapped[str] = mapped_column(String(36), default=new_agent_public_id, nullable=False)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default=AgentReviewSessionStatus.ACTIVE.value,
        nullable=False,
        index=True,
    )
    # Ordered queue frozen at session start: [{review_item_public_id, kind,
    # source_type, source_entity_id, source_fingerprint}, ...]
    candidates_json: Mapped[list] = mapped_column(JSON, nullable=False)
    current_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Outcomes recorded so far: [{source_entity_id, kind, outcome,
    # proposal_public_id}, ...] where outcome in personal|split|skipped|stale
    results_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped[AgentConversation] = relationship()

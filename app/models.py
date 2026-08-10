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
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class TransactionStatus(StrEnum):
    ASK_USER = "ask_user"
    PERSONAL = "personal"
    SHARED_DRAFT = "shared_draft"
    APPROVED = "approved"
    POSTED = "posted"
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


class PlaidItem(Base):
    __tablename__ = "plaid_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
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


class ExpenseTransaction(Base):
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
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    plaid_item: Mapped[PlaidItem] = relationship(back_populates="transactions")


class AIInterpretationMemory(Base):
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


class TelegramSession(Base):
    __tablename__ = "telegram_sessions"
    __table_args__ = (UniqueConstraint("chat_id", "user_id", name="uq_telegram_session_chat_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    state_data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HouseholdItem(Base):
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


class Errand(Base):
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


class ErrandPlan(Base):
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


class SavedLocation(Base):
    __tablename__ = "saved_locations"

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


class PreferredPlace(Base):
    __tablename__ = "preferred_places"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    preference_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    canonical_name: Mapped[str] = mapped_column(String(255))
    full_address: Mapped[str] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider_place_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

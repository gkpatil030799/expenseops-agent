from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
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
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_telegram_session_chat_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    state_data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

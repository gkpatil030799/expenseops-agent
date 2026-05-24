"""initial schema

Revision ID: 20260523_0001
Revises:
Create Date: 2026-05-23

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260523_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plaid_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.String(length=128), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("institution_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_plaid_items_item_id"), "plaid_items", ["item_id"], unique=True)

    op.create_table(
        "ai_interpretation_memories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("original_message", sa.Text(), nullable=False),
        sa.Column("failure_reason", sa.String(length=64), nullable=False),
        sa.Column("final_action", sa.String(length=64), nullable=False),
        sa.Column("final_group_id", sa.String(length=128), nullable=True),
        sa.Column("final_group_name", sa.String(length=255), nullable=True),
        sa.Column("final_participants", sa.JSON(), nullable=False),
        sa.Column("final_split_mode", sa.String(length=64), nullable=True),
        sa.Column("payer_included", sa.Boolean(), nullable=False),
        sa.Column("custom_values", sa.JSON(), nullable=True),
        sa.Column("correction_type", sa.String(length=64), nullable=False),
        sa.Column("merchant", sa.String(length=255), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("usage_count", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "telegram_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("state_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "user_id", name="uq_telegram_session_chat_user"),
    )
    op.create_index(
        op.f("ix_telegram_sessions_chat_id"),
        "telegram_sessions",
        ["chat_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_telegram_sessions_user_id"),
        "telegram_sessions",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "expense_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plaid_transaction_id", sa.String(length=128), nullable=False),
        sa.Column("plaid_item_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=True),
        sa.Column("merchant_name", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("iso_currency_code", sa.String(length=8), nullable=False),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("authorized_date", sa.Date(), nullable=True),
        sa.Column("pending", sa.Boolean(), nullable=False),
        sa.Column("payment_channel", sa.String(length=64), nullable=True),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("raw_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("agent_question", sa.Text(), nullable=True),
        sa.Column("splitwise_expense_id", sa.String(length=128), nullable=True),
        sa.Column("splitwise_payload_json", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plaid_item_id"], ["plaid_items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plaid_transaction_id", name="uq_plaid_transaction_id"),
    )
    op.create_index(
        op.f("ix_expense_transactions_plaid_item_id"),
        "expense_transactions",
        ["plaid_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_expense_transactions_plaid_transaction_id"),
        "expense_transactions",
        ["plaid_transaction_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_expense_transactions_splitwise_expense_id"),
        "expense_transactions",
        ["splitwise_expense_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_expense_transactions_splitwise_expense_id"),
        table_name="expense_transactions",
    )
    op.drop_index(
        op.f("ix_expense_transactions_plaid_transaction_id"),
        table_name="expense_transactions",
    )
    op.drop_index(
        op.f("ix_expense_transactions_plaid_item_id"),
        table_name="expense_transactions",
    )
    op.drop_table("expense_transactions")
    op.drop_index(op.f("ix_telegram_sessions_user_id"), table_name="telegram_sessions")
    op.drop_index(op.f("ix_telegram_sessions_chat_id"), table_name="telegram_sessions")
    op.drop_table("telegram_sessions")
    op.drop_table("ai_interpretation_memories")
    op.drop_index(op.f("ix_plaid_items_item_id"), table_name="plaid_items")
    op.drop_table("plaid_items")

"""add durable financial operation journal

Revision ID: 20260812_0017
Revises: 20260812_0016
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260812_0017"
down_revision: str | None = "20260812_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("expense_transactions") as batch_op:
        batch_op.add_column(
            sa.Column("splitwise_generation", sa.Integer(), nullable=False, server_default="0")
        )
    op.create_table(
        "financial_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer()),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("provider_object_id", sa.String(128)),
        sa.Column("request_json", sa.JSON()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["expense_transactions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "workspace_id",
            "transaction_id",
            "action",
            "generation",
            name="uq_financial_operation_transaction_action_generation",
        ),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_financial_operation_idempotency"
        ),
    )
    for column in (
        "workspace_id",
        "transaction_id",
        "actor_user_id",
        "action",
        "idempotency_key",
        "state",
        "lease_expires_at",
        "provider_object_id",
        "correlation_id",
    ):
        op.create_index(f"ix_financial_operations_{column}", "financial_operations", [column])


def downgrade() -> None:
    op.drop_table("financial_operations")
    with op.batch_alter_table("expense_transactions") as batch_op:
        batch_op.drop_column("splitwise_generation")

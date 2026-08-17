"""add proactive attention preferences and delivery dedupe

Revision ID: 20260817_0032
Revises: 20260817_0031
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260817_0032"
down_revision: str | None = "20260817_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "proactive_attention_preferences",
    "proactive_attention_deliveries",
)


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    workspace = "NULLIF(current_setting('expenseops.workspace_id', true), '')::integer"
    for table in TENANT_TABLES:
        op.execute(sa.text(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE public."{table}" FORCE ROW LEVEL SECURITY'))
        op.execute(
            sa.text(
                f'CREATE POLICY expenseops_workspace_isolation ON public."{table}" '
                f"USING (workspace_id = {workspace}) "
                f"WITH CHECK (workspace_id = {workspace})"
            )
        )


def upgrade() -> None:
    op.create_table(
        "proactive_attention_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("categories_json", sa.JSON(), nullable=False),
        sa.Column("in_app_enabled", sa.Boolean(), nullable=False),
        sa.Column("telegram_enabled", sa.Boolean(), nullable=False),
        sa.Column("delivery_mode", sa.String(length=16), nullable=False),
        sa.Column("quiet_start_hour", sa.Integer(), nullable=False),
        sa.Column("quiet_end_hour", sa.Integer(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("max_alerts_per_day", sa.Integer(), nullable=False),
        sa.Column("cooldown_minutes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "workspace_id", "user_id", name="uq_attention_preferences_workspace_user"
        ),
    )
    op.create_index(
        "ix_proactive_attention_preferences_workspace_id",
        "proactive_attention_preferences",
        ["workspace_id"],
    )
    op.create_index(
        "ix_proactive_attention_preferences_user_id",
        "proactive_attention_preferences",
        ["user_id"],
    )
    op.create_table(
        "proactive_attention_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("attention_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            "channel",
            "dedupe_key",
            name="uq_attention_delivery_dedupe",
        ),
    )
    op.create_index(
        "ix_proactive_attention_deliveries_workspace_id",
        "proactive_attention_deliveries",
        ["workspace_id"],
    )
    op.create_index(
        "ix_proactive_attention_deliveries_user_id",
        "proactive_attention_deliveries",
        ["user_id"],
    )
    op.create_index(
        "ix_proactive_attention_deliveries_fingerprint",
        "proactive_attention_deliveries",
        ["fingerprint"],
    )
    op.create_index(
        "ix_proactive_attention_deliveries_status",
        "proactive_attention_deliveries",
        ["status"],
    )
    op.create_index(
        "ix_attention_delivery_user_delivered",
        "proactive_attention_deliveries",
        ["workspace_id", "user_id", "delivered_at"],
    )
    _enable_rls()


def downgrade() -> None:
    op.drop_table("proactive_attention_deliveries")
    op.drop_table("proactive_attention_preferences")

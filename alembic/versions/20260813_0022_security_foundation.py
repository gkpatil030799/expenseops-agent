"""add database tenant and shared rate-limit enforcement

Revision ID: 20260813_0022
Revises: 20260812_0021
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0022"
down_revision: str | None = "20260812_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "gmail_accounts", "telegram_identities", "splitwise_integrations",
    "workspace_invitations", "telegram_link_codes", "plaid_items",
    "expense_transactions", "financial_operations", "outbox_events",
    "scheduled_job_leases", "ai_interpretation_memories", "telegram_sessions",
    "household_items", "purchase_receipts", "household_item_acquisitions",
    "replenishment_model_versions", "replenishment_predictions",
    "replenishment_feedback", "replenishment_job_runs", "promotion_messages",
    "promotion_offers", "promotion_digest_runs", "promotion_settings",
    "gmail_sync_checkpoints", "errands", "errand_plans", "saved_locations",
    "preferred_places",
)


def upgrade() -> None:
    op.create_table(
        "rate_limit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("key", "window_started_at", name="uq_rate_limit_key_window"),
    )
    op.create_index("ix_rate_limit_events_key", "rate_limit_events", ["key"])
    op.create_index(
        "ix_rate_limit_events_window_started_at", "rate_limit_events", ["window_started_at"]
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TENANT_TABLES:
        op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
        op.execute(sa.text(
            f'CREATE POLICY expenseops_workspace_isolation ON "{table}" '
            "USING (current_setting('expenseops.bypass_rls', true) = 'on' OR "
            "workspace_id = NULLIF(current_setting('expenseops.workspace_id', true), '')::integer) "
            "WITH CHECK (current_setting('expenseops.bypass_rls', true) = 'on' OR "
            "workspace_id = NULLIF(current_setting('expenseops.workspace_id', true), '')::integer)"
        ))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in TENANT_TABLES:
            op.execute(
                sa.text(f'DROP POLICY IF EXISTS expenseops_workspace_isolation ON "{table}"')
            )
            op.execute(sa.text(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY'))
    op.drop_index("ix_rate_limit_events_window_started_at", table_name="rate_limit_events")
    op.drop_index("ix_rate_limit_events_key", table_name="rate_limit_events")
    op.drop_table("rate_limit_events")

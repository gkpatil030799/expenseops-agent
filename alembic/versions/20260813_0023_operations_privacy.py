"""add privacy lifecycle and operational indexes

Revision ID: 20260813_0023
Revises: 20260813_0022
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0023"
down_revision: str | None = "20260813_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("deletion_requested_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.create_table(
        "data_consents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("policy_version", sa.String(32), nullable=False, server_default="2026-08-13"),
        sa.Column("granted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            "purpose",
            name="uq_data_consent_workspace_user_purpose",
        ),
    )
    op.create_index("ix_data_consents_workspace_id", "data_consents", ["workspace_id"])
    op.create_index("ix_data_consents_user_id", "data_consents", ["user_id"])
    op.create_index(
        "ix_data_consents_user_purpose", "data_consents", ["user_id", "purpose"]
    )
    indexes = (
        ("ix_auth_sessions_user_expiry", "auth_sessions", ["user_id", "revoked_at", "expires_at"]),
        ("ix_audit_events_workspace_created", "audit_events", ["workspace_id", "created_at"]),
        (
            "ix_audit_events_workspace_type_created",
            "audit_events",
            ["workspace_id", "event_type", "created_at"],
        ),
        (
            "ix_plaid_webhook_processing_received",
            "plaid_webhook_events",
            ["processing_status", "received_at"],
        ),
        (
            "ix_expense_transactions_workspace_review",
            "expense_transactions",
            ["workspace_id", "status", "pending", "date"],
        ),
        (
            "ix_financial_operations_workspace_state_updated",
            "financial_operations",
            ["workspace_id", "state", "updated_at"],
        ),
        (
            "ix_outbox_events_workspace_delivery",
            "outbox_events",
            ["workspace_id", "state", "available_at"],
        ),
        (
            "ix_purchase_receipts_workspace_queue",
            "purchase_receipts",
            ["workspace_id", "parse_status", "created_at"],
        ),
        (
            "ix_promotion_messages_workspace_processing",
            "promotion_messages",
            ["workspace_id", "parse_status", "received_at"],
        ),
        (
            "ix_promotion_offers_workspace_active_rank",
            "promotion_offers",
            ["workspace_id", "status", "saved", "score"],
        ),
    )
    for name, table, columns in indexes:
        op.create_index(name, table, columns)
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text('ALTER TABLE "data_consents" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text('ALTER TABLE "data_consents" FORCE ROW LEVEL SECURITY'))
        op.execute(sa.text(
            'CREATE POLICY expenseops_workspace_isolation ON "data_consents" '
            "USING (current_setting('expenseops.bypass_rls', true) = 'on' OR "
            "workspace_id = NULLIF(current_setting('expenseops.workspace_id', true), '')::integer) "
            "WITH CHECK (current_setting('expenseops.bypass_rls', true) = 'on' OR "
            "workspace_id = NULLIF(current_setting('expenseops.workspace_id', true), '')::integer)"
        ))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text('DROP POLICY IF EXISTS expenseops_workspace_isolation ON "data_consents"')
        )
        op.execute(sa.text('ALTER TABLE "data_consents" DISABLE ROW LEVEL SECURITY'))
    for name, table in reversed((
        ("ix_auth_sessions_user_expiry", "auth_sessions"),
        ("ix_audit_events_workspace_created", "audit_events"),
        ("ix_audit_events_workspace_type_created", "audit_events"),
        ("ix_plaid_webhook_processing_received", "plaid_webhook_events"),
        ("ix_expense_transactions_workspace_review", "expense_transactions"),
        ("ix_financial_operations_workspace_state_updated", "financial_operations"),
        ("ix_outbox_events_workspace_delivery", "outbox_events"),
        ("ix_purchase_receipts_workspace_queue", "purchase_receipts"),
        ("ix_promotion_messages_workspace_processing", "promotion_messages"),
        ("ix_promotion_offers_workspace_active_rank", "promotion_offers"),
    )):
        op.drop_index(name, table_name=table)
    op.drop_index("ix_data_consents_user_purpose", table_name="data_consents")
    op.drop_index("ix_data_consents_user_id", table_name="data_consents")
    op.drop_index("ix_data_consents_workspace_id", table_name="data_consents")
    op.drop_table("data_consents")
    op.drop_column("users", "deleted_at")
    op.drop_column("users", "deletion_requested_at")

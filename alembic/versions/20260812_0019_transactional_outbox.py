"""add transactional outbox

Revision ID: 20260812_0019
Revises: 20260812_0018
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260812_0019"
down_revision: str | None = "20260812_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("expense_transactions") as batch_op:
        batch_op.add_column(
            sa.Column("review_notification_queued_at", sa.DateTime(timezone=True))
        )
    with op.batch_alter_table("gmail_sync_checkpoints") as batch_op:
        batch_op.add_column(sa.Column("incremental_page_token", sa.Text()))
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("aggregate_type", sa.String(100), nullable=False),
        sa.Column("aggregate_id", sa.String(128), nullable=False),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "workspace_id", "dedupe_key", name="uq_outbox_events_workspace_dedupe"
        ),
    )
    for column in (
        "workspace_id",
        "event_type",
        "aggregate_id",
        "state",
        "available_at",
        "lease_expires_at",
        "correlation_id",
    ):
        op.create_index(f"ix_outbox_events_{column}", "outbox_events", [column])


def downgrade() -> None:
    op.drop_table("outbox_events")
    with op.batch_alter_table("gmail_sync_checkpoints") as batch_op:
        batch_op.drop_column("incremental_page_token")
    with op.batch_alter_table("expense_transactions") as batch_op:
        batch_op.drop_column("review_notification_queued_at")

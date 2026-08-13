"""deduplicate Telegram webhook updates

Revision ID: 20260812_0020
Revises: 20260812_0019
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260812_0020"
down_revision: str | None = "20260812_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_webhook_updates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("update_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer()),
        sa.Column("user_id", sa.Integer()),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="processing"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("update_id", name="uq_telegram_webhook_update_id"),
    )
    for column in ("update_id", "workspace_id", "user_id", "state", "lease_expires_at"):
        op.create_index(
            f"ix_telegram_webhook_updates_{column}",
            "telegram_webhook_updates",
            [column],
        )
    op.create_table(
        "scheduled_job_leases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("job_name", sa.String(100), nullable=False),
        sa.Column("lease_token", sa.String(64), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "workspace_id", "job_name", name="uq_scheduled_job_lease_workspace_job"
        ),
    )
    for column in ("workspace_id", "job_name", "lease_expires_at"):
        op.create_index(
            f"ix_scheduled_job_leases_{column}", "scheduled_job_leases", [column]
        )


def downgrade() -> None:
    op.drop_table("scheduled_job_leases")
    op.drop_table("telegram_webhook_updates")

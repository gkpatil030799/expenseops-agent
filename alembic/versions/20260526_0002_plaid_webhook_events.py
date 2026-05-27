"""add plaid webhook events

Revision ID: 20260526_0002
Revises: 20260523_0001
Create Date: 2026-05-26

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260526_0002"
down_revision: str | None = "20260523_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "plaid_webhook_events"

    if table_name not in inspector.get_table_names():
        op.create_table(
            table_name,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("webhook_type", sa.String(length=64), nullable=False),
            sa.Column("webhook_code", sa.String(length=128), nullable=False),
            sa.Column("plaid_item_id", sa.String(length=128), nullable=True),
            sa.Column("item_id", sa.Integer(), nullable=True),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("processing_status", sa.String(length=32), nullable=False),
            sa.Column("sync_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sync_completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("payload_hash", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["item_id"], ["plaid_items.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
    for index_name, columns in {
        op.f("ix_plaid_webhook_events_item_id"): ["item_id"],
        op.f("ix_plaid_webhook_events_plaid_item_id"): ["plaid_item_id"],
        op.f("ix_plaid_webhook_events_received_at"): ["received_at"],
        op.f("ix_plaid_webhook_events_webhook_code"): ["webhook_code"],
        op.f("ix_plaid_webhook_events_webhook_type"): ["webhook_type"],
    }.items():
        if index_name not in existing_indexes:
            op.create_index(index_name, table_name, columns, unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_plaid_webhook_events_webhook_type"), table_name="plaid_webhook_events")
    op.drop_index(op.f("ix_plaid_webhook_events_webhook_code"), table_name="plaid_webhook_events")
    op.drop_index(op.f("ix_plaid_webhook_events_received_at"), table_name="plaid_webhook_events")
    op.drop_index(op.f("ix_plaid_webhook_events_plaid_item_id"), table_name="plaid_webhook_events")
    op.drop_index(op.f("ix_plaid_webhook_events_item_id"), table_name="plaid_webhook_events")
    op.drop_table("plaid_webhook_events")

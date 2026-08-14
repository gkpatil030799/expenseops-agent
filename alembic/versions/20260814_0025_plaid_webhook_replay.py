"""deduplicate verified Plaid webhook deliveries

Revision ID: 20260814_0025
Revises: 20260814_0024
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260814_0025"
down_revision: str | None = "20260814_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plaid_webhook_events",
        sa.Column("delivery_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ux_plaid_webhook_events_delivery_fingerprint",
        "plaid_webhook_events",
        ["delivery_fingerprint"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ux_plaid_webhook_events_delivery_fingerprint",
        table_name="plaid_webhook_events",
    )
    op.drop_column("plaid_webhook_events", "delivery_fingerprint")

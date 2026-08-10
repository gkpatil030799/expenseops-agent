"""persist promotion backfill continuation

Revision ID: 20260810_0011
Revises: 20260809_0010
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260810_0011"
down_revision: str | None = "20260809_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("gmail_sync_checkpoints", sa.Column("backfill_page_token", sa.Text()))
    # Earlier builds marked a capped first page complete. Re-run a bounded backfill safely;
    # message-level idempotency prevents duplicate offers.
    op.execute(
        sa.text(
            "UPDATE gmail_sync_checkpoints "
            "SET initial_backfill_complete = false "
            "WHERE account_key LIKE :account_suffix"
        ).bindparams(account_suffix="%:promotions")
    )


def downgrade() -> None:
    op.drop_column("gmail_sync_checkpoints", "backfill_page_token")

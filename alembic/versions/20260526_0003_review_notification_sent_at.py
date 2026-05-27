"""add review notification sent marker

Revision ID: 20260526_0003
Revises: 20260526_0002
Create Date: 2026-05-26

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260526_0003"
down_revision: str | None = "20260526_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("expense_transactions")}
    if "review_notification_sent_at" not in columns:
        op.add_column(
            "expense_transactions",
            sa.Column("review_notification_sent_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("expense_transactions")}
    if "review_notification_sent_at" in columns:
        op.drop_column("expense_transactions", "review_notification_sent_at")

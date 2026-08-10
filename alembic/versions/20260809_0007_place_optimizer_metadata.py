"""add automatic place optimizer metadata

Revision ID: 20260809_0007
Revises: 20260809_0006
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260809_0007"
down_revision: str | None = "20260809_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("errands", sa.Column("resolved_open_now", sa.Boolean()))
    op.add_column("errands", sa.Column("resolved_opening_hours", sa.JSON()))
    op.add_column("errands", sa.Column("place_resolution_method", sa.String(length=32)))


def downgrade() -> None:
    op.drop_column("errands", "place_resolution_method")
    op.drop_column("errands", "resolved_opening_hours")
    op.drop_column("errands", "resolved_open_now")

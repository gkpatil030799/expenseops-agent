"""bind route plans to their planning inputs

Revision ID: 20260812_0021
Revises: 20260812_0020
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260812_0021"
down_revision: str | None = "20260812_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("errand_plans") as batch_op:
        batch_op.add_column(sa.Column("input_fingerprint", sa.String(64)))
        batch_op.add_column(sa.Column("input_snapshot", sa.JSON()))
        batch_op.create_index("ix_errand_plans_input_fingerprint", ["input_fingerprint"])


def downgrade() -> None:
    with op.batch_alter_table("errand_plans") as batch_op:
        batch_op.drop_index("ix_errand_plans_input_fingerprint")
        batch_op.drop_column("input_snapshot")
        batch_op.drop_column("input_fingerprint")

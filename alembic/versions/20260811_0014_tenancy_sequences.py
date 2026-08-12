"""advance tenancy bootstrap sequences

Revision ID: 20260811_0014
Revises: 20260811_0013
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0014"
down_revision: str | None = "20260811_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in ("users", "workspaces", "workspace_memberships"):
        op.execute(
            sa.text(
                "SELECT setval(pg_get_serial_sequence(:table_name, 'id'), "
                "COALESCE((SELECT MAX(id) FROM " + table_name + "), 1), true)"
            ).bindparams(table_name=table_name)
        )


def downgrade() -> None:
    # Sequence advancement is intentionally not reversed: lowering a sequence
    # could cause primary-key collisions with existing tenant records.
    pass

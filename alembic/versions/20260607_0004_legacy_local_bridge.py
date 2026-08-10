"""bridge a legacy local-only schema revision

Revision ID: 20260607_0004
Revises: 20260526_0003
Create Date: 2026-06-07

This revision identifier existed in an older local database but its feature is
not part of the current repository. Keeping a no-op bridge preserves that
database's migration history without creating or dropping legacy tables.
"""

from collections.abc import Sequence

revision: str = "20260607_0004"
down_revision: str | None = "20260526_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

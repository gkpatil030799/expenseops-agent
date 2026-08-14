"""enforce one active Telegram identity per app user

Revision ID: 20260814_0024
Revises: 20260813_0023
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260814_0024"
down_revision: str | None = "20260813_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    is_postgresql = op.get_bind().dialect.name == "postgresql"
    if is_postgresql:
        # Migration 0022 FORCE-enabled RLS on this table. Enable the existing
        # policy bypass only for this migration transaction so a non-superuser
        # migration role can see and repair duplicate rows before the unique
        # index is built. ``is_local=true`` prevents the setting from escaping
        # the transaction even if the migration aborts.
        op.execute(
            sa.text("SELECT set_config('expenseops.bypass_rls', 'on', true)")
        )
        # The old application may still accept Telegram connections while this
        # migration runs. Block concurrent inserts/updates until the duplicate
        # repair and partial unique index are both committed, closing the gap in
        # which a new duplicate could otherwise make CREATE UNIQUE INDEX fail.
        op.execute(sa.text("LOCK TABLE telegram_identities IN SHARE ROW EXCLUSIVE MODE"))
    # Preserve the newest active binding if legacy/concurrent requests created
    # more than one. Older rows remain for auditability but can no longer route
    # messages or notifications.
    op.execute(
        sa.text(
            """
            UPDATE telegram_identities AS older
            SET enabled = false
            WHERE older.enabled = true
              AND EXISTS (
                SELECT 1
                FROM telegram_identities AS newer
                WHERE newer.user_id = older.user_id
                  AND newer.enabled = true
                  AND newer.id > older.id
              )
            """
        )
    )
    if is_postgresql:
        op.execute(
            sa.text("SELECT set_config('expenseops.bypass_rls', 'off', true)")
        )
    op.create_index(
        "uq_telegram_identity_user_active",
        "telegram_identities",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("enabled IS TRUE"),
        sqlite_where=sa.text("enabled = 1"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_telegram_identity_user_active",
        table_name="telegram_identities",
    )

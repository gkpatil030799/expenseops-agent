"""add idempotent read-only agent turns

Revision ID: 20260815_0027
Revises: 20260815_0026
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260815_0027"
down_revision: str | None = "20260815_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    is_postgresql = op.get_bind().dialect.name == "postgresql"
    if is_postgresql:
        # Migration 0026 FORCE-enabled RLS for agent tables. Use its existing
        # transaction-local bypass so duplicate repair sees every workspace.
        op.execute(sa.text("SELECT set_config('expenseops.bypass_rls', 'on', true)"))
        # Close the race between repairing legacy duplicates and creating the
        # unique index while an older application revision is still serving.
        op.execute(sa.text("LOCK TABLE agent_runs IN SHARE ROW EXCLUSIVE MODE"))

    # Day 1 did not enforce one run per trigger at the database boundary, so a
    # retried/concurrent request may already have produced duplicates. Preserve
    # the earliest run as canonical and retain every later run for auditability;
    # only detach the duplicate trigger link, which is nullable by design.
    op.execute(
        sa.text(
            """
            UPDATE agent_runs AS later_run
            SET trigger_message_id = NULL
            WHERE later_run.trigger_message_id IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM agent_runs AS canonical_run
                  WHERE canonical_run.workspace_id = later_run.workspace_id
                    AND canonical_run.owner_user_id = later_run.owner_user_id
                    AND canonical_run.trigger_message_id = later_run.trigger_message_id
                    AND canonical_run.id < later_run.id
              )
            """
        )
    )

    if is_postgresql:
        op.execute(sa.text("SELECT set_config('expenseops.bypass_rls', 'off', true)"))

    predicate = sa.text("trigger_message_id IS NOT NULL")
    op.create_index(
        "uq_agent_runs_workspace_owner_trigger",
        "agent_runs",
        ["workspace_id", "owner_user_id", "trigger_message_id"],
        unique=True,
        postgresql_where=predicate,
        sqlite_where=predicate,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_agent_runs_workspace_owner_trigger",
        table_name="agent_runs",
    )

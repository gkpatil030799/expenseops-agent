"""remove obsolete global tenant uniqueness

Revision ID: 20260812_0015
Revises: 20260811_0014
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260812_0015"
down_revision: str | None = "20260811_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_gmail_accounts_enabled", "gmail_accounts", ["enabled"], unique=False)
    op.create_index(
        "ix_telegram_identities_telegram_user_id",
        "telegram_identities",
        ["telegram_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_identities_chat_id",
        "telegram_identities",
        ["chat_id"],
        unique=False,
    )
    op.create_index(
        "ix_workspace_invitations_invited_by_user_id",
        "workspace_invitations",
        ["invited_by_user_id"],
        unique=False,
    )
    # Migration 0012 replaced the old unique constraints, but these separately
    # created legacy indexes remained and continued to enforce global uniqueness.
    op.drop_index("ix_preferred_places_preference_key", table_name="preferred_places")
    op.create_index(
        "ix_preferred_places_preference_key",
        "preferred_places",
        ["preference_key"],
        unique=False,
    )
    op.drop_index("ix_replenishment_job_runs_run_key", table_name="replenishment_job_runs")
    op.create_index(
        "ix_replenishment_job_runs_run_key",
        "replenishment_job_runs",
        ["run_key"],
        unique=False,
    )
    op.drop_index(
        "ix_replenishment_model_versions_version",
        table_name="replenishment_model_versions",
    )
    op.create_index(
        "ix_replenishment_model_versions_version",
        "replenishment_model_versions",
        ["version"],
        unique=False,
    )
    op.drop_index(
        "uq_replenishment_single_active_model",
        table_name="replenishment_model_versions",
    )
    op.create_index(
        "uq_replenishment_workspace_single_active_model",
        "replenishment_model_versions",
        ["workspace_id", "status"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_invitations_invited_by_user_id",
        table_name="workspace_invitations",
    )
    op.drop_index("ix_telegram_identities_chat_id", table_name="telegram_identities")
    op.drop_index(
        "ix_telegram_identities_telegram_user_id",
        table_name="telegram_identities",
    )
    op.drop_index("ix_gmail_accounts_enabled", table_name="gmail_accounts")
    op.drop_index(
        "uq_replenishment_workspace_single_active_model",
        table_name="replenishment_model_versions",
    )
    op.create_index(
        "uq_replenishment_single_active_model",
        "replenishment_model_versions",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    op.drop_index(
        "ix_replenishment_model_versions_version",
        table_name="replenishment_model_versions",
    )
    op.create_index(
        "ix_replenishment_model_versions_version",
        "replenishment_model_versions",
        ["version"],
        unique=True,
    )
    op.drop_index("ix_replenishment_job_runs_run_key", table_name="replenishment_job_runs")
    op.create_index(
        "ix_replenishment_job_runs_run_key",
        "replenishment_job_runs",
        ["run_key"],
        unique=True,
    )
    op.drop_index("ix_preferred_places_preference_key", table_name="preferred_places")
    op.create_index(
        "ix_preferred_places_preference_key",
        "preferred_places",
        ["preference_key"],
        unique=True,
    )

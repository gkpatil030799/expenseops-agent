"""add the agent-owned transaction review session queue

Revision ID: 20260819_0035
Revises: 20260818_0034
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260819_0035"
down_revision: str | None = "20260818_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    workspace = "NULLIF(current_setting('expenseops.workspace_id', true), '')::integer"
    op.execute(sa.text('ALTER TABLE public."agent_review_sessions" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text('ALTER TABLE public."agent_review_sessions" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            'CREATE POLICY expenseops_workspace_isolation ON public."agent_review_sessions" '
            f"USING (workspace_id = {workspace}) WITH CHECK (workspace_id = {workspace})"
        )
    )


def upgrade() -> None:
    op.create_table(
        "agent_review_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("candidates_json", sa.JSON(), nullable=False),
        sa.Column("current_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("results_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('active', 'completed', 'cancelled')",
            name="ck_agent_review_sessions_status",
        ),
        sa.UniqueConstraint("public_id", name="uq_agent_review_sessions_public_id"),
    )
    for column in ("workspace_id", "owner_user_id", "conversation_id", "status"):
        op.create_index(
            f"ix_agent_review_sessions_{column}", "agent_review_sessions", [column]
        )
    op.create_index(
        "ix_agent_review_sessions_workspace_owner_status",
        "agent_review_sessions",
        ["workspace_id", "owner_user_id", "status"],
    )
    active_state = sa.text("status = 'active'")
    op.create_index(
        "uq_agent_review_sessions_active_owner_conversation",
        "agent_review_sessions",
        ["workspace_id", "owner_user_id", "conversation_id"],
        unique=True,
        postgresql_where=active_state,
        sqlite_where=active_state,
    )
    _enable_rls()


def downgrade() -> None:
    op.drop_table("agent_review_sessions")

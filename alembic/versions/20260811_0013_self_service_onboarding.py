"""add managed auth and self-service onboarding

Revision ID: 20260811_0013
Revises: 20260811_0012
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0013"
down_revision: str | None = "20260811_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("plaid_items") as batch_op:
        batch_op.alter_column("access_token_encrypted", nullable=True)
        batch_op.add_column(
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.create_index("ix_plaid_items_enabled", ["enabled"])
    op.create_table(
        "auth_identities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("provider_subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(255)),
        sa.Column("avatar_url", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "provider", "provider_subject", name="uq_auth_identity_provider_subject"
        ),
    )
    op.create_index("ix_auth_identities_user_id", "auth_identities", ["user_id"])
    op.create_index("ix_auth_identities_provider", "auth_identities", ["provider"])
    op.create_index("ix_auth_identities_email", "auth_identities", ["email"])
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("selected_workspace_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["selected_workspace_id"], ["workspaces.id"]),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    for column in ("user_id", "selected_workspace_id", "token_hash", "expires_at"):
        op.create_index(f"ix_auth_sessions_{column}", "auth_sessions", [column])
    op.create_table(
        "workspace_invitations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="member"),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invited_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"]),
        sa.UniqueConstraint("token_hash", name="uq_workspace_invitation_token_hash"),
    )
    for column in ("workspace_id", "email", "token_hash", "status", "expires_at"):
        op.create_index(f"ix_workspace_invitations_{column}", "workspace_invitations", [column])
    op.create_table(
        "oauth_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.Integer()),
        sa.Column("user_id", sa.Integer()),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("payload_encrypted", sa.Text()),
        sa.Column("redirect_after", sa.String(500)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("state_hash", name="uq_oauth_state_hash"),
    )
    for column in ("provider", "workspace_id", "user_id", "state_hash", "expires_at"):
        op.create_index(f"ix_oauth_states_{column}", "oauth_states", [column])
    op.create_table(
        "telegram_link_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("code_hash", name="uq_telegram_link_code_hash"),
    )
    for column in ("workspace_id", "user_id", "code_hash", "expires_at"):
        op.create_index(f"ix_telegram_link_codes_{column}", "telegram_link_codes", [column])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer()),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(100)),
        sa.Column("resource_id", sa.String(128)),
        sa.Column("request_id", sa.String(64)),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    for column in ("workspace_id", "user_id", "event_type", "request_id"):
        op.create_index(f"ix_audit_events_{column}", "audit_events", [column])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("telegram_link_codes")
    op.drop_table("oauth_states")
    op.drop_table("workspace_invitations")
    op.drop_table("auth_sessions")
    op.drop_table("auth_identities")
    with op.batch_alter_table("plaid_items") as batch_op:
        batch_op.drop_index("ix_plaid_items_enabled")
        batch_op.drop_column("enabled")
        batch_op.alter_column("access_token_encrypted", nullable=False)

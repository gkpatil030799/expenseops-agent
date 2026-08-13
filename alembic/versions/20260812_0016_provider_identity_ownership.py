"""add verified provider identity ownership

Revision ID: 20260812_0016
Revises: 20260812_0015
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260812_0016"
down_revision: str | None = "20260812_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("plaid_items") as batch_op:
        batch_op.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("ownership_verified_at", sa.DateTime(timezone=True)))
        batch_op.create_foreign_key(
            "fk_plaid_items_owner_user_id_users",
            "users",
            ["owner_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_plaid_items_owner_user_id", ["owner_user_id"])

    with op.batch_alter_table("splitwise_integrations") as batch_op:
        batch_op.drop_constraint("uq_splitwise_workspace", type_="unique")
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("splitwise_user_id", sa.String(128)))
        batch_op.add_column(sa.Column("display_name", sa.String(255)))
        batch_op.add_column(sa.Column("email", sa.String(320)))
        batch_op.add_column(sa.Column("verified_at", sa.DateTime(timezone=True)))
        batch_op.create_foreign_key(
            "fk_splitwise_integrations_user_id_users",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # Existing workspace credentials predate actor attribution. Assign them to
    # the workspace creator for continuity, but leave verified_at empty until
    # that person reconnects and Splitwise confirms the external identity.
    op.execute(
        sa.text(
            "UPDATE splitwise_integrations SET user_id = "
            "(SELECT created_by_user_id FROM workspaces "
            "WHERE workspaces.id = splitwise_integrations.workspace_id)"
        )
    )
    with op.batch_alter_table("splitwise_integrations") as batch_op:
        batch_op.alter_column("user_id", nullable=False)
        batch_op.create_index("ix_splitwise_integrations_user_id", ["user_id"])
        batch_op.create_unique_constraint(
            "uq_splitwise_workspace_user", ["workspace_id", "user_id"]
        )
        batch_op.create_unique_constraint(
            "uq_splitwise_workspace_external_user", ["workspace_id", "splitwise_user_id"]
        )


def downgrade() -> None:
    # A workspace may now contain several personal Splitwise connections. Keep
    # the oldest connection per workspace before restoring the legacy shape.
    op.execute(
        sa.text(
            "DELETE FROM splitwise_integrations WHERE id NOT IN "
            "(SELECT MIN(id) FROM splitwise_integrations GROUP BY workspace_id)"
        )
    )
    with op.batch_alter_table("splitwise_integrations") as batch_op:
        batch_op.drop_constraint("uq_splitwise_workspace_external_user", type_="unique")
        batch_op.drop_constraint("uq_splitwise_workspace_user", type_="unique")
        batch_op.drop_index("ix_splitwise_integrations_user_id")
        batch_op.drop_constraint("fk_splitwise_integrations_user_id_users", type_="foreignkey")
        batch_op.drop_column("verified_at")
        batch_op.drop_column("email")
        batch_op.drop_column("display_name")
        batch_op.drop_column("splitwise_user_id")
        batch_op.drop_column("user_id")
        batch_op.create_unique_constraint("uq_splitwise_workspace", ["workspace_id"])

    with op.batch_alter_table("plaid_items") as batch_op:
        batch_op.drop_index("ix_plaid_items_owner_user_id")
        batch_op.drop_constraint("fk_plaid_items_owner_user_id_users", type_="foreignkey")
        batch_op.drop_column("ownership_verified_at")
        batch_op.drop_column("owner_user_id")

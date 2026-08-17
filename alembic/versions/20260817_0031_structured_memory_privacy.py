"""make interpretation memory structured and user-owned

Revision ID: 20260817_0031
Revises: 20260817_0030
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260817_0031"
down_revision: str | None = "20260817_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ai_interpretation_memories") as batch:
        batch.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_ai_interpretation_memories_owner_user_id_users",
            "users",
            ["owner_user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index(
            "ix_ai_interpretation_memories_owner_user_id", ["owner_user_id"], unique=False
        )

    # Raw chat text is not structured memory. Existing rows have no reliable
    # per-user owner, so retain them as legacy workspace evidence while
    # irreversibly replacing the transcript with a deterministic label.
    op.execute(
        sa.text(
            "UPDATE ai_interpretation_memories "
            "SET original_message = substr("
            "COALESCE(NULLIF(trim(merchant), ''), 'This merchant') || ': ' || "
            "CASE WHEN final_action = 'personal' "
            "THEN 'usually personal' ELSE 'usually shared' END, 1, 500)"
        )
    )


def downgrade() -> None:
    # The privacy scrub is intentionally irreversible: a downgrade must never
    # recreate deleted chat transcripts.
    with op.batch_alter_table("ai_interpretation_memories") as batch:
        batch.drop_index("ix_ai_interpretation_memories_owner_user_id")
        batch.drop_constraint(
            "fk_ai_interpretation_memories_owner_user_id_users", type_="foreignkey"
        )
        batch.drop_column("owner_user_id")

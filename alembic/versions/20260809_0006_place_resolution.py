"""add explicit errand place resolution

Revision ID: 20260809_0006
Revises: 20260809_0005
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260809_0006"
down_revision: str | None = "20260809_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "errands",
        sa.Column(
            "place_resolution_status",
            sa.String(length=32),
            nullable=False,
            server_default="unresolved",
        ),
    )
    op.add_column("errands", sa.Column("resolved_place_name", sa.String(255)))
    op.add_column("errands", sa.Column("resolved_place_address", sa.Text()))
    op.add_column("errands", sa.Column("resolved_latitude", sa.Float()))
    op.add_column("errands", sa.Column("resolved_longitude", sa.Float()))
    op.add_column("errands", sa.Column("resolved_provider_place_id", sa.String(255)))
    op.create_index(
        op.f("ix_errands_place_resolution_status"),
        "errands",
        ["place_resolution_status"],
    )
    op.create_table(
        "preferred_places",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("preference_key", sa.String(length=255), nullable=False),
        sa.Column("canonical_name", sa.String(length=255), nullable=False),
        sa.Column("full_address", sa.Text(), nullable=False),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("provider_place_id", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("preference_key"),
    )
    op.create_index(
        op.f("ix_preferred_places_preference_key"),
        "preferred_places",
        ["preference_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_preferred_places_preference_key"),
        table_name="preferred_places",
    )
    op.drop_table("preferred_places")
    op.drop_index(op.f("ix_errands_place_resolution_status"), table_name="errands")
    for name in (
        "resolved_provider_place_id",
        "resolved_longitude",
        "resolved_latitude",
        "resolved_place_address",
        "resolved_place_name",
        "place_resolution_status",
    ):
        op.drop_column("errands", name)

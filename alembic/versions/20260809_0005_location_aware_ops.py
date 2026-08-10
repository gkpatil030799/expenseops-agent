"""add location-aware Household Ops planning

Revision ID: 20260809_0005
Revises: 20260809_0004
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260809_0005"
down_revision: str | None = "20260809_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "saved_locations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("location_type", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_saved_locations_location_type"),
        "saved_locations",
        ["location_type"],
    )

    for name, column_type in (
        ("travel_duration_minutes", sa.Integer()),
        ("distance_meters", sa.Integer()),
        ("baseline_travel_duration_minutes", sa.Integer()),
        ("incremental_travel_duration_minutes", sa.Integer()),
        ("available_minutes", sa.Integer()),
        ("primary_destination", sa.Text()),
        ("final_destination", sa.Text()),
        ("recommendation_summary", sa.JSON()),
    ):
        op.add_column("errand_plans", sa.Column(name, column_type, nullable=True))
    op.add_column(
        "errand_plans",
        sa.Column("planning_mode", sa.String(length=32), nullable=False, server_default="standard"),
    )
    op.create_index(op.f("ix_errand_plans_planning_mode"), "errand_plans", ["planning_mode"])

    op.create_table(
        "errand_plan_stop_household_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stop_id", sa.Integer(), nullable=False),
        sa.Column("household_item_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["household_item_id"], ["household_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stop_id"], ["errand_plan_stops.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stop_id", "household_item_id", name="uq_plan_stop_household_item"),
    )
    op.create_index(
        op.f("ix_errand_plan_stop_household_items_household_item_id"),
        "errand_plan_stop_household_items",
        ["household_item_id"],
    )
    op.create_index(
        op.f("ix_errand_plan_stop_household_items_stop_id"),
        "errand_plan_stop_household_items",
        ["stop_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_errand_plan_stop_household_items_stop_id"),
        table_name="errand_plan_stop_household_items",
    )
    op.drop_index(
        op.f("ix_errand_plan_stop_household_items_household_item_id"),
        table_name="errand_plan_stop_household_items",
    )
    op.drop_table("errand_plan_stop_household_items")
    op.drop_index(op.f("ix_errand_plans_planning_mode"), table_name="errand_plans")
    for name in (
        "planning_mode",
        "recommendation_summary",
        "final_destination",
        "primary_destination",
        "available_minutes",
        "incremental_travel_duration_minutes",
        "baseline_travel_duration_minutes",
        "distance_meters",
        "travel_duration_minutes",
    ):
        op.drop_column("errand_plans", name)
    op.drop_index(op.f("ix_saved_locations_location_type"), table_name="saved_locations")
    op.drop_table("saved_locations")

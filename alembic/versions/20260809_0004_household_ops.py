"""add Household Ops errands, replenishment, and plans

Revision ID: 20260809_0004
Revises: 20260607_0004
Create Date: 2026-08-09

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260809_0004"
down_revision: str | None = "20260607_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "household_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.String(length=64), nullable=True),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("preferred_place_name", sa.String(length=255), nullable=True),
        sa.Column("preferred_place_address", sa.Text(), nullable=True),
        sa.Column("replenishment_mode", sa.String(length=32), nullable=False),
        sa.Column("cadence_days", sa.Integer(), nullable=False),
        sa.Column("last_acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_household_items_enabled"), "household_items", ["enabled"])
    op.create_index(op.f("ix_household_items_name"), "household_items", ["name"])
    op.create_index(
        op.f("ix_household_items_replenishment_mode"),
        "household_items",
        ["replenishment_mode"],
    )

    op.create_table(
        "errands",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("errand_type", sa.String(length=32), nullable=False),
        sa.Column("place_name", sa.String(length=255), nullable=True),
        sa.Column("place_address", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_duration_minutes", sa.Integer(), nullable=True),
        sa.Column("priority", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("included_in_next_plan", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_errands_errand_type"), "errands", ["errand_type"])
    op.create_index(op.f("ix_errands_included_in_next_plan"), "errands", ["included_in_next_plan"])
    op.create_index(op.f("ix_errands_place_name"), "errands", ["place_name"])
    op.create_index(op.f("ix_errands_priority"), "errands", ["priority"])
    op.create_index(op.f("ix_errands_source"), "errands", ["source"])
    op.create_index(op.f("ix_errands_status"), "errands", ["status"])

    op.create_table(
        "errand_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("planned_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("base_location", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("routing_provider", sa.String(length=64), nullable=False),
        sa.Column("routing_is_optimized", sa.Boolean(), nullable=False),
        sa.Column("route_url", sa.Text(), nullable=True),
        sa.Column("estimated_stop_minutes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_errand_plans_status"), "errand_plans", ["status"])

    op.create_table(
        "errand_household_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("errand_id", sa.Integer(), nullable=False),
        sa.Column("household_item_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["errand_id"], ["errands.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["household_item_id"], ["household_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("errand_id", "household_item_id", name="uq_errand_household_item"),
    )
    op.create_index(
        op.f("ix_errand_household_items_errand_id"),
        "errand_household_items",
        ["errand_id"],
    )
    op.create_index(
        op.f("ix_errand_household_items_household_item_id"),
        "errand_household_items",
        ["household_item_id"],
    )

    op.create_table(
        "errand_plan_stops",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("stop_order", sa.Integer(), nullable=False),
        sa.Column("place_name", sa.String(length=255), nullable=False),
        sa.Column("place_address", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["errand_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "stop_order", name="uq_plan_stop_order"),
    )
    op.create_index(op.f("ix_errand_plan_stops_plan_id"), "errand_plan_stops", ["plan_id"])

    op.create_table(
        "errand_plan_stop_errands",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stop_id", sa.Integer(), nullable=False),
        sa.Column("errand_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["errand_id"], ["errands.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stop_id"], ["errand_plan_stops.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stop_id", "errand_id", name="uq_plan_stop_errand"),
    )
    op.create_index(
        op.f("ix_errand_plan_stop_errands_errand_id"),
        "errand_plan_stop_errands",
        ["errand_id"],
    )
    op.create_index(
        op.f("ix_errand_plan_stop_errands_stop_id"),
        "errand_plan_stop_errands",
        ["stop_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_errand_plan_stop_errands_stop_id"),
        table_name="errand_plan_stop_errands",
    )
    op.drop_index(
        op.f("ix_errand_plan_stop_errands_errand_id"),
        table_name="errand_plan_stop_errands",
    )
    op.drop_table("errand_plan_stop_errands")
    op.drop_index(op.f("ix_errand_plan_stops_plan_id"), table_name="errand_plan_stops")
    op.drop_table("errand_plan_stops")
    op.drop_index(
        op.f("ix_errand_household_items_household_item_id"),
        table_name="errand_household_items",
    )
    op.drop_index(op.f("ix_errand_household_items_errand_id"), table_name="errand_household_items")
    op.drop_table("errand_household_items")
    op.drop_index(op.f("ix_errand_plans_status"), table_name="errand_plans")
    op.drop_table("errand_plans")
    op.drop_index(op.f("ix_errands_status"), table_name="errands")
    op.drop_index(op.f("ix_errands_source"), table_name="errands")
    op.drop_index(op.f("ix_errands_priority"), table_name="errands")
    op.drop_index(op.f("ix_errands_place_name"), table_name="errands")
    op.drop_index(op.f("ix_errands_included_in_next_plan"), table_name="errands")
    op.drop_index(op.f("ix_errands_errand_type"), table_name="errands")
    op.drop_table("errands")
    op.drop_index(op.f("ix_household_items_replenishment_mode"), table_name="household_items")
    op.drop_index(op.f("ix_household_items_name"), table_name="household_items")
    op.drop_index(op.f("ix_household_items_enabled"), table_name="household_items")
    op.drop_table("household_items")

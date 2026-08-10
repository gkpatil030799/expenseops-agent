"""strengthen replenishment learning reliability

Revision ID: 20260809_0009
Revises: 20260809_0008
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260809_0009"
down_revision: str | None = "20260809_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("purchase_receipt_items", "household_item_acquisitions"):
        op.add_column(table, sa.Column("normalized_quantity", sa.Float()))
        op.add_column(table, sa.Column("normalized_unit", sa.String(length=64)))
        op.add_column(table, sa.Column("package_size", sa.Float()))
        op.add_column(table, sa.Column("quantity_confidence", sa.Float()))
    op.add_column("household_item_acquisitions", sa.Column("configured_cadence_days", sa.Integer()))

    op.add_column("household_item_aliases", sa.Column("voided_at", sa.DateTime(timezone=True)))
    op.add_column(
        "household_item_acquisitions", sa.Column("logical_purchase_key", sa.String(length=64))
    )
    op.add_column(
        "household_item_acquisitions",
        sa.Column("user_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_household_item_acquisitions_user_confirmed",
        "household_item_acquisitions",
        ["user_confirmed"],
    )
    with op.batch_alter_table("household_item_acquisitions") as batch_op:
        batch_op.add_column(sa.Column("supersedes_acquisition_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_household_item_acquisitions_supersedes",
            "household_item_acquisitions",
            ["supersedes_acquisition_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_household_item_acquisitions_logical_purchase_key",
        "household_item_acquisitions",
        ["logical_purchase_key"],
        unique=True,
    )
    op.add_column(
        "replenishment_predictions",
        sa.Column(
            "confidence_level", sa.String(length=32), nullable=False, server_default="insufficient"
        ),
    )
    op.create_index(
        "ix_replenishment_predictions_confidence_level",
        "replenishment_predictions",
        ["confidence_level"],
    )
    op.create_index(
        "uq_replenishment_single_active_model",
        "replenishment_model_versions",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_replenishment_single_active_model",
        table_name="replenishment_model_versions",
    )
    op.drop_index(
        "ix_replenishment_predictions_confidence_level",
        table_name="replenishment_predictions",
    )
    op.drop_column("replenishment_predictions", "confidence_level")
    op.drop_index(
        "ix_household_item_acquisitions_logical_purchase_key",
        table_name="household_item_acquisitions",
    )
    with op.batch_alter_table("household_item_acquisitions") as batch_op:
        batch_op.drop_constraint(
            "fk_household_item_acquisitions_supersedes",
            type_="foreignkey",
        )
        batch_op.drop_column("supersedes_acquisition_id")
    op.drop_column("household_item_acquisitions", "logical_purchase_key")
    op.drop_index(
        "ix_household_item_acquisitions_user_confirmed",
        table_name="household_item_acquisitions",
    )
    op.drop_column("household_item_acquisitions", "user_confirmed")
    op.drop_column("household_item_aliases", "voided_at")
    op.drop_column("household_item_acquisitions", "configured_cadence_days")
    for table in ("household_item_acquisitions", "purchase_receipt_items"):
        op.drop_column(table, "quantity_confidence")
        op.drop_column(table, "package_size")
        op.drop_column(table, "normalized_unit")
        op.drop_column(table, "normalized_quantity")

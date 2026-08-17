"""add truthful receipt learning state

Revision ID: 20260817_0030
Revises: 20260815_0029
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260817_0030"
down_revision: str | None = "20260815_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("household_items") as batch:
        batch.alter_column("cadence_days", existing_type=sa.Integer(), nullable=True)
        batch.add_column(
            sa.Column(
                "cadence_source",
                sa.String(length=32),
                nullable=False,
                server_default="configured",
            )
        )
        batch.create_check_constraint(
            "ck_household_items_cadence_state",
            "(cadence_source = 'learning' AND cadence_days IS NULL) OR "
            "(cadence_source IN ('configured', 'observed', 'adaptive') "
            "AND cadence_days IS NOT NULL AND cadence_days > 0)",
        )

    with op.batch_alter_table("purchase_receipt_items") as batch:
        batch.add_column(sa.Column("classification", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("classification_confidence", sa.Float(), nullable=True))
        batch.add_column(sa.Column("canonical_name", sa.String(length=255), nullable=True))
        batch.create_index(
            "ix_purchase_receipt_items_classification",
            ["classification"],
            unique=False,
        )
        batch.create_check_constraint(
            "ck_purchase_receipt_items_classification",
            "classification IS NULL OR classification IN ("
            "'replenishable_household', 'perishable_grocery', 'routine_consumption', "
            "'dining_or_experience', 'one_time_purchase', 'non_product_line', 'uncertain')",
        )
        batch.create_check_constraint(
            "ck_purchase_receipt_items_classification_confidence",
            "classification_confidence IS NULL OR "
            "(classification_confidence >= 0 AND classification_confidence <= 1)",
        )


def downgrade() -> None:
    # Learning items cannot be truthfully represented by the old mandatory
    # cadence schema. Fail closed instead of inventing a number on downgrade.
    connection = op.get_bind()
    learning_count = connection.execute(
        sa.text("SELECT count(*) FROM household_items WHERE cadence_days IS NULL")
    ).scalar_one()
    if learning_count:
        raise RuntimeError(
            "Cannot downgrade 0030 while cadence-free learning household items exist"
        )
    with op.batch_alter_table("purchase_receipt_items") as batch:
        batch.drop_constraint("ck_purchase_receipt_items_classification_confidence", type_="check")
        batch.drop_constraint("ck_purchase_receipt_items_classification", type_="check")
        batch.drop_index("ix_purchase_receipt_items_classification")
        batch.drop_column("canonical_name")
        batch.drop_column("classification_confidence")
        batch.drop_column("classification")
    with op.batch_alter_table("household_items") as batch:
        batch.drop_constraint("ck_household_items_cadence_state", type_="check")
        batch.drop_column("cadence_source")
        batch.alter_column("cadence_days", existing_type=sa.Integer(), nullable=False)

"""add replenishment learning pipeline

Revision ID: 20260809_0008
Revises: 20260809_0007
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260809_0008"
down_revision: str | None = "20260809_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "purchase_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_external_id", sa.String(255), nullable=False),
        sa.Column("content_sha256", sa.String(64)),
        sa.Column("merchant_raw", sa.String(255)),
        sa.Column("merchant_normalized", sa.String(255)),
        sa.Column("purchased_at", sa.DateTime(timezone=True)),
        sa.Column("subtotal_cents", sa.Integer()),
        sa.Column("tax_cents", sa.Integer()),
        sa.Column("total_cents", sa.Integer()),
        sa.Column("currency", sa.String(8), nullable=False, server_default="USD"),
        sa.Column(
            "transaction_id",
            sa.Integer(),
            sa.ForeignKey("expense_transactions.id", ondelete="SET NULL"),
        ),
        sa.Column("parse_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("parse_confidence", sa.Float()),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("ignored_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "source_external_id", name="uq_receipt_source_external"),
    )
    for name, columns in (
        ("ix_purchase_receipts_source", ["source"]),
        ("ix_purchase_receipts_content_sha256", ["content_sha256"]),
        ("ix_purchase_receipts_merchant_normalized", ["merchant_normalized"]),
        ("ix_purchase_receipts_transaction_id", ["transaction_id"]),
        ("ix_purchase_receipts_parse_status", ["parse_status"]),
    ):
        op.create_index(name, "purchase_receipts", columns)

    op.create_table(
        "purchase_receipt_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "receipt_id",
            sa.Integer(),
            sa.ForeignKey("purchase_receipts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("raw_name", sa.String(500), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("quantity", sa.Float()),
        sa.Column("unit", sa.String(64)),
        sa.Column("unit_price_cents", sa.Integer()),
        sa.Column("line_total_cents", sa.Integer()),
        sa.Column("brand", sa.String(255)),
        sa.Column("category", sa.String(128)),
        sa.Column(
            "household_item_id",
            sa.Integer(),
            sa.ForeignKey("household_items.id", ondelete="SET NULL"),
        ),
        sa.Column("match_status", sa.String(32), nullable=False, server_default="unmatched"),
        sa.Column("match_confidence", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, columns in (
        ("ix_purchase_receipt_items_receipt_id", ["receipt_id"]),
        ("ix_purchase_receipt_items_normalized_name", ["normalized_name"]),
        ("ix_purchase_receipt_items_household_item_id", ["household_item_id"]),
        ("ix_purchase_receipt_items_match_status", ["match_status"]),
    ):
        op.create_index(name, "purchase_receipt_items", columns)

    op.create_table(
        "household_item_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "household_item_id",
            sa.Integer(),
            sa.ForeignKey("household_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("merchant_normalized", sa.String(255), nullable=False, server_default=""),
        sa.Column("raw_pattern", sa.String(500), nullable=False),
        sa.Column("normalized_alias", sa.String(255), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("source", sa.String(32), nullable=False, server_default="user"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "household_item_id",
            "merchant_normalized",
            "normalized_alias",
            name="uq_household_alias_item_merchant_name",
        ),
    )
    op.create_index(
        "ix_household_item_aliases_household_item_id",
        "household_item_aliases",
        ["household_item_id"],
    )
    op.create_index(
        "ix_household_item_aliases_normalized_alias", "household_item_aliases", ["normalized_alias"]
    )

    op.create_table(
        "replenishment_model_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("algorithm", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("training_rows", sa.Integer(), nullable=False),
        sa.Column("training_start_at", sa.DateTime(timezone=True)),
        sa.Column("training_end_at", sa.DateTime(timezone=True)),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("artifact_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("version", name="uq_replenishment_model_versions_version"),
    )
    op.create_index(
        "ix_replenishment_model_versions_version",
        "replenishment_model_versions",
        ["version"],
        unique=True,
    )
    op.create_index(
        "ix_replenishment_model_versions_status", "replenishment_model_versions", ["status"]
    )

    op.create_table(
        "household_item_acquisitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "household_item_id",
            sa.Integer(),
            sa.ForeignKey("household_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantity", sa.Float()),
        sa.Column("unit", sa.String(64)),
        sa.Column("merchant_normalized", sa.String(255)),
        sa.Column(
            "receipt_item_id",
            sa.Integer(),
            sa.ForeignKey("purchase_receipt_items.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "transaction_id",
            sa.Integer(),
            sa.ForeignKey("expense_transactions.id", ondelete="SET NULL"),
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("voided_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("receipt_item_id", name="uq_acquisition_receipt_item"),
    )
    for name, columns in (
        ("ix_household_item_acquisitions_household_item_id", ["household_item_id"]),
        ("ix_household_item_acquisitions_acquired_at", ["acquired_at"]),
        ("ix_household_item_acquisitions_receipt_item_id", ["receipt_item_id"]),
        ("ix_household_item_acquisitions_transaction_id", ["transaction_id"]),
        ("ix_household_item_acquisitions_source", ["source"]),
        ("ix_household_item_acquisitions_confirmed", ["confirmed"]),
    ):
        op.create_index(name, "household_item_acquisitions", columns)

    op.create_table(
        "replenishment_predictions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "household_item_id",
            sa.Integer(),
            sa.ForeignKey("household_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("predicted_need_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("predicted_days_remaining", sa.Float(), nullable=False),
        sa.Column("due_score", sa.Float(), nullable=False),
        sa.Column("method", sa.String(64), nullable=False),
        sa.Column(
            "model_version_id",
            sa.Integer(),
            sa.ForeignKey("replenishment_model_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("feature_snapshot", sa.JSON(), nullable=False),
        sa.Column("actual_next_acquisition_at", sa.DateTime(timezone=True)),
        sa.Column("error_days", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, columns in (
        ("ix_replenishment_predictions_household_item_id", ["household_item_id"]),
        ("ix_replenishment_predictions_generated_at", ["generated_at"]),
        ("ix_replenishment_predictions_predicted_need_at", ["predicted_need_at"]),
        ("ix_replenishment_predictions_method", ["method"]),
    ):
        op.create_index(name, "replenishment_predictions", columns)

    op.create_table(
        "replenishment_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "household_item_id",
            sa.Integer(),
            sa.ForeignKey("household_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "prediction_id",
            sa.Integer(),
            sa.ForeignKey("replenishment_predictions.id", ondelete="SET NULL"),
        ),
        sa.Column("feedback_type", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_replenishment_feedback_household_item_id",
        "replenishment_feedback",
        ["household_item_id"],
    )
    op.create_index(
        "ix_replenishment_feedback_feedback_type", "replenishment_feedback", ["feedback_type"]
    )
    op.create_index(
        "ix_replenishment_feedback_occurred_at", "replenishment_feedback", ["occurred_at"]
    )

    op.create_table(
        "replenishment_job_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_key", sa.String(100), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("dataset_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "model_version_id",
            sa.Integer(),
            sa.ForeignKey("replenishment_model_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_key", name="uq_replenishment_job_runs_run_key"),
    )
    op.create_index(
        "ix_replenishment_job_runs_run_key", "replenishment_job_runs", ["run_key"], unique=True
    )
    op.create_index("ix_replenishment_job_runs_status", "replenishment_job_runs", ["status"])


def downgrade() -> None:
    for table in (
        "replenishment_job_runs",
        "replenishment_feedback",
        "replenishment_predictions",
        "household_item_acquisitions",
        "replenishment_model_versions",
        "household_item_aliases",
        "purchase_receipt_items",
        "purchase_receipts",
    ):
        op.drop_table(table)

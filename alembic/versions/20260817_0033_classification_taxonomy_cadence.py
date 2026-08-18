"""add canonical classification taxonomy, ledger, reconciliation, and cadence provenance

Revision ID: 20260817_0033
Revises: 20260817_0032
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260817_0033"
down_revision: str | None = "20260817_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIRECT_TENANT_TABLES = (
    "classification_subcategories",
    "classification_concepts",
    "classification_concept_aliases",
    "classification_settings",
    "classification_decisions",
)

PARENTS = (
    "'food_dining', 'household_home', 'lifestyle_shopping', 'personal_care', "
    "'health', 'transportation', 'travel', 'entertainment', 'subscriptions', "
    "'pets', 'education_office', 'services', 'fees_taxes_discounts', "
    "'other_uncertain'"
)
ACTIVITIES = (
    "'grocery', 'household_consumable', 'routine_consumption', 'one_time_purchase', "
    "'restaurant_meal', 'coffee_beverage', "
    "'food_delivery', 'nightlife', 'apparel', 'electronics', 'pharmacy', "
    "'personal_care', 'beauty', 'pet_supply', 'automotive', 'transportation', "
    "'travel', 'entertainment', 'subscription', 'education_office', 'service', "
    "'tax', 'tip', 'discount', 'fee', 'refund', 'non_product', 'uncertain'"
)
REPLENISHMENT = "'replenishable', 'potentially_replenishable', 'not_replenishable', 'uncertain'"
BANDS = "'low', 'medium', 'high'"
AUTHORITIES = (
    "'fallback', 'model_evidence', 'provider_evidence', 'receipt_evidence', "
    "'deterministic_exact', 'confirmed_alias', 'user_correction'"
)
STATES = "'provisional', 'final', 'corrected'"

_WORKSPACE_SETTING = "NULLIF(current_setting('expenseops.workspace_id', true), '')::integer"


def _create_taxonomy_tables() -> None:
    op.create_table(
        "classification_subcategories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("parent_category", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=128), nullable=False),
        sa.Column(
            "source", sa.String(length=32), nullable=False, server_default="deterministic_exact"
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("merged_into_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_classification_subcategory_workspace_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "parent_category",
            "normalized_name",
            name="uq_classification_subcategory_workspace_parent_name",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "merged_into_id"],
            ["classification_subcategories.workspace_id", "classification_subcategories.id"],
            name="fk_classification_subcategory_merged_workspace",
        ),
        sa.CheckConstraint(
            f"parent_category IN ({PARENTS})", name="ck_classification_subcategory_parent"
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_classification_subcategory_confidence"
        ),
        sa.CheckConstraint(
            f"source IN ({AUTHORITIES})", name="ck_classification_subcategory_source"
        ),
        sa.CheckConstraint(
            "merged_into_id IS NULL OR merged_into_id != id",
            name="ck_classification_subcategory_not_self_merged",
        ),
    )
    op.create_index(
        "ix_classification_subcategories_workspace_id",
        "classification_subcategories",
        ["workspace_id"],
    )
    op.create_index(
        "ix_classification_subcategories_parent_category",
        "classification_subcategories",
        ["parent_category"],
    )
    op.create_index(
        "ix_classification_subcategories_normalized_name",
        "classification_subcategories",
        ["normalized_name"],
    )
    op.create_index(
        "ix_classification_subcategories_merged_into_id",
        "classification_subcategories",
        ["merged_into_id"],
    )

    op.create_table(
        "classification_concepts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("subcategory_id", sa.Integer(), nullable=True),
        sa.Column("parent_category", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("item_activity_type", sa.String(length=64), nullable=False),
        sa.Column("replenishment_eligibility", sa.String(length=32), nullable=False),
        sa.Column(
            "source", sa.String(length=32), nullable=False, server_default="deterministic_exact"
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("merged_into_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_classification_concept_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id", "normalized_name", name="uq_classification_concept_workspace_name"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "subcategory_id"],
            ["classification_subcategories.workspace_id", "classification_subcategories.id"],
            name="fk_classification_concept_subcategory_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "merged_into_id"],
            ["classification_concepts.workspace_id", "classification_concepts.id"],
            name="fk_classification_concept_merged_workspace",
        ),
        sa.CheckConstraint(
            f"parent_category IN ({PARENTS})", name="ck_classification_concept_parent"
        ),
        sa.CheckConstraint(
            f"item_activity_type IN ({ACTIVITIES})", name="ck_classification_concept_activity"
        ),
        sa.CheckConstraint(
            f"replenishment_eligibility IN ({REPLENISHMENT})",
            name="ck_classification_concept_replenishment_eligibility",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_classification_concept_confidence"
        ),
        sa.CheckConstraint(f"source IN ({AUTHORITIES})", name="ck_classification_concept_source"),
        sa.CheckConstraint(
            "merged_into_id IS NULL OR merged_into_id != id",
            name="ck_classification_concept_not_self_merged",
        ),
    )
    for column in (
        "workspace_id",
        "subcategory_id",
        "parent_category",
        "normalized_name",
        "item_activity_type",
        "replenishment_eligibility",
        "merged_into_id",
    ):
        op.create_index(f"ix_classification_concepts_{column}", "classification_concepts", [column])

    op.create_table(
        "classification_concept_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("concept_id", sa.Integer(), nullable=False),
        sa.Column("merchant_normalized", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("raw_pattern", sa.String(length=500), nullable=False),
        sa.Column("normalized_alias", sa.String(length=255), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="confirmed_alias"),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "concept_id"],
            ["classification_concepts.workspace_id", "classification_concepts.id"],
            name="fk_classification_concept_alias_workspace",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_classification_concept_alias_confidence"
        ),
        sa.CheckConstraint(
            f"source IN ({AUTHORITIES})", name="ck_classification_concept_alias_source"
        ),
    )
    op.create_index(
        "ix_classification_concept_aliases_workspace_id",
        "classification_concept_aliases",
        ["workspace_id"],
    )
    op.create_index(
        "ix_classification_concept_aliases_concept_id",
        "classification_concept_aliases",
        ["concept_id"],
    )
    op.create_index(
        "ix_classification_concept_aliases_normalized_alias",
        "classification_concept_aliases",
        ["normalized_alias"],
    )
    op.create_index(
        "uq_classification_concept_alias_active",
        "classification_concept_aliases",
        ["workspace_id", "merchant_normalized", "normalized_alias"],
        unique=True,
        postgresql_where=sa.text("voided_at IS NULL"),
        sqlite_where=sa.text("voided_at IS NULL"),
    )

    op.create_table(
        "classification_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("autonomous_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "category_creation_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "cadence_estimation_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("grace_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("receipt_item_backfill_cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transaction_backfill_cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "receipt_match_backfill_cursor", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_backfill_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workspace_id", name="uq_classification_settings_workspace"),
        sa.CheckConstraint(
            "grace_hours >= 1 AND grace_hours <= 168", name="ck_classification_settings_grace_hours"
        ),
        sa.CheckConstraint(
            "receipt_item_backfill_cursor >= 0 AND transaction_backfill_cursor >= 0 "
            "AND receipt_match_backfill_cursor >= 0",
            name="ck_classification_settings_backfill_cursors",
        ),
    )
    op.create_index(
        "ix_classification_settings_workspace_id", "classification_settings", ["workspace_id"]
    )


def _extend_household_items() -> None:
    with op.batch_alter_table("household_items") as batch:
        batch.drop_constraint("ck_household_items_cadence_state", type_="check")
        batch.add_column(
            sa.Column("cadence_confidence", sa.Float(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("cadence_min_days", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cadence_max_days", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "cadence_provenance_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
            )
        )
        batch.add_column(
            sa.Column("cadence_estimated_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("canonical_key", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("classification_concept_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "spending_parent_category",
                sa.String(length=64),
                nullable=False,
                server_default="other_uncertain",
            )
        )
        batch.add_column(
            sa.Column(
                "replenishment_eligibility",
                sa.String(length=32),
                nullable=False,
                server_default="replenishable",
            )
        )
        batch.add_column(
            sa.Column("classification_confidence", sa.Float(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column(
                "classification_provenance_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.add_column(sa.Column("merged_into_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_unique_constraint("uq_household_item_workspace_id", ["workspace_id", "id"])
        batch.create_foreign_key(
            "fk_household_item_classification_concept_workspace",
            "classification_concepts",
            ["workspace_id", "classification_concept_id"],
            ["workspace_id", "id"],
        )
        batch.create_foreign_key(
            "fk_household_item_merged_workspace",
            "household_items",
            ["workspace_id", "merged_into_id"],
            ["workspace_id", "id"],
        )
        batch.create_check_constraint(
            "ck_household_item_not_self_merged",
            "merged_into_id IS NULL OR merged_into_id != id",
        )
        batch.create_check_constraint(
            "ck_household_items_cadence_state",
            "(cadence_source = 'learning' AND cadence_days IS NULL) OR "
            "(cadence_source IN ('configured', 'category_prior', 'model_prior', "
            "'observed', 'quantity_adjusted', 'adaptive') "
            "AND cadence_days IS NOT NULL AND cadence_days > 0)",
        )
        batch.create_check_constraint(
            "ck_household_items_cadence_confidence",
            "cadence_confidence >= 0 AND cadence_confidence <= 1",
        )
        batch.create_check_constraint(
            "ck_household_items_cadence_range",
            "(cadence_min_days IS NULL AND cadence_max_days IS NULL) OR "
            "(cadence_days IS NOT NULL AND cadence_min_days > 0 AND "
            "cadence_max_days > 0 AND cadence_min_days <= cadence_days AND "
            "cadence_days <= cadence_max_days)",
        )
        batch.create_check_constraint(
            "ck_household_items_spending_parent", f"spending_parent_category IN ({PARENTS})"
        )
        batch.create_check_constraint(
            "ck_household_items_replenishment_eligibility",
            f"replenishment_eligibility IN ({REPLENISHMENT})",
        )
        batch.create_check_constraint(
            "ck_household_items_classification_confidence",
            "classification_confidence >= 0 AND classification_confidence <= 1",
        )
    op.execute(
        sa.text(
            "UPDATE household_items SET "
            "cadence_confidence = CASE "
            "WHEN cadence_source = 'configured' THEN 1.0 "
            "WHEN cadence_source IN ('observed', 'adaptive') THEN 0.8 ELSE 0.0 END, "
            "cadence_min_days = cadence_days, cadence_max_days = cadence_days, "
            "cadence_estimated_at = CASE WHEN cadence_source IN ('observed', 'adaptive') "
            "THEN updated_at ELSE NULL END"
        )
    )
    op.create_index("ix_household_items_canonical_key", "household_items", ["canonical_key"])
    op.create_index(
        "ix_household_items_classification_concept_id",
        "household_items",
        ["classification_concept_id"],
    )
    op.create_index(
        "ix_household_items_spending_parent_category",
        "household_items",
        ["spending_parent_category"],
    )
    op.create_index(
        "ix_household_items_replenishment_eligibility",
        "household_items",
        ["replenishment_eligibility"],
    )
    op.create_index("ix_household_items_merged_into_id", "household_items", ["merged_into_id"])
    op.create_index(
        "uq_household_items_workspace_canonical_enabled",
        "household_items",
        ["workspace_id", "canonical_key"],
        unique=True,
        postgresql_where=sa.text("canonical_key IS NOT NULL AND enabled IS TRUE"),
        sqlite_where=sa.text("canonical_key IS NOT NULL AND enabled = 1"),
    )


def _harden_replenishment_child_links() -> None:
    with op.batch_alter_table("replenishment_model_versions") as batch:
        batch.create_unique_constraint(
            "uq_replenishment_model_version_workspace_id", ["workspace_id", "id"]
        )
    with op.batch_alter_table("household_item_acquisitions") as batch:
        batch.drop_constraint("uq_acquisition_receipt_item", type_="unique")
        batch.create_unique_constraint(
            "uq_household_item_acquisition_workspace_id", ["workspace_id", "id"]
        )
        batch.create_foreign_key(
            "fk_household_item_acquisition_item_workspace",
            "household_items",
            ["workspace_id", "household_item_id"],
            ["workspace_id", "id"],
            ondelete="CASCADE",
        )
    op.create_index(
        "uq_acquisition_receipt_item_active",
        "household_item_acquisitions",
        ["receipt_item_id"],
        unique=True,
        postgresql_where=sa.text("receipt_item_id IS NOT NULL AND voided_at IS NULL"),
        sqlite_where=sa.text("receipt_item_id IS NOT NULL AND voided_at IS NULL"),
    )
    with op.batch_alter_table("replenishment_predictions") as batch:
        batch.create_unique_constraint(
            "uq_replenishment_prediction_workspace_id", ["workspace_id", "id"]
        )
        batch.create_foreign_key(
            "fk_replenishment_prediction_item_workspace",
            "household_items",
            ["workspace_id", "household_item_id"],
            ["workspace_id", "id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_replenishment_prediction_model_workspace",
            "replenishment_model_versions",
            ["workspace_id", "model_version_id"],
            ["workspace_id", "id"],
        )
    with op.batch_alter_table("replenishment_feedback") as batch:
        batch.create_foreign_key(
            "fk_replenishment_feedback_item_workspace",
            "household_items",
            ["workspace_id", "household_item_id"],
            ["workspace_id", "id"],
            ondelete="CASCADE",
        )
    with op.batch_alter_table("replenishment_job_runs") as batch:
        batch.create_foreign_key(
            "fk_replenishment_job_run_model_workspace",
            "replenishment_model_versions",
            ["workspace_id", "model_version_id"],
            ["workspace_id", "id"],
        )


def _create_decision_ledger() -> None:
    op.create_table(
        "classification_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_entity_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("spending_parent_category", sa.String(length=64), nullable=False),
        sa.Column("subcategory_id", sa.Integer(), nullable=True),
        sa.Column("subcategory_name", sa.String(length=128), nullable=True),
        sa.Column("concept_id", sa.Integer(), nullable=True),
        sa.Column("concept_name", sa.String(length=255), nullable=True),
        sa.Column("item_activity_type", sa.String(length=64), nullable=False),
        sa.Column("replenishment_eligibility", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("confidence_band", sa.String(length=16), nullable=False),
        sa.Column("authority", sa.String(length=32), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("decision_state", sa.String(length=16), nullable=False),
        sa.Column("auto_finalize_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("corrects_decision_id", sa.Integer(), nullable=True),
        sa.Column("household_item_id", sa.Integer(), nullable=True),
        sa.Column("created_subcategory", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_concept", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_alias", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_household_item", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_classification_decision_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "source_type",
            "source_entity_id",
            "version",
            name="uq_classification_decision_source_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "subcategory_id"],
            ["classification_subcategories.workspace_id", "classification_subcategories.id"],
            name="fk_classification_decision_subcategory_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "concept_id"],
            ["classification_concepts.workspace_id", "classification_concepts.id"],
            name="fk_classification_decision_concept_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "corrects_decision_id"],
            ["classification_decisions.workspace_id", "classification_decisions.id"],
            name="fk_classification_decision_correction_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "household_item_id"],
            ["household_items.workspace_id", "household_items.id"],
            name="fk_classification_decision_household_item_workspace",
        ),
        sa.CheckConstraint(
            "source_type IN ('receipt_line', 'transaction')",
            name="ck_classification_decision_source_type",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_classification_decision_confidence"
        ),
        sa.CheckConstraint("version > 0", name="ck_classification_decision_version"),
        sa.CheckConstraint(
            f"spending_parent_category IN ({PARENTS})",
            name="ck_classification_decision_spending_parent",
        ),
        sa.CheckConstraint(
            f"item_activity_type IN ({ACTIVITIES})", name="ck_classification_decision_activity"
        ),
        sa.CheckConstraint(
            f"replenishment_eligibility IN ({REPLENISHMENT})",
            name="ck_classification_decision_replenishment_eligibility",
        ),
        sa.CheckConstraint(f"confidence_band IN ({BANDS})", name="ck_classification_decision_band"),
        sa.CheckConstraint(
            f"authority IN ({AUTHORITIES})", name="ck_classification_decision_authority"
        ),
        sa.CheckConstraint(
            f"decision_state IN ({STATES})", name="ck_classification_decision_state"
        ),
        sa.CheckConstraint(
            "(decision_state = 'provisional' AND auto_finalize_at IS NOT NULL "
            "AND finalized_at IS NULL) OR "
            "(decision_state IN ('final', 'corrected') AND finalized_at IS NOT NULL)",
            name="ck_classification_decision_finalization",
        ),
    )
    for column in (
        "workspace_id",
        "source_type",
        "source_entity_id",
        "spending_parent_category",
        "subcategory_id",
        "concept_id",
        "item_activity_type",
        "replenishment_eligibility",
        "confidence_band",
        "authority",
        "decision_state",
        "auto_finalize_at",
        "corrects_decision_id",
        "household_item_id",
    ):
        op.create_index(
            f"ix_classification_decisions_{column}", "classification_decisions", [column]
        )


def _extend_transactions() -> None:
    with op.batch_alter_table("expense_transactions") as batch:
        batch.add_column(sa.Column("provider_category", sa.String(length=255), nullable=True))
        batch.add_column(
            sa.Column(
                "spending_parent_category",
                sa.String(length=64),
                nullable=False,
                server_default="other_uncertain",
            )
        )
        batch.add_column(sa.Column("classification_subcategory_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("classification_subcategory_name", sa.String(length=128), nullable=True)
        )
        batch.add_column(sa.Column("classification_concept_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("classification_concept_name", sa.String(length=255), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "classification_activity_type",
                sa.String(length=64),
                nullable=False,
                server_default="uncertain",
            )
        )
        batch.add_column(
            sa.Column(
                "replenishment_eligibility",
                sa.String(length=32),
                nullable=False,
                server_default="uncertain",
            )
        )
        batch.add_column(
            sa.Column("classification_confidence", sa.Float(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column(
                "classification_confidence_band",
                sa.String(length=16),
                nullable=False,
                server_default="low",
            )
        )
        batch.add_column(
            sa.Column(
                "classification_authority",
                sa.String(length=32),
                nullable=False,
                server_default="fallback",
            )
        )
        batch.add_column(
            sa.Column(
                "classification_provenance_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.add_column(
            sa.Column(
                "classification_decision_state",
                sa.String(length=16),
                nullable=False,
                server_default="final",
            )
        )
        batch.add_column(
            sa.Column("classification_applied_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("classification_auto_finalize_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("classification_retry_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("classification_finalized_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("classification_corrected_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("classification_corrects_version", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("classification_version", sa.Integer(), nullable=False, server_default="1")
        )
        batch.create_foreign_key(
            "fk_expense_transaction_classification_subcategory_workspace",
            "classification_subcategories",
            ["workspace_id", "classification_subcategory_id"],
            ["workspace_id", "id"],
        )
        batch.create_foreign_key(
            "fk_expense_transaction_classification_concept_workspace",
            "classification_concepts",
            ["workspace_id", "classification_concept_id"],
            ["workspace_id", "id"],
        )
        batch.create_check_constraint(
            "ck_expense_transaction_classification_confidence",
            "classification_confidence >= 0 AND classification_confidence <= 1",
        )
        batch.create_check_constraint(
            "ck_expense_transaction_classification_version", "classification_version > 0"
        )
        batch.create_check_constraint(
            "ck_expense_transaction_spending_parent", f"spending_parent_category IN ({PARENTS})"
        )
        batch.create_check_constraint(
            "ck_expense_transaction_classification_activity",
            f"classification_activity_type IN ({ACTIVITIES})",
        )
        batch.create_check_constraint(
            "ck_expense_transaction_replenishment_eligibility",
            f"replenishment_eligibility IN ({REPLENISHMENT})",
        )
        batch.create_check_constraint(
            "ck_expense_transaction_classification_band",
            f"classification_confidence_band IN ({BANDS})",
        )
        batch.create_check_constraint(
            "ck_expense_transaction_classification_authority",
            f"classification_authority IN ({AUTHORITIES})",
        )
        batch.create_check_constraint(
            "ck_expense_transaction_classification_state",
            f"classification_decision_state IN ({STATES})",
        )
    op.execute(
        sa.text(
            "UPDATE expense_transactions SET provider_category = category "
            "WHERE provider_category IS NULL"
        )
    )
    for column in (
        "spending_parent_category",
        "classification_subcategory_id",
        "classification_concept_id",
        "classification_activity_type",
        "replenishment_eligibility",
        "classification_confidence_band",
        "classification_authority",
        "classification_decision_state",
        "classification_auto_finalize_at",
        "classification_retry_at",
    ):
        op.create_index(f"ix_expense_transactions_{column}", "expense_transactions", [column])


def _extend_receipts() -> None:
    with op.batch_alter_table("purchase_receipts") as batch:
        batch.add_column(
            sa.Column(
                "owner_user_id",
                sa.Integer(),
                sa.ForeignKey(
                    "users.id",
                    name="fk_purchase_receipt_owner_user",
                    ondelete="SET NULL",
                ),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("tip_cents", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("discount_cents", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "line_items_complete", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.add_column(
            sa.Column(
                "quality_warnings_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
            )
        )
        batch.add_column(
            sa.Column(
                "arithmetic_status", sa.String(length=32), nullable=False, server_default="unknown"
            )
        )
        batch.add_column(
            sa.Column(
                "transaction_match_status",
                sa.String(length=32),
                nullable=False,
                server_default="not_attempted",
            )
        )
        batch.add_column(
            sa.Column(
                "transaction_match_confidence", sa.Float(), nullable=False, server_default="0"
            )
        )
        batch.add_column(
            sa.Column(
                "transaction_match_evidence_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(
            sa.Column("transaction_match_attempted_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("transaction_matched_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_check_constraint(
            "ck_purchase_receipts_transaction_match_status",
            "transaction_match_status IN "
            "('not_attempted', 'auto_matched', 'ambiguous', 'no_match')",
        )
        batch.create_check_constraint(
            "ck_purchase_receipts_transaction_match_confidence",
            "transaction_match_confidence >= 0 AND transaction_match_confidence <= 1",
        )
        batch.create_check_constraint(
            "ck_purchase_receipts_arithmetic_status",
            "arithmetic_status IN ('unknown', 'verified', 'mismatch', 'insufficient_data')",
        )
    op.execute(
        sa.text(
            "UPDATE purchase_receipts SET "
            "transaction_match_status = 'auto_matched', "
            "transaction_match_confidence = 1.0, "
            "transaction_match_evidence_json = "
            "'{\"legacy_transaction_link\": true}', "
            "transaction_match_attempted_at = COALESCE(updated_at, created_at), "
            "transaction_matched_at = COALESCE(updated_at, created_at) "
            "WHERE transaction_id IS NOT NULL"
        )
    )
    with op.batch_alter_table("purchase_receipts") as batch:
        batch.create_check_constraint(
            "ck_purchase_receipts_transaction_match_consistency",
            "(transaction_match_status != 'auto_matched' OR "
            "(transaction_id IS NOT NULL AND transaction_matched_at IS NOT NULL)) AND "
            "(transaction_match_status = 'auto_matched' OR transaction_matched_at IS NULL)",
        )
    for column in (
        "owner_user_id",
        "arithmetic_status",
        "transaction_match_status",
        "transaction_match_attempted_at",
    ):
        op.create_index(f"ix_purchase_receipts_{column}", "purchase_receipts", [column])

    with op.batch_alter_table("purchase_receipt_items") as batch:
        batch.add_column(
            sa.Column(
                "spending_parent_category",
                sa.String(length=64),
                nullable=False,
                server_default="other_uncertain",
            )
        )
        batch.add_column(sa.Column("classification_subcategory_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("classification_subcategory_name", sa.String(length=128), nullable=True)
        )
        batch.add_column(sa.Column("classification_concept_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("classification_concept_name", sa.String(length=255), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "item_activity_type",
                sa.String(length=64),
                nullable=False,
                server_default="uncertain",
            )
        )
        batch.add_column(
            sa.Column(
                "replenishment_eligibility",
                sa.String(length=32),
                nullable=False,
                server_default="uncertain",
            )
        )
        batch.add_column(
            sa.Column(
                "classification_confidence_band",
                sa.String(length=16),
                nullable=False,
                server_default="low",
            )
        )
        batch.add_column(
            sa.Column(
                "classification_authority",
                sa.String(length=32),
                nullable=False,
                server_default="fallback",
            )
        )
        batch.add_column(
            sa.Column(
                "classification_provenance_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.add_column(
            sa.Column(
                "classification_decision_state",
                sa.String(length=16),
                nullable=False,
                server_default="final",
            )
        )
        batch.add_column(
            sa.Column("classification_applied_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("classification_auto_finalize_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("classification_retry_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("classification_finalized_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("classification_corrected_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("classification_corrects_version", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("classification_version", sa.Integer(), nullable=False, server_default="1")
        )
        batch.create_foreign_key(
            "fk_purchase_receipt_item_classification_subcategory",
            "classification_subcategories",
            ["classification_subcategory_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_purchase_receipt_item_classification_concept",
            "classification_concepts",
            ["classification_concept_id"],
            ["id"],
        )
        batch.create_check_constraint(
            "ck_purchase_receipt_items_classification_version", "classification_version > 0"
        )
        batch.create_check_constraint(
            "ck_purchase_receipt_items_spending_parent", f"spending_parent_category IN ({PARENTS})"
        )
        batch.create_check_constraint(
            "ck_purchase_receipt_items_classification_activity",
            f"item_activity_type IN ({ACTIVITIES})",
        )
        batch.create_check_constraint(
            "ck_purchase_receipt_items_replenishment_eligibility",
            f"replenishment_eligibility IN ({REPLENISHMENT})",
        )
        batch.create_check_constraint(
            "ck_purchase_receipt_items_classification_band",
            f"classification_confidence_band IN ({BANDS})",
        )
        batch.create_check_constraint(
            "ck_purchase_receipt_items_classification_authority",
            f"classification_authority IN ({AUTHORITIES})",
        )
        batch.create_check_constraint(
            "ck_purchase_receipt_items_classification_state",
            f"classification_decision_state IN ({STATES})",
        )
    for column in (
        "spending_parent_category",
        "classification_subcategory_id",
        "classification_concept_id",
        "item_activity_type",
        "replenishment_eligibility",
        "classification_confidence_band",
        "classification_authority",
        "classification_decision_state",
        "classification_auto_finalize_at",
        "classification_retry_at",
    ):
        op.create_index(f"ix_purchase_receipt_items_{column}", "purchase_receipt_items", [column])


def _install_postgres_tenant_guards() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in DIRECT_TENANT_TABLES:
        op.execute(sa.text(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE public."{table}" FORCE ROW LEVEL SECURITY'))
        op.execute(
            sa.text(
                f'CREATE POLICY expenseops_workspace_isolation ON public."{table}" '
                f"USING (workspace_id = {_WORKSPACE_SETTING}) "
                f"WITH CHECK (workspace_id = {_WORKSPACE_SETTING})"
            )
        )
    item_policy = (
        "EXISTS (SELECT 1 FROM public.purchase_receipts AS receipt "
        "WHERE receipt.id = purchase_receipt_items.receipt_id "
        f"AND receipt.workspace_id = {_WORKSPACE_SETTING}) AND ("
        "household_item_id IS NULL OR EXISTS (SELECT 1 FROM public.household_items AS item "
        "WHERE item.id = purchase_receipt_items.household_item_id "
        f"AND item.workspace_id = {_WORKSPACE_SETTING})) AND ("
        "classification_subcategory_id IS NULL OR EXISTS ("
        "SELECT 1 FROM public.classification_subcategories AS subcategory "
        "WHERE subcategory.id = purchase_receipt_items.classification_subcategory_id "
        f"AND subcategory.workspace_id = {_WORKSPACE_SETTING})) AND ("
        "classification_concept_id IS NULL OR EXISTS ("
        "SELECT 1 FROM public.classification_concepts AS concept "
        "WHERE concept.id = purchase_receipt_items.classification_concept_id "
        f"AND concept.workspace_id = {_WORKSPACE_SETTING}))"
    )
    op.execute(
        sa.text(
            "DROP POLICY IF EXISTS expenseops_workspace_isolation ON public.purchase_receipt_items"
        )
    )
    op.execute(
        sa.text(
            "CREATE POLICY expenseops_workspace_isolation ON public.purchase_receipt_items "
            f"USING ({item_policy}) WITH CHECK ({item_policy})"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION
                public.expenseops_validate_receipt_item_classification_workspace()
            RETURNS trigger LANGUAGE plpgsql AS $expenseops$
            DECLARE parent_workspace integer;
            BEGIN
                SELECT workspace_id INTO parent_workspace FROM public.purchase_receipts
                WHERE id = NEW.receipt_id;
                IF parent_workspace IS NULL THEN
                    RAISE EXCEPTION 'receipt item parent is unavailable' USING ERRCODE = '23514';
                END IF;
                IF NEW.household_item_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.household_items
                    WHERE id = NEW.household_item_id AND workspace_id = parent_workspace
                ) THEN
                    RAISE EXCEPTION 'cross-workspace household item link'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.classification_subcategory_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.classification_subcategories
                    WHERE id = NEW.classification_subcategory_id AND workspace_id = parent_workspace
                ) THEN
                    RAISE EXCEPTION 'cross-workspace classification subcategory link'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.classification_concept_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.classification_concepts
                    WHERE id = NEW.classification_concept_id AND workspace_id = parent_workspace
                ) THEN
                    RAISE EXCEPTION 'cross-workspace classification concept link'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END $expenseops$
            """
        )
    )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS expenseops_receipt_item_workspace_guard "
            "ON public.purchase_receipt_items"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER expenseops_receipt_item_workspace_guard "
            "BEFORE INSERT OR UPDATE OF receipt_id, household_item_id, "
            "classification_subcategory_id, classification_concept_id "
            "ON public.purchase_receipt_items FOR EACH ROW EXECUTE FUNCTION "
            "public.expenseops_validate_receipt_item_classification_workspace()"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.expenseops_validate_receipt_transaction_workspace()
            RETURNS trigger LANGUAGE plpgsql AS $expenseops$
            BEGIN
                IF NEW.transaction_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.expense_transactions
                    WHERE id = NEW.transaction_id AND workspace_id = NEW.workspace_id
                ) THEN
                    RAISE EXCEPTION 'cross-workspace receipt transaction link'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END $expenseops$
            """
        )
    )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS expenseops_receipt_transaction_workspace_guard "
            "ON public.purchase_receipts"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER expenseops_receipt_transaction_workspace_guard "
            "BEFORE INSERT OR UPDATE OF workspace_id, transaction_id ON public.purchase_receipts "
            "FOR EACH ROW EXECUTE FUNCTION "
            "public.expenseops_validate_receipt_transaction_workspace()"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION
                public.expenseops_validate_classification_decision_source_workspace()
            RETURNS trigger LANGUAGE plpgsql AS $expenseops$
            BEGIN
                IF NEW.source_type = 'receipt_line' AND NOT EXISTS (
                    SELECT 1 FROM public.purchase_receipt_items AS line
                    JOIN public.purchase_receipts AS receipt ON receipt.id = line.receipt_id
                    WHERE line.id = NEW.source_entity_id
                      AND receipt.workspace_id = NEW.workspace_id
                ) THEN
                    RAISE EXCEPTION 'cross-workspace classification receipt source'
                        USING ERRCODE = '23514';
                ELSIF NEW.source_type = 'transaction' AND NOT EXISTS (
                    SELECT 1 FROM public.expense_transactions AS transaction
                    WHERE transaction.id = NEW.source_entity_id
                      AND transaction.workspace_id = NEW.workspace_id
                ) THEN
                    RAISE EXCEPTION 'cross-workspace classification transaction source'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END $expenseops$
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER expenseops_classification_decision_source_workspace_guard "
            "BEFORE INSERT OR UPDATE OF workspace_id, source_type, source_entity_id "
            "ON public.classification_decisions FOR EACH ROW EXECUTE FUNCTION "
            "public.expenseops_validate_classification_decision_source_workspace()"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION
                public.expenseops_validate_acquisition_provenance_workspace()
            RETURNS trigger LANGUAGE plpgsql AS $expenseops$
            BEGIN
                IF NEW.receipt_item_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.purchase_receipt_items AS line
                    JOIN public.purchase_receipts AS receipt ON receipt.id = line.receipt_id
                    WHERE line.id = NEW.receipt_item_id
                      AND receipt.workspace_id = NEW.workspace_id
                ) THEN
                    RAISE EXCEPTION 'cross-workspace acquisition receipt link'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.transaction_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.expense_transactions AS transaction
                    WHERE transaction.id = NEW.transaction_id
                      AND transaction.workspace_id = NEW.workspace_id
                ) THEN
                    RAISE EXCEPTION 'cross-workspace acquisition transaction link'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.supersedes_acquisition_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.household_item_acquisitions AS prior
                    WHERE prior.id = NEW.supersedes_acquisition_id
                      AND prior.workspace_id = NEW.workspace_id
                ) THEN
                    RAISE EXCEPTION 'cross-workspace superseded acquisition link'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END $expenseops$
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER expenseops_acquisition_provenance_workspace_guard "
            "BEFORE INSERT OR UPDATE OF workspace_id, receipt_item_id, transaction_id, "
            "supersedes_acquisition_id ON public.household_item_acquisitions "
            "FOR EACH ROW EXECUTE FUNCTION "
            "public.expenseops_validate_acquisition_provenance_workspace()"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION
                public.expenseops_validate_replenishment_feedback_workspace()
            RETURNS trigger LANGUAGE plpgsql AS $expenseops$
            BEGIN
                IF NEW.prediction_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.replenishment_predictions AS prediction
                    WHERE prediction.id = NEW.prediction_id
                      AND prediction.workspace_id = NEW.workspace_id
                      AND prediction.household_item_id = NEW.household_item_id
                ) THEN
                    RAISE EXCEPTION 'cross-workspace or cross-item feedback prediction link'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END $expenseops$
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER expenseops_replenishment_feedback_workspace_guard "
            "BEFORE INSERT OR UPDATE OF workspace_id, household_item_id, prediction_id "
            "ON public.replenishment_feedback FOR EACH ROW EXECUTE FUNCTION "
            "public.expenseops_validate_replenishment_feedback_workspace()"
        )
    )


def upgrade() -> None:
    _create_taxonomy_tables()
    _extend_household_items()
    _harden_replenishment_child_links()
    _create_decision_ledger()
    _extend_transactions()
    _extend_receipts()
    _install_postgres_tenant_guards()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS "
                "expenseops_classification_decision_source_workspace_guard "
                "ON public.classification_decisions"
            )
        )
        op.execute(
            sa.text(
                "DROP FUNCTION IF EXISTS "
                "public.expenseops_validate_classification_decision_source_workspace()"
            )
        )
        op.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS expenseops_replenishment_feedback_workspace_guard "
                "ON public.replenishment_feedback"
            )
        )
        op.execute(
            sa.text(
                "DROP FUNCTION IF EXISTS "
                "public.expenseops_validate_replenishment_feedback_workspace()"
            )
        )
        op.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS expenseops_acquisition_provenance_workspace_guard "
                "ON public.household_item_acquisitions"
            )
        )
        op.execute(
            sa.text(
                "DROP FUNCTION IF EXISTS "
                "public.expenseops_validate_acquisition_provenance_workspace()"
            )
        )
        op.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS expenseops_receipt_transaction_workspace_guard "
                "ON public.purchase_receipts"
            )
        )
        op.execute(
            sa.text(
                "DROP FUNCTION IF EXISTS public.expenseops_validate_receipt_transaction_workspace()"
            )
        )
        op.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS expenseops_receipt_item_workspace_guard "
                "ON public.purchase_receipt_items"
            )
        )
        op.execute(
            sa.text(
                "DROP FUNCTION IF EXISTS "
                "public.expenseops_validate_receipt_item_classification_workspace()"
            )
        )
        original_policy = (
            "EXISTS (SELECT 1 FROM public.purchase_receipts AS receipt "
            "WHERE receipt.id = purchase_receipt_items.receipt_id "
            f"AND receipt.workspace_id = {_WORKSPACE_SETTING}) AND ("
            "household_item_id IS NULL OR EXISTS (SELECT 1 FROM public.household_items AS item "
            "WHERE item.id = purchase_receipt_items.household_item_id "
            f"AND item.workspace_id = {_WORKSPACE_SETTING}))"
        )
        op.execute(
            sa.text(
                "DROP POLICY IF EXISTS expenseops_workspace_isolation "
                "ON public.purchase_receipt_items"
            )
        )
        op.execute(
            sa.text(
                "CREATE POLICY expenseops_workspace_isolation ON public.purchase_receipt_items "
                f"USING ({original_policy}) WITH CHECK ({original_policy})"
            )
        )

    with op.batch_alter_table("replenishment_feedback") as batch:
        batch.drop_constraint(
            "fk_replenishment_feedback_item_workspace", type_="foreignkey"
        )
    with op.batch_alter_table("replenishment_job_runs") as batch:
        batch.drop_constraint(
            "fk_replenishment_job_run_model_workspace", type_="foreignkey"
        )
    with op.batch_alter_table("replenishment_predictions") as batch:
        batch.drop_constraint(
            "fk_replenishment_prediction_model_workspace", type_="foreignkey"
        )
        batch.drop_constraint(
            "fk_replenishment_prediction_item_workspace", type_="foreignkey"
        )
        batch.drop_constraint(
            "uq_replenishment_prediction_workspace_id", type_="unique"
        )
    with op.batch_alter_table("replenishment_model_versions") as batch:
        batch.drop_constraint(
            "uq_replenishment_model_version_workspace_id", type_="unique"
        )
    op.drop_index(
        "uq_acquisition_receipt_item_active",
        table_name="household_item_acquisitions",
    )
    # The legacy schema allowed only one historical row per receipt line. Keep
    # active provenance and degrade only voided links when crossing that boundary.
    op.execute(
        sa.text(
            "UPDATE household_item_acquisitions SET receipt_item_id = NULL "
            "WHERE voided_at IS NOT NULL AND receipt_item_id IS NOT NULL"
        )
    )
    with op.batch_alter_table("household_item_acquisitions") as batch:
        batch.drop_constraint(
            "fk_household_item_acquisition_item_workspace", type_="foreignkey"
        )
        batch.drop_constraint(
            "uq_household_item_acquisition_workspace_id", type_="unique"
        )
        batch.create_unique_constraint("uq_acquisition_receipt_item", ["receipt_item_id"])

    for column in (
        "spending_parent_category",
        "classification_subcategory_id",
        "classification_concept_id",
        "item_activity_type",
        "replenishment_eligibility",
        "classification_confidence_band",
        "classification_authority",
        "classification_decision_state",
        "classification_auto_finalize_at",
        "classification_retry_at",
    ):
        op.drop_index(
            f"ix_purchase_receipt_items_{column}",
            table_name="purchase_receipt_items",
        )
    with op.batch_alter_table("purchase_receipt_items") as batch:
        for name in (
            "ck_purchase_receipt_items_classification_state",
            "ck_purchase_receipt_items_classification_authority",
            "ck_purchase_receipt_items_classification_band",
            "ck_purchase_receipt_items_replenishment_eligibility",
            "ck_purchase_receipt_items_classification_activity",
            "ck_purchase_receipt_items_spending_parent",
            "ck_purchase_receipt_items_classification_version",
        ):
            batch.drop_constraint(name, type_="check")
        batch.drop_constraint("fk_purchase_receipt_item_classification_concept", type_="foreignkey")
        batch.drop_constraint(
            "fk_purchase_receipt_item_classification_subcategory", type_="foreignkey"
        )
        for column in (
            "classification_version",
            "classification_corrects_version",
            "classification_corrected_at",
            "classification_finalized_at",
            "classification_retry_at",
            "classification_auto_finalize_at",
            "classification_applied_at",
            "classification_decision_state",
            "classification_provenance_json",
            "classification_authority",
            "classification_confidence_band",
            "replenishment_eligibility",
            "item_activity_type",
            "classification_concept_name",
            "classification_concept_id",
            "classification_subcategory_name",
            "classification_subcategory_id",
            "spending_parent_category",
        ):
            batch.drop_column(column)
    for column in (
        "owner_user_id",
        "arithmetic_status",
        "transaction_match_status",
        "transaction_match_attempted_at",
    ):
        op.drop_index(
            f"ix_purchase_receipts_{column}",
            table_name="purchase_receipts",
        )
    with op.batch_alter_table("purchase_receipts") as batch:
        batch.drop_constraint("ck_purchase_receipts_arithmetic_status", type_="check")
        batch.drop_constraint(
            "ck_purchase_receipts_transaction_match_consistency", type_="check"
        )
        batch.drop_constraint("ck_purchase_receipts_transaction_match_confidence", type_="check")
        batch.drop_constraint("ck_purchase_receipts_transaction_match_status", type_="check")
        for column in (
            "transaction_matched_at",
            "transaction_match_attempted_at",
            "transaction_match_evidence_json",
            "transaction_match_confidence",
            "transaction_match_status",
            "arithmetic_status",
            "quality_warnings_json",
            "line_items_complete",
            "discount_cents",
            "tip_cents",
            "owner_user_id",
        ):
            batch.drop_column(column)
    for column in (
        "spending_parent_category",
        "classification_subcategory_id",
        "classification_concept_id",
        "classification_activity_type",
        "replenishment_eligibility",
        "classification_confidence_band",
        "classification_authority",
        "classification_decision_state",
        "classification_auto_finalize_at",
        "classification_retry_at",
    ):
        op.drop_index(
            f"ix_expense_transactions_{column}",
            table_name="expense_transactions",
        )
    with op.batch_alter_table("expense_transactions") as batch:
        for name in (
            "ck_expense_transaction_classification_state",
            "ck_expense_transaction_classification_authority",
            "ck_expense_transaction_classification_band",
            "ck_expense_transaction_replenishment_eligibility",
            "ck_expense_transaction_classification_activity",
            "ck_expense_transaction_spending_parent",
            "ck_expense_transaction_classification_version",
            "ck_expense_transaction_classification_confidence",
        ):
            batch.drop_constraint(name, type_="check")
        batch.drop_constraint(
            "fk_expense_transaction_classification_concept_workspace", type_="foreignkey"
        )
        batch.drop_constraint(
            "fk_expense_transaction_classification_subcategory_workspace", type_="foreignkey"
        )
        for column in (
            "classification_version",
            "classification_corrects_version",
            "classification_corrected_at",
            "classification_finalized_at",
            "classification_retry_at",
            "classification_auto_finalize_at",
            "classification_applied_at",
            "classification_decision_state",
            "classification_provenance_json",
            "classification_authority",
            "classification_confidence_band",
            "classification_confidence",
            "replenishment_eligibility",
            "classification_activity_type",
            "classification_concept_name",
            "classification_concept_id",
            "classification_subcategory_name",
            "classification_subcategory_id",
            "spending_parent_category",
            "provider_category",
        ):
            batch.drop_column(column)
    op.drop_table("classification_decisions")
    op.drop_index("uq_household_items_workspace_canonical_enabled", table_name="household_items")
    op.drop_index("ix_household_items_merged_into_id", table_name="household_items")
    for column in (
        "canonical_key",
        "classification_concept_id",
        "spending_parent_category",
        "replenishment_eligibility",
    ):
        op.drop_index(f"ix_household_items_{column}", table_name="household_items")
    # The pre-Day16 schema has no explicit prior/quantity sources. Preserve the
    # bounded estimated cadence as its closest legacy equivalent before restoring
    # the old CHECK constraint; never leave downgrade to fail halfway through.
    op.execute(
        sa.text(
            "UPDATE household_items SET cadence_source = 'adaptive' "
            "WHERE cadence_source IN "
            "('category_prior', 'model_prior', 'quantity_adjusted') "
            "AND cadence_days IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE household_items SET cadence_source = 'learning' "
            "WHERE cadence_source IN "
            "('category_prior', 'model_prior', 'quantity_adjusted') "
            "AND cadence_days IS NULL"
        )
    )
    with op.batch_alter_table("household_items") as batch:
        batch.drop_constraint("fk_household_item_merged_workspace", type_="foreignkey")
        batch.drop_constraint(
            "fk_household_item_classification_concept_workspace", type_="foreignkey"
        )
        batch.drop_constraint("uq_household_item_workspace_id", type_="unique")
        batch.drop_constraint("ck_household_items_classification_confidence", type_="check")
        batch.drop_constraint("ck_household_items_replenishment_eligibility", type_="check")
        batch.drop_constraint("ck_household_items_spending_parent", type_="check")
        batch.drop_constraint("ck_household_items_cadence_range", type_="check")
        batch.drop_constraint("ck_household_items_cadence_confidence", type_="check")
        batch.drop_constraint("ck_household_items_cadence_state", type_="check")
        batch.drop_constraint("ck_household_item_not_self_merged", type_="check")
        batch.create_check_constraint(
            "ck_household_items_cadence_state",
            "(cadence_source = 'learning' AND cadence_days IS NULL) OR "
            "(cadence_source IN ('configured', 'observed', 'adaptive') "
            "AND cadence_days IS NOT NULL AND cadence_days > 0)",
        )
        for column in (
            "merged_at",
            "merged_into_id",
            "classification_provenance_json",
            "classification_confidence",
            "replenishment_eligibility",
            "spending_parent_category",
            "classification_concept_id",
            "canonical_key",
            "cadence_estimated_at",
            "cadence_provenance_json",
            "cadence_max_days",
            "cadence_min_days",
            "cadence_confidence",
        ):
            batch.drop_column(column)
    op.drop_table("classification_settings")
    op.drop_table("classification_concept_aliases")
    op.drop_table("classification_concepts")
    op.drop_table("classification_subcategories")

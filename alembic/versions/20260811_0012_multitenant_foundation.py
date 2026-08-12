"""add multi-tenant user and workspace foundation

Revision ID: 20260811_0012
Revises: 20260810_0011
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0012"
down_revision: str | None = "20260810_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "plaid_items",
    "expense_transactions",
    "ai_interpretation_memories",
    "telegram_sessions",
    "household_items",
    "purchase_receipts",
    "household_item_acquisitions",
    "replenishment_model_versions",
    "replenishment_predictions",
    "replenishment_feedback",
    "replenishment_job_runs",
    "promotion_messages",
    "promotion_offers",
    "promotion_digest_runs",
    "promotion_settings",
    "gmail_sync_checkpoints",
    "errands",
    "errand_plans",
    "saved_locations",
    "preferred_places",
)

SCOPED_UNIQUES = (
    (
        "telegram_sessions",
        "uq_telegram_session_chat_user",
        "uq_telegram_session_workspace_chat_user",
        ("workspace_id", "chat_id", "user_id"),
    ),
    (
        "purchase_receipts",
        "uq_receipt_source_external",
        "uq_receipt_workspace_source_external",
        ("workspace_id", "source", "source_external_id"),
    ),
    (
        "replenishment_model_versions",
        "uq_replenishment_model_versions_version",
        "uq_model_version_workspace_version",
        ("workspace_id", "version"),
    ),
    (
        "replenishment_job_runs",
        "uq_replenishment_job_runs_run_key",
        "uq_job_run_workspace_key",
        ("workspace_id", "run_key"),
    ),
    (
        "promotion_messages",
        "uq_promotion_message_gmail_id",
        "uq_promotion_message_workspace_gmail",
        ("workspace_id", "gmail_message_id"),
    ),
    (
        "promotion_offers",
        "uq_promotion_campaign_fingerprint",
        "uq_offer_workspace_campaign",
        ("workspace_id", "campaign_fingerprint"),
    ),
    (
        "gmail_sync_checkpoints",
        "uq_gmail_sync_checkpoint_account",
        "uq_gmail_checkpoint_workspace_key",
        ("workspace_id", "account_key"),
    ),
    (
        "preferred_places",
        "uq_preferred_places_preference_key",
        "uq_preferred_place_workspace_key",
        ("workspace_id", "preference_key"),
    ),
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("api_token_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("api_token_hash", name="uq_users_api_token_hash"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_status", "users", ["status"])
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("workspace_type", sa.String(32), nullable=False, server_default="personal"),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
    )
    op.create_index("ix_workspaces_created_by_user_id", "workspaces", ["created_by_user_id"])
    op.create_table(
        "workspace_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="member"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_membership"),
    )
    op.create_index(
        "ix_workspace_memberships_workspace_id", "workspace_memberships", ["workspace_id"]
    )
    op.create_index("ix_workspace_memberships_user_id", "workspace_memberships", ["user_id"])

    now = sa.func.now()
    users = sa.table(
        "users",
        sa.column("id", sa.Integer()),
        sa.column("email", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("status", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    workspaces = sa.table(
        "workspaces",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("workspace_type", sa.String()),
        sa.column("created_by_user_id", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    memberships = sa.table(
        "workspace_memberships",
        sa.column("workspace_id", sa.Integer()),
        sa.column("user_id", sa.Integer()),
        sa.column("role", sa.String()),
        sa.column("is_default", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        users,
        [
            {
                "id": 1,
                "email": "local@expenseops.invalid",
                "display_name": "Local user",
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        ],
        multiinsert=False,
    )
    op.bulk_insert(
        workspaces,
        [
            {
                "id": 1,
                "name": "Personal workspace",
                "workspace_type": "personal",
                "created_by_user_id": 1,
                "created_at": now,
                "updated_at": now,
            }
        ],
        multiinsert=False,
    )
    op.bulk_insert(
        memberships,
        [{"workspace_id": 1, "user_id": 1, "role": "owner", "is_default": True, "created_at": now}],
        multiinsert=False,
    )

    for table_name in TENANT_TABLES:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(
                sa.Column("workspace_id", sa.Integer(), nullable=False, server_default="1")
            )
            batch_op.create_foreign_key(
                f"fk_{table_name}_workspace_id", "workspaces", ["workspace_id"], ["id"]
            )
            batch_op.create_index(f"ix_{table_name}_workspace_id", ["workspace_id"])

    with op.batch_alter_table("plaid_webhook_events") as batch_op:
        batch_op.add_column(sa.Column("workspace_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_plaid_webhook_events_workspace_id",
            "workspaces",
            ["workspace_id"],
            ["id"],
        )
        batch_op.create_index("ix_plaid_webhook_events_workspace_id", ["workspace_id"])

    naming_convention = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
    for table_name, old_name, new_name, columns in SCOPED_UNIQUES:
        with op.batch_alter_table(table_name, naming_convention=naming_convention) as batch_op:
            batch_op.drop_constraint(old_name, type_="unique")
            batch_op.create_unique_constraint(new_name, list(columns))
    with op.batch_alter_table("household_item_acquisitions") as batch_op:
        batch_op.drop_index("ix_household_item_acquisitions_logical_purchase_key")
        batch_op.create_index(
            "ix_household_item_acquisitions_logical_purchase_key",
            ["logical_purchase_key"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            "uq_acquisition_workspace_purchase_key",
            ["workspace_id", "logical_purchase_key"],
        )
    with op.batch_alter_table("promotion_settings") as batch_op:
        batch_op.create_unique_constraint("uq_promotion_settings_workspace", ["workspace_id"])
    with op.batch_alter_table("saved_locations") as batch_op:
        batch_op.create_unique_constraint(
            "uq_saved_location_workspace_type_label",
            ["workspace_id", "location_type", "label"],
        )

    op.create_table(
        "gmail_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("google_user_id", sa.String(320), nullable=False, server_default="me"),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "workspace_id", "google_user_id", name="uq_gmail_account_workspace_user"
        ),
    )
    op.create_index("ix_gmail_accounts_workspace_id", "gmail_accounts", ["workspace_id"])
    op.create_index("ix_gmail_accounts_user_id", "gmail_accounts", ["user_id"])
    op.create_table(
        "telegram_identities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.String(128), nullable=False),
        sa.Column("chat_id", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("telegram_user_id", "chat_id", name="uq_telegram_identity_external"),
    )
    op.create_index("ix_telegram_identities_workspace_id", "telegram_identities", ["workspace_id"])
    op.create_index("ix_telegram_identities_user_id", "telegram_identities", ["user_id"])
    op.create_table(
        "splitwise_integrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("credentials_encrypted", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workspace_id", name="uq_splitwise_workspace"),
    )
    op.create_index(
        "ix_splitwise_integrations_workspace_id", "splitwise_integrations", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_table("splitwise_integrations")
    op.drop_table("telegram_identities")
    op.drop_table("gmail_accounts")
    with op.batch_alter_table("household_item_acquisitions") as batch_op:
        batch_op.drop_constraint("uq_acquisition_workspace_purchase_key", type_="unique")
        batch_op.drop_index("ix_household_item_acquisitions_logical_purchase_key")
        batch_op.create_index(
            "ix_household_item_acquisitions_logical_purchase_key",
            ["logical_purchase_key"],
            unique=True,
        )
    with op.batch_alter_table("saved_locations") as batch_op:
        batch_op.drop_constraint("uq_saved_location_workspace_type_label", type_="unique")
    with op.batch_alter_table("promotion_settings") as batch_op:
        batch_op.drop_constraint("uq_promotion_settings_workspace", type_="unique")
    naming_convention = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
    for table_name, old_name, new_name, columns in reversed(SCOPED_UNIQUES):
        with op.batch_alter_table(table_name, naming_convention=naming_convention) as batch_op:
            batch_op.drop_constraint(new_name, type_="unique")
            old_columns = [column for column in columns if column != "workspace_id"]
            batch_op.create_unique_constraint(old_name, old_columns)
    for table_name in reversed(TENANT_TABLES):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_index(f"ix_{table_name}_workspace_id")
            batch_op.drop_constraint(f"fk_{table_name}_workspace_id", type_="foreignkey")
            batch_op.drop_column("workspace_id")
    with op.batch_alter_table("plaid_webhook_events") as batch_op:
        batch_op.drop_index("ix_plaid_webhook_events_workspace_id")
        batch_op.drop_constraint("fk_plaid_webhook_events_workspace_id", type_="foreignkey")
        batch_op.drop_column("workspace_id")
    op.drop_table("workspace_memberships")
    op.drop_table("workspaces")
    op.drop_table("users")

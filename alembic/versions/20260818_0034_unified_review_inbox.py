"""add the tenant-scoped unified review inbox projection

Revision ID: 20260818_0034
Revises: 20260817_0033
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260818_0034"
down_revision: str | None = "20260817_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    workspace = "NULLIF(current_setting('expenseops.workspace_id', true), '')::integer"
    op.execute(sa.text('ALTER TABLE public."review_items" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text('ALTER TABLE public."review_items" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            'CREATE POLICY expenseops_workspace_isolation ON public."review_items" '
            f"USING (workspace_id = {workspace}) WITH CHECK (workspace_id = {workspace})"
        )
    )


def upgrade() -> None:
    op.create_table(
        "review_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_entity_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action_codes_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("public_id", name="uq_review_items_public_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "owner_user_id",
            "kind",
            "source_type",
            "source_entity_id",
            name="uq_review_item_owner_kind_source",
        ),
        sa.CheckConstraint(
            "kind IN ('transaction_review', 'itemized_split_ready', "
            "'receipt_match_needed', 'financial_reconciliation')",
            name="ck_review_items_kind",
        ),
        sa.CheckConstraint(
            "source_type IN ('transaction', 'receipt', 'financial_operation')",
            name="ck_review_items_source_type",
        ),
        sa.CheckConstraint("state IN ('open', 'resolved', 'stale')", name="ck_review_items_state"),
        sa.CheckConstraint("source_entity_id > 0", name="ck_review_items_source_entity_positive"),
    )
    for column in (
        "workspace_id",
        "public_id",
        "owner_user_id",
        "kind",
        "source_type",
        "source_entity_id",
        "state",
    ):
        op.create_index(f"ix_review_items_{column}", "review_items", [column])
    op.create_index(
        "ix_review_items_owner_state_created",
        "review_items",
        ["workspace_id", "owner_user_id", "state", "created_at"],
    )
    _enable_rls()
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                "INSERT INTO review_items (workspace_id, public_id, owner_user_id, kind, "
                "source_type, source_entity_id, state, action_codes_json, created_at, updated_at) "
                "SELECT tx.workspace_id, "
                "substring(token,1,8)||'-'||substring(token,9,4)||'-4'||substring(token,14,3)"
                "||'-a'||substring(token,18,3)||'-'||substring(token,21,12), "
                "item.owner_user_id, 'transaction_review', 'transaction', tx.id, 'open', "
                'CAST(\'["personal","recommended_split","customize"]\' AS json), '
                "COALESCE(tx.created_at, CURRENT_TIMESTAMP), CURRENT_TIMESTAMP "
                "FROM (SELECT existing.*, md5(random()::text || clock_timestamp()::text || "
                "existing.id::text) AS token FROM expense_transactions AS existing) AS tx "
                "JOIN plaid_items AS item ON item.id = tx.plaid_item_id "
                "WHERE item.owner_user_id IS NOT NULL "
                "AND tx.status IN ('ask_user','shared_draft') "
                "AND tx.splitwise_expense_id IS NULL"
            )
        )
    else:
        op.execute(
            sa.text(
                "INSERT INTO review_items (workspace_id, public_id, owner_user_id, kind, "
                "source_type, source_entity_id, state, action_codes_json, created_at, updated_at) "
                "SELECT tx.workspace_id, lower(hex(randomblob(4)))||'-'||"
                "lower(hex(randomblob(2)))||'-4'||substr(lower(hex(randomblob(2))),2)||'-a'||"
                "substr(lower(hex(randomblob(2))),2)||'-'||lower(hex(randomblob(6))), "
                "item.owner_user_id, 'transaction_review', 'transaction', tx.id, 'open', "
                '\'["personal","recommended_split","customize"]\', '
                "COALESCE(tx.created_at, CURRENT_TIMESTAMP), CURRENT_TIMESTAMP "
                "FROM expense_transactions AS tx JOIN plaid_items AS item "
                "ON item.id = tx.plaid_item_id WHERE item.owner_user_id IS NOT NULL "
                "AND tx.status IN ('ask_user','shared_draft') "
                "AND tx.splitwise_expense_id IS NULL"
            )
        )


def downgrade() -> None:
    op.drop_table("review_items")

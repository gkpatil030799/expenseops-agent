"""add transaction reconciliation invariants

Revision ID: 20260812_0018
Revises: 20260812_0017
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260812_0018"
down_revision: str | None = "20260812_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Legacy one-click Telegram drafts did not contain a participant/allocation
    # payload. They are review decisions, not valid Splitwise drafts.
    op.execute(
        "UPDATE expense_transactions SET status = 'ask_user' "
        "WHERE status = 'shared_draft' "
        "AND (splitwise_payload_json IS NULL OR trim(splitwise_payload_json) IN ('', '{}'))"
    )
    with op.batch_alter_table("expense_transactions") as batch_op:
        batch_op.add_column(sa.Column("splitwise_amount_cents", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("replaces_transaction_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("replaced_by_transaction_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_expense_transactions_replaces_transaction_id",
            "expense_transactions",
            ["replaces_transaction_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_expense_transactions_replaced_by_transaction_id",
            "expense_transactions",
            ["replaced_by_transaction_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_check_constraint(
            "ck_expense_transactions_valid_shared_draft",
            "status != 'shared_draft' OR "
            "(splitwise_payload_json IS NOT NULL "
            "AND trim(splitwise_payload_json) NOT IN ('', '{}'))",
        )
    op.create_index(
        "ix_expense_transactions_replaces_transaction_id",
        "expense_transactions",
        ["replaces_transaction_id"],
    )
    op.create_index(
        "ix_expense_transactions_replaced_by_transaction_id",
        "expense_transactions",
        ["replaced_by_transaction_id"],
    )

    # Existing posted rows predate explicit amount snapshots. Their current bank
    # amount is the best available baseline and prevents false drift alerts.
    op.execute(
        "UPDATE expense_transactions SET splitwise_amount_cents = abs(amount_cents) "
        "WHERE splitwise_expense_id IS NOT NULL AND splitwise_amount_cents IS NULL"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_expense_transactions_replaced_by_transaction_id",
        table_name="expense_transactions",
    )
    op.drop_index(
        "ix_expense_transactions_replaces_transaction_id",
        table_name="expense_transactions",
    )
    with op.batch_alter_table("expense_transactions") as batch_op:
        batch_op.drop_constraint(
            "ck_expense_transactions_valid_shared_draft", type_="check"
        )
        batch_op.drop_constraint(
            "fk_expense_transactions_replaced_by_transaction_id", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_expense_transactions_replaces_transaction_id", type_="foreignkey"
        )
        batch_op.drop_column("replaced_by_transaction_id")
        batch_op.drop_column("replaces_transaction_id")
        batch_op.drop_column("splitwise_amount_cents")

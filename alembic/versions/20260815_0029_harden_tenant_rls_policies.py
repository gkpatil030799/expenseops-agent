"""remove the generic tenant RLS escape hatch

Revision ID: 20260815_0029
Revises: 20260815_0028
Create Date: 2026-08-15

This is the contract half of the tenant-routing change.  Every tenant table is
visible only when its workspace matches the transaction-local workspace set by
the application.  The previous generic session switch is intentionally not
restored by downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260815_0029"
down_revision: str | None = "20260815_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Keep this explicit and closed.  A model/table addition must make a deliberate
# RLS decision and update the associated exact-set migration/readiness tests.
TENANT_TABLES = (
    "gmail_accounts",
    "telegram_identities",
    "splitwise_integrations",
    "workspace_invitations",
    "telegram_link_codes",
    "plaid_items",
    "expense_transactions",
    "financial_operations",
    "outbox_events",
    "scheduled_job_leases",
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
    "data_consents",
    "agent_conversations",
    "agent_messages",
    "agent_runs",
    "agent_tool_calls",
    "agent_action_proposals",
)

_WORKSPACE_SETTING = "NULLIF(current_setting('expenseops.workspace_id', true), '')::integer"

# These rows are logically tenant-owned but derive their workspace from an
# RLS-protected parent. Keeping the relationship in the policy avoids a schema
# rewrite while preventing direct cross-tenant reads or writes by the shared
# runtime role.
TENANT_CHILD_POLICIES = {
    "purchase_receipt_items": (
        "EXISTS (SELECT 1 FROM public.purchase_receipts AS receipt "
        "WHERE receipt.id = purchase_receipt_items.receipt_id "
        f"AND receipt.workspace_id = {_WORKSPACE_SETTING}) AND ("
        "purchase_receipt_items.household_item_id IS NULL OR EXISTS ("
        "SELECT 1 FROM public.household_items AS item "
        "WHERE item.id = purchase_receipt_items.household_item_id "
        f"AND item.workspace_id = {_WORKSPACE_SETTING}))"
    ),
    "household_item_aliases": (
        "EXISTS (SELECT 1 FROM public.household_items AS item "
        "WHERE item.id = household_item_aliases.household_item_id "
        f"AND item.workspace_id = {_WORKSPACE_SETTING})"
    ),
    "promotion_feedback": (
        "EXISTS (SELECT 1 FROM public.promotion_offers AS offer "
        "WHERE offer.id = promotion_feedback.promotion_offer_id "
        f"AND offer.workspace_id = {_WORKSPACE_SETTING})"
    ),
    "errand_household_items": (
        "EXISTS (SELECT 1 FROM public.errands AS errand "
        "WHERE errand.id = errand_household_items.errand_id "
        f"AND errand.workspace_id = {_WORKSPACE_SETTING}) AND EXISTS ("
        "SELECT 1 FROM public.household_items AS item "
        "WHERE item.id = errand_household_items.household_item_id "
        f"AND item.workspace_id = {_WORKSPACE_SETTING})"
    ),
    "errand_plan_stops": (
        "EXISTS (SELECT 1 FROM public.errand_plans AS plan "
        "WHERE plan.id = errand_plan_stops.plan_id "
        f"AND plan.workspace_id = {_WORKSPACE_SETTING})"
    ),
    "errand_plan_stop_errands": (
        "EXISTS (SELECT 1 FROM public.errand_plan_stops AS stop "
        "JOIN public.errand_plans AS plan ON plan.id = stop.plan_id "
        "WHERE stop.id = errand_plan_stop_errands.stop_id "
        f"AND plan.workspace_id = {_WORKSPACE_SETTING}) AND EXISTS ("
        "SELECT 1 FROM public.errands AS errand "
        "WHERE errand.id = errand_plan_stop_errands.errand_id "
        f"AND errand.workspace_id = {_WORKSPACE_SETTING})"
    ),
    "errand_plan_stop_household_items": (
        "EXISTS (SELECT 1 FROM public.errand_plan_stops AS stop "
        "JOIN public.errand_plans AS plan ON plan.id = stop.plan_id "
        "WHERE stop.id = errand_plan_stop_household_items.stop_id "
        f"AND plan.workspace_id = {_WORKSPACE_SETTING}) AND EXISTS ("
        "SELECT 1 FROM public.household_items AS item "
        "WHERE item.id = errand_plan_stop_household_items.household_item_id "
        f"AND item.workspace_id = {_WORKSPACE_SETTING})"
    ),
}

# These four link tables derive tenancy from two independent parents.  The
# original foreign keys guarantee that each parent exists, but they do not
# guarantee that both parents belong to the same workspace.  Check that
# invariant before changing any policy so an unsafe legacy link blocks the
# migration instead of becoming invisible to every runtime workspace.
MULTI_PARENT_CHILD_INTEGRITY_CHECKS = {
    "purchase_receipt_items": (
        "SELECT 1 FROM public.purchase_receipt_items AS link "
        "JOIN public.purchase_receipts AS receipt ON receipt.id = link.receipt_id "
        "JOIN public.household_items AS item ON item.id = link.household_item_id "
        "WHERE link.household_item_id IS NOT NULL "
        "AND receipt.workspace_id IS DISTINCT FROM item.workspace_id"
    ),
    "errand_household_items": (
        "SELECT 1 FROM public.errand_household_items AS link "
        "JOIN public.errands AS errand ON errand.id = link.errand_id "
        "JOIN public.household_items AS item ON item.id = link.household_item_id "
        "WHERE errand.workspace_id IS DISTINCT FROM item.workspace_id"
    ),
    "errand_plan_stop_errands": (
        "SELECT 1 FROM public.errand_plan_stop_errands AS link "
        "JOIN public.errand_plan_stops AS stop ON stop.id = link.stop_id "
        "JOIN public.errand_plans AS plan ON plan.id = stop.plan_id "
        "JOIN public.errands AS errand ON errand.id = link.errand_id "
        "WHERE plan.workspace_id IS DISTINCT FROM errand.workspace_id"
    ),
    "errand_plan_stop_household_items": (
        "SELECT 1 FROM public.errand_plan_stop_household_items AS link "
        "JOIN public.errand_plan_stops AS stop ON stop.id = link.stop_id "
        "JOIN public.errand_plans AS plan ON plan.id = stop.plan_id "
        "JOIN public.household_items AS item ON item.id = link.household_item_id "
        "WHERE plan.workspace_id IS DISTINCT FROM item.workspace_id"
    ),
}

_MULTI_PARENT_INTEGRITY_LOCK = """
LOCK TABLE
    public.errand_household_items,
    public.errand_plan_stop_errands,
    public.errand_plan_stop_household_items,
    public.errand_plan_stops,
    public.errand_plans,
    public.errands,
    public.household_items,
    public.purchase_receipt_items,
    public.purchase_receipts
IN SHARE ROW EXCLUSIVE MODE
"""


def _assert_multi_parent_child_integrity() -> None:
    # Hold write-conflicting locks through the policy replacement to close the
    # check/use race with the still-active compatibility application revision.
    op.execute(sa.text(_MULTI_PARENT_INTEGRITY_LOCK))
    for table, query in MULTI_PARENT_CHILD_INTEGRITY_CHECKS.items():
        error_message = (
            f"Migration 20260815_0029 blocked: cross-workspace parent links exist in {table}"
        )
        op.execute(
            sa.text(
                f"""
                DO $expenseops_tenant_link_integrity$
                BEGIN
                    IF EXISTS ({query}) THEN
                        RAISE EXCEPTION
                            '{error_message}'
                            USING ERRCODE = '23514';
                    END IF;
                END
                $expenseops_tenant_link_integrity$
                """
            )
        )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _assert_multi_parent_child_integrity()
    policy_expressions = {
        **{table: f"workspace_id = {_WORKSPACE_SETTING}" for table in TENANT_TABLES},
        **TENANT_CHILD_POLICIES,
    }
    for table, expression in policy_expressions.items():
        op.execute(sa.text(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE public."{table}" FORCE ROW LEVEL SECURITY'))
        op.execute(
            sa.text(f'DROP POLICY IF EXISTS expenseops_workspace_isolation ON public."{table}"')
        )
        op.execute(
            sa.text(
                f'CREATE POLICY expenseops_workspace_isolation ON public."{table}" '
                f"USING ({expression}) WITH CHECK ({expression})"
            )
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    # Reintroducing the old generic session-controlled policy would turn an
    # operational rollback into a tenant-isolation regression.  Only application
    # revisions compatible with workspace-only routing may run after this point;
    # a database downgrade across the boundary is blocked and transactional.
    raise RuntimeError(
        "Migration 20260815_0029 is an irreversible tenant-security boundary; "
        "redeploy compatible application code without downgrading this revision."
    )

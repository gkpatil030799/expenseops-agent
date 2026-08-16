from __future__ import annotations

import json
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_RELEASE_SECURITY") != "1",
    reason="requires the isolated PostgreSQL release-gate database",
)

HARDENED_RLS_EXPECTED = os.environ.get("EXPECTED_HARDENED_RLS", "1") == "1"


def test_runtime_readiness_reports_real_restricted_role_and_rls():
    from app.main import readiness

    response = readiness()
    payload = json.loads(response.body)
    checks = payload["checks"]

    assert response.status_code == (200 if HARDENED_RLS_EXPECTED else 503)
    assert payload["status"] == ("ready" if HARDENED_RLS_EXPECTED else "not_ready")
    assert checks["database"] == "ok"
    assert checks["migration_current"] is True
    assert checks["database_rls"] is HARDENED_RLS_EXPECTED
    assert checks["tenant_rls_enabled"] is HARDENED_RLS_EXPECTED
    assert checks["tenant_rls_forced"] is HARDENED_RLS_EXPECTED
    assert checks["tenant_rls_policies_hardened"] is HARDENED_RLS_EXPECTED
    assert checks["tenant_routing_functions_hardened"] is True
    assert checks["runtime_role_expected"] is True
    assert checks["runtime_role_login"] is True
    assert checks["runtime_role_superuser"] is False
    assert checks["runtime_role_bypassrls"] is False
    assert checks["runtime_role_createdb"] is False
    assert checks["runtime_role_createrole"] is False
    assert checks["runtime_role_replication"] is False
    assert checks["runtime_role_inherit"] is False
    assert checks["runtime_role_has_memberships"] is False
    assert checks["runtime_role_owns_application_tables"] is False
    assert checks["runtime_role_owns_routing_functions"] is False
    assert checks["runtime_role_database_create"] is False
    assert checks["runtime_role_database_temporary"] is False
    assert checks["runtime_role_database_connect"] is True
    assert checks["runtime_role_schema_create"] is False
    assert checks["runtime_role_schema_usage"] is True
    assert checks["runtime_role_excess_table_privileges"] is False
    assert checks["runtime_role_missing_table_privileges"] is False
    assert checks["runtime_role_unexpected_table_privileges"] is False
    assert checks["runtime_role_sequence_privileges_unsafe"] is False
    assert checks["runtime_role_unexpected_sequence_privileges"] is False
    assert checks["runtime_role_unexpected_function_execute"] is False
    assert checks["runtime_role_public_type_usage"] is False
    assert checks["runtime_role_alembic_write"] is False


def test_runtime_role_cannot_escape_cross_workspace_rls():
    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    first_workspace = int(os.environ.get("RLS_TEST_FIRST_WORKSPACE_ID", "1"))
    second_workspace = int(os.environ.get("RLS_TEST_SECOND_WORKSPACE_ID", "900002"))

    with engine.connect() as connection:
        role = connection.execute(
            text(
                "SELECT current_user, rolsuper, rolbypassrls, rolcreatedb, "
                "rolcreaterole, rolreplication, rolinherit FROM pg_roles "
                "WHERE rolname = current_user"
            )
        ).one()
        assert role._mapping["current_user"] == os.environ.get(
            "EXPECTED_RUNTIME_ROLE", "expenseops_runtime"
        )
        assert tuple(role[1:]) == (False, False, False, False, False, False)
        assert not connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_auth_members membership "
                "WHERE membership.member = (SELECT oid FROM pg_roles "
                "WHERE rolname = current_user))"
            )
        )

        connection.execute(text("SELECT set_config('expenseops.workspace_id', '', true)"))
        connection.execute(text("SELECT set_config('expenseops.bypass_rls', 'off', true)"))
        assert connection.scalar(text("SELECT count(*) FROM scheduled_job_leases")) == 0

        if HARDENED_RLS_EXPECTED:
            connection.execute(text("SELECT set_config('expenseops.bypass_rls', 'on', true)"))
            assert connection.scalar(text("SELECT count(*) FROM scheduled_job_leases")) == 0

        connection.execute(
            text("SELECT set_config('expenseops.workspace_id', :workspace_id, true)"),
            {"workspace_id": str(first_workspace)},
        )
        visible = connection.execute(
            text("SELECT workspace_id, job_name FROM scheduled_job_leases ORDER BY job_name")
        ).all()
        assert visible == [(first_workspace, "ci-rls-first")]
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM scheduled_job_leases WHERE workspace_id = :workspace_id"
                ),
                {"workspace_id": second_workspace},
            )
            == 0
        )
        assert (
            connection.execute(
                text(
                    "UPDATE scheduled_job_leases SET lease_token = 'forbidden' "
                    "WHERE workspace_id = :workspace_id"
                ),
                {"workspace_id": second_workspace},
            ).rowcount
            == 0
        )
        connection.rollback()

        transaction = connection.begin()
        connection.execute(
            text("SELECT set_config('expenseops.workspace_id', :workspace_id, true)"),
            {"workspace_id": str(first_workspace)},
        )
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "INSERT INTO scheduled_job_leases "
                    "(workspace_id, job_name, lease_token, lease_expires_at, "
                    "acquired_at, updated_at) VALUES "
                    "(:workspace_id, 'ci-cross-workspace-insert', 'forbidden', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"workspace_id": second_workspace},
            )
        transaction.rollback()

        connection.execute(
            text("SELECT set_config('expenseops.workspace_id', :workspace_id, true)"),
            {"workspace_id": str(second_workspace)},
        )
        visible = connection.execute(
            text("SELECT workspace_id, job_name FROM scheduled_job_leases ORDER BY job_name")
        ).all()
        assert visible == [(second_workspace, "ci-rls-second")]

        assert (
            connection.execute(
                text(
                    "SELECT * FROM public.expenseops_route_plaid_item('ci-nonexistent-plaid-item')"
                )
            ).all()
            == []
        )


@pytest.mark.skipif(
    not HARDENED_RLS_EXPECTED,
    reason="parent-derived child policies are introduced at the hardening boundary",
)
@pytest.mark.parametrize(
    ("table", "first_id", "second_id", "cross_workspace_update", "cross_workspace_insert"),
    (
        (
            "purchase_receipt_items",
            900021,
            900022,
            "UPDATE purchase_receipt_items SET household_item_id = 900012 WHERE id = 900021",
            """
            INSERT INTO purchase_receipt_items
                (receipt_id, raw_name, normalized_name, household_item_id,
                 match_status, created_at, updated_at)
            VALUES
                (900021, 'CI forbidden receipt item', 'ci-forbidden-receipt-item',
                 900012, 'matched', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
        ),
        (
            "household_item_aliases",
            900011,
            900012,
            "UPDATE household_item_aliases SET household_item_id = 900012 WHERE id = 900011",
            """
            INSERT INTO household_item_aliases
                (household_item_id, merchant_normalized, raw_pattern,
                 normalized_alias, confidence, source, created_at)
            VALUES
                (900012, 'ci-forbidden', 'CI forbidden alias',
                 'ci-forbidden-alias', 1.0, 'user', CURRENT_TIMESTAMP)
            """,
        ),
        (
            "promotion_feedback",
            900031,
            900032,
            "UPDATE promotion_feedback SET promotion_offer_id = 900032 WHERE id = 900031",
            """
            INSERT INTO promotion_feedback
                (promotion_offer_id, feedback_type, occurred_at, metadata_json, created_at)
            VALUES
                (900032, 'dismissed', CURRENT_TIMESTAMP, '{}'::json, CURRENT_TIMESTAMP)
            """,
        ),
        (
            "errand_household_items",
            900041,
            900042,
            "UPDATE errand_household_items SET household_item_id = 900012 WHERE id = 900041",
            """
            INSERT INTO errand_household_items
                (errand_id, household_item_id, created_at)
            VALUES (900041, 900012, CURRENT_TIMESTAMP)
            """,
        ),
        (
            "errand_plan_stops",
            900051,
            900052,
            "UPDATE errand_plan_stops SET plan_id = 900052 WHERE id = 900051",
            """
            INSERT INTO errand_plan_stops
                (plan_id, stop_order, place_name, created_at)
            VALUES (900052, 99, 'CI forbidden stop', CURRENT_TIMESTAMP)
            """,
        ),
        (
            "errand_plan_stop_errands",
            900061,
            900062,
            "UPDATE errand_plan_stop_errands SET errand_id = 900042 WHERE id = 900061",
            """
            INSERT INTO errand_plan_stop_errands
                (stop_id, errand_id, created_at)
            VALUES (900051, 900042, CURRENT_TIMESTAMP)
            """,
        ),
        (
            "errand_plan_stop_household_items",
            900071,
            900072,
            """
            UPDATE errand_plan_stop_household_items
            SET household_item_id = 900012
            WHERE id = 900071
            """,
            """
            INSERT INTO errand_plan_stop_household_items
                (stop_id, household_item_id, reason, created_at)
            VALUES (900051, 900012, 'CI forbidden link', CURRENT_TIMESTAMP)
            """,
        ),
    ),
)
def test_runtime_role_enforces_every_parent_derived_child_policy(
    table: str,
    first_id: int,
    second_id: int,
    cross_workspace_update: str,
    cross_workspace_insert: str,
):
    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    first_workspace = int(os.environ.get("RLS_TEST_FIRST_WORKSPACE_ID", "1"))
    second_workspace = int(os.environ.get("RLS_TEST_SECOND_WORKSPACE_ID", "900002"))

    with engine.connect() as connection:
        connection.execute(
            text("SELECT set_config('expenseops.workspace_id', :workspace_id, true)"),
            {"workspace_id": str(first_workspace)},
        )
        connection.execute(text("SELECT set_config('expenseops.bypass_rls', 'on', true)"))
        assert connection.execute(text(f"SELECT id FROM {table} ORDER BY id")).scalars().all() == [
            first_id
        ]
        assert (
            connection.execute(
                text(f"UPDATE {table} SET id = id WHERE id = :second_id"),
                {"second_id": second_id},
            ).rowcount
            == 0
        )
        connection.rollback()

        transaction = connection.begin()
        connection.execute(
            text("SELECT set_config('expenseops.workspace_id', :workspace_id, true)"),
            {"workspace_id": str(second_workspace)},
        )
        assert connection.execute(text(f"SELECT id FROM {table} ORDER BY id")).scalars().all() == [
            second_id
        ]
        transaction.rollback()

        transaction = connection.begin()
        connection.execute(
            text("SELECT set_config('expenseops.workspace_id', :workspace_id, true)"),
            {"workspace_id": str(first_workspace)},
        )
        with pytest.raises(DBAPIError):
            connection.execute(text(cross_workspace_update))
        transaction.rollback()

        transaction = connection.begin()
        connection.execute(
            text("SELECT set_config('expenseops.workspace_id', :workspace_id, true)"),
            {"workspace_id": str(first_workspace)},
        )
        with pytest.raises(DBAPIError):
            connection.execute(text(cross_workspace_insert))
        transaction.rollback()


def test_tenant_routing_functions_reject_stale_rows_without_active_membership():
    from app.models import (
        PlaidItem,
        TelegramIdentity,
        TelegramLinkCode,
        User,
        WorkspaceInvitation,
        WorkspaceMembership,
        utc_now,
    )
    from app.tenancy import clear_session_tenant, set_trusted_workspace

    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    workspace_id = int(os.environ.get("RLS_TEST_FIRST_WORKSPACE_ID", "1"))
    suffix = uuid4().hex

    with Session(engine) as db:
        user = User(
            email=f"ci-stale-routing-{suffix}@example.test",
            display_name="CI stale routing",
        )
        db.add(user)
        db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace_id,
                user_id=user.id,
                role="member",
            )
        )
        db.commit()

        set_trusted_workspace(db, workspace_id)
        db.add_all(
            [
                PlaidItem(
                    workspace_id=workspace_id,
                    item_id=f"ci-stale-plaid-{suffix}",
                    owner_user_id=user.id,
                    ownership_verified_at=utc_now(),
                    access_token_encrypted="ci-stale-plaid-secret",
                ),
                TelegramIdentity(
                    workspace_id=workspace_id,
                    user_id=user.id,
                    telegram_user_id=f"ci-stale-telegram-{suffix}",
                    chat_id=f"ci-stale-chat-{suffix}",
                ),
                TelegramLinkCode(
                    workspace_id=workspace_id,
                    user_id=user.id,
                    code_hash=f"ci-stale-link-{suffix}",
                    expires_at=utc_now() + timedelta(hours=1),
                ),
                WorkspaceInvitation(
                    workspace_id=workspace_id,
                    email=f"ci-stale-invitee-{suffix}@example.test",
                    token_hash=f"ci-stale-invite-{suffix}",
                    invited_by_user_id=user.id,
                    expires_at=utc_now() + timedelta(hours=1),
                ),
            ]
        )
        db.commit()

        db.execute(
            text(
                "DELETE FROM workspace_memberships "
                "WHERE workspace_id = :workspace_id AND user_id = :user_id"
            ),
            {"workspace_id": workspace_id, "user_id": user.id},
        )
        db.commit()
        clear_session_tenant(db)

        routed_rows = {
            "plaid": db.execute(
                text("SELECT * FROM public.expenseops_route_plaid_item(:value)"),
                {"value": f"ci-stale-plaid-{suffix}"},
            ).all(),
            "telegram_identity": db.execute(
                text(
                    "SELECT * FROM public.expenseops_route_telegram_identity"
                    "(:telegram_user_id, :chat_id)"
                ),
                {
                    "telegram_user_id": f"ci-stale-telegram-{suffix}",
                    "chat_id": f"ci-stale-chat-{suffix}",
                },
            ).all(),
            "active_telegram_identity": db.execute(
                text(
                    "SELECT * FROM "
                    "public.expenseops_route_active_telegram_identity_by_link_code(:value)"
                ),
                {"value": f"ci-stale-link-{suffix}"},
            ).all(),
            "telegram_link": db.execute(
                text("SELECT * FROM public.expenseops_route_telegram_link_code(:value)"),
                {"value": f"ci-stale-link-{suffix}"},
            ).all(),
            "invitation": db.execute(
                text("SELECT * FROM public.expenseops_route_workspace_invitation(:value)"),
                {"value": f"ci-stale-invite-{suffix}"},
            ).all(),
        }
        assert routed_rows == {name: [] for name in routed_rows}


def test_account_deletion_revokes_credentials_in_every_workspace_under_rls():
    from app.config import get_settings
    from app.models import (
        DataConsent,
        GmailAccount,
        PlaidItem,
        SplitwiseIntegration,
        TelegramIdentity,
        TelegramLinkCode,
        User,
        WorkspaceMembership,
        utc_now,
    )
    from app.services.data_lifecycle_service import DataLifecycleService
    from app.tenancy import TenantContext, set_session_tenant, set_trusted_workspace

    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    first_workspace = int(os.environ.get("RLS_TEST_FIRST_WORKSPACE_ID", "1"))
    second_workspace = int(os.environ.get("RLS_TEST_SECOND_WORKSPACE_ID", "900002"))
    deleting_user_id = 900003
    credential_models = (
        DataConsent,
        GmailAccount,
        SplitwiseIntegration,
        TelegramIdentity,
        TelegramLinkCode,
    )

    with Session(engine) as db:
        deleting_user = User(
            id=deleting_user_id,
            email="ci-delete-across-workspaces@example.test",
            display_name="CI delete across workspaces",
        )
        db.add(deleting_user)
        if (
            db.scalar(
                select(WorkspaceMembership.id).where(
                    WorkspaceMembership.workspace_id == second_workspace,
                    WorkspaceMembership.user_id == second_workspace,
                )
            )
            is None
        ):
            db.add(
                WorkspaceMembership(
                    workspace_id=second_workspace,
                    user_id=second_workspace,
                    role="owner",
                    is_default=True,
                )
            )
        db.add_all(
            [
                WorkspaceMembership(
                    workspace_id=workspace_id,
                    user_id=deleting_user_id,
                    role="member",
                    is_default=workspace_id == first_workspace,
                )
                for workspace_id in (first_workspace, second_workspace)
            ]
        )
        db.commit()

        for workspace_id in (first_workspace, second_workspace):
            suffix = str(workspace_id)
            set_trusted_workspace(db, workspace_id)
            db.add_all(
                [
                    DataConsent(
                        workspace_id=workspace_id,
                        user_id=deleting_user_id,
                        purpose="gmail_receipts",
                        granted=True,
                    ),
                    GmailAccount(
                        workspace_id=workspace_id,
                        user_id=deleting_user_id,
                        google_user_id=f"ci-delete-{suffix}@example.test",
                        refresh_token_encrypted=f"gmail-credential-{suffix}",
                    ),
                    SplitwiseIntegration(
                        workspace_id=workspace_id,
                        user_id=deleting_user_id,
                        credentials_encrypted=f"splitwise-credential-{suffix}",
                    ),
                    TelegramIdentity(
                        workspace_id=workspace_id,
                        user_id=deleting_user_id,
                        telegram_user_id=f"ci-delete-telegram-{suffix}",
                        chat_id=f"ci-delete-chat-{suffix}",
                        enabled=False,
                    ),
                    TelegramLinkCode(
                        workspace_id=workspace_id,
                        user_id=deleting_user_id,
                        code_hash=f"ci-delete-link-{suffix}",
                        expires_at=utc_now() + timedelta(hours=1),
                    ),
                    PlaidItem(
                        workspace_id=workspace_id,
                        item_id=f"ci-delete-plaid-{suffix}",
                        owner_user_id=deleting_user_id,
                        ownership_verified_at=utc_now(),
                        access_token_encrypted=f"plaid-credential-{suffix}",
                    ),
                ]
            )
            db.commit()

        set_session_tenant(
            db,
            TenantContext(user_id=deleting_user_id, workspace_id=first_workspace),
        )
        DataLifecycleService(db, get_settings()).delete_account(db.get(User, deleting_user_id))

        assert db.info["workspace_id"] == first_workspace
        assert db.get(User, deleting_user_id).status == "deleted"
        assert (
            db.scalar(
                select(WorkspaceMembership.id).where(
                    WorkspaceMembership.user_id == deleting_user_id
                )
            )
            is None
        )
        for workspace_id in (first_workspace, second_workspace):
            set_trusted_workspace(db, workspace_id)
            for model in credential_models:
                assert db.scalar(select(model.id).where(model.user_id == deleting_user_id)) is None
            plaid_item = db.scalar(
                select(PlaidItem).where(PlaidItem.item_id == f"ci-delete-plaid-{workspace_id}")
            )
            assert plaid_item is not None
            assert plaid_item.owner_user_id is None
            assert plaid_item.ownership_verified_at is None
            assert plaid_item.access_token_encrypted is None
            assert plaid_item.enabled is False

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

import app.tenant_routing as tenant_routing
from app.db import Base
from app.models import (
    PlaidItem,
    TelegramIdentity,
    TelegramLinkCode,
    TenantScoped,
    User,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
)

EXPECTED_TENANT_TABLES = {
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
}

EXPECTED_ROUTING_SIGNATURES = {
    "public.expenseops_route_plaid_item(text)",
    "public.expenseops_route_telegram_identity(text, text)",
    "public.expenseops_route_active_telegram_identity_by_link_code(text)",
    "public.expenseops_route_telegram_link_code(text)",
    "public.expenseops_route_workspace_invitation(text)",
}
SCRIPTS = ScriptDirectory.from_config(Config("alembic.ini"))
HAS_POLICY_HARDENING = any(
    revision.revision == "20260815_0029" for revision in SCRIPTS.walk_revisions()
)


def _migration(revision: str):
    return ScriptDirectory.from_config(Config("alembic.ini")).get_revision(revision).module


class _RecordingOp:
    def __init__(self, dialect: str = "postgresql") -> None:
        self.dialect = dialect
        self.statements: list[str] = []

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

    def execute(self, statement) -> None:
        self.statements.append(str(statement))


def test_tenant_routing_and_policy_hardening_are_linear_head():
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))

    assert scripts.get_revision("20260815_0028").down_revision == "20260815_0027"
    if HAS_POLICY_HARDENING:
        assert scripts.get_revision("20260815_0029").down_revision == "20260815_0028"
        next_revision = scripts.get_revision("20260817_0030")
        if next_revision is not None:
            assert next_revision.down_revision == "20260815_0029"
            assert scripts.get_current_head() == "20260817_0030"
        else:
            assert scripts.get_current_head() == "20260815_0029"
    else:
        assert scripts.get_current_head() == "20260815_0028"


def test_routing_migration_creates_only_narrow_hardened_functions(monkeypatch):
    migration = _migration("20260815_0028")
    recorder = _RecordingOp()
    monkeypatch.setattr(migration, "op", recorder)

    migration.upgrade()

    assert {signature for signature, _definition in migration.ROUTING_FUNCTIONS} == (
        EXPECTED_ROUTING_SIGNATURES
    )
    assert len(recorder.statements) == len(EXPECTED_ROUTING_SIGNATURES) * 2
    definitions = recorder.statements[::2]
    revocations = recorder.statements[1::2]
    for definition in definitions:
        normalized = " ".join(definition.split())
        assert "SECURITY DEFINER" in normalized
        assert "SET search_path = pg_catalog, pg_temp" in normalized
        assert "FROM public." in normalized
        assert "SELECT *" not in normalized
        assert "EXECUTE" not in normalized
        assert "ROW LEVEL SECURITY" not in normalized
        assert "CREATE POLICY" not in normalized
        assert "JOIN public.workspace_memberships" in normalized
        assert "JOIN public.users" in normalized
        assert "status = 'active'" in normalized
    assert set(revocations) == {
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC"
        for signature in EXPECTED_ROUTING_SIGNATURES
    }

    returns_by_signature = {
        signature: " ".join(definition.split())
        .split("RETURNS TABLE ", maxsplit=1)[1]
        .split(" LANGUAGE ", maxsplit=1)[0]
        for signature, definition in migration.ROUTING_FUNCTIONS
    }
    assert returns_by_signature["public.expenseops_route_plaid_item(text)"] == (
        "(workspace_id integer, plaid_item_id integer)"
    )
    assert (
        returns_by_signature["public.expenseops_route_telegram_identity(text, text)"]
        == "( workspace_id integer, telegram_identity_id integer, user_id integer )"
    )
    assert (
        returns_by_signature["public.expenseops_route_active_telegram_identity_by_link_code(text)"]
        == "(workspace_id integer, telegram_identity_id integer)"
    )
    assert (
        returns_by_signature["public.expenseops_route_telegram_link_code(text)"]
        == "( workspace_id integer, telegram_link_code_id integer, user_id integer )"
    )
    assert (
        returns_by_signature["public.expenseops_route_workspace_invitation(text)"]
        == "(workspace_id integer, workspace_invitation_id integer)"
    )


def test_routing_migration_revokes_before_drop_and_is_sqlite_neutral(monkeypatch):
    migration = _migration("20260815_0028")
    recorder = _RecordingOp()
    monkeypatch.setattr(migration, "op", recorder)

    migration.downgrade()

    assert recorder.statements == [
        f"DROP FUNCTION IF EXISTS {signature}"
        for signature in reversed(tuple(signature for signature, _ in migration.ROUTING_FUNCTIONS))
    ]

    sqlite_recorder = _RecordingOp("sqlite")
    monkeypatch.setattr(migration, "op", sqlite_recorder)
    migration.upgrade()
    migration.downgrade()
    assert sqlite_recorder.statements == []


@pytest.mark.skipif(
    not HAS_POLICY_HARDENING,
    reason="the compatibility commit intentionally stops at tenant routing",
)
def test_policy_hardening_covers_exact_tenant_model_set_without_escape(monkeypatch):
    migration = _migration("20260815_0029")
    recorder = _RecordingOp()
    monkeypatch.setattr(migration, "op", recorder)

    migration.upgrade()

    model_tables = {
        mapper.local_table.name
        for mapper in Base.registry.mappers
        if issubclass(mapper.class_, TenantScoped)
    }
    assert set(migration.TENANT_TABLES) == EXPECTED_TENANT_TABLES == model_tables
    assert len(migration.TENANT_TABLES) == len(set(migration.TENANT_TABLES)) == 34
    protected_tables = {*migration.TENANT_TABLES, *migration.TENANT_CHILD_POLICIES}
    assert len(migration.TENANT_CHILD_POLICIES) == 7
    preflight_statement_count = 1 + len(migration.MULTI_PARENT_CHILD_INTEGRITY_CHECKS)
    assert len(recorder.statements) == preflight_statement_count + len(protected_tables) * 4
    for table in migration.TENANT_TABLES:
        assert f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY' in recorder.statements
        assert f'ALTER TABLE public."{table}" FORCE ROW LEVEL SECURITY' in recorder.statements
        assert (
            f'DROP POLICY IF EXISTS expenseops_workspace_isolation ON public."{table}"'
            in recorder.statements
        )
        policy = next(
            statement
            for statement in recorder.statements
            if statement.startswith(
                f'CREATE POLICY expenseops_workspace_isolation ON public."{table}"'
            )
        )
        assert "USING (workspace_id =" in policy
        assert "WITH CHECK (workspace_id =" in policy
        assert "current_setting('expenseops.workspace_id', true)" in policy
        assert " OR " not in policy
    for table, expected_expression in migration.TENANT_CHILD_POLICIES.items():
        assert f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY' in recorder.statements
        assert f'ALTER TABLE public."{table}" FORCE ROW LEVEL SECURITY' in recorder.statements
        policy = next(
            statement
            for statement in recorder.statements
            if statement.startswith(
                f'CREATE POLICY expenseops_workspace_isolation ON public."{table}"'
            )
        )
        assert expected_expression in policy
        assert "EXISTS (SELECT 1 FROM public." in policy
    assert "expenseops.bypass_rls" not in "\n".join(recorder.statements)


@pytest.mark.skipif(
    not HAS_POLICY_HARDENING,
    reason="the compatibility commit intentionally stops at tenant routing",
)
def test_policy_hardening_fails_closed_on_cross_workspace_multi_parent_links(monkeypatch):
    migration = _migration("20260815_0029")
    recorder = _RecordingOp()
    monkeypatch.setattr(migration, "op", recorder)

    migration.upgrade()

    expected_tables = {
        "purchase_receipt_items",
        "errand_household_items",
        "errand_plan_stop_errands",
        "errand_plan_stop_household_items",
    }
    assert set(migration.MULTI_PARENT_CHILD_INTEGRITY_CHECKS) == expected_tables
    assert recorder.statements[0].lstrip().startswith("LOCK TABLE")
    assert "IN SHARE ROW EXCLUSIVE MODE" in recorder.statements[0]
    for table in expected_tables:
        assert f"public.{table}" in recorder.statements[0]

    assertions = recorder.statements[1 : 1 + len(expected_tables)]
    assert len(assertions) == len(expected_tables)
    for table, query in migration.MULTI_PARENT_CHILD_INTEGRITY_CHECKS.items():
        assertion = next(statement for statement in assertions if f"in {table}" in statement)
        assert query in assertion
        assert "IF EXISTS" in assertion
        assert "RAISE EXCEPTION" in assertion
        assert "cross-workspace parent links exist" in assertion
        assert "ERRCODE = '23514'" in assertion
        assert assertion.index("IF EXISTS") < assertion.index("RAISE EXCEPTION")

    first_policy_change = next(
        index
        for index, statement in enumerate(recorder.statements)
        if statement.lstrip().startswith("ALTER TABLE")
    )
    assert first_policy_change == 1 + len(expected_tables)
    preflight_sql = "\n".join(recorder.statements[:first_policy_change])
    assert not any(
        operation in preflight_sql
        for operation in ("INSERT INTO", "UPDATE ", "DELETE FROM", "TRUNCATE ")
    )


@pytest.mark.skipif(
    not HAS_POLICY_HARDENING,
    reason="the compatibility commit intentionally stops at tenant routing",
)
def test_policy_hardening_downgrade_fails_safe(monkeypatch):
    migration = _migration("20260815_0029")
    recorder = _RecordingOp()
    monkeypatch.setattr(migration, "op", recorder)

    with pytest.raises(RuntimeError, match="irreversible tenant-security boundary"):
        migration.downgrade()
    assert recorder.statements == []

    sqlite_recorder = _RecordingOp("sqlite")
    monkeypatch.setattr(migration, "op", sqlite_recorder)
    migration.upgrade()
    migration.downgrade()
    assert sqlite_recorder.statements == []


@pytest.fixture
def routing_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(UTC)
    with factory() as db:
        users = [
            User(email="route-a@example.test", display_name="Route A"),
            User(email="route-b@example.test", display_name="Route B"),
        ]
        db.add_all(users)
        db.flush()
        workspaces = [
            Workspace(name="Route A", created_by_user_id=users[0].id),
            Workspace(name="Route B", created_by_user_id=users[1].id),
        ]
        db.add_all(workspaces)
        db.flush()
        db.add_all(
            [
                WorkspaceMembership(
                    workspace_id=workspaces[index].id,
                    user_id=users[index].id,
                    role="owner",
                    is_default=True,
                )
                for index in range(2)
            ]
        )
        plaid = PlaidItem(
            workspace_id=workspaces[1].id,
            item_id="plaid-route-b",
            owner_user_id=users[1].id,
            access_token_encrypted="not-returned",
        )
        identity = TelegramIdentity(
            workspace_id=workspaces[1].id,
            user_id=users[1].id,
            telegram_user_id="telegram-route-b",
            chat_id="chat-route-b",
            enabled=True,
        )
        link = TelegramLinkCode(
            workspace_id=workspaces[1].id,
            user_id=users[1].id,
            code_hash="link-hash-route-b",
            expires_at=now + timedelta(minutes=10),
        )
        invitation = WorkspaceInvitation(
            workspace_id=workspaces[1].id,
            email="invitee@example.test",
            token_hash="invite-hash-route-b",
            invited_by_user_id=users[1].id,
            expires_at=now + timedelta(days=1),
        )
        db.add_all([plaid, identity, link, invitation])
        db.commit()
        expected = {
            "workspace_id": workspaces[1].id,
            "user_id": users[1].id,
            "plaid_item_id": plaid.id,
            "telegram_identity_id": identity.id,
            "telegram_link_code_id": link.id,
            "workspace_invitation_id": invitation.id,
        }
    try:
        with factory() as db:
            yield db, expected
    finally:
        engine.dispose()


def test_sqlite_routing_scans_workspace_by_workspace_and_returns_only_ids(routing_db):
    db, expected = routing_db

    plaid = tenant_routing.route_plaid_item(db, "plaid-route-b")
    assert asdict(plaid) == {
        "workspace_id": expected["workspace_id"],
        "plaid_item_id": expected["plaid_item_id"],
    }
    identity = tenant_routing.route_telegram_identity(
        db,
        "telegram-route-b",
        "chat-route-b",
    )
    assert asdict(identity) == {
        "workspace_id": expected["workspace_id"],
        "telegram_identity_id": expected["telegram_identity_id"],
        "user_id": expected["user_id"],
    }
    active = tenant_routing.route_active_telegram_identity_by_link_code(db, "link-hash-route-b")
    assert asdict(active) == {
        "workspace_id": expected["workspace_id"],
        "telegram_identity_id": expected["telegram_identity_id"],
    }
    link = tenant_routing.route_telegram_link_code(db, "link-hash-route-b")
    assert asdict(link) == {
        "workspace_id": expected["workspace_id"],
        "telegram_link_code_id": expected["telegram_link_code_id"],
        "user_id": expected["user_id"],
    }
    invitation = tenant_routing.route_workspace_invitation(db, "invite-hash-route-b")
    assert asdict(invitation) == {
        "workspace_id": expected["workspace_id"],
        "workspace_invitation_id": expected["workspace_invitation_id"],
    }
    assert db.info["workspace_id"] == expected["workspace_id"]
    assert "trusted_global_scope" not in db.info

    assert tenant_routing.route_plaid_item(db, "missing") is None
    assert "workspace_id" not in db.info
    assert "trusted_global_scope" not in db.info


def test_routing_rejects_provider_rows_after_workspace_membership_is_removed(routing_db):
    db, expected = routing_db
    db.execute(
        delete(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == expected["workspace_id"],
            WorkspaceMembership.user_id == expected["user_id"],
        )
    )
    db.commit()

    assert tenant_routing.route_plaid_item(db, "plaid-route-b") is None
    assert (
        tenant_routing.route_telegram_identity(
            db,
            "telegram-route-b",
            "chat-route-b",
        )
        is None
    )
    assert (
        tenant_routing.route_active_telegram_identity_by_link_code(
            db,
            "link-hash-route-b",
        )
        is None
    )
    assert tenant_routing.route_telegram_link_code(db, "link-hash-route-b") is None
    assert tenant_routing.route_workspace_invitation(db, "invite-hash-route-b") is None
    assert "workspace_id" not in db.info


def test_routing_rejects_provider_rows_for_a_deleted_workspace_member(routing_db):
    db, expected = routing_db
    user = db.get(User, expected["user_id"])
    user.status = "deleted"
    db.commit()

    assert tenant_routing.route_plaid_item(db, "plaid-route-b") is None
    assert (
        tenant_routing.route_telegram_identity(
            db,
            "telegram-route-b",
            "chat-route-b",
        )
        is None
    )
    assert tenant_routing.route_telegram_link_code(db, "link-hash-route-b") is None
    assert tenant_routing.route_workspace_invitation(db, "invite-hash-route-b") is None
    assert "workspace_id" not in db.info


@pytest.mark.parametrize(
    ("route_call", "row", "function_name", "expected"),
    [
        (
            lambda db: tenant_routing.route_plaid_item(db, "external-item"),
            {"workspace_id": 7, "plaid_item_id": 11},
            "public.expenseops_route_plaid_item",
            tenant_routing.PlaidItemRoute(7, 11),
        ),
        (
            lambda db: tenant_routing.route_telegram_identity(db, "tg-user", "tg-chat"),
            {"workspace_id": 7, "telegram_identity_id": 12, "user_id": 3},
            "public.expenseops_route_telegram_identity",
            tenant_routing.TelegramIdentityRoute(7, 12, 3),
        ),
        (
            lambda db: tenant_routing.route_active_telegram_identity_by_link_code(db, "link-hash"),
            {"workspace_id": 7, "telegram_identity_id": 12},
            "public.expenseops_route_active_telegram_identity_by_link_code",
            tenant_routing.ActiveTelegramIdentityRoute(7, 12),
        ),
        (
            lambda db: tenant_routing.route_telegram_link_code(db, "link-hash"),
            {"workspace_id": 7, "telegram_link_code_id": 13, "user_id": 3},
            "public.expenseops_route_telegram_link_code",
            tenant_routing.TelegramLinkCodeRoute(7, 13, 3),
        ),
        (
            lambda db: tenant_routing.route_workspace_invitation(db, "invite-hash"),
            {"workspace_id": 7, "workspace_invitation_id": 14},
            "public.expenseops_route_workspace_invitation",
            tenant_routing.WorkspaceInvitationRoute(7, 14),
        ),
    ],
)
def test_postgres_routing_uses_only_static_functions_and_establishes_scope(
    monkeypatch,
    route_call,
    row,
    function_name,
    expected,
):
    calls: list[tuple[str, object]] = []

    class _Result:
        def __init__(self, value):
            self.value = value

        def mappings(self):
            return self

        def one_or_none(self):
            return self.value

    class _PostgresSession:
        @staticmethod
        def get_bind():
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        @staticmethod
        def execute(statement, parameters):
            statement_text = str(statement)
            calls.append((statement_text, parameters))
            if (
                "expenseops_route_active_telegram_identity_by_link_code" in function_name
                and "expenseops_route_telegram_link_code" in statement_text
            ):
                return _Result({"workspace_id": 7, "telegram_link_code_id": 13, "user_id": 3})
            return _Result(row)

        @staticmethod
        def get(model, _row_id):
            if model is PlaidItem:
                return SimpleNamespace(
                    workspace_id=7,
                    item_id="external-item",
                    owner_user_id=3,
                )
            if model is TelegramIdentity:
                return SimpleNamespace(
                    workspace_id=7,
                    user_id=3,
                    telegram_user_id="tg-user",
                    chat_id="tg-chat",
                    enabled=True,
                )
            if model is TelegramLinkCode:
                return SimpleNamespace(
                    workspace_id=7,
                    user_id=3,
                    code_hash="link-hash",
                )
            if model is WorkspaceInvitation:
                return SimpleNamespace(
                    workspace_id=7,
                    token_hash="invite-hash",
                    invited_by_user_id=3,
                )
            raise AssertionError(f"unexpected route model: {model}")

        @staticmethod
        def scalar(_statement):
            return 1

    monkeypatch.setattr(
        tenant_routing,
        "clear_session_tenant",
        lambda db: calls.append(("clear", db)),
    )
    monkeypatch.setattr(
        tenant_routing,
        "set_trusted_workspace",
        lambda db, workspace_id: calls.append(("scope", (db, workspace_id))),
    )
    db = _PostgresSession()

    assert route_call(db) == expected
    sql, parameters = next(
        (sql, parameters)
        for sql, parameters in calls
        if isinstance(sql, str) and function_name in sql
    )
    assert function_name in sql
    assert "public." in sql
    assert all(str(value) not in sql for value in parameters.values())
    assert calls[0] == ("clear", db)
    assert calls[-1] == ("scope", (db, 7))


def test_postgres_routing_miss_leaves_session_unscoped(monkeypatch):
    calls: list[tuple[str, object]] = []

    class _Result:
        @staticmethod
        def mappings():
            return _Result()

        @staticmethod
        def one_or_none():
            return None

    class _PostgresSession:
        @staticmethod
        def get_bind():
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        @staticmethod
        def execute(_statement, _parameters):
            return _Result()

    monkeypatch.setattr(
        tenant_routing,
        "clear_session_tenant",
        lambda db: calls.append(("clear", db)),
    )
    monkeypatch.setattr(
        tenant_routing,
        "set_trusted_workspace",
        lambda db, workspace_id: calls.append(("scope", (db, workspace_id))),
    )
    db = _PostgresSession()

    assert tenant_routing.route_plaid_item(db, "missing") is None
    assert calls == [("clear", db)]

from __future__ import annotations

from pathlib import Path

import pytest

from app import models  # noqa: F401
from app.db import Base
from scripts import bootstrap_database_roles as bootstrap


def test_application_table_allowlist_matches_metadata_plus_alembic_version():
    assert set(bootstrap.APPLICATION_TABLES) == {
        *Base.metadata.tables,
        "alembic_version",
    }
    assert len(bootstrap.APPLICATION_TABLES) == len(set(bootstrap.APPLICATION_TABLES)) == 52


def test_role_plan_has_exact_attributes_and_no_membership_inheritance():
    plan = bootstrap.render_plan()

    assert (
        "ALTER ROLE expenseops_runtime WITH LOGIN NOSUPERUSER NOBYPASSRLS "
        "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION"
    ) in plan
    assert (
        "ALTER ROLE expenseops_migrator WITH LOGIN NOSUPERUSER BYPASSRLS "
        "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION"
    ) in plan
    assert "FROM pg_catalog.pg_auth_members" in plan
    assert plan.count("JOIN pg_catalog.pg_roles AS granted") == 2
    assert "REVOKE %I FROM %I" in plan
    assert "OR granted.rolname IN" in plan
    assert "GRANT expenseops_migrator TO expenseops_runtime" not in plan
    assert "GRANT expenseops_runtime TO expenseops_migrator" not in plan
    assert "runtime_record.rolinherit" in plan
    assert "migrator_record.rolinherit" in plan


def test_role_plan_establishes_owner_and_least_privilege_default_acls():
    plan = bootstrap.render_plan()

    assert plan.count("SET search_path = public, pg_catalog, pg_temp") == 2
    assert "ALTER SCHEMA public OWNER TO expenseops_migrator" in plan
    assert "ALTER TABLE %I.%I OWNER TO expenseops_migrator" in plan
    assert "ALTER SEQUENCE %I.%I OWNER TO expenseops_migrator" in plan
    assert "GRANT USAGE ON SCHEMA public TO expenseops_runtime" in plan
    assert plan.count("REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC") == 2
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE %s TO expenseops_runtime" in plan
    assert "GRANT USAGE, SELECT ON SEQUENCE %s TO expenseops_runtime" in plan
    assert "GRANT UPDATE ON SEQUENCE" not in plan
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE expenseops_migrator" in plan
    assert "REVOKE ALL PRIVILEGES ON TABLES FROM expenseops_runtime" in plan
    assert "REVOKE ALL PRIVILEGES ON SEQUENCES FROM expenseops_runtime" in plan
    assert "REVOKE ALL PRIVILEGES ON TYPES FROM PUBLIC" in plan
    assert "REVOKE ALL PRIVILEGES ON TYPES FROM expenseops_runtime" in plan
    assert (
        "ALTER DEFAULT PRIVILEGES FOR ROLE expenseops_migrator\n"
        "    REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC"
    ) in plan
    assert (
        "ALTER DEFAULT PRIVILEGES FOR ROLE expenseops_migrator\n"
        "    REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC"
    ) in plan
    assert (
        "ALTER DEFAULT PRIVILEGES FOR ROLE expenseops_migrator\n"
        "    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    ) in plan
    assert (
        "ALTER DEFAULT PRIVILEGES FOR ROLE expenseops_migrator\n"
        "    REVOKE ALL PRIVILEGES ON TYPES FROM PUBLIC"
    ) in plan
    assert "REVOKE EXECUTE ON FUNCTIONS FROM expenseops_runtime" in plan
    assert "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC" in plan


def test_role_plan_revokes_only_independently_grantable_public_types():
    plan = bootstrap.render_plan()

    assert "REVOKE ALL PRIVILEGES ON ALL TYPES IN SCHEMA" not in plan
    assert "FOR grantable_type IN" in plan
    assert "type_object.typelem <> 0" in plan
    assert "type_object.typsubscript =" in plan
    assert "'pg_catalog.array_subscript_handler'::pg_catalog.regproc" in plan
    assert "type_object.typtype <> 'm'" in plan
    assert "REVOKE ALL PRIVILEGES ON TYPE %I.%I FROM PUBLIC" in plan
    assert "REVOKE ALL PRIVILEGES ON TYPE %I.%I FROM expenseops_runtime" in plan
    assert bootstrap.RUNTIME_GRANTS_SQL.count("type_object.typtype <> 'm'") == 1
    assert bootstrap.VERIFY_SQL.count("type_object.typtype <> 'm'") == 1


def test_bootstrap_is_idempotent_and_avoids_broad_ownership_reassignment():
    plan = bootstrap.render_plan()

    assert plan.count("IF NOT EXISTS (") >= 2
    assert "to_regclass" in plan
    assert "to_regprocedure" in plan
    assert "REASSIGN OWNED" not in plan
    assert "DROP OWNED" not in plan
    assert "migration role owns an unexpected public relation" in plan
    assert "migration role owns unexpected function" in plan


def test_runtime_cannot_write_schema_or_alembic_version():
    plan = bootstrap.render_plan()

    assert "REVOKE CONNECT, CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC" in plan
    assert "REVOKE ALL PRIVILEGES ON SCHEMA public FROM expenseops_runtime" in plan
    assert "GRANT USAGE ON SCHEMA public TO expenseops_runtime" in plan
    assert "IF object_name = 'alembic_version'" in plan
    assert "GRANT SELECT ON TABLE %s TO expenseops_runtime" in plan
    assert "runtime alembic_version privileges are unsafe" in plan
    assert "runtime has privileges on an unexpected public relation" in plan
    assert "runtime has privileges on an unexpected public sequence" in plan
    assert "runtime has privileges on an unexpected public type" in plan
    assert "PUBLIC database privileges are unsafe" in plan
    assert "PUBLIC schema privileges are unsafe" in plan
    assert "expenseops_migrator must not own the database" in plan
    assert "migration role owns an unexpected public sequence" in plan
    assert (
        "has_database_privilege(\n        'expenseops_migrator', current_database(), 'TEMPORARY'"
    ) in plan
    for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert f"has_table_privilege('expenseops_runtime', relation_oid, '{privilege}')" in plan


def test_verification_sql_has_valid_membership_aliases_and_loop_terminators():
    assert "JOIN pg_catalog.pg_roles AS member" in bootstrap.VERIFY_SQL
    assert "JOIN pg_catalog.pg_roles AS granted" in bootstrap.VERIFY_SQL
    assert "END LOOP;\n    END LOOP;" not in bootstrap.VERIFY_SQL


def test_runtime_function_allowlist_is_exact_and_secret_routed():
    assert bootstrap.ROUTING_FUNCTIONS == (
        "public.expenseops_route_plaid_item(text)",
        "public.expenseops_route_telegram_identity(text,text)",
        "public.expenseops_route_active_telegram_identity_by_link_code(text)",
        "public.expenseops_route_telegram_link_code(text)",
        "public.expenseops_route_workspace_invitation(text)",
    )
    assert len(set(bootstrap.ROUTING_FUNCTIONS)) == 5
    assert all("(integer)" not in signature for signature in bootstrap.ROUTING_FUNCTIONS)

    plan = bootstrap.render_plan()
    assert "GRANT EXECUTE ON FUNCTION %s TO expenseops_runtime" in plan
    assert "GRANT EXECUTE ON ALL FUNCTIONS" not in plan
    assert "allowed_function_oids" in plan
    assert "runtime function privilege is unsafe" in plan


def test_default_invocation_is_non_connecting_secret_free_dry_run(monkeypatch, capsys):
    def fail_connect(*_args, **_kwargs):
        raise AssertionError("dry run must not connect")

    monkeypatch.setattr(bootstrap.psycopg, "connect", fail_connect)
    monkeypatch.setenv(bootstrap.ADMIN_URL_ENV, "postgresql://admin:do-not-print@db/railway")
    monkeypatch.setenv(bootstrap.RUNTIME_PASSWORD_ENV, "runtime-secret-must-not-print")
    monkeypatch.setenv(bootstrap.MIGRATOR_PASSWORD_ENV, "migrator-secret-must-not-print")

    assert bootstrap.main([]) == 0

    output = capsys.readouterr().out
    assert "dry run; nothing executed" in output
    assert "do-not-print" not in output
    assert "runtime-secret-must-not-print" not in output
    assert "migrator-secret-must-not-print" not in output
    assert bootstrap.RUNTIME_PASSWORD_ENV in output
    assert bootstrap.MIGRATOR_PASSWORD_ENV in output


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("admin", bootstrap.ADMIN_URL_ENV),
        ("runtime", bootstrap.RUNTIME_PASSWORD_ENV),
        ("migrator", bootstrap.MIGRATOR_PASSWORD_ENV),
    ],
)
def test_apply_requires_all_environment_only_credentials(monkeypatch, missing, message):
    values = {
        bootstrap.ADMIN_URL_ENV: "postgresql://admin@example.test/expenseops",
        bootstrap.RUNTIME_PASSWORD_ENV: "runtime-password-that-is-long-enough",
        bootstrap.MIGRATOR_PASSWORD_ENV: "migrator-password-that-is-long-enough",
    }
    missing_env = {
        "admin": bootstrap.ADMIN_URL_ENV,
        "runtime": bootstrap.RUNTIME_PASSWORD_ENV,
        "migrator": bootstrap.MIGRATOR_PASSWORD_ENV,
    }[missing]
    for name, value in values.items():
        if name == missing_env:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit, match=message):
        bootstrap.main(["--apply"])


def test_apply_uses_parameterized_passwords_and_verifies_before_commit(monkeypatch):
    calls: list[tuple[str, tuple[str, ...] | None]] = []
    connected: list[str] = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, _type, _value, _traceback):
            return False

        def execute(self, statement, parameters=None):
            calls.append((str(statement), parameters))

        @staticmethod
        def fetchone():
            return (True,)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, _type, _value, _traceback):
            return False

        @staticmethod
        def cursor():
            return FakeCursor()

    def fake_connect(dsn):
        connected.append(dsn)
        return FakeConnection()

    monkeypatch.setattr(bootstrap.psycopg, "connect", fake_connect)
    runtime_password = "runtime-password-that-is-long-enough"
    migrator_password = "migrator-password-that-is-long-enough"
    admin_url = "postgresql+psycopg://admin:private@example.test/expenseops"

    bootstrap.apply_bootstrap(
        admin_url=admin_url,
        runtime_password=runtime_password,
        migrator_password=migrator_password,
    )

    assert connected == ["postgresql://admin:private@example.test/expenseops"]
    sql_text = "\n".join(statement for statement, _parameters in calls)
    assert runtime_password not in sql_text
    assert migrator_password not in sql_text
    assert (
        (f"SELECT pg_catalog.set_config('{bootstrap.RUNTIME_PASSWORD_SETTING}', %s, true)"),
        (runtime_password,),
    ) in calls
    assert (
        (f"SELECT pg_catalog.set_config('{bootstrap.MIGRATOR_PASSWORD_SETTING}', %s, true)"),
        (migrator_password,),
    ) in calls
    assert (bootstrap.PASSWORD_SQL, None) in calls
    assert "PASSWORD %L" in bootstrap.PASSWORD_SQL
    assert calls[0][0] == "SET LOCAL search_path = pg_catalog, pg_temp"
    assert calls[-1][0] == bootstrap.VERIFY_SQL


def test_reconcile_uses_only_database_url_and_no_admin_secrets(monkeypatch, capsys):
    calls: list[str] = []
    migration_url = "postgresql://expenseops_migrator:private@example.test/expenseops"
    monkeypatch.setenv(bootstrap.MIGRATION_URL_ENV, migration_url)
    monkeypatch.delenv(bootstrap.ADMIN_URL_ENV, raising=False)
    monkeypatch.delenv(bootstrap.RUNTIME_PASSWORD_ENV, raising=False)
    monkeypatch.delenv(bootstrap.MIGRATOR_PASSWORD_ENV, raising=False)
    monkeypatch.setattr(
        bootstrap,
        "reconcile_runtime_grants",
        lambda *, database_url: calls.append(database_url),
    )

    assert bootstrap.main(["--reconcile-runtime-grants"]) == 0

    assert calls == [migration_url]
    output = capsys.readouterr().out
    assert "runtime database grants reconciled" in output
    assert "private" not in output


@pytest.mark.parametrize(
    "forbidden_name",
    (
        bootstrap.ADMIN_URL_ENV,
        bootstrap.RUNTIME_PASSWORD_ENV,
        bootstrap.MIGRATOR_PASSWORD_ENV,
    ),
)
def test_reconcile_rejects_bootstrap_or_admin_secret_scope(monkeypatch, forbidden_name):
    monkeypatch.setenv(
        bootstrap.MIGRATION_URL_ENV,
        "postgresql://expenseops_migrator:private@example.test/expenseops",
    )
    monkeypatch.setenv(forbidden_name, "x" * 32)

    with pytest.raises(SystemExit, match="must not be present"):
        bootstrap.main(["--reconcile-runtime-grants"])


def test_reconcile_requires_database_url(monkeypatch):
    monkeypatch.delenv(bootstrap.MIGRATION_URL_ENV, raising=False)
    monkeypatch.delenv(bootstrap.ADMIN_URL_ENV, raising=False)
    monkeypatch.delenv(bootstrap.RUNTIME_PASSWORD_ENV, raising=False)
    monkeypatch.delenv(bootstrap.MIGRATOR_PASSWORD_ENV, raising=False)

    with pytest.raises(SystemExit, match=bootstrap.MIGRATION_URL_ENV):
        bootstrap.main(["--reconcile-runtime-grants"])


def test_reconcile_authenticates_exact_migrator_then_applies_only_acl_phases(monkeypatch):
    calls: list[tuple[str, object]] = []
    connected: list[str] = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, _type, _value, _traceback):
            return False

        def execute(self, statement, parameters=None):
            calls.append((str(statement), parameters))

        @staticmethod
        def fetchone():
            return (
                bootstrap.MIGRATOR_ROLE,
                True,
                False,
                True,
                False,
                False,
                False,
                False,
            )

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, _type, _value, _traceback):
            return False

        @staticmethod
        def cursor():
            return FakeCursor()

    def fake_connect(dsn):
        connected.append(dsn)
        return FakeConnection()

    monkeypatch.setattr(bootstrap.psycopg, "connect", fake_connect)

    bootstrap.reconcile_runtime_grants(
        database_url="postgresql+psycopg://expenseops_migrator:secret@db/expenseops"
    )

    assert connected == ["postgresql://expenseops_migrator:secret@db/expenseops"]
    assert [statement for statement, _parameters in calls[-4:]] == [
        bootstrap.RECONCILE_REQUIRED_FUNCTIONS_SQL,
        bootstrap.RUNTIME_GRANTS_SQL,
        bootstrap.DEFAULT_PRIVILEGES_SQL,
        bootstrap.VERIFY_SQL,
    ]
    sql_text = "\n".join(statement for statement, _parameters in calls)
    assert "CREATE ROLE" not in sql_text
    assert "PASSWORD %s" not in sql_text
    assert bootstrap.OWNERSHIP_SQL not in sql_text
    identity_query = calls[len(bootstrap.SESSION_STATEMENTS)][0]
    assert "WHERE rolname = current_user" in identity_query
    assert "rolbypassrls" in identity_query


@pytest.mark.parametrize(
    "role_row",
    [
        ("postgres", True, True, True, True, True, True, True),
        (
            bootstrap.MIGRATOR_ROLE,
            True,
            False,
            False,
            False,
            False,
            False,
            False,
        ),
        (
            bootstrap.MIGRATOR_ROLE,
            True,
            True,
            True,
            False,
            False,
            False,
            False,
        ),
    ],
)
def test_reconcile_rejects_wrong_or_unsafe_current_role_before_acl_changes(
    monkeypatch,
    role_row,
):
    calls: list[str] = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, _type, _value, _traceback):
            return False

        def execute(self, statement, _parameters=None):
            calls.append(str(statement))

        @staticmethod
        def fetchone():
            return role_row

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, _type, _value, _traceback):
            return False

        @staticmethod
        def cursor():
            return FakeCursor()

    monkeypatch.setattr(bootstrap.psycopg, "connect", lambda _dsn: FakeConnection())

    with pytest.raises(RuntimeError, match="exact expenseops_migrator"):
        bootstrap.reconcile_runtime_grants(
            database_url="postgresql://expenseops_migrator:secret@db/expenseops"
        )
    assert bootstrap.RUNTIME_GRANTS_SQL not in calls
    assert bootstrap.RECONCILE_REQUIRED_FUNCTIONS_SQL not in calls
    assert bootstrap.DEFAULT_PRIVILEGES_SQL not in calls
    assert bootstrap.VERIFY_SQL not in calls


def test_reconcile_requires_all_five_routing_functions_before_granting():
    statement = bootstrap.RECONCILE_REQUIRED_FUNCTIONS_SQL

    assert bootstrap.ROUTING_FUNCTION_ARRAY_SQL in statement
    assert "to_regprocedure(route.signature) IS NULL" in statement
    assert "required tenant-routing function is missing" in statement


@pytest.mark.parametrize(
    ("admin_url", "runtime_password", "migrator_password", "message"),
    [
        (
            "sqlite:///expenseops.db",
            "runtime-password-that-is-long-enough",
            "migrator-password-that-is-long-enough",
            bootstrap.ADMIN_URL_ENV,
        ),
        (
            "postgresql://admin@example.test/expenseops",
            "short",
            "migrator-password-that-is-long-enough",
            "Runtime password is too short",
        ),
        (
            "postgresql://admin@example.test/expenseops",
            "same-password-that-is-long-enough",
            "same-password-that-is-long-enough",
            "must be different",
        ),
    ],
)
def test_apply_rejects_unsafe_inputs(
    admin_url,
    runtime_password,
    migrator_password,
    message,
):
    with pytest.raises(ValueError, match=message):
        bootstrap.apply_bootstrap(
            admin_url=admin_url,
            runtime_password=runtime_password,
            migrator_password=migrator_password,
        )


def test_role_bootstrap_documentation_preserves_two_pass_and_backup_boundary():
    documentation = Path("docs/PRODUCTION_DATABASE_ROLES.md").read_text(encoding="utf-8")
    documentation_words = " ".join(documentation.split())

    assert "Confirm and lock the approved recovery point/PITR window" in documentation
    assert documentation.count("--apply") >= 2
    assert "--reconcile-runtime-grants" in documentation
    assert "verifies that `current_user` is the exact expected" in documentation
    assert "migration, identity, grant, or verification failure must" in documentation.casefold()
    assert "Do not put these values in repository files" in documentation
    assert "never attach them to a deployed Railway service" in documentation
    assert "secrets.token_urlsafe(32)" in documentation
    assert "does not transfer database ownership" in documentation
    assert "Alembic itself commits before reconciliation" in documentation_words


def test_release_gate_runs_role_bootstrap_on_real_postgres_paths():
    workflow = Path(".github/workflows/release-gate.yml").read_text(encoding="utf-8")

    assert "postgres-security:" in workflow
    assert "ADMIN_FRESH_DATABASE_URL" in workflow
    assert "ADMIN_INCREMENTAL_DATABASE_URL" in workflow
    assert "python scripts/bootstrap_database_roles.py --apply" in workflow
    assert (
        workflow.count("python scripts/bootstrap_database_roles.py --reconcile-runtime-grants") == 2
    )
    assert "RUNTIME_FRESH_DATABASE_URL" in workflow
    assert "RUNTIME_INCREMENTAL_DATABASE_URL" in workflow
    assert workflow.count("pytest -q tests/test_postgres_release_security.py") == 2


def test_operations_runbook_preserves_partial_failure_and_rollback_boundaries():
    runbook = Path("docs/PRODUCTION_OPERATIONS_RUNBOOK.md").read_text(encoding="utf-8")
    runbook_words = " ".join(runbook.split())

    assert "separate Alembic and grant-reconciliation" in runbook
    assert "new schema may already be committed" in runbook
    assert "Before `0029`, do **not** use `release_phase=rollback`" in runbook
    assert "After `0029`, use `release_phase=rollback`" in runbook
    assert "database is at exactly `0029`" in runbook
    assert "Railway-created sibling Postgres service" in runbook
    assert "never restore over or reset the source" in runbook_words

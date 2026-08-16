from __future__ import annotations

import re
from copy import deepcopy
from inspect import getsource
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.main import (
    _CHILD_WORKSPACE_POLICY_EXPRESSIONS,
    _REQUIRED_ROUTING_FUNCTIONS,
    _ROUTING_FUNCTION_CONTRACTS,
    _normalize_policy_expression,
    _normalize_sql_contract,
    _policies_are_hardened,
    _routing_functions_are_hardened,
    readiness,
)

ROOT = Path(__file__).resolve().parents[1]


def _policy_rows(roles: object = ("public",)) -> list[dict[str, object]]:
    expression = (
        "(workspace_id = (NULLIF(current_setting("
        "'expenseops.workspace_id'::text, true), ''::text))::integer)"
    )
    return [
        {
            "tablename": table,
            "policyname": "expenseops_workspace_isolation",
            "permissive": "PERMISSIVE",
            "roles": roles,
            "cmd": "ALL",
            "qual": expression,
            "with_check": expression,
        }
        for table in ("tenant_a", "tenant_b")
    ]


@pytest.mark.parametrize(
    "roles",
    [
        ["public"],
        ("PUBLIC",),
        "public",
        "{public}",
        '{"public"}',
    ],
)
def test_readiness_accepts_supported_public_policy_role_encodings(roles):
    assert _policies_are_hardened(_policy_rows(roles), ["tenant_a", "tenant_b"])


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("policyname", "legacy_policy"),
        ("permissive", "RESTRICTIVE"),
        ("roles", ["public", "expenseops_runtime"]),
        ("cmd", "SELECT"),
        (
            "qual",
            "current_setting('expenseops.bypass_rls', true) = 'on' OR workspace_id = 1",
        ),
        ("with_check", "true"),
    ],
)
def test_readiness_rejects_any_policy_contract_drift(field, unsafe_value):
    rows = _policy_rows()
    rows[0][field] = unsafe_value

    assert not _policies_are_hardened(rows, ["tenant_a", "tenant_b"])


def test_readiness_requires_exactly_one_policy_for_every_tenant_table():
    rows = _policy_rows()

    assert not _policies_are_hardened(rows[:-1], ["tenant_a", "tenant_b"])
    assert not _policies_are_hardened(rows + [deepcopy(rows[0])], ["tenant_a", "tenant_b"])


def test_readiness_accepts_catalog_rendering_of_parent_derived_child_policy():
    table, expression = next(iter(_CHILD_WORKSPACE_POLICY_EXPRESSIONS.items()))
    catalog_expression = expression.replace("public.", "").replace(" AS ", " ")
    row = {
        "tablename": table,
        "policyname": "expenseops_workspace_isolation",
        "permissive": "PERMISSIVE",
        "roles": ["public"],
        "cmd": "ALL",
        "qual": catalog_expression,
        "with_check": catalog_expression,
    }

    assert _policies_are_hardened([row], {table: expression})


def test_readiness_policy_comparison_preserves_boolean_precedence():
    expected = "workspace_id = 1 AND (parent_id IS NULL OR workspace_id = 1)"
    precedence_drift = "(workspace_id = 1 AND parent_id IS NULL) OR workspace_id = 1"
    row = {
        "tablename": "child_table",
        "policyname": "expenseops_workspace_isolation",
        "permissive": "PERMISSIVE",
        "roles": ["public"],
        "cmd": "ALL",
        "qual": precedence_drift,
        "with_check": precedence_drift,
    }

    assert re.sub(r"[\s()]", "", expected.casefold()) == re.sub(
        r"[\s()]", "", precedence_drift.casefold()
    )
    assert _normalize_policy_expression(expected) != _normalize_policy_expression(precedence_drift)
    assert not _policies_are_hardened([row], {"child_table": expected})


def test_readiness_membership_queries_reject_both_graph_directions():
    source = getsource(readiness)

    assert "owner_membership.member = owner_role.oid OR" in source
    assert "owner_membership.roleid = owner_role.oid" in source
    assert "membership.member = (SELECT oid FROM pg_roles" in source
    assert "OR membership.roleid = " in source


def _routing_rows(proconfig: object = ("search_path=pg_catalog, pg_temp",)):
    return [
        {
            "signature": signature,
            "security_definer": True,
            "proconfig": proconfig,
            "prosrc": _ROUTING_FUNCTION_CONTRACTS[signature]["source"],
            "function_result": _ROUTING_FUNCTION_CONTRACTS[signature]["result"],
            "language_name": "sql",
            "volatility": "s",
            "is_strict": True,
            "runtime_execute": True,
            "public_execute": False,
            "runtime_owned": False,
            "owner_name": "expenseops_migrator",
            "owner_superuser": False,
            "owner_bypassrls": True,
            "owner_createdb": False,
            "owner_createrole": False,
            "owner_replication": False,
            "owner_login": True,
            "owner_inherit": False,
            "owner_has_memberships": False,
        }
        for signature in sorted(_REQUIRED_ROUTING_FUNCTIONS)
    ]


@pytest.mark.parametrize(
    "proconfig",
    [
        ["search_path=pg_catalog, pg_temp"],
        ("search_path=pg_catalog,pg_temp",),
        '{"search_path=pg_catalog, pg_temp"}',
    ],
)
def test_readiness_accepts_hardened_narrow_routing_functions(proconfig):
    assert _routing_functions_are_hardened(_routing_rows(proconfig))


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("security_definer", False),
        ("proconfig", ["search_path=public"]),
        ("prosrc", "SELECT 1"),
        ("function_result", "integer"),
        ("language_name", "plpgsql"),
        ("volatility", "v"),
        ("is_strict", False),
        ("runtime_execute", False),
        ("public_execute", True),
        ("runtime_owned", True),
        ("owner_name", "postgres"),
        ("owner_superuser", True),
        ("owner_bypassrls", False),
        ("owner_createdb", True),
        ("owner_createrole", True),
        ("owner_replication", True),
        ("owner_login", False),
        ("owner_inherit", True),
        ("owner_has_memberships", True),
    ],
)
def test_readiness_rejects_unsafe_routing_function_contract(field, unsafe_value):
    rows = _routing_rows()
    rows[0][field] = unsafe_value

    assert not _routing_functions_are_hardened(rows)


def test_readiness_requires_exact_routing_function_set():
    rows = _routing_rows()

    assert not _routing_functions_are_hardened(rows[:-1])
    rows[0]["signature"] = "public.expenseops_route_everything(text)"
    assert not _routing_functions_are_hardened(rows)


def test_readiness_routing_contracts_match_the_migration_bodies_exactly():
    migration = (
        ScriptDirectory.from_config(Config(ROOT / "alembic.ini"))
        .get_revision("20260815_0028")
        .module
    )
    migration_sources = {}
    for signature, definition in migration.ROUTING_FUNCTIONS:
        normalized_signature = re.sub(r",\s*", ", ", signature)
        source = definition.split("AS $function$", maxsplit=1)[1].rsplit("$function$", maxsplit=1)[
            0
        ]
        migration_sources[normalized_signature] = _normalize_sql_contract(source)

    assert set(migration_sources) == _REQUIRED_ROUTING_FUNCTIONS
    assert migration_sources == {
        signature: _normalize_sql_contract(contract["source"])
        for signature, contract in _ROUTING_FUNCTION_CONTRACTS.items()
    }


def test_release_gate_uses_separate_roles_and_real_postgres_rls_evidence():
    workflow = (ROOT / ".github/workflows/release-gate.yml").read_text(encoding="utf-8")

    assert "bootstrap_database_roles.py --apply" in workflow
    assert workflow.count("bootstrap_database_roles.py --reconcile-runtime-grants") == 2
    assert "alembic upgrade 20260813_0023" in workflow
    assert "CREATE DATABASE expenseops_fresh OWNER postgres" in workflow
    assert "MIGRATOR_FRESH_DATABASE_URL" in workflow
    assert "MIGRATOR_INCREMENTAL_DATABASE_URL" in workflow
    assert 'EXPENSEOPS_ADMIN_DATABASE_URL="$admin_url"' in workflow
    assert '"$ADMIN_FRESH_DATABASE_URL" "$ADMIN_INCREMENTAL_DATABASE_URL"' in workflow
    assert "id: tenant-security-phase" in workflow
    assert 'EXPECTED_HARDENED_RLS="${{ steps.tenant-security-phase.outputs.hardened }}"' in workflow
    assert "INSERT INTO household_item_aliases" in workflow
    assert workflow.count("pytest -q tests/test_postgres_release_security.py") == 2
    assert 'AGENT_WRITE_ACTIONS_ENABLED: "false"' in workflow
    assert 'AGENT_PROACTIVE_ENABLED: "false"' in workflow
    assert 'AGENT_PURCHASING_ENABLED: "false"' in workflow

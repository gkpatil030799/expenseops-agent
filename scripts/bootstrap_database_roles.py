#!/usr/bin/env python3
"""Provision the ExpenseOps PostgreSQL role split with an explicit apply guard.

The command is a dry run unless ``--apply`` is supplied.  Apply mode reads the
admin URL and all three generated role passwords from environment variables; secret
values are always sent as bind parameters and are never rendered in the plan or
success output.

Before the first role cutover, ``--bootstrap-backup-role`` can provision only
the least-privilege logical-backup login so a Hobby-plan encrypted recovery
artifact can be created and restore-tested.  It does not create the other roles
or transfer ownership.

Run ``--apply`` once before the first restricted-role migration to transfer the
existing application objects.  The private migration service then runs
``--reconcile-runtime-grants`` after every Alembic upgrade so newly created
routing functions receive their exact EXECUTE grants before runtime deploys.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

import psycopg

RUNTIME_ROLE = "expenseops_runtime"
MIGRATOR_ROLE = "expenseops_migrator"
BACKUP_ROLE = "expenseops_backup"

ADMIN_URL_ENV = "EXPENSEOPS_ADMIN_DATABASE_URL"
RUNTIME_PASSWORD_ENV = "EXPENSEOPS_RUNTIME_PASSWORD"
MIGRATOR_PASSWORD_ENV = "EXPENSEOPS_MIGRATOR_PASSWORD"
BACKUP_PASSWORD_ENV = "EXPENSEOPS_BACKUP_PASSWORD"
MIGRATION_URL_ENV = "DATABASE_URL"
MINIMUM_PASSWORD_LENGTH = 24
RUNTIME_PASSWORD_SETTING = "expenseops.bootstrap_runtime_password"
MIGRATOR_PASSWORD_SETTING = "expenseops.bootstrap_migrator_password"
BACKUP_PASSWORD_SETTING = "expenseops.bootstrap_backup_password"

# This allowlist is intentionally the complete SQLAlchemy/Alembic application
# surface rather than every object in ``public``.  An unexpected object is not
# silently reassigned to the migration role.
APPLICATION_TABLES = (
    "agent_action_proposals",
    "agent_conversations",
    "agent_messages",
    "agent_runs",
    "agent_tool_calls",
    "ai_interpretation_memories",
    "alembic_version",
    "audit_events",
    "auth_identities",
    "auth_sessions",
    "classification_concept_aliases",
    "classification_concepts",
    "classification_decisions",
    "classification_settings",
    "classification_subcategories",
    "data_consents",
    "errand_household_items",
    "errand_plan_stop_errands",
    "errand_plan_stop_household_items",
    "errand_plan_stops",
    "errand_plans",
    "errands",
    "expense_transactions",
    "financial_operations",
    "gmail_accounts",
    "gmail_sync_checkpoints",
    "household_item_acquisitions",
    "household_item_aliases",
    "household_items",
    "oauth_states",
    "outbox_events",
    "plaid_items",
    "plaid_webhook_events",
    "preferred_places",
    "promotion_digest_runs",
    "promotion_feedback",
    "promotion_messages",
    "promotion_offers",
    "promotion_settings",
    "proactive_attention_deliveries",
    "proactive_attention_preferences",
    "purchase_receipt_items",
    "purchase_receipts",
    "rate_limit_events",
    "review_items",
    "replenishment_feedback",
    "replenishment_job_runs",
    "replenishment_model_versions",
    "replenishment_predictions",
    "saved_locations",
    "scheduled_job_leases",
    "splitwise_integrations",
    "telegram_identities",
    "telegram_link_codes",
    "telegram_sessions",
    "telegram_webhook_updates",
    "users",
    "workspace_invitations",
    "workspace_memberships",
    "workspaces",
)

# The classification ledger is append-only for the application role. Corrections
# are represented by a new version linked through ``corrects_decision_id``; only
# the migrator/owner may alter history for lifecycle or recovery operations.
RUNTIME_APPEND_ONLY_TABLES = ("classification_decisions",)

ROUTING_FUNCTIONS = (
    "public.expenseops_route_plaid_item(text)",
    "public.expenseops_route_telegram_identity(text,text)",
    "public.expenseops_route_active_telegram_identity_by_link_code(text)",
    "public.expenseops_route_telegram_link_code(text)",
    "public.expenseops_route_workspace_invitation(text)",
)

APPLICATION_FUNCTIONS = (
    "public.expenseops_agent_proposal_snapshot_immutable()",
    "public.expenseops_validate_acquisition_provenance_workspace()",
    "public.expenseops_validate_classification_decision_source_workspace()",
    "public.expenseops_validate_receipt_item_classification_workspace()",
    "public.expenseops_validate_receipt_transaction_workspace()",
    "public.expenseops_validate_replenishment_feedback_workspace()",
    *ROUTING_FUNCTIONS,
)


def _text_array(values: Sequence[str]) -> str:
    if not values:
        return "ARRAY[]::text[]"
    # All callers pass immutable repository constants.  Doubling quotes keeps
    # this helper correct if a future allowlisted identifier contains one.
    literals = ", ".join("'" + value.replace("'", "''") + "'" for value in values)
    return f"ARRAY[{literals}]::text[]"


TABLE_ARRAY_SQL = _text_array(APPLICATION_TABLES)
RUNTIME_APPEND_ONLY_TABLE_ARRAY_SQL = _text_array(RUNTIME_APPEND_ONLY_TABLES)
FUNCTION_ARRAY_SQL = _text_array(APPLICATION_FUNCTIONS)
ROUTING_FUNCTION_ARRAY_SQL = _text_array(ROUTING_FUNCTIONS)


ROLE_STATEMENTS = (
    f"""
    DO $expenseops_roles$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{RUNTIME_ROLE}'
        ) THEN
            CREATE ROLE {RUNTIME_ROLE};
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{MIGRATOR_ROLE}'
        ) THEN
            CREATE ROLE {MIGRATOR_ROLE};
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{BACKUP_ROLE}'
        ) THEN
            CREATE ROLE {BACKUP_ROLE};
        END IF;
    END
    $expenseops_roles$
    """,
    f"ALTER ROLE {RUNTIME_ROLE} RESET ALL",
    f"ALTER ROLE {MIGRATOR_ROLE} RESET ALL",
    f"ALTER ROLE {BACKUP_ROLE} RESET ALL",
    (
        f"ALTER ROLE {RUNTIME_ROLE} WITH LOGIN NOSUPERUSER NOBYPASSRLS "
        "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION "
        "CONNECTION LIMIT -1 VALID UNTIL 'infinity'"
    ),
    (
        f"ALTER ROLE {MIGRATOR_ROLE} WITH LOGIN NOSUPERUSER BYPASSRLS "
        "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION "
        "CONNECTION LIMIT -1 VALID UNTIL 'infinity'"
    ),
    (
        f"ALTER ROLE {BACKUP_ROLE} WITH LOGIN NOSUPERUSER BYPASSRLS "
        "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION "
        "CONNECTION LIMIT -1 VALID UNTIL 'infinity'"
    ),
    f"ALTER ROLE {BACKUP_ROLE} SET default_transaction_read_only = on",
    f"""
    DO $expenseops_memberships$
    DECLARE
        membership record;
    BEGIN
        FOR membership IN
            SELECT granted.rolname AS granted_role, member.rolname AS member_role
            FROM pg_catalog.pg_auth_members AS auth_members
            JOIN pg_catalog.pg_roles AS granted ON granted.oid = auth_members.roleid
            JOIN pg_catalog.pg_roles AS member ON member.oid = auth_members.member
            WHERE member.rolname IN (
                '{RUNTIME_ROLE}', '{MIGRATOR_ROLE}', '{BACKUP_ROLE}'
            )
               OR granted.rolname IN (
                   '{RUNTIME_ROLE}', '{MIGRATOR_ROLE}', '{BACKUP_ROLE}'
               )
        LOOP
            EXECUTE format(
                'REVOKE %I FROM %I',
                membership.granted_role,
                membership.member_role
            );
        END LOOP;
    END
    $expenseops_memberships$
    """,
)

BACKUP_ROLE_STATEMENTS = (
    f"""
    DO $expenseops_backup_role$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{BACKUP_ROLE}'
        ) THEN
            CREATE ROLE {BACKUP_ROLE};
        END IF;
    END
    $expenseops_backup_role$
    """,
    f"ALTER ROLE {BACKUP_ROLE} RESET ALL",
    (
        f"ALTER ROLE {BACKUP_ROLE} WITH LOGIN NOSUPERUSER BYPASSRLS "
        "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION "
        "CONNECTION LIMIT -1 VALID UNTIL 'infinity'"
    ),
    f"ALTER ROLE {BACKUP_ROLE} SET default_transaction_read_only = on",
    f"""
    DO $expenseops_backup_memberships$
    DECLARE
        membership record;
    BEGIN
        FOR membership IN
            SELECT granted.rolname AS granted_role, member.rolname AS member_role
            FROM pg_catalog.pg_auth_members AS auth_members
            JOIN pg_catalog.pg_roles AS granted ON granted.oid = auth_members.roleid
            JOIN pg_catalog.pg_roles AS member ON member.oid = auth_members.member
            WHERE member.rolname = '{BACKUP_ROLE}'
               OR granted.rolname = '{BACKUP_ROLE}'
        LOOP
            EXECUTE format(
                'REVOKE %I FROM %I',
                membership.granted_role,
                membership.member_role
            );
        END LOOP;
    END
    $expenseops_backup_memberships$
    """,
)

SESSION_STATEMENTS = (
    "SET LOCAL search_path = pg_catalog, pg_temp",
    "SET LOCAL lock_timeout = '5s'",
    "SET LOCAL statement_timeout = '120s'",
    "SET LOCAL password_encryption = 'scram-sha-256'",
)

PASSWORD_SQL = f"""
DO $expenseops_passwords$
DECLARE
    runtime_password text := pg_catalog.current_setting('{RUNTIME_PASSWORD_SETTING}', true);
    migrator_password text := pg_catalog.current_setting('{MIGRATOR_PASSWORD_SETTING}', true);
    backup_password text := pg_catalog.current_setting('{BACKUP_PASSWORD_SETTING}', true);
BEGIN
    IF coalesce(runtime_password, '') = ''
       OR coalesce(migrator_password, '') = ''
       OR coalesce(backup_password, '') = '' THEN
        RAISE EXCEPTION 'ExpenseOps role passwords were not bound to this transaction';
    END IF;
    EXECUTE format('ALTER ROLE {RUNTIME_ROLE} PASSWORD %L', runtime_password);
    EXECUTE format('ALTER ROLE {MIGRATOR_ROLE} PASSWORD %L', migrator_password);
    EXECUTE format('ALTER ROLE {BACKUP_ROLE} PASSWORD %L', backup_password);
    PERFORM pg_catalog.set_config('{RUNTIME_PASSWORD_SETTING}', '', true);
    PERFORM pg_catalog.set_config('{MIGRATOR_PASSWORD_SETTING}', '', true);
    PERFORM pg_catalog.set_config('{BACKUP_PASSWORD_SETTING}', '', true);
END
$expenseops_passwords$
"""

BACKUP_PASSWORD_SQL = f"""
DO $expenseops_backup_password$
DECLARE
    backup_password text := pg_catalog.current_setting('{BACKUP_PASSWORD_SETTING}', true);
BEGIN
    IF coalesce(backup_password, '') = '' THEN
        RAISE EXCEPTION 'ExpenseOps backup password was not bound to this transaction';
    END IF;
    EXECUTE format('ALTER ROLE {BACKUP_ROLE} PASSWORD %L', backup_password);
    PERFORM pg_catalog.set_config('{BACKUP_PASSWORD_SETTING}', '', true);
END
$expenseops_backup_password$
"""

DATABASE_AND_SCHEMA_SQL = f"""
DO $expenseops_database$
BEGIN
    EXECUTE format(
        'REVOKE CONNECT, CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC',
        current_database()
    );
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON DATABASE %I FROM {RUNTIME_ROLE}',
        current_database()
    );
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON DATABASE %I FROM {MIGRATOR_ROLE}',
        current_database()
    );
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON DATABASE %I FROM {BACKUP_ROLE}',
        current_database()
    );
    EXECUTE format(
        'GRANT CONNECT ON DATABASE %I TO '
        '{RUNTIME_ROLE}, {MIGRATOR_ROLE}, {BACKUP_ROLE}',
        current_database()
    );
    EXECUTE format(
        'ALTER ROLE {RUNTIME_ROLE} IN DATABASE %I RESET ALL',
        current_database()
    );
    EXECUTE format(
        'ALTER ROLE {MIGRATOR_ROLE} IN DATABASE %I RESET ALL',
        current_database()
    );
    EXECUTE format(
        'ALTER ROLE {BACKUP_ROLE} IN DATABASE %I RESET ALL',
        current_database()
    );
    EXECUTE format(
        'ALTER ROLE {RUNTIME_ROLE} IN DATABASE %I '
        'SET search_path = public, pg_catalog, pg_temp',
        current_database()
    );
    EXECUTE format(
        'ALTER ROLE {MIGRATOR_ROLE} IN DATABASE %I '
        'SET search_path = public, pg_catalog, pg_temp',
        current_database()
    );
    EXECUTE format(
        'ALTER ROLE {BACKUP_ROLE} IN DATABASE %I '
        'SET search_path = public, pg_catalog, pg_temp',
        current_database()
    );
END
$expenseops_database$;

ALTER SCHEMA public OWNER TO {MIGRATOR_ROLE};
REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON SCHEMA public FROM {RUNTIME_ROLE};
REVOKE ALL PRIVILEGES ON SCHEMA public FROM {BACKUP_ROLE};
GRANT USAGE ON SCHEMA public TO {RUNTIME_ROLE};
GRANT USAGE ON SCHEMA public TO {BACKUP_ROLE};
GRANT USAGE, CREATE ON SCHEMA public TO {MIGRATOR_ROLE};
"""

BACKUP_DATABASE_AND_SCHEMA_SQL = f"""
DO $expenseops_backup_database$
BEGIN
    -- These PUBLIC revocations are required because a direct role-specific
    -- REVOKE cannot override privileges inherited through PUBLIC.
    EXECUTE format(
        'REVOKE CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC',
        current_database()
    );
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON DATABASE %I FROM {BACKUP_ROLE}',
        current_database()
    );
    EXECUTE format(
        'GRANT CONNECT ON DATABASE %I TO {BACKUP_ROLE}',
        current_database()
    );
    EXECUTE format(
        'ALTER ROLE {BACKUP_ROLE} IN DATABASE %I RESET ALL',
        current_database()
    );
    EXECUTE format(
        'ALTER ROLE {BACKUP_ROLE} IN DATABASE %I '
        'SET search_path = public, pg_catalog, pg_temp',
        current_database()
    );
END
$expenseops_backup_database$;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON SCHEMA public FROM {BACKUP_ROLE};
GRANT USAGE ON SCHEMA public TO {BACKUP_ROLE};
"""

BACKUP_GRANTS_SQL = f"""
DO $expenseops_backup_grants$
DECLARE
    object_name text;
    relation_oid regclass;
    owned_sequence record;
    grantable_type record;
BEGIN
    FOREACH object_name IN ARRAY {TABLE_ARRAY_SQL}
    LOOP
        relation_oid := pg_catalog.to_regclass(format('%I.%I', 'public', object_name));
        IF relation_oid IS NOT NULL THEN
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE %s FROM {BACKUP_ROLE}',
                relation_oid
            );
            EXECUTE format(
                'GRANT SELECT ON TABLE %s TO {BACKUP_ROLE}',
                relation_oid
            );
        END IF;
    END LOOP;

    FOR owned_sequence IN
        SELECT DISTINCT sequence_class.oid::regclass AS sequence_oid
        FROM pg_catalog.pg_class AS sequence_class
        JOIN pg_catalog.pg_namespace AS sequence_namespace
          ON sequence_namespace.oid = sequence_class.relnamespace
        JOIN pg_catalog.pg_depend AS dependency
          ON dependency.classid = 'pg_class'::regclass
         AND dependency.objid = sequence_class.oid
         AND dependency.deptype IN ('a', 'i')
        JOIN pg_catalog.pg_class AS table_class
          ON table_class.oid = dependency.refobjid
        JOIN pg_catalog.pg_namespace AS table_namespace
          ON table_namespace.oid = table_class.relnamespace
        WHERE sequence_class.relkind = 'S'
          AND sequence_namespace.nspname = 'public'
          AND table_namespace.nspname = 'public'
          AND table_class.relname = ANY ({TABLE_ARRAY_SQL})
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM {BACKUP_ROLE}',
            owned_sequence.sequence_oid
        );
        EXECUTE format(
            'GRANT SELECT ON SEQUENCE %s TO {BACKUP_ROLE}',
            owned_sequence.sequence_oid
        );
    END LOOP;

    -- Application functions are unnecessary for pg_dump and BYPASSRLS makes
    -- any callable SECURITY DEFINER surface an avoidable privilege.
    REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
    REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM {BACKUP_ROLE};

    -- A role-specific revoke cannot override the default PUBLIC USAGE grant
    -- on types.  Remove that inherited surface before verifying that the
    -- backup login has no type privileges.  PostgreSQL has no bulk
    -- "ALL TYPES IN SCHEMA" form, so use the same independently grantable
    -- type filter as the full role reconciliation.
    FOR grantable_type IN
        SELECT type_namespace.nspname AS schema_name,
               type_object.typname AS type_name
        FROM pg_catalog.pg_type AS type_object
        JOIN pg_catalog.pg_namespace AS type_namespace
          ON type_namespace.oid = type_object.typnamespace
        WHERE type_namespace.nspname = 'public'
          AND NOT (
              type_object.typelem <> 0
              AND type_object.typsubscript =
                  'pg_catalog.array_subscript_handler'::pg_catalog.regproc
          )
          AND type_object.typtype <> 'm'
        ORDER BY type_object.oid
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TYPE %I.%I FROM PUBLIC',
            grantable_type.schema_name,
            grantable_type.type_name
        );
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TYPE %I.%I FROM {BACKUP_ROLE}',
            grantable_type.schema_name,
            grantable_type.type_name
        );
    END LOOP;
END
$expenseops_backup_grants$
"""

OWNERSHIP_SQL = f"""
DO $expenseops_ownership$
DECLARE
    object_name text;
    owned_sequence record;
    function_signature text;
    function_oid regprocedure;
BEGIN
    FOREACH object_name IN ARRAY {TABLE_ARRAY_SQL}
    LOOP
        IF pg_catalog.to_regclass(format('%I.%I', 'public', object_name)) IS NOT NULL THEN
            EXECUTE format(
                'ALTER TABLE %I.%I OWNER TO {MIGRATOR_ROLE}',
                'public',
                object_name
            );
        END IF;
    END LOOP;

    FOR owned_sequence IN
        SELECT DISTINCT sequence_namespace.nspname AS schema_name,
                        sequence_class.relname AS sequence_name
        FROM pg_catalog.pg_class AS sequence_class
        JOIN pg_catalog.pg_namespace AS sequence_namespace
          ON sequence_namespace.oid = sequence_class.relnamespace
        JOIN pg_catalog.pg_depend AS dependency
          ON dependency.classid = 'pg_class'::regclass
         AND dependency.objid = sequence_class.oid
         AND dependency.deptype IN ('a', 'i')
        JOIN pg_catalog.pg_class AS table_class
          ON table_class.oid = dependency.refobjid
        JOIN pg_catalog.pg_namespace AS table_namespace
          ON table_namespace.oid = table_class.relnamespace
        WHERE sequence_class.relkind = 'S'
          AND sequence_namespace.nspname = 'public'
          AND table_namespace.nspname = 'public'
          AND table_class.relname = ANY ({TABLE_ARRAY_SQL})
    LOOP
        EXECUTE format(
            'ALTER SEQUENCE %I.%I OWNER TO {MIGRATOR_ROLE}',
            owned_sequence.schema_name,
            owned_sequence.sequence_name
        );
    END LOOP;

    FOREACH function_signature IN ARRAY {FUNCTION_ARRAY_SQL}
    LOOP
        function_oid := pg_catalog.to_regprocedure(function_signature);
        IF function_oid IS NOT NULL THEN
            EXECUTE format(
                'ALTER FUNCTION %s OWNER TO {MIGRATOR_ROLE}',
                function_oid
            );
        END IF;
    END LOOP;
END
$expenseops_ownership$
"""

RUNTIME_GRANTS_SQL = f"""
REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON SCHEMA public FROM {RUNTIME_ROLE};
REVOKE ALL PRIVILEGES ON SCHEMA public FROM {BACKUP_ROLE};
GRANT USAGE ON SCHEMA public TO {RUNTIME_ROLE};
GRANT USAGE ON SCHEMA public TO {BACKUP_ROLE};
GRANT USAGE, CREATE ON SCHEMA public TO {MIGRATOR_ROLE};

DO $expenseops_table_grants$
DECLARE
    object_name text;
    relation_oid regclass;
    owned_sequence record;
    grantable_type record;
    function_signature text;
    function_oid regprocedure;
BEGIN
    FOREACH object_name IN ARRAY {TABLE_ARRAY_SQL}
    LOOP
        relation_oid := pg_catalog.to_regclass(format('%I.%I', 'public', object_name));
        IF relation_oid IS NOT NULL THEN
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE %s FROM PUBLIC',
                relation_oid
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE %s FROM {RUNTIME_ROLE}',
                relation_oid
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE %s FROM {BACKUP_ROLE}',
                relation_oid
            );
            EXECUTE format(
                'GRANT SELECT ON TABLE %s TO {BACKUP_ROLE}',
                relation_oid
            );
            IF object_name = 'alembic_version' THEN
                EXECUTE format(
                    'GRANT SELECT ON TABLE %s TO {RUNTIME_ROLE}',
                    relation_oid
                );
            ELSIF object_name = ANY ({RUNTIME_APPEND_ONLY_TABLE_ARRAY_SQL}) THEN
                EXECUTE format(
                    'GRANT SELECT, INSERT ON TABLE %s TO {RUNTIME_ROLE}',
                    relation_oid
                );
            ELSE
                EXECUTE format(
                    'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE %s TO {RUNTIME_ROLE}',
                    relation_oid
                );
            END IF;
        END IF;
    END LOOP;

    FOR owned_sequence IN
        SELECT DISTINCT sequence_class.oid::regclass AS sequence_oid
        FROM pg_catalog.pg_class AS sequence_class
        JOIN pg_catalog.pg_namespace AS sequence_namespace
          ON sequence_namespace.oid = sequence_class.relnamespace
        JOIN pg_catalog.pg_depend AS dependency
          ON dependency.classid = 'pg_class'::regclass
         AND dependency.objid = sequence_class.oid
         AND dependency.deptype IN ('a', 'i')
        JOIN pg_catalog.pg_class AS table_class
          ON table_class.oid = dependency.refobjid
        JOIN pg_catalog.pg_namespace AS table_namespace
          ON table_namespace.oid = table_class.relnamespace
        WHERE sequence_class.relkind = 'S'
          AND sequence_namespace.nspname = 'public'
          AND table_namespace.nspname = 'public'
          AND table_class.relname = ANY ({TABLE_ARRAY_SQL})
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM PUBLIC',
            owned_sequence.sequence_oid
        );
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM {RUNTIME_ROLE}',
            owned_sequence.sequence_oid
        );
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM {BACKUP_ROLE}',
            owned_sequence.sequence_oid
        );
        EXECUTE format(
            'GRANT SELECT ON SEQUENCE %s TO {BACKUP_ROLE}',
            owned_sequence.sequence_oid
        );
        EXECUTE format(
            'GRANT USAGE, SELECT ON SEQUENCE %s TO {RUNTIME_ROLE}',
            owned_sequence.sequence_oid
        );
    END LOOP;

    -- Revoke PUBLIC first because role-specific REVOKE cannot override a grant
    -- inherited through PUBLIC.  Runtime receives only the five exact routes.
    REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
    REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM {RUNTIME_ROLE};
    REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM {BACKUP_ROLE};
    FOREACH function_signature IN ARRAY {ROUTING_FUNCTION_ARRAY_SQL}
    LOOP
        function_oid := pg_catalog.to_regprocedure(function_signature);
        IF function_oid IS NOT NULL THEN
            EXECUTE format(
                'GRANT EXECUTE ON FUNCTION %s TO {RUNTIME_ROLE}',
                function_oid
            );
        END IF;
    END LOOP;

    -- PostgreSQL has no "ALL TYPES IN SCHEMA" REVOKE form.  Reconcile each
    -- independently grantable type while excluding generated array and
    -- multirange types, whose privileges are governed by their base types and
    -- which PostgreSQL rejects as direct GRANT/REVOKE targets.
    FOR grantable_type IN
        SELECT type_namespace.nspname AS schema_name,
               type_object.typname AS type_name
        FROM pg_catalog.pg_type AS type_object
        JOIN pg_catalog.pg_namespace AS type_namespace
          ON type_namespace.oid = type_object.typnamespace
        WHERE type_namespace.nspname = 'public'
          AND NOT (
              type_object.typelem <> 0
              AND type_object.typsubscript =
                  'pg_catalog.array_subscript_handler'::pg_catalog.regproc
          )
          AND type_object.typtype <> 'm'
        ORDER BY type_object.oid
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TYPE %I.%I FROM PUBLIC',
            grantable_type.schema_name,
            grantable_type.type_name
        );
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TYPE %I.%I FROM {RUNTIME_ROLE}',
            grantable_type.schema_name,
            grantable_type.type_name
        );
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TYPE %I.%I FROM {BACKUP_ROLE}',
            grantable_type.schema_name,
            grantable_type.type_name
        );
    END LOOP;
END
$expenseops_table_grants$;
"""

DEFAULT_PRIVILEGES_SQL = f"""
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE}
    REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE} IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE}
    REVOKE ALL PRIVILEGES ON TABLES FROM {RUNTIME_ROLE};
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE} IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM {RUNTIME_ROLE};
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE}
    REVOKE ALL PRIVILEGES ON TABLES FROM {BACKUP_ROLE};
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE} IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM {BACKUP_ROLE};
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE}
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE} IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE}
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM {RUNTIME_ROLE};
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE} IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM {RUNTIME_ROLE};
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE}
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM {BACKUP_ROLE};
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE} IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM {BACKUP_ROLE};
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE}
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE} IN SCHEMA public
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE}
    REVOKE EXECUTE ON FUNCTIONS FROM {RUNTIME_ROLE};
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE} IN SCHEMA public
    REVOKE EXECUTE ON FUNCTIONS FROM {RUNTIME_ROLE};
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE}
    REVOKE EXECUTE ON FUNCTIONS FROM {BACKUP_ROLE};
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE} IN SCHEMA public
    REVOKE EXECUTE ON FUNCTIONS FROM {BACKUP_ROLE};
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE}
    REVOKE ALL PRIVILEGES ON TYPES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE} IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TYPES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE}
    REVOKE ALL PRIVILEGES ON TYPES FROM {RUNTIME_ROLE};
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE} IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TYPES FROM {RUNTIME_ROLE};
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE}
    REVOKE ALL PRIVILEGES ON TYPES FROM {BACKUP_ROLE};
ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR_ROLE} IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TYPES FROM {BACKUP_ROLE};
"""

RECONCILE_REQUIRED_FUNCTIONS_SQL = f"""
DO $expenseops_required_routes$
DECLARE
    missing_signature text;
BEGIN
    SELECT route.signature
    INTO missing_signature
    FROM pg_catalog.unnest({ROUTING_FUNCTION_ARRAY_SQL}) AS route(signature)
    WHERE pg_catalog.to_regprocedure(route.signature) IS NULL
    ORDER BY route.signature
    LIMIT 1;
    IF missing_signature IS NOT NULL THEN
        RAISE EXCEPTION 'required tenant-routing function is missing: %', missing_signature;
    END IF;
END
$expenseops_required_routes$
"""

BACKUP_VERIFY_SQL = f"""
DO $expenseops_backup_verify$
DECLARE
    object_name text;
    relation_oid regclass;
    sequence_record record;
    function_record record;
    backup_record record;
BEGIN
    SELECT * INTO backup_record
    FROM pg_catalog.pg_roles
    WHERE rolname = '{BACKUP_ROLE}';
    IF backup_record IS NULL
       OR NOT backup_record.rolcanlogin
       OR backup_record.rolsuper
       OR NOT backup_record.rolbypassrls
       OR backup_record.rolcreatedb
       OR backup_record.rolcreaterole
       OR backup_record.rolreplication
       OR backup_record.rolinherit
       OR coalesce(backup_record.rolconfig, ARRAY[]::text[])
          <> ARRAY['default_transaction_read_only=on']::text[] THEN
        RAISE EXCEPTION 'expenseops_backup role attributes are unsafe';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_auth_members AS auth_members
        JOIN pg_catalog.pg_roles AS member ON member.oid = auth_members.member
        JOIN pg_catalog.pg_roles AS granted ON granted.oid = auth_members.roleid
        WHERE member.rolname = '{BACKUP_ROLE}'
           OR granted.rolname = '{BACKUP_ROLE}'
    ) THEN
        RAISE EXCEPTION 'expenseops_backup must not have role memberships';
    END IF;

    IF NOT pg_catalog.has_database_privilege(
        '{BACKUP_ROLE}', current_database(), 'CONNECT'
    ) OR pg_catalog.has_database_privilege(
        '{BACKUP_ROLE}', current_database(), 'CREATE'
    ) OR pg_catalog.has_database_privilege(
        '{BACKUP_ROLE}', current_database(), 'TEMPORARY'
    ) OR pg_catalog.has_schema_privilege(
        '{BACKUP_ROLE}', 'public', 'CREATE'
    ) OR NOT pg_catalog.has_schema_privilege(
        '{BACKUP_ROLE}', 'public', 'USAGE'
    ) THEN
        RAISE EXCEPTION 'expenseops_backup database/schema privileges are unsafe';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace AS namespace_object
        WHERE namespace_object.nspname <> 'public'
          AND namespace_object.nspname <> 'information_schema'
          AND pg_catalog.left(namespace_object.nspname, 3) <> 'pg_'
          AND (
              pg_catalog.has_schema_privilege(
                  '{BACKUP_ROLE}', namespace_object.oid, 'USAGE'
              ) OR pg_catalog.has_schema_privilege(
                  '{BACKUP_ROLE}', namespace_object.oid, 'CREATE'
              )
          )
    ) THEN
        RAISE EXCEPTION 'expenseops_backup has privileges on an unexpected schema';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_database AS database_object
        JOIN pg_catalog.pg_roles AS database_owner
          ON database_owner.oid = database_object.datdba
        WHERE database_owner.rolname = '{BACKUP_ROLE}'
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS object_class
        JOIN pg_catalog.pg_roles AS object_owner
          ON object_owner.oid = object_class.relowner
        WHERE object_owner.rolname = '{BACKUP_ROLE}'
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc AS function_object
        JOIN pg_catalog.pg_roles AS object_owner
          ON object_owner.oid = function_object.proowner
        WHERE object_owner.rolname = '{BACKUP_ROLE}'
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_type AS type_object
        JOIN pg_catalog.pg_roles AS object_owner
          ON object_owner.oid = type_object.typowner
        WHERE object_owner.rolname = '{BACKUP_ROLE}'
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace AS namespace_object
        JOIN pg_catalog.pg_roles AS object_owner
          ON object_owner.oid = namespace_object.nspowner
        WHERE object_owner.rolname = '{BACKUP_ROLE}'
    ) THEN
        RAISE EXCEPTION 'expenseops_backup must not own database objects';
    END IF;

    FOREACH object_name IN ARRAY {TABLE_ARRAY_SQL}
    LOOP
        relation_oid := pg_catalog.to_regclass(format('%I.%I', 'public', object_name));
        IF relation_oid IS NULL THEN
            CONTINUE;
        END IF;
        IF NOT pg_catalog.has_table_privilege('{BACKUP_ROLE}', relation_oid, 'SELECT')
           OR pg_catalog.has_table_privilege('{BACKUP_ROLE}', relation_oid, 'INSERT')
           OR pg_catalog.has_table_privilege('{BACKUP_ROLE}', relation_oid, 'UPDATE')
           OR pg_catalog.has_table_privilege('{BACKUP_ROLE}', relation_oid, 'DELETE')
           OR pg_catalog.has_table_privilege('{BACKUP_ROLE}', relation_oid, 'TRUNCATE')
           OR pg_catalog.has_table_privilege('{BACKUP_ROLE}', relation_oid, 'REFERENCES')
           OR pg_catalog.has_table_privilege('{BACKUP_ROLE}', relation_oid, 'TRIGGER')
           OR pg_catalog.has_any_column_privilege(
               '{BACKUP_ROLE}', relation_oid, 'INSERT,UPDATE,REFERENCES'
           ) THEN
            RAISE EXCEPTION 'backup table privileges are unsafe for public.%', object_name;
        END IF;
    END LOOP;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS relation
        JOIN pg_catalog.pg_namespace AS relation_namespace
          ON relation_namespace.oid = relation.relnamespace
        WHERE relation_namespace.nspname <> 'information_schema'
          AND pg_catalog.left(relation_namespace.nspname, 3) <> 'pg_'
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND (
              relation_namespace.nspname <> 'public'
              OR relation.relname <> ALL ({TABLE_ARRAY_SQL})
          )
          AND (
              pg_catalog.has_table_privilege(
                  '{BACKUP_ROLE}', relation.oid, 'SELECT'
              ) OR pg_catalog.has_table_privilege(
                  '{BACKUP_ROLE}', relation.oid, 'INSERT'
              ) OR pg_catalog.has_table_privilege(
                  '{BACKUP_ROLE}', relation.oid, 'UPDATE'
              ) OR pg_catalog.has_table_privilege(
                  '{BACKUP_ROLE}', relation.oid, 'DELETE'
              ) OR pg_catalog.has_table_privilege(
                  '{BACKUP_ROLE}', relation.oid, 'TRUNCATE'
              ) OR pg_catalog.has_table_privilege(
                  '{BACKUP_ROLE}', relation.oid, 'REFERENCES'
              ) OR pg_catalog.has_table_privilege(
                  '{BACKUP_ROLE}', relation.oid, 'TRIGGER'
              ) OR pg_catalog.has_any_column_privilege(
                  '{BACKUP_ROLE}', relation.oid, 'SELECT,INSERT,UPDATE,REFERENCES'
              )
          )
    ) THEN
        RAISE EXCEPTION 'backup has privileges on an unexpected relation';
    END IF;

    FOR sequence_record IN
        SELECT DISTINCT sequence_class.oid
        FROM pg_catalog.pg_class AS sequence_class
        JOIN pg_catalog.pg_namespace AS sequence_namespace
          ON sequence_namespace.oid = sequence_class.relnamespace
        JOIN pg_catalog.pg_depend AS dependency
          ON dependency.classid = 'pg_class'::regclass
         AND dependency.objid = sequence_class.oid
         AND dependency.deptype IN ('a', 'i')
        JOIN pg_catalog.pg_class AS table_class
          ON table_class.oid = dependency.refobjid
        JOIN pg_catalog.pg_namespace AS table_namespace
          ON table_namespace.oid = table_class.relnamespace
        WHERE sequence_class.relkind = 'S'
          AND sequence_namespace.nspname = 'public'
          AND table_namespace.nspname = 'public'
          AND table_class.relname = ANY ({TABLE_ARRAY_SQL})
    LOOP
        IF NOT pg_catalog.has_sequence_privilege(
            '{BACKUP_ROLE}', sequence_record.oid, 'SELECT'
        ) OR pg_catalog.has_sequence_privilege(
            '{BACKUP_ROLE}', sequence_record.oid, 'USAGE'
        ) OR pg_catalog.has_sequence_privilege(
            '{BACKUP_ROLE}', sequence_record.oid, 'UPDATE'
        ) THEN
            RAISE EXCEPTION 'backup sequence privileges are unsafe for %',
                sequence_record.oid::regclass;
        END IF;
    END LOOP;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS sequence_object
        JOIN pg_catalog.pg_namespace AS sequence_namespace
          ON sequence_namespace.oid = sequence_object.relnamespace
        WHERE sequence_namespace.nspname <> 'information_schema'
          AND pg_catalog.left(sequence_namespace.nspname, 3) <> 'pg_'
          AND sequence_object.relkind = 'S'
          AND NOT EXISTS (
              SELECT 1
              FROM pg_catalog.pg_depend AS dependency
              JOIN pg_catalog.pg_class AS table_class
                ON table_class.oid = dependency.refobjid
              JOIN pg_catalog.pg_namespace AS table_namespace
                ON table_namespace.oid = table_class.relnamespace
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = sequence_object.oid
                AND dependency.deptype IN ('a', 'i')
                AND sequence_namespace.nspname = 'public'
                AND table_namespace.nspname = 'public'
                AND table_class.relname = ANY ({TABLE_ARRAY_SQL})
          )
          AND (
              pg_catalog.has_sequence_privilege(
                  '{BACKUP_ROLE}', sequence_object.oid, 'USAGE'
              ) OR pg_catalog.has_sequence_privilege(
                  '{BACKUP_ROLE}', sequence_object.oid, 'SELECT'
              ) OR pg_catalog.has_sequence_privilege(
                  '{BACKUP_ROLE}', sequence_object.oid, 'UPDATE'
              )
          )
    ) THEN
        RAISE EXCEPTION 'backup has privileges on an unexpected sequence';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_type AS type_object
        JOIN pg_catalog.pg_namespace AS type_namespace
          ON type_namespace.oid = type_object.typnamespace
        WHERE type_namespace.nspname = 'public'
          AND NOT (
              type_object.typelem <> 0
              AND type_object.typsubscript =
                  'pg_catalog.array_subscript_handler'::pg_catalog.regproc
          )
          AND type_object.typtype <> 'm'
          AND pg_catalog.has_type_privilege(
              '{BACKUP_ROLE}', type_object.oid, 'USAGE'
          )
    ) THEN
        RAISE EXCEPTION 'backup has privileges on a public type';
    END IF;

    FOR function_record IN
        SELECT function_object.oid,
               function_object.oid::regprocedure AS signature
        FROM pg_catalog.pg_proc AS function_object
        JOIN pg_catalog.pg_namespace AS function_namespace
          ON function_namespace.oid = function_object.pronamespace
        WHERE function_namespace.nspname = 'public'
    LOOP
        IF pg_catalog.has_function_privilege(
            '{BACKUP_ROLE}', function_record.oid, 'EXECUTE'
        ) THEN
            RAISE EXCEPTION 'backup function privilege is unsafe for %',
                function_record.signature;
        END IF;
    END LOOP;
END
$expenseops_backup_verify$
"""

VERIFY_SQL = f"""
DO $expenseops_verify$
DECLARE
    object_name text;
    relation_oid regclass;
    function_record record;
    sequence_record record;
    allowed_function_oids oid[];
    application_function_oids oid[];
    runtime_record record;
    migrator_record record;
    backup_record record;
BEGIN
    SELECT * INTO runtime_record
    FROM pg_catalog.pg_roles
    WHERE rolname = '{RUNTIME_ROLE}';
    IF runtime_record IS NULL
       OR NOT runtime_record.rolcanlogin
       OR runtime_record.rolsuper
       OR runtime_record.rolbypassrls
       OR runtime_record.rolcreatedb
       OR runtime_record.rolcreaterole
       OR runtime_record.rolreplication
       OR runtime_record.rolinherit THEN
        RAISE EXCEPTION 'expenseops_runtime role attributes are unsafe';
    END IF;

    SELECT * INTO migrator_record
    FROM pg_catalog.pg_roles
    WHERE rolname = '{MIGRATOR_ROLE}';
    IF migrator_record IS NULL
       OR NOT migrator_record.rolcanlogin
       OR migrator_record.rolsuper
       OR NOT migrator_record.rolbypassrls
       OR migrator_record.rolcreatedb
       OR migrator_record.rolcreaterole
       OR migrator_record.rolreplication
       OR migrator_record.rolinherit THEN
        RAISE EXCEPTION 'expenseops_migrator role attributes are unsafe';
    END IF;

    SELECT * INTO backup_record
    FROM pg_catalog.pg_roles
    WHERE rolname = '{BACKUP_ROLE}';
    IF backup_record IS NULL
       OR NOT backup_record.rolcanlogin
       OR backup_record.rolsuper
       OR NOT backup_record.rolbypassrls
       OR backup_record.rolcreatedb
       OR backup_record.rolcreaterole
       OR backup_record.rolreplication
       OR backup_record.rolinherit
       OR coalesce(backup_record.rolconfig, ARRAY[]::text[])
          <> ARRAY['default_transaction_read_only=on']::text[] THEN
        RAISE EXCEPTION 'expenseops_backup role attributes are unsafe';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_auth_members AS auth_members
        JOIN pg_catalog.pg_roles AS member ON member.oid = auth_members.member
        JOIN pg_catalog.pg_roles AS granted ON granted.oid = auth_members.roleid
        WHERE member.rolname IN (
            '{RUNTIME_ROLE}', '{MIGRATOR_ROLE}', '{BACKUP_ROLE}'
        )
           OR granted.rolname IN (
               '{RUNTIME_ROLE}', '{MIGRATOR_ROLE}', '{BACKUP_ROLE}'
           )
    ) THEN
        RAISE EXCEPTION 'ExpenseOps database roles must not have role memberships';
    END IF;

    IF pg_catalog.has_database_privilege(
        '{RUNTIME_ROLE}', current_database(), 'CREATE'
    ) OR pg_catalog.has_database_privilege(
        '{RUNTIME_ROLE}', current_database(), 'TEMPORARY'
    ) OR NOT pg_catalog.has_database_privilege(
        '{RUNTIME_ROLE}', current_database(), 'CONNECT'
    ) OR pg_catalog.has_schema_privilege(
        '{RUNTIME_ROLE}', 'public', 'CREATE'
    ) OR NOT pg_catalog.has_schema_privilege(
        '{RUNTIME_ROLE}', 'public', 'USAGE'
    ) THEN
        RAISE EXCEPTION 'expenseops_runtime database/schema privileges are unsafe';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_database AS database_object,
             LATERAL pg_catalog.aclexplode(
                 coalesce(
                     database_object.datacl,
                     pg_catalog.acldefault('d', database_object.datdba)
                 )
             ) AS database_acl
        WHERE database_object.datname = current_database()
          AND database_acl.grantee = 0
          AND database_acl.privilege_type IN ('CONNECT', 'CREATE', 'TEMPORARY')
    ) THEN
        RAISE EXCEPTION 'PUBLIC database privileges are unsafe';
    END IF;
    IF NOT pg_catalog.has_database_privilege(
        '{MIGRATOR_ROLE}', current_database(), 'CONNECT'
    ) OR pg_catalog.has_database_privilege(
        '{MIGRATOR_ROLE}', current_database(), 'CREATE'
    ) OR pg_catalog.has_database_privilege(
        '{MIGRATOR_ROLE}', current_database(), 'TEMPORARY'
    ) OR NOT pg_catalog.has_schema_privilege(
        '{MIGRATOR_ROLE}', 'public', 'USAGE'
    ) OR NOT pg_catalog.has_schema_privilege(
        '{MIGRATOR_ROLE}', 'public', 'CREATE'
    ) THEN
        RAISE EXCEPTION 'expenseops_migrator database/schema privileges are unsafe';
    END IF;
    IF NOT pg_catalog.has_database_privilege(
        '{BACKUP_ROLE}', current_database(), 'CONNECT'
    ) OR pg_catalog.has_database_privilege(
        '{BACKUP_ROLE}', current_database(), 'CREATE'
    ) OR pg_catalog.has_database_privilege(
        '{BACKUP_ROLE}', current_database(), 'TEMPORARY'
    ) OR pg_catalog.has_schema_privilege(
        '{BACKUP_ROLE}', 'public', 'CREATE'
    ) OR NOT pg_catalog.has_schema_privilege(
        '{BACKUP_ROLE}', 'public', 'USAGE'
    ) THEN
        RAISE EXCEPTION 'expenseops_backup database/schema privileges are unsafe';
    END IF;
    IF (SELECT database_owner.rolname
        FROM pg_catalog.pg_database AS database_object
        JOIN pg_catalog.pg_roles AS database_owner
          ON database_owner.oid = database_object.datdba
        WHERE database_object.datname = current_database()) = '{MIGRATOR_ROLE}' THEN
        RAISE EXCEPTION 'expenseops_migrator must not own the database';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_database AS database_object
        JOIN pg_catalog.pg_roles AS database_owner
          ON database_owner.oid = database_object.datdba
        WHERE database_owner.rolname = '{BACKUP_ROLE}'
    ) THEN
        RAISE EXCEPTION 'expenseops_backup must not own a database';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace AS namespace_object,
             LATERAL pg_catalog.aclexplode(
                 coalesce(
                     namespace_object.nspacl,
                     pg_catalog.acldefault('n', namespace_object.nspowner)
                 )
             ) AS namespace_acl
        WHERE namespace_object.nspname = 'public'
          AND namespace_acl.grantee = 0
          AND namespace_acl.privilege_type IN ('USAGE', 'CREATE')
    ) THEN
        RAISE EXCEPTION 'PUBLIC schema privileges are unsafe';
    END IF;

    IF (SELECT namespace_owner.rolname
        FROM pg_catalog.pg_namespace AS namespace
        JOIN pg_catalog.pg_roles AS namespace_owner
          ON namespace_owner.oid = namespace.nspowner
        WHERE namespace.nspname = 'public') <> '{MIGRATOR_ROLE}' THEN
        RAISE EXCEPTION 'expenseops_migrator must own the public schema';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS object_class
        JOIN pg_catalog.pg_namespace AS object_namespace
          ON object_namespace.oid = object_class.relnamespace
        JOIN pg_catalog.pg_roles AS object_owner
          ON object_owner.oid = object_class.relowner
        WHERE object_namespace.nspname = 'public'
          AND object_owner.rolname = '{RUNTIME_ROLE}'
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc AS function_object
        JOIN pg_catalog.pg_namespace AS object_namespace
          ON object_namespace.oid = function_object.pronamespace
        JOIN pg_catalog.pg_roles AS object_owner
          ON object_owner.oid = function_object.proowner
        WHERE object_namespace.nspname = 'public'
          AND object_owner.rolname = '{RUNTIME_ROLE}'
    ) THEN
        RAISE EXCEPTION 'expenseops_runtime must not own schema objects';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS object_class
        JOIN pg_catalog.pg_roles AS object_owner
          ON object_owner.oid = object_class.relowner
        WHERE object_owner.rolname = '{BACKUP_ROLE}'
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc AS function_object
        JOIN pg_catalog.pg_roles AS object_owner
          ON object_owner.oid = function_object.proowner
        WHERE object_owner.rolname = '{BACKUP_ROLE}'
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_type AS type_object
        JOIN pg_catalog.pg_roles AS object_owner
          ON object_owner.oid = type_object.typowner
        WHERE object_owner.rolname = '{BACKUP_ROLE}'
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace AS namespace_object
        JOIN pg_catalog.pg_roles AS object_owner
          ON object_owner.oid = namespace_object.nspowner
        WHERE object_owner.rolname = '{BACKUP_ROLE}'
    ) THEN
        RAISE EXCEPTION 'expenseops_backup must not own database objects';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS object_class
        JOIN pg_catalog.pg_namespace AS object_namespace
          ON object_namespace.oid = object_class.relnamespace
        JOIN pg_catalog.pg_roles AS object_owner
          ON object_owner.oid = object_class.relowner
        WHERE object_namespace.nspname = 'public'
          AND object_owner.rolname = '{MIGRATOR_ROLE}'
          AND object_class.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND object_class.relname <> ALL ({TABLE_ARRAY_SQL})
    ) THEN
        RAISE EXCEPTION 'migration role owns an unexpected public relation';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS sequence_object
        JOIN pg_catalog.pg_namespace AS sequence_namespace
          ON sequence_namespace.oid = sequence_object.relnamespace
        JOIN pg_catalog.pg_roles AS sequence_owner
          ON sequence_owner.oid = sequence_object.relowner
        WHERE sequence_namespace.nspname = 'public'
          AND sequence_object.relkind = 'S'
          AND sequence_owner.rolname = '{MIGRATOR_ROLE}'
          AND NOT EXISTS (
              SELECT 1
              FROM pg_catalog.pg_depend AS dependency
              JOIN pg_catalog.pg_class AS table_class
                ON table_class.oid = dependency.refobjid
              JOIN pg_catalog.pg_namespace AS table_namespace
                ON table_namespace.oid = table_class.relnamespace
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = sequence_object.oid
                AND dependency.deptype IN ('a', 'i')
                AND table_namespace.nspname = 'public'
                AND table_class.relname = ANY ({TABLE_ARRAY_SQL})
          )
    ) THEN
        RAISE EXCEPTION 'migration role owns an unexpected public sequence';
    END IF;

    FOREACH object_name IN ARRAY {TABLE_ARRAY_SQL}
    LOOP
        relation_oid := pg_catalog.to_regclass(format('%I.%I', 'public', object_name));
        IF relation_oid IS NULL THEN
            CONTINUE;
        END IF;
        IF (SELECT owner.rolname
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_roles AS owner ON owner.oid = relation.relowner
            WHERE relation.oid = relation_oid) <> '{MIGRATOR_ROLE}' THEN
            RAISE EXCEPTION 'migration role does not own public.%', object_name;
        END IF;
        IF object_name = 'alembic_version' THEN
            IF NOT pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'SELECT')
               OR pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'INSERT')
               OR pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'UPDATE')
               OR pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'DELETE')
               OR pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'TRUNCATE')
               OR pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'REFERENCES')
               OR pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'TRIGGER') THEN
                RAISE EXCEPTION 'runtime alembic_version privileges are unsafe';
            END IF;
        ELSIF object_name = ANY ({RUNTIME_APPEND_ONLY_TABLE_ARRAY_SQL}) THEN
            IF NOT pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'SELECT')
               OR NOT pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'INSERT')
               OR pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'UPDATE')
               OR pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'DELETE')
               OR pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'TRUNCATE')
               OR pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'REFERENCES')
               OR pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'TRIGGER') THEN
                RAISE EXCEPTION 'runtime append-only table privileges are unsafe for public.%',
                    object_name;
            END IF;
        ELSIF NOT pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'SELECT')
           OR NOT pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'INSERT')
           OR NOT pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'UPDATE')
           OR NOT pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'DELETE')
           OR pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'TRUNCATE')
           OR pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'REFERENCES')
           OR pg_catalog.has_table_privilege('{RUNTIME_ROLE}', relation_oid, 'TRIGGER') THEN
            RAISE EXCEPTION 'runtime table privileges are unsafe for public.%', object_name;
        END IF;
        IF NOT pg_catalog.has_table_privilege('{BACKUP_ROLE}', relation_oid, 'SELECT')
           OR pg_catalog.has_table_privilege('{BACKUP_ROLE}', relation_oid, 'INSERT')
           OR pg_catalog.has_table_privilege('{BACKUP_ROLE}', relation_oid, 'UPDATE')
           OR pg_catalog.has_table_privilege('{BACKUP_ROLE}', relation_oid, 'DELETE')
           OR pg_catalog.has_table_privilege('{BACKUP_ROLE}', relation_oid, 'TRUNCATE')
           OR pg_catalog.has_table_privilege('{BACKUP_ROLE}', relation_oid, 'REFERENCES')
           OR pg_catalog.has_table_privilege('{BACKUP_ROLE}', relation_oid, 'TRIGGER')
           OR pg_catalog.has_any_column_privilege(
               '{BACKUP_ROLE}', relation_oid, 'INSERT,UPDATE,REFERENCES'
           ) THEN
            RAISE EXCEPTION 'backup table privileges are unsafe for public.%', object_name;
        END IF;
    END LOOP;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS relation
        JOIN pg_catalog.pg_namespace AS relation_namespace
          ON relation_namespace.oid = relation.relnamespace
        WHERE relation_namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND relation.relname <> ALL ({TABLE_ARRAY_SQL})
          AND (
              pg_catalog.has_table_privilege(
                  '{RUNTIME_ROLE}', relation.oid, 'SELECT'
              ) OR pg_catalog.has_table_privilege(
                  '{RUNTIME_ROLE}', relation.oid, 'INSERT'
              ) OR pg_catalog.has_table_privilege(
                  '{RUNTIME_ROLE}', relation.oid, 'UPDATE'
              ) OR pg_catalog.has_table_privilege(
                  '{RUNTIME_ROLE}', relation.oid, 'DELETE'
              ) OR pg_catalog.has_table_privilege(
                  '{RUNTIME_ROLE}', relation.oid, 'TRUNCATE'
              ) OR pg_catalog.has_table_privilege(
                  '{RUNTIME_ROLE}', relation.oid, 'REFERENCES'
              ) OR pg_catalog.has_table_privilege(
                  '{RUNTIME_ROLE}', relation.oid, 'TRIGGER'
              )
          )
    ) THEN
        RAISE EXCEPTION 'runtime has privileges on an unexpected public relation';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS relation
        JOIN pg_catalog.pg_namespace AS relation_namespace
          ON relation_namespace.oid = relation.relnamespace
        WHERE relation_namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND relation.relname <> ALL ({TABLE_ARRAY_SQL})
          AND (
              pg_catalog.has_table_privilege(
                  '{BACKUP_ROLE}', relation.oid, 'SELECT'
              ) OR pg_catalog.has_table_privilege(
                  '{BACKUP_ROLE}', relation.oid, 'INSERT'
              ) OR pg_catalog.has_table_privilege(
                  '{BACKUP_ROLE}', relation.oid, 'UPDATE'
              ) OR pg_catalog.has_table_privilege(
                  '{BACKUP_ROLE}', relation.oid, 'DELETE'
              ) OR pg_catalog.has_table_privilege(
                  '{BACKUP_ROLE}', relation.oid, 'TRUNCATE'
              ) OR pg_catalog.has_table_privilege(
                  '{BACKUP_ROLE}', relation.oid, 'REFERENCES'
              ) OR pg_catalog.has_table_privilege(
                  '{BACKUP_ROLE}', relation.oid, 'TRIGGER'
              ) OR pg_catalog.has_any_column_privilege(
                  '{BACKUP_ROLE}', relation.oid, 'SELECT,INSERT,UPDATE,REFERENCES'
              )
          )
    ) THEN
        RAISE EXCEPTION 'backup has privileges on an unexpected public relation';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS sequence_object
        JOIN pg_catalog.pg_namespace AS sequence_namespace
          ON sequence_namespace.oid = sequence_object.relnamespace
        WHERE sequence_namespace.nspname = 'public'
          AND sequence_object.relkind = 'S'
          AND NOT EXISTS (
              SELECT 1
              FROM pg_catalog.pg_depend AS dependency
              JOIN pg_catalog.pg_class AS table_class
                ON table_class.oid = dependency.refobjid
              JOIN pg_catalog.pg_namespace AS table_namespace
                ON table_namespace.oid = table_class.relnamespace
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = sequence_object.oid
                AND dependency.deptype IN ('a', 'i')
                AND table_namespace.nspname = 'public'
                AND table_class.relname = ANY ({TABLE_ARRAY_SQL})
          )
          AND (
              pg_catalog.has_sequence_privilege(
                  '{RUNTIME_ROLE}', sequence_object.oid, 'USAGE'
              ) OR pg_catalog.has_sequence_privilege(
                  '{RUNTIME_ROLE}', sequence_object.oid, 'SELECT'
              ) OR pg_catalog.has_sequence_privilege(
                  '{RUNTIME_ROLE}', sequence_object.oid, 'UPDATE'
              )
          )
    ) THEN
        RAISE EXCEPTION 'runtime has privileges on an unexpected public sequence';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS sequence_object
        JOIN pg_catalog.pg_namespace AS sequence_namespace
          ON sequence_namespace.oid = sequence_object.relnamespace
        WHERE sequence_namespace.nspname = 'public'
          AND sequence_object.relkind = 'S'
          AND NOT EXISTS (
              SELECT 1
              FROM pg_catalog.pg_depend AS dependency
              JOIN pg_catalog.pg_class AS table_class
                ON table_class.oid = dependency.refobjid
              JOIN pg_catalog.pg_namespace AS table_namespace
                ON table_namespace.oid = table_class.relnamespace
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = sequence_object.oid
                AND dependency.deptype IN ('a', 'i')
                AND table_namespace.nspname = 'public'
                AND table_class.relname = ANY ({TABLE_ARRAY_SQL})
          )
          AND (
              pg_catalog.has_sequence_privilege(
                  '{BACKUP_ROLE}', sequence_object.oid, 'USAGE'
              ) OR pg_catalog.has_sequence_privilege(
                  '{BACKUP_ROLE}', sequence_object.oid, 'SELECT'
              ) OR pg_catalog.has_sequence_privilege(
                  '{BACKUP_ROLE}', sequence_object.oid, 'UPDATE'
              )
          )
    ) THEN
        RAISE EXCEPTION 'backup has privileges on an unexpected public sequence';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_type AS type_object
        JOIN pg_catalog.pg_namespace AS type_namespace
          ON type_namespace.oid = type_object.typnamespace
        WHERE type_namespace.nspname = 'public'
          AND NOT (
              type_object.typelem <> 0
              AND type_object.typsubscript =
                  'pg_catalog.array_subscript_handler'::pg_catalog.regproc
          )
          AND type_object.typtype <> 'm'
          AND pg_catalog.has_type_privilege(
              '{RUNTIME_ROLE}', type_object.oid, 'USAGE'
          )
    ) THEN
        RAISE EXCEPTION 'runtime has privileges on an unexpected public type';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_type AS type_object
        JOIN pg_catalog.pg_namespace AS type_namespace
          ON type_namespace.oid = type_object.typnamespace
        WHERE type_namespace.nspname = 'public'
          AND NOT (
              type_object.typelem <> 0
              AND type_object.typsubscript =
                  'pg_catalog.array_subscript_handler'::pg_catalog.regproc
          )
          AND type_object.typtype <> 'm'
          AND pg_catalog.has_type_privilege(
              '{BACKUP_ROLE}', type_object.oid, 'USAGE'
          )
    ) THEN
        RAISE EXCEPTION 'backup has privileges on a public type';
    END IF;

    FOR sequence_record IN
        SELECT DISTINCT sequence_class.oid,
                        sequence_owner.rolname AS owner_name
        FROM pg_catalog.pg_class AS sequence_class
        JOIN pg_catalog.pg_namespace AS sequence_namespace
          ON sequence_namespace.oid = sequence_class.relnamespace
        JOIN pg_catalog.pg_depend AS dependency
          ON dependency.classid = 'pg_class'::regclass
         AND dependency.objid = sequence_class.oid
         AND dependency.deptype IN ('a', 'i')
        JOIN pg_catalog.pg_class AS table_class
          ON table_class.oid = dependency.refobjid
        JOIN pg_catalog.pg_namespace AS table_namespace
          ON table_namespace.oid = table_class.relnamespace
        JOIN pg_catalog.pg_roles AS sequence_owner
          ON sequence_owner.oid = sequence_class.relowner
        WHERE sequence_class.relkind = 'S'
          AND sequence_namespace.nspname = 'public'
          AND table_namespace.nspname = 'public'
          AND table_class.relname = ANY ({TABLE_ARRAY_SQL})
    LOOP
        IF sequence_record.owner_name <> '{MIGRATOR_ROLE}'
           OR NOT pg_catalog.has_sequence_privilege(
            '{RUNTIME_ROLE}', sequence_record.oid, 'USAGE'
        ) OR NOT pg_catalog.has_sequence_privilege(
            '{RUNTIME_ROLE}', sequence_record.oid, 'SELECT'
        ) OR pg_catalog.has_sequence_privilege(
            '{RUNTIME_ROLE}', sequence_record.oid, 'UPDATE'
        ) THEN
            RAISE EXCEPTION 'runtime sequence privileges are unsafe for %',
                sequence_record.oid::regclass;
        END IF;
        IF NOT pg_catalog.has_sequence_privilege(
            '{BACKUP_ROLE}', sequence_record.oid, 'SELECT'
        ) OR pg_catalog.has_sequence_privilege(
            '{BACKUP_ROLE}', sequence_record.oid, 'USAGE'
        ) OR pg_catalog.has_sequence_privilege(
            '{BACKUP_ROLE}', sequence_record.oid, 'UPDATE'
        ) THEN
            RAISE EXCEPTION 'backup sequence privileges are unsafe for %',
                sequence_record.oid::regclass;
        END IF;
    END LOOP;

    SELECT pg_catalog.array_agg(pg_catalog.to_regprocedure(route.signature)::oid)
    INTO allowed_function_oids
    FROM pg_catalog.unnest({ROUTING_FUNCTION_ARRAY_SQL}) AS route(signature)
    WHERE pg_catalog.to_regprocedure(route.signature) IS NOT NULL;
    allowed_function_oids := coalesce(allowed_function_oids, ARRAY[]::oid[]);

    SELECT pg_catalog.array_agg(pg_catalog.to_regprocedure(app_function.signature)::oid)
    INTO application_function_oids
    FROM pg_catalog.unnest({FUNCTION_ARRAY_SQL}) AS app_function(signature)
    WHERE pg_catalog.to_regprocedure(app_function.signature) IS NOT NULL;
    application_function_oids := coalesce(
        application_function_oids,
        ARRAY[]::oid[]
    );

    FOR function_record IN
        SELECT function_object.oid,
               function_object.oid::regprocedure AS signature,
               function_owner.rolname AS owner_name
        FROM pg_catalog.pg_proc AS function_object
        JOIN pg_catalog.pg_namespace AS function_namespace
          ON function_namespace.oid = function_object.pronamespace
        JOIN pg_catalog.pg_roles AS function_owner
          ON function_owner.oid = function_object.proowner
        WHERE function_namespace.nspname = 'public'
    LOOP
        IF pg_catalog.has_function_privilege(
            '{RUNTIME_ROLE}', function_record.oid, 'EXECUTE'
        ) <> (function_record.oid = ANY (allowed_function_oids)) THEN
            RAISE EXCEPTION 'runtime function privilege is unsafe for %',
                function_record.signature;
        END IF;
        IF pg_catalog.has_function_privilege(
            '{BACKUP_ROLE}', function_record.oid, 'EXECUTE'
        ) THEN
            RAISE EXCEPTION 'backup function privilege is unsafe for %',
                function_record.signature;
        END IF;
        IF function_record.oid = ANY (application_function_oids)
           AND function_record.owner_name <> '{MIGRATOR_ROLE}' THEN
            RAISE EXCEPTION 'migration role does not own function %',
                function_record.signature;
        END IF;
        IF function_record.owner_name = '{MIGRATOR_ROLE}'
           AND function_record.oid <> ALL (application_function_oids) THEN
            RAISE EXCEPTION 'migration role owns unexpected function %',
                function_record.signature;
        END IF;
    END LOOP;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_default_acl AS default_acl_object
        JOIN pg_catalog.pg_roles AS default_acl_owner
          ON default_acl_owner.oid = default_acl_object.defaclrole
        LEFT JOIN pg_catalog.pg_namespace AS default_acl_namespace
          ON default_acl_namespace.oid = default_acl_object.defaclnamespace
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            default_acl_object.defaclacl
        ) AS default_acl
        WHERE default_acl.grantee = backup_record.oid
           OR (
               default_acl_owner.rolname = '{MIGRATOR_ROLE}'
               AND (
                   default_acl_namespace.oid IS NULL
                   OR default_acl_namespace.nspname = 'public'
               )
               AND default_acl.grantee = 0
           )
    ) THEN
        RAISE EXCEPTION 'backup/default privileges are unsafe for future objects';
    END IF;
END
$expenseops_verify$
"""

PRIVILEGE_STATEMENTS = (
    DATABASE_AND_SCHEMA_SQL,
    OWNERSHIP_SQL,
    RUNTIME_GRANTS_SQL,
    DEFAULT_PRIVILEGES_SQL,
    VERIFY_SQL,
)


def render_plan() -> str:
    statements = [
        "-- ExpenseOps database role bootstrap (dry run; nothing executed)",
        f"-- {ADMIN_URL_ENV} is read only in guarded mutation modes and is never printed.",
        *(_normalized(statement) + ";" for statement in SESSION_STATEMENTS),
        *(_normalized(statement) + ";" for statement in ROLE_STATEMENTS),
        (
            "SELECT pg_catalog.set_config("
            f"'{RUNTIME_PASSWORD_SETTING}', %({RUNTIME_PASSWORD_ENV})s, true);"
        ),
        (
            "SELECT pg_catalog.set_config("
            f"'{MIGRATOR_PASSWORD_SETTING}', %({MIGRATOR_PASSWORD_ENV})s, true);"
        ),
        (
            "SELECT pg_catalog.set_config("
            f"'{BACKUP_PASSWORD_SETTING}', %({BACKUP_PASSWORD_ENV})s, true);"
        ),
        _normalized(PASSWORD_SQL) + ";",
        *(_normalized(statement) + ";" for statement in PRIVILEGE_STATEMENTS),
        ("-- After Alembic, run --reconcile-runtime-grants with the migration-role DATABASE_URL."),
    ]
    return "\n\n".join(statements)


def _normalized(statement: str) -> str:
    return statement.strip().rstrip(";")


def _postgres_dsn(value: str, *, source_name: str) -> str:
    if value.startswith("postgresql+psycopg://"):
        return value.replace("postgresql+psycopg://", "postgresql://", 1)
    if value.startswith(("postgresql://", "postgres://")):
        return value
    raise ValueError(f"{source_name} must be a PostgreSQL connection URL")


def _required_secret(environment: dict[str, str], name: str) -> str:
    value = environment.get(name, "")
    if len(value) < MINIMUM_PASSWORD_LENGTH:
        raise ValueError(f"{name} must contain at least {MINIMUM_PASSWORD_LENGTH} characters")
    return value


def apply_bootstrap(
    *,
    admin_url: str,
    runtime_password: str,
    migrator_password: str,
    backup_password: str,
) -> None:
    dsn = _postgres_dsn(admin_url, source_name=ADMIN_URL_ENV)
    if len({runtime_password, migrator_password, backup_password}) != 3:
        raise ValueError("Runtime, migrator, and backup passwords must be different")
    if len(runtime_password) < MINIMUM_PASSWORD_LENGTH:
        raise ValueError("Runtime password is too short")
    if len(migrator_password) < MINIMUM_PASSWORD_LENGTH:
        raise ValueError("Migrator password is too short")
    if len(backup_password) < MINIMUM_PASSWORD_LENGTH:
        raise ValueError("Backup password is too short")

    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        for statement in SESSION_STATEMENTS:
            cursor.execute(statement)
        cursor.execute(
            """
            SELECT rolsuper
            FROM pg_catalog.pg_roles
            WHERE rolname = current_user
            """
        )
        admin_row = cursor.fetchone()
        if admin_row is None or not admin_row[0]:
            raise RuntimeError("Role bootstrap requires a PostgreSQL superuser connection")

        for statement in ROLE_STATEMENTS:
            cursor.execute(statement)
        cursor.execute(
            f"SELECT pg_catalog.set_config('{RUNTIME_PASSWORD_SETTING}', %s, true)",
            (runtime_password,),
        )
        cursor.execute(
            f"SELECT pg_catalog.set_config('{MIGRATOR_PASSWORD_SETTING}', %s, true)",
            (migrator_password,),
        )
        cursor.execute(
            f"SELECT pg_catalog.set_config('{BACKUP_PASSWORD_SETTING}', %s, true)",
            (backup_password,),
        )
        cursor.execute(PASSWORD_SQL)
        for statement in PRIVILEGE_STATEMENTS:
            cursor.execute(statement)


def bootstrap_backup_role(*, admin_url: str, backup_password: str) -> None:
    """Provision only the read-only backup role before the full role cutover."""

    dsn = _postgres_dsn(admin_url, source_name=ADMIN_URL_ENV)
    if len(backup_password) < MINIMUM_PASSWORD_LENGTH:
        raise ValueError("Backup password is too short")

    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        for statement in SESSION_STATEMENTS:
            cursor.execute(statement)
        cursor.execute(
            """
            SELECT rolsuper
            FROM pg_catalog.pg_roles
            WHERE rolname = current_user
            """
        )
        admin_row = cursor.fetchone()
        if admin_row is None or not admin_row[0]:
            raise RuntimeError("Backup-role bootstrap requires a PostgreSQL superuser connection")

        for statement in BACKUP_ROLE_STATEMENTS:
            cursor.execute(statement)
        cursor.execute(
            f"SELECT pg_catalog.set_config('{BACKUP_PASSWORD_SETTING}', %s, true)",
            (backup_password,),
        )
        for statement in (
            BACKUP_PASSWORD_SQL,
            BACKUP_DATABASE_AND_SCHEMA_SQL,
            BACKUP_GRANTS_SQL,
            BACKUP_VERIFY_SQL,
        ):
            cursor.execute(statement)


def reconcile_runtime_grants(*, database_url: str) -> None:
    """Reconcile runtime ACLs as the already-provisioned migration role.

    This mode deliberately cannot create/alter roles or transfer pre-existing
    ownership.  It is safe to run after every Alembic upgrade because new
    migration-owned objects receive the reviewed runtime/default ACLs before a
    new application revision is eligible to deploy.
    """

    dsn = _postgres_dsn(database_url, source_name=MIGRATION_URL_ENV)
    expected_role = (
        MIGRATOR_ROLE,
        True,  # LOGIN
        False,  # NOSUPERUSER
        True,  # BYPASSRLS for controlled migrations
        False,  # NOCREATEDB
        False,  # NOCREATEROLE
        False,  # NOREPLICATION
        False,  # NOINHERIT
    )
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        for statement in SESSION_STATEMENTS:
            cursor.execute(statement)
        cursor.execute(
            """
            SELECT rolname,
                   rolcanlogin,
                   rolsuper,
                   rolbypassrls,
                   rolcreatedb,
                   rolcreaterole,
                   rolreplication,
                   rolinherit
            FROM pg_catalog.pg_roles
            WHERE rolname = current_user
            """
        )
        if cursor.fetchone() != expected_role:
            raise RuntimeError(
                "Runtime-grant reconciliation requires the exact expenseops_migrator role"
            )
        for statement in (
            RECONCILE_REQUIRED_FUNCTIONS_SQL,
            RUNTIME_GRANTS_SQL,
            DEFAULT_PRIVILEGES_SQL,
            VERIFY_SQL,
        ):
            cursor.execute(statement)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply the least-privilege ExpenseOps PostgreSQL role split."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the secret-free SQL plan (default).",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Apply atomically using credentials supplied only through environment variables.",
    )
    mode.add_argument(
        "--bootstrap-backup-role",
        action="store_true",
        help=(
            "Before the full cutover, provision only expenseops_backup using the admin URL "
            "and backup password from environment variables."
        ),
    )
    mode.add_argument(
        "--reconcile-runtime-grants",
        action="store_true",
        help=(
            "After Alembic, atomically reconcile ACLs using only the migration role DATABASE_URL."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.bootstrap_backup_role:
        forbidden_role_passwords = [
            name for name in (RUNTIME_PASSWORD_ENV, MIGRATOR_PASSWORD_ENV) if os.environ.get(name)
        ]
        if forbidden_role_passwords:
            raise SystemExit(
                "Runtime/migrator passwords must not be present during --bootstrap-backup-role"
            )
        environment = dict(os.environ)
        admin_url = environment.get(ADMIN_URL_ENV, "")
        if not admin_url:
            raise SystemExit(f"{ADMIN_URL_ENV} is required with --bootstrap-backup-role")
        try:
            backup_password = _required_secret(environment, BACKUP_PASSWORD_ENV)
            bootstrap_backup_role(
                admin_url=admin_url,
                backup_password=backup_password,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        print(
            "ExpenseOps backup database role and read-only privileges verified. "
            "No connection URL or role password was printed."
        )
        return 0
    if args.reconcile_runtime_grants:
        forbidden_bootstrap_inputs = [
            name
            for name in (
                ADMIN_URL_ENV,
                RUNTIME_PASSWORD_ENV,
                MIGRATOR_PASSWORD_ENV,
                BACKUP_PASSWORD_ENV,
            )
            if os.environ.get(name)
        ]
        if forbidden_bootstrap_inputs:
            raise SystemExit(
                "Bootstrap/admin credentials must not be present during --reconcile-runtime-grants"
            )
        database_url = os.environ.get(MIGRATION_URL_ENV, "")
        if not database_url:
            raise SystemExit(f"{MIGRATION_URL_ENV} is required with --reconcile-runtime-grants")
        try:
            reconcile_runtime_grants(database_url=database_url)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        print(
            "ExpenseOps runtime database grants reconciled and verified as "
            "expenseops_migrator. No database URL was printed."
        )
        return 0
    if not args.apply:
        print(render_plan())
        return 0

    environment = dict(os.environ)
    admin_url = environment.get(ADMIN_URL_ENV, "")
    if not admin_url:
        raise SystemExit(f"{ADMIN_URL_ENV} is required with --apply")
    try:
        runtime_password = _required_secret(environment, RUNTIME_PASSWORD_ENV)
        migrator_password = _required_secret(environment, MIGRATOR_PASSWORD_ENV)
        backup_password = _required_secret(environment, BACKUP_PASSWORD_ENV)
        apply_bootstrap(
            admin_url=admin_url,
            runtime_password=runtime_password,
            migrator_password=migrator_password,
            backup_password=backup_password,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print(
        "ExpenseOps database roles and privileges verified. "
        "No connection URL or role password was printed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

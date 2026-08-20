import ast
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from scripts.bootstrap_database_roles import APPLICATION_TABLES

ROOT = Path(__file__).resolve().parents[1]


def _json_file(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def _inline_hobby_recovery_program() -> str:
    workflow = (ROOT / ".github/workflows/production-release.yml").read_text(encoding="utf-8")
    step_start = workflow.index("Create and restore-verify a fresh logical backup")
    program_start = workflow.index("          python - <<'PY'\n", step_start)
    program_start = workflow.index("\n", program_start) + 1
    program_end = workflow.index("\n          PY", program_start)
    return "\n".join(
        line.removeprefix("          ") for line in workflow[program_start:program_end].splitlines()
    )


def test_dockerfile_packages_sandbox_for_app_import():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "app ./app" in dockerfile
    assert "sandbox ./sandbox" in dockerfile
    assert "USER expenseops" in dockerfile


def test_dockerfile_builds_frontend_and_packages_migrations():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "npm run build" in dockerfile
    assert "/frontend/dist ./app/static" in dockerfile
    assert "alembic ./alembic" in dockerfile
    assert "alembic.ini ./" in dockerfile
    assert "scripts/bootstrap_database_roles.py ./scripts/bootstrap_database_roles.py" in dockerfile
    # Migrations run once as a deployment pre-step, not concurrently in every app replica.
    assert "alembic upgrade head" not in dockerfile


def test_dockerfile_can_start_the_durable_outbox_worker():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "EXPENSEOPS_PROCESS" in dockerfile
    assert "python -m app.jobs.outbox" in dockerfile
    assert "uvicorn app.main:app" in dockerfile
    assert "EXPENSEOPS_PROCESS must be explicit in production" in dockerfile
    assert "EXPENSEOPS_PROCESS:-web" not in dockerfile


def test_release_gate_verifies_migrations_before_building_the_image():
    workflow = (ROOT / ".github/workflows/release-gate.yml").read_text(encoding="utf-8")

    assert "alembic upgrade head" in workflow
    assert "alembic check" in workflow
    assert "docker/build-push-action" in workflow


def test_shared_railway_config_remains_neutral_for_service_specific_configs():
    assert _json_file("railway.json") == {"$schema": "https://railway.com/railway.schema.json"}


def test_web_railway_config_is_migration_free_and_uses_the_phase_health_gate():
    config = _json_file("railway.web.json")

    assert config["build"] == {
        "builder": "DOCKERFILE",
        "dockerfilePath": "Dockerfile",
    }
    deploy = config["deploy"]
    assert "uvicorn app.main:app" in deploy["startCommand"]
    assert "alembic" not in deploy["startCommand"].casefold()
    assert "preDeployCommand" not in deploy
    hardened_migration = ROOT / "alembic/versions/20260815_0029_harden_tenant_rls_policies.py"
    assert deploy["healthcheckPath"] == ("/readiness" if hardened_migration.exists() else "/health")
    assert deploy["healthcheckTimeout"] > 0


def test_migration_railway_config_is_one_shot_and_fail_closed():
    config = _json_file("railway.migrations.json")

    assert config["build"] == {
        "builder": "DOCKERFILE",
        "dockerfilePath": "Dockerfile",
    }
    deploy = config["deploy"]
    command = deploy["preDeployCommand"]
    assert "alembic upgrade head" in command
    assert "alembic current --check-heads" in command
    assert "bootstrap_database_roles.py --reconcile-runtime-grants" in command
    assert command.index("alembic upgrade head") < command.index(
        "bootstrap_database_roles.py --reconcile-runtime-grants"
    )
    assert "&&" in command
    assert "uvicorn" not in command
    assert "sleep infinity" in deploy["startCommand"]
    assert "alembic" not in deploy["startCommand"]
    assert deploy["restartPolicyType"] == "NEVER"
    assert "healthcheckPath" not in deploy


@pytest.mark.parametrize(
    ("config_name", "expected_command", "restart_policy"),
    [
        ("railway.outbox.json", "python -m app.jobs.outbox", "ON_FAILURE"),
        (
            "railway.classification-finalizer.json",
            "python -m app.jobs.classification_finalizer --forever",
            "ON_FAILURE",
        ),
        (
            "railway.gmail-receipts.json",
            "python -m app.jobs.gmail_receipts --max-results 25",
            "NEVER",
        ),
        (
            "railway.gmail-promotions.json",
            "python -m app.jobs.promotions sync",
            "NEVER",
        ),
    ],
)
def test_runtime_railway_configs_pin_process_without_migrations(
    config_name, expected_command, restart_policy
):
    config = _json_file(config_name)

    assert config["build"] == {
        "builder": "DOCKERFILE",
        "dockerfilePath": "Dockerfile",
    }
    deploy = config["deploy"]
    assert expected_command in deploy["startCommand"]
    assert "alembic" not in deploy["startCommand"]
    assert "uvicorn" not in deploy["startCommand"]
    assert "preDeployCommand" not in deploy
    assert "healthcheckPath" not in deploy
    assert deploy["restartPolicyType"] == restart_policy


def test_cron_configs_leave_existing_railway_schedules_unchanged():
    for config_name in (
        "railway.gmail-receipts.json",
        "railway.gmail-promotions.json",
    ):
        assert "cronSchedule" not in _json_file(config_name)["deploy"]


def test_production_release_is_manual_and_all_runtimes_precede_web():
    workflow = (ROOT / ".github/workflows/production-release.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "pull_request:" not in workflow
    assert "branches:" not in workflow
    assert "environment: production" in workflow
    assert "timeout-minutes: 120" in workflow
    action_uses = re.findall(r"uses: (actions/[^@\s]+)@([^\s]+)", workflow)
    assert action_uses
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for _, revision in action_uses)
    assert "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in workflow
    assert 'python-version: "3.11.13"' in workflow
    assert "psycopg==3.3.4 --hash=sha256:b6bbc25c" in workflow
    assert "psycopg-binary==3.3.4 --hash=sha256:ab8cca8e" in workflow
    assert "typing-extensions==4.15.0 --hash=sha256:f0fa19c6" in workflow
    assert "--require-hashes" in workflow
    assert "--only-binary=:all:" in workflow
    assert "--force-reinstall" in workflow
    assert "--no-deps" in workflow
    postgres_image = (
        "postgres:18.1-bookworm@sha256:"
        "cc9f4143a8d2fa8cf3749d0cb4d26ecf2d53a77a2ac807e9ebd67ae22426221a"
    )
    assert workflow.count(postgres_image) == 2
    assert "ref: ${{ inputs.release_sha }}" in workflow
    assert "git merge-base --is-ancestor" in workflow
    assert "RAILWAY_MIGRATION_SERVICE_ID" not in workflow
    assert (
        "EXPENSEOPS_MIGRATION_DATABASE_URL: ${{ secrets.EXPENSEOPS_MIGRATION_DATABASE_URL }}"
    ) in workflow
    assert "RAILWAY_OUTBOX_SERVICE_ID" in workflow
    assert "RAILWAY_CLASSIFICATION_FINALIZER_SERVICE_ID" in workflow
    assert "RAILWAY_GMAIL_RECEIPTS_SERVICE_ID" in workflow
    assert "RAILWAY_GMAIL_PROMOTIONS_SERVICE_ID" in workflow
    assert "RAILWAY_WEB_SERVICE_ID" in workflow
    assert "Each release component must use a distinct Railway service ID." in workflow
    migration_step = workflow.index(
        "Apply migrations and reconcile grants before any Railway deployment"
    )
    outbox_step = workflow.index("Deploy outbox worker after migrations succeed")
    finalizer_step = workflow.index("Deploy classification finalizer after outbox succeeds")
    receipts_step = workflow.index(
        "Deploy Gmail receipts cron after classification finalizer succeeds"
    )
    promotions_step = workflow.index("Deploy Gmail promotions cron after receipts succeeds")
    web_step = workflow.index("Deploy web only after every runtime succeeds")
    assert (
        migration_step
        < outbox_step
        < finalizer_step
        < receipts_step
        < promotions_step
        < web_step
    )
    assert workflow.count('"${RELEASE_SHA}"') == 5
    assert "continue-on-error" not in workflow
    assert "/railway.migrations.json" not in workflow
    assert "/railway.outbox.json" in workflow
    assert "/railway.classification-finalizer.json" in workflow
    assert "/railway.gmail-receipts.json" in workflow
    assert "/railway.gmail-promotions.json" in workflow
    assert "/railway.web.json" in workflow
    assert "AGENT_WRITE_ACTIONS_ENABLED" in workflow
    assert "AGENT_PROACTIVE_ENABLED" in workflow
    assert "AGENT_PURCHASING_ENABLED" in workflow
    assert "expected_web_agent_write_actions_enabled:" in workflow
    assert "expected_web_agent_proactive_enabled:" in workflow
    assert "expected_autonomous_classification_enabled:" in workflow
    assert "Verify approved Agent rollout flags" in workflow
    assert "Verify the Agent remains read only" not in workflow
    assert (
        workflow.count('verify_service_flags "${RAILWAY_OUTBOX_SERVICE_ID}" false false false') == 1
    )
    assert (
        workflow.count(
            '"${RAILWAY_CLASSIFICATION_FINALIZER_SERVICE_ID}" false false false'
        )
        == 1
    )
    assert (
        workflow.count(
            'verify_service_flags "${RAILWAY_GMAIL_RECEIPTS_SERVICE_ID}" false false false'
        )
        == 1
    )
    assert (
        workflow.count(
            'verify_service_flags "${RAILWAY_GMAIL_PROMOTIONS_SERVICE_ID}" false false false'
        )
        == 1
    )
    assert "${EXPECTED_WEB_AGENT_WRITE_ACTIONS_ENABLED}" in workflow
    assert "${EXPECTED_WEB_AGENT_PROACTIVE_ENABLED}" in workflow
    assert "Expected Agent rollout inputs must be true or false." in workflow
    assert "AUTONOMOUS_CLASSIFICATION_ENABLED does not match the approved release state" in workflow
    assert workflow.count('verify_classification_flag "${service_id}"') == 1
    assert workflow.count('"${RAILWAY_CLASSIFICATION_FINALIZER_SERVICE_ID}"') >= 8
    receipt_configuration = workflow[
        workflow.index("Verify receipt processing configuration") : workflow.index(
            "Verify database credentials are isolated by service"
        )
    ]
    assert 'provider="$(jq -r \'.RECEIPT_PARSER_PROVIDER // ""\'' in receipt_configuration
    assert 'model="$(jq -r \'.RECEIPT_PARSER_MODEL // ""\'' in receipt_configuration
    assert 'openai_key="$(jq -r \'.OPENAI_API_KEY // ""\'' in receipt_configuration
    assert '[[ "${provider}" != "openai" ]]' in receipt_configuration
    assert '[[ "${model}" != "gpt-5.6-luna" ]]' in receipt_configuration
    assert receipt_configuration.count("verify_receipt_runtime") == 4
    assert 'verify_receipt_runtime "${RAILWAY_OUTBOX_SERVICE_ID}"' in receipt_configuration
    assert 'verify_receipt_runtime "${RAILWAY_GMAIL_RECEIPTS_SERVICE_ID}"' in receipt_configuration
    assert 'verify_receipt_runtime "${RAILWAY_WEB_SERVICE_ID}"' in receipt_configuration
    classification_configuration = workflow[
        workflow.index("Verify autonomous classification finalizer configuration") : workflow.index(
            "Verify receipt processing configuration"
        )
    ]
    assert "RAILWAY_CLASSIFICATION_FINALIZER_SERVICE_ID" in classification_configuration
    assert 'CLASSIFICATION_MODEL // ""' in classification_configuration
    assert 'OPENAI_API_KEY // ""' in classification_configuration
    assert '"${classification_model}" != "gpt-5.6-luna"' in classification_configuration
    assert "REQUESTED_RELEASE_SHA: ${{ inputs.release_sha }}" in workflow
    assert "RELEASE_PHASE: ${{ inputs.release_phase }}" in workflow
    assert "COMPATIBILITY_SHA_INPUT: ${{ inputs.compatibility_sha }}" in workflow
    assert "recovery_backup_id" not in workflow
    assert 'compatibility_sha="${{ inputs.compatibility_sha }}"' not in workflow
    assert 'release_phase="${{ inputs.release_phase }}"' not in workflow
    assert "must be a lowercase Railway UUID" in workflow
    assert "PRODUCTION_BASE_URL must be an HTTPS origin" in workflow
    job_env = workflow[workflow.index("    env:") : workflow.index("    steps:")]
    assert "RAILWAY_TOKEN" not in job_env
    assert "RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}" in workflow


def test_production_release_preflights_topology_hobby_recovery_and_credentials():
    workflow = (ROOT / ".github/workflows/production-release.yml").read_text(encoding="utf-8")

    orchestrator = workflow.index("Materialize the reviewed release orchestrator")
    pre_backup_reconcile = workflow.index("Reconcile reviewed grants before the recovery proof")
    preflight = workflow.index("Verify Railway service topology before any upload")
    migration = workflow.index(
        "Apply migrations and reconcile grants before any Railway deployment"
    )
    first_upload = workflow.index("Deploy outbox worker after migrations succeed")
    assert preflight < migration < first_upload
    assert "railway environment config" in workflow
    assert "railway link" not in workflow
    assert ".services[$id].configFile == $path" in workflow
    assert ".services[$id].source.repo == null" in workflow
    assert ".services[$id].source.image == null" in workflow
    assert ".privateNetworkDisabled != true" in workflow
    assert ".networking.serviceDomains" in workflow
    assert ".networking.customDomains" in workflow
    assert ".networking.tcpProxies" in workflow
    assert ".deploy.cronSchedule" in workflow

    recovery = workflow[workflow.index("Verify Railway PITR defense in depth") : first_upload]
    backup = workflow.index("Create and restore-verify a fresh logical backup")
    pitr_recovery = workflow[workflow.index("Verify Railway PITR defense in depth") : backup]
    assert "railway postgres pitr status" in recovery
    assert ".enabled == true" in recovery
    assert ".bucketWired == true" in recovery
    assert "((.blockers // []) | length == 0)" in recovery
    assert "liveProbeAvailable: (.live.available == true)" in recovery
    assert "archiverHealthyWhenProbed: (.live.archiverHealthy == true)" in recovery
    assert "PITR live probe is informational under project-token" in recovery
    assert "fresh encrypted PostgreSQL 18 restore below is the fail-closed" in recovery
    assert "railway ssh" not in pitr_recovery
    assert "railway postgres pitr backup list" not in workflow
    assert "railway postgres pitr schedule list" not in workflow
    assert ".live.backupSetCount" not in workflow

    artifact = workflow.index("Upload only the authenticated encrypted backup")
    assert orchestrator < pre_backup_reconcile < backup
    assert preflight < backup < artifact < first_upload
    assert "${{ github.sha }}" in workflow
    assert "REVIEWED_ORCHESTRATOR_SHA}:scripts/bootstrap_database_roles.py" in workflow
    assert "REVIEWED_ORCHESTRATOR_SHA}:scripts/railway_deploy_and_wait.sh" in workflow
    assert "EXPENSEOPS_ROLE_UTILITY=" in workflow
    assert "EXPENSEOPS_RAILWAY_DEPLOY_HELPER=" in workflow
    assert workflow.count('python "${EXPENSEOPS_ROLE_UTILITY}" --reconcile-runtime-grants') == 2
    assert workflow.count('bash "${EXPENSEOPS_RAILWAY_DEPLOY_HELPER}"') == 5
    assert "Migration URL does not target the selected production Postgres." in workflow
    assert (
        "EXPENSEOPS_BACKUP_DATABASE_URL: ${{ secrets.EXPENSEOPS_BACKUP_DATABASE_URL }}"
    ) in recovery
    assert (
        "EXPENSEOPS_BACKUP_RECIPIENT_CERT_B64: ${{ vars.EXPENSEOPS_BACKUP_RECIPIENT_CERT_B64 }}"
    ) in recovery
    assert (
        "EXPENSEOPS_BACKUP_RECIPIENT_CERT_SHA256: "
        "${{ vars.EXPENSEOPS_BACKUP_RECIPIENT_CERT_SHA256 }}"
    ) in recovery
    assert 'username != "expenseops_backup"' in recovery
    assert "EXPENSEOPS_SELECTED_POSTGRES_PUBLIC_TARGET_SHA256" in recovery
    assert "source_target_fingerprint != expected_target_sha256" in recovery
    resolve_target = workflow[
        workflow.index(
            "Resolve the selected production Postgres target without database secrets"
        ) : backup
    ]
    assert "RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}" in resolve_target
    assert "EXPENSEOPS_BACKUP_DATABASE_URL" not in resolve_target
    backup_step = workflow[
        backup : workflow.index("Upload only the authenticated encrypted backup")
    ]
    assert "RAILWAY_TOKEN" not in backup_step
    assert '"require",' in recovery
    assert '"verify-ca",' in recovery
    assert '"verify-full",' in recovery
    assert "SERIALIZABLE READ ONLY DEFERRABLE" in recovery
    assert "SELECT pg_export_snapshot()" in recovery
    assert '"--format=custom"' in recovery
    assert 'f"--snapshot={snapshot}"' in recovery
    assert 'environment["PGHOST"] = host' in recovery
    assert 'environment["PGPORT"] = str(parsed.port or 5432)' in recovery
    assert 'environment["PGDATABASE"] = database' in recovery
    assert 'environment["PGUSER"] = username' in recovery
    assert 'environment["PGPASSWORD"] = password' in recovery
    assert 'environment["PGSSLMODE"] = parameters["sslmode"]' in recovery
    assert 'environment["PGDATABASE"] = database_url' not in recovery
    assert 'for item in ("--env", variable)' in recovery
    assert 'f"--dbname={source_url}"' not in recovery
    assert "source_rows = row_manifest(source, relations)" in recovery
    assert "APPLICATION_TABLES = frozenset(" in recovery
    assert '"20260813_0023"' in recovery
    assert '"20260815_0029"' in recovery
    assert '"20260817_0031"' in recovery
    assert 'ATTENTION_REVISIONS = frozenset({"20260817_0032"})' in recovery
    assert 'CLASSIFICATION_REVISIONS = frozenset({"20260817_0033"})' in recovery
    assert 'REVIEW_INBOX_REVISIONS = frozenset({"20260818_0034"})' in recovery
    assert 'CURRENT_REVISIONS = frozenset({"20260819_0035"})' in recovery
    assert "- PROACTIVE_ATTENTION_TABLES" in recovery
    assert "tables = APPLICATION_TABLES - REVIEW_INBOX_TABLES" in recovery
    assert "tables = APPLICATION_TABLES - AGENT_REVIEW_SESSION_TABLES" in recovery
    assert "relations != reviewed_relations" in recovery
    assert "source table inventory differs from the reviewed" in recovery
    assert "restored_rows = row_manifest(restored, restored_relations)" in recovery
    assert "restored_rows != source_rows" in recovery
    assert "restored_sequences != source_sequences" in recovery
    assert "sequence inventory, ownership, or configuration differs" in recovery
    assert "verify_owned_sequence_safety(restored)" in recovery
    assert "could collide with restored rows" in recovery
    assert 'f"--dbname={restore_url}"' in recovery
    assert "180000 <= restored.info.server_version < 190000" in recovery
    assert "OpenSSL 3\\.5\\.7" in workflow
    assert "a8c0d28a529ca480f9f36cf5792e2cd21984552a3c8e4aa11a24aa31aeac98e8" in workflow
    assert '"${EXPENSEOPS_OPENSSL_BIN}" cms' in recovery
    assert "-encrypt -binary -outform DER -aes-256-gcm" in recovery
    assert '-recip "${recipient_cert}"' in recovery
    assert "-keyopt rsa_padding_mode:oaep" in recovery
    assert "-keyopt rsa_oaep_md:sha256" in recovery
    assert "-keyopt rsa_mgf1_md:sha256" in recovery
    assert recovery.index('-recip "${recipient_cert}"') < recovery.index(
        "-keyopt rsa_padding_mode:oaep"
    )
    assert "id-smime-ct-authEnvelopedData" in recovery
    assert "rsaesOaep" in recovery
    assert recovery.index('"pg_restore"') < recovery.index('"${EXPENSEOPS_OPENSSL_BIN}" cms')
    assert "The backup recipient value must never contain a private key." in recovery
    assert "at least 3072 bits" in recovery
    assert "certificate fingerprint is not approved" in recovery
    assert "expenseops-production.dump" in recovery
    assert "source-manifest.json" in recovery
    assert "restored-manifest.json" in recovery
    assert "recovery-metadata.json" in recovery
    assert "expenseops-production-${RELEASE_SHA}.tar" in recovery
    assert "expenseops-production-${{ inputs.release_sha }}.tar.cms" in recovery
    assert 'test "$(find "${recovery_dir}" -maxdepth 1 -type f | wc -l)" = "1"' in recovery
    assert '"source_alembic_revision"' in recovery
    assert '"restored_alembic_revision"' in recovery
    assert '("public", "alembic_version") not in source_rows' in recovery
    assert '"logical_backup_between_release_rpo": "unbounded"' in recovery
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in recovery
    assert "retention-days: 90" in recovery
    assert "compression-level: 0" in recovery
    assert "github.run_id" in recovery
    assert "github.run_attempt" in recovery
    assert "BACKUP_CMS_SHA256" in recovery
    assert "artifact-digest" in recovery
    assert "Raw CMS ciphertext SHA-256" in recovery
    assert "GitHub artifact archive SHA-256" in recovery
    assert "BACKUP_ENCRYPTION_PASSPHRASE" not in workflow
    assert "gpg" not in recovery.casefold()
    assert "cms -decrypt" not in recovery

    assert "membership_count" in recovery
    assert "rolinherit" in recovery
    assert "TEMPORARY" in recovery
    assert "unexpected_schema_privilege_count" in recovery
    assert "writable_table_count" in recovery
    assert "writable_sequence_count" in recovery
    assert "executable_function_count" in recovery
    assert "usable_type_count" in recovery
    assert "type_object.typsubscript" in recovery
    assert "'pg_catalog.array_subscript_handler'::regproc" in recovery
    assert "type_object.typtype <> 'm'" in recovery
    assert "owned_object_count" in recovery

    credential_isolation = workflow[
        workflow.index("Verify database credentials are isolated by service") : first_upload
    ]
    assert "EXPENSEOPS_BACKUP_DATABASE_URL" in credential_isolation
    assert "EXPENSEOPS_BACKUP_PASSWORD" in credential_isolation
    assert "EXPENSEOPS_ADMIN_DATABASE_URL" in workflow
    assert "EXPENSEOPS_RUNTIME_PASSWORD" in workflow
    assert "EXPENSEOPS_MIGRATOR_PASSWORD" in workflow
    assert "POSTGRES_PASSWORD" in workflow
    assert "PGPASSWORD" in workflow
    assert "PGUSER POSTGRES_USER DATABASE_USER DB_USER" in workflow
    assert "DATABASE_URL must use PostgreSQL" in workflow
    assert "contains an unexpected PostgreSQL URL" in workflow
    assert "must explicitly use the production application environment" in workflow
    assert "Verify the isolated one-shot migration credential" in workflow
    assert "\n              EXPENSEOPS_MIGRATION_DATABASE_URL \\\n" in credential_isolation
    assert "Migration URL must authenticate only expenseops_migrator" in workflow
    assert "Migration URL does not target the selected production Postgres" in workflow
    assert "Migration role attributes are unsafe" in workflow
    assert "Migration role ownership or database boundary is unsafe" in workflow
    migration_credential = workflow[
        workflow.index("Verify the isolated one-shot migration credential") : workflow.index(
            "Verify a normal release starts from hardened production"
        )
    ]
    assert "RAILWAY_TOKEN" not in migration_credential
    migration_commands = workflow[migration:first_upload]
    assert "set -Eeuo pipefail" in migration_commands
    assert "DATABASE_URL: ${{ secrets.EXPENSEOPS_MIGRATION_DATABASE_URL }}" in migration_commands
    upgrade = migration_commands.index("alembic upgrade head")
    first_head_check = migration_commands.index("alembic current --check-heads")
    reconcile = migration_commands.index('"${EXPENSEOPS_ROLE_UTILITY}" --reconcile-runtime-grants')
    second_head_check = migration_commands.index(
        "alembic current --check-heads", first_head_check + 1
    )
    assert upgrade < first_head_check < reconcile < second_head_check
    assert "railway up" not in migration_commands
    assert "railway_deploy_and_wait.sh" not in migration_commands
    assert 'test "${migration_heads}" = "20260815_0028"' in workflow
    assert 'test "${migration_heads}" = "20260815_0029"' in workflow
    assert "All production services must target the same PostgreSQL database" in workflow
    assert "Application services do not target the selected production Postgres service" in workflow
    assert "candidate_target_fingerprint" in workflow


def test_production_release_has_explicit_cutover_normal_and_rollback_guards():
    workflow = (ROOT / ".github/workflows/production-release.yml").read_text(encoding="utf-8")

    assert 'case "${RELEASE_PHASE}" in' in workflow
    assert "compatibility|rollback)" in workflow
    assert "hardened)" in workflow
    assert "normal)" in workflow
    assert "compatibility_sha already contains the irreversible hardening migration" in workflow
    assert "Verify a normal release starts from hardened production" in workflow
    rollback_start = workflow.index("Verify app-only rollback targets the hardened RLS boundary")
    rollback_end = workflow.index("Verify hardened release follows the compatibility deployment")
    rollback = workflow[rollback_start:rollback_end]
    assert "if: ${{ inputs.release_phase == 'rollback' }}" in rollback
    assert (
        "EXPENSEOPS_MIGRATION_DATABASE_URL: ${{ secrets.EXPENSEOPS_MIGRATION_DATABASE_URL }}"
    ) in rollback
    assert "psycopg.connect(" in rollback
    assert "SELECT version_num FROM public.alembic_version" in rollback
    assert 'revision != ("20260815_0029",)' in rollback
    assert "railway ssh" not in rollback
    assert "RAILWAY_MIGRATION_SERVICE_ID" not in rollback
    assert "if: ${{ inputs.release_phase != 'rollback' }}" in workflow


def test_rollback_does_not_require_or_deploy_day16_finalizer_topology():
    workflow = (ROOT / ".github/workflows/production-release.yml").read_text(encoding="utf-8")

    required_config = workflow[
        workflow.index("Verify release commit and required configuration") : workflow.index(
            "Set up Node.js for the pinned Railway CLI"
        )
    ]
    topology = workflow[
        workflow.index("Verify Railway service topology before any upload") : workflow.index(
            "Verify Railway PITR defense in depth"
        )
    ]
    flags = workflow[
        workflow.index("Verify approved Agent rollout flags") : workflow.index(
            "Verify receipt processing configuration"
        )
    ]
    database_roles = workflow[
        workflow.index("Verify database credentials are isolated by service") : workflow.index(
            "Apply migrations and reconcile grants before any Railway deployment"
        )
    ]
    deploy = workflow[
        workflow.index("Deploy classification finalizer after outbox succeeds") : workflow.index(
            "Deploy Gmail receipts cron after classification finalizer succeeds"
        )
    ]

    for section in (required_config, topology, flags, database_roles):
        assert '[[ "${RELEASE_PHASE}" != "rollback" ]]' in section
        assert "RAILWAY_CLASSIFICATION_FINALIZER_SERVICE_ID" in section
    assert "if: ${{ inputs.release_phase != 'rollback' }}" in deploy
    assert "/railway.classification-finalizer.json" in deploy


def test_hardened_release_parses_latest_successful_railway_deployment():
    workflow = (ROOT / ".github/workflows/production-release.yml").read_text(encoding="utf-8")
    jq_filter = (
        '[.[] | select(.status == "SUCCESS")][0] | (.meta.cliMessage // .meta.commitMessage // "")'
    )
    assert f"'{jq_filter}'" in workflow

    deployments = [
        {"status": "FAILED", "meta": {"cliMessage": "failed"}},
        {
            "status": "SUCCESS",
            "meta": {"cliMessage": "ExpenseOps production web compatibility-sha"},
        },
        {"status": "SUCCESS", "meta": {"commitMessage": "older"}},
    ]
    result = subprocess.run(
        ["jq", "-r", jq_filter],
        input=json.dumps(deployments),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ExpenseOps production web compatibility-sha"


def test_inline_hobby_recovery_program_is_valid_python():
    program = _inline_hobby_recovery_program()

    compile(program, ".github/workflows/production-release.yml:hobby-recovery", "exec")


def test_hobby_recovery_inventory_matches_the_bootstrap_allowlist():
    tree = ast.parse(_inline_hobby_recovery_program())
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "APPLICATION_TABLES"
            for target in node.targets
        )
    )
    assert isinstance(assignment.value, ast.Call)
    assert len(assignment.value.args) == 1
    embedded_tables = set(ast.literal_eval(assignment.value.args[0]))

    assert embedded_tables == set(APPLICATION_TABLES)


def test_hobby_recovery_inventory_is_revision_aware_through_review_inbox():
    tree = ast.parse(_inline_hobby_recovery_program())
    required_assignments = {
        "APPLICATION_TABLES",
        "AGENT_TABLES",
        "PROACTIVE_ATTENTION_TABLES",
        "CLASSIFICATION_TABLES",
        "REVIEW_INBOX_TABLES",
        "AGENT_REVIEW_SESSION_TABLES",
        "PRE_AGENT_REVISIONS",
        "AGENT_REVISIONS",
        "ATTENTION_REVISIONS",
        "CLASSIFICATION_REVISIONS",
        "REVIEW_INBOX_REVISIONS",
        "CURRENT_REVISIONS",
    }
    selected_nodes: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in required_assignments
            for target in node.targets
        ):
            selected_nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "expected_relations":
            selected_nodes.append(node)

    namespace: dict[str, object] = {"RecoveryGateError": RuntimeError}
    exec(compile(ast.Module(body=selected_nodes, type_ignores=[]), "<recovery>", "exec"), namespace)
    expected_relations = namespace["expected_relations"]
    assert callable(expected_relations)

    attention_tables = {
        ("public", "proactive_attention_deliveries"),
        ("public", "proactive_attention_preferences"),
    }
    revision_0030 = set(expected_relations("20260817_0030"))
    revision_0031 = set(expected_relations("20260817_0031"))
    revision_0032 = set(expected_relations("20260817_0032"))
    revision_0033 = set(expected_relations("20260817_0033"))
    revision_0034 = set(expected_relations("20260818_0034"))
    revision_0035 = set(expected_relations("20260819_0035"))
    classification_tables = {
        ("public", "classification_concept_aliases"),
        ("public", "classification_concepts"),
        ("public", "classification_decisions"),
        ("public", "classification_settings"),
        ("public", "classification_subcategories"),
    }
    review_inbox_tables = {("public", "review_items")}
    agent_review_session_tables = {("public", "agent_review_sessions")}

    assert revision_0030 == revision_0031
    assert revision_0030.isdisjoint(attention_tables)
    assert revision_0030.isdisjoint(classification_tables)
    assert revision_0030.isdisjoint(review_inbox_tables)
    assert attention_tables <= revision_0032
    assert revision_0032 - revision_0031 == attention_tables
    assert revision_0032.isdisjoint(classification_tables)
    assert revision_0032.isdisjoint(review_inbox_tables)
    assert revision_0033 - revision_0032 == classification_tables
    assert revision_0033.isdisjoint(review_inbox_tables)
    assert revision_0034 - revision_0033 == review_inbox_tables
    assert revision_0034.isdisjoint(agent_review_session_tables)
    assert revision_0035 - revision_0034 == agent_review_session_tables


def test_railway_waiter_requires_success_and_fails_closed():
    waiter = (ROOT / "scripts/railway_deploy_and_wait.sh").read_text(encoding="utf-8")

    assert "--detach" in waiter
    assert "deployment list" in waiter
    assert "SUCCESS)" in waiter
    assert "FAILED|CRASHED)" in waiter
    assert "expected_config_file" in waiter
    assert ".meta.cliMessage // .meta.commitMessage" in waiter
    assert "environment config" in waiter
    assert '"${railway_bin}" link' not in waiter
    assert ".services[$id].configFile" in waiter
    assert ".services[$id].source.repo == null" in waiter
    assert ".services[$id].source.image == null" in waiter
    assert ".services[$id].networking.serviceDomains" in waiter
    assert ".services[$id].deploy.cronSchedule" in waiter
    assert ".meta.serviceManifest.deploy.startCommand" in waiter
    assert "reviewed service command manifest" in waiter
    assert "exit 1" in waiter


@pytest.mark.parametrize(
    (
        "terminal_status",
        "manifest_matches",
        "expected_returncode",
        "service_id",
        "config_file",
        "component",
    ),
    [
        (
            "SUCCESS",
            True,
            0,
            "migration-service",
            "/railway.migrations.json",
            "migrations",
        ),
        (
            "SUCCESS",
            False,
            1,
            "migration-service",
            "/railway.migrations.json",
            "migrations",
        ),
        (
            "FAILED",
            True,
            1,
            "migration-service",
            "/railway.migrations.json",
            "migrations",
        ),
        (
            "CRASHED",
            True,
            1,
            "migration-service",
            "/railway.migrations.json",
            "migrations",
        ),
        (
            "SUCCESS",
            True,
            0,
            "classification-finalizer-service",
            "/railway.classification-finalizer.json",
            "classification-finalizer",
        ),
    ],
)
def test_railway_waiter_blocks_non_successful_terminal_states(
    tmp_path,
    terminal_status,
    manifest_matches,
    expected_returncode,
    service_id,
    config_file,
    component,
):
    fake_railway = tmp_path / "railway"
    state_file = tmp_path / "deployment-created"
    fake_railway.write_text(
        """#!/usr/bin/env bash
set -eu
if [[ "${1:-}" == "link" ]]; then
  exit 0
fi
if [[ "${1:-} ${2:-}" == "environment config" ]]; then
  payload_format='{"services":{"%s":{"configFile":"%s"}}}'
  printf "${payload_format}\\n" "${FAKE_SERVICE_ID}" "${FAKE_PREFLIGHT_CONFIG_FILE}"
  exit 0
fi
if [[ "${1:-} ${2:-}" == "deployment list" ]]; then
  if [[ -f "${FAKE_RAILWAY_STATE}" ]]; then
    printf '%s\\n' "${FAKE_DEPLOYMENT_JSON}"
  else
    printf '[]\\n'
  fi
  exit 0
fi
if [[ "${1:-}" == "up" ]]; then
  touch "${FAKE_RAILWAY_STATE}"
  exit 0
fi
exit 64
""",
        encoding="utf-8",
    )
    fake_railway.chmod(0o755)
    release_sha = "a" * 40
    deploy_manifest = _json_file(config_file.removeprefix("/"))["deploy"]
    if not manifest_matches:
        deploy_manifest = {**deploy_manifest, "startCommand": "sh -c 'exec false'"}
    deployment_payload = [
        {
            "id": "new-deployment",
            "status": terminal_status,
            "meta": {
                "cliMessage": f"ExpenseOps production {component} {release_sha}",
                "configFile": config_file,
                "serviceManifest": {"deploy": deploy_manifest},
            },
        }
    ]
    env = os.environ.copy()
    env.update(
        {
            "FAKE_PREFLIGHT_CONFIG_FILE": config_file,
            "FAKE_SERVICE_ID": service_id,
            "FAKE_DEPLOYMENT_JSON": json.dumps(deployment_payload),
            "FAKE_RAILWAY_STATE": str(state_file),
            "RAILWAY_BIN": str(fake_railway),
            "RAILWAY_DEPLOY_POLL_SECONDS": "0",
            "RAILWAY_DEPLOY_TIMEOUT_SECONDS": "5",
            "RAILWAY_PRODUCTION_ENVIRONMENT_ID": "production-environment",
            "RAILWAY_PROJECT_ID": "project",
        }
    )

    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/railway_deploy_and_wait.sh"),
            service_id,
            config_file,
            release_sha,
            component,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == expected_returncode
    if expected_returncode == 0:
        assert "reached SUCCESS" in result.stdout
    elif terminal_status == "SUCCESS":
        assert "reviewed service command manifest" in result.stderr
    else:
        assert terminal_status in result.stderr


def test_railway_waiter_rejects_config_mismatch_before_upload(tmp_path):
    fake_railway = tmp_path / "railway"
    upload_marker = tmp_path / "upload-called"
    fake_railway.write_text(
        """#!/usr/bin/env bash
set -eu
if [[ "${1:-}" == "link" ]]; then
  exit 0
fi
if [[ "${1:-} ${2:-}" == "environment config" ]]; then
  printf '{"services":{"migration-service":{"configFile":"/railway.web.json"}}}\\n'
  exit 0
fi
if [[ "${1:-}" == "up" ]]; then
  touch "${FAKE_UPLOAD_MARKER}"
  exit 0
fi
if [[ "${1:-} ${2:-}" == "deployment list" ]]; then
  printf '[]\\n'
  exit 0
fi
exit 64
""",
        encoding="utf-8",
    )
    fake_railway.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "FAKE_UPLOAD_MARKER": str(upload_marker),
            "RAILWAY_BIN": str(fake_railway),
            "RAILWAY_PRODUCTION_ENVIRONMENT_ID": "production-environment",
            "RAILWAY_PROJECT_ID": "project",
        }
    )

    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/railway_deploy_and_wait.sh"),
            "migration-service",
            "/railway.migrations.json",
            "a" * 40,
            "migrations",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "expected /railway.migrations.json" in result.stderr
    assert not upload_marker.exists()

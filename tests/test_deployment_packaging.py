import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _json_file(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


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
    assert "actions/setup-python@v5" in workflow
    assert 'python-version: "3.11.13"' in workflow
    assert "ref: ${{ inputs.release_sha }}" in workflow
    assert "git merge-base --is-ancestor" in workflow
    assert "RAILWAY_MIGRATION_SERVICE_ID" in workflow
    assert "RAILWAY_OUTBOX_SERVICE_ID" in workflow
    assert "RAILWAY_GMAIL_RECEIPTS_SERVICE_ID" in workflow
    assert "RAILWAY_GMAIL_PROMOTIONS_SERVICE_ID" in workflow
    assert "RAILWAY_WEB_SERVICE_ID" in workflow
    assert "Each release component must use a distinct Railway service ID." in workflow
    migration_step = workflow.index("Deploy migration job and wait for terminal success")
    outbox_step = workflow.index("Deploy outbox worker after migrations succeed")
    receipts_step = workflow.index("Deploy Gmail receipts cron after outbox succeeds")
    promotions_step = workflow.index("Deploy Gmail promotions cron after receipts succeeds")
    web_step = workflow.index("Deploy web only after every runtime succeeds")
    assert migration_step < outbox_step < receipts_step < promotions_step < web_step
    assert workflow.count('"${RELEASE_SHA}"') == 5
    assert "continue-on-error" not in workflow
    assert "/railway.migrations.json" in workflow
    assert "/railway.outbox.json" in workflow
    assert "/railway.gmail-receipts.json" in workflow
    assert "/railway.gmail-promotions.json" in workflow
    assert "/railway.web.json" in workflow
    assert "AGENT_WRITE_ACTIONS_ENABLED" in workflow
    assert "AGENT_PROACTIVE_ENABLED" in workflow
    assert "AGENT_PURCHASING_ENABLED" in workflow
    assert "REQUESTED_RELEASE_SHA: ${{ inputs.release_sha }}" in workflow
    assert "RELEASE_PHASE: ${{ inputs.release_phase }}" in workflow
    assert "COMPATIBILITY_SHA_INPUT: ${{ inputs.compatibility_sha }}" in workflow
    assert "RECOVERY_BACKUP_ID: ${{ inputs.recovery_backup_id }}" in workflow
    assert 'compatibility_sha="${{ inputs.compatibility_sha }}"' not in workflow
    assert 'release_phase="${{ inputs.release_phase }}"' not in workflow
    assert "must be a lowercase Railway UUID" in workflow
    assert "PRODUCTION_BASE_URL must be an HTTPS origin" in workflow


def test_production_release_preflights_topology_recovery_and_credentials():
    workflow = (ROOT / ".github/workflows/production-release.yml").read_text(encoding="utf-8")

    preflight = workflow.index("Verify Railway service topology before any upload")
    first_upload = workflow.index("Deploy migration job and wait for terminal success")
    assert preflight < first_upload
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

    assert "railway postgres pitr status" in workflow
    assert ".live.available == true" in workflow
    assert ".live.archiverHealthy == true" in workflow
    assert ".live.backupSetCount > 0" in workflow
    assert "railway postgres pitr backup list" in workflow
    assert ".externalId" in workflow
    assert ".expiresAt == null" in workflow
    assert ".scheduleId == null" in workflow
    assert 'sub("\\\\.[0-9]+Z$"; "Z")' in workflow
    assert "<= 86400" in workflow
    assert "railway postgres pitr schedule list" in workflow
    assert '([.[].kind] | sort) == ["DAILY", "MONTHLY", "WEEKLY"]' in workflow
    assert '["DAILY", "MONTHLY", "WEEKLY"]' in workflow

    assert "EXPENSEOPS_ADMIN_DATABASE_URL" in workflow
    assert "EXPENSEOPS_RUNTIME_PASSWORD" in workflow
    assert "EXPENSEOPS_MIGRATOR_PASSWORD" in workflow
    assert "POSTGRES_PASSWORD" in workflow
    assert "PGPASSWORD" in workflow
    assert "PGUSER POSTGRES_USER DATABASE_USER DB_USER" in workflow
    assert "DATABASE_URL must use PostgreSQL" in workflow
    assert "contains an unexpected PostgreSQL URL" in workflow
    assert "must explicitly use the production application environment" in workflow
    assert "The migration service must not define EXPENSEOPS_PROCESS" in workflow
    assert "The migration service must not contain application encryption keys" in workflow
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
    assert "Verify app-only rollback targets the hardened RLS boundary" in workflow
    assert '--service "${RAILWAY_MIGRATION_SERVICE_ID}"' in workflow
    assert 'revision == "20260815_0029"' in workflow
    assert "if: ${{ inputs.release_phase != 'rollback' }}" in workflow


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is required by the release runner")
@pytest.mark.parametrize(
    ("age", "expires_at", "schedule_id", "expected_returncode"),
    [
        (timedelta(minutes=5), None, None, 0),
        (timedelta(hours=25), None, None, 1),
        (timedelta(minutes=5), "2099-01-01T00:00:00.000Z", None, 1),
        (timedelta(minutes=5), None, "daily-schedule", 1),
    ],
)
def test_release_backup_filter_accepts_only_fresh_locked_on_demand_backup(
    age, expires_at, schedule_id, expected_returncode
):
    workflow = (ROOT / ".github/workflows/production-release.yml").read_text(encoding="utf-8")
    filter_start = workflow.index("'any(.[];") + 1
    filter_end = workflow.index(")' \\", filter_start) + 1
    backup_filter = workflow[filter_start:filter_end]
    created_at = (datetime.now(UTC) - age).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    payload = [
        {
            "id": "approved-backup",
            "externalId": "provider-backup",
            "createdAt": created_at,
            "expiresAt": expires_at,
            "scheduleId": schedule_id,
        }
    ]

    result = subprocess.run(
        ["jq", "--exit-status", "--arg", "backup_id", "approved-backup", backup_filter],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )

    assert (result.returncode == 0) == (expected_returncode == 0), result.stderr


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
    ("terminal_status", "manifest_matches", "expected_returncode"),
    [
        ("SUCCESS", True, 0),
        ("SUCCESS", False, 1),
        ("FAILED", True, 1),
        ("CRASHED", True, 1),
    ],
)
def test_railway_waiter_blocks_non_successful_terminal_states(
    tmp_path, terminal_status, manifest_matches, expected_returncode
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
  payload_format='{"services":{"migration-service":{"configFile":"%s"}}}'
  printf "${payload_format}\\n" "${FAKE_PREFLIGHT_CONFIG_FILE}"
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
    deploy_manifest = _json_file("railway.migrations.json")["deploy"]
    if not manifest_matches:
        deploy_manifest = {**deploy_manifest, "startCommand": "sh -c 'exec false'"}
    deployment_payload = [
        {
            "id": "new-deployment",
            "status": terminal_status,
            "meta": {
                "cliMessage": f"ExpenseOps production migrations {release_sha}",
                "configFile": "/railway.migrations.json",
                "serviceManifest": {"deploy": deploy_manifest},
            },
        }
    ]
    env = os.environ.copy()
    env.update(
        {
            "FAKE_PREFLIGHT_CONFIG_FILE": "/railway.migrations.json",
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
            "migration-service",
            "/railway.migrations.json",
            release_sha,
            "migrations",
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

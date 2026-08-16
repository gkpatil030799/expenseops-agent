# ExpenseOps Production Operations Runbook

**Owner:** ExpenseOps production operator
**Recovery objectives:** primary RPO **5 minutes** with healthy PITR; fallback RPO **24 hours**
with daily snapshots; RTO **4 hours**.
**Status:** procedures are defined; the launch gate remains open until a timed restore drill and
alert delivery test are recorded against the release revision.

## Release procedure

This RLS cutover is an expand/contract release with two reviewed commits. The
**compatibility commit** contains migration `20260815_0028` and application code
that no longer depends on the legacy global tenant bypass. Its web healthcheck
is temporarily `/health`; `/readiness` must remain 503 because the old policy is
still unsafe. The descendant **hardening commit** adds migration
`20260815_0029`, removes the bypass from every tenant policy, and changes the
web healthcheck to `/readiness`. Both commits belong to the same reviewed PR.
After `0029`, the compatibility commit—not the pre-cutover application—is the
oldest permitted application rollback target. Never downgrade `0029`.

1. Release only clean, reviewed commits on `main` after
   `.github/workflows/release-gate.yml` passes. Record both full SHAs. The merge
   must preserve the standalone compatibility and hardening commits; do not
   squash this cutover PR. Use a merge commit, or another non-squashing method
   that preserves both reviewed SHAs on `main`.
2. Enable PITR, wait for a healthy base backup/WAL recovery range, configure
   daily, weekly, and monthly snapshots, and complete a restore drill into a
   sibling database. Lock and record a second pre-release backup. Do not change
   roles, variables, schema, or deployments without that evidence.
3. Run the guarded role bootstrap from a trusted operator environment using
   the Railway PostgreSQL superuser only for that transaction. Create
   `expenseops_runtime` as `NOSUPERUSER NOBYPASSRLS` and
   `expenseops_migrator` as `NOSUPERUSER BYPASSRLS`; both must be `NOINHERIT`,
   `NOCREATEDB`, `NOCREATEROLE`, and `NOREPLICATION`. Follow
   [PRODUCTION_DATABASE_ROLES.md](PRODUCTION_DATABASE_ROLES.md). The database
   stays owned by Railway `postgres`; both application roles receive database
   `CONNECT` but not `CREATE` or `TEMPORARY`, and only the migrator owns
   `public` and reviewed application objects. Then remove all bootstrap secrets
   from the operator environment.
4. Create a private `expenseops-migrations` Railway service with only the
   migrator database URL and no application encryption key. Put only the
   runtime URL on web, outbox, and both Gmail cron services. Every URL must
   identify the same production Postgres host/database while using its assigned
   role. Stage variable changes without deploying and verify usernames and the
   secret-free database target fingerprint against the selected production
   Postgres service. The Railway-provided `postgres` URL must not remain on a
   deployed service.
5. Assign the explicit config paths: web `/railway.web.json`, migrations
   `/railway.migrations.json`, outbox `/railway.outbox.json`, Gmail receipts
   `/railway.gmail-receipts.json`, and Gmail promotions
   `/railway.gmail-promotions.json`. The shared `/railway.json` stays neutral.
6. Explicitly set `AGENT_WRITE_ACTIONS_ENABLED`, `AGENT_PROACTIVE_ENABLED`, and
   `AGENT_PURCHASING_ENABLED` to `false` on every runtime service.
7. Manually run **Production release** with `release_phase=compatibility`, the
   compatibility SHA, and the locked backup ID. The protected `production`
   environment must require approval. Never use direct `railway up` or a
   GitHub-push deployment for production.
8. The workflow verifies recovery evidence, credentials, Agent flags, and the
   exact SHA/config, then deploys migrations. Its fail-closed pre-deploy command
   upgrades Alembic, verifies the head, and reconciles exact runtime grants. An
   inert private `sleep infinity` sentinel provides an unambiguous Railway
   `SUCCESS`; it has no public domain or listener.
9. Only after migration success, the workflow deploys outbox, Gmail receipts,
   and Gmail promotions from the same checkout, requiring `SUCCESS` from each.
   Web deploys last. A failure leaves the old web revision active.
10. The compatibility web gates on `/health`. Verify `/health` is 200 and
    `/readiness` is truthfully 503; record all deployment IDs.
11. After compatibility verification, create and lock a fresh on-demand backup.
    Run **Production release** again with `release_phase=hardened`, the hardening
    SHA, `compatibility_sha` set to the deployed compatibility SHA, and that new
    approved backup ID. The workflow requires that compatibility web revision
    to be the latest success, applies `0029`, repeats the ordered runtimes, and
    deploys the `/readiness`-gated web last.
12. Verify `/health` and `/readiness` are 200, then verify sign-in, one
    transaction review, webhook routing, and outbox delivery. Record all IDs.
13. Keep the private migration sentinel active and non-public. The controlled
    app-only rollback gate uses it for a read-only revision check; do not attach
    a domain or application traffic to it.

The service config paths are part of the release boundary:

| Railway service | Config path | Database credential | Process |
| --- | --- | --- | --- |
| `expenseops` web | `/railway.web.json` | Restricted runtime role | Uvicorn only; never Alembic |
| `expenseops-migrations` | `/railway.migrations.json` | `expenseops_migrator` only | Alembic/head/grant verification, then inert sentinel |
| Outbox worker | `/railway.outbox.json` | Restricted runtime role | `python -m app.jobs.outbox` |
| Gmail receipts cron | `/railway.gmail-receipts.json` | Restricted runtime role | `python -m app.jobs.gmail_receipts --max-results 25` |
| Gmail promotions cron | `/railway.gmail-promotions.json` | Restricted runtime role | `python -m app.jobs.promotions sync` |

Set each absolute path in the service's Railway **Settings → Config as Code** field; the CLI upload
does not choose a custom config file. See Railway's
[custom config file instructions](https://docs.railway.com/config-as-code#using-a-custom-config-as-code-file).

The two cron config files intentionally do not set a schedule. Keep each existing schedule in its
Railway service settings, record it before assigning the custom config path, and verify it is
unchanged afterward. Do not convert a cron into an always-running service during this change.

Keep automatic GitHub deployments disabled for every production service. Railway does not order
independent GitHub-push deployments across services. The manual workflow is the release sequencer:
a failed migration, worker, or cron deployment cannot activate the new web revision.

Web, outbox, and crons use `expenseops_runtime`: database `CONNECT`, schema
`USAGE`, application-table DML, application-sequence `USAGE`/`SELECT`, and the
five exact routing-function executions. It has no superuser, `BYPASSRLS`,
database/schema ownership or creation, temporary-table, role membership, type,
Alembic-write, or excess table/sequence/function rights. Migrations use
`expenseops_migrator`: database `CONNECT`, ownership plus `USAGE`/`CREATE` in
`public`, and ownership of reviewed application objects. It is not the database
owner or a superuser and cannot create roles/databases, create temporary tables,
or participate in another role membership; it has `BYPASSRLS` only for
controlled migrations. `PUBLIC` has no database connect/temp/create, schema,
reviewed application-object, function, or type grant. `/readiness` verifies
runtime role attributes, ownership/grants, exact RLS/FORCE policies, and the
five narrow SECURITY DEFINER routers. The Railway `postgres` superuser remains
operator-only bootstrap/recovery access and is never stored on a deployment.

The GitHub `production` environment needs one secret, `RAILWAY_TOKEN`, and these non-secret
variables: `RAILWAY_PROJECT_ID`, `RAILWAY_PRODUCTION_ENVIRONMENT_ID`,
`RAILWAY_MIGRATION_SERVICE_ID`, `RAILWAY_OUTBOX_SERVICE_ID`,
`RAILWAY_GMAIL_RECEIPTS_SERVICE_ID`, `RAILWAY_GMAIL_PROMOTIONS_SERVICE_ID`,
`RAILWAY_WEB_SERVICE_ID`, `RAILWAY_POSTGRES_SERVICE_ID`, and
`PRODUCTION_BASE_URL`. Require a reviewer on that environment. The
project token authorizes deployments but is not a database credential; database URLs remain
scoped to their Railway services.

### Migration failure

If the migration deployment is `FAILED`, `CRASHED`, times out, or uses the wrong config file, the
workflow stops and does not deploy web. Leave the current web revision serving, inspect the bounded
migration logs, correct the cause through a reviewed change or safe configuration repair, and rerun
the same reviewed SHA only when appropriate. Alembic migrations are retryable after a rolled-back
failure. Do not stamp the database, edit `alembic_version`, recreate the database, or manually
rewrite customer/domain rows to force progress.

The migration deployment contains separate Alembic and grant-reconciliation
transactions. If Alembic fails inside its transaction, that transaction rolls
back. If Alembic reaches the requested head but the later head check or runtime
grant reconciliation fails, the new schema may already be committed even
though the Railway migration deployment is failed and no new web revision is
activated. Inspect `alembic current` and the migration logs, correct the
reviewed migration/allowlist/ACL cause, and rerun the same release barrier. Do
not assume every failed migration deployment left the database at its prior
revision.

If a runtime deployment fails after one or more earlier runtime services reached `SUCCESS`, web
still remains on the previous revision. Restore any already-updated runtime service to the previous
reviewed SHA through the same controlled workflow/helper before attempting a different release, or
repair the failure and resume the same reviewed SHA. Do not bypass the failed service and deploy web
manually.

Before `0029`, do **not** use `release_phase=rollback`: its revision gate is
deliberately exact to `0029`. A compatibility-phase failure before web leaves
the existing web deployment active. If the compatibility web is already active
at `0028`, keep `0028`, repair or forward-fix the compatibility application, and
rerun that reviewed phase; do not reintroduce the pre-cutover global-bypass
application and do not advance to `0029` until compatibility is stable.

After `0029`, use `release_phase=rollback` with a fresh locked backup ID and the
reviewed compatibility SHA. This app-only path first SSHes to the private
hardened migration sentinel to prove the database is at exactly `0029`; it
never runs Alembic, then deploys compatibility runtimes and web last.
`/readiness` will be 503 because that revision expects the older head, so keep
the incident open and forward-fix with a descendant that retains `0029`. The
gate intentionally refuses this rollback if the database has advanced beyond
`0029`; use only a separately reviewed schema-compatible hardened recovery
revision in that case. Never run the compatibility migration phase against an
`0029` database, deploy the pre-cutover application, or downgrade `0029`.

For data damage, freeze releases and stop every writer, including web, outbox,
and cron executions. Restore the locked backup or selected PITR timestamp into
a Railway-created sibling Postgres service; never restore over or reset the
source. Verify the sibling's revision, role attributes, RLS/FORCE policies, row
counts, and representative financial/provider records with a non-writing
temporary application. Build sibling runtime and migrator URLs without exposing
the owner credential, stage all affected service variables together, and cut
over only under a reviewed recovery plan. Retain the original database without
writes until reconciliation is complete. Never manually rewrite
customer/domain records to force recovery.

## Backup and point-in-time recovery

Railway now supports scheduled volume backups and PostgreSQL point-in-time recovery. In the
Postgres service **Backups** tab:

1. Enable PITR and wait for the first base backup plus healthy WAL archiving.
2. Also configure daily, weekly, and monthly volume snapshots.
3. Lock the pre-release backup for every high-risk migration.
4. Record the oldest and newest recoverable timestamps in the release ticket.

PITR documentation: <https://docs.railway.com/volumes/point-in-time-recovery>
Scheduled backup documentation: <https://docs.railway.com/volumes/backups>

### Quarterly restore drill

1. Record drill start time and a target timestamp at least 30 minutes old.
2. Restore PITR into the Railway-created sibling Postgres service; do not modify the source.
3. Point a temporary ExpenseOps service at the restored database.
4. Run `alembic current`, `alembic check`, `/readiness`, tenant isolation checks, row counts for all
   financial tables, and sample transaction/Splitwise operation reconciliation.
5. Confirm secrets remain decryptable and no worker writes to the restored environment.
6. Record elapsed time, achieved RPO, achieved RTO, row-count evidence, and approver.
7. Delete the temporary service only after evidence is retained.

## Encryption-key rotation

1. Generate a new Fernet key and choose a new short version such as `v2`.
2. Move the current `version:key` pair into `APP_SECRET_KEY_PREVIOUS`.
3. Set the new `APP_SECRET_KEY` and `APP_SECRET_KEY_VERSION` securely in Railway.
4. Deploy and confirm old provider connections still work.
5. Run `python -m app.jobs.rotate_encryption_keys` once in a trusted worker context.
6. Verify the rotation succeeds and reconnect one provider in a test workspace.
7. Remove the previous key only after the database backup retention window and a successful restore
   drill both contain the rotated ciphertext.

## Retention

Run `python -m app.jobs.data_retention` daily. It exits non-zero on any failure so Railway reports a
failed cron run. The configured maximums are exposed in Settings → Privacy and account.

Account deletion immediately revokes sessions and provider credentials, removes imported content
from workspaces only that user could access, and anonymizes the identity. Shared records remain
with their workspace; shared-workspace owners must transfer ownership first. Minimized
financial/audit history is retained for integrity and security obligations.

## Alerts and response

Monitor Railway CPU, memory, HTTP status/latency, and Postgres resource metrics. Poll the protected
`/api/admin/operations` endpoint for application-level health.

| Signal | Warning | Critical | Response |
| --- | ---: | ---: | --- |
| Oldest pending outbox event | 5 minutes | 15 minutes | Inspect worker deployment and provider errors |
| Dead-letter events | 1 | 5 | Stop financial automation and reconcile affected events |
| Ambiguous/failed financial operations | 1 | 3 | Review before allowing retries or new posts |
| Gmail checkpoint freshness | 2 hours | 8 hours | Inspect cron status, OAuth expiry, and Gmail limits |
| HTTP 5xx rate | 1% for 5 min | 5% for 5 min | Correlate Railway HTTP logs by request ID |
| p95 request latency | 1.5 seconds | 3 seconds | Inspect DB pool, slow paths, and provider timeouts |
| Database pool usage | 80% | 95% | Find leaked/slow sessions before scaling pool size |

Every alert channel must be tested before launch. A dashboard without a delivered alert is not
operational evidence.

## Security incident basics

- Rotate a compromised provider credential at the provider first, then in Railway.
- Never paste secret values into logs, tickets, chat, or screenshots.
- Use request/correlation IDs to investigate; customer-facing errors intentionally hide provider
  payloads and stack details.
- For suspected tenant leakage, disable the web and workers, preserve logs, and do not run repair
  scripts until the affected workspace boundaries are understood.

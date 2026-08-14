# ExpenseOps Production Operations Runbook

**Owner:** ExpenseOps production operator
**Recovery objectives:** primary RPO **5 minutes** with healthy PITR; fallback RPO **24 hours**
with daily snapshots; RTO **4 hours**.
**Status:** procedures are defined; the launch gate remains open until a timed restore drill and
alert delivery test are recorded against the release revision.

## Release procedure

1. Release only a clean, reviewed commit that passed `.github/workflows/release-gate.yml`.
2. Run `alembic upgrade head` once through a dedicated migration job using the database owner
   credential. Do not store that credential on the web, worker, or cron services. A non-zero
   migration exit blocks the application release.
3. Keep migrations out of every Docker start command. Web replicas, workers, and cron services
   must never race one another to migrate the same database.
4. Use `/readiness` as the web healthcheck. Production returns HTTP 503 when the database,
   migration head, OIDC, RLS, shared rate limiter, host allowlist, or HTTPS policy is unsafe.
5. Deploy one backward-compatible schema expansion before code that requires it. Contract/remove
   old columns only in a later release after rollback is no longer needed.
6. Verify the deployment reaches `SUCCESS`, then verify `/health`, `/readiness`, a sign-in, one
   transaction review, and the outbox worker.

The web, outbox worker, and cron services must use a dedicated PostgreSQL login that is neither a
superuser nor granted `BYPASSRLS`. Grant it only connect, schema usage, table DML, and sequence use.
`/readiness` verifies every tenant table has both RLS and FORCE RLS and rejects a privileged runtime
role. Keep the owner credential only in the migration job's secret scope.

Rollback: redeploy the previous application revision while leaving additive migrations in place.
Never downgrade a destructive migration during an incident. Restore the database only for actual
data damage, not ordinary application rollback.

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

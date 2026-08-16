# ExpenseOps Production Operations Runbook

**Owner:** ExpenseOps production operator
**Recovery objectives:** the proven logical-backup RPO is the latest successful
release artifact; there is no bounded logical RPO between approved production
releases. Ciphertext release artifacts are retained for **90 days**. Railway
PITR is enabled as defense in depth, but its Railway SSH-based live probe is
not available to the project-scoped release token and it is not credited as a
proven restore path on the current Hobby setup. Target RTO is **4 hours**.
**Status:** every release must pass the isolated PostgreSQL 18 logical restore
gate. The launch gate remains open until its artifact evidence and the alert
delivery test are recorded against the release revision.

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

The workflow owns its release orchestration separately from the selected
application SHA. It materializes the reviewed role-reconciliation and Railway
deployment helpers from the protected workflow commit, so a compatibility or
rollback application revision cannot reintroduce stale infrastructure logic.
Before creating the logical backup it idempotently reconciles only the reviewed
database ACLs with `expenseops_migrator`; this may repair missing backup/runtime
grants but does not change schema or customer/domain rows. The fresh encrypted
dump and isolated PostgreSQL 18 restore must then succeed before Alembic or any
Railway upload can run.

1. Release only clean, reviewed commits on `main` after
   `.github/workflows/release-gate.yml` passes. Record both full SHAs. The merge
   must preserve the standalone compatibility and hardening commits; do not
   squash this cutover PR. Use a merge commit, or another non-squashing method
   that preserves both reviewed SHAs on `main`.
2. Enable Railway PITR and require it to remain enabled, bucket-wired, and free
   of configuration blockers. Treat its project-token live-probe fields as
   informational because Railway obtains them through an SSH capability the
   release token does not have. PITR is a secondary layer, not the release's
   restore proof. On the current Railway Hobby setup, managed volume-backup
   schedules and a safe sibling-service PITR restore are unavailable; do not
   claim live coverage, a daily snapshot schedule, or a completed PITR drill.
3. Before the one-time role bootstrap mutates privileges, create an
   authenticated encrypted logical backup with the Railway PostgreSQL operator
   credential, retain no plaintext copy, and prove offline decryption plus an
   isolated PostgreSQL restore. This bootstrap-only safety artifact does not
   replace the restricted-role release backups. The repository does not
   automate this one-time admin-credential path: its recorded ciphertext hash,
   successful authenticated-decrypt exit, PostgreSQL 18 restore, row evidence,
   and operator approval are a required manual evidence blocker.
4. From a trusted operator environment, export only
   `EXPENSEOPS_ADMIN_DATABASE_URL` and a distinct generated
   `EXPENSEOPS_BACKUP_PASSWORD`, then run
   `scripts/bootstrap_database_roles.py --bootstrap-backup-role`. This guarded
   transaction creates or normalizes only `expenseops_backup` and its exact
   current read surface; it does not create the runtime/migrator roles,
   transfer ownership, or change default ACLs. It also removes inherited
   `PUBLIC` create/temporary/function execution privileges that cannot be
   denied to one login independently. Follow
   [PRODUCTION_DATABASE_ROLES.md](PRODUCTION_DATABASE_ROLES.md).
5. Construct the public TLS backup URL without logging it. Using
   `expenseops_backup`, create a fresh custom-format dump and source manifest
   from the same exported `SERIALIZABLE READ ONLY DEFERRABLE` snapshot. Restore
   the dump into an isolated PostgreSQL 18 instance, require exact per-table
   row counts plus sequence inventory/ownership/configuration, and check each
   restored owned sequence's effective next value for a collision. PostgreSQL
   sequence values are not MVCC snapshot data, so do not claim exact source
   `last_value` equality unless writers were quiesced. Stop before the full role
   cutover unless this succeeds.
6. Run the full guarded role bootstrap from the trusted operator environment
   using the Railway PostgreSQL superuser only for that transaction. Create
   `expenseops_runtime` as `NOSUPERUSER NOBYPASSRLS` and
   `expenseops_migrator` as `NOSUPERUSER BYPASSRLS`; both must be `NOINHERIT`,
   `NOCREATEDB`, `NOCREATEROLE`, and `NOREPLICATION`. The database stays owned
   by Railway `postgres`; both application roles receive database `CONNECT`
   but not `CREATE` or `TEMPORARY`, and only the migrator owns `public` and
   reviewed application objects. Reconcile the backup read allowlist, then
   remove all bootstrap secrets from the operator environment.
7. Store the public TLS `expenseops_migrator` URL only as the protected GitHub
   `production` environment secret `EXPENSEOPS_MIGRATION_DATABASE_URL`; do not
   put it on any Railway service. Put only the private runtime URL on web,
   outbox, and both Gmail cron services. Every URL must identify the same
   production Postgres host/database while using its assigned role. Stage
   variable changes without deploying and verify usernames and the
   secret-free database target fingerprint against the selected production
   Postgres service. The Railway-provided `postgres` URL must not remain on a
   deployed service.
8. Assign the explicit config paths: web `/railway.web.json`, outbox
   `/railway.outbox.json`, Gmail receipts
   `/railway.gmail-receipts.json`, and Gmail promotions
   `/railway.gmail-promotions.json`. The shared `/railway.json` stays neutral.
9. Explicitly set `AGENT_WRITE_ACTIONS_ENABLED`, `AGENT_PROACTIVE_ENABLED`, and
   `AGENT_PURCHASING_ENABLED` to `false` on every runtime service.
10. Configure the protected GitHub `production` environment with the exact
    backup/migration credential and public-certificate contract described
    below. Keep the certificate private key and its password offline and
    outside GitHub and Railway.
11. Manually run **Production release** with `release_phase=compatibility` and
    the exact compatibility SHA. The protected `production` environment must
    require approval. Never use direct `railway up` or a GitHub-push deployment
    for production. There is no operator-supplied backup ID: before any Railway
    upload, the workflow verifies that PITR remains configured and independently
    creates and restore-validates a new logical backup through
    `expenseops_backup`.
12. Only after the restore matches the source snapshot does the workflow
    package the dump, source/restored manifests, and recovery metadata, then
    encrypt that bundle using authenticated CMS AES-256-GCM to the approved
    public recipient certificate. It deletes plaintext recovery files and
    uploads only ciphertext as a 90-day GitHub Actions artifact, recording the
    Actions archive digest separately from the raw CMS SHA-256. A PITR, backup,
    restore, manifest, certificate, encryption, or artifact-upload failure
    stops the release before a Railway application deployment.
13. The workflow verifies credentials, Agent flags, and the exact SHA/config,
    then runs a protected, one-shot migration stage on the GitHub runner. It
    authenticates only as `expenseops_migrator`, upgrades Alembic, verifies the
    head, and reconciles exact runtime grants. The credential is exposed only
    to the protected credential-verification, migration, or rollback-query
    steps in that approved job and never to a Railway service. A non-zero result
    stops the workflow before any Railway application upload. This
    Hobby-compatible job is the dedicated migration boundary; no sixth Railway
    service is required.
14. Only after migration success, the workflow deploys outbox, Gmail receipts,
    and Gmail promotions from the same checkout, requiring `SUCCESS` from each.
    Web deploys last. A failure leaves the old web revision active.
15. The compatibility web gates on `/health`. Verify `/health` is 200 and
    `/readiness` is truthfully 503; record all deployment IDs.
16. Run **Production release** again with `release_phase=hardened`, the exact
    hardening SHA, and `compatibility_sha` set to the deployed compatibility
    SHA. The hardening SHA must be its descendant on `main`. The workflow first
    creates and restore-validates another fresh recovery artifact, requires the
    compatibility web revision to be the latest success, applies `0029`,
    repeats the ordered runtimes, and deploys the `/readiness`-gated web last.
17. Verify `/health` and `/readiness` are 200, then verify sign-in, one
    transaction review, webhook routing, and outbox delivery. Record all IDs.
18. The controlled app-only rollback gate queries `alembic_version` directly
    through the protected migrator secret and never runs Alembic.

The service config paths are part of the release boundary:

| Railway service | Config path | Database credential | Process |
| --- | --- | --- | --- |
| `expenseops` web | `/railway.web.json` | Restricted runtime role | Uvicorn only; never Alembic |
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
a failed migration stage, worker deployment, or cron deployment cannot activate the new web
revision.

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

The GitHub `production` environment needs three secrets: `RAILWAY_TOKEN`,
`EXPENSEOPS_BACKUP_DATABASE_URL`, and
`EXPENSEOPS_MIGRATION_DATABASE_URL`. The database URLs must be public TLS URLs
for exactly `expenseops_backup` and `expenseops_migrator`, respectively, point
to the selected production host/database, and never appear on a Railway
service. The environment also needs public-key variables
`EXPENSEOPS_BACKUP_RECIPIENT_CERT_B64` and
`EXPENSEOPS_BACKUP_RECIPIENT_CERT_SHA256`; the fingerprint is the approved
uppercase, colon-delimited SHA-256 certificate fingerprint. Keep the matching
private key and its password offline, outside GitHub and Railway.

Keep a separate recovery escrow outside GitHub and Railway for
`APP_SECRET_KEY` and every still-required previous application-encryption key.
The CMS private key can decrypt the database artifact, but it cannot decrypt
provider credentials stored inside the database without that application key
material.

The remaining non-secret variables are `RAILWAY_PROJECT_ID`,
`RAILWAY_PRODUCTION_ENVIRONMENT_ID`,
`RAILWAY_OUTBOX_SERVICE_ID`,
`RAILWAY_GMAIL_RECEIPTS_SERVICE_ID`, `RAILWAY_GMAIL_PROMOTIONS_SERVICE_ID`,
`RAILWAY_WEB_SERVICE_ID`, `RAILWAY_POSTGRES_SERVICE_ID`, and
`PRODUCTION_BASE_URL`. Require a reviewer on that environment. The
project token authorizes deployments but is not a database credential; database URLs remain
scoped to their Railway services.

### Migration failure

If the protected migration stage fails, times out, targets the wrong database,
or fails its role/head/grant checks, the workflow stops before any Railway
upload. Leave the current web revision serving, inspect the bounded Actions
logs, correct the cause through a reviewed change or safe configuration repair,
and rerun the same reviewed SHA only when appropriate. Alembic migrations are
retryable after a rolled-back failure. Do not stamp the database, edit
`alembic_version`, recreate the database, or manually rewrite customer/domain
rows to force progress.

The migration stage contains separate Alembic and grant-reconciliation
transactions. If Alembic fails inside its transaction, that transaction rolls
back. If Alembic reaches the requested head but the later head check or runtime
grant reconciliation fails, the new schema may already be committed even
though the protected migration stage is failed and no new web revision is
activated. Inspect `alembic current` and the migration logs, correct the
reviewed migration/allowlist/ACL cause, and rerun the same release barrier. Do
not assume every failed migration stage left the database at its prior
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

After `0029`, use `release_phase=rollback` with the reviewed compatibility SHA.
The workflow must first pass the same fresh logical backup, PostgreSQL 18
restore, manifest, encryption, and ciphertext-artifact gate. This app-only path
then queries through the isolated migrator credential. It proves the database is at exactly `0029`
and never runs Alembic, then deploys compatibility runtimes and web last.
`/readiness` will be 503 because that revision expects the older head, so keep
the incident open and forward-fix with a descendant that retains `0029`. The
gate intentionally refuses this rollback if the database has advanced beyond
`0029`; use only a separately reviewed schema-compatible hardened recovery
revision in that case. Never run the compatibility migration phase against an
`0029` database, deploy the pre-cutover application, or downgrade `0029`.

For data damage, freeze releases and stop every writer, including web, outbox,
and cron executions. Select an earlier 90-day encrypted logical artifact,
retrieve the private key and password only in a trusted offline recovery
environment, and follow the authenticated, non-streaming procedure in
**Offline recovery drill** below. Restore into an isolated PostgreSQL 18 target;
never restore over or reset the source. Verify revision, role
attributes, RLS/FORCE policies, exact row evidence, sequence safety, and
representative financial/provider records with a non-writing temporary
application. The Railway-created sibling Postgres service required for a safe
PITR drill cannot be provisioned by the current Hobby project, so PITR is not
an independently proven recovery path here. Do not attempt an in-place PITR
restore. Stage any eventual target URLs together and cut over only under a
reviewed recovery plan.
Retain the original database without writes until reconciliation is complete.
Never manually rewrite customer/domain records to force recovery.

## Backup and point-in-time recovery

### Railway Hobby boundary

The current Railway Hobby project cannot use managed volume-backup schedules
or provision the sibling Postgres service needed for a non-destructive PITR
restore. Do not model daily/weekly/monthly snapshots in release evidence, and
do not describe a 24-hour fallback RPO. The logical-backup RPO is the most
recent successful release artifact and is therefore unbounded between approved
production releases. A 90-day retention period controls artifact history; it
does not create a schedule. An ad hoc manual `pg_dump` is not equivalent
release evidence.

Keep PITR enabled as defense in depth. Every release requires it to be enabled,
bucket-wired, and blocker-free. The protected workflow records the SSH-derived
live and archiver booleans as telemetry, but does not treat them as release
proof because project-token authentication cannot perform Railway's live SSH
probe. This does not count as backup-set or restore proof; the fresh encrypted
logical backup and exact isolated PostgreSQL 18 restore remain the fail-closed
recovery gate.

PITR documentation: <https://docs.railway.com/volumes/point-in-time-recovery>

### Release recovery gate

Before any Railway application upload, the protected workflow:

1. Authenticates over TLS as the dedicated `expenseops_backup` role, confirms
   the URL points to the selected production Postgres host/database, and
   verifies the role is `NOSUPERUSER`, read-only, object-free, and restricted
   to the reviewed table/sequence read surface. `BYPASSRLS` is required only so
   a complete dump includes every tenant while `FORCE ROW LEVEL SECURITY`
   remains enabled.
2. Starts `SERIALIZABLE READ ONLY DEFERRABLE`, exports one PostgreSQL snapshot,
   and uses that exact snapshot for the table-row manifest and custom-format
   `pg_dump`. It also compares exact sequence inventory, ownership, and static
   configuration. Sequence counters are not MVCC snapshot data, so exact live
   `last_value` equality is not claimed while writers run.
3. Restores into an ephemeral PostgreSQL 18 service with no access to
   production, compares the exact table-row and sequence-configuration
   manifests plus the Alembic revision, and checks that each restored owned
   sequence's effective next value cannot collide with restored rows.
4. Packages the dump, source/restored manifests, and self-describing recovery
   metadata, then encrypts that validated bundle with OpenSSL CMS authenticated
   encryption using AES-256-GCM and RSA-OAEP/SHA-256 to the approved public
   certificate. The certificate fingerprint must match
   `EXPENSEOPS_BACKUP_RECIPIENT_CERT_SHA256`; the private key is never present
   in CI.
5. Deletes the plaintext bundle and members, uploads only the authenticated
   ciphertext, and retains it for 90 days. Release evidence distinguishes the
   GitHub Actions artifact-archive digest from the raw CMS ciphertext SHA-256;
   the two hashes identify different byte streams.

Any failure blocks migrations and every application deployment. Merely running
`pg_dump` is not equivalent evidence.

Minimum retained evidence is the exact release SHA; workflow run and attempt;
artifact ID, URL, archive digest, raw CMS SHA-256, and expected expiry/retention
policy; recipient certificate fingerprint; source and restore PostgreSQL
versions; exact source and restored Alembic revision; source/restored manifest
contents and hashes; verification timestamps; and the recovery-gate result. A
timed offline drill also records achieved RPO/RTO, key custodian, approver, and
secure disposal.

### Offline recovery drill

1. Choose a retained ciphertext artifact. Verify and record its release SHA,
   workflow run/attempt, artifact ID and expected expiry, Actions
   artifact-archive digest, and the separately recorded raw CMS SHA-256. Copy
   it into a trusted offline recovery environment.
2. Retrieve the matching CMS private key and password plus `APP_SECRET_KEY` and
   every still-required previous application-encryption key from separate
   offline custody. Verify the expected public-certificate SHA-256 fingerprint.
   Never upload this private material to GitHub or Railway.
3. Set `umask 077`, use a dedicated mode-0700 temporary directory, and decrypt
   the CMS ciphertext to a new mode-0600 temporary file. OpenSSL CMS may emit
   plaintext before a GCM authentication failure is reported: never pipe its
   output to `tar`, `pg_restore`, or another consumer. Require the decrypt
   command to exit zero before atomically moving or consuming the plaintext.
4. Before extraction, safely list the authenticated bundle and reject absolute
   paths, `..` traversal, links, devices, extra members, or duplicate members.
   Require only the expected regular files, then extract them inside the
   dedicated directory. Verify the recovery metadata, raw dump and manifest
   hashes, release identity, certificate fingerprint, source/restore versions,
   and Alembic revision.
5. Restore the verified dump into an isolated disposable PostgreSQL 18
   instance; never modify the production source. Re-run tenant-isolation tests,
   exact table-row and sequence-configuration comparisons, effective-next-value
   collision checks, and sample transaction/Splitwise reconciliation. Confirm
   no worker can write to the restored target and provider credentials decrypt
   with the escrowed application keys.
6. Record elapsed time, artifact age, achieved RPO/RTO, evidence listed above,
   key custodian, approver, and secure disposal of plaintext and the temporary
   database.

Do not credit Railway PITR as restore-tested until it can be restored into an
independent target without modifying production and the same validation is
recorded. The present Hobby resource limit makes that drill unavailable; PITR
remains a monitored secondary layer.

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

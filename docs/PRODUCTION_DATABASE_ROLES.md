# Production database roles

ExpenseOps production uses three dedicated PostgreSQL login roles. The public
web process, outbox worker, and scheduled jobs use `expenseops_runtime`; only
the dedicated migration service uses `expenseops_migrator`; and only the
off-platform encrypted logical-backup job uses `expenseops_backup`. The
Railway-provided `postgres` superuser remains an operator/bootstrap credential
and must not be stored on an application service.

| Capability | `expenseops_runtime` | `expenseops_migrator` | `expenseops_backup` |
| --- | --- | --- | --- |
| Superuser, create role/database, replication | No | No | No |
| Bypass RLS | No | Yes, for controlled migrations only | Yes, solely for complete logical backups |
| Own the database | No | No; Railway `postgres` remains the owner | No |
| Database privileges | `CONNECT` only; no `CREATE` or `TEMPORARY` | `CONNECT` only; no `CREATE` or `TEMPORARY` | `CONNECT` only; no `CREATE` or `TEMPORARY` |
| `public` schema | `USAGE` only | Owner with `USAGE`, `CREATE` | `USAGE` only |
| Own schemas or database objects | No | Owns reviewed application objects | No |
| Create persistent schema objects | No | Yes, in `public` | No |
| Application tables | `SELECT`, `INSERT`, `UPDATE`, `DELETE` | Owner | `SELECT` only |
| `alembic_version` | `SELECT` only | Owner/write | `SELECT` only |
| Application sequences | `USAGE`, `SELECT`; never `setval`/`UPDATE` | Owner | `SELECT` only; no `USAGE`/`UPDATE` |
| Functions in `public` | Five exact tenant-routing functions only | Owner | No `EXECUTE` |
| Types in `public` | No `USAGE` grant | Owner/creator rights used by migrations | No `USAGE` grant |
| Role memberships to or from another role | No | No | No |

`expenseops_backup` has `BYPASSRLS` because `FORCE ROW LEVEL SECURITY` must
remain enabled and a complete disaster-recovery dump must contain every
tenant's rows. A non-bypass login would silently produce an incomplete dump.
This one exceptional attribute is paired with direct `SELECT` only on the
reviewed application-table and owned-sequence allowlists. The role has no DML,
schema creation, temporary-table, ownership, membership, application-function,
or type privileges. It is never used by a Railway application service.

The routing grants are deliberately restricted to exact provider/token lookups:

- `public.expenseops_route_plaid_item(text)`
- `public.expenseops_route_telegram_identity(text,text)`
- `public.expenseops_route_active_telegram_identity_by_link_code(text)`
- `public.expenseops_route_telegram_link_code(text)`
- `public.expenseops_route_workspace_invitation(text)`

## Guarded bootstrap

[`scripts/bootstrap_database_roles.py`](../scripts/bootstrap_database_roles.py)
prints a secret-free SQL plan by default and makes no connection:

```bash
.venv/bin/python scripts/bootstrap_database_roles.py --dry-run
```

`--apply` is the guarded role/ownership mutation mode. It requires a PostgreSQL superuser URL and
three distinct generated passwords of at least 24 characters, supplied through
`EXPENSEOPS_ADMIN_DATABASE_URL`, `EXPENSEOPS_RUNTIME_PASSWORD`, and
`EXPENSEOPS_MIGRATOR_PASSWORD`, and `EXPENSEOPS_BACKUP_PASSWORD`.
Do not put these values in repository files. Do not pass them as command-line
arguments, and never attach them to a deployed Railway service.
Inject them only into the trusted operator process from an approved secret
manager so they do not enter shell history. Generate URL-safe values, for
example with `secrets.token_urlsafe(32)`, or percent-encode them when
constructing the three PostgreSQL URLs.

The first restricted-role cutover has this guarded sequence:

1. Confirm the Railway PITR window and inventory database owners, login roles,
   and inherited `PUBLIC` privileges. Stop if PITR is not independently
   verified.
2. Before changing runtime or object ownership, export only
   `EXPENSEOPS_ADMIN_DATABASE_URL` and `EXPENSEOPS_BACKUP_PASSWORD`, then run:

   ```bash
   .venv/bin/python scripts/bootstrap_database_roles.py --bootstrap-backup-role
   ```

   This guarded mode creates or normalizes only `expenseops_backup`, binds its
   password as a query parameter, and grants its exact current read surface in
   one transaction. It does not create or alter the runtime/migrator roles,
   transfer ownership, or mutate default ACLs. It rejects runtime or migrator
   password variables. Because privileges inherited from `PUBLIC` cannot be
   denied with a role-specific revoke, it removes `PUBLIC` database
   `CREATE`/`TEMPORARY`, `public` schema `CREATE`, and execution of existing
   `public` functions. The Railway owner/superuser retains owner authority;
   stop and review any other legitimate login that depended on those inherited
   privileges.
3. Construct the backup-role URL without logging it and encrypt the
   off-platform logical backup to the approved public recipient certificate.
   Prove the artifact can be decrypted with the offline private key and
   restored into an isolated disposable database. Stop if either recovery path
   is unverified.
4. With the current application stopped from schema changes, run `--dry-run`,
   review the allowlists, then run `--apply`. This creates and normalizes all
   three roles and transfers the `public` schema and existing reviewed
   application objects to the migrator. It also reconciles the backup role's
   exact reviewed read surface. It does not transfer database ownership. The
   full bootstrap revokes `CONNECT`, `CREATE`, and `TEMPORARY` from `PUBLIC`;
   the Railway `postgres` database owner retains owner authority and the three
   ExpenseOps roles receive direct `CONNECT`.
5. Configure only the dedicated migration service with the migrator URL in
   `DATABASE_URL`. Do not copy `APP_SECRET_KEY` or
   `APP_SECRET_KEY_PREVIOUS` to that service; Alembic intentionally loads only
   migration-safe production settings. Its release barrier must run both
   commands in order:

   ```bash
   alembic upgrade head && \
     python scripts/bootstrap_database_roles.py --reconcile-runtime-grants
   ```

   The reconciliation mode is the second mutating mode. It rejects any admin
   URL or bootstrap password variables and
   verifies that `current_user` is the exact expected `expenseops_migrator`,
   reapplies only runtime/default ACLs, and runs all postcondition checks in one
   transaction. A migration, identity, grant, or verification failure must
   block web deployment.
6. Keep `--reconcile-runtime-grants` after every future Alembic upgrade. This
   ensures newly created allowlisted routing functions are executable before a
   new runtime revision is deployed, without exposing the admin credential to
   the migration service.
7. Store the runtime URL only on web/workers/crons. Store the backup URL only
   as the GitHub production-environment secret
   `EXPENSEOPS_BACKUP_DATABASE_URL`. Store the base64-encoded public recipient
   certificate as the GitHub production-environment variable
   `EXPENSEOPS_BACKUP_RECIPIENT_CERT_B64`; it is public key material, not a
   secret. Pin its colon-delimited uppercase SHA-256 fingerprint in
   `EXPENSEOPS_BACKUP_RECIPIENT_CERT_SHA256` so an accidental recipient-key
   replacement fails the release before a backup is created. The recipient
   private key and its password must remain offline and outside GitHub.
   Do not store any backup credential on a Railway service.
   Remove all bootstrap secrets from the operator environment and validate
   `/readiness` before traffic.

The admin bootstrap and migration-role reconciliation are idempotent and each
runs in one transaction. The utility explicitly
allowlists application tables/functions, uses catalog-derived quoted names for
owned sequences, revokes inherited role memberships, and verifies the final
role attributes, ownership, table/sequence/function ACLs, default ACLs, and
schema boundary. The backup verification fails closed if that role owns an
object, receives any non-`SELECT` data privilege, can execute a `public`
function, or can access a relation or sequence outside the reviewed allowlist.
An error rolls back the active mode's transaction. Alembic itself commits
before reconciliation; if reconciliation fails, the new schema may exist while
the old application remains active. Correct the reviewed ACL/allowlist issue and
rerun the same migration deployment, which rechecks the head and retries
reconciliation. Unexpected objects are not silently reassigned; update the
reviewed allowlist before provisioning a new object.

The bootstrap and every reconciliation also remove `PUBLIC` access to the
schema and reviewed tables, sequences, functions, and types. The bootstrap
removes `PUBLIC` database access. Default privileges for objects subsequently
created by `expenseops_migrator` grant nothing to `PUBLIC` or
`expenseops_runtime` or `expenseops_backup`; reconciliation then grants only the
allowlisted runtime operations and backup reads. This deny-by-default interval
prevents a newly created object from becoming accessible before its reviewed
grant pass succeeds. Adding a future table or owned sequence therefore requires
updating the reviewed application allowlist; the post-migration reconciliation
grants backup `SELECT` and verifies that no broader or unexpected privilege was
introduced before the release can continue.

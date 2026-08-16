# Production database roles

ExpenseOps production uses two PostgreSQL login roles. The public web process,
outbox worker, and scheduled jobs use `expenseops_runtime`; only the dedicated
migration service uses `expenseops_migrator`. The Railway-provided `postgres`
superuser remains an operator/bootstrap credential and must not be stored on an
application service.

| Capability | `expenseops_runtime` | `expenseops_migrator` |
| --- | --- | --- |
| Superuser, create role/database, replication | No | No |
| Bypass RLS | No | Yes, for controlled migrations only |
| Own the database | No | No; Railway `postgres` remains the owner |
| Database privileges | `CONNECT` only; no `CREATE` or `TEMPORARY` | `CONNECT` only; no `CREATE` or `TEMPORARY` |
| `public` schema | `USAGE` only | Owner with `USAGE`, `CREATE` |
| Own `public` and application objects | No | Yes |
| Create persistent schema objects | No | Yes, in `public` |
| Application tables | `SELECT`, `INSERT`, `UPDATE`, `DELETE` | Owner |
| `alembic_version` | `SELECT` only | Owner/write |
| Application sequences | `USAGE`, `SELECT`; never `setval`/`UPDATE` | Owner |
| Functions in `public` | Five exact tenant-routing functions only | Owner |
| Types in `public` | No `USAGE` grant | Owner/creator rights used by migrations |
| Role memberships to or from another role | No | No |

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
two distinct generated passwords of at least 24 characters, supplied through
`EXPENSEOPS_ADMIN_DATABASE_URL`, `EXPENSEOPS_RUNTIME_PASSWORD`, and
`EXPENSEOPS_MIGRATOR_PASSWORD`. Do not put these values in repository files or
command-line arguments, and never attach them to a deployed Railway service.
Inject them only into the trusted operator process from an approved secret
manager so they do not enter shell history. Generate URL-safe values, for
example with `secrets.token_urlsafe(32)`, or percent-encode them when
constructing the two PostgreSQL URLs.

The first restricted-role cutover has two guarded passes:

1. Confirm and lock the approved recovery point/PITR window. Stop if recovery
   has not been independently verified.
2. Inventory database owners and login roles. The script revokes `CONNECT`,
   `CREATE`, and `TEMPORARY` from `PUBLIC`; the Railway `postgres` database
   owner retains owner authority and the two ExpenseOps roles receive direct
   `CONNECT`. Stop and review any other legitimate login that currently relies
   on a `PUBLIC` database grant rather than silently breaking it.
3. With the current application stopped from schema changes, run `--dry-run`,
   review the allowlists, then run `--apply`. This creates and normalizes both
   roles and transfers the `public` schema and existing reviewed application
   objects to the migrator. It does not transfer database ownership.
4. Configure only the dedicated migration service with the migrator URL in
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
5. Keep `--reconcile-runtime-grants` after every future Alembic upgrade. This
   ensures newly created allowlisted routing functions are executable before a
   new runtime revision is deployed, without exposing the admin credential to
   the migration service.
6. Store the runtime URL only on web/workers/crons, remove bootstrap secrets
   from the operator environment, and validate `/readiness` before traffic.

The admin bootstrap and migration-role reconciliation are idempotent and each
runs in one transaction. The utility explicitly
allowlists application tables/functions, uses catalog-derived quoted names for
owned sequences, revokes inherited role memberships, and verifies the final
role attributes, ownership, table/sequence/function ACLs, and schema boundary.
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
`expenseops_runtime`; reconciliation then grants only the allowlisted runtime
operations. This deny-by-default interval prevents a newly created object from
becoming accessible before its reviewed grant pass succeeds.

# FDE Readiness Phase 1B

ExpenseOps uses a provider-neutral OpenID Connect boundary for real-user authentication. The
identity provider proves who the user is; ExpenseOps owns workspace membership, authorization,
integration credentials, and tenant isolation.

## Authentication modes

- `AUTH_MODE=local` keeps the existing API token/basic-auth path for local development and tests.
- `AUTH_MODE=oidc` validates issuer, audience, expiry, subject, algorithm, and the provider JWKS.
- Production configuration requires OIDC and does not accept static development credentials.

Any standards-compliant Authorization Code OIDC provider can be used. Register
`OIDC_REDIRECT_URI` as an allowed callback and configure:

```dotenv
AUTH_MODE="oidc"
OIDC_ISSUER="https://your-provider.example"
OIDC_AUDIENCE="expenseops-api"
OIDC_CLIENT_ID="..."
OIDC_CLIENT_SECRET="..."
OIDC_REDIRECT_URI="https://your-expenseops-domain/auth/callback"
OIDC_SCOPES="openid profile email"
OIDC_ALGORITHMS="RS256"
# Set for the first migration only when an existing single-user workspace must be claimed.
OIDC_BOOTSTRAP_EMAIL="owner@example.com"
```

The first verified identity creates one user, personal workspace, and owner/default membership.
The `(provider, sub)` pair is the permanent identity key, so repeat login is idempotent even when
the provider email changes.

For an upgraded single-user deployment, set `OIDC_BOOTSTRAP_EMAIL` to the intended owner's exact
verified OIDC email before their first login. That login claims the migrated local workspace and
its existing data without SQL. Remove the variable after the claim; subsequent authentication is
bound to the provider subject.

## Self-service integrations

Gmail and Splitwise use expiring, single-use OAuth state persisted by hash in the database and
bound to the initiating user and workspace. Plaid Link ownership comes only from authenticated
request context. Telegram uses a hashed, one-time 10-minute `/connect CODE` generated in Settings.
Disconnecting an integration removes or disables credentials while retaining financial and
workflow history.

Gmail requires its existing OAuth client to allow:

```text
https://your-expenseops-domain/api/integrations/gmail/callback
```

Splitwise requires:

```dotenv
SPLITWISE_OAUTH_CALLBACK_URL="https://your-expenseops-domain/splitwise/oauth/callback"
```

## Rate limiting and RLS

Security and expensive write endpoints use a small rate-limiter abstraction. Phase 1B ships an
in-process backend, so production should remain at one web replica. A shared Redis-compatible
backend is a Phase 1C requirement before horizontal web scaling.

PostgreSQL RLS is intentionally not enabled in Phase 1B. Application-level session enforcement
already covers workspace-owned ORM rows, while webhooks and jobs establish scope from trusted
stored identifiers. Safe RLS requires separate runtime/admin roles, transaction-local
`app.workspace_id`, policies for every workspace-owned table (including indirect children), and
dedicated webhook/job tests. `ENABLE_POSTGRES_RLS=true` is rejected until that work is complete.

## Local verification

```bash
alembic upgrade head
pytest -q
ruff check .
cd frontend
npm test -- --run
npm run lint
npm run build
```

Use `AUTH_MODE=local` locally. For a production-style smoke test, use a test OIDC tenant and two
test users: verify separate personal workspaces, invite the second user into a shared workspace,
switch both users between workspaces, and confirm each integration appears only in its connected
workspace.

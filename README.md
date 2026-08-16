# ExpenseOps

### The personal operations assistant for the chores hiding inside your transactions

A dinner needs splitting. The laundry detergent is almost due. You need a
haircut, but only if it fits on the way home. Three promotion emails claim to
be “must-see.”

None of these jobs is difficult. Remembering them—and opening four different
apps to finish them—is the annoying part.

ExpenseOps brings those small decisions into one private dashboard and one
Telegram conversation. It watches for work, explains what it found, and waits
for you before doing anything consequential.

> **Built for one trusted user.** ExpenseOps is a personal project and
> controlled design-user release. ExpenseOps now has workspace isolation and
> per-user provider identities, while the broader GA operations gate is still in progress.

## A Day With ExpenseOps

```text
Card transaction arrives
        ↓
Telegram: “Personal or shared?”
        ↓
You: “Split with Rahul and Janhavi equally”
        ↓
ExpenseOps prepares the Splitwise expense
        ↓
You confirm before anything is posted
```

Later, you photograph a grocery receipt. ExpenseOps recognizes the household
items, lets you correct the matches, and starts learning when those staples are
usually needed again.

On the way out, you add “get a haircut” and “shop at Aldi.” ExpenseOps searches
real locations, compares complete trips, and builds a route using an actual
salon and an actual Aldi branch—not vague search terms.

Meanwhile, the Deals workspace turns a noisy Gmail Promotions tab into a short,
ranked list of offers that might genuinely matter to you.

## What You Get

| Workspace | What it takes off your plate |
| --- | --- |
| **Expense Review** | Finds card transactions that still need a personal/shared decision |
| **Splitwise Groups** | Searches friends, creates groups, and manages participants |
| **Household Ops** | Tracks errands, staples, receipts, and optimized trips |
| **Deals** | Extracts and ranks concrete offers from Gmail Promotions |
| **Sandbox Lab** | Tests Plaid and notification behavior without real money |
| **Telegram** | Handles expense review, splits, and receipt uploads from your phone |

ExpenseOps is confirmation-first: it can recommend, prepare, and explain, but
you stay in control of shared expenses and uncertain receipt matches.

---

## Install It Locally

You can get the dashboard running without configuring every external
integration. Start with the app, then add Plaid, Telegram, Gmail, or Google Maps
when you are ready.

### 1. Prerequisites

- Python 3.11+
- Node.js 20+
- npm 10+
- Git

### 2. Clone and install

```bash
git clone https://github.com/gkpatil030799/expenseops-agent.git
cd expenseops-agent

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

npm --prefix frontend install
```

### 3. Create your private configuration

```bash
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the generated key into `APP_SECRET_KEY` in `.env`. Never commit this
file—`.env` is where your API keys, OAuth tokens, personal locations, and
passwords belong.

The minimum local configuration is:

```env
DATABASE_URL="sqlite:///./expenseops.db"
APP_ENV="local"
ENVIRONMENT="local"
FRONTEND_ORIGIN="http://localhost:5173"
APP_SECRET_KEY="paste-your-generated-fernet-key"
```

### 4. Prepare the database

```bash
make migrate
```

### 5. Start ExpenseOps

Run the backend:

```bash
make run
```

In a second terminal, run the frontend:

```bash
npm --prefix frontend run dev
```

Open the URL printed by Vite, normally <http://localhost:5173>.

Check the backend at <http://127.0.0.1:8000/health>. If Vite chooses a different
port, update `FRONTEND_ORIGIN` in `.env` and restart the backend.

That is enough to explore the dashboard. The sections below unlock the live
integrations.

---

## Pick Your Integrations

ExpenseOps is modular. You do not need a bank connection to try Household Ops,
and you do not need Gmail to use expense splitting.

| If you want… | Configure… |
| --- | --- |
| Card transaction ingestion | Plaid |
| Real shared-expense posting | Splitwise |
| Mobile review and receipt photos | Telegram |
| Natural-language and receipt parsing | OpenAI |
| Receipt discovery and ranked deals | Gmail |
| Real business search and trip optimization | Google Maps Platform |

The complete list of settings and safe defaults lives in
[`.env.example`](.env.example).

### Plaid: bring in card transactions

Start in Plaid Sandbox:

```env
PLAID_CLIENT_ID=""
PLAID_SECRET=""
PLAID_ENV="sandbox"
PLAID_WEBHOOK_URL="https://your-tunnel.example/plaid/webhook"
ENABLE_EXPENSEOPS_SANDBOX_LAB="true"
PLAID_VERIFY_WEBHOOKS=false
```

For local webhook tests, expose port 8000 with ngrok or another HTTPS tunnel:

```bash
ngrok http 8000
```

Once linked, ExpenseOps stores Plaid access tokens encrypted, maintains each
Item's transaction cursor, and idempotently handles added, modified, and removed
transactions.

Production rules are intentionally stricter:

- Use `PLAID_ENV=production`.
- Use a stable HTTPS webhook URL.
- Keep Plaid webhook verification enabled.
- Disable Sandbox Lab.
- Never enable the local unverified-webhook bypass.

### Splitwise: turn decisions into shared expenses

The simplest setup uses a Splitwise API key:

```env
SPLITWISE_API_KEY=""
```

OAuth 1.0 fields are also available in `.env.example`.

ExpenseOps can search friends and groups, create groups, add or remove
participants, calculate multiple split styles, and prepare expenses. Pending
card transactions are blocked from posting by default, and every real post
requires confirmation.

### Telegram: operate ExpenseOps from your phone

1. Create a bot with **BotFather**.
2. Send your new bot a message.
3. Configure:

```env
TELEGRAM_BOT_TOKEN=""
TELEGRAM_CHAT_ID=""
TELEGRAM_ALLOWED_USER_ID=""
TELEGRAM_WEBHOOK_SECRET="choose-a-long-random-value"
APP_PUBLIC_URL="https://your-expenseops-domain.example"
```

4. Register the webhook:

```bash
curl --request POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  --data-urlencode "url=https://YOUR_DOMAIN/telegram/webhook" \
  --data-urlencode "secret_token=${TELEGRAM_WEBHOOK_SECRET}"
```

Now you can review transactions with buttons or natural language. You can also
send a receipt directly—no command or caption required.

Supported receipt uploads:

- Telegram photos
- JPEG, PNG, and WebP documents
- PDF receipts
- Up to 10 MB by default

The bot replies with the merchant, parsed items, and three choices:

- **Confirm** — record the matched household purchases
- **Edit matches** — correct them in Household Ops
- **Ignore** — learn nothing from this receipt

Confirming a receipt does **not** create a Splitwise expense.

### OpenAI: enable richer parsing

```env
OPENAI_API_KEY=""
OPENAI_MODEL="gpt-4.1-mini"
RECEIPT_PARSER_PROVIDER="openai"
RECEIPT_PARSER_MODEL="gpt-4.1-mini"
```

Without an OpenAI key, deterministic and fallback paths remain available where
supported. Keep model usage bounded by the limits in `.env.example`.

### Gmail: learn from receipts and tame Promotions

Gmail uses one OAuth client and one refresh token with the read-only
`gmail.readonly` scope:

```env
GMAIL_CLIENT_ID=""
GMAIL_CLIENT_SECRET=""
GMAIL_REFRESH_TOKEN=""
GMAIL_USER_ID="me"

GMAIL_RECEIPT_SYNC_ENABLED=true
PROMOTIONS_ENABLED=true
PROMOTIONS_MIN_SCORE=50
```

Receipt discovery uses the narrow `GMAIL_RECEIPT_QUERY`. Promotion
Intelligence resolves Gmail's Promotions system label, performs a resumable
bounded backfill, and keeps receipt and promotion processing state independent.

Read the deeper guides:

- [Promotion Intelligence](docs/PROMOTION_INTELLIGENCE.md)
- [Replenishment Learning](docs/REPLENISHMENT_LEARNING.md)

### Google Maps: turn “Aldi” into the right Aldi

Enable these APIs in Google Cloud:

- Places API (New)
- Geocoding API
- Routes API

Restrict the key to those APIs, then configure:

```env
HOUSEHOLD_PLACE_SEARCH_PROVIDER="google_places"
HOUSEHOLD_ROUTING_PROVIDER="google_maps"
GOOGLE_MAPS_API_KEY=""
```

Place search and routing are deliberately separate. ExpenseOps first finds
concrete candidates, then compares those candidates against the complete trip.
A chain branch is not chosen merely because it is closest to home.

If live providers are disabled, the app uses deterministic fallback planning
and does not pretend that it has measured travel time or distance.

---

## How the Pieces Fit Together

```text
 Plaid ───────────────┐
 Telegram ────────────┤
 Splitwise ───────────┤
 Gmail ───────────────┼──> FastAPI ──> SQLAlchemy/Alembic ──> PostgreSQL
 Google Maps ─────────┤       │
 OpenAI (optional) ───┘       └──> React/Vite dashboard
```

The important boundaries are more useful than the boxes:

- **Place search answers where.** Routing answers how.
- **Receipt parsing identifies products.** Plaid only corroborates merchant,
  date, and amount.
- **Gmail is read-only.** Receipt and promotion processors remain independent.
- **Recommendations are not actions.** Posting and uncertain learning wait for
  confirmation.

### Repository map

```text
app/
  api/              FastAPI and Telegram routes
  jobs/             Scheduled-job entry points
  services/         Domain logic and external integrations
  models.py         SQLAlchemy data model
  static/           Built production dashboard

frontend/src/       React dashboard
alembic/            Database migrations
docs/               Architecture and feature deep dives
sandbox/            Plaid scenarios and reliability tooling
tests/              Backend and integration-focused tests
```

### Stack

- Python, FastAPI, SQLAlchemy, Alembic, and Pydantic
- React, TypeScript, Vite, and Tailwind CSS
- SQLite locally and PostgreSQL in production
- pytest, Ruff, Vitest, ESLint, and TypeScript checks
- Docker and Railway deployment

---

## Background Jobs

Gmail receipt sync:

```bash
python -m app.jobs.gmail_receipts --max-results 25
```

Promotion jobs:

```bash
python -m app.jobs.promotions sync
python -m app.jobs.promotions rescore
python -m app.jobs.promotions digest
```

Weekly replenishment learning:

```bash
python -m app.jobs.weekly_replenishment
```

These jobs are idempotent. A practical starting schedule is every six hours for
Gmail receipts and promotions, with the replenishment workflow running weekly.
In production, configure those schedules explicitly with an external scheduler
such as Railway cron services—schedule-like environment variables do not create
cron services by themselves. Leave `PROMOTIONS_DIGEST_ENABLED=false` until you
have reviewed the quality of your ranked deals.

## Test It

Backend:

```bash
make test
make lint
```

Frontend:

```bash
npm --prefix frontend run lint
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

Fresh migration check:

```bash
DATABASE_URL=sqlite:////tmp/expenseops-migration-check.sqlite alembic upgrade head
```

The test suite covers the risky parts: transaction sync, webhook verification,
notification idempotency, Telegram callbacks, Splitwise payloads, place
resolution, route planning, receipt ingestion, replenishment learning,
promotion ranking, and failure/race-condition scenarios.

### Sandbox Lab

Sandbox Lab gives you a safe place to test Plaid without touching real
transactions:

```env
ENABLE_EXPENSEOPS_SANDBOX_LAB="true"
PLAID_ENV="sandbox"
```

Open `/sandbox-lab`. You can create test transactions, fire webhooks, run
manual sync, inspect the event timeline, execute repeatable scenarios, and
simulate duplicate, concurrent, timeout, cursor, Plaid, or Telegram failures.

Sandbox runtime state and JSONL logs are ignored by Git.

---

## Deploy on Railway

The included [Dockerfile](Dockerfile) builds the React app and packages it with
FastAPI. The shared [railway.json](railway.json) stays neutral. Assign each
production service its explicit config: [railway.web.json](railway.web.json),
[railway.migrations.json](railway.migrations.json),
[railway.outbox.json](railway.outbox.json),
[railway.gmail-receipts.json](railway.gmail-receipts.json), or
[railway.gmail-promotions.json](railway.gmail-promotions.json). The final web
config starts only Uvicorn and uses `/readiness`; `/health` remains lightweight
process liveness. The one-time RLS cutover first deploys a reviewed compatibility
commit on `/health`, then its hardened descendant on `/readiness`.

At minimum, production should have:

```env
APP_ENV="production"
ENVIRONMENT="production"
DATABASE_URL="postgresql+psycopg://..."
APP_SECRET_KEY="generated-fernet-key"
AUTH_MODE="oidc"
OIDC_ISSUER="https://your-provider.example/"
OIDC_AUDIENCE="your-audience"
OIDC_CLIENT_ID="your-client-id"
OIDC_REDIRECT_URI="https://your-app.example/auth/callback"
ENABLE_POSTGRES_RLS=true
RATE_LIMIT_BACKEND="postgres"
ENABLE_EXPENSEOPS_SANDBOX_LAB=false
ALLOW_UNVERIFIED_PLAID_WEBHOOKS_FOR_LOCAL_TEST=false
AGENT_WRITE_ACTIONS_ENABLED=false
AGENT_PROACTIVE_ENABLED=false
AGENT_PURCHASING_ENABLED=false
```

Keep Railway GitHub auto-deploy disabled for production. After review, run the
protected **Production release** workflow for the compatibility SHA and then its
hardening descendant. Preserve both commits when merging this cutover; a squash
merge removes the compatibility rollback/release boundary and is not allowed.
The private migration service uses the non-superuser
`expenseops_migrator` role to upgrade Alembic, verify the head, and reconcile
runtime grants. Only after that succeeds does the workflow deploy outbox and
both Gmail crons with `expenseops_runtime`; web deploys last. Any migration,
grant, worker, or cron failure prevents web activation.

The current Railway Hobby setup does not provide the managed volume-backup
schedules or safe sibling Postgres restore needed to claim a daily snapshot or
a completed PITR restore drill. PITR remains enabled as a health- and
freshness-gated secondary layer. The proven release path is a fresh consistent
logical dump through the dedicated `expenseops_backup` login, restored into an
ephemeral PostgreSQL 18 service and checked for exact table-row counts,
sequence inventory/static configuration, Alembic revision, and collision-safe
effective next values before any Railway upload. Sequence counters are not
MVCC snapshot data, so exact live `last_value` equality is not claimed while
writers run. The validated dump, manifests, and recovery metadata are then
bundled and encrypted to an approved public certificate whose private key is
held offline, using authenticated CMS AES-256-GCM. Only ciphertext is retained
as a 90-day GitHub Actions artifact. Its Actions archive digest and raw CMS
SHA-256 are separate evidence.
Logical-backup RPO is therefore the latest successful release artifact and is
unbounded between approved releases, not a fictitious 24-hour schedule.

Before the first controlled release, assign the web, migration, outbox, Gmail
receipts, and Gmail promotions services their corresponding custom config paths.
The Gmail config files deliberately leave cron schedules in Railway service
settings; record and preserve the existing schedules when changing paths.
Configure the protected GitHub `production` environment and database credential
separation exactly as documented in
[the production operations runbook](docs/PRODUCTION_OPERATIONS_RUNBOOK.md) and
[the database-role contract](docs/PRODUCTION_DATABASE_ROLES.md). The runtime
role has only database connect, schema usage, reviewed table DML, sequence use,
and the five routing-function grants. The migrator owns the reviewed schema
objects but is neither a database owner nor a superuser. Keep the Railway
`postgres` owner credential and all bootstrap password inputs out of every
deployed service.

The initial role cutover order is fixed: verify healthy/fresh PITR and an
encrypted operator backup with recorded manual decrypt/restore evidence; run
the guarded `--bootstrap-backup-role` mode; create and PostgreSQL-18-restore a
fresh dump as `expenseops_backup`; only then run the full runtime/migrator
ownership cutover. The backup login is `NOSUPERUSER` and read-only. Its sole
exceptional `BYPASSRLS` attribute lets a
complete disaster-recovery dump include every tenant while `FORCE ROW LEVEL
SECURITY` remains active; it has no DML, create, temporary, ownership,
membership, function, or deployed-service access.

The protected GitHub `production` environment stores `RAILWAY_TOKEN` and
`EXPENSEOPS_BACKUP_DATABASE_URL` as secrets. It stores the base64 public
certificate and its approved uppercase, colon-delimited SHA-256 fingerprint in
`EXPENSEOPS_BACKUP_RECIPIENT_CERT_B64` and
`EXPENSEOPS_BACKUP_RECIPIENT_CERT_SHA256`; the Railway project, environment,
service IDs, and `PRODUCTION_BASE_URL` remain environment variables. Keep the
recipient private key and its password offline and outside GitHub and Railway.
Separately escrow `APP_SECRET_KEY` and every still-required previous
application-encryption key outside those systems; the CMS key alone cannot
decrypt provider credentials stored in the database. Offline recovery must
authenticate CMS to a protected temporary file before consuming plaintext—it
must never stream decryption directly into `tar` or `pg_restore`. Follow the
runbook's full path and evidence checks.

The compatibility and hardening phases have different rollback boundaries.
Before migration `0029`, repair or forward-fix the compatibility phase; after
`0029`, the controlled app-only rollback can deploy only the reviewed
compatibility SHA while retaining `0029`. Never run an old migration graph
against a database already at `0029`; use the exact procedures in the runbook.

Use Railway Variables for secrets and managed PostgreSQL for data. Do not
deploy `.env`, SQLite databases, receipt files, Sandbox logs, or Sandbox
state.

## Safety Before Cleverness

ExpenseOps handles sensitive integrations, so its defaults lean conservative:

- The private dashboard supports Basic authentication or an API token.
- Plaid access tokens are encrypted at rest with `APP_SECRET_KEY`.
- Production Plaid webhooks are verified.
- Telegram access can be restricted to one user and a secret webhook URL.
- Gmail uses read-only access and narrow queries/labels.
- Promotion links receive conservative trust checks.
- High-risk promotion categories are suppressed.
- Pending transactions do not post to Splitwise by default.
- Logs should never contain tokens, secrets, raw prompts, full email bodies, or
  complete webhook payloads.

For the design rationale, see [Architecture](docs/ARCHITECTURE.md).

## Useful Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Check the application and database |
| `POST /plaid/link-token` | Start Plaid Link |
| `POST /plaid/sync` | Sync configured Plaid Items |
| `GET /transactions?status=ask_user` | View the expense review queue |
| `GET /splitwise/friends?q=alex` | Search Splitwise friends |
| `GET /api/household/errands` | List errands |
| `POST /api/replenishment/gmail/sync` | Discover Gmail receipts |
| `GET /api/promotions` | View active ranked deals |
| `POST /api/promotions/sync` | Sync Gmail Promotions |

Interactive API documentation is available at `/docs` when
`ENABLE_DOCS=true`.

## When Something Does Not Work

<details>
<summary><strong>The frontend cannot reach the backend</strong></summary>

- Confirm the backend is listening on port 8000.
- Confirm Vite is running.
- Make `FRONTEND_ORIGIN` match the browser origin exactly.
- Restart the backend after changing `.env`.

</details>

<details>
<summary><strong>Telegram does not respond</strong></summary>

- Confirm the webhook points to the current public deployment.
- Confirm its secret matches `TELEGRAM_WEBHOOK_SECRET`.
- Confirm `TELEGRAM_ALLOWED_USER_ID` is your Telegram user ID.
- For receipts, use JPEG, PNG, WebP, or PDF within the configured size limit.

</details>

<details>
<summary><strong>Gmail says sync is not configured</strong></summary>

- Set the client ID, client secret, and refresh token in the running environment.
- Confirm the refresh token includes `gmail.readonly`.
- Confirm Gmail API is enabled in the same Google Cloud project.

</details>

<details>
<summary><strong>Place search falls back to manual entry</strong></summary>

- Enable Places API (New), Geocoding API, and Routes API.
- Put the restricted key in the running environment.
- Set both Household Ops providers to their live values.

</details>

<details>
<summary><strong>Plaid webhook returns 403</strong></summary>

Fix webhook verification in production rather than bypassing it. The bypass is
limited to an explicitly local environment with the local-test flag enabled.

</details>

## Honest Limitations

- ExpenseOps isolates workspace data and provider identities per user. The
  controlled design-user beta is currently held on the combined beta gate, and
  broad GA remains NO-GO; see the August 14 re-audit and remediation strategy.
- Connected-account management needs stronger duplicate-link prevention.
- Scheduled work requires an external scheduler such as Railway cron.
- Receipt quality depends on the image and parsing provider.
- Gmail, Maps, Plaid, and OpenAI can consume quotas or incur costs.
- Replenishment predicts likely need; it cannot see physical inventory.
- Sandbox Lab uses local diagnostic state rather than a durable event bus.

## More to Read

- [Architecture](docs/ARCHITECTURE.md)
- [Full-Application Launch Re-audit — August 14, 2026](docs/FULL_APPLICATION_LAUNCH_REAUDIT_2026-08-14.md)
- [Independent Design-User Beta Audit — August 14, 2026](docs/INDEPENDENT_DESIGN_USER_BETA_AUDIT_2026-08-14.md)
- [Consolidated Launch-Remediation Strategy](docs/CONSOLIDATED_LAUNCH_REMEDIATION_STRATEGY_2026-08-14.md)
- [Promotion Intelligence](docs/PROMOTION_INTELLIGENCE.md)
- [Replenishment Learning](docs/REPLENISHMENT_LEARNING.md)
- [Sandbox Lab](sandbox/README.md)

## License

No open-source license has been declared. Treat this repository as
all-rights-reserved unless a license is added.

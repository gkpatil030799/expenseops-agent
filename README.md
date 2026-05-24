# ExpenseOps Agent

Private, approval-first expense automation for Plaid card transactions, Splitwise posting, Telegram review, and a React dashboard.

A personal approval-first agent for shared expenses:

```text
Chase credit card transaction appears
        ↓
Plaid Transactions sync/webhook imports it
        ↓
Agent asks: personal or shared?
        ↓
You search/select Splitwise friends and shares
        ↓
Agent posts the confirmed expense to Splitwise
```

This app uses **Plaid + Splitwise APIs directly**, not a Splitwise MCP. It is designed for private use and private beta testing, with explicit user approval before anything is posted.

## What is included

- FastAPI backend
- Plaid Link token creation
- Plaid public-token exchange
- Plaid `/transactions/sync` ingestion
- Plaid webhook receiver that queues a sync
- Local SQLite by default, PostgreSQL-compatible SQLAlchemy models
- Alembic database migrations for production/private beta deploys
- Encrypted Plaid access-token storage
- Splitwise friends/groups lookup
- Splitwise custom-share `create_expense` posting
- Equal and custom split endpoints
- Human approval-first workflow
- Rule-based personal/shared/unsure recommendations
- Telegram notifications with inline review buttons
- Duplicate posting guard
- React + Vite dashboard, with the old HTML UI kept as fallback
- Unit tests for split calculations and rule-based parsing

## Project structure

```text
app/
  api/                  FastAPI routes
  services/             Plaid, Splitwise, agent, split logic
  static/index.html     Small local UI
  models.py             SQLAlchemy models
  schemas.py            Pydantic API schemas
  security.py           Fernet token encryption
frontend/               React + Vite + TypeScript dashboard
docs/ARCHITECTURE.md    Design and safety notes
tests/                  Unit tests
```

## Prerequisites

Recommended versions:

- Python `3.11` or newer
- Node.js `20` or newer
- npm `10` or newer
- Git
- Plaid developer account
- Splitwise developer app
- Optional: Telegram bot token from BotFather
- Optional: OpenAI API key for AI chat parsing
- Optional: ngrok for local webhook testing

## Clone

```bash
git clone https://github.com/YOUR_USERNAME/expenseops-agent.git
cd expenseops-agent
```

## Local Setup

Create and activate a Python virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Install frontend dependencies:

```bash
cd frontend
npm install
cd ..
```

Create your local environment file:

```bash
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the generated key into `APP_SECRET_KEY` in `.env`.

Important: `.env` contains secrets and must never be committed. Keep `.env` ignored by git.

Minimum local settings:

```bash
ENVIRONMENT="local"
DATABASE_URL="sqlite:///./expenseops.db"
FRONTEND_ORIGIN="http://localhost:5173"
PLAID_CLIENT_ID="..."
PLAID_SECRET="..."
PLAID_ENV="sandbox"
PLAID_WEBHOOK_URL="https://YOUR-NGROK-DOMAIN.ngrok-free.app/plaid/webhook"
SPLITWISE_API_KEY="..."
TELEGRAM_BOT_TOKEN="..."
TELEGRAM_CHAT_ID="..."
TELEGRAM_WEBHOOK_SECRET="..."
```

Run the backend:

```bash
make run
```

Backend API and fallback HTML UI:

For local development, startup still creates missing SQLite tables automatically.
You can also run migrations explicitly:

```bash
make migrate
```

This uses `DATABASE_URL` from `.env`, so it works with local SQLite or Postgres.

```text
http://localhost:8000
```

Run the React dashboard:

```bash
cd frontend
npm run dev
```

Open `http://localhost:5173`.

The Vite dev server proxies `/plaid`, `/transactions`, `/splitwise`, and other backend APIs to `http://localhost:8000`. The original `app/static/index.html` remains available as a fallback at the backend root.

## Splitwise Setup

Recommended for this private beta: use a Splitwise API key.

For Splitwise, generate an API key from your Splitwise app page and paste it into
`SPLITWISE_API_KEY`. The app sends it as `Authorization: Bearer <api key>`.

```bash
SPLITWISE_API_KEY="..."
SPLITWISE_ACCESS_TOKEN=""
SPLITWISE_AUTH_SCHEME="Bearer"
```

Optional OAuth 1.0 fallback is supported if you prefer consumer key/secret plus user tokens:

```bash
SPLITWISE_CONSUMER_KEY="..."
SPLITWISE_CONSUMER_SECRET="..."
SPLITWISE_OAUTH_CALLBACK_URL="http://localhost:8000/splitwise/oauth/callback"
SPLITWISE_OAUTH_TOKEN="..."
SPLITWISE_OAUTH_TOKEN_SECRET="..."
```

To generate OAuth 1.0 user tokens:

1. Start the app.
2. Open `http://localhost:8000/splitwise/oauth/authorize`.
3. Visit the returned `authorize_url` and approve the app in Splitwise.
4. Splitwise redirects to `/splitwise/oauth/callback`; copy the returned values into `.env`:

```bash
SPLITWISE_OAUTH_TOKEN="..."
SPLITWISE_OAUTH_TOKEN_SECRET="..."
```

The old bearer-token path still works if `SPLITWISE_ACCESS_TOKEN` is set.

## Plaid Setup

For Plaid Transactions webhooks in local development, expose the backend with
ngrok and set `PLAID_WEBHOOK_URL` before creating a Plaid Link token:

```bash
ngrok http 8000
PLAID_WEBHOOK_URL="https://YOUR-NGROK-DOMAIN.ngrok-free.app/plaid/webhook"
```

Plaid sends `TRANSACTIONS/SYNC_UPDATES_AVAILABLE` to `/plaid/webhook`. The app
then syncs the matching linked item. After a new Item is created, the app also
runs one initial transactions sync so future `SYNC_UPDATES_AVAILABLE` webhooks
can fire.

## OpenAI Setup

OpenAI is optional. Without `OPENAI_API_KEY`, deterministic parsing and Button mode still work.

```bash
OPENAI_API_KEY=""
OPENAI_MODEL="gpt-4.1-mini"
```

OpenAI powers Telegram AI chat interpretation and custom split parsing. The app still validates everything deterministically and always requires confirmation before posting.

## Telegram Setup

Telegram is optional. Leave Telegram fields blank if you only want dashboard review and console logging.

Create a Telegram bot with BotFather, paste the bot token into `.env`, and send at least one message to the bot from the Telegram chat you want to use. Then get the chat id:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates"
```

Paste the chat id into `.env` and set a webhook secret:

```bash
TELEGRAM_BOT_TOKEN="..."
TELEGRAM_CHAT_ID="..."
TELEGRAM_WEBHOOK_SECRET="choose-a-long-random-string"
```

Expose the backend locally with ngrok:

```bash
ngrok http 8000
```

Register the Telegram webhook using your ngrok URL and the same secret:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook?url=https://YOUR-NGROK-DOMAIN.ngrok-free.app/telegram/webhook?secret=$TELEGRAM_WEBHOOK_SECRET"
```

If `TELEGRAM_WEBHOOK_SECRET` is blank, `/telegram/webhook` remains open for local backward compatibility. Always use a secret when exposing the app publicly.

Current Telegram features:

- Button mode for deterministic review
- AI chat mode for natural-language expense instructions
- People splits and group splits
- Equal, exact amount, percentage, and share-based custom splits
- Confirmation before posting
- Undo after personal or Splitwise-posted actions
- AI prompt guardrails for unsafe or irrelevant messages
- AI fallback learning: if AI fails and you complete through Button mode, the app remembers that correction for future prompts

## First-Run Verification Checklist

1. Start the backend with `make run`.
2. Start the frontend with `cd frontend && npm run dev`.
3. Open `http://localhost:5173`.
4. Click **Open Plaid Link** and connect a sandbox institution.
5. Click **Manual sync**.
6. Confirm pending transactions appear in the dashboard.
7. Search Splitwise friends by name.
8. Mark one transaction personal or create a draft before testing real posting.
9. If Telegram is configured, confirm the bot receives a review message.
10. Confirm `.env` is not staged with `git status`.

## Railway Deployment

Railway injects a `PORT` variable for the web service and a Postgres
`DATABASE_URL` when you add a Postgres service. This app keeps SQLite as the
local default and uses `DATABASE_URL` automatically in production.

Railway’s FastAPI guide recommends deploying from GitHub and defining a start
command for the server; Railway’s public networking docs expect the app to bind
to `0.0.0.0` and the Railway-provided port:

- https://docs.railway.com/guides/fastapi
- https://docs.railway.com/deploy/exposing-your-app

### 1. Create Railway services

1. Push this repo to GitHub.
2. In Railway, create a new project from the GitHub repo.
3. Add a Railway Postgres service.
4. Set the app service `DATABASE_URL` to the Postgres connection string Railway
   provides. Prefer the private/internal database URL when the app and database
   are in the same Railway project.

### 2. Set Railway app variables

```bash
ENVIRONMENT="production"
ENABLE_DOCS=false
DATABASE_URL="${{ Postgres.DATABASE_URL }}"
APP_SECRET_KEY="generated-fernet-key"
FRONTEND_ORIGIN="https://YOUR-RAILWAY-APP.up.railway.app"
DASHBOARD_USERNAME="your-private-beta-username"
DASHBOARD_PASSWORD="a-long-random-password"
DASHBOARD_API_TOKEN="a-long-random-api-token"

PLAID_CLIENT_ID="..."
PLAID_SECRET="..."
PLAID_ENV="sandbox"
PLAID_WEBHOOK_URL="https://YOUR-RAILWAY-APP.up.railway.app/plaid/webhook"

SPLITWISE_API_KEY="..."

TELEGRAM_BOT_TOKEN="..."
TELEGRAM_CHAT_ID="..."
TELEGRAM_WEBHOOK_SECRET="a-long-random-webhook-secret"

OPENAI_API_KEY="..."
OPENAI_MODEL="gpt-4.1-mini"
```

`/health`, `/telegram/webhook`, and `/plaid/webhook` remain public. Telegram is
still protected by `TELEGRAM_WEBHOOK_SECRET`. All dashboard/API routes require
either Basic auth with `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD`, or
`Authorization: Bearer <DASHBOARD_API_TOKEN>`.

### 3. Start command

Railway can use either the Dockerfile or this start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

`railway.json` includes this command for non-Docker Railway builds.

### 4. Database initialization

For local development, the app still runs SQLAlchemy `create_all()` on startup
as a convenience for SQLite. In production, startup does not run `create_all()`;
use Alembic migrations instead.

Run migrations locally:

```bash
make migrate
```

If your local SQLite database already existed before Alembic was added and the
tables are already present, mark it as current once:

```bash
make stamp-db
```

Run migrations on Railway after setting `DATABASE_URL`:

```bash
railway run alembic upgrade head
```

If you deploy with a release/predeploy step, use:

```bash
alembic upgrade head
```

Create future migrations after model changes:

```bash
MESSAGE="add table name" make revision
```

### 5. Cost controls

Railway documents usage limits and cost controls for avoiding runaway spend:

- https://docs.railway.com/pricing/cost-control
- https://docs.railway.com/projects/project-usage

For this private beta, set a Railway project usage limit around `$5`:

1. Open the Railway project.
2. Go to project usage/cost controls.
3. Set a hard usage limit around `$5`.
4. Keep preview/PR deployments disabled unless needed.
5. Use Railway private networking for app-to-Postgres traffic when available.

## Local Workflow

1. Click **Open Plaid Link**.
2. Link a Plaid sandbox institution or your real Chase card once you are ready for development/production mode.
3. Click **Manual sync** or wait for a Plaid webhook.
4. Review transactions under **Review transactions**.
5. Search and select Splitwise friends by name.
6. Mark a transaction as personal, create a draft, or split equally.

Each transaction card shows a recommendation-only classification:
`likely_personal`, `likely_shared`, or `unsure`. The app never auto-posts based
on this suggestion.

## Useful API endpoints

### Create Plaid Link token

```bash
curl -X POST http://localhost:8000/plaid/link-token
```

### Manually sync all Plaid items

```bash
curl -X POST http://localhost:8000/plaid/sync
```

### List transactions waiting for review

```bash
curl "http://localhost:8000/transactions?status=ask_user"
```

### Mark personal

```bash
curl -X POST http://localhost:8000/transactions/1/personal
```

### Search Splitwise friends

```bash
curl "http://localhost:8000/splitwise/friends?q=rahul"
```

### Split equally with selected friends

```bash
curl -X POST http://localhost:8000/transactions/1/split/equal \
  -H "Content-Type: application/json" \
  -d '{"friend_user_ids":[12345,67890],"confirm":true}'
```

### Create draft only, without posting to Splitwise

```bash
curl -X POST http://localhost:8000/transactions/1/split/equal \
  -H "Content-Type: application/json" \
  -d '{"friend_user_ids":[12345,67890],"confirm":false}'
```

## Pending transaction behavior

The app blocks posting pending card transactions by default. This avoids posting a Splitwise expense before the final card amount settles.

To override for a single request:

```json
{"friend_user_ids":[12345],"confirm":true,"post_pending":true}
```

To override globally in `.env`:

```bash
ALLOW_POSTING_PENDING_TRANSACTIONS=true
```

## Splitwise payload behavior

The app uses Splitwise's custom-share format:

```json
{
  "cost": "60.00",
  "description": "Dinner",
  "group_id": 0,
  "users__0__user_id": 111,
  "users__0__paid_share": "60.00",
  "users__0__owed_share": "20.00",
  "users__1__user_id": 222,
  "users__1__paid_share": "0.00",
  "users__1__owed_share": "20.00",
  "users__2__user_id": 333,
  "users__2__paid_share": "0.00",
  "users__2__owed_share": "20.00"
}
```

The payer is the authenticated Splitwise user returned by `/get_current_user`.

## Logging & Observability

ExpenseOps uses structured event logs for local debugging and Railway private
beta operations.

Local logs are readable key/value lines. Production logs are compact JSON so
Railway can display and filter them cleanly. The default production level is
`INFO`; local development allows `DEBUG`.

Every HTTP request gets a `trace_id`. Telegram webhook handling reuses that
trace id across AI parsing, entity resolution, memory lookup, Splitwise posting,
undo, and related failures. When debugging an AI chat issue, search Railway logs
for one `trace_id` and inspect events such as:

- `telegram_ai_started`
- `ai_memory_retrieved`
- `ai_intent_extraction_success`
- `ai_entity_resolution_ambiguous`
- `ai_custom_split_validation_failed`
- `telegram_ai_fallback`
- `telegram_split_posted`
- `telegram_custom_split_posted`

Safe logging policy:

- Do not log Plaid access tokens, Splitwise tokens, OpenAI raw prompts or raw
  responses, auth headers, webhook verification payloads, passwords, or secrets.
- Production logs do not include raw Telegram user text.
- Logs focus on business events, state transitions, external API calls, and safe
  reason codes like `parse_failed`, `unknown_person`, `duplicate_post`,
  `split_validation_failed`, and `plaid_verification_failed`.

## Tests

```bash
make test
```

## Security checklist before using real Chase data

- Treat real Chase data as private beta production data.
- Keep this app private; do not expose it publicly without dashboard/API authentication.
- Use Plaid OAuth/Link only. Do not scrape Chase and do not store Chase credentials.
- Never commit `.env` or paste real tokens into GitHub issues, screenshots, or logs.
- Use Railway variables or another secret manager for deployment secrets.
- Use PostgreSQL for deployment, not SQLite.
- Enable Plaid webhook verification.
- Keep `confirm=true` only after explicit user action.
- Keep duplicate-posting guard enabled.
- Set Railway usage limits before inviting beta users.

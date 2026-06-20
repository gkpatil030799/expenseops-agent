# ExpenseOps Agent

Ever paid for dinner or an Uber, said “I’ll split it later,” and then completely forgot?

Most shared expenses do not get lost because they are complicated. They get lost because they are easy to postpone.

ExpenseOps is designed to eliminate that friction.

Instead of making users hunt through transaction histories, ExpenseOps automatically surfaces card transactions that may need attention and guides them through a simple review workflow. Shared expense decisions, transaction review, and split preparation all happen in one place.

What makes the experience different is the AI layer. Users can review expenses through Telegram using natural language:

```text
"Split this with Alex and Priya 50-50 in Weekend Crew."
"This was personal."
"Ignore this transaction."
```

## How It Works

1. A card transaction appears.
2. ExpenseOps brings it into the review dashboard.
3. The user decides whether it is personal or shared.
4. The user can also reply through Telegram using buttons or natural language.
5. The AI-assisted review flow interprets the user’s intent and prepares the next step.
6. If the transaction is personal, it is marked resolved.
7. If it is shared, the user can choose friends, groups, and split rules.
8. ExpenseOps prepares the shared expense and waits for confirmation.
9. After approval, the expense can be posted to the shared-expense workflow.
10. Already resolved transactions are not repeatedly shown or re-notified.

## What This Project Demonstrates

Although the user experience is simple, the system handles several real-world engineering challenges behind the scenes:

- AI-assisted Telegram review and natural-language expense instructions
- Bank/card transaction ingestion
- Webhook-based transaction updates
- Safe transaction review and state management
- Duplicate notification prevention
- Approval-first shared expense posting
- A React dashboard for visibility and control
- Sandbox testing for safe financial workflow validation
- Scenario-based QA and reliability testing

## Why It Exists

Shared expense tools are useful only if the workflow is fast, safe, and hard to
double-post. ExpenseOps experiments with a practical human-in-the-loop system:

1. Card transactions arrive through Plaid.
2. ExpenseOps determines whether review is needed.
3. The user decides personal vs shared.
4. Shared expenses can be drafted or posted after confirmation.
5. Telegram gives a fast mobile review loop.
6. Sandbox Lab makes the Plaid/webhook/notification flow testable without using
   real money data.

## Core Features

- Connect a bank or card account through Plaid Link
- Exchange Plaid `public_token` for encrypted `access_token` and `item_id`
- Run manual transaction sync
- Receive Plaid transaction webhooks
- Upsert added/modified/removed transactions idempotently
- Review pending transactions in the dashboard
- Mark transactions personal
- Create shared split drafts
- Confirm shared expense posting
- Receive Telegram review notifications
- Use Telegram buttons/replies for review actions
- Avoid re-notifying personal, posted, removed, or already-claimed transactions
- Exercise the whole workflow through Sandbox Lab
- Run Scenario Runner test cases
- Run Reliability Suite failure/race-condition checks

## Architecture Overview

Production-style Plaid flow:

```text
Plaid Link
  -> public_token exchange
  -> encrypted access_token + item_id stored
  -> initial /transactions/sync initializes cursor
  -> Plaid webhook hits /plaid/webhook
  -> webhook verification
  -> sync added/modified/removed transactions
  -> idempotent upsert
  -> review eligibility check
  -> atomic Telegram notification claim
  -> Telegram notification
  -> user action
  -> personal / shared_draft / posted state
```

Sandbox Lab flow:

```text
Plaid Sandbox
  -> create test transaction
  -> attach/fire Sandbox webhook
  -> /plaid/webhook receives webhook
  -> sync imports transaction
  -> Telegram sends at most once
  -> Scenario Runner / Reliability Suite validate behavior
```

Key safety ideas:

- Production Plaid webhooks require verification.
- Telegram review notifications are claimed before sending.
- Resolved transactions are not re-notified.
- Sandbox Lab is guarded by `ENABLE_EXPENSEOPS_SANDBOX_LAB=true` and
  `PLAID_ENV=sandbox`.
- Sandbox fault injection is scoped to Sandbox Lab traces only.

## Tech Stack

Backend:

- Python 3.11+
- FastAPI
- SQLAlchemy
- Alembic
- Pydantic / pydantic-settings
- Plaid Python SDK
- Telegram Bot API via HTTP
- Splitwise-compatible shared expense workflow
- SQLite locally, PostgreSQL-compatible models for deployment

Frontend:

- React
- Vite
- TypeScript
- TailwindCSS
- Lucide icons

Testing and QA:

- pytest
- ruff
- Vitest
- Sandbox Lab
- Scenario Runner
- Reliability Suite

## Repository Structure

```text
app/
  api/                  FastAPI routes for Plaid, transactions, Telegram, Splitwise
  services/             Plaid, Telegram, transaction, split, AI, and notification logic
  models.py             SQLAlchemy models
  schemas.py            Pydantic API schemas
  security.py           Fernet encryption helpers
  static/               Fallback static UI

frontend/
  src/                  Main React dashboard

sandbox/
  backend/              Sandbox Lab API, orchestrator, scenarios, reliability runner
  frontend/             Sandbox Lab React UI
  scenarios/            JSON Scenario Runner definitions
  reliability/          JSON Reliability Suite definitions
  logs/                 Local runtime JSONL logs, ignored by git except .gitkeep
  state/                Local Sandbox Lab state, ignored by git except .gitkeep

tests/                  Backend unit/integration-style tests
alembic/                Database migrations
docs/                   Additional architecture notes, if present
```

## Prerequisites

- Python 3.11 or newer
- Node.js 20 or newer
- npm 10 or newer
- Git
- Plaid developer account
- Telegram bot token from BotFather, optional but recommended
- ngrok or another HTTPS tunnel for local webhook testing
- Splitwise credentials or API key if you want to exercise posting workflows
- OpenAI API key only if you want AI-assisted Telegram parsing

## Local Setup

Clone the repo:

```bash
git clone https://github.com/YOUR_USERNAME/expenseops-agent.git
cd expenseops-agent
```

Create a Python virtual environment and install backend dependencies:

```bash
python3 -m venv .venv
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
```

Generate an encryption key and paste it into `APP_SECRET_KEY`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Run migrations:

```bash
make migrate
```

For local SQLite development, the app also creates missing tables on startup.
For production deployments, use Alembic migrations rather than `create_all`.

## Environment Variables

Important local variables:

```env
APP_NAME="ExpenseOps Agent"
DATABASE_URL="sqlite:///./expenseops.db"
ENVIRONMENT="local"
APP_ENV="local"
FRONTEND_ORIGIN="http://localhost:5173"
APP_SECRET_KEY="paste-a-generated-fernet-key-here"

PLAID_CLIENT_ID=""
PLAID_SECRET=""
PLAID_ENV="sandbox"
PLAID_WEBHOOK_URL="https://your-ngrok-domain.ngrok-free.app/plaid/webhook"
PLAID_COUNTRY_CODES="US"
PLAID_PRODUCTS="transactions"
PLAID_DAYS_REQUESTED=90

ENABLE_EXPENSEOPS_SANDBOX_LAB="true"
PLAID_VERIFY_WEBHOOKS=false
PLAID_VERIFY_WEBHOOKS_IN_SANDBOX="false"
ALLOW_UNVERIFIED_PLAID_WEBHOOKS_FOR_LOCAL_TEST="false"

TELEGRAM_BOT_TOKEN=""
TELEGRAM_CHAT_ID=""
TELEGRAM_WEBHOOK_SECRET=""

SPLITWISE_API_KEY=""
SPLITWISE_ACCESS_TOKEN=""
SPLITWISE_AUTH_SCHEME="Bearer"

OPENAI_API_KEY=""
OPENAI_MODEL="gpt-4.1-mini"
```

Notes:

- Never commit `.env`.
- `APP_SECRET_KEY` encrypts Plaid access tokens at rest.
- `PLAID_ENV=sandbox` and `PLAID_ENV=production` are separate Plaid worlds.
- Sandbox Lab should be disabled outside local development.
- Set `PLAID_VERIFY_WEBHOOKS_IN_SANDBOX` to true only when you want to test
  Plaid webhook JWT verification through Sandbox Lab.
- `ALLOW_UNVERIFIED_PLAID_WEBHOOKS_FOR_LOCAL_TEST` must never be true in a real
  deployed production environment.
- Production Plaid webhooks should be verified.
- `FRONTEND_ORIGIN` must match the Vite URL you open in the browser. Vite
  usually starts on `http://localhost:5173`; if that port is busy it may use
  `5174`.

## Running Locally

Start the backend:

```bash
source .venv/bin/activate
make run
```

`make run` starts uvicorn with reload on port `8000`.

Start the frontend:

```bash
cd frontend
npm run dev
```

Open the Vite URL shown in the terminal, usually:

```text
http://localhost:5173
```

If Vite chooses `5174`, update `FRONTEND_ORIGIN` in `.env` and restart the
backend.

Useful health checks:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/sandbox/status
```

## Webhook Setup With ngrok

Start an HTTPS tunnel to the backend:

```bash
ngrok http 8000
```

With a reserved ngrok domain:

```bash
ngrok http --domain=<your-domain>.ngrok-free.dev 8000
```

Set:

```env
PLAID_WEBHOOK_URL="https://<your-domain>.ngrok-free.dev/plaid/webhook"
```

ngrok's local request inspector is available at:

```text
http://127.0.0.1:4040
```

Plaid webhooks should show as:

```text
POST /plaid/webhook 200 OK
```

Telegram webhook callbacks, if configured, should show as:

```text
POST /telegram/webhook?secret=... 200 OK
```

## Plaid Setup

Sandbox:

1. Create a Plaid developer account.
2. Set `PLAID_CLIENT_ID` and `PLAID_SECRET`.
3. Set `PLAID_ENV=sandbox`.
4. Set `PLAID_WEBHOOK_URL` to your ngrok `/plaid/webhook` URL.
5. Use the dashboard Plaid Link flow or Sandbox Lab to create items and test
   transaction sync.

Production-like local testing:

1. Set `PLAID_ENV=production`.
2. Use Plaid Link to connect a real account.
3. The frontend calls `/plaid/link-token`.
4. Plaid returns a `public_token`.
5. The backend exchanges it at `/plaid/exchange-public-token`.
6. The app stores encrypted `access_token` and `item_id`.
7. Initial sync stores the cursor.
8. Future Plaid webhooks use `item_id` to trigger `/transactions/sync`.

Important:

- Do not call Plaid Sandbox endpoints in production.
- Sandbox Items and Production Items are separate.
- Production webhooks require valid `Plaid-Verification` JWT verification.

## Telegram Setup

Telegram is optional, but it is part of the intended MVP workflow.

1. Create a bot with BotFather.
2. Put the token in `TELEGRAM_BOT_TOKEN`.
3. Send a message to the bot from your Telegram account.
4. Get your chat id:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates"
```

5. Set:

```env
TELEGRAM_CHAT_ID="..."
TELEGRAM_WEBHOOK_SECRET="choose-a-long-random-string"
```

6. Register the webhook:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook?url=https://<your-domain>.ngrok-free.dev/telegram/webhook?secret=$TELEGRAM_WEBHOOK_SECRET"
```

Telegram notifications are sent only for actionable review transactions.
Personal, posted, removed, ignored, already-claimed, and Splitwise-posted
transactions should not be re-notified.

## Splitwise / Shared Expense Setup

ExpenseOps supports a Splitwise-style shared expense workflow.

Recommended local setup:

```env
SPLITWISE_API_KEY=""
SPLITWISE_ACCESS_TOKEN=""
SPLITWISE_AUTH_SCHEME="Bearer"
```

Optional OAuth 1.0 fallback:

```env
SPLITWISE_CONSUMER_KEY=""
SPLITWISE_CONSUMER_SECRET=""
SPLITWISE_OAUTH_CALLBACK_URL="http://localhost:8010/splitwise/oauth/callback"
SPLITWISE_OAUTH_TOKEN=""
SPLITWISE_OAUTH_TOKEN_SECRET=""
```

Pending card transactions are blocked from posting by default. This prevents
posting a split before the final card amount settles.

## Sandbox Lab

Sandbox Lab is a developer-only testing UI for Plaid Sandbox flows.

Required settings:

```env
ENABLE_EXPENSEOPS_SANDBOX_LAB="true"
PLAID_ENV="sandbox"
```

Open:

```text
http://localhost:5173/sandbox-lab
```

If your frontend runs on `5174`, use:

```text
http://localhost:5174/sandbox-lab
```

Sandbox Lab can:

- Create or reuse a Plaid Sandbox Item
- Initialize sync cursor
- Create Plaid Sandbox transactions
- Detach webhook before create-only tests
- Attach webhook before webhook tests
- Fire Plaid Sandbox webhooks
- Run manual sync
- Run a full E2E smoke flow
- Show an event timeline
- Show raw JSON responses for debugging

Sandbox Lab writes local runtime events to `sandbox/logs/*.jsonl`; these files
are ignored by git.

## Scenario Runner

Scenario Runner turns Sandbox Lab into repeatable QA.

Definitions live in:

```text
sandbox/scenarios/
```

Included scenarios:

- `create_only_no_import`
- `manual_sync_basic`
- `webhook_basic`
- `full_e2e_smoke`
- `refund_manual_sync`
- `subscription_manual_sync`

Use it from:

```text
/sandbox-lab -> Scenario Runner
```

Each scenario:

1. Creates a trace id.
2. Executes a flow.
3. Polls Sandbox events.
4. Runs assertions.
5. Persists a local JSONL result.
6. Displays pass, fail, partial, or error.

Run All can hit Plaid Sandbox rate limits. During active development, run
individual scenarios first.

## Reliability Suite

Reliability Suite validates safe behavior under messy conditions.

Definitions live in:

```text
sandbox/reliability/
```

Included checks cover:

- Duplicate webhooks
- Repeated manual sync
- Concurrent sync attempts
- Webhook observation timeout
- Telegram failure simulation
- Plaid sync failure simulation
- Cursor missing recovery
- Loop guard behavior

Use it from:

```text
/sandbox-lab -> Reliability Suite
```

Some reliability tests intentionally return `partial` or warning-style results
when the app safely handles a simulated failure. This is expected.

## End-To-End Local Smoke Test

1. Start ngrok:

```bash
ngrok http 8000
```

2. Set `PLAID_WEBHOOK_URL` in `.env` to the ngrok `/plaid/webhook` URL.
3. Start the backend:

```bash
make run
```

4. Start the frontend:

```bash
cd frontend
npm run dev
```

5. Confirm Sandbox status:

```bash
curl http://127.0.0.1:8000/api/sandbox/status
```

6. Open `/sandbox-lab`.
7. Create or reuse a Sandbox Item.
8. Run Init Sync if needed.
9. Create a Sandbox transaction.
10. Click **Ask Plaid to Fire Webhook**.
11. Confirm ngrok shows `POST /plaid/webhook 200 OK`.
12. Confirm the Event Timeline shows:

```text
plaid_webhook_received
plaid_transactions_sync_started
plaid_transactions_sync_completed
```

13. Confirm Telegram sends at most one review notification.
14. Click a Telegram button and confirm Telegram webhook returns `200 OK` if
    Telegram webhook mode is configured.

## Testing

Backend lint:

```bash
make lint
```

Backend tests:

```bash
make test
```

Frontend build:

```bash
cd frontend
npm run build
```

Frontend tests:

```bash
cd frontend
npm run test
```

The backend test suite includes production-risk paths such as Plaid webhook
handling, transaction sync, notification idempotency, Telegram routes, Splitwise
payload handling, Sandbox Lab, Scenario Runner, and Reliability Suite behavior.

## Useful API Endpoints

Create Plaid Link token:

```bash
curl -X POST http://localhost:8000/plaid/link-token
```

Manually sync all Plaid Items:

```bash
curl -X POST http://localhost:8000/plaid/sync
```

List transactions waiting for review:

```bash
curl "http://localhost:8000/transactions?status=ask_user"
```

Mark a transaction personal:

```bash
curl -X POST http://localhost:8000/transactions/1/personal
```

Search Splitwise friends:

```bash
curl "http://localhost:8000/splitwise/friends?q=alex"
```

Create an equal split draft:

```bash
curl -X POST http://localhost:8000/transactions/1/split/equal \
  -H "Content-Type: application/json" \
  -d '{"friend_user_ids":[12345,67890],"confirm":false}'
```

## Troubleshooting

### `Address already in use`

Port `8000` is already occupied. Find the process:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

Stop it or run the backend on another port.

### Frontend cannot reach backend

Make sure:

- backend is running on port `8000`
- frontend dev server is running
- `FRONTEND_ORIGIN` matches the actual Vite URL
- the browser URL is the Vite URL, not the backend URL, for the React dashboard

### Sandbox Lab disabled

Check:

```env
ENABLE_EXPENSEOPS_SANDBOX_LAB="true"
PLAID_ENV="sandbox"
```

Restart the backend after changing `.env`.

### `Create a sandbox item first`

Create or reuse a Sandbox Item, then run init sync:

```text
/sandbox-lab -> Overview -> Create Sandbox Item
/sandbox-lab -> Manual Sync Flow or Webhook Flow -> Init Sync if prompted
```

### Webhook fired but not received

Check:

- ngrok is running
- `PLAID_WEBHOOK_URL` is exactly `https://.../plaid/webhook`
- backend is on port `8000`
- ngrok inspector shows `POST /plaid/webhook`
- Plaid Item has the expected webhook attached

### `403 Forbidden` on `/plaid/webhook`

Usually means webhook verification failed.

For production, fix verification. Do not bypass it.

For explicit local production webhook testing only, the bypass requires all of:

```env
PLAID_ENV="production"
ALLOW_UNVERIFIED_PLAID_WEBHOOKS_FOR_LOCAL_TEST="true"
APP_ENV="local"
# or ENVIRONMENT="local"
```

The bypass must never be enabled in deployed production.

### Telegram duplicate notifications

Check whether the same real account was linked multiple times as multiple Plaid
Items. ExpenseOps has duplicate notification guards, but connected account
management is still a known improvement area.

### Plaid Sandbox rate limits

Plaid Sandbox can rate-limit `/sandbox/transactions/create`. Wait a bit and run
individual scenarios instead of Run All.

### Cursor missing

Run init sync for that Item before expecting webhook-driven sync.

## Logging And Observability

ExpenseOps emits structured events for important lifecycle transitions:

- Plaid webhook received / verified / failed
- Plaid sync started / completed / failed
- transaction upsert and classification
- notification claim / skipped / sent
- Telegram callback handling
- Splitwise post success/failure
- Sandbox Lab scenario and reliability events

Safe logging policy:

- Do not log Plaid access tokens.
- Do not log Plaid secrets.
- Do not log Telegram bot tokens.
- Do not log full Plaid webhook payloads.
- Do not log raw OpenAI prompts/responses in production logs.
- Prefer safe reason codes and IDs.

## Production Readiness Notes

Before using real bank production data:

- Keep the app private behind auth.
- Use `ENVIRONMENT=production`.
- Set `APP_ENV=production` in deployed environments.
- Use managed PostgreSQL or another production database instead of SQLite.
- Run `alembic upgrade head` before starting the app process.
- Use a stable HTTPS webhook URL for Plaid and Telegram callbacks.
- Keep Plaid webhook verification enabled.
- Keep Sandbox Lab disabled for deployed MVPs.
- Keep local webhook bypass disabled.
- Store secrets in a deployment secret manager, not in git.
- Keep Plaid access tokens encrypted.
- Monitor webhook verification failures.
- Monitor duplicate notification skips.
- Validate the real Plaid production webhook flow before deployment.
- Do not deploy `.env`, local SQLite databases, Sandbox Lab logs, or Sandbox Lab
  state files.

Required production environment values:

```env
APP_ENV="production"
ENVIRONMENT="production"
APP_SECRET_KEY="generated-fernet-key"
DATABASE_URL="postgresql+psycopg://..."
PLAID_CLIENT_ID="..."
PLAID_SECRET="..."
PLAID_ENV="production"
PLAID_WEBHOOK_URL="https://your-deployed-domain.example/plaid/webhook"
TELEGRAM_BOT_TOKEN="..."
TELEGRAM_CHAT_ID="..."
TELEGRAM_WEBHOOK_SECRET="long-random-secret"
TELEGRAM_ALLOWED_USER_ID="your-telegram-user-id"
ENABLE_EXPENSEOPS_SANDBOX_LAB="false"
ALLOW_UNVERIFIED_PLAID_WEBHOOKS_FOR_LOCAL_TEST="false"
```

Startup order for deployment:

```bash
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
```

Deployment is future work for this repo. The current README is focused on
running and testing the local/private-beta MVP.

## Known Limitations / Future Work

- Some MVP files are large and should be split before team-scale development.
- Connected account management is needed to prevent users from linking the same
  real account multiple times.
- Sandbox Lab event storage uses local JSONL files, which are not an enterprise
  event store.
- A background job queue would be better for high-volume production webhook
  processing.
- Reminder/digest notifications are intentionally not implemented yet.
- Production deployment should be validated against a stable deployed webhook
  environment, not only local ngrok.
- More dashboard components can be extracted from the main frontend file.

## Project Status

ExpenseOps is a working MVP and private-beta style prototype. It demonstrates
real integration depth, production-safety thinking, and repeatable QA tooling.
It is not yet a polished SaaS product. The most important next engineering work
is modular cleanup, connected account management, and deployment hardening.

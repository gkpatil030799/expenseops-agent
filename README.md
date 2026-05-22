# ExpenseOps Agent

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

This scaffold uses **Plaid + Splitwise API directly**, not a Splitwise MCP. The app is built as a backend automation system first, with a small local web UI for testing.

## What is included

- FastAPI backend
- Plaid Link token creation
- Plaid public-token exchange
- Plaid `/transactions/sync` ingestion
- Plaid webhook receiver that queues a sync
- Local SQLite by default, PostgreSQL-compatible SQLAlchemy models
- Encrypted Plaid access-token storage
- Splitwise friends/groups lookup
- Splitwise custom-share `create_expense` posting
- Equal and custom split endpoints
- Human approval-first workflow
- Duplicate posting guard
- Simple local web UI at `/`
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

## 1. Install

```bash
cd expenseops-agent
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## 2. Configure

```bash
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the generated key into `APP_SECRET_KEY` in `.env`.

Then fill in:

```bash
PLAID_CLIENT_ID="..."
PLAID_SECRET="..."
PLAID_ENV="sandbox"
SPLITWISE_API_KEY="..."
```

For Splitwise, generate an API key from your Splitwise app page and paste it into
`SPLITWISE_API_KEY`. The app sends it as `Authorization: Bearer <api key>`.

Optional OAuth 1.0 fallback:

1. Start the app.
2. Open `http://localhost:8000/splitwise/oauth/authorize`.
3. Visit the returned `authorize_url` and approve the app in Splitwise.
4. Splitwise redirects to `/splitwise/oauth/callback`; copy the returned values into `.env`:

```bash
SPLITWISE_OAUTH_TOKEN="..."
SPLITWISE_OAUTH_TOKEN_SECRET="..."
```

The old bearer-token path still works if `SPLITWISE_ACCESS_TOKEN` is set.

## 3. Run locally

### Backend

```bash
make run
```

Backend API and fallback HTML UI:

```text
http://localhost:8000
```

### React frontend

```bash
cd frontend
npm install
npm run dev
```

Open:

```text
http://localhost:5173
```

The Vite dev server proxies `/plaid`, `/transactions`, and `/splitwise` to
`http://localhost:8000`, so the FastAPI APIs stay unchanged. The original
`app/static/index.html` remains available as a fallback at the backend root.

### Telegram webhook with ngrok

Set a local webhook secret in `.env`:

```bash
TELEGRAM_WEBHOOK_SECRET="choose-a-long-random-string"
```

Start the backend:

```bash
make run
```

Expose the backend with ngrok:

```bash
ngrok http 8000
```

Register the Telegram webhook using your ngrok URL and the same secret:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook?url=https://YOUR-NGROK-DOMAIN.ngrok-free.app/telegram/webhook?secret=$TELEGRAM_WEBHOOK_SECRET"
```

If `TELEGRAM_WEBHOOK_SECRET` is blank, `/telegram/webhook` remains open for local
backward compatibility. Use a secret when exposing the app through ngrok.

## 4. Local workflow

1. Click **Open Plaid Link**.
2. Link a Plaid sandbox institution or your real Chase card once you are ready for development/production mode.
3. Click **Manual sync** or wait for a Plaid webhook.
4. Review transactions under **Review transactions**.
5. Search and select Splitwise friends by name.
6. Mark a transaction as personal, create a draft, or split equally.

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

## Tests

```bash
make test
```

## Security checklist before using real Chase data

- Keep this app private; do not expose it publicly without authentication.
- Use Plaid OAuth/Link only. Do not scrape Chase and do not store Chase credentials.
- Use a real secret manager for deployment.
- Use PostgreSQL for deployment.
- Enable Plaid webhook verification.
- Keep `confirm=true` only after explicit user action.
- Keep duplicate-posting guard enabled.

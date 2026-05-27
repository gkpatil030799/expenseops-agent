# ExpenseOps Sandbox Lab

Sandbox Lab adds Plaid Sandbox testing plumbing inside this `sandbox/` folder.
It uses real Plaid Sandbox APIs and the existing ExpenseOps Plaid webhook/sync
pipeline. It does not use production Plaid.

Phase 2 adds a React/Vite developer UI at `/sandbox-lab`. The older `/sandbox`
and `/dev/sandbox` aliases are kept for compatibility.

## What It Tests

```text
Plaid Sandbox Item
→ sandbox transaction create
→ sandbox webhook fire
→ existing ExpenseOps Plaid webhook
→ existing transaction sync
→ ExpenseOps transaction processing
→ Sandbox Lab event log
```

## Required Environment

```bash
ENABLE_EXPENSEOPS_SANDBOX_LAB=true
PLAID_ENV=sandbox
PLAID_CLIENT_ID="..."
PLAID_SECRET="..."
PLAID_WEBHOOK_URL="https://your-ngrok-url.ngrok-free.app/plaid/webhook"
```

If `PLAID_WEBHOOK_URL` is not set, Sandbox Lab also accepts:

```bash
SANDBOX_PUBLIC_WEBHOOK_URL="https://your-ngrok-url.ngrok-free.app/plaid/webhook"
```

This repository currently mounts the Plaid webhook at `/plaid/webhook`. If your
deployment/proxy exposes it under `/api/plaid/webhook`, configure the public URL
accordingly. Do not hardcode ngrok URLs in code.

## Local Run

Start the backend:

```bash
make run
```

Expose it with ngrok if needed:

```bash
ngrok http 8000
```

Then run the full backend test:

```bash
curl -X POST http://localhost:8000/api/sandbox/run-e2e
```

View Sandbox Lab events:

```bash
curl http://localhost:8000/api/sandbox/events
```

You can also inspect:

```bash
sandbox/logs/sandbox_events.jsonl
```

## Frontend UI

Start the existing React frontend:

```bash
cd frontend
npm install
npm run dev
```

Open:

```text
http://localhost:5173/sandbox-lab
```

The page shows:

- A polished Sandbox-only header and safety badge.
- Status overview cards for Plaid env, webhook URL, sandbox item, cursor,
  latest run, and Telegram observation.
- Internal navigation for:
  - Overview
  - Webhook Flow
  - Manual Sync Flow
  - Full E2E
  - Event Explorer
- Webhook Flow for production-style testing:
  create in Plaid Sandbox → ask Plaid to fire webhook → observe webhook receipt
  → observe webhook-triggered sync → observe Telegram.
- Manual Sync Flow for direct `/transactions/sync` testing:
  create in Plaid Sandbox → pull `/transactions/sync` → inspect import counts
  → observe Telegram.
- Full E2E for a one-click smoke test with fallback sync clearly labeled as a
  warning, not a failure.
- Event Explorer with trace filtering, event filtering, auto-refresh, copy
  buttons, expandable payload JSON, and raw JSON drawers.
- Raw JSON drawers for developer debugging without dominating the page.

The UI displays `SANDBOX ONLY — NO REAL BANK DATA` and blocks clearly if
`ENABLE_EXPENSEOPS_SANDBOX_LAB` is false or `PLAID_ENV` is not `sandbox`.

Date handling note: the transaction form leaves dates blank by default so the
backend picks a safe Plaid Sandbox date. If you fill dates manually, use today
or a date within the last 14 days.

Action boundary note: `Create in Plaid Sandbox` only creates the fake transaction
in Plaid when auto flags are false. It does not import into ExpenseOps, run sync,
fire a webhook, or trigger Telegram unless you explicitly use Webhook Flow,
Manual Sync Flow, or Full E2E.

Known behavior: `webhook_received` may show `unknown` if fallback sync completes
before Sandbox Lab observes the webhook. If sync completes successfully, treat
that as a warning rather than a full failure.

Reminder features are intentionally out of scope for Sandbox Lab.

## Scripts

```bash
python sandbox/scripts/run_e2e.py
python sandbox/scripts/create_transaction.py --description "ExpenseOps Sandbox Coffee" --amount 12.34
python sandbox/scripts/fire_webhook.py
```

Set `SANDBOX_BACKEND_URL` if your backend is not on `http://localhost:8000`.

## State And Events

Sandbox Lab stores local-only state at:

```text
sandbox/state/sandbox_state.local.json
```

The access token in that file is only for local Plaid Sandbox testing. It is
ignored by git via `sandbox/.gitignore` and must not be committed.

Events are stored at:

```text
sandbox/logs/sandbox_events.jsonl
```

## Troubleshooting

- `PLAID_ENV` must be `sandbox`.
- `ENABLE_EXPENSEOPS_SANDBOX_LAB` must be `true`.
- The ngrok/public URL must be active and point to the existing Plaid webhook.
- Plaid Sandbox Item creation uses `user_transactions_dynamic`.
- `/transactions/sync` should be initialized before reliable
  `SYNC_UPDATES_AVAILABLE` testing.
- `webhook_fired=true` means Plaid accepted the sandbox fire request; it does
  not prove your backend received the webhook.
- Plaid webhooks do not contain full transaction data. The backend must call
  `/transactions/sync`.

## Minimal Outside-Sandbox Wiring

Small app touchpoints are required:

- `app/main.py` includes the `/api/sandbox` router.
- `app/api/plaid_routes.py` calls
  `sandbox.backend.webhook_hooks.maybe_log_sandbox_webhook(payload)` so the
  Sandbox Lab can observe receipt of the existing Plaid webhook.
- `frontend/src/App.tsx` routes `/sandbox-lab`, `/sandbox`, and `/dev/sandbox`
  to `sandbox/frontend/SandboxLabPage.tsx`.
- Frontend TypeScript/Vite/Tailwind config includes `sandbox/frontend` and
  proxies `/api` to the backend in local development.

All Sandbox Lab business logic lives under `sandbox/`.

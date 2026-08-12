# First design-user onboarding

This runbook is for the ExpenseOps owner operating the first external beta. Do not invite an
external user until every preflight item is green.

## Before inviting the user

- Confirm `https://expenseops-production.up.railway.app/health` and `/readiness` return success.
- Confirm Railway `Postgres` has a fresh manual backup under **Postgres → Backups**.
- Confirm migrations report revision `20260811_0013` after deployment.
- Confirm Google OAuth is in **External / Testing** and both the owner and design user are listed
  under **Google Auth Platform → Audience → Test users**.
- Confirm the Google OAuth web client allows these exact redirect URIs:
  - `https://expenseops-production.up.railway.app/auth/callback`
  - `https://expenseops-production.up.railway.app/api/integrations/gmail/callback`
  - `http://localhost:8000/auth/callback`
  - `http://localhost:8000/api/integrations/gmail/callback`
- Confirm `AUTH_MODE=oidc`, production URL/origin, OIDC, Gmail, and Telegram variables are set in
  Railway. Keep secrets in Railway variables, never chat or source control.
- Confirm the Telegram bot webhook is healthy and points to
  `https://expenseops-production.up.railway.app/telegram/webhook` using Telegram's
  `X-Telegram-Bot-Api-Secret-Token` header (configured with `secret_token` in `setWebhook`).
- For the owner's migration only, set `OIDC_BOOTSTRAP_EMAIL` to the owner's exact verified Google
  email. Sign in once, verify the existing data checklist below, then remove this variable.
- Confirm `/api/admin/onboarding-funnel` works for the email listed in `ADMIN_USER_EMAILS`.

Owner data verification after bootstrap:

- receipts and receipt review history are visible;
- household items and acquisition history are intact;
- replenishment history and recommendations are intact;
- promotions and feedback remain intact;
- errands and saved locations remain intact;
- Gmail, Telegram, Plaid, and Splitwise status is associated with the claimed workspace;
- only one owner user, personal workspace, and default membership exist.

After those checks, remove `OIDC_BOOTSTRAP_EMAIL` and the legacy user-bound variables
`GMAIL_REFRESH_TOKEN`, `GMAIL_USER_ID`, `TELEGRAM_ALLOWED_USER_ID`, `TELEGRAM_CHAT_ID`,
`SPLITWISE_API_KEY`, `SPLITWISE_ACCESS_TOKEN`, `SPLITWISE_OAUTH_TOKEN`, and
`SPLITWISE_OAUTH_TOKEN_SECRET`. The bootstrap has copied active credentials into encrypted,
workspace-owned records. Keep application credentials such as Gmail client ID/secret, Telegram
bot token/webhook secret, Plaid app credentials, and Splitwise consumer credentials.

## What I send the user

> ExpenseOps is a private beta that helps turn receipts, promotions, household replenishment, and
> errands into fewer manual chores. Open
> https://expenseops-production.up.railway.app and sign in with the Google account I approved for
> testing. Gmail and Telegram are the recommended starting connections. Plaid and Splitwise are
> optional. ExpenseOps stores connection credentials encrypted and keeps each workspace isolated;
> you can disconnect an integration from Settings without deleting your workflow history.

## What the user does

1. Open the ExpenseOps URL and select **Sign in**.
2. Approve basic Google identity access. ExpenseOps creates a private personal workspace.
3. Open **Settings**, select **Connect Gmail**, and approve read-only Gmail access.
4. Select **Connect Telegram**, copy the one-time command, and send it to the ExpenseOps bot.
5. Explore receipts, Promotion Intelligence, Replenishment, and Household Ops.
6. Optionally connect Plaid or Splitwise.

The user should never need a workspace ID, API token, SQL instruction, or developer assistance.

## What I monitor

- `/api/admin/onboarding-funnel` for login, workspace, connection, completion, and workflow counts;
- Railway application logs by request ID, workspace ID, event name, and error class;
- failed Gmail/Telegram connection events without provider payloads;
- `/health`, `/readiness`, web-service status, Postgres status, and cron outcomes.

## What I record

- onboarding duration and time to first successful workflow;
- questions, hesitation, or confusing labels;
- connection and workflow failures;
- incorrect AI decisions and user corrections;
- useful workflows and remaining manual work;
- any developer intervention required;
- the user's own estimate of minutes saved.

Use [design-user-feedback.md](design-user-feedback.md) immediately after the session.

## Rollback and support

- Integration problem: disconnect only that workspace's connection from Settings and reconnect.
- Session concern: sign out to revoke the active session. A database operator can revoke all user
  sessions if an account is suspected compromised.
- Workspace access: revoke pending invitations or have members leave; the last owner cannot leave.
- Bad release: redeploy the last known-good Railway deployment. Do not downgrade the database
  unless the code requires it and the backup has been verified.
- Bad migration/data change: use the timestamped Railway volume backup. Restore creates a staged
  replacement volume; review it before deploying the restore.

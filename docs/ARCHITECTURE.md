# ExpenseOps Agent architecture

```text
Chase card
   ↓ secure bank link
Plaid Link + Transactions Sync
   ↓ webhook or manual sync
FastAPI backend
   ↓ stores transaction + asks user
Approval-first agent workflow
   ↓ selected friends + shares
Splitwise create_expense API
```

## Safety defaults

1. The app never stores Chase credentials. Chase is linked through Plaid Link.
2. Plaid access tokens are encrypted with Fernet before being stored.
3. Every outgoing transaction starts in `ask_user` state.
4. Posting pending card transactions is blocked by default.
5. Splitwise posting is idempotent at the app layer: a transaction with a `splitwise_expense_id` cannot be posted again.
6. Splitwise response bodies are checked for `errors`; HTTP 200 alone is not trusted.
7. Webhooks trigger sync; `/transactions/sync` remains the source of truth.

## Transaction states

- `ask_user`: new charge detected; user must classify it.
- `personal`: user confirmed no Splitwise action is needed.
- `shared_draft`: shares were calculated but not posted.
- `posted`: Splitwise accepted the expense and returned an expense ID.
- `error`: posting failed or Splitwise returned errors.
- `removed`: Plaid reported the transaction as removed.

## Recommended production hardening

- Enable Plaid webhook JWT verification.
- Use PostgreSQL instead of SQLite.
- Store secrets in a cloud secret manager.
- Add user authentication to the FastAPI UI before exposing it publicly.
- Add retry queues for Splitwise posting, but keep user confirmation mandatory.
- Add audit logs for every posted expense.

# Promotion Intelligence

Promotion Intelligence turns a bounded stream of Gmail Promotions messages into a short list of
concrete, active, personally relevant offers. Gmail remains read-only. The system never follows a
link server-side, loads tracking pixels, modifies messages, or performs a purchase.

## Architecture

- `GmailClient` shares the existing OAuth credentials and token refresh transport with receipt sync.
- Promotion and receipt processors keep independent idempotency records. The same Gmail message can
  be handled by both.
- The first sync queries Gmail's `CATEGORY_PROMOTIONS` system label for a bounded recent period and
  explicitly excludes Spam and Trash. Later syncs use the stored Gmail history ID. An invalid history
  checkpoint triggers one bounded recovery backfill.
- Extraction tries JSON-LD `DiscountOffer`/`Offer` annotations, deterministic text parsing, and only
  then an optional structured-output LLM fallback. Generic marketing is stored as
  `no_concrete_offer` without creating a deal.
- Only a short sanitized snippet and structured offer fields are retained; raw HTML and remote images
  are not stored.
- Campaign fingerprints use merchant, concrete offer terms, promo code, expiry, and destination
  domain/path. Reminder messages update one representative campaign while distinct terms remain
  separate.
- URLs must be HTTPS. Unsafe schemes are rejected. Domain mismatches lower trust, and sensitive V1
  categories are suppressed.
- Ranking is deterministic and combines replenishment relevance, merchant/category history, explicit
  preferences, deal value, expiry urgency, trust, extraction confidence, minimum-spend burden,
  feedback, and saved state. The score breakdown is stored for auditability.

## Gmail setup

Use the same Google Cloud OAuth client already configured for receipt ingestion. Enable Gmail API and
authorize only:

`https://www.googleapis.com/auth/gmail.readonly`

Set `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, and `GMAIL_USER_ID`. No second
OAuth app or token store is needed. Push/Pub/Sub remains optional; scheduled history sync is the V1
default.

## Configuration

```env
PROMOTIONS_ENABLED=true
PROMOTIONS_INITIAL_LOOKBACK_DAYS=30
PROMOTIONS_MAX_MESSAGES_PER_SYNC=100
PROMOTIONS_LLM_FALLBACK_ENABLED=true
PROMOTIONS_MAX_LLM_BODY_CHARS=12000
PROMOTIONS_MIN_SCORE=50
PROMOTIONS_SYNC_SCHEDULE="0 */6 * * *"

PROMOTIONS_DIGEST_ENABLED=false
PROMOTIONS_DIGEST_CADENCE="weekly"
PROMOTIONS_DIGEST_MAX_DEALS=8
PROMOTIONS_DIGEST_TIMEZONE="UTC"
PROMOTIONS_DIGEST_LOCAL_HOUR=17

GMAIL_PUSH_ENABLED=false
GMAIL_PUBSUB_TOPIC=""
```

The LLM fallback additionally requires `OPENAI_API_KEY`. Deterministic extraction does not call the
LLM. Keep the digest disabled until the Deals page has been reviewed.

## Local testing

```bash
.venv/bin/alembic upgrade head
.venv/bin/python -m app.jobs.promotions sync
.venv/bin/python -m app.jobs.promotions rescore
.venv/bin/python -m app.jobs.promotions digest
npm --prefix frontend run dev
```

Open `http://localhost:5174/?workspace=promotions`. The authenticated API also provides manual sync,
rescore, settings, feedback, digest preview, and explicit digest send operations under
`/api/promotions`.

For Railway, create a cron service using the same repository and variables. Run
`.venv/bin/python -m app.jobs.promotions sync` on `PROMOTIONS_SYNC_SCHEDULE`; run the rescore job daily
and the digest job at the configured local cadence. The commands are idempotent.

## Known V1 limitations

- Deterministic date parsing currently handles explicit numeric dates; relative or natural-language
  dates generally require the LLM fallback.
- Domain trust is deliberately conservative and does not yet maintain a broad affiliate/redirect
  allowlist.
- Merchant/category affinity uses normalized transaction aggregates, not product-level card data.
- Gmail Pub/Sub watch creation and renewal are reserved behind configuration; scheduled Gmail history
  sync is the supported V1 production path.
- The UI shows active and saved states; a dedicated recently-expired history panel is deferred.

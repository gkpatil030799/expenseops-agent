# Replenishment Learning Pipeline

## Architecture

The pipeline is intentionally modular:

```text
Telegram attachment / Gmail message
  -> ReceiptParser (OpenAI vision/text or replaceable provider)
  -> ReceiptLearningService (closed classification and candidate policy)
  -> ReceiptIngestionService (idempotency, Plaid reconciliation)
  -> ItemNormalizationService (raw description, canonical text, learned aliases)
  -> AcquisitionService (immutable confirmed events and feedback)
  -> TrainingDatasetService (chronological, prediction-time-only features)
  -> ReplenishmentPredictionService (cadence/adaptive/model forecast history)
  -> ReplenishmentModelService (candidate training and validation)
  -> WeeklyReplenishmentService (idempotent weekly orchestration)
```

Routing, Google Maps, Splitwise, and expense review logic are unchanged. Plaid is read only as
corroborating evidence; a transaction without receipt line items cannot create product acquisitions.

## Database changes

Migration `20260809_0008` creates:

- `purchase_receipts` and `purchase_receipt_items` for source provenance and parsed facts;
- `household_item_aliases` for confirmed merchant/product mappings;
- `household_item_acquisitions` for immutable, quantity-aware purchase events;
- `replenishment_feedback` for Still have, Skip, timing, and correction evidence;
- `replenishment_predictions` for every historical forecast and its eventual error;
- `replenishment_model_versions` for candidate/champion metadata and JSON artifacts; and
- `replenishment_job_runs` for idempotent weekly execution and metrics.

Existing `HouseholdItem.last_acquired_at` remains as a compatibility projection of the newest
confirmed, non-void acquisition. Undo recomputes it; history is never overwritten.

Migration `20260817_0030` adds zero-setup learning: receipt-line classification evidence and a
truthful `configured|learning|observed|adaptive` cadence source. Newly confirmed receipt candidates
may have `cadence_days=null`; see [Zero-Setup Receipt Learning](ZERO_SETUP_RECEIPT_LEARNING.md).

## Source flows

### Telegram

The existing authenticated webhook accepts Telegram photos, supported image documents, and PDFs.
The bot downloads the attachment, parses it, shows relevant matches, and offers Confirm, Edit, and
Ignore. Confirmed matches create acquisitions and teach aliases. Edit opens the private dashboard
when `APP_PUBLIC_URL` is configured. Originals are processed in memory and are not persisted.

### Gmail

Gmail ingestion is disabled by default. When enabled, it uses a narrow Gmail search query, retrieves
only matching messages, rejects likely marketing-only mail, and sends only the relevant receipt text
to the parser. Gmail message IDs enforce idempotency. Full email bodies are not stored.

Setup:

1. In Google Cloud, enable the Gmail API.
2. Configure an OAuth consent screen for the personal Google account.
3. Create an OAuth client and authorize only `https://www.googleapis.com/auth/gmail.readonly`.
4. Obtain a long-lived refresh token for that client/account.
5. Set `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, and
   `GMAIL_RECEIPT_SYNC_ENABLED=true` in local `.env` or Railway Variables.
6. Keep `GMAIL_RECEIPT_QUERY` narrow. Trigger `POST /api/replenishment/gmail/sync` manually or from
   a separately authorized scheduled job.

### Plaid reconciliation and deduplication

Receipts are matched to Plaid transactions using amount (within two cents), purchase date (within two
days), and normalized merchant similarity. Exact source/external IDs and SHA-256 content fingerprints
deduplicate ingestion. If different Telegram/Gmail representations reconcile to the same Plaid
transaction, the item+transaction constraint in acquisition logic prevents duplicate learning. When
no transaction link exists, same item, normalized merchant, and purchase date are treated as the same
evidence.

## Prediction and model behavior

Manually configured items use configured cadence. Receipt-created items start in Learning with no
invented cadence and do not surface as due after one purchase. The second confirmed purchase
establishes an observed interval; subsequent intervals use the robust adaptive baseline. Still have
/ Skip / Too early feedback adjusts a known cadence by 5% per event, capped at 25% by default; real
acquisitions always reset and outweigh this weak evidence. Known count, roll, volume, and weight
units are normalized only at 0.8+ extraction confidence. Ambiguous packages stay unknown, and
quantity enters model features only after two comparable observations.

The optional model is one global standardized ridge regression. Ridge was chosen because this is a
small, correlated tabular dataset for one household: it is deterministic, fast, interpretable, stores
as small JSON, and adds no heavy runtime dependency. Features include configured cadence, history
count, robust/recent/last intervals, quantity, prior feedback counts, and month seasonality.

The default gate is 30 usable training rows with at least 8 validation rows. Smaller eligible
datasets use one chronological holdout; 60+ rows use expanding walk-forward validation. Rows use the
cadence snapshot, acquisitions, quantities, aliases, and feedback known before the target purchase;
future edits cannot leak backward. The candidate is compared on the same validation points against
configured cadence, adaptive cadence, and the active model when valid. It must beat the strongest
baseline by both 10% and 1.0 MAE day by default. Marginal candidates are rejected with an audit
reason. Failed training records a failed version, rolls back, and preserves the active model.

Evidence levels are deterministic rather than percentages: `insufficient`, `low`, `medium`, and
`high`, based on usable rows, validation size/method, acquisition observations, and interval/model
stability. Active inference validates artifact shape/numbers and rejects negative, non-finite, or
extreme cadence-relative output. Any failure is logged and falls back to the adaptive baseline.

Training eligibility is centralized: trusted manual/correction evidence and high-confidence receipt
evidence qualify; medium-confidence receipt evidence qualifies only after explicit confirmation;
low-confidence, unconfirmed, and voided evidence is excluded with a diagnostic reason. Corrections
soft-invalidate stale aliases/evidence, create an auditable superseding acquisition, and rebuild
historical prediction outcomes against the next valid acquisition. A logical purchase key plus a
database uniqueness constraint prevents source-order races from learning the same item twice.

## Weekly operation

Manual command:

```bash
python -m app.jobs.weekly_replenishment
```

Manual API trigger:

```bash
curl -u "$DASHBOARD_USERNAME:$DASHBOARD_PASSWORD" \
  -H 'Content-Type: application/json' \
  -d '{}' http://localhost:8000/api/replenishment/weekly-run
```

For Railway, create a separate cron service using the same repository and variables, set its start
command to `python -m app.jobs.weekly_replenishment`, and use the value documented by
`REPLENISHMENT_WEEKLY_SCHEDULE` (default Sunday 09:00). The database `run_key` makes repeat delivery
of the same ISO week safe. Do not run an in-process scheduler in every web replica.

## Environment variables

Required for automatic Telegram receipt parsing:

```env
APP_PUBLIC_URL="https://your-private-app.example"
OPENAI_API_KEY="..."
RECEIPT_PARSER_PROVIDER="openai"
RECEIPT_PARSER_MODEL="gpt-4.1-mini"
```

Optional controls:

```env
RECEIPT_MAX_ATTACHMENT_BYTES=10000000
RECEIPT_AUTO_MATCH_CONFIDENCE=0.90
RECEIPT_POSSIBLE_MATCH_CONFIDENCE=0.65
REPLENISHMENT_ML_MIN_ROWS=30
REPLENISHMENT_ML_MIN_VALIDATION_ROWS=8
REPLENISHMENT_WALK_FORWARD_MIN_ROWS=60
REPLENISHMENT_MODEL_MIN_MAE_IMPROVEMENT_PCT=10
REPLENISHMENT_MODEL_MIN_MAE_IMPROVEMENT_DAYS=1.0
REPLENISHMENT_MAX_FEEDBACK_CADENCE_ADJUSTMENT_PCT=25
REPLENISHMENT_WEEKLY_SCHEDULE="0 9 * * 0"
```

All secrets belong in `.env` locally or Railway Variables, never Git.

## Local verification

```bash
alembic upgrade head
pytest -q
ruff check app tests alembic
cd frontend
npm test
npm run lint
npm run build
```

Start the app, send the bot a clear store receipt image, review the proposed known matches/new
Learning items/non-trackable lines as one batch, confirm it, and verify Recent receipts and Learning
from purchases. Manual staple creation remains available but is no longer required for safe receipt
candidates. Use Refresh predictions or the manual job to populate This week after enough evidence.
Gmail can be tested only after the OAuth variables above exist.

## Privacy and limitations

- Receipt images and full Gmail bodies are not retained; only a SHA-256 fingerprint and minimum
  parsed purchase facts are stored. Card numbers, loyalty identifiers, and unrelated email content
  are explicitly excluded from extraction and must not be logged.
- OCR/LLM extraction can be wrong. Low-confidence lines are excluded; medium-confidence lines wait
  for review. The user can change matches or undo learned acquisitions.
- Gmail HTML extraction is deliberately lightweight and retailer coverage depends on message content.
- Merchant/date/amount reconciliation is conservative but is not a universal receipt identity system.
- Quantity is captured and exposed to training, but unit conversion and per-package consumption models
  are not implemented yet.
- The model learns only after enough confirmed repeat purchases. Until then the statistical baseline is
  the expected and preferred behavior.

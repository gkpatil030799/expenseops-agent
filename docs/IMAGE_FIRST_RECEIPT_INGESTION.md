# Image-first receipt ingestion

Status: Day 15 implementation and local validation complete on 2026-08-17. This document does not authorize a push or deployment.

## Outcome

A user can send a normal receipt photo through Telegram, attach a receipt image to an eligible Gmail message, or take/upload a photo from Household Ops. JPEG, PNG, and WebP images use one canonical validation and multimodal parsing path. PDF remains supported as a secondary input.

The design intentionally adds no OCR service, receipt microservice, workflow engine, queue, vector store, or new AI framework. The only new runtime library is Pillow 12.3.0 for safe media inspection and EXIF orientation correction.

## Root cause of the production failure

The old Telegram path was not PDF-only. It did download the Telegram file bytes and did send image bytes to the OpenAI Responses API as an `input_image`. The failure was a combination of weaker boundaries:

- Telegram chose a photo variant by `file_size` alone. Missing or tied sizes could select a lower-resolution variant even when width and height identified a better one.
- The configured `gpt-4.1-mini` path used `detail=high`. For a long, narrow receipt, platform image resizing/patch limits could make tiny line text materially less legible.
- Bytes labeled as an image were not decoded or content-sniffed before the provider call. Corrupt images, declared/actual MIME mismatches, implausibly small images, and pixel bombs had no deterministic boundary.
- EXIF rotation was not normalized.
- A schema-valid but empty extraction—no merchant, total, or lines—was persisted as a receipt needing review and could be presented as “ready.”
- Important uncertainty, tip, discount, line completeness, and per-field confidence were absent from the provider contract.
- No application arithmetic check distinguished a useful partial result from a contradictory result.
- There was no bounded quality/provider retry.
- Gmail parsed text/HTML but ignored image and PDF attachments.
- There was no web photo-upload surface.
- Telegram gave no acknowledgment before the potentially slow provider call.

PDF behaved differently because it used the Responses API `input_file` document path rather than a raster image constrained by the image-detail path.

The permanent Telegram regression now proves: highest-resolution useful variant selection, actual JPEG bytes downloaded, immediate acknowledgment, canonical image ingestion attempted, and a review result returned without user-side PDF conversion.

## Previous and current pipelines

Previous Telegram flow:

```text
Telegram photo -> largest file_size -> downloaded bytes -> MIME prefix check
-> base64 input_image detail=high -> gpt-4.1-mini -> permissive parsed object
-> receipt row (including valid-empty results)
```

Current common flow:

```text
Telegram / Gmail / web
-> ReceiptArtifact
-> size + magic/MIME + Pillow decode + pixel limits + EXIF correction
-> ReceiptParser
-> direct Responses API image/file input, strict structured output, store=false
-> application quality + integer-cent arithmetic assessment
-> complete / partial / non-receipt / unusable
-> canonical receipt, transaction reconciliation, Day 9 and Day 11 workflows
```

`ReceiptArtifact` contains source, external source ID, safe filename, declared and server-detected MIME, media class, original and normalized bytes, byte size, image/page count where known, dimensions, orientation status, and normalization latency. It is an in-memory ingestion object. ExpenseOps persists the existing SHA-256 fingerprint and parsed facts, not the sensitive image bytes.

## Build versus integrate

### Platform capability

ExpenseOps continues to use the existing OpenAI Responses API. Current official documentation confirms that image inputs can be supplied as data URLs, models can understand text in images, and supported raster inputs include JPEG, PNG, WebP, and non-animated GIF. Detail selection is model-dependent. The GPT-5.6 family supports original-dimension image handling through `auto`/`original`, which is helpful for dense receipt text. Sources reviewed:

- [OpenAI images and vision guide](https://developers.openai.com/api/docs/guides/images-vision)
- [GPT-4.1 mini model](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
- [GPT-5.6 luna model](https://developers.openai.com/api/docs/models/gpt-5.6-luna)

The parser default is now `gpt-5.6-luna` with image detail `auto`. It is the cost-conscious GPT-5.6 choice evaluated for preserving receipt text resolution. The receipt model remains independently configurable. A deployment with an existing `RECEIPT_PARSER_MODEL` environment override will continue to use that override until an operator intentionally changes it.

### Direct image rather than OCR-first

Direct multimodal understanding was retained. Adding image-to-OCR-to-LLM would introduce a second lossy interpretation stage and more plumbing without measured evidence that it improves this use case. Deterministic code remains authoritative for money bounds, arithmetic, currency shape, deduplication, transaction identity, acquisitions, and actions.

### Normalization library

Pillow is used only to decode supported images, reject unsafe dimensions, identify actual format, and apply EXIF orientation. ExpenseOps does not sharpen, threshold, denoise, crop, deskew, tile, or run custom OCR by default.

## Source behavior

### Telegram

- Photo arrays are ranked by pixel area first and file size second, constrained by the application byte limit.
- Documents accept PDF, JPEG, PNG, and WebP.
- The bot first sends “Got it — I'm reading this receipt.”
- Success and partial results show the existing receipt review keyboard.
- Download, poor-image, non-receipt, oversized, unsupported, and temporary-provider failures map to useful user copy; MIME/schema/provider internals are not exposed.
- The Telegram update and receipt source IDs remain idempotent.

### Gmail

- Existing structured text/HTML remains preferred when it contains deterministic total-and-money evidence.
- Otherwise, an eligible message's best supported image attachment is preferred, with PDF secondary.
- Inline data and Gmail attachment IDs are supported.
- Attachment bytes enter the same `ReceiptArtifact` and `ReceiptParser` path; Gmail has no separate vision intelligence.
- Message-ID and fingerprint deduplication remain in place.

### Web

Household Ops exposes a primary “Take or upload receipt photo” control. The file input requests the environment camera on compatible mobile browsers and accepts JPEG, PNG, WebP, and PDF. Multipart upload is sent without an incorrect JSON `Content-Type`. Parsing runs on FastAPI's worker thread so synchronous provider I/O does not block the application event loop. Complete results open the existing review; partial and failed results use the same bounded messages as other sources.

The first-upload state is important: the returned receipt now seeds Day 9's default learning decisions directly rather than waiting for a React refresh to settle.

## Media and normalization

Supported:

- JPEG, including Telegram photos;
- PNG;
- WebP;
- PDF as secondary input;
- generic `application/octet-stream` only when server-side byte inspection proves a supported image/PDF.

Rejected before the model:

- zero bytes;
- corrupt raster data;
- declared/actual MIME mismatch;
- unsupported formats;
- images smaller than 32×32;
- files above the configured 10 MB default;
- images above 50 million decoded pixels.

HEIC is not sent directly because it is not a documented Responses image format and Pillow does not decode it without another plugin. Telegram's normal photo channel produces JPEG, and the web input asks mobile browsers for supported camera output. A direct HEIC file receives useful unsupported-format copy. Add a lightweight HEIC decoder only after observed channel data justifies it.

The original bytes remain unchanged in `ReceiptArtifact.original_content`; EXIF-transposed bytes, when required, are separate `normalized_content`. Pixel-rotated images without EXIF are still sent to the vision model, which is expected to interpret rotation.

## Structured output and quality

The strict provider result now includes:

- receipt/non-receipt classification;
- merchant/date/total field confidence;
- currency, subtotal, tax, tip, discount, total;
- line-item completeness and closed quality warnings;
- raw line name, quantity, unit, unit price, line total, brand/category, confidence;
- existing Day 9 tracking classification, its confidence, and bounded canonical hint.

Money fields are nonnegative integer cents. Coupon/discount/tax/tip/subtotal/total rows are excluded from purchased items and represented in top-level fields. Local validation repeats strict field, type, enum, size, confidence, and money checks even after provider structured output.

The canonical database continues to keep corrected raw line names, line amounts, subtotal, tax, total, currency, and the existing overall/line confidence used by review and Day 11. Tip, discount, per-field confidence, and image-quality warnings participate in ingestion assessment but do not add parallel durable columns in Day 15. If product review later needs to edit those individual fields, extend the canonical receipt schema rather than persisting a second parser document.

Application quality is one of:

- `complete`: useful facts with no material contradiction;
- `partial`: useful lines/facts remain, but total, completeness, image quality, arithmetic, or matching needs review;
- `non_receipt`: image is confidently not a receipt;
- `unusable`: no useful merchant, amount, or lines were extracted.

A valid-empty model response is unusable, not “ready.” Partial receipts persist useful lines and cannot auto-confirm.

## Arithmetic and retry policy

When sufficient facts exist, code checks within a two-cent tolerance:

```text
sum(purchased line totals) - discount ~= subtotal
subtotal + tax + tip ~= total
```

An inconsistency is `receipt_arithmetic_mismatch` and remains reviewable. Missing evidence is `not_checkable`, not fabricated.

Every receipt starts with one provider request. A second and final request is allowed only for a retryable timeout/rate limit/5xx/malformed structured output or a materially incomplete image result. The second request sees the same original visual input with a focused recovery instruction. The higher-quality valid parse wins. The hard maximum is two; PDFs and normal complete images do not receive quality retries.

## Canonical reconciliation and downstream behavior

Transaction identity remains application-owned: amount within two cents, date within two days, normalized merchant similarity, and tenant-scoped database candidates. A near tie is disclosed as ambiguous and no transaction is linked.

Existing deduplication remains:

- workspace + source + external source ID;
- content SHA-256 across sources;
- database uniqueness wins concurrent same-source races;
- acquisition logical keys and receipt-item uniqueness prevent duplicate learning;
- provider retry creates no intermediate receipt.

Byte-identical Telegram/Gmail/web images converge to one receipt. Separately compressed images may have different fingerprints; canonical transaction linkage and acquisition keys still prevent duplicate downstream learning where evidence supports it.

Day 9 continues to classify and learn from persisted parsed lines. The media layer does not create a second classification policy. Hostile lines remain `uncertain`/inert unless the existing deterministic classifier has safe evidence.

Day 11 continues to consume `PurchaseReceipt`, subtotal, tax, total, and line amounts. Restaurant photographs therefore feed the existing itemized split proposal/math/confirmation flow; Day 15 adds no Splitwise authority and performs no provider write.

## Security, privacy, and tenancy

- Receipt text is untrusted data. Prompt-like line text cannot choose tools, reveal secrets, create household items, confirm a receipt, post Splitwise, mark a transaction, or purchase.
- Responses requests set `store=false`.
- No raw receipt bytes, raw lines, email bodies, addresses, or card fragments enter aggregate telemetry.
- Original image bytes are not persisted by this change.
- File names are basename-normalized and bounded.
- Receipt rows, matched transactions, and acquisitions remain under existing session tenancy and PostgreSQL RLS.
- Authority is rechecked after the potentially slow provider call. If membership or the user becomes inactive during parsing, no receipt is persisted.
- Crafted cross-workspace receipt IDs return not found.

## Observability and measured performance

Safe events include:

- `receipt_image_received`;
- `receipt_image_parse_success`;
- `receipt_image_parse_partial`;
- `receipt_image_parse_failed`;
- `receipt_image_retry`;
- `receipt_non_receipt_detected`;
- `receipt_transaction_match_success`;
- `receipt_transaction_match_ambiguous`;
- `telegram_receipt_acknowledged`.

Dimensions are aggregate-only: source, media class, byte size, dimensions, orientation correction, normalization/provider/total latency, requests, tokens, optional model-matched estimated cost, item count, failure code, and whether a transaction matched.

The 20-case deterministic media/quality gate reported:

- 20 scenarios;
- 100% seeded merchant/date/total/line preservation;
- 10% intentional partial success;
- 5% intentional non-receipt hard failure;
- median normalization 1 ms;
- legacy false-ready results 1; Day 15 false-ready results 0.

These seeded accuracy values test the production artifact/validator/quality path; they are not claimed as model accuracy.

The final paid live observation used four synthetic, non-sensitive images with `gpt-5.6-luna`: normal grocery, readable blur, EXIF rotation, and restaurant. Result: 4/4 passed, one request per image, zero retries, all expected merchants/dates/totals/minimum lines, and reconciled arithmetic. Aggregate provider latency was 18,447 ms (4,612 ms/image average), input 9,432 tokens, output 1,224 tokens, and estimated cost $0.016776 total ($0.004194/image). The estimate uses the 2026-08-17 model-matched $1.00/M input and $6.00/M output snapshot; it is an observation, not an SLO or future price guarantee.

Telegram acknowledgment ordering is deterministic and tested, but production network acknowledgment latency was not measured. Gmail provider latency was not separately live-measured; it shares the same parser after attachment download.

## Final local validation

The settled Day 15 tree passed:

- full backend: 1,350 passed, 18 configured skips;
- focused Day 15 media, chaos, benchmark, and live-gate tests: 35 passed, 1 opt-in live skip;
- final opt-in OpenAI image smoke: 4/4 passed;
- frontend unit: 9 files, 106 tests;
- full Playwright: 306 passed, 162 configured project/viewport skips across Chromium, mobile Chromium, Firefox, and WebKit;
- targeted receipt plus visual/accessibility matrix: 155 passed, 5 configured skips, with no snapshot updates;
- TypeScript and production build: passed;
- ESLint: zero errors and 20 unchanged existing warnings;
- Ruff and touched-file format checks: passed;
- Python `pip check`, `pip-audit`, and npm audit: no broken requirements or known vulnerabilities;
- Alembic: one head (`20260817_0032`) and no migration/model-schema change in this scope;
- `git diff --check`: passed.

The browser gate initially caught the visually hidden file input as a phantom 1×1 touch target. The settled control has one accessible 44px+ button and a fully hidden implementation input. A separate existing Day 12 responsive test race was made deterministic by waiting for the Settings page before choosing its mobile or desktop navigation control; the full matrix then passed.

## Limitations and future path

- Direct HEIC/HEIF decoding is intentionally not installed.
- One upload represents one image/PDF. There is no 2–3-photo grouping or stitching system. A future bounded design should create a single explicit multi-artifact draft before parsing so separate photos never silently become separate purchases.
- Long images use original-preserving model detail. Automatic tiling is not enabled because the four-image live smoke and deterministic long-receipt gate did not justify an extra call policy. Add deterministic vertical segments only after a measured long-receipt miss set.
- Original photos are not persisted, which minimizes privacy exposure but means the current dashboard cannot redisplay the photo during correction.
- Telegram parsing remains synchronous after the immediate acknowledgment because this repository has no durable receipt-processing queue suitable for reuse. Update and receipt idempotency make retries safe; a future queue is justified only if measured webhook/provider latency causes delivery issues.
- Fingerprints deduplicate exact bytes, not perceptually equivalent recompressions.
- Model quality can change behind an alias. Operators may pin a dated snapshot when available and should rerun the synthetic live gate before rollout changes.
- Production environments with an existing `RECEIPT_PARSER_MODEL=gpt-4.1-mini` override will not adopt the new model merely from the code default. Changing that variable is a separate reviewed deployment action.

## Day 16 recommendation

Day 15 is ready for human review. Do not use image parsing confidence alone to begin broad autonomous categorization. Day 16 should start only after production-like receipt observations confirm field quality and correction rates. It should preserve the complete/partial boundary, code-owned money checks, explicit action confirmations, and Day 9's conservative learning rules.

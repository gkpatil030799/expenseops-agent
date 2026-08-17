# Real-user UX regressions

This file records production-discovered UX failures that must remain covered by permanent tests.

## Receipt photo required manual PDF conversion

Status: resolved in the Day 15 local branch; not yet deployed.

Observed behavior:

- A user photographed a readable physical receipt and sent it through Telegram.
- ExpenseOps returned a review with an unknown merchant and zero line items, making PDF conversion appear necessary.

Root cause:

- Image bytes did reach the multimodal parser, but the combined photo-selection, image-detail/model, validation, orientation, output-quality, and retry boundaries could accept a valid-empty result as ready. Gmail image attachments and a web camera/upload path were also absent.

Permanent acceptance:

- select the highest-resolution useful Telegram photo variant;
- send immediate acknowledgment;
- download actual JPEG bytes;
- validate and normalize them through the common `ReceiptArtifact` path;
- send direct image input to the receipt model;
- never present an empty/non-receipt extraction as ready;
- keep useful partial lines for review;
- require no user-side PDF conversion;
- create no external financial write.

Evidence on 2026-08-17:

- deterministic Telegram regression passed with actual JPEG bytes and the largest pixel-area variant;
- 20-case deterministic media/quality benchmark passed;
- final synthetic OpenAI image smoke passed 4/4 (normal, blurred, rotated, restaurant), one request each and zero retries;
- Day 15 Playwright passed across Chromium, Firefox, WebKit, and 320px mobile;
- the full regression result is recorded in `IMAGE_FIRST_RECEIPT_INGESTION.md` and the Day 15 completion report.

The issue is marked resolved in code, not in production. It must be reopened if the deployed environment keeps the old receipt-model override or the production photo regression fails after rollout.

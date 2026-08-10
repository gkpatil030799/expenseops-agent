from __future__ import annotations

import argparse

from app.config import get_settings
from app.db import SessionLocal
from app.services.gmail_receipt_service import GmailReceiptService


def run(max_results: int = 25) -> dict[str, int]:
    settings = get_settings()
    with SessionLocal() as db:
        result = GmailReceiptService(db, settings).sync(max_results=max_results)
        return {
            "scanned": result.scanned,
            "ingested": result.ingested,
            "skipped": result.skipped,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Gmail receipt emails")
    parser.add_argument("--max-results", type=int, default=25, metavar="1-100")
    args = parser.parse_args()
    if not 1 <= args.max_results <= 100:
        parser.error("--max-results must be between 1 and 100")
    print(run(args.max_results))

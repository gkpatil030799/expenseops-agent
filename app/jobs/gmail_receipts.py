from __future__ import annotations

import argparse
import logging

from app.config import get_settings
from app.db import SessionLocal
from app.job_tenancy import enter_job_workspace, gmail_job_contexts, leave_job_workspace
from app.logging_config import log_event
from app.services.gmail_receipt_service import GmailReceiptService

logger = logging.getLogger(__name__)


def run(max_results: int = 25) -> dict[str, int]:
    settings = get_settings()
    with SessionLocal() as db:
        totals = {"scanned": 0, "ingested": 0, "skipped": 0}
        for context in gmail_job_contexts(db, settings):
            try:
                enter_job_workspace(db, context.workspace_id)
                result = GmailReceiptService(db, context.settings).sync(max_results=max_results)
                totals["scanned"] += result.scanned
                totals["ingested"] += result.ingested
                totals["skipped"] += result.skipped
            except Exception as exc:
                db.rollback()
                log_event(
                    logger,
                    "gmail_receipt_workspace_sync_failed",
                    level=logging.ERROR,
                    workspace_id=context.workspace_id,
                    error_type=type(exc).__name__,
                )
        leave_job_workspace()
        return totals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Gmail receipt emails")
    parser.add_argument("--max-results", type=int, default=25, metavar="1-100")
    args = parser.parse_args()
    if not 1 <= args.max_results <= 100:
        parser.error("--max-results must be between 1 and 100")
    print(run(args.max_results))

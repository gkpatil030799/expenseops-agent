from __future__ import annotations

import argparse
import logging

from app.config import get_settings
from app.db import SessionLocal
from app.job_tenancy import enter_job_workspace, gmail_job_contexts, leave_job_workspace
from app.logging_config import log_event
from app.services.gmail_receipt_service import GmailReceiptService
from app.services.job_lease_service import acquire_job_lease, release_job_lease

logger = logging.getLogger(__name__)


def run(max_results: int = 25) -> dict[str, int]:
    settings = get_settings()
    settings.validate_worker_runtime()
    with SessionLocal() as db:
        totals = {"scanned": 0, "ingested": 0, "skipped": 0}
        failures: list[int] = []
        for context in gmail_job_contexts(db, settings):
            try:
                enter_job_workspace(db, context.workspace_id)
                lease = acquire_job_lease(
                    db,
                    workspace_id=context.workspace_id,
                    job_name="gmail_receipts_sync",
                )
                if lease is None:
                    log_event(
                        logger,
                        "gmail_receipt_workspace_sync_skipped_overlap",
                        workspace_id=context.workspace_id,
                    )
                    continue
                try:
                    service = GmailReceiptService(db, context.settings)
                    if not service.configured:
                        log_event(
                            logger,
                            "gmail_receipt_workspace_sync_skipped_not_configured",
                            workspace_id=context.workspace_id,
                        )
                        continue
                    result = service.sync(max_results=max_results)
                finally:
                    release_job_lease(
                        db,
                        workspace_id=context.workspace_id,
                        job_name="gmail_receipts_sync",
                        lease_token=lease,
                    )
                totals["scanned"] += result.scanned
                totals["ingested"] += result.ingested
                totals["skipped"] += result.skipped
            except Exception as exc:
                db.rollback()
                failures.append(context.workspace_id)
                log_event(
                    logger,
                    "gmail_receipt_workspace_sync_failed",
                    level=logging.ERROR,
                    workspace_id=context.workspace_id,
                    error_type=type(exc).__name__,
                )
        leave_job_workspace()
        if failures:
            raise RuntimeError(
                f"gmail_receipt_sync_failed_for_{len(failures)}_workspace(s)"
            )
        return totals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Gmail receipt emails")
    parser.add_argument("--max-results", type=int, default=25, metavar="1-100")
    args = parser.parse_args()
    if not 1 <= args.max_results <= 100:
        parser.error("--max-results must be between 1 and 100")
    print(run(args.max_results))

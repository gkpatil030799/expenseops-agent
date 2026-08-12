from __future__ import annotations

import argparse
import logging

from app.config import get_settings
from app.db import SessionLocal
from app.job_tenancy import (
    all_workspace_job_contexts,
    enter_job_workspace,
    gmail_job_contexts,
    leave_job_workspace,
)
from app.logging_config import log_event
from app.services.gmail_promotion_ingestion_service import GmailPromotionIngestionService
from app.services.promotion_digest_service import PromotionDigestService
from app.services.promotion_ranking_service import PromotionRankingService

logger = logging.getLogger(__name__)


def run(command: str) -> dict:
    settings = get_settings()
    with SessionLocal() as db:
        results = []
        contexts = (
            gmail_job_contexts(db, settings)
            if command == "sync"
            else all_workspace_job_contexts(db, settings)
        )
        for context in contexts:
            try:
                enter_job_workspace(db, context.workspace_id)
                if command == "sync":
                    value = GmailPromotionIngestionService(db, context.settings).sync().__dict__
                elif command == "rescore":
                    value = {"rescored": PromotionRankingService(db).rescore_all()}
                elif command == "digest":
                    result = PromotionDigestService(db, context.settings).send()
                    value = {"status": result.delivery_status, "offers": result.offers_included}
                else:
                    raise ValueError("unknown_command")
                results.append({"workspace_id": context.workspace_id, **value})
            except Exception as exc:
                db.rollback()
                log_event(
                    logger,
                    "promotion_workspace_job_failed",
                    level=logging.ERROR,
                    workspace_id=context.workspace_id,
                    command=command,
                    error_type=type(exc).__name__,
                )
        leave_job_workspace()
        return {"workspaces": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Promotion Intelligence jobs")
    parser.add_argument("command", choices=("sync", "rescore", "digest"))
    args = parser.parse_args()
    print(run(args.command))

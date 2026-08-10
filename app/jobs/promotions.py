from __future__ import annotations

import argparse

from app.config import get_settings
from app.db import SessionLocal
from app.services.gmail_promotion_ingestion_service import GmailPromotionIngestionService
from app.services.promotion_digest_service import PromotionDigestService
from app.services.promotion_ranking_service import PromotionRankingService


def run(command: str) -> dict:
    settings = get_settings()
    with SessionLocal() as db:
        if command == "sync":
            return GmailPromotionIngestionService(db, settings).sync().__dict__
        if command == "rescore":
            return {"rescored": PromotionRankingService(db).rescore_all()}
        if command == "digest":
            result = PromotionDigestService(db, settings).send()
            return {"status": result.delivery_status, "offers": result.offers_included}
    raise ValueError("unknown_command")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Promotion Intelligence jobs")
    parser.add_argument("command", choices=("sync", "rescore", "digest"))
    args = parser.parse_args()
    print(run(args.command))

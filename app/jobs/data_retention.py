from __future__ import annotations

import json
import logging

from app.config import get_settings
from app.db import SessionLocal
from app.logging_config import configure_logging, log_event
from app.services.data_lifecycle_service import DataLifecycleService
from app.tenancy import clear_session_tenant

logger = logging.getLogger(__name__)


def main() -> int:
    settings = get_settings()
    settings.validate_worker_runtime()
    configure_logging(settings)
    try:
        with SessionLocal() as db:
            clear_session_tenant(db)
            counts = DataLifecycleService(db, settings).purge_expired()
        log_event(logger, "data_retention_completed", **counts)
        print(json.dumps({"status": "ok", "deleted": counts}, sort_keys=True))
        return 0
    except Exception as exc:
        log_event(
            logger,
            "data_retention_failed",
            level=logging.ERROR,
            error_class=type(exc).__name__,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

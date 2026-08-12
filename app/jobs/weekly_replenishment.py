import logging

from app.config import get_settings
from app.db import SessionLocal
from app.job_tenancy import (
    all_workspace_job_contexts,
    enter_job_workspace,
    leave_job_workspace,
)
from app.logging_config import log_event
from app.services.weekly_replenishment_service import WeeklyReplenishmentService

logger = logging.getLogger(__name__)


def main() -> None:
    with SessionLocal() as db:
        for context in all_workspace_job_contexts(db, get_settings()):
            try:
                enter_job_workspace(db, context.workspace_id)
                run = WeeklyReplenishmentService(db).run()
                print(
                    f"workspace {context.workspace_id} replenishment run "
                    f"{run.run_key}: {run.status}"
                )
            except Exception as exc:
                db.rollback()
                log_event(
                    logger,
                    "weekly_replenishment_workspace_failed",
                    level=logging.ERROR,
                    workspace_id=context.workspace_id,
                    error_type=type(exc).__name__,
                )
        leave_job_workspace()


if __name__ == "__main__":
    main()

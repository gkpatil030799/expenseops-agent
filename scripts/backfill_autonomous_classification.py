from __future__ import annotations

import argparse
import json
import logging

from app.config import get_settings
from app.db import SessionLocal
from app.job_tenancy import (
    all_workspace_job_contexts,
    enter_job_workspace,
    leave_job_workspace,
)
from app.logging_config import configure_logging, log_event
from app.services.classification_backfill_service import run_backfill_for_workspace

logger = logging.getLogger(__name__)


def run(
    *,
    workspace_id: int,
    batch_size: int | None = None,
    dry_run: bool = False,
    max_pages: int = 1,
    use_model: bool = False,
) -> dict:
    settings = get_settings()
    settings.validate_worker_runtime()
    reports: list[dict] = []
    failures: list[int] = []
    with SessionLocal() as db:
        contexts = all_workspace_job_contexts(db, settings)
        contexts = [value for value in contexts if value.workspace_id == workspace_id]
        if not contexts:
            raise ValueError("Requested workspace does not exist.")
        for context in contexts:
            pages: list[dict] = []
            try:
                for _ in range(max_pages):
                    enter_job_workspace(db, context.workspace_id)
                    result = run_backfill_for_workspace(
                        db,
                        workspace_id=context.workspace_id,
                        settings=settings,
                        batch_size=batch_size,
                        dry_run=dry_run,
                        use_model=use_model,
                    )
                    pages.append(result.safe_metrics())
                    if dry_run or not result.lease_acquired or not result.has_more:
                        break
                reports.append({"workspace_id": context.workspace_id, "pages": pages})
            except Exception as exc:
                db.rollback()
                failures.append(context.workspace_id)
                log_event(
                    logger,
                    "classification_backfill_workspace_failed",
                    level=logging.ERROR,
                    workspace_id=context.workspace_id,
                    error_type=type(exc).__name__,
                )
        leave_job_workspace()
    if failures:
        raise RuntimeError(f"classification_backfill_failed_for_{len(failures)}_workspace(s)")
    return {
        "dry_run": dry_run,
        "model_enabled": use_model,
        "workspaces": reports,
    }


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run bounded, resumable Day 16 classification backfill pages."
    )
    parser.add_argument("--batch-size", type=int, default=None, metavar="1-N")
    parser.add_argument("--workspace-id", type=int, required=True)
    parser.add_argument("--max-pages", type=int, default=1, metavar="1-100")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--use-model",
        action="store_true",
        help="Use one consent-gated bounded model batch for unresolved records.",
    )
    return parser


def main() -> int:
    parser = _argument_parser()
    args = parser.parse_args()
    settings = get_settings()
    maximum = settings.classification_backfill_batch_size
    if args.batch_size is not None and not 1 <= args.batch_size <= maximum:
        parser.error(f"--batch-size must be between 1 and {maximum}")
    if args.workspace_id < 1:
        parser.error("--workspace-id must be positive")
    if not 1 <= args.max_pages <= 100:
        parser.error("--max-pages must be between 1 and 100")
    configure_logging(settings)
    result = run(
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        workspace_id=args.workspace_id,
        max_pages=args.max_pages,
        use_model=args.use_model,
    )
    print(json.dumps({"status": "ok", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

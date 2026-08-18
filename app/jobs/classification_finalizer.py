from __future__ import annotations

import argparse
import json
import logging
import time

from app.config import get_settings
from app.db import SessionLocal
from app.job_tenancy import (
    all_workspace_job_contexts,
    enter_job_workspace,
    leave_job_workspace,
)
from app.logging_config import configure_logging, log_event
from app.services.classification_finalizer_service import run_finalizer_for_workspace

logger = logging.getLogger(__name__)


def run(*, batch_size: int | None = None, use_model: bool = True) -> dict[str, int]:
    settings = get_settings()
    settings.validate_worker_runtime()
    totals = {
        "workspaces": 0,
        "overlap_skips": 0,
        "due": 0,
        "finalized": 0,
        "receipt_lines_finalized": 0,
        "receipt_lines_recovered": 0,
        "transactions_finalized": 0,
        "transactions_recovered": 0,
        "model_calls": 0,
        "fallbacks": 0,
        "failures": 0,
    }
    failures: list[int] = []
    with SessionLocal() as db:
        contexts = all_workspace_job_contexts(db, settings)
        for context in contexts:
            try:
                enter_job_workspace(db, context.workspace_id)
                result = run_finalizer_for_workspace(
                    db,
                    workspace_id=context.workspace_id,
                    settings=settings,
                    batch_size=batch_size,
                    use_model=use_model,
                )
                totals["workspaces"] += 1
                totals["overlap_skips"] += int(not result.lease_acquired)
                totals["due"] += result.due
                totals["finalized"] += result.finalized
                totals["receipt_lines_finalized"] += result.receipt_lines_finalized
                totals["receipt_lines_recovered"] += result.receipt_lines_recovered
                totals["transactions_finalized"] += result.transactions_finalized
                totals["transactions_recovered"] += result.transactions_recovered
                totals["model_calls"] += result.model_calls
                totals["fallbacks"] += result.deterministic_fallbacks
                totals["failures"] += result.failures
                if result.failures:
                    failures.append(context.workspace_id)
            except Exception as exc:
                db.rollback()
                failures.append(context.workspace_id)
                log_event(
                    logger,
                    "classification_finalizer_workspace_failed",
                    level=logging.ERROR,
                    workspace_id=context.workspace_id,
                    error_type=type(exc).__name__,
                )
        leave_job_workspace()
    failed_workspaces = len(set(failures))
    if failed_workspaces:
        raise RuntimeError(
            f"classification_finalizer_failed_for_{failed_workspaces}_workspace(s)"
        )
    return totals


def run_forever(
    *,
    batch_size: int | None = None,
    use_model: bool = True,
    poll_seconds: int | None = None,
) -> None:
    settings = get_settings()
    interval = poll_seconds or settings.classification_finalizer_poll_seconds
    if not 30 <= interval <= 3600:
        raise ValueError("classification finalizer poll interval is out of bounds")
    consecutive_failures = 0
    while True:
        try:
            run(batch_size=batch_size, use_model=use_model)
            consecutive_failures = 0
        except Exception as exc:
            # One-shot mode remains fail-fast for CI and operators.  The fleet
            # worker, however, must not permanently stop serving healthy
            # workspaces because one tenant remains malformed.  Keep the same
            # bounded poll interval and emit only safe operational metadata.
            consecutive_failures += 1
            log_event(
                logger,
                "classification_finalizer_iteration_failed",
                level=logging.ERROR,
                error_type=type(exc).__name__,
                consecutive_failures=consecutive_failures,
                retry_seconds=interval,
            )
        time.sleep(interval)


def main() -> int:
    settings = get_settings()
    configure_logging(settings)
    parser = argparse.ArgumentParser(
        description="Finalize due ExpenseOps classification decisions"
    )
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--poll-seconds", type=int, default=None)
    parser.add_argument("--forever", action="store_true")
    parser.add_argument("--no-model", action="store_true")
    args = parser.parse_args()
    if args.forever:
        run_forever(
            batch_size=args.batch_size,
            use_model=not args.no_model,
            poll_seconds=args.poll_seconds,
        )
        return 0
    result = run(batch_size=args.batch_size, use_model=not args.no_model)
    print(json.dumps({"status": "ok", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

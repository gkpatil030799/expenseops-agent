from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ReplenishmentJobRun, utc_now
from app.services.replenishment_model_service import ReplenishmentModelService
from app.services.replenishment_prediction_service import (
    ReplenishmentPredictionService,
    TrainingDatasetService,
)


class WeeklyReplenishmentService:
    def __init__(self, db: Session):
        self.db = db

    def run(
        self,
        *,
        trigger: str = "scheduled",
        run_key: str | None = None,
        now: datetime | None = None,
    ) -> ReplenishmentJobRun:
        current = now or utc_now()
        iso = current.isocalendar()
        key = run_key or f"weekly-{iso.year}-W{iso.week:02d}"
        existing = self.db.execute(
            select(ReplenishmentJobRun).where(ReplenishmentJobRun.run_key == key)
        ).scalar_one_or_none()
        if existing:
            return existing
        run = ReplenishmentJobRun(
            run_key=key,
            trigger=trigger,
            status="running",
            started_at=current,
            metrics_json={},
        )
        self.db.add(run)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            return self.db.execute(
                select(ReplenishmentJobRun).where(ReplenishmentJobRun.run_key == key)
            ).scalar_one()
        try:
            rows = TrainingDatasetService(self.db).rows()
            model = ReplenishmentModelService(self.db).train_and_evaluate()
            predictions = ReplenishmentPredictionService(self.db).predict_all(now=current)
            run.dataset_size = len(rows)
            run.model_version_id = model.id if model else None
            run.metrics_json = {
                "predictions_generated": len(predictions),
                "training_skipped": model is None,
                "model_status": model.status if model else None,
            }
            run.status = "completed"
            run.completed_at = utc_now()
            self.db.commit()
        except Exception:
            self.db.rollback()
            run = self.db.get(ReplenishmentJobRun, run.id)
            run.status = "failed"
            run.error_code = "pipeline_error"
            run.completed_at = utc_now()
            self.db.commit()
            raise
        self.db.refresh(run)
        return run

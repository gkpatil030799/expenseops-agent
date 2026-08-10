from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import ReplenishmentModelVersion, utc_now
from app.services.replenishment_prediction_service import TrainingDatasetService, TrainingRow


@dataclass(frozen=True)
class ValidationResult:
    method: str
    validation_rows: int
    folds: int
    candidate_predictions: list[float]
    configured_predictions: list[float]
    adaptive_predictions: list[float]
    active_predictions: list[float] | None
    actuals: list[float]


class ReplenishmentModelService:
    def __init__(self, db: Session, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()

    def train_and_evaluate(self) -> ReplenishmentModelVersion | None:
        try:
            return self._train_and_evaluate()
        except Exception as exc:
            self.db.rollback()
            self._record_failed(type(exc).__name__)
            raise

    def _train_and_evaluate(self) -> ReplenishmentModelVersion | None:
        dataset = TrainingDatasetService(self.db, self.settings)
        rows = dataset.rows()
        acquisition_observations = dataset.eligible_acquisition_count or (len(rows) + 2)
        if len(rows) < self.settings.replenishment_ml_min_rows:
            return None
        active_model = self._active_model()
        validation = _validate(rows, self.settings, active_model)
        candidate_mae = _mae(validation.candidate_predictions, validation.actuals)
        baseline_maes = {
            "configured_cadence": _mae(validation.configured_predictions, validation.actuals),
            "adaptive_cadence": _mae(validation.adaptive_predictions, validation.actuals),
        }
        if validation.active_predictions is not None:
            baseline_maes["active_model"] = _mae(validation.active_predictions, validation.actuals)
        best_name, best_mae = min(baseline_maes.items(), key=lambda item: item[1])
        improvement_days = best_mae - candidate_mae
        improvement_pct = (improvement_days / best_mae * 100) if best_mae > 0 else 0.0
        final_artifact = _fit_ridge(rows)
        artifact_stable = _artifact_is_stable(final_artifact)
        outputs_stable = _predictions_are_stable(
            validation.candidate_predictions,
            validation.configured_predictions,
        )
        promote, reason = _promotion_decision(
            candidate_mae=candidate_mae,
            best_baseline_name=best_name,
            best_baseline_mae=best_mae,
            improvement_days=improvement_days,
            improvement_pct=improvement_pct,
            artifact_stable=artifact_stable,
            outputs_stable=outputs_stable,
            settings=self.settings,
        )
        now = utc_now()
        metrics = {
            "total_rows": len(rows),
            "acquisition_observations": acquisition_observations,
            "validation_rows": validation.validation_rows,
            "validation_method": validation.method,
            "validation_folds": validation.folds,
            "candidate_mae_days": round(candidate_mae, 4),
            "model_mae_days": round(candidate_mae, 4),
            "baseline_mae_days": round(best_mae, 4),
            "baseline_maes": {name: round(value, 4) for name, value in baseline_maes.items()},
            "best_baseline": best_name,
            "improvement_days": round(improvement_days, 4),
            "improvement_pct": round(improvement_pct, 4),
            "artifact_stable": artifact_stable,
            "predictions_stable": outputs_stable,
            "promoted": promote,
            "decision_reason": reason,
            "data_confidence_level": model_data_confidence_level(
                total_rows=len(rows),
                validation_rows=validation.validation_rows,
                acquisition_observations=acquisition_observations,
                validation_method=validation.method,
                stable=artifact_stable and outputs_stable,
                settings=self.settings,
            ),
        }
        model = ReplenishmentModelVersion(
            version=f"ridge-{now.strftime('%Y%m%dT%H%M%S%fZ')}",
            algorithm="standardized_ridge_regression",
            status="active" if promote else "rejected",
            trained_at=now,
            training_rows=len(rows),
            training_start_at=rows[0].observed_at,
            training_end_at=rows[-1].observed_at,
            metrics_json=metrics,
            artifact_json=final_artifact,
        )
        if promote:
            self.db.execute(
                update(ReplenishmentModelVersion)
                .where(ReplenishmentModelVersion.status == "active")
                .values(status="superseded")
            )
        self.db.add(model)
        self.db.commit()
        self.db.refresh(model)
        return model

    def rollback_to(self, model_id: int) -> ReplenishmentModelVersion:
        target = self.db.get(ReplenishmentModelVersion, model_id)
        if (
            target is None
            or target.status not in {"superseded", "rolled_back", "active"}
            or not target.artifact_json
        ):
            raise ValueError("Rollback model is missing or has no artifact.")
        if not _artifact_is_stable(target.artifact_json):
            raise ValueError("Rollback model artifact failed stability checks.")
        previous = self._active_model()
        if previous and previous.id != target.id:
            previous.status = "rolled_back"
            previous_metrics = dict(previous.metrics_json or {})
            previous_metrics["rollback_reason"] = f"Rolled back to model {target.version}."
            previous.metrics_json = previous_metrics
        target.status = "active"
        target_metrics = dict(target.metrics_json or {})
        target_metrics["activation_reason"] = "Manual rollback selected this validated artifact."
        target.metrics_json = target_metrics
        self.db.commit()
        self.db.refresh(target)
        return target

    def _active_model(self) -> ReplenishmentModelVersion | None:
        return (
            self.db.execute(
                select(ReplenishmentModelVersion)
                .where(ReplenishmentModelVersion.status == "active")
                .order_by(ReplenishmentModelVersion.trained_at.desc())
            )
            .scalars()
            .first()
        )

    def _record_failed(self, error_type: str) -> None:
        now = utc_now()
        failed = ReplenishmentModelVersion(
            version=f"ridge-failed-{now.strftime('%Y%m%dT%H%M%S%fZ')}",
            algorithm="standardized_ridge_regression",
            status="failed",
            trained_at=now,
            training_rows=0,
            metrics_json={
                "decision_reason": f"Training failed safely ({error_type}).",
                "error_type": error_type,
            },
            artifact_json=None,
        )
        self.db.add(failed)
        self.db.commit()


def _validate(
    rows: list[TrainingRow],
    settings: Settings,
    active_model: ReplenishmentModelVersion | None,
) -> ValidationResult:
    if len(rows) >= settings.replenishment_walk_forward_min_rows:
        return _walk_forward_validation(rows, settings, active_model)
    return _chronological_holdout(rows, settings, active_model)


def _chronological_holdout(
    rows: list[TrainingRow],
    settings: Settings,
    active_model: ReplenishmentModelVersion | None,
) -> ValidationResult:
    validation_count = max(settings.replenishment_ml_min_validation_rows, int(len(rows) * 0.2))
    if validation_count >= len(rows):
        raise ValueError("validation_rows_exceed_dataset")
    train = rows[:-validation_count]
    validation = rows[-validation_count:]
    artifact = _fit_ridge(train)
    return _result_from_predictions(
        method="chronological_holdout",
        rows=validation,
        candidate_predictions=[_predict(artifact, row.features) for row in validation],
        active_model=active_model,
        folds=1,
    )


def _walk_forward_validation(
    rows: list[TrainingRow],
    settings: Settings,
    active_model: ReplenishmentModelVersion | None,
) -> ValidationResult:
    start = max(settings.replenishment_ml_min_rows, len(rows) // 2)
    validation = rows[start:]
    if len(validation) < settings.replenishment_ml_min_validation_rows:
        return _chronological_holdout(rows, settings, active_model)
    predictions = []
    for index in range(start, len(rows)):
        artifact = _fit_ridge(rows[:index])
        predictions.append(_predict(artifact, rows[index].features))
    return _result_from_predictions(
        method="expanding_walk_forward",
        rows=validation,
        candidate_predictions=predictions,
        active_model=active_model,
        folds=len(validation),
    )


def _result_from_predictions(
    *,
    method: str,
    rows: list[TrainingRow],
    candidate_predictions: list[float],
    active_model: ReplenishmentModelVersion | None,
    folds: int,
) -> ValidationResult:
    active_predictions = _active_predictions(active_model, rows)
    return ValidationResult(
        method=method,
        validation_rows=len(rows),
        folds=folds,
        candidate_predictions=candidate_predictions,
        configured_predictions=[
            row.configured_baseline_days
            if row.configured_baseline_days is not None
            else row.features[0]
            for row in rows
        ],
        adaptive_predictions=[row.baseline_days for row in rows],
        active_predictions=active_predictions,
        actuals=[row.target_days for row in rows],
    )


def _active_predictions(
    active_model: ReplenishmentModelVersion | None, rows: list[TrainingRow]
) -> list[float] | None:
    if not active_model or not active_model.artifact_json:
        return None
    try:
        if not _artifact_is_stable(active_model.artifact_json):
            return None
        return [_predict(active_model.artifact_json, row.features) for row in rows]
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None


def _promotion_decision(
    *,
    candidate_mae: float,
    best_baseline_name: str,
    best_baseline_mae: float,
    improvement_days: float,
    improvement_pct: float,
    artifact_stable: bool,
    outputs_stable: bool,
    settings: Settings,
) -> tuple[bool, str]:
    if not artifact_stable:
        return False, "Candidate artifact failed coefficient or numeric stability checks."
    if not outputs_stable:
        return False, "Candidate produced non-finite or extreme validation predictions."
    if candidate_mae >= best_baseline_mae:
        return (
            False,
            f"Candidate MAE {candidate_mae:.2f} did not beat {best_baseline_name} "
            f"MAE {best_baseline_mae:.2f}.",
        )
    if improvement_pct < settings.replenishment_model_min_mae_improvement_pct:
        return (
            False,
            f"Candidate improved MAE by only {improvement_pct:.1f}%, below the required "
            f"{settings.replenishment_model_min_mae_improvement_pct:.1f}% threshold.",
        )
    if improvement_days < settings.replenishment_model_min_mae_improvement_days:
        return (
            False,
            f"Candidate improved MAE by only {improvement_days:.2f} days, below the required "
            f"{settings.replenishment_model_min_mae_improvement_days:.2f} days.",
        )
    return (
        True,
        f"Candidate MAE {candidate_mae:.2f} vs {best_baseline_name} "
        f"{best_baseline_mae:.2f}: {improvement_pct:.1f}% "
        f"({improvement_days:.2f} days) improvement.",
    )


def model_data_confidence_level(
    *,
    total_rows: int,
    validation_rows: int,
    acquisition_observations: int,
    validation_method: str,
    stable: bool,
    settings: Settings,
) -> str:
    if (
        total_rows < settings.replenishment_ml_min_rows
        or validation_rows < settings.replenishment_ml_min_validation_rows
        or acquisition_observations < settings.replenishment_ml_min_rows
    ):
        return "insufficient"
    if not stable or validation_method == "chronological_holdout":
        return "low"
    if total_rows < settings.replenishment_walk_forward_min_rows * 2:
        return "medium"
    if validation_rows < settings.replenishment_ml_min_validation_rows * 2:
        return "medium"
    return "high"


def _fit_ridge(rows: list[TrainingRow], regularization: float = 1.0) -> dict:
    width = len(rows[0].features)
    means = [sum(row.features[i] for row in rows) / len(rows) for i in range(width)]
    scales = []
    for index in range(width):
        variance = sum((row.features[index] - means[index]) ** 2 for row in rows) / len(rows)
        scales.append(max(math.sqrt(variance), 1.0))
    x = [[(value - means[i]) / scales[i] for i, value in enumerate(row.features)] for row in rows]
    y_mean = sum(row.target_days for row in rows) / len(rows)
    y = [row.target_days - y_mean for row in rows]
    matrix = [[0.0 for _ in range(width)] for _ in range(width)]
    vector = [0.0 for _ in range(width)]
    for features, target in zip(x, y, strict=True):
        for i in range(width):
            vector[i] += features[i] * target
            for j in range(width):
                matrix[i][j] += features[i] * features[j]
    for i in range(width):
        matrix[i][i] += regularization
    weights = _solve(matrix, vector)
    return {"means": means, "scales": scales, "weights": weights, "intercept": y_mean}


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    augmented = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    size = len(vector)
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        if abs(divisor) < 1e-12:
            continue
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(augmented[row], augmented[column], strict=True)
            ]
    return [augmented[index][-1] for index in range(size)]


def _predict(artifact: dict, features: list[float]) -> float:
    if len(artifact["means"]) != len(features) or len(artifact["weights"]) != len(features):
        raise ValueError("artifact_feature_width_mismatch")
    normalized = [
        (value - artifact["means"][index]) / artifact["scales"][index]
        for index, value in enumerate(features)
    ]
    return float(artifact["intercept"]) + sum(
        weight * value for weight, value in zip(artifact["weights"], normalized, strict=True)
    )


def _artifact_is_stable(artifact: dict) -> bool:
    try:
        values = [float(artifact["intercept"])] + [float(value) for value in artifact["weights"]]
        scales = [float(value) for value in artifact["scales"]]
        means = [float(value) for value in artifact["means"]]
        same_width = len(scales) == len(means) == len(artifact["weights"]) and bool(scales)
        return bool(
            same_width
            and all(math.isfinite(value) and abs(value) <= 3650 for value in values + means)
            and all(math.isfinite(value) and 0 < value <= 3650 for value in scales)
        )
    except (KeyError, TypeError, ValueError):
        return False


def _predictions_are_stable(predictions: list[float], configured_cadences: list[float]) -> bool:
    return all(
        math.isfinite(prediction) and prediction >= 1.0 and prediction <= max(3.0, cadence * 3.0)
        for prediction, cadence in zip(predictions, configured_cadences, strict=True)
    )


def _mae(predictions: list[float], actuals: list[float]) -> float:
    return sum(
        abs(predicted - actual) for predicted, actual in zip(predictions, actuals, strict=True)
    ) / len(actuals)

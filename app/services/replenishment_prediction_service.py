from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.logging_config import log_event
from app.models import (
    HouseholdItem,
    HouseholdItemAcquisition,
    ReplenishmentFeedback,
    ReplenishmentModelVersion,
    ReplenishmentPrediction,
    utc_now,
)
from app.services.training_eligibility_service import (
    training_eligibility,
)

logger = logging.getLogger(__name__)

FEATURE_NAMES = (
    "cadence_days",
    "history_count",
    "median_interval",
    "weighted_interval",
    "last_interval",
    "quantity",
    "quantity_known",
    "still_have_count",
    "skipped_count",
    "month_sin",
    "month_cos",
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _interval_days(left: datetime, right: datetime) -> float:
    return max(0.25, (_aware(right) - _aware(left)).total_seconds() / 86_400)


def weighted_median(values: list[float]) -> float:
    if not values:
        raise ValueError("values cannot be empty")
    ordered = list(reversed(values[-6:]))
    pairs = sorted((value, 1.0 / (index + 1)) for index, value in enumerate(ordered))
    midpoint = sum(weight for _, weight in pairs) / 2
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= midpoint:
            return value
    return pairs[-1][0]


@dataclass(frozen=True)
class TrainingRow:
    household_item_id: int
    observed_at: datetime
    features: list[float]
    target_days: float
    baseline_days: float
    configured_baseline_days: float | None = None


class TrainingDatasetService:
    def __init__(self, db: Session, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()
        self.exclusions: list[dict[str, int | str]] = []
        self.eligible_acquisition_count = 0

    def rows(self) -> list[TrainingRow]:
        result: list[TrainingRow] = []
        items = list(self.db.execute(select(HouseholdItem).order_by(HouseholdItem.id)).scalars())
        for item in items:
            all_acquisitions = list(
                self.db.execute(
                    select(HouseholdItemAcquisition)
                    .where(
                        HouseholdItemAcquisition.household_item_id == item.id,
                    )
                    .order_by(HouseholdItemAcquisition.acquired_at, HouseholdItemAcquisition.id)
                ).scalars()
            )
            acquisitions = []
            for acquisition in all_acquisitions:
                eligibility = training_eligibility(acquisition, self.settings)
                if eligibility.eligible:
                    acquisitions.append(acquisition)
                    self.eligible_acquisition_count += 1
                else:
                    self.exclusions.append(
                        {
                            "acquisition_id": acquisition.id,
                            "household_item_id": item.id,
                            "reason": eligibility.reason,
                        }
                    )
            for target_index in range(2, len(acquisitions)):
                history = acquisitions[:target_index]
                intervals = [
                    _interval_days(history[index - 1].acquired_at, history[index].acquired_at)
                    for index in range(1, len(history))
                ]
                feedback = self._feedback_before(item.id, history[-1].acquired_at)
                historical_cadence = history[-1].configured_cadence_days or item.cadence_days
                features = feature_vector(
                    item,
                    history,
                    intervals,
                    feedback,
                    cadence_days=historical_cadence,
                )
                target = _interval_days(
                    history[-1].acquired_at, acquisitions[target_index].acquired_at
                )
                result.append(
                    TrainingRow(
                        household_item_id=item.id,
                        observed_at=history[-1].acquired_at,
                        features=features,
                        target_days=target,
                        baseline_days=adaptive_interval(
                            item, intervals, cadence_days=historical_cadence
                        ),
                        configured_baseline_days=float(historical_cadence),
                    )
                )
        return sorted(result, key=lambda row: (_aware(row.observed_at), row.household_item_id))

    def _feedback_before(self, item_id: int, at: datetime) -> list[ReplenishmentFeedback]:
        return list(
            self.db.execute(
                select(ReplenishmentFeedback).where(
                    ReplenishmentFeedback.household_item_id == item_id,
                    ReplenishmentFeedback.occurred_at <= at,
                )
            ).scalars()
        )


def adaptive_interval(
    item: HouseholdItem,
    intervals: list[float],
    *,
    cadence_days: int | None = None,
) -> float:
    configured = cadence_days or item.cadence_days
    if not intervals:
        return float(configured)
    if len(intervals) == 1:
        return round((intervals[0] * 0.65) + (configured * 0.35), 4)
    median = statistics.median(intervals[-8:])
    recent = weighted_median(intervals)
    return round((median * 0.4) + (recent * 0.6), 4)


def feature_vector(
    item: HouseholdItem,
    acquisitions: list[HouseholdItemAcquisition],
    intervals: list[float],
    feedback: list[ReplenishmentFeedback],
    *,
    cadence_days: int | None = None,
) -> list[float]:
    configured = cadence_days or item.cadence_days
    observed = _aware(acquisitions[-1].acquired_at) if acquisitions else utc_now()
    quantity, quantity_known = _comparable_quantity(acquisitions)
    angle = 2 * math.pi * observed.month / 12
    return [
        float(configured),
        float(len(acquisitions)),
        float(statistics.median(intervals[-8:])) if intervals else float(configured),
        float(weighted_median(intervals)) if intervals else float(configured),
        float(intervals[-1]) if intervals else float(configured),
        float(quantity),
        float(quantity_known),
        float(sum(event.feedback_type == "still_have" for event in feedback)),
        float(sum(event.feedback_type == "skipped" for event in feedback)),
        math.sin(angle),
        math.cos(angle),
    ]


class ReplenishmentPredictionService:
    def __init__(self, db: Session, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()

    def predict_item(
        self, item: HouseholdItem, *, now: datetime | None = None, commit: bool = True
    ) -> ReplenishmentPrediction | None:
        current = _aware(now or utc_now())
        acquisitions = list(
            self.db.execute(
                select(HouseholdItemAcquisition)
                .where(
                    HouseholdItemAcquisition.household_item_id == item.id,
                )
                .order_by(HouseholdItemAcquisition.acquired_at, HouseholdItemAcquisition.id)
            ).scalars()
        )
        acquisitions = [
            acquisition
            for acquisition in acquisitions
            if training_eligibility(acquisition, self.settings).eligible
        ]
        if not acquisitions and item.last_acquired_at is None:
            return None
        last_at = _aware(acquisitions[-1].acquired_at if acquisitions else item.last_acquired_at)
        intervals = [
            _interval_days(acquisitions[index - 1].acquired_at, acquisitions[index].acquired_at)
            for index in range(1, len(acquisitions))
        ]
        feedback = list(
            self.db.execute(
                select(ReplenishmentFeedback).where(
                    ReplenishmentFeedback.household_item_id == item.id
                )
            ).scalars()
        )
        features = feature_vector(item, acquisitions, intervals, feedback)
        active_model = (
            self.db.execute(
                select(ReplenishmentModelVersion)
                .where(ReplenishmentModelVersion.status == "active")
                .order_by(ReplenishmentModelVersion.trained_at.desc())
            )
            .scalars()
            .first()
        )
        method = "adaptive_median" if intervals else "configured_cadence"
        interval = adaptive_interval(item, intervals)
        feedback_after_purchase = [
            event
            for event in feedback
            if _aware(event.occurred_at) >= last_at
            and event.feedback_type in {"still_have", "skipped", "too_early"}
        ]
        if feedback_after_purchase:
            interval += min(
                interval * (self.settings.replenishment_max_feedback_cadence_adjustment_pct / 100),
                item.cadence_days * 0.05 * len(feedback_after_purchase),
            )
            method = "adaptive_weighted_feedback"
        model_id = None
        if active_model and active_model.artifact_json:
            try:
                _validate_active_artifact(active_model.artifact_json, len(features))
                candidate_interval = _linear_predict(active_model.artifact_json, features)
                _validate_model_interval(candidate_interval, item.cadence_days)
                interval = candidate_interval
                method = f"ml_ridge_{active_model.id}"
                model_id = active_model.id
            except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
                log_event(
                    logger,
                    "replenishment_model_inference_fallback",
                    level=logging.WARNING,
                    model_version_id=active_model.id,
                    household_item_id=item.id,
                    reason=type(exc).__name__,
                )
        predicted_need = last_at + timedelta(days=interval)
        remaining = (predicted_need - current).total_seconds() / 86_400
        elapsed = max(0.0, (current - last_at).total_seconds() / 86_400)
        confidence_level = prediction_confidence_level(
            acquisition_observations=len(acquisitions),
            intervals=intervals,
            active_model=active_model if model_id else None,
            settings=self.settings,
        )
        prediction = ReplenishmentPrediction(
            household_item_id=item.id,
            generated_at=current,
            predicted_need_at=predicted_need,
            predicted_days_remaining=round(remaining, 4),
            due_score=round(min(1.0, elapsed / max(interval, 0.25)), 4),
            method=method,
            model_version_id=model_id,
            confidence=0.0,
            confidence_level=confidence_level,
            feature_snapshot=dict(zip(FEATURE_NAMES, features, strict=True)),
        )
        self.db.add(prediction)
        if commit:
            self.db.commit()
            self.db.refresh(prediction)
        return prediction

    def predict_all(self, *, now: datetime | None = None) -> list[ReplenishmentPrediction]:
        items = list(
            self.db.execute(select(HouseholdItem).where(HouseholdItem.enabled.is_(True))).scalars()
        )
        predictions = [self.predict_item(item, now=now, commit=False) for item in items]
        self.db.commit()
        return [prediction for prediction in predictions if prediction is not None]


def _linear_predict(artifact: dict, features: list[float]) -> float:
    means = artifact["means"]
    scales = artifact["scales"]
    normalized = [(value - means[index]) / scales[index] for index, value in enumerate(features)]
    return float(artifact["intercept"]) + sum(
        weight * value for weight, value in zip(artifact["weights"], normalized, strict=True)
    )


def _comparable_quantity(
    acquisitions: list[HouseholdItemAcquisition],
) -> tuple[float, int]:
    if not acquisitions:
        return 1.0, 0
    latest = acquisitions[-1]
    if latest.normalized_quantity is None or not latest.normalized_unit:
        return 1.0, 0
    comparable = [
        acquisition
        for acquisition in acquisitions
        if acquisition.normalized_quantity is not None
        and acquisition.normalized_unit == latest.normalized_unit
        and (acquisition.quantity_confidence or 0) >= 0.8
    ]
    if (latest.quantity_confidence or 0) < 0.8 or len(comparable) < 2:
        return 1.0, 0
    return float(latest.normalized_quantity), 1


def _validate_model_interval(interval: float, cadence_days: int) -> None:
    if not math.isfinite(interval):
        raise ValueError("non_finite_prediction")
    if interval < 1.0:
        raise ValueError("negative_or_subday_prediction")
    if interval > max(3.0, cadence_days * 3.0):
        raise ValueError("extreme_prediction")


def _validate_active_artifact(artifact: dict, feature_count: int) -> None:
    means = artifact.get("means")
    scales = artifact.get("scales")
    weights = artifact.get("weights")
    intercept = artifact.get("intercept")
    if not all(isinstance(values, list) for values in (means, scales, weights)):
        raise ValueError("malformed_artifact")
    if not (len(means) == len(scales) == len(weights) == feature_count):
        raise ValueError("artifact_feature_width_mismatch")
    numeric = [float(intercept)] + [float(value) for value in means + scales + weights]
    if not all(math.isfinite(value) and abs(value) <= 3650 for value in numeric):
        raise ValueError("unstable_artifact")
    if any(float(scale) <= 0 for scale in scales):
        raise ValueError("invalid_artifact_scale")


def prediction_confidence_level(
    *,
    acquisition_observations: int,
    intervals: list[float],
    active_model: ReplenishmentModelVersion | None,
    settings: Settings,
) -> str:
    stable = _intervals_stable(intervals)
    if active_model:
        metrics = active_model.metrics_json or {}
        training_rows = int(metrics.get("total_rows", active_model.training_rows))
        validation_rows = int(metrics.get("validation_rows", 0))
        if acquisition_observations < 3:
            return "insufficient"
        if (
            training_rows < settings.replenishment_ml_min_rows
            or validation_rows < settings.replenishment_ml_min_validation_rows
        ):
            return "insufficient"
        if training_rows < settings.replenishment_walk_forward_min_rows or not stable:
            return "low"
        if training_rows < settings.replenishment_walk_forward_min_rows * 2:
            return "medium"
        return (
            "high"
            if validation_rows >= settings.replenishment_ml_min_validation_rows * 2
            else "medium"
        )
    if acquisition_observations < 3:
        return "insufficient"
    if acquisition_observations < 5 or not stable:
        return "low"
    if acquisition_observations < 9:
        return "medium"
    return "high"


def _intervals_stable(intervals: list[float]) -> bool:
    if len(intervals) < 2:
        return False
    mean = statistics.fmean(intervals)
    if mean <= 0:
        return False
    return statistics.pstdev(intervals) / mean <= 0.5

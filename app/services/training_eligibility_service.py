from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, get_settings
from app.models import HouseholdItemAcquisition


@dataclass(frozen=True)
class TrainingEligibility:
    eligible: bool
    reason: str


_TRUSTED_SOURCES = {"manual", "manual_bought", "correction", "transaction_confirmed", "imported"}


def training_eligibility(
    acquisition: HouseholdItemAcquisition,
    settings: Settings | None = None,
) -> TrainingEligibility:
    config = settings or get_settings()
    if acquisition.voided_at is not None:
        return TrainingEligibility(False, "voided")
    if not acquisition.confirmed:
        return TrainingEligibility(False, "not_confirmed")
    if acquisition.source in _TRUSTED_SOURCES:
        return TrainingEligibility(True, "trusted_source")
    if acquisition.confidence >= config.receipt_auto_match_confidence:
        return TrainingEligibility(True, "high_confidence")
    if (
        acquisition.user_confirmed
        and acquisition.confidence >= config.receipt_possible_match_confidence
    ):
        return TrainingEligibility(True, "user_confirmed_medium_confidence")
    return TrainingEligibility(False, "confidence_below_training_threshold")


def is_training_eligible(
    acquisition: HouseholdItemAcquisition,
    settings: Settings | None = None,
) -> bool:
    return training_eligibility(acquisition, settings).eligible

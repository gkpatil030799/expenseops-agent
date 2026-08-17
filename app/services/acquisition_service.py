from __future__ import annotations

import statistics
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    HouseholdCadenceSource,
    HouseholdItem,
    HouseholdItemAcquisition,
    ReplenishmentFeedback,
    ReplenishmentPrediction,
    utc_now,
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class AcquisitionService:
    def __init__(self, db: Session):
        self.db = db

    def record(
        self,
        item: HouseholdItem,
        *,
        acquired_at: datetime | None = None,
        quantity: float | None = None,
        unit: str | None = None,
        normalized_quantity: float | None = None,
        normalized_unit: str | None = None,
        package_size: float | None = None,
        quantity_confidence: float | None = None,
        configured_cadence_days: int | None = None,
        merchant: str | None = None,
        logical_purchase_key: str | None = None,
        receipt_item_id: int | None = None,
        transaction_id: int | None = None,
        source: str = "manual",
        confidence: float = 1.0,
        confirmed: bool = True,
        user_confirmed: bool = False,
        commit: bool = True,
    ) -> HouseholdItemAcquisition:
        if logical_purchase_key is not None:
            existing = self.db.execute(
                select(HouseholdItemAcquisition).where(
                    HouseholdItemAcquisition.logical_purchase_key == logical_purchase_key,
                    HouseholdItemAcquisition.voided_at.is_(None),
                )
            ).scalar_one_or_none()
            if existing:
                if existing.transaction_id is None and transaction_id is not None:
                    existing.transaction_id = transaction_id
                return existing
        if receipt_item_id is not None:
            existing = self.db.execute(
                select(HouseholdItemAcquisition).where(
                    HouseholdItemAcquisition.receipt_item_id == receipt_item_id
                )
            ).scalar_one_or_none()
            if existing:
                return existing
        when = _aware(acquired_at or utc_now())
        if normalized_quantity is None and quantity is not None:
            from app.services.quantity_normalization_service import normalize_quantity

            normalized = normalize_quantity(
                quantity,
                unit,
                source_confidence=quantity_confidence
                if quantity_confidence is not None
                else confidence,
            )
            normalized_quantity = normalized.quantity
            normalized_unit = normalized.unit
            package_size = normalized.package_size
            quantity_confidence = normalized.confidence
        duplicate_conditions = [
            HouseholdItemAcquisition.household_item_id == item.id,
            HouseholdItemAcquisition.voided_at.is_(None),
        ]
        if transaction_id is not None:
            duplicate_conditions.append(HouseholdItemAcquisition.transaction_id == transaction_id)
        elif merchant:
            duplicate_conditions.extend(
                [
                    HouseholdItemAcquisition.merchant_normalized == merchant,
                    func.date(HouseholdItemAcquisition.acquired_at) == when.date(),
                ]
            )
        else:
            duplicate_conditions = []
        if duplicate_conditions:
            duplicate = (
                self.db.execute(select(HouseholdItemAcquisition).where(*duplicate_conditions))
                .scalars()
                .first()
            )
            if duplicate:
                if duplicate.logical_purchase_key is None and logical_purchase_key is not None:
                    duplicate.logical_purchase_key = logical_purchase_key
                if duplicate.transaction_id is None and transaction_id is not None:
                    duplicate.transaction_id = transaction_id
                return duplicate
        acquisition = HouseholdItemAcquisition(
            household_item_id=item.id,
            acquired_at=when,
            quantity=quantity,
            unit=unit,
            normalized_quantity=normalized_quantity,
            normalized_unit=normalized_unit,
            package_size=package_size,
            quantity_confidence=quantity_confidence,
            configured_cadence_days=(
                configured_cadence_days
                if configured_cadence_days is not None
                else (
                    item.cadence_days
                    if item.cadence_source == HouseholdCadenceSource.CONFIGURED.value
                    else None
                )
            ),
            merchant_normalized=merchant,
            logical_purchase_key=logical_purchase_key,
            receipt_item_id=receipt_item_id,
            transaction_id=transaction_id,
            source=source,
            confidence=confidence,
            confirmed=confirmed,
            user_confirmed=user_confirmed,
        )
        if logical_purchase_key:
            try:
                with self.db.begin_nested():
                    self.db.add(acquisition)
                    self.db.flush()
            except IntegrityError:
                existing = self.db.execute(
                    select(HouseholdItemAcquisition).where(
                        HouseholdItemAcquisition.logical_purchase_key == logical_purchase_key,
                        HouseholdItemAcquisition.voided_at.is_(None),
                    )
                ).scalar_one()
                if existing.transaction_id is None and transaction_id is not None:
                    existing.transaction_id = transaction_id
                return existing
        else:
            self.db.add(acquisition)
        self.rebuild_prediction_outcomes(item.id)
        if confirmed and (item.last_acquired_at is None or _aware(item.last_acquired_at) <= when):
            item.last_acquired_at = when
            item.snoozed_until = None
            item.updated_at = utc_now()
        if confirmed:
            self._sync_learning_cadence(item.id)
        if commit:
            self.db.commit()
            self.db.refresh(acquisition)
        return acquisition

    def feedback(
        self,
        item: HouseholdItem,
        feedback_type: str,
        *,
        prediction_id: int | None = None,
        occurred_at: datetime | None = None,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> ReplenishmentFeedback:
        event = ReplenishmentFeedback(
            household_item_id=item.id,
            prediction_id=prediction_id,
            feedback_type=feedback_type,
            occurred_at=occurred_at or utc_now(),
            metadata_json=metadata or {},
        )
        self.db.add(event)
        if commit:
            self.db.commit()
            self.db.refresh(event)
        return event

    def undo(self, acquisition_id: int, *, commit: bool = True) -> HouseholdItemAcquisition:
        acquisition = self.db.get(HouseholdItemAcquisition, acquisition_id)
        if acquisition is None or acquisition.voided_at is not None:
            raise ValueError("Acquisition not found or already undone.")
        acquisition.voided_at = utc_now()
        self._release_logical_key(acquisition)
        self._sync_item_last_acquired(acquisition.household_item_id)
        self._sync_learning_cadence(acquisition.household_item_id)
        self.rebuild_prediction_outcomes(acquisition.household_item_id)
        self.feedback(
            acquisition.household_item,
            "acquisition_undone",
            metadata={"acquisition_id": acquisition.id},
            commit=False,
        )
        if commit:
            self.db.commit()
            self.db.refresh(acquisition)
        return acquisition

    def correct(
        self,
        acquisition_id: int,
        *,
        household_item: HouseholdItem | None = None,
        acquired_at: datetime | None = None,
        quantity: float | None = None,
        unit: str | None = None,
        commit: bool = True,
    ) -> HouseholdItemAcquisition:
        old = self.db.get(HouseholdItemAcquisition, acquisition_id)
        if old is None or old.voided_at is not None:
            raise ValueError("Acquisition not found or already undone.")
        old_item_id = old.household_item_id
        logical_purchase_key = old.logical_purchase_key
        receipt_item_id = old.receipt_item_id
        old.voided_at = utc_now()
        self._release_logical_key(old)
        old.receipt_item_id = None
        from app.services.quantity_normalization_service import normalize_quantity

        corrected_quantity = quantity if quantity is not None else old.quantity
        corrected_unit = unit if unit is not None else old.unit
        normalized = normalize_quantity(
            corrected_quantity,
            corrected_unit,
            source_confidence=1.0,
        )
        replacement = self.record(
            household_item or old.household_item,
            acquired_at=acquired_at or old.acquired_at,
            quantity=corrected_quantity,
            unit=corrected_unit,
            normalized_quantity=normalized.quantity,
            normalized_unit=normalized.unit,
            package_size=normalized.package_size,
            quantity_confidence=normalized.confidence,
            configured_cadence_days=old.configured_cadence_days,
            merchant=old.merchant_normalized,
            logical_purchase_key=logical_purchase_key,
            receipt_item_id=receipt_item_id,
            transaction_id=old.transaction_id,
            source="correction",
            confirmed=True,
            user_confirmed=True,
            commit=False,
        )
        replacement.supersedes_acquisition_id = old.id
        self.feedback(
            household_item or old.household_item,
            "acquisition_corrected",
            metadata={"replaces_acquisition_id": old.id},
            commit=False,
        )
        self._sync_item_last_acquired(old_item_id)
        self._sync_item_last_acquired(replacement.household_item_id)
        self._sync_learning_cadence(old_item_id)
        if replacement.household_item_id != old_item_id:
            self._sync_learning_cadence(replacement.household_item_id)
        self.rebuild_prediction_outcomes(old_item_id)
        if replacement.household_item_id != old_item_id:
            self.rebuild_prediction_outcomes(replacement.household_item_id)
        if commit:
            self.db.commit()
            self.db.refresh(replacement)
        return replacement

    def _sync_item_last_acquired(self, item_id: int) -> None:
        item = self.db.get(HouseholdItem, item_id)
        if item is None:
            return
        latest = (
            self.db.execute(
                select(HouseholdItemAcquisition)
                .where(
                    HouseholdItemAcquisition.household_item_id == item_id,
                    HouseholdItemAcquisition.confirmed.is_(True),
                    HouseholdItemAcquisition.voided_at.is_(None),
                )
                .order_by(HouseholdItemAcquisition.acquired_at.desc())
            )
            .scalars()
            .first()
        )
        item.last_acquired_at = latest.acquired_at if latest else None
        item.updated_at = utc_now()

    def _sync_learning_cadence(self, item_id: int) -> None:
        item = self.db.get(HouseholdItem, item_id)
        if item is None or item.cadence_source == HouseholdCadenceSource.CONFIGURED.value:
            return
        acquisitions = list(
            self.db.execute(
                select(HouseholdItemAcquisition)
                .where(
                    HouseholdItemAcquisition.household_item_id == item_id,
                    HouseholdItemAcquisition.confirmed.is_(True),
                    HouseholdItemAcquisition.voided_at.is_(None),
                )
                .order_by(
                    HouseholdItemAcquisition.acquired_at,
                    HouseholdItemAcquisition.id,
                )
            ).scalars()
        )
        if len(acquisitions) < 2:
            item.cadence_days = None
            item.cadence_source = HouseholdCadenceSource.LEARNING.value
        else:
            intervals = [
                max(
                    1.0,
                    (
                        _aware(acquisitions[index].acquired_at)
                        - _aware(acquisitions[index - 1].acquired_at)
                    ).total_seconds()
                    / 86_400,
                )
                for index in range(1, len(acquisitions))
            ]
            item.cadence_days = max(1, round(statistics.median(intervals[-8:])))
            item.cadence_source = (
                HouseholdCadenceSource.OBSERVED.value
                if len(acquisitions) == 2
                else HouseholdCadenceSource.ADAPTIVE.value
            )
        item.updated_at = utc_now()

    def rebuild_prediction_outcomes(self, item_id: int) -> None:
        from app.services.training_eligibility_service import is_training_eligible

        predictions = list(
            self.db.execute(
                select(ReplenishmentPrediction)
                .where(ReplenishmentPrediction.household_item_id == item_id)
                .order_by(ReplenishmentPrediction.generated_at, ReplenishmentPrediction.id)
            ).scalars()
        )
        acquisitions = [
            acquisition
            for acquisition in self.db.execute(
                select(HouseholdItemAcquisition)
                .where(HouseholdItemAcquisition.household_item_id == item_id)
                .order_by(HouseholdItemAcquisition.acquired_at, HouseholdItemAcquisition.id)
            ).scalars()
            if is_training_eligible(acquisition)
        ]
        for prediction in predictions:
            next_acquisition = next(
                (
                    acquisition
                    for acquisition in acquisitions
                    if _aware(acquisition.acquired_at) > _aware(prediction.generated_at)
                ),
                None,
            )
            prediction.actual_next_acquisition_at = (
                next_acquisition.acquired_at if next_acquisition else None
            )
            prediction.error_days = (
                round(
                    abs(
                        (
                            _aware(next_acquisition.acquired_at)
                            - _aware(prediction.predicted_need_at)
                        ).total_seconds()
                    )
                    / 86_400,
                    4,
                )
                if next_acquisition
                else None
            )

    @staticmethod
    def _release_logical_key(acquisition: HouseholdItemAcquisition) -> None:
        if acquisition.logical_purchase_key:
            acquisition.logical_purchase_key = (
                f"{acquisition.logical_purchase_key[:48]}:void:{acquisition.id}"
            )

from __future__ import annotations

import math
import re
import statistics
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    ClassificationSettings,
    ExpenseTransaction,
    HouseholdCadenceSource,
    HouseholdItem,
    HouseholdItemAcquisition,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReplenishmentFeedback,
    ReplenishmentPrediction,
    utc_now,
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _safe_cadence_provenance(value: dict | None) -> dict:
    result: dict[str, str | int | float | bool] = {"source": "bounded_prior"}
    for key, item in (value or {}).items():
        key_value = str(key)
        if len(result) >= 16 or not re.fullmatch(r"[a-z0-9_]{1,64}", key_value):
            continue
        if isinstance(item, bool):
            result[key_value] = item
        elif isinstance(item, int) and abs(item) <= 1_000_000:
            result[key_value] = item
        elif isinstance(item, float) and math.isfinite(item) and abs(item) <= 1_000_000:
            result[key_value] = item
        elif isinstance(item, str) and re.fullmatch(r"[a-z0-9_.:-]{1,128}", item):
            result[key_value] = item
    return result


def _cadence_prior_snapshot(item: HouseholdItem) -> dict | None:
    if (
        item.cadence_source
        in {
            HouseholdCadenceSource.CATEGORY_PRIOR.value,
            HouseholdCadenceSource.MODEL_PRIOR.value,
        }
        and item.cadence_days
    ):
        return {
            "source": item.cadence_source,
            "days": item.cadence_days,
            "confidence": item.cadence_confidence,
            "min_days": item.cadence_min_days,
            "max_days": item.cadence_max_days,
            "provenance": dict(item.cadence_provenance_json or {}),
        }
    snapshot = (item.cadence_provenance_json or {}).get("prior_snapshot")
    if not isinstance(snapshot, dict):
        return None
    if snapshot.get("source") not in {
        HouseholdCadenceSource.CATEGORY_PRIOR.value,
        HouseholdCadenceSource.MODEL_PRIOR.value,
    }:
        return None
    days = snapshot.get("days")
    if not isinstance(days, int) or not 1 <= days <= 3650:
        return None
    return snapshot


def _restore_cadence_prior(item: HouseholdItem, snapshot: dict) -> None:
    item.cadence_source = str(snapshot["source"])
    item.cadence_days = int(snapshot["days"])
    item.cadence_confidence = min(1.0, max(0.0, float(snapshot.get("confidence", 0.0))))
    item.cadence_min_days = int(snapshot.get("min_days") or item.cadence_days)
    item.cadence_max_days = int(snapshot.get("max_days") or item.cadence_days)
    item.cadence_provenance_json = _safe_cadence_provenance(
        snapshot.get("provenance") if isinstance(snapshot.get("provenance"), dict) else None
    )
    item.cadence_estimated_at = utc_now()


def _quantity_adjusted_intervals(
    acquisitions: list[HouseholdItemAcquisition],
) -> list[float]:
    if len(acquisitions) < 2:
        return []
    latest = acquisitions[-1]
    if (
        latest.normalized_quantity is None
        or latest.normalized_quantity <= 0
        or not latest.normalized_unit
        or (latest.quantity_confidence or 0.0) < 0.8
    ):
        return []
    adjusted: list[float] = []
    for index in range(1, len(acquisitions)):
        prior = acquisitions[index - 1]
        if (
            prior.normalized_quantity is None
            or prior.normalized_quantity <= 0
            or prior.normalized_unit != latest.normalized_unit
            or (prior.quantity_confidence or 0.0) < 0.8
        ):
            return []
        interval = max(
            1.0,
            (_aware(acquisitions[index].acquired_at) - _aware(prior.acquired_at)).total_seconds()
            / 86_400,
        )
        adjusted.append(interval / prior.normalized_quantity * latest.normalized_quantity)
    return adjusted


def _cadence_purchase_episodes(
    acquisitions: list[HouseholdItemAcquisition],
) -> tuple[list[HouseholdItemAcquisition], bool]:
    """Collapse same-day purchases and flag quantity-ambiguous purchase episodes.

    Several receipt lines or purchases on one calendar day establish one elapsed-time
    observation.  Their quantities cannot be combined safely without stronger package
    identity, so quantity-adjusted cadence is disabled for histories containing one.
    """

    episodes: list[HouseholdItemAcquisition] = []
    has_multirow_episode = False
    for acquisition in acquisitions:
        if episodes and _aware(episodes[-1].acquired_at).date() == _aware(
            acquisition.acquired_at
        ).date():
            has_multirow_episode = True
            episodes[-1] = acquisition
        else:
            episodes.append(acquisition)
    return episodes, has_multirow_episode


class AcquisitionService:
    def __init__(self, db: Session):
        self.db = db

    def _active_workspace_id(self, *, sqlite_fallback: int | None = None) -> int:
        workspace_id = self.db.info.get("workspace_id")
        if (
            workspace_id is None
            and sqlite_fallback is not None
            and self.db.get_bind().dialect.name == "sqlite"
        ):
            return sqlite_fallback
        if not isinstance(workspace_id, int) or workspace_id < 1:
            raise ValueError("acquisition requires an authenticated workspace")
        return workspace_id

    def _require_active_item(self, item: HouseholdItem) -> None:
        if item.workspace_id != self._active_workspace_id(sqlite_fallback=item.workspace_id):
            raise ValueError("household item is outside the active workspace")

    def _lock_active_item(self, item: HouseholdItem) -> HouseholdItem:
        return self._lock_active_items(item)[item.id]

    def _lock_active_items(self, *items: HouseholdItem) -> dict[int, HouseholdItem]:
        """Lock tenant-owned items in deterministic primary-key order."""

        if not items:
            return {}
        for item in items:
            self._require_active_item(item)
        workspace_id = items[0].workspace_id
        if any(item.workspace_id != workspace_id for item in items):
            raise ValueError("household items must belong to one active workspace")
        item_ids = sorted({item.id for item in items})
        locked_items = list(
            self.db.scalars(
                select(HouseholdItem)
                .where(
                    HouseholdItem.workspace_id == workspace_id,
                    HouseholdItem.id.in_(item_ids),
                )
                .order_by(HouseholdItem.id)
                .with_for_update(of=HouseholdItem)
                .execution_options(populate_existing=True)
            )
        )
        locked = {item.id: item for item in locked_items}
        if set(locked) != set(item_ids):
            raise ValueError("household item is outside the active workspace")
        return locked

    def _validate_provenance_links(
        self,
        *,
        item_workspace_id: int,
        receipt_item_id: int | None,
        transaction_id: int | None,
    ) -> int | None:
        workspace_id = self._active_workspace_id(sqlite_fallback=item_workspace_id)
        receipt_id = None
        if receipt_item_id is not None:
            receipt_id = self.db.scalar(
                select(PurchaseReceiptItem.receipt_id)
                .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
                .where(
                    PurchaseReceipt.workspace_id == workspace_id,
                    PurchaseReceiptItem.id == receipt_item_id,
                )
            )
            if receipt_id is None:
                raise ValueError("receipt item is outside the active workspace")
        if transaction_id is not None:
            transaction = self.db.scalar(
                select(ExpenseTransaction.id).where(
                    ExpenseTransaction.workspace_id == workspace_id,
                    ExpenseTransaction.id == transaction_id,
                )
            )
            if transaction is None:
                raise ValueError("transaction is outside the active workspace")
        return receipt_id

    def _lock_active_acquisition(
        self,
        acquisition_id: int,
        *,
        workspace_id: int,
    ) -> HouseholdItemAcquisition:
        acquisition = self.db.scalar(
            select(HouseholdItemAcquisition)
            .where(
                HouseholdItemAcquisition.workspace_id == workspace_id,
                HouseholdItemAcquisition.id == acquisition_id,
            )
            .with_for_update(of=HouseholdItemAcquisition)
            .execution_options(populate_existing=True)
        )
        if acquisition is None or acquisition.voided_at is not None:
            raise ValueError("Acquisition not found or already undone.")
        return acquisition

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
        estimate_cadence: bool = True,
        refresh_prediction: bool = False,
        commit: bool = True,
    ) -> HouseholdItemAcquisition:
        item = self._lock_active_item(item)
        if not item.enabled:
            raise ValueError("cannot record an acquisition for a disabled household item")
        receipt_id = self._validate_provenance_links(
            item_workspace_id=item.workspace_id,
            receipt_item_id=receipt_item_id,
            transaction_id=transaction_id,
        )
        if logical_purchase_key is not None:
            existing = self.db.execute(
                select(HouseholdItemAcquisition).where(
                    HouseholdItemAcquisition.workspace_id == item.workspace_id,
                    HouseholdItemAcquisition.logical_purchase_key == logical_purchase_key,
                    HouseholdItemAcquisition.voided_at.is_(None),
                )
            ).scalar_one_or_none()
            if existing:
                self._require_existing_item(existing, item)
                self._merge_existing_confirmation(
                    existing,
                    transaction_id=transaction_id,
                    source=source,
                    confidence=confidence,
                    confirmed=confirmed,
                    user_confirmed=user_confirmed,
                )
                return self._finish_existing(
                    existing,
                    item,
                    estimate_cadence=estimate_cadence,
                    refresh_prediction=refresh_prediction,
                    commit=commit,
                )
        if receipt_item_id is not None:
            existing = self.db.execute(
                select(HouseholdItemAcquisition).where(
                    HouseholdItemAcquisition.workspace_id == item.workspace_id,
                    HouseholdItemAcquisition.receipt_item_id == receipt_item_id,
                    HouseholdItemAcquisition.voided_at.is_(None),
                )
            ).scalar_one_or_none()
            if existing:
                self._require_existing_item(existing, item)
                self._merge_existing_confirmation(
                    existing,
                    transaction_id=transaction_id,
                    source=source,
                    confidence=confidence,
                    confirmed=confirmed,
                    user_confirmed=user_confirmed,
                )
                return self._finish_existing(
                    existing,
                    item,
                    estimate_cadence=estimate_cadence,
                    refresh_prediction=refresh_prediction,
                    commit=commit,
                )
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
        duplicate = None
        if transaction_id is not None:
            candidates = list(
                self.db.scalars(
                    select(HouseholdItemAcquisition).where(
                        HouseholdItemAcquisition.workspace_id == item.workspace_id,
                        HouseholdItemAcquisition.household_item_id == item.id,
                        HouseholdItemAcquisition.voided_at.is_(None),
                        HouseholdItemAcquisition.transaction_id == transaction_id,
                    )
                )
            )
            for candidate in candidates:
                if receipt_id is None or candidate.receipt_item_id is None:
                    duplicate = candidate
                    break
                candidate_receipt_id = self.db.scalar(
                    select(PurchaseReceiptItem.receipt_id).where(
                        PurchaseReceiptItem.id == candidate.receipt_item_id
                    )
                )
                # Distinct lines on one canonical receipt are distinct facts and
                # retain their quantities. A second receipt linked to the same
                # transaction is another representation of that purchase.
                if candidate_receipt_id != receipt_id:
                    duplicate = candidate
                    break
        if duplicate:
            if duplicate.logical_purchase_key is None and logical_purchase_key is not None:
                duplicate.logical_purchase_key = logical_purchase_key
            self._merge_existing_confirmation(
                duplicate,
                transaction_id=transaction_id,
                source=source,
                confidence=confidence,
                confirmed=confirmed,
                user_confirmed=user_confirmed,
            )
            return self._finish_existing(
                duplicate,
                item,
                estimate_cadence=estimate_cadence,
                refresh_prediction=refresh_prediction,
                commit=commit,
            )
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
                        HouseholdItemAcquisition.workspace_id == item.workspace_id,
                        HouseholdItemAcquisition.logical_purchase_key == logical_purchase_key,
                        HouseholdItemAcquisition.voided_at.is_(None),
                    )
                ).scalar_one()
                self._require_existing_item(existing, item)
                self._merge_existing_confirmation(
                    existing,
                    transaction_id=transaction_id,
                    source=source,
                    confidence=confidence,
                    confirmed=confirmed,
                    user_confirmed=user_confirmed,
                )
                return self._finish_existing(
                    existing,
                    item,
                    estimate_cadence=estimate_cadence,
                    refresh_prediction=refresh_prediction,
                    commit=commit,
                )
        else:
            self.db.add(acquisition)
        self.rebuild_prediction_outcomes(item.id)
        if confirmed and (item.last_acquired_at is None or _aware(item.last_acquired_at) <= when):
            item.last_acquired_at = when
            item.snoozed_until = None
            item.updated_at = utc_now()
        if confirmed:
            self._sync_learning_cadence(
                item.id,
                estimate_cadence=estimate_cadence,
            )
        if refresh_prediction:
            self.refresh_basic_prediction(item, commit=False)
        if commit:
            self.db.commit()
            self.db.refresh(acquisition)
        return acquisition

    @staticmethod
    def _require_existing_item(
        acquisition: HouseholdItemAcquisition,
        item: HouseholdItem,
    ) -> None:
        if acquisition.household_item_id != item.id:
            raise ValueError("acquisition identity belongs to another household item")

    @staticmethod
    def _merge_existing_confirmation(
        acquisition: HouseholdItemAcquisition,
        *,
        transaction_id: int | None,
        source: str,
        confidence: float,
        confirmed: bool,
        user_confirmed: bool,
    ) -> None:
        if acquisition.transaction_id is None and transaction_id is not None:
            acquisition.transaction_id = transaction_id
        if confirmed:
            acquisition.confirmed = True
            acquisition.confidence = max(acquisition.confidence, confidence)
        if user_confirmed:
            acquisition.user_confirmed = True
            acquisition.source = source

    def _finish_existing(
        self,
        acquisition: HouseholdItemAcquisition,
        item: HouseholdItem,
        *,
        estimate_cadence: bool,
        refresh_prediction: bool,
        commit: bool,
    ) -> HouseholdItemAcquisition:
        self._require_existing_item(acquisition, item)
        if acquisition.confirmed:
            acquired_at = _aware(acquisition.acquired_at)
            if item.last_acquired_at is None or _aware(item.last_acquired_at) <= acquired_at:
                item.last_acquired_at = acquired_at
                item.snoozed_until = None
                item.updated_at = utc_now()
            self._sync_learning_cadence(
                item.id,
                estimate_cadence=estimate_cadence,
            )
        if refresh_prediction:
            self.refresh_basic_prediction(item, commit=False)
        if commit:
            self.db.commit()
            self.db.refresh(acquisition)
        else:
            self.db.flush()
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
        self._require_active_item(item)
        if prediction_id is not None:
            prediction = self.db.scalar(
                select(ReplenishmentPrediction).where(
                    ReplenishmentPrediction.workspace_id == item.workspace_id,
                    ReplenishmentPrediction.id == prediction_id,
                    ReplenishmentPrediction.household_item_id == item.id,
                )
            )
            if prediction is None:
                raise ValueError("feedback prediction is outside the active household item")
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

    def undo(
        self,
        acquisition_id: int,
        *,
        refresh_prediction: bool = False,
        commit: bool = True,
    ) -> HouseholdItemAcquisition:
        acquisition = self.db.get(HouseholdItemAcquisition, acquisition_id)
        if (
            acquisition is None
            or acquisition.workspace_id
            != self._active_workspace_id(sqlite_fallback=acquisition.workspace_id)
            or acquisition.voided_at is not None
        ):
            raise ValueError("Acquisition not found or already undone.")
        workspace_id = acquisition.workspace_id
        item = self._lock_active_item(acquisition.household_item)
        acquisition = self._lock_active_acquisition(
            acquisition_id,
            workspace_id=workspace_id,
        )
        if acquisition.household_item_id != item.id:
            raise ValueError("acquisition household item changed during correction")
        acquisition.voided_at = utc_now()
        self._release_logical_key(acquisition)
        self._sync_item_last_acquired(acquisition.household_item_id)
        self._sync_learning_cadence(acquisition.household_item_id, repair=True)
        self.rebuild_prediction_outcomes(acquisition.household_item_id)
        self.feedback(
            item,
            "acquisition_undone",
            metadata={"acquisition_id": acquisition.id},
            commit=False,
        )
        if refresh_prediction:
            self.refresh_basic_prediction(item, commit=False)
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
        refresh_prediction: bool = False,
        commit: bool = True,
    ) -> HouseholdItemAcquisition:
        old = self.db.get(HouseholdItemAcquisition, acquisition_id)
        if (
            old is None
            or old.workspace_id != self._active_workspace_id(sqlite_fallback=old.workspace_id)
            or old.voided_at is not None
        ):
            raise ValueError("Acquisition not found or already undone.")
        old_item_candidate = old.household_item
        target_item_candidate = household_item or old_item_candidate
        locked_items = self._lock_active_items(old_item_candidate, target_item_candidate)
        old = self._lock_active_acquisition(
            acquisition_id,
            workspace_id=old.workspace_id,
        )
        if old.household_item_id != old_item_candidate.id:
            raise ValueError("acquisition household item changed during correction")
        old_item = locked_items[old.household_item_id]
        target_item = locked_items[target_item_candidate.id]
        old_item_id = old.household_item_id
        logical_purchase_key = old.logical_purchase_key
        receipt_item_id = old.receipt_item_id
        old.voided_at = utc_now()
        self._release_logical_key(old)
        # Preserve immutable receipt-line provenance. The active-only unique index
        # permits the replacement after the superseded row is visibly voided.
        self.db.flush()
        from app.services.quantity_normalization_service import normalize_quantity

        corrected_quantity = quantity if quantity is not None else old.quantity
        corrected_unit = unit if unit is not None else old.unit
        normalized = normalize_quantity(
            corrected_quantity,
            corrected_unit,
            source_confidence=1.0,
        )
        replacement = self.record(
            target_item,
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
            target_item,
            "acquisition_corrected",
            metadata={"replaces_acquisition_id": old.id},
            commit=False,
        )
        self._sync_item_last_acquired(old_item_id)
        self._sync_item_last_acquired(replacement.household_item_id)
        self._sync_learning_cadence(old_item_id, repair=True)
        if replacement.household_item_id != old_item_id:
            self._sync_learning_cadence(replacement.household_item_id, repair=True)
        self.rebuild_prediction_outcomes(old_item_id)
        if replacement.household_item_id != old_item_id:
            self.rebuild_prediction_outcomes(replacement.household_item_id)
        if refresh_prediction:
            old_item = self.db.get(HouseholdItem, old_item_id)
            if old_item is not None:
                self.refresh_basic_prediction(old_item, commit=False)
        if refresh_prediction and replacement.household_item_id != old_item_id:
            self.refresh_basic_prediction(replacement.household_item, commit=False)
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
        if latest is None:
            self._invalidate_open_predictions(item, reason="acquisition_history_removed")

    def apply_cadence_prior(
        self,
        item: HouseholdItem,
        *,
        cadence_days: int,
        source: HouseholdCadenceSource,
        confidence: float,
        min_days: int | None = None,
        max_days: int | None = None,
        provenance: dict | None = None,
        refresh_prediction: bool = True,
        commit: bool = True,
    ) -> bool:
        """Apply a bounded sparse-data prior without replacing stronger cadence evidence."""

        item = self._lock_active_item(item)
        source = HouseholdCadenceSource(source)
        if source not in {
            HouseholdCadenceSource.CATEGORY_PRIOR,
            HouseholdCadenceSource.MODEL_PRIOR,
        }:
            raise ValueError("cadence prior source must be category_prior or model_prior")
        if not isinstance(cadence_days, int) or cadence_days < 1 or cadence_days > 3650:
            raise ValueError("cadence prior must be between 1 and 3650 days")
        if not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 1:
            raise ValueError("cadence prior confidence must be between 0 and 1")
        lower = cadence_days if min_days is None else min_days
        upper = cadence_days if max_days is None else max_days
        if not (1 <= lower <= cadence_days <= upper <= 3650):
            raise ValueError("cadence prior range is invalid")
        settings = self.db.scalar(
            select(ClassificationSettings).where(
                ClassificationSettings.workspace_id == item.workspace_id
            )
        )
        if settings is not None and not settings.cadence_estimation_enabled:
            return False
        current = HouseholdCadenceSource(item.cadence_source)
        protected = {
            HouseholdCadenceSource.CONFIGURED,
            HouseholdCadenceSource.OBSERVED,
            HouseholdCadenceSource.QUANTITY_ADJUSTED,
            HouseholdCadenceSource.ADAPTIVE,
        }
        if current in protected:
            return False
        if (
            current is HouseholdCadenceSource.CATEGORY_PRIOR
            and source is HouseholdCadenceSource.MODEL_PRIOR
        ):
            return False
        safe_provenance = _safe_cadence_provenance(provenance)
        if current is source and item.cadence_confidence > float(confidence):
            return False
        if (
            current is source
            and item.cadence_days == cadence_days
            and item.cadence_confidence == float(confidence)
            and item.cadence_min_days == lower
            and item.cadence_max_days == upper
            and (item.cadence_provenance_json or {}) == safe_provenance
        ):
            return False
        old_source = item.cadence_source
        old_days = item.cadence_days
        old_confidence = item.cadence_confidence
        old_min_days = item.cadence_min_days
        old_max_days = item.cadence_max_days
        old_provenance = dict(item.cadence_provenance_json or {})
        item.cadence_days = cadence_days
        item.cadence_source = source.value
        item.cadence_confidence = float(confidence)
        item.cadence_min_days = lower
        item.cadence_max_days = upper
        item.cadence_provenance_json = safe_provenance
        item.cadence_estimated_at = utc_now()
        item.updated_at = utc_now()
        self._record_cadence_change(
            item,
            old_source=old_source,
            old_days=old_days,
            old_confidence=old_confidence,
            old_min_days=old_min_days,
            old_max_days=old_max_days,
            old_provenance=old_provenance,
        )
        self.db.flush()
        if refresh_prediction:
            self.refresh_basic_prediction(item, commit=False)
        if commit:
            self.db.commit()
            self.db.refresh(item)
        return True

    def _sync_learning_cadence(
        self,
        item_id: int,
        *,
        estimate_cadence: bool = True,
        repair: bool = False,
    ) -> bool:
        item = self.db.get(HouseholdItem, item_id)
        if (
            item is None
            or (not estimate_cadence and not repair)
            or item.cadence_source == HouseholdCadenceSource.CONFIGURED.value
        ):
            return False
        settings = self.db.scalar(
            select(ClassificationSettings).where(
                ClassificationSettings.workspace_id == item.workspace_id
            )
        )
        if settings is not None and not settings.cadence_estimation_enabled and not repair:
            return False
        old_source = item.cadence_source
        old_days = item.cadence_days
        old_confidence = item.cadence_confidence
        old_min_days = item.cadence_min_days
        old_max_days = item.cadence_max_days
        old_provenance = dict(item.cadence_provenance_json or {})
        prior_snapshot = _cadence_prior_snapshot(item)
        raw_acquisitions = list(
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
        acquisitions, quantity_ambiguous = _cadence_purchase_episodes(raw_acquisitions)
        if len(acquisitions) < 2:
            if prior_snapshot is not None:
                _restore_cadence_prior(item, prior_snapshot)
            elif item.cadence_source not in {
                HouseholdCadenceSource.CATEGORY_PRIOR.value,
                HouseholdCadenceSource.MODEL_PRIOR.value,
            }:
                item.cadence_days = None
                item.cadence_source = HouseholdCadenceSource.LEARNING.value
                item.cadence_confidence = 0.0
                item.cadence_min_days = None
                item.cadence_max_days = None
                item.cadence_provenance_json = {
                    "source": "insufficient_history",
                    "observation_count": len(acquisitions),
                }
                item.cadence_estimated_at = None
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
            recent_intervals = intervals[-8:]
            cadence = max(1, round(statistics.median(recent_intervals)))
            adjusted_values = (
                []
                if quantity_ambiguous
                else _quantity_adjusted_intervals(acquisitions[-9:])
            )
            quantity_adjusted = bool(adjusted_values)
            if adjusted_values:
                adjusted = max(1, round(statistics.median(adjusted_values)))
                if abs(adjusted - cadence) / max(cadence, 1) >= 0.10:
                    cadence = adjusted
                else:
                    quantity_adjusted = False
            item.cadence_days = cadence
            item.cadence_source = (
                HouseholdCadenceSource.QUANTITY_ADJUSTED.value
                if quantity_adjusted
                else HouseholdCadenceSource.OBSERVED.value
                if len(acquisitions) == 2
                else HouseholdCadenceSource.ADAPTIVE.value
            )
            range_values = adjusted_values if quantity_adjusted else recent_intervals
            item.cadence_min_days = max(1, round(min(range_values)))
            item.cadence_max_days = max(item.cadence_min_days, round(max(range_values)))
            item.cadence_confidence = min(
                0.95,
                (0.72 if quantity_adjusted else 0.65) + 0.04 * max(0, len(intervals) - 1),
            )
            item.cadence_provenance_json = {
                "source": "confirmed_acquisition_history",
                "observation_count": len(acquisitions),
                "raw_acquisition_count": len(raw_acquisitions),
                "interval_count": len(intervals),
                "quantity_adjusted": quantity_adjusted,
                "quantity_ambiguous_same_day": quantity_ambiguous,
                **({"prior_snapshot": prior_snapshot} if prior_snapshot else {}),
            }
            item.cadence_estimated_at = utc_now()
        item.updated_at = utc_now()
        changed = (
            item.cadence_source != old_source
            or item.cadence_days != old_days
            or item.cadence_confidence != old_confidence
            or item.cadence_min_days != old_min_days
            or item.cadence_max_days != old_max_days
            or (item.cadence_provenance_json or {}) != old_provenance
        )
        if changed:
            self._record_cadence_change(
                item,
                old_source=old_source,
                old_days=old_days,
                old_confidence=old_confidence,
                old_min_days=old_min_days,
                old_max_days=old_max_days,
                old_provenance=old_provenance,
            )
        return changed

    def refresh_basic_prediction(
        self,
        item: HouseholdItem,
        *,
        commit: bool = False,
    ) -> ReplenishmentPrediction | None:
        """Persist a local deterministic prediction when enough cadence state exists."""

        if item.cadence_days is None or item.last_acquired_at is None:
            self._invalidate_open_predictions(item, reason="cadence_or_history_unavailable")
            if commit:
                self.db.commit()
            return None
        from app.services.replenishment_prediction_service import ReplenishmentPredictionService

        prediction = ReplenishmentPredictionService(self.db).predict_item(item, commit=False)
        if commit:
            self.db.commit()
            if prediction is not None:
                self.db.refresh(prediction)
        return prediction

    def repair_item_after_history_change(
        self,
        item: HouseholdItem,
        *,
        refresh_prediction: bool = True,
    ) -> None:
        """Rebuild canonical derived state after a bounded history correction."""

        if item.workspace_id != self._active_workspace_id(sqlite_fallback=item.workspace_id):
            raise ValueError("household item is outside the active workspace")
        self._sync_item_last_acquired(item.id)
        self._sync_learning_cadence(item.id, repair=True)
        self.rebuild_prediction_outcomes(item.id)
        if refresh_prediction:
            self.refresh_basic_prediction(item, commit=False)

    def _invalidate_open_predictions(self, item: HouseholdItem, *, reason: str) -> int:
        result = self.db.execute(
            delete(ReplenishmentPrediction).where(
                ReplenishmentPrediction.workspace_id == item.workspace_id,
                ReplenishmentPrediction.household_item_id == item.id,
                ReplenishmentPrediction.actual_next_acquisition_at.is_(None),
            )
        )
        count = int(result.rowcount or 0)
        if count:
            self.db.add(
                AuditEvent(
                    workspace_id=item.workspace_id,
                    user_id=self.db.info.get("user_id"),
                    event_type="replenishment_predictions_invalidated",
                    resource_type="household_item",
                    resource_id=str(item.id),
                    metadata_json={"count": count, "reason": reason},
                )
            )
        return count

    def _record_cadence_change(
        self,
        item: HouseholdItem,
        *,
        old_source: str,
        old_days: int | None,
        old_confidence: float | None = None,
        old_min_days: int | None = None,
        old_max_days: int | None = None,
        old_provenance: dict | None = None,
    ) -> None:
        self.db.add(
            AuditEvent(
                workspace_id=item.workspace_id,
                user_id=self.db.info.get("user_id"),
                event_type="household_cadence_source_changed",
                resource_type="household_item",
                resource_id=str(item.id),
                metadata_json={
                    "old_source": old_source,
                    "new_source": item.cadence_source,
                    "old_days": old_days,
                    "new_days": item.cadence_days,
                    "old_confidence": old_confidence,
                    "confidence": item.cadence_confidence,
                    "old_min_days": old_min_days,
                    "new_min_days": item.cadence_min_days,
                    "old_max_days": old_max_days,
                    "new_max_days": item.cadence_max_days,
                    "source_changed": old_source != item.cadence_source,
                    "old_observation_count": (old_provenance or {}).get(
                        "observation_count"
                    ),
                    "new_observation_count": (item.cadence_provenance_json or {}).get(
                        "observation_count"
                    ),
                },
            )
        )

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

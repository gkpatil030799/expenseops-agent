from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    ClassificationConcept,
    Errand,
    ErrandHouseholdItem,
    ErrandPlan,
    ErrandPlanStop,
    ErrandPlanStopHouseholdItem,
    HouseholdItem,
    HouseholdItemAcquisition,
    HouseholdItemAlias,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReplenishmentPrediction,
    utc_now,
)
from app.services.acquisition_service import AcquisitionService
from app.services.classification_taxonomy_service import (
    ClassificationCollisionError,
    ClassificationNotFoundError,
    ClassificationTaxonomyError,
    ClassificationTaxonomyService,
)

MAX_HOUSEHOLD_ITEM_MERGE_ROWS = 250


@dataclass(frozen=True)
class HouseholdItemMergeResult:
    applied: bool
    source_household_item_id: int
    target_household_item_id: int
    target_name: str
    merge_event_id: int
    aliases_moved: int
    receipt_items_updated: int
    acquisitions_moved: int
    errand_links_updated: int
    plan_links_updated: int
    predictions_invalidated: int
    reverted: bool = False


class HouseholdItemMergeService:
    """Owner-invoked, tenant-scoped merge/undo for canonical household items.

    Provider records and immutable classification/acquisition facts are never deleted.
    Current foreign keys are repaired under locks; the audit snapshot makes the bounded
    operation reversible while the source remains a disabled tombstone.
    """

    def __init__(self, db: Session):
        self.db = db
        workspace_id = db.info.get("workspace_id")
        if workspace_id is None and db.get_bind().dialect.name == "sqlite":
            workspace_id = 1
        if not isinstance(workspace_id, int) or workspace_id < 1:
            raise ClassificationTaxonomyError(
                "household item merge requires an authenticated workspace"
            )
        self.workspace_id = workspace_id

    def merge(
        self,
        source_item_id: int,
        *,
        target_item_id: int,
        commit: bool = True,
    ) -> HouseholdItemMergeResult:
        if source_item_id < 1 or target_item_id < 1:
            raise ClassificationTaxonomyError("household item ids must be positive")
        if source_item_id == target_item_id:
            raise ClassificationCollisionError("a household item cannot be merged into itself")
        preview = list(
            self.db.scalars(
                select(HouseholdItem).where(
                    HouseholdItem.workspace_id == self.workspace_id,
                    HouseholdItem.id.in_((source_item_id, target_item_id)),
                )
            )
        )
        if len(preview) != 2:
            raise ClassificationNotFoundError("household item not found")
        # Receipt correction uses line -> item ordering. Acquire the same first
        # lock here before sorted item locks to avoid an item/line deadlock cycle.
        receipt_items = self._lock_receipt_items(source_item_id)
        items = self._lock_items((source_item_id, target_item_id))
        source = items.get(source_item_id)
        target = items.get(target_item_id)
        if source is None or target is None:
            raise ClassificationNotFoundError("household item not found")
        if not source.enabled or source.merged_into_id is not None:
            raise ClassificationCollisionError("source household item is not active")
        if not target.enabled or target.merged_into_id is not None:
            raise ClassificationCollisionError("target household item is not active")
        # A merge cycle cannot form: both sides must already be active and
        # unmerged (checked above), so the target can never point back into
        # a chain that would loop to source.
        if (
            source.spending_parent_category != target.spending_parent_category
            or source.replenishment_eligibility != target.replenishment_eligibility
        ):
            raise ClassificationCollisionError(
                "household item classifications are incompatible; correct them before merging"
            )
        if source.classification_concept_id != target.classification_concept_id:
            raise ClassificationCollisionError(
                "household items use different concepts; merge the compatible concepts first"
            )

        aliases = self._lock_aliases((source.id, target.id))
        source_aliases = [value for value in aliases if value.household_item_id == source.id]
        target_alias_keys = {
            (value.merchant_normalized, value.normalized_alias)
            for value in aliases
            if value.household_item_id == target.id and value.voided_at is None
        }
        aliases_to_move = [
            value
            for value in source_aliases
            if value.voided_at is not None
            or (value.merchant_normalized, value.normalized_alias) not in target_alias_keys
        ]
        acquisitions = self._lock_acquisitions(source.id)
        errand_links = self._lock_errand_links((source.id, target.id))
        plan_links = self._lock_plan_links((source.id, target.id))
        predictions = self._lock_open_predictions((source.id, target.id))

        moved_errand_ids: list[int] = []
        deduped_errand_ids: list[int] = []
        moved_plan_stop_ids: list[int] = []
        deduped_plan_links: list[dict[str, int | str | None]] = []
        now = utc_now()
        taxonomy = ClassificationTaxonomyService(self.db)
        try:
            with self.db.begin_nested():
                for alias in aliases_to_move:
                    alias.household_item_id = target.id
                for line in receipt_items:
                    taxonomy.retarget_receipt_line_household_item(
                        line,
                        household_item_id=target.id,
                        provenance="household_item_merge",
                        now=now,
                    )
                for acquisition in acquisitions:
                    acquisition.household_item_id = target.id

                target_errands = {
                    link.errand_id for link in errand_links if link.household_item_id == target.id
                }
                for link in [
                    value for value in errand_links if value.household_item_id == source.id
                ]:
                    if link.errand_id in target_errands:
                        deduped_errand_ids.append(link.errand_id)
                        self.db.delete(link)
                    else:
                        moved_errand_ids.append(link.id)
                        link.household_item_id = target.id

                target_stops = {
                    link.stop_id for link in plan_links if link.household_item_id == target.id
                }
                for link in [value for value in plan_links if value.household_item_id == source.id]:
                    if link.stop_id in target_stops:
                        deduped_plan_links.append({"stop_id": link.stop_id, "reason": link.reason})
                        self.db.delete(link)
                    else:
                        moved_plan_stop_ids.append(link.id)
                        link.household_item_id = target.id

                for prediction in predictions:
                    self.db.delete(prediction)
                source.enabled = False
                source.merged_into_id = target.id
                source.merged_at = now
                source.updated_at = now
                target.updated_at = now
                self.db.flush()

                acquisition_service = AcquisitionService(self.db)
                acquisition_service.repair_item_after_history_change(target)
                event = AuditEvent(
                    workspace_id=self.workspace_id,
                    user_id=self.db.info.get("user_id"),
                    event_type="household_item_merged",
                    resource_type="household_item",
                    resource_id=str(source.id),
                    metadata_json={
                        "source_household_item_id": source.id,
                        "source_name": source.name,
                        "source_classification_concept_id": source.classification_concept_id,
                        "target_household_item_id": target.id,
                        "target_name": target.name,
                        "moved_alias_ids": [value.id for value in aliases_to_move],
                        "moved_receipt_item_ids": [value.id for value in receipt_items],
                        "moved_acquisition_ids": [value.id for value in acquisitions],
                        "moved_errand_link_ids": moved_errand_ids,
                        "deduped_errand_ids": deduped_errand_ids,
                        "moved_plan_link_ids": moved_plan_stop_ids,
                        "deduped_plan_links": deduped_plan_links,
                        "predictions_invalidated": len(predictions),
                        "historical_predictions_retargeted": False,
                        "historical_feedback_retargeted": False,
                        "provider_records_changed": False,
                    },
                )
                self.db.add(event)
                self.db.flush()
        except IntegrityError:
            raise ClassificationCollisionError(
                "household item merge conflicted; no partial merge was applied"
            ) from None
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return HouseholdItemMergeResult(
            applied=True,
            source_household_item_id=source.id,
            target_household_item_id=target.id,
            target_name=target.name,
            merge_event_id=event.id,
            aliases_moved=len(aliases_to_move),
            receipt_items_updated=len(receipt_items),
            acquisitions_moved=len(acquisitions),
            errand_links_updated=len(moved_errand_ids) + len(deduped_errand_ids),
            plan_links_updated=len(moved_plan_stop_ids) + len(deduped_plan_links),
            predictions_invalidated=len(predictions),
        )

    def undo(
        self,
        source_item_id: int,
        *,
        merge_event_id: int,
        commit: bool = True,
    ) -> HouseholdItemMergeResult:
        if source_item_id < 1 or merge_event_id < 1:
            raise ClassificationTaxonomyError("merge identifiers must be positive")
        event_preview = self.db.scalar(
            select(AuditEvent).where(
                AuditEvent.workspace_id == self.workspace_id,
                AuditEvent.id == merge_event_id,
                AuditEvent.event_type == "household_item_merged",
                AuditEvent.resource_type == "household_item",
                AuditEvent.resource_id == str(source_item_id),
            )
        )
        if event_preview is None:
            raise ClassificationNotFoundError("household item merge event not found")
        metadata = event_preview.metadata_json or {}
        target_item_id = self._positive_id(metadata.get("target_household_item_id"))
        moved_alias_ids = self._id_list(metadata, "moved_alias_ids")
        moved_receipt_ids = self._id_list(metadata, "moved_receipt_item_ids")
        moved_acquisition_ids = self._id_list(metadata, "moved_acquisition_ids")
        moved_errand_link_ids = self._id_list(metadata, "moved_errand_link_ids")
        deduped_errand_ids = self._id_list(metadata, "deduped_errand_ids")
        moved_plan_link_ids = self._id_list(metadata, "moved_plan_link_ids")
        deduped_plan_links = self._plan_link_snapshots(metadata)

        # Match receipt-correction lock order before locking the item pair.
        receipt_items = self._lock_receipt_items_by_ids(moved_receipt_ids, target_item_id)
        # AuditEvent rows are append-only elsewhere in this codebase -- nothing
        # updates metadata_json in place after creation -- so re-checking the
        # row still exists under lock is sufficient; it cannot have changed
        # shape out from under the unlocked preview above.
        event = self.db.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == self.workspace_id,
                AuditEvent.id == merge_event_id,
                AuditEvent.event_type == "household_item_merged",
                AuditEvent.resource_type == "household_item",
                AuditEvent.resource_id == str(source_item_id),
            )
            .with_for_update(of=AuditEvent)
        )
        if event is None:
            raise ClassificationCollisionError("household item merge audit changed")
        items = self._lock_items((source_item_id, target_item_id))
        source = items.get(source_item_id)
        target = items.get(target_item_id)
        if source is None or target is None:
            raise ClassificationNotFoundError("household item not found")
        if source.enabled or source.merged_into_id != target.id or source.merged_at is None:
            raise ClassificationCollisionError("household item merge is no longer reversible")
        if not target.enabled or target.merged_into_id is not None:
            raise ClassificationCollisionError("merge target is no longer active")
        source_concept_id = metadata.get("source_classification_concept_id")
        if source_concept_id is not None:
            concept = self.db.scalar(
                select(ClassificationConcept).where(
                    ClassificationConcept.workspace_id == self.workspace_id,
                    ClassificationConcept.id == self._positive_id(source_concept_id),
                )
            )
            if concept is None or concept.merged_into_id is not None:
                raise ClassificationCollisionError(
                    "the source taxonomy changed after this merge; undo it before "
                    "restoring the item"
                )

        aliases = self._lock_rows_by_ids(
            HouseholdItemAlias,
            moved_alias_ids,
            HouseholdItemAlias.household_item_id == target.id,
        )
        acquisitions = self._lock_rows_by_ids(
            HouseholdItemAcquisition,
            moved_acquisition_ids,
            HouseholdItemAcquisition.workspace_id == self.workspace_id,
            HouseholdItemAcquisition.household_item_id == target.id,
        )
        errand_links = self._lock_errand_links_by_ids(moved_errand_link_ids, target.id)
        plan_links = self._lock_plan_links_by_ids(moved_plan_link_ids, target.id)
        now = utc_now()
        taxonomy = ClassificationTaxonomyService(self.db)
        try:
            with self.db.begin_nested():
                for alias in aliases:
                    alias.household_item_id = source.id
                for line in receipt_items:
                    taxonomy.retarget_receipt_line_household_item(
                        line,
                        household_item_id=source.id,
                        provenance="household_item_merge_undo",
                        now=now,
                    )
                for acquisition in acquisitions:
                    acquisition.household_item_id = source.id
                for link in errand_links:
                    link.household_item_id = source.id
                for errand_id in deduped_errand_ids:
                    self.db.add(
                        ErrandHouseholdItem(
                            errand_id=errand_id,
                            household_item_id=source.id,
                        )
                    )
                for link in plan_links:
                    link.household_item_id = source.id
                for snapshot in deduped_plan_links:
                    self.db.add(
                        ErrandPlanStopHouseholdItem(
                            stop_id=snapshot["stop_id"],
                            household_item_id=source.id,
                            reason=snapshot["reason"],
                        )
                    )
                source.enabled = True
                source.merged_into_id = None
                source.merged_at = None
                source.updated_at = now
                self.db.flush()
                acquisition_service = AcquisitionService(self.db)
                acquisition_service.repair_item_after_history_change(source)
                acquisition_service.repair_item_after_history_change(target)
                undo_event = AuditEvent(
                    workspace_id=self.workspace_id,
                    user_id=self.db.info.get("user_id"),
                    event_type="household_item_merge_reverted",
                    resource_type="household_item",
                    resource_id=str(source.id),
                    metadata_json={
                        "merge_event_id": event.id,
                        "source_household_item_id": source.id,
                        "target_household_item_id": target.id,
                        "provider_records_changed": False,
                    },
                )
                self.db.add(undo_event)
                self.db.flush()
        except IntegrityError:
            raise ClassificationCollisionError(
                "household item merge undo conflicted; no partial undo was applied"
            ) from None
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return HouseholdItemMergeResult(
            applied=True,
            source_household_item_id=source.id,
            target_household_item_id=target.id,
            target_name=target.name,
            merge_event_id=event.id,
            aliases_moved=len(aliases),
            receipt_items_updated=len(receipt_items),
            acquisitions_moved=len(acquisitions),
            errand_links_updated=len(errand_links) + len(deduped_errand_ids),
            plan_links_updated=len(plan_links) + len(deduped_plan_links),
            predictions_invalidated=0,
            reverted=True,
        )

    def _lock_items(self, item_ids: tuple[int, ...]) -> dict[int, HouseholdItem]:
        rows = list(
            self.db.scalars(
                select(HouseholdItem)
                .where(
                    HouseholdItem.workspace_id == self.workspace_id,
                    HouseholdItem.id.in_(sorted(set(item_ids))),
                )
                .order_by(HouseholdItem.id)
                .with_for_update(of=HouseholdItem)
            )
        )
        return {row.id: row for row in rows}

    def _bounded(self, values: list[Any], noun: str) -> list[Any]:
        if len(values) > MAX_HOUSEHOLD_ITEM_MERGE_ROWS:
            raise ClassificationTaxonomyError(
                f"household item has too many {noun} for an online merge"
            )
        return values

    def _locked_bounded(self, statement, model, order_col, noun: str) -> list[Any]:
        return self._bounded(
            list(
                self.db.scalars(
                    statement.order_by(order_col)
                    .limit(MAX_HOUSEHOLD_ITEM_MERGE_ROWS + 1)
                    .with_for_update(of=model)
                )
            ),
            noun,
        )

    def _lock_aliases(self, item_ids: tuple[int, ...]) -> list[HouseholdItemAlias]:
        statement = select(HouseholdItemAlias).where(
            HouseholdItemAlias.household_item_id.in_(item_ids)
        )
        return self._locked_bounded(statement, HouseholdItemAlias, HouseholdItemAlias.id, "aliases")

    def _lock_receipt_items(self, item_id: int) -> list[PurchaseReceiptItem]:
        statement = (
            select(PurchaseReceiptItem)
            .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
            .where(
                PurchaseReceipt.workspace_id == self.workspace_id,
                PurchaseReceiptItem.household_item_id == item_id,
            )
        )
        return self._locked_bounded(
            statement, PurchaseReceiptItem, PurchaseReceiptItem.id, "receipt lines"
        )

    def _lock_acquisitions(self, item_id: int) -> list[HouseholdItemAcquisition]:
        statement = select(HouseholdItemAcquisition).where(
            HouseholdItemAcquisition.workspace_id == self.workspace_id,
            HouseholdItemAcquisition.household_item_id == item_id,
        )
        return self._locked_bounded(
            statement, HouseholdItemAcquisition, HouseholdItemAcquisition.id, "acquisitions"
        )

    def _lock_errand_links(self, item_ids: tuple[int, ...]) -> list[ErrandHouseholdItem]:
        statement = (
            select(ErrandHouseholdItem)
            .join(Errand, Errand.id == ErrandHouseholdItem.errand_id)
            .where(
                Errand.workspace_id == self.workspace_id,
                ErrandHouseholdItem.household_item_id.in_(item_ids),
            )
        )
        return self._locked_bounded(
            statement, ErrandHouseholdItem, ErrandHouseholdItem.id, "errand links"
        )

    def _lock_plan_links(
        self,
        item_ids: tuple[int, ...],
    ) -> list[ErrandPlanStopHouseholdItem]:
        statement = (
            select(ErrandPlanStopHouseholdItem)
            .join(
                ErrandPlanStop,
                ErrandPlanStop.id == ErrandPlanStopHouseholdItem.stop_id,
            )
            .join(ErrandPlan, ErrandPlan.id == ErrandPlanStop.plan_id)
            .where(
                ErrandPlan.workspace_id == self.workspace_id,
                ErrandPlanStopHouseholdItem.household_item_id.in_(item_ids),
            )
        )
        return self._locked_bounded(
            statement, ErrandPlanStopHouseholdItem, ErrandPlanStopHouseholdItem.id, "plan links"
        )

    def _lock_open_predictions(self, item_ids: tuple[int, ...]) -> list[ReplenishmentPrediction]:
        statement = select(ReplenishmentPrediction).where(
            ReplenishmentPrediction.workspace_id == self.workspace_id,
            ReplenishmentPrediction.household_item_id.in_(item_ids),
            ReplenishmentPrediction.actual_next_acquisition_at.is_(None),
        )
        return self._locked_bounded(
            statement, ReplenishmentPrediction, ReplenishmentPrediction.id, "open predictions"
        )

    def _lock_rows_by_ids(self, model, ids: list[int], *criteria):
        if not ids:
            return []
        rows = list(
            self.db.scalars(
                select(model)
                .where(model.id.in_(ids), *criteria)
                .order_by(model.id)
                .with_for_update(of=model)
            )
        )
        if len(rows) != len(ids):
            raise ClassificationCollisionError(
                "merged household history changed and cannot be undone safely"
            )
        return rows

    def _lock_receipt_items_by_ids(
        self,
        ids: list[int],
        item_id: int,
    ) -> list[PurchaseReceiptItem]:
        if not ids:
            return []
        rows = list(
            self.db.scalars(
                select(PurchaseReceiptItem)
                .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
                .where(
                    PurchaseReceipt.workspace_id == self.workspace_id,
                    PurchaseReceiptItem.id.in_(ids),
                    PurchaseReceiptItem.household_item_id == item_id,
                )
                .order_by(PurchaseReceiptItem.id)
                .with_for_update(of=PurchaseReceiptItem)
            )
        )
        if len(rows) != len(ids):
            raise ClassificationCollisionError(
                "merged receipt history changed and cannot be undone safely"
            )
        return rows

    def _lock_errand_links_by_ids(
        self,
        ids: list[int],
        item_id: int,
    ) -> list[ErrandHouseholdItem]:
        if not ids:
            return []
        rows = list(
            self.db.scalars(
                select(ErrandHouseholdItem)
                .join(Errand, Errand.id == ErrandHouseholdItem.errand_id)
                .where(
                    Errand.workspace_id == self.workspace_id,
                    ErrandHouseholdItem.id.in_(ids),
                    ErrandHouseholdItem.household_item_id == item_id,
                )
                .order_by(ErrandHouseholdItem.id)
                .with_for_update(of=ErrandHouseholdItem)
            )
        )
        if len(rows) != len(ids):
            raise ClassificationCollisionError(
                "merged errand links changed and cannot be undone safely"
            )
        return rows

    def _lock_plan_links_by_ids(
        self,
        ids: list[int],
        item_id: int,
    ) -> list[ErrandPlanStopHouseholdItem]:
        if not ids:
            return []
        rows = list(
            self.db.scalars(
                select(ErrandPlanStopHouseholdItem)
                .join(
                    ErrandPlanStop,
                    ErrandPlanStop.id == ErrandPlanStopHouseholdItem.stop_id,
                )
                .join(ErrandPlan, ErrandPlan.id == ErrandPlanStop.plan_id)
                .where(
                    ErrandPlan.workspace_id == self.workspace_id,
                    ErrandPlanStopHouseholdItem.id.in_(ids),
                    ErrandPlanStopHouseholdItem.household_item_id == item_id,
                )
                .order_by(ErrandPlanStopHouseholdItem.id)
                .with_for_update(of=ErrandPlanStopHouseholdItem)
            )
        )
        if len(rows) != len(ids):
            raise ClassificationCollisionError(
                "merged plan links changed and cannot be undone safely"
            )
        return rows

    @staticmethod
    def _positive_id(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ClassificationCollisionError("household item merge audit is invalid")
        return value

    def _id_list(self, metadata: dict, key: str) -> list[int]:
        value = metadata.get(key, [])
        if not isinstance(value, list) or len(value) > MAX_HOUSEHOLD_ITEM_MERGE_ROWS:
            raise ClassificationCollisionError("household item merge audit is invalid")
        ids = [self._positive_id(item) for item in value]
        if len(ids) != len(set(ids)):
            raise ClassificationCollisionError("household item merge audit is invalid")
        return ids

    def _plan_link_snapshots(self, metadata: dict) -> list[dict[str, int | str | None]]:
        value = metadata.get("deduped_plan_links", [])
        if not isinstance(value, list) or len(value) > MAX_HOUSEHOLD_ITEM_MERGE_ROWS:
            raise ClassificationCollisionError("household item merge audit is invalid")
        result: list[dict[str, int | str | None]] = []
        seen: set[int] = set()
        for snapshot in value:
            if not isinstance(snapshot, dict) or set(snapshot) != {"stop_id", "reason"}:
                raise ClassificationCollisionError("household item merge audit is invalid")
            stop_id = self._positive_id(snapshot["stop_id"])
            reason = snapshot["reason"]
            if reason is not None and (not isinstance(reason, str) or len(reason) > 10_000):
                raise ClassificationCollisionError("household item merge audit is invalid")
            if stop_id in seen:
                raise ClassificationCollisionError("household item merge audit is invalid")
            seen.add(stop_id)
            result.append({"stop_id": stop_id, "reason": reason})
        return result

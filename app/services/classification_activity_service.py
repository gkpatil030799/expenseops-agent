from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.classification_activity_schemas import (
    MAX_CLASSIFICATION_ACTIVITY_RANGE_DAYS,
    ClassificationActivityOut,
    ClassificationActivityRangeOut,
    ClassificationActivityRangeView,
    ClassificationActivityView,
)
from app.models import (
    ClassificationConcept,
    ClassificationConceptAlias,
    ClassificationConfidenceBand,
    ClassificationDecisionRecord,
    ClassificationDecisionState,
    ExpenseTransaction,
    HouseholdItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReceiptParseStatus,
    ReplenishmentEligibility,
    SpendingParentCategory,
    TransactionStatus,
    utc_now,
)

MAX_CLASSIFICATION_ACTIVITY_RESULTS = 20
_LEGACY_VALID_VIEWS = frozenset(
    {
        "summary",
        "categories",
        "new_categories",
        "matches",
        "staples",
        "cadence",
        "uncertain",
    }
)
_VALID_VIEWS = _LEGACY_VALID_VIEWS | {"staple_candidates", "aliases"}
_CLASSIFICATION_UNCERTAIN_REASONS = (
    ("low_confidence", "confidence_band", "low"),
    ("provisional", "decision_state", "provisional"),
    ("other_uncertain", "spending_parent_category", "other_uncertain"),
    ("replenishment_uncertain", "replenishment_eligibility", "uncertain"),
)


class ClassificationActivityError(ValueError):
    pass


class ClassificationActivityService:
    """Build a bounded retrospective from the immutable tenant classification ledger."""

    def __init__(self, db: Session):
        self.db = db
        workspace_id = db.info.get("workspace_id")
        if not isinstance(workspace_id, int) or workspace_id < 1:
            raise ClassificationActivityError(
                "classification activity requires an authenticated workspace"
            )
        self.workspace_id = workspace_id

    def read(
        self,
        *,
        activity_date: date,
        view: ClassificationActivityView = "summary",
        limit: int = 10,
    ) -> ClassificationActivityOut:
        if view not in _LEGACY_VALID_VIEWS:
            raise ClassificationActivityError("unsupported classification activity view")
        self._validate_limit(limit)
        start = datetime.combine(activity_date, time.min, UTC)
        end = start + timedelta(days=1)
        result = self._read_window(
            start=start,
            end=end,
            view=view,
            limit=limit,
            include_day17=False,
        )
        return ClassificationActivityOut.model_validate(
            {
                "view": view,
                "activity_date": activity_date,
                "as_of": utc_now(),
                **result,
            }
        )

    def read_range(
        self,
        *,
        start_date: date,
        end_date: date,
        timezone: str,
        view: ClassificationActivityRangeView = "summary",
        limit: int = 10,
    ) -> ClassificationActivityRangeOut:
        if view not in _VALID_VIEWS:
            raise ClassificationActivityError("unsupported classification activity view")
        self._validate_limit(limit)
        start, end = _utc_range(start_date, end_date, timezone)
        result = self._read_window(
            start=start,
            end=end,
            view=view,
            limit=limit,
            include_day17=True,
        )
        return ClassificationActivityRangeOut.model_validate(
            {
                "view": view,
                "start_date": start_date,
                "end_date": end_date,
                "timezone": timezone,
                "as_of": utc_now(),
                **result,
            }
        )

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if not 1 <= limit <= MAX_CLASSIFICATION_ACTIVITY_RESULTS:
            raise ClassificationActivityError("classification activity limit is out of range")

    def _read_window(
        self,
        *,
        start: datetime,
        end: datetime,
        view: str,
        limit: int,
        include_day17: bool,
    ) -> dict[str, Any]:

        decision_event_criteria = (
            ClassificationDecisionRecord.workspace_id == self.workspace_id,
            ClassificationDecisionRecord.created_at >= start,
            ClassificationDecisionRecord.created_at < end,
        )
        decision_criteria = (
            *decision_event_criteria,
            _latest_decision_clause(self.workspace_id),
        )
        transaction_criteria = (
            *decision_criteria,
            ClassificationDecisionRecord.source_type == "transaction",
        )
        receipt_item_criteria = (
            *decision_criteria,
            ClassificationDecisionRecord.source_type == "receipt_line",
        )
        staple_candidate_criteria = (
            *receipt_item_criteria,
            ClassificationDecisionRecord.replenishment_eligibility.in_(
                (
                    ReplenishmentEligibility.REPLENISHABLE.value,
                    ReplenishmentEligibility.POTENTIALLY_REPLENISHABLE.value,
                )
            ),
            exists(
                select(PurchaseReceiptItem.id)
                .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
                .where(
                    PurchaseReceiptItem.id == ClassificationDecisionRecord.source_entity_id,
                    PurchaseReceipt.workspace_id == self.workspace_id,
                    PurchaseReceipt.parse_status.not_in(
                        (ReceiptParseStatus.IGNORED.value, ReceiptParseStatus.FAILED.value)
                    ),
                )
            ),
        )
        alias_criteria = (
            ClassificationConceptAlias.workspace_id == self.workspace_id,
            ClassificationConceptAlias.created_at >= start,
            ClassificationConceptAlias.created_at < end,
        )
        match_criteria = (
            PurchaseReceipt.workspace_id == self.workspace_id,
            PurchaseReceipt.transaction_match_attempted_at >= start,
            PurchaseReceipt.transaction_match_attempted_at < end,
            PurchaseReceipt.transaction_match_status.in_(("auto_matched", "ambiguous", "no_match")),
        )
        new_item_criteria = (
            *decision_event_criteria,
            ClassificationDecisionRecord.created_household_item.is_(True),
        )
        new_category_criteria = (
            *decision_event_criteria,
            ClassificationDecisionRecord.created_subcategory.is_(True),
        )
        cadence_criteria = (
            HouseholdItem.workspace_id == self.workspace_id,
            HouseholdItem.cadence_estimated_at >= start,
            HouseholdItem.cadence_estimated_at < end,
        )
        decision_uncertain_criteria = (
            *decision_criteria,
            _classification_uncertain_clause(ClassificationDecisionRecord),
        )
        match_uncertain_criteria = (
            PurchaseReceipt.workspace_id == self.workspace_id,
            PurchaseReceipt.transaction_match_attempted_at >= start,
            PurchaseReceipt.transaction_match_attempted_at < end,
            PurchaseReceipt.transaction_match_status.in_(("ambiguous", "no_match")),
        )

        transaction_count = self._count(ClassificationDecisionRecord.id, *transaction_criteria)
        receipt_item_count = self._count(ClassificationDecisionRecord.id, *receipt_item_criteria)
        receipt_match_count = self._count(PurchaseReceipt.id, *match_criteria)
        new_category_count = self._count(ClassificationDecisionRecord.id, *new_category_criteria)
        new_item_count = self._count(ClassificationDecisionRecord.id, *new_item_criteria)
        cadence_update_count = self._count(HouseholdItem.id, *cadence_criteria)
        uncertain_count = self._count(
            ClassificationDecisionRecord.id, *decision_uncertain_criteria
        ) + self._count(PurchaseReceipt.id, *match_uncertain_criteria)
        staple_candidate_count = (
            self._count(ClassificationDecisionRecord.id, *staple_candidate_criteria)
            if include_day17
            else 0
        )
        alias_count = (
            self._count(ClassificationConceptAlias.id, *alias_criteria) if include_day17 else 0
        )
        category_rows = self._category_rows(decision_criteria)

        include_summary = view == "summary"
        transactions = (
            self._decision_rows("transaction", transaction_criteria, limit=limit)
            if include_summary
            else []
        )
        receipt_items = (
            self._decision_rows("receipt_line", receipt_item_criteria, limit=limit)
            if include_summary
            else []
        )
        categories = category_rows if include_summary or view == "categories" else []
        new_categories = (
            self._new_category_rows(new_category_criteria, limit=limit)
            if include_summary or view == "new_categories"
            else []
        )
        receipt_matches = (
            self._receipt_match_rows(match_criteria, limit=limit)
            if include_summary or view == "matches"
            else []
        )
        new_household_items = (
            self._new_household_item_rows(new_item_criteria, limit=limit)
            if include_summary or view == "staples"
            else []
        )
        staple_candidates = (
            self._staple_candidate_rows(staple_candidate_criteria, limit=limit)
            if include_day17 and (include_summary or view == "staple_candidates")
            else []
        )
        aliases = (
            self._alias_rows(alias_criteria, limit=limit)
            if include_day17 and (include_summary or view == "aliases")
            else []
        )
        cadence_updates = (
            self._cadence_rows(cadence_criteria, limit=limit)
            if include_summary or view == "cadence"
            else []
        )
        uncertain = (
            self._uncertain_rows(
                decision_criteria=decision_uncertain_criteria,
                match_criteria=match_uncertain_criteria,
                limit=limit,
            )
            if include_summary or view == "uncertain"
            else []
        )

        section_counts = {
            "transactions": transaction_count,
            "receipt_items": receipt_item_count,
            "categories": len(category_rows),
            "new_categories": new_category_count,
            "receipt_matches": receipt_match_count,
            "new_household_items": new_item_count,
            "cadence_updates": cadence_update_count,
            "uncertain": uncertain_count,
        }
        section_rows = {
            "transactions": transactions,
            "receipt_items": receipt_items,
            "categories": categories,
            "new_categories": new_categories,
            "receipt_matches": receipt_matches,
            "new_household_items": new_household_items,
            "cadence_updates": cadence_updates,
            "uncertain": uncertain,
        }
        if include_day17:
            section_counts.update(
                {
                    "staple_candidates": staple_candidate_count,
                    "aliases": alias_count,
                }
            )
            section_rows.update(
                {
                    "staple_candidates": staple_candidates,
                    "aliases": aliases,
                }
            )
        visible_sections = (
            set(section_rows)
            if include_summary
            else {
                "categories": {"categories"},
                "new_categories": {"new_categories"},
                "matches": {"receipt_matches"},
                "staples": {"new_household_items"},
                "staple_candidates": {"staple_candidates"},
                "aliases": {"aliases"},
                "cadence": {"cadence_updates"},
                "uncertain": {"uncertain"},
            }[view]
        )
        truncated_sections = [
            name
            for name, rows in section_rows.items()
            if name in visible_sections and section_counts[name] > len(rows)
        ]
        return {
            "counts": section_counts,
            **section_rows,
            "truncated_sections": truncated_sections,
        }

    def _count(self, identifier: Any, *criteria: Any) -> int:
        return int(self.db.scalar(select(func.count(identifier)).where(*criteria)) or 0)

    def _decision_rows(
        self,
        source_type: str,
        criteria: tuple[Any, ...],
        *,
        limit: int,
    ) -> list[dict]:
        records = list(
            self.db.scalars(
                select(ClassificationDecisionRecord)
                .where(*criteria)
                .order_by(
                    ClassificationDecisionRecord.created_at.desc(),
                    ClassificationDecisionRecord.id.desc(),
                )
                .limit(limit)
            )
        )
        transactions, receipt_lines, receipt_by_line, household_items = self._decision_sources(
            records
        )
        if source_type == "transaction":
            return [
                _transaction_decision_dict(record, transactions.get(record.source_entity_id))
                for record in records
            ]
        return [
            _receipt_item_decision_dict(
                record,
                receipt_lines.get(record.source_entity_id),
                receipt_by_line.get(record.source_entity_id),
                household_items.get(record.household_item_id),
            )
            for record in records
        ]

    def _decision_sources(
        self,
        records: list[ClassificationDecisionRecord],
    ) -> tuple[
        dict[int, ExpenseTransaction],
        dict[int, PurchaseReceiptItem],
        dict[int, PurchaseReceipt],
        dict[int, HouseholdItem],
    ]:
        transaction_ids = {
            record.source_entity_id for record in records if record.source_type == "transaction"
        }
        line_ids = {
            record.source_entity_id for record in records if record.source_type == "receipt_line"
        }
        household_item_ids = {
            record.household_item_id for record in records if record.household_item_id is not None
        }
        transactions = (
            {
                row.id: row
                for row in self.db.scalars(
                    select(ExpenseTransaction).where(
                        ExpenseTransaction.workspace_id == self.workspace_id,
                        ExpenseTransaction.id.in_(transaction_ids),
                        ExpenseTransaction.status != TransactionStatus.REMOVED.value,
                    )
                )
            }
            if transaction_ids
            else {}
        )
        receipt_rows = (
            list(
                self.db.execute(
                    select(PurchaseReceiptItem, PurchaseReceipt)
                    .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
                    .where(
                        PurchaseReceipt.workspace_id == self.workspace_id,
                        PurchaseReceiptItem.id.in_(line_ids),
                    )
                )
            )
            if line_ids
            else []
        )
        receipt_lines = {line.id: line for line, _receipt in receipt_rows}
        receipt_by_line = {line.id: receipt for line, receipt in receipt_rows}
        household_items = (
            {
                row.id: row
                for row in self.db.scalars(
                    select(HouseholdItem).where(
                        HouseholdItem.workspace_id == self.workspace_id,
                        HouseholdItem.id.in_(household_item_ids),
                    )
                )
            }
            if household_item_ids
            else {}
        )
        return transactions, receipt_lines, receipt_by_line, household_items

    def _category_rows(self, criteria: tuple[Any, ...]) -> list[dict]:
        rows = self.db.execute(
            select(
                ClassificationDecisionRecord.spending_parent_category,
                ClassificationDecisionRecord.source_type,
                func.count(ClassificationDecisionRecord.id),
            )
            .where(*criteria)
            .group_by(
                ClassificationDecisionRecord.spending_parent_category,
                ClassificationDecisionRecord.source_type,
            )
        )
        counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {"transaction": 0, "receipt_line": 0}
        )
        for category, source_type, count in rows:
            counts[str(category)][str(source_type)] = int(count)
        result = [
            {
                "parent_category": category,
                "transaction_count": values["transaction"],
                "receipt_item_count": values["receipt_line"],
                "total_count": values["transaction"] + values["receipt_line"],
            }
            for category, values in counts.items()
        ]
        result.sort(key=lambda row: (-row["total_count"], row["parent_category"]))
        return result[:MAX_CLASSIFICATION_ACTIVITY_RESULTS]

    def _receipt_match_rows(self, criteria: tuple[Any, ...], *, limit: int) -> list[dict]:
        rows = list(
            self.db.execute(
                select(PurchaseReceipt, ExpenseTransaction)
                .outerjoin(
                    ExpenseTransaction,
                    and_(
                        ExpenseTransaction.id == PurchaseReceipt.transaction_id,
                        ExpenseTransaction.workspace_id == self.workspace_id,
                        ExpenseTransaction.status != TransactionStatus.REMOVED.value,
                    ),
                )
                .where(*criteria)
                .order_by(
                    PurchaseReceipt.transaction_match_attempted_at.desc(),
                    PurchaseReceipt.id.desc(),
                )
                .limit(limit)
            )
        )
        return [_receipt_match_dict(receipt, transaction) for receipt, transaction in rows]

    def _new_category_rows(
        self,
        criteria: tuple[Any, ...],
        *,
        limit: int,
    ) -> list[dict]:
        records = list(
            self.db.scalars(
                select(ClassificationDecisionRecord)
                .where(*criteria)
                .order_by(
                    ClassificationDecisionRecord.created_at.desc(),
                    ClassificationDecisionRecord.id.desc(),
                )
                .limit(limit)
            )
        )
        result: list[dict] = []
        for record in records:
            subcategory = _optional_label(record.subcategory_name, maximum=128)
            if subcategory is None:
                raise ClassificationActivityError(
                    "created classification category is missing its persisted name"
                )
            result.append(
                {
                    "decision_public_id": str(record.id),
                    "parent_category": record.spending_parent_category,
                    "subcategory": subcategory,
                    "source_type": record.source_type,
                    "authority": record.authority,
                    "created_at": record.created_at,
                }
            )
        return result

    def _new_household_item_rows(
        self,
        criteria: tuple[Any, ...],
        *,
        limit: int,
    ) -> list[dict]:
        records = list(
            self.db.scalars(
                select(ClassificationDecisionRecord)
                .where(*criteria)
                .order_by(
                    ClassificationDecisionRecord.created_at.desc(),
                    ClassificationDecisionRecord.id.desc(),
                )
                .limit(limit)
            )
        )
        item_ids = {record.household_item_id for record in records if record.household_item_id}
        items = (
            {
                row.id: row
                for row in self.db.scalars(
                    select(HouseholdItem).where(
                        HouseholdItem.workspace_id == self.workspace_id,
                        HouseholdItem.id.in_(item_ids),
                    )
                )
            }
            if item_ids
            else {}
        )
        result = []
        for record in records:
            item = items.get(record.household_item_id)
            if item is None:
                continue
            result.append(
                _household_item_dict(
                    item,
                    activity_at=record.created_at,
                    created_by_decision_id=record.id,
                )
            )
        return result

    def _staple_candidate_rows(
        self,
        criteria: tuple[Any, ...],
        *,
        limit: int,
    ) -> list[dict]:
        records = list(
            self.db.scalars(
                select(ClassificationDecisionRecord)
                .where(*criteria)
                .order_by(
                    ClassificationDecisionRecord.created_at.desc(),
                    ClassificationDecisionRecord.id.desc(),
                )
                .limit(limit)
            )
        )
        _transactions, receipt_lines, receipt_by_line, household_items = self._decision_sources(
            records
        )
        return [
            _staple_candidate_dict(
                record,
                receipt_lines.get(record.source_entity_id),
                receipt_by_line.get(record.source_entity_id),
                household_items.get(record.household_item_id),
            )
            for record in records
        ]

    def _alias_rows(self, criteria: tuple[Any, ...], *, limit: int) -> list[dict]:
        rows = list(
            self.db.execute(
                select(ClassificationConceptAlias, ClassificationConcept)
                .join(
                    ClassificationConcept,
                    and_(
                        ClassificationConcept.id == ClassificationConceptAlias.concept_id,
                        ClassificationConcept.workspace_id
                        == ClassificationConceptAlias.workspace_id,
                    ),
                )
                .where(
                    *criteria,
                    ClassificationConcept.workspace_id == self.workspace_id,
                )
                .order_by(
                    ClassificationConceptAlias.created_at.desc(),
                    ClassificationConceptAlias.id.desc(),
                )
                .limit(limit)
            )
        )
        return [_alias_dict(alias, concept) for alias, concept in rows]

    def _cadence_rows(self, criteria: tuple[Any, ...], *, limit: int) -> list[dict]:
        items = list(
            self.db.scalars(
                select(HouseholdItem)
                .where(*criteria)
                .order_by(HouseholdItem.cadence_estimated_at.desc(), HouseholdItem.id.desc())
                .limit(limit)
            )
        )
        return [
            _household_item_dict(
                item,
                activity_at=item.cadence_estimated_at,
                created_by_decision_id=None,
            )
            for item in items
        ]

    def _uncertain_rows(
        self,
        *,
        decision_criteria: tuple[Any, ...],
        match_criteria: tuple[Any, ...],
        limit: int,
    ) -> list[dict]:
        decisions = list(
            self.db.scalars(
                select(ClassificationDecisionRecord)
                .where(*decision_criteria)
                .order_by(
                    ClassificationDecisionRecord.created_at.desc(),
                    ClassificationDecisionRecord.id.desc(),
                )
                .limit(limit)
            )
        )
        transactions, receipt_lines, receipts, _household_items = self._decision_sources(decisions)
        matches = list(
            self.db.scalars(
                select(PurchaseReceipt)
                .where(*match_criteria)
                .order_by(
                    PurchaseReceipt.transaction_match_attempted_at.desc(),
                    PurchaseReceipt.id.desc(),
                )
                .limit(limit)
            )
        )
        result: list[dict] = []
        for record in decisions:
            if record.source_type == "transaction":
                transaction = transactions.get(record.source_entity_id)
                result.append(
                    {
                        "kind": "transaction",
                        "public_id": str(record.source_entity_id),
                        "receipt_public_id": None,
                        "label": _transaction_label(transaction),
                        "reasons": _classification_uncertain_reasons(record),
                        "confidence_band": record.confidence_band,
                        "decision_state": record.decision_state,
                        "observed_at": record.created_at,
                    }
                )
            else:
                line = receipt_lines.get(record.source_entity_id)
                receipt = receipts.get(record.source_entity_id)
                result.append(
                    {
                        "kind": "receipt_item",
                        "public_id": str(record.source_entity_id),
                        "receipt_public_id": (
                            str(receipt.id) if receipt else str(record.source_entity_id)
                        ),
                        "label": line.raw_name if line else "Receipt item",
                        "reasons": _classification_uncertain_reasons(record),
                        "confidence_band": record.confidence_band,
                        "decision_state": record.decision_state,
                        "observed_at": record.created_at,
                    }
                )
        result.extend(
            {
                "kind": "receipt_match",
                "public_id": str(receipt.id),
                "receipt_public_id": None,
                "label": _optional_label(receipt.merchant_raw, maximum=255) or "Receipt",
                "reasons": [
                    "ambiguous_receipt_match"
                    if receipt.transaction_match_status == "ambiguous"
                    else "no_receipt_match"
                ],
                "confidence_band": None,
                "decision_state": None,
                "observed_at": receipt.transaction_match_attempted_at,
            }
            for receipt in matches
        )
        result.sort(
            key=lambda row: (
                _aware(row["observed_at"]),
                row["kind"],
                int(row["public_id"]),
            ),
            reverse=True,
        )
        return result[:limit]


def _classification_uncertain_clause(model: Any) -> Any:
    return or_(
        model.confidence_band == ClassificationConfidenceBand.LOW.value,
        model.decision_state == ClassificationDecisionState.PROVISIONAL.value,
        model.spending_parent_category == SpendingParentCategory.OTHER_UNCERTAIN.value,
        model.replenishment_eligibility == ReplenishmentEligibility.UNCERTAIN.value,
    )


def _latest_decision_clause(workspace_id: int) -> Any:
    newer = aliased(ClassificationDecisionRecord)
    return ~exists(
        select(newer.id).where(
            newer.workspace_id == workspace_id,
            newer.source_type == ClassificationDecisionRecord.source_type,
            newer.source_entity_id == ClassificationDecisionRecord.source_entity_id,
            newer.version > ClassificationDecisionRecord.version,
        )
    )


def _classification_uncertain_reasons(value: Any) -> list[str]:
    reasons = [
        reason
        for reason, field, expected in _CLASSIFICATION_UNCERTAIN_REASONS
        if getattr(value, field, None) == expected
    ]
    return reasons or ["low_confidence"]


def _decision_common(record: ClassificationDecisionRecord) -> dict:
    return {
        "decision_public_id": str(record.id),
        "public_id": str(record.source_entity_id),
        "version": record.version,
        "parent_category": record.spending_parent_category,
        "subcategory": _optional_label(record.subcategory_name, maximum=128),
        "concept": _optional_label(record.concept_name, maximum=255),
        "activity_type": record.item_activity_type,
        "replenishment_eligibility": record.replenishment_eligibility,
        "confidence": float(record.confidence),
        "confidence_band": record.confidence_band,
        "authority": record.authority,
        "decision_state": record.decision_state,
        "provenance_codes": list(record.provenance_json or []),
        "auto_finalize_at": record.auto_finalize_at,
        "finalized_at": record.finalized_at,
        "corrects_decision_public_id": (
            str(record.corrects_decision_id) if record.corrects_decision_id else None
        ),
        "created_subcategory": bool(record.created_subcategory),
        "created_concept": bool(record.created_concept),
        "created_household_item": bool(record.created_household_item),
        "applied_at": record.created_at,
    }


def _transaction_decision_dict(
    record: ClassificationDecisionRecord,
    transaction: ExpenseTransaction | None,
) -> dict:
    return {
        **_decision_common(record),
        "source_available": transaction is not None,
        "merchant": _transaction_label(transaction),
        "occurred_on": transaction.date if transaction else None,
    }


def _receipt_item_decision_dict(
    record: ClassificationDecisionRecord,
    line: PurchaseReceiptItem | None,
    receipt: PurchaseReceipt | None,
    household_item: HouseholdItem | None,
) -> dict:
    source_available = bool(
        line is not None
        and receipt is not None
        and receipt.parse_status
        not in {
            ReceiptParseStatus.IGNORED.value,
            ReceiptParseStatus.FAILED.value,
        }
    )
    return {
        **_decision_common(record),
        "receipt_public_id": str(receipt.id) if receipt else str(record.source_entity_id),
        "source_available": source_available,
        "merchant": _optional_label(receipt.merchant_raw, maximum=255) if receipt else None,
        "name": line.raw_name if line else "Receipt item",
        "household_item_public_id": str(household_item.id) if household_item else None,
        "household_item_name": household_item.name if household_item else None,
    }


def _staple_candidate_dict(
    record: ClassificationDecisionRecord,
    line: PurchaseReceiptItem | None,
    receipt: PurchaseReceipt | None,
    household_item: HouseholdItem | None,
) -> dict:
    decision = _receipt_item_decision_dict(record, line, receipt, household_item)
    learning_state = (
        "candidate"
        if household_item is None
        else "learning"
        if household_item.cadence_source == "learning"
        else "tracked"
    )
    return {
        "decision_public_id": decision["decision_public_id"],
        "receipt_item_public_id": decision["public_id"],
        "receipt_public_id": decision["receipt_public_id"],
        "source_available": decision["source_available"],
        "merchant": decision["merchant"],
        "name": decision["name"],
        "parent_category": decision["parent_category"],
        "subcategory": decision["subcategory"],
        "concept": decision["concept"],
        "activity_type": decision["activity_type"],
        "replenishment_eligibility": decision["replenishment_eligibility"],
        "confidence": decision["confidence"],
        "confidence_band": decision["confidence_band"],
        "decision_state": decision["decision_state"],
        "created_household_item": decision["created_household_item"],
        "household_item_public_id": decision["household_item_public_id"],
        "household_item_name": decision["household_item_name"],
        "learning_state": learning_state,
        "applied_at": decision["applied_at"],
    }


def _alias_dict(
    alias: ClassificationConceptAlias,
    concept: ClassificationConcept,
) -> dict:
    concept_name = _optional_label(concept.name, maximum=255)
    raw_pattern = _optional_label(alias.raw_pattern, maximum=500)
    if concept_name is None or raw_pattern is None:
        raise ClassificationActivityError("classification alias is missing durable label data")
    return {
        "public_id": str(alias.id),
        "concept": concept_name,
        "parent_category": concept.parent_category,
        "raw_pattern": raw_pattern,
        "merchant": _optional_label(alias.merchant_normalized, maximum=255),
        "confidence": float(alias.confidence),
        "authority": alias.source,
        "active": alias.voided_at is None,
        "created_at": alias.created_at,
    }


def _receipt_match_dict(
    receipt: PurchaseReceipt,
    transaction: ExpenseTransaction | None,
) -> dict:
    status = receipt.transaction_match_status
    if status == "auto_matched" and transaction is None:
        reason_code = "linked_transaction_unavailable"
    elif status == "auto_matched":
        reason_code = "matched_by_receipt_evidence"
    elif status == "ambiguous":
        reason_code = "multiple_possible_transactions"
    else:
        reason_code = "no_eligible_transaction"
    return {
        "receipt_public_id": str(receipt.id),
        "merchant": _optional_label(receipt.merchant_raw, maximum=255),
        "status": status,
        "confidence": float(receipt.transaction_match_confidence or 0.0),
        "transaction_public_id": str(transaction.id) if transaction else None,
        "reason_code": reason_code,
        "attempted_at": receipt.transaction_match_attempted_at,
        "matched_at": receipt.transaction_matched_at if status == "auto_matched" else None,
    }


def _household_item_dict(
    item: HouseholdItem,
    *,
    activity_at: datetime | None,
    created_by_decision_id: int | None,
) -> dict:
    if activity_at is None:
        raise ClassificationActivityError("household activity timestamp is missing")
    return {
        "created_by_decision_public_id": (
            str(created_by_decision_id) if created_by_decision_id else None
        ),
        "public_id": str(item.id),
        "name": item.name,
        "parent_category": item.spending_parent_category,
        "replenishment_eligibility": item.replenishment_eligibility,
        "classification_confidence": float(item.classification_confidence or 0.0),
        "cadence_source": item.cadence_source,
        "cadence_days": item.cadence_days,
        "cadence_min_days": item.cadence_min_days,
        "cadence_max_days": item.cadence_max_days,
        "cadence_confidence": float(item.cadence_confidence or 0.0),
        "activity_at": activity_at,
    }


def _transaction_label(transaction: ExpenseTransaction | None) -> str:
    if transaction is None:
        return "Transaction"
    return (
        _optional_label(transaction.merchant_name, maximum=255)
        or _optional_label(transaction.name, maximum=255)
        or "Transaction"
    )


def _optional_label(value: str | None, *, maximum: int) -> str | None:
    clean = " ".join((value or "").split()).strip()
    return clean[:maximum] or None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _utc_range(
    start_date: date,
    end_date: date,
    timezone: str,
) -> tuple[datetime, datetime]:
    if start_date > end_date:
        raise ClassificationActivityError("start_date must not be after end_date")
    if (end_date - start_date).days >= MAX_CLASSIFICATION_ACTIVITY_RANGE_DAYS:
        raise ClassificationActivityError(
            "classification activity range cannot exceed "
            f"{MAX_CLASSIFICATION_ACTIVITY_RANGE_DAYS} days"
        )
    try:
        zone = ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ClassificationActivityError("timezone must be a valid IANA timezone") from exc
    try:
        end_exclusive = end_date + timedelta(days=1)
    except OverflowError as exc:
        raise ClassificationActivityError(
            "classification activity end date is out of range"
        ) from exc
    return (
        datetime.combine(start_date, time.min, zone).astimezone(UTC),
        datetime.combine(end_exclusive, time.min, zone).astimezone(UTC),
    )

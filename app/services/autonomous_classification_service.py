from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, replace
from datetime import UTC, timedelta

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.config import Settings, get_settings
from app.logging_config import log_event
from app.models import (
    ClassificationActivityType,
    ClassificationAuthority,
    ClassificationConcept,
    ClassificationDecisionRecord,
    ClassificationDecisionState,
    ClassificationSubcategory,
    ExpenseTransaction,
    HouseholdCadenceSource,
    HouseholdItem,
    HouseholdItemAcquisition,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReceiptItemMatchStatus,
    ReceiptParseStatus,
    ReplenishmentEligibility,
    SpendingParentCategory,
    TransactionStatus,
    utc_now,
)
from app.services.acquisition_service import AcquisitionService
from app.services.classification_model_service import ClassificationModelSuggestion
from app.services.classification_taxonomy_service import (
    HIGH_CONFIDENCE_THRESHOLD,
    ClassificationApplication,
    ClassificationDecision,
    ClassificationSourceType,
    ClassificationTaxonomyError,
    ClassificationTaxonomyService,
    build_classification_decision,
    classify_known_text,
    normalize_taxonomy_name,
)
from app.services.item_normalization_service import ItemNormalizationService
from app.services.spending_insights_service import (
    SpendClassification,
    classify_spend_transaction,
)

logger = logging.getLogger(__name__)


@dataclass
class ClassificationRunSummary:
    receipt_items_categorized: int = 0
    transactions_categorized: int = 0
    categories_auto_created: int = 0
    concepts_auto_created: int = 0
    household_items_auto_created: int = 0
    aliases_auto_created: int = 0
    acquisitions_auto_recorded: int = 0
    classifications_provisional: int = 0
    classifications_auto_finalized: int = 0
    cadence_estimates_created: int = 0
    classification_rule_hits: int = 0
    classification_model_calls: int = 0
    skipped: int = 0
    failures: int = 0

    def merge(self, other: ClassificationRunSummary) -> ClassificationRunSummary:
        for field_name in self.__dataclass_fields__:
            setattr(self, field_name, getattr(self, field_name) + getattr(other, field_name))
        return self

    def safe_metrics(self) -> dict[str, int]:
        return {
            field_name: int(getattr(self, field_name))
            for field_name in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class _CadencePrior:
    days: int
    minimum: int
    maximum: int
    confidence: float


_CATEGORY_PRIORS: tuple[tuple[tuple[str, ...], _CadencePrior], ...] = (
    (("eggs", "milk", "bread"), _CadencePrior(10, 5, 21, 0.55)),
    (("vegetables", "produce", "meat", "chicken", "beef"), _CadencePrior(7, 3, 14, 0.5)),
    (("rice",), _CadencePrior(30, 14, 60, 0.45)),
    (("paper towels", "toilet paper"), _CadencePrior(30, 14, 60, 0.5)),
    (("dish soap", "dishwasher tablets"), _CadencePrior(35, 14, 75, 0.45)),
    (("laundry detergent",), _CadencePrior(45, 21, 90, 0.5)),
    (("household bags", "food storage bags"), _CadencePrior(45, 21, 90, 0.4)),
    (("pet food",), _CadencePrior(30, 14, 60, 0.45)),
)


class AutonomousClassificationService:
    """Apply reversible internal classification without granting action authority."""

    def __init__(self, db: Session, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.taxonomy = ClassificationTaxonomyService(db, self.settings)

    def classify_receipt(
        self,
        receipt: PurchaseReceipt,
        *,
        model_suggestions: dict[int, ClassificationModelSuggestion] | None = None,
        only_line_ids: frozenset[int] | None = None,
        commit: bool = True,
    ) -> ClassificationRunSummary:
        summary = ClassificationRunSummary()
        workspace_settings = self._active_settings()
        if workspace_settings is None:
            summary.skipped += len(receipt.items)
            return summary
        transaction_preview = self.db.scalar(
            select(PurchaseReceipt.transaction_id).where(
                PurchaseReceipt.workspace_id == self.taxonomy.workspace_id,
                PurchaseReceipt.id == receipt.id,
            )
        )
        if transaction_preview is not None:
            linked_transaction = self.db.scalar(
                select(ExpenseTransaction)
                .where(
                    ExpenseTransaction.workspace_id == self.taxonomy.workspace_id,
                    ExpenseTransaction.id == transaction_preview,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            if linked_transaction is None:
                raise ClassificationTaxonomyError(
                    "linked receipt transaction is outside the active workspace"
                )
        persisted = self.db.scalar(
            select(PurchaseReceipt)
            .where(
                PurchaseReceipt.workspace_id == self.taxonomy.workspace_id,
                PurchaseReceipt.id == receipt.id,
            )
            .options(selectinload(PurchaseReceipt.items))
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if persisted is None:
            raise ClassificationTaxonomyError("receipt is outside the active workspace")
        if persisted.transaction_id != transaction_preview:
            raise ClassificationTaxonomyError(
                "receipt transaction changed during classification; retry safely"
            )
        selected_lines = [
            line
            for line in persisted.items
            if only_line_ids is None or line.id in only_line_ids
        ]
        if persisted.parse_status in {
            ReceiptParseStatus.IGNORED.value,
            ReceiptParseStatus.FAILED.value,
        }:
            summary.skipped += len(selected_lines)
            return summary
        if selected_lines:
            selected_ids = sorted(line.id for line in selected_lines)
            selected_lines = list(
                self.db.scalars(
                    select(PurchaseReceiptItem)
                    .where(
                        PurchaseReceiptItem.receipt_id == persisted.id,
                        PurchaseReceiptItem.id.in_(selected_ids),
                    )
                    .order_by(PurchaseReceiptItem.id)
                    .execution_options(populate_existing=True)
                    .with_for_update(of=PurchaseReceiptItem)
                )
            )
            if [line.id for line in selected_lines] != selected_ids:
                raise ClassificationTaxonomyError(
                    "receipt lines changed during classification; retry safely"
                )
        merchant = persisted.merchant_normalized or persisted.merchant_raw
        for line in selected_lines:
            if (
                line.classification_applied_at is not None
                and line.classification_decision_state
                in {
                    ClassificationDecisionState.FINAL.value,
                    ClassificationDecisionState.CORRECTED.value,
                }
            ):
                if self._receipt_learning_is_complete(persisted, line):
                    # Receipt facts are immutable after parsing. Reprocessing the
                    # same source must not manufacture another decision version.
                    summary.skipped += 1
                    continue
                application = self._current_receipt_application(line)
                if application is None:
                    summary.failures += 1
                    continue
                self._safe_apply_receipt_learning(
                    persisted,
                    line,
                    application,
                    suggestion=None,
                    cadence_enabled=(
                        self.settings.autonomous_cadence_estimation_enabled
                        and workspace_settings.cadence_estimation_enabled
                    ),
                    summary=summary,
                )
                continue
            suggestion = (model_suggestions or {}).get(line.id)
            decision = suggestion.decision if suggestion else self._receipt_decision(
                line,
                merchant=merchant,
                grace_hours=workspace_settings.grace_hours,
            )
            application = self._apply(
                decision,
                raw_alias=line.raw_name,
                merchant=merchant,
                create_household_item=_receipt_line_is_acquisition_safe(persisted, line),
                summary=summary,
            )
            if application is None or not application.applied:
                continue
            summary.receipt_items_categorized += 1
            if decision.authority is ClassificationAuthority.DETERMINISTIC_EXACT:
                summary.classification_rule_hits += 1
            if decision.decision_state is ClassificationDecisionState.PROVISIONAL:
                summary.classifications_provisional += 1
            if suggestion is not None:
                summary.classification_model_calls = 1
            self._safe_apply_receipt_learning(
                persisted,
                line,
                application,
                suggestion=suggestion,
                cadence_enabled=(
                    self.settings.autonomous_cadence_estimation_enabled
                    and workspace_settings.cadence_estimation_enabled
                ),
                summary=summary,
            )
        if selected_lines and persisted.transaction_id is not None:
            transaction = self.db.scalar(
                select(ExpenseTransaction).where(
                    ExpenseTransaction.workspace_id == self.taxonomy.workspace_id,
                    ExpenseTransaction.id == persisted.transaction_id,
                )
            )
            if transaction is not None:
                summary.merge(self.classify_transaction(transaction, commit=False))
        self._finish(summary, "receipt_autonomous_classification", commit=commit)
        return summary

    def classify_transaction(
        self,
        transaction: ExpenseTransaction,
        *,
        model_suggestion: ClassificationModelSuggestion | None = None,
        commit: bool = True,
    ) -> ClassificationRunSummary:
        summary = ClassificationRunSummary()
        workspace_settings = self._active_settings()
        if workspace_settings is None:
            summary.skipped = 1
            return summary
        transaction = self.db.scalar(
            select(ExpenseTransaction).where(
                ExpenseTransaction.workspace_id == self.taxonomy.workspace_id,
                ExpenseTransaction.id == transaction.id,
            )
        )
        if transaction is None:
            raise ClassificationTaxonomyError("transaction is outside the active workspace")
        if transaction.status == TransactionStatus.REMOVED.value:
            summary.skipped = 1
            return summary
        receipt_decision = self._linked_receipt_decision(transaction)
        decision = (
            receipt_decision
            or (model_suggestion.decision if model_suggestion else None)
            or self._transaction_decision(
                transaction,
                grace_hours=workspace_settings.grace_hours,
            )
        )
        application = self._apply(
            decision,
            raw_alias=None,
            merchant=transaction.merchant_name or transaction.name,
            create_household_item=False,
            summary=summary,
        )
        if application is not None and application.applied:
            summary.transactions_categorized = 1
            if decision.authority is ClassificationAuthority.DETERMINISTIC_EXACT:
                summary.classification_rule_hits = 1
            if decision.decision_state is ClassificationDecisionState.PROVISIONAL:
                summary.classifications_provisional = 1
            if model_suggestion is not None and receipt_decision is None:
                summary.classification_model_calls = 1
        self._finish(summary, "transaction_autonomous_classification", commit=commit)
        return summary

    def finalize_decision(
        self,
        target: PurchaseReceiptItem | ExpenseTransaction,
        *,
        model_suggestion: ClassificationModelSuggestion | None = None,
        commit: bool = True,
    ) -> ClassificationRunSummary:
        summary = ClassificationRunSummary()
        workspace_settings = self._active_settings()
        if workspace_settings is None:
            summary.skipped = 1
            return summary
        if getattr(target, "classification_decision_state", None) != "provisional":
            summary.skipped = 1
            return summary
        if getattr(target, "classification_auto_finalize_at", None) is None:
            summary.skipped = 1
            return summary
        if model_suggestion is not None:
            decision = model_suggestion.decision
            summary.classification_model_calls = 1
            if decision.decision_state is ClassificationDecisionState.PROVISIONAL:
                decision = replace(
                    decision,
                    decision_state=ClassificationDecisionState.FINAL,
                    auto_finalize_at=None,
                    provenance_codes=decision.provenance_codes + ("finalizer_model_pass",),
                )
        else:
            decision = self._projection_decision(target)
            decision = replace(
                decision,
                decision_state=ClassificationDecisionState.FINAL,
                auto_finalize_at=None,
                provenance_codes=decision.provenance_codes + ("grace_period_elapsed",),
            )
        is_line = isinstance(target, PurchaseReceiptItem)
        receipt = target.receipt if is_line else None
        application = self._apply(
            decision,
            raw_alias=target.raw_name if is_line else None,
            merchant=(
                receipt.merchant_normalized or receipt.merchant_raw
                if receipt is not None
                else target.merchant_name or target.name
            ),
            create_household_item=(
                is_line
                and receipt is not None
                and _receipt_line_is_acquisition_safe(receipt, target)
            ),
            summary=summary,
        )
        if application is not None and application.applied:
            summary.classifications_auto_finalized = 1
            if is_line:
                summary.receipt_items_categorized = 1
                self._safe_apply_receipt_learning(
                    receipt,
                    target,
                    application,
                    suggestion=model_suggestion,
                    cadence_enabled=(
                        self.settings.autonomous_cadence_estimation_enabled
                        and workspace_settings.cadence_estimation_enabled
                    ),
                    summary=summary,
                )
            else:
                summary.transactions_categorized = 1
        self._finish(summary, "classification_auto_finalized", commit=commit)
        return summary

    def correct_receipt_line(
        self,
        line_id: int,
        *,
        spending_parent_category: SpendingParentCategory,
        item_activity_type: ClassificationActivityType,
        replenishment_eligibility: ReplenishmentEligibility,
        subcategory_name: str | None = None,
        canonical_concept: str | None = None,
        commit: bool = True,
    ) -> ClassificationApplication:
        """Apply an authoritative correction and repair autonomous learning effects."""

        preview = self.db.execute(
            select(PurchaseReceiptItem.id, PurchaseReceipt.transaction_id)
            .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
            .where(
                PurchaseReceipt.workspace_id == self.taxonomy.workspace_id,
                PurchaseReceiptItem.id == line_id,
            )
        ).one_or_none()
        if preview is None:
            raise ClassificationTaxonomyError("classification target not found")
        linked_transaction_id = preview[1]
        if linked_transaction_id is not None:
            linked_transaction = self.db.scalar(
                select(ExpenseTransaction)
                .where(
                    ExpenseTransaction.workspace_id == self.taxonomy.workspace_id,
                    ExpenseTransaction.id == linked_transaction_id,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            if linked_transaction is None:
                raise ClassificationTaxonomyError(
                    "linked receipt transaction is outside the active workspace"
                )
        line = self.db.scalar(
            select(PurchaseReceiptItem)
            .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
            .where(
                PurchaseReceipt.workspace_id == self.taxonomy.workspace_id,
                PurchaseReceiptItem.id == line_id,
            )
            .options(selectinload(PurchaseReceiptItem.receipt))
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if line is None:
            raise ClassificationTaxonomyError("classification target not found")
        receipt = line.receipt
        if receipt.transaction_id != linked_transaction_id:
            raise ClassificationTaxonomyError(
                "receipt transaction changed during correction; retry safely"
            )
        if receipt.parse_status in {
            ReceiptParseStatus.IGNORED.value,
            ReceiptParseStatus.FAILED.value,
        }:
            raise ClassificationTaxonomyError(
                "ignored or failed receipts must be restored before correction"
            )
        old_item_id = line.household_item_id
        old_item = (
            self.db.scalar(
                select(HouseholdItem).where(
                    HouseholdItem.workspace_id == self.taxonomy.workspace_id,
                    HouseholdItem.id == old_item_id,
                )
            )
            if old_item_id is not None
            else None
        )
        active_acquisition = self.db.scalar(
            select(HouseholdItemAcquisition)
            .where(
                HouseholdItemAcquisition.workspace_id == self.taxonomy.workspace_id,
                HouseholdItemAcquisition.receipt_item_id == line.id,
                HouseholdItemAcquisition.voided_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        decision = ClassificationDecision(
            source_type=ClassificationSourceType.RECEIPT_LINE,
            source_entity_id=line.id,
            spending_parent_category=spending_parent_category,
            subcategory_name=subcategory_name,
            item_activity_type=item_activity_type,
            replenishment_eligibility=replenishment_eligibility,
            canonical_concept=canonical_concept,
            confidence=1.0,
            authority=ClassificationAuthority.USER_CORRECTION,
            provenance_codes=("user_correction",),
            decision_state=ClassificationDecisionState.CORRECTED,
        )
        application = self.taxonomy.apply_decision(
            decision,
            raw_alias=line.raw_name,
            merchant=receipt.merchant_normalized or receipt.merchant_raw,
            create_household_item=True,
            allow_category_creation=True,
            commit=False,
        )
        new_item = (
            self.db.scalar(
                select(HouseholdItem).where(
                    HouseholdItem.workspace_id == self.taxonomy.workspace_id,
                    HouseholdItem.id == application.household_item_id,
                )
            )
            if application.household_item_id is not None
            else None
        )
        acquisition_service = AcquisitionService(self.db)
        if new_item is None:
            if active_acquisition is not None:
                acquisition_service.undo(
                    active_acquisition.id,
                    refresh_prediction=True,
                    commit=False,
                )
            line.household_item_id = None
            line.match_status = (
                ReceiptItemMatchStatus.UNMATCHED.value
                if replenishment_eligibility is ReplenishmentEligibility.UNCERTAIN
                else ReceiptItemMatchStatus.IRRELEVANT.value
            )
            line.match_confidence = 1.0
        elif active_acquisition is not None:
            if active_acquisition.household_item_id != new_item.id:
                acquisition_service.correct(
                    active_acquisition.id,
                    household_item=new_item,
                    refresh_prediction=True,
                    commit=False,
                )
            line.household_item_id = new_item.id
            line.match_status = ReceiptItemMatchStatus.MATCHED.value
            line.match_confidence = 1.0
        else:
            self._record_corrected_acquisition(receipt, line, new_item)
        if new_item is not None:
            self._apply_corrected_cadence_prior(new_item)
        if old_item is not None and old_item.id != line.household_item_id:
            ItemNormalizationService(self.db).void_alias(
                old_item,
                line.raw_name,
                merchant=receipt.merchant_normalized or receipt.merchant_raw,
            )
            self._disable_orphaned_auto_item(old_item)
        receipt.updated_at = utc_now()
        if receipt.transaction_id is not None:
            self.recompute_transaction_from_receipt_state(
                receipt.transaction_id,
                commit=False,
            )
        if commit:
            self.db.commit()
            self.db.refresh(line)
        else:
            self.db.flush()
        return application

    def correct_transaction(
        self,
        transaction_id: int,
        *,
        spending_parent_category: SpendingParentCategory,
        item_activity_type: ClassificationActivityType,
        replenishment_eligibility: ReplenishmentEligibility,
        subcategory_name: str | None = None,
        canonical_concept: str | None = None,
        commit: bool = True,
    ) -> ClassificationApplication:
        transaction = self.db.scalar(
            select(ExpenseTransaction)
            .where(
                ExpenseTransaction.workspace_id == self.taxonomy.workspace_id,
                ExpenseTransaction.id == transaction_id,
                ExpenseTransaction.status != TransactionStatus.REMOVED.value,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if transaction is None:
            raise ClassificationTaxonomyError("classification target not found")
        merchant = transaction.merchant_name or transaction.name
        decision = ClassificationDecision(
            source_type=ClassificationSourceType.TRANSACTION,
            source_entity_id=transaction_id,
            spending_parent_category=spending_parent_category,
            subcategory_name=subcategory_name,
            item_activity_type=item_activity_type,
            replenishment_eligibility=replenishment_eligibility,
            canonical_concept=canonical_concept,
            confidence=1.0,
            authority=ClassificationAuthority.USER_CORRECTION,
            provenance_codes=("user_correction",),
            decision_state=ClassificationDecisionState.CORRECTED,
        )
        return self.taxonomy.apply_decision(
            decision,
            raw_alias=merchant,
            merchant=merchant,
            commit=commit,
        )

    def recompute_transaction_from_receipt_state(
        self,
        transaction_id: int,
        *,
        commit: bool = True,
    ) -> ClassificationApplication | None:
        """Refresh receipt-derived transaction truth after evidence changes."""

        transaction = self.db.scalar(
            select(ExpenseTransaction).where(
                ExpenseTransaction.workspace_id == self.taxonomy.workspace_id,
                ExpenseTransaction.id == transaction_id,
            )
        )
        if transaction is None or transaction.status == TransactionStatus.REMOVED.value:
            return None
        decision = self._linked_receipt_decision(transaction)
        if decision is None:
            workspace_settings = self.taxonomy.get_settings()
            if workspace_settings is None:
                return None
            decision = self._transaction_decision(
                transaction,
                grace_hours=workspace_settings.grace_hours,
            )
            decision = replace(
                decision,
                provenance_codes=decision.provenance_codes
                + ("receipt_evidence_recomputed",),
            )
        application = self.taxonomy.apply_decision(
            decision,
            create_household_item=False,
            allow_category_creation=False,
            allow_receipt_evidence_cleanup=True,
            commit=False,
        )
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return application

    def revert_ignored_receipt_learning(
        self,
        receipt_id: int,
        *,
        commit: bool = True,
    ) -> int:
        """Undo only unconfirmed autonomous learning when a receipt is ignored."""

        receipt = self.db.scalar(
            select(PurchaseReceipt)
            .where(
                PurchaseReceipt.workspace_id == self.taxonomy.workspace_id,
                PurchaseReceipt.id == receipt_id,
            )
            .options(selectinload(PurchaseReceipt.items))
        )
        if receipt is None:
            raise ClassificationTaxonomyError("receipt is outside the active workspace")
        reverted = 0
        affected_items: dict[int, HouseholdItem] = {}
        acquisition_service = AcquisitionService(self.db)
        normalizer = ItemNormalizationService(self.db)
        for line in receipt.items:
            created_autonomous_alias = self.db.scalar(
                select(ClassificationDecisionRecord.id).where(
                    ClassificationDecisionRecord.workspace_id == self.taxonomy.workspace_id,
                    ClassificationDecisionRecord.source_type
                    == ClassificationSourceType.RECEIPT_LINE.value,
                    ClassificationDecisionRecord.source_entity_id == line.id,
                    ClassificationDecisionRecord.version == line.classification_version,
                    ClassificationDecisionRecord.created_alias.is_(True),
                    ClassificationDecisionRecord.authority.not_in(
                        (
                            ClassificationAuthority.CONFIRMED_ALIAS.value,
                            ClassificationAuthority.USER_CORRECTION.value,
                        )
                    ),
                )
            )
            if line.classification_concept_id is not None and created_autonomous_alias is not None:
                self.taxonomy.void_alias(
                    line.classification_concept_id,
                    line.raw_name,
                    merchant=receipt.merchant_normalized or receipt.merchant_raw,
                    commit=False,
                )
            acquisition = self.db.scalar(
                select(HouseholdItemAcquisition).where(
                    HouseholdItemAcquisition.workspace_id == self.taxonomy.workspace_id,
                    HouseholdItemAcquisition.receipt_item_id == line.id,
                    HouseholdItemAcquisition.voided_at.is_(None),
                )
            )
            if acquisition is None or acquisition.user_confirmed:
                continue
            if acquisition.source != "autonomous_receipt" and not acquisition.source.startswith(
                "receipt_"
            ):
                continue
            item = self.db.scalar(
                select(HouseholdItem).where(
                    HouseholdItem.workspace_id == self.taxonomy.workspace_id,
                    HouseholdItem.id == acquisition.household_item_id,
                )
            )
            acquisition_service.undo(
                acquisition.id,
                refresh_prediction=True,
                commit=False,
            )
            if item is not None:
                normalizer.void_alias(
                    item,
                    line.raw_name,
                    merchant=receipt.merchant_normalized or receipt.merchant_raw,
                    sources=frozenset({"autonomous_classification"}),
                )
            line.household_item_id = None
            line.match_status = ReceiptItemMatchStatus.REJECTED.value
            line.match_confidence = 1.0
            if item is not None:
                affected_items[item.id] = item
            reverted += 1
        for item in affected_items.values():
            created_for_item = self.db.scalar(
                select(ClassificationDecisionRecord.id).where(
                    ClassificationDecisionRecord.workspace_id == self.taxonomy.workspace_id,
                    ClassificationDecisionRecord.source_type
                    == ClassificationSourceType.RECEIPT_LINE.value,
                    ClassificationDecisionRecord.household_item_id == item.id,
                    ClassificationDecisionRecord.created_household_item.is_(True),
                )
            )
            if created_for_item is not None:
                self._disable_orphaned_auto_item(item)
        log_event(
            logger,
            "ignored_receipt_autonomous_learning_reverted",
            autonomous_acquisitions_reverted=reverted,
        )
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return reverted

    def _active_settings(self):
        if not self.settings.autonomous_classification_enabled:
            return None
        workspace_settings = self.taxonomy.get_settings()
        if workspace_settings is None or not workspace_settings.autonomous_enabled:
            return None
        return workspace_settings

    def _receipt_decision(
        self,
        line: PurchaseReceiptItem,
        *,
        merchant: str | None,
        grace_hours: int,
    ) -> ClassificationDecision:
        alias = self.taxonomy.resolve_alias_evidence(line.raw_name, merchant=merchant)
        if alias is not None:
            return self._concept_decision(
                alias.concept,
                source_type=ClassificationSourceType.RECEIPT_LINE,
                source_entity_id=line.id,
                authority=alias.decision_authority,
                provenance=(
                    "confirmed_concept_alias"
                    if alias.is_confirmed
                    else "stored_concept_alias",
                ),
                alias_confidence=alias.confidence,
            )
        deterministic = classify_known_text(
            source_type=ClassificationSourceType.RECEIPT_LINE,
            source_entity_id=line.id,
            text=" ".join(value for value in (line.raw_name, line.brand) if value),
            provider_category=line.category,
        )
        if deterministic.spending_parent_category is not SpendingParentCategory.OTHER_UNCERTAIN:
            return deterministic
        return self._parser_evidence_decision(line, grace_hours=grace_hours)

    def _transaction_decision(
        self,
        transaction: ExpenseTransaction,
        *,
        grace_hours: int,
    ) -> ClassificationDecision:
        if transaction.amount_cents < 0:
            return ClassificationDecision(
                source_type=ClassificationSourceType.TRANSACTION,
                source_entity_id=transaction.id,
                spending_parent_category=SpendingParentCategory.FEES_TAXES_DISCOUNTS,
                subcategory_name="Refunds and credits",
                item_activity_type=ClassificationActivityType.REFUND,
                replenishment_eligibility=ReplenishmentEligibility.NOT_REPLENISHABLE,
                canonical_concept=None,
                confidence=1.0,
                authority=ClassificationAuthority.DETERMINISTIC_EXACT,
                provenance_codes=("purchase_sign_credit",),
                decision_state=ClassificationDecisionState.FINAL,
            )
        if classify_spend_transaction(transaction) is SpendClassification.EXCLUDED:
            return ClassificationDecision(
                source_type=ClassificationSourceType.TRANSACTION,
                source_entity_id=transaction.id,
                spending_parent_category=SpendingParentCategory.OTHER_UNCERTAIN,
                item_activity_type=ClassificationActivityType.NON_PRODUCT,
                replenishment_eligibility=ReplenishmentEligibility.NOT_REPLENISHABLE,
                confidence=1.0,
                authority=ClassificationAuthority.PROVIDER_EVIDENCE,
                provenance_codes=("provider_transfer_or_payment",),
                decision_state=ClassificationDecisionState.FINAL,
            )
        merchant = transaction.merchant_name or transaction.name
        alias = self.taxonomy.resolve_alias_evidence(merchant, merchant=merchant)
        if alias is not None:
            return self._concept_decision(
                alias.concept,
                source_type=ClassificationSourceType.TRANSACTION,
                source_entity_id=transaction.id,
                authority=alias.decision_authority,
                provenance=(
                    "confirmed_merchant_alias"
                    if alias.is_confirmed
                    else "stored_merchant_alias",
                ),
                alias_confidence=alias.confidence,
            )
        decision = classify_known_text(
            source_type=ClassificationSourceType.TRANSACTION,
            source_entity_id=transaction.id,
            text=merchant,
            provider_category=transaction.provider_category or transaction.category,
        )
        if decision.spending_parent_category is not SpendingParentCategory.OTHER_UNCERTAIN:
            return decision
        return _unresolved_decision(
            source_type=ClassificationSourceType.TRANSACTION,
            source_entity_id=transaction.id,
            provenance_codes=(
                "non_purchase_fallback" if transaction.amount_cents <= 0 else "no_supported_rule",
            ),
            grace_hours=grace_hours,
        )

    def _linked_receipt_decision(
        self, transaction: ExpenseTransaction
    ) -> ClassificationDecision | None:
        receipts = list(
            self.db.scalars(
                select(PurchaseReceipt)
                .where(
                    PurchaseReceipt.workspace_id == self.taxonomy.workspace_id,
                    PurchaseReceipt.transaction_id == transaction.id,
                    PurchaseReceipt.transaction_match_status == "auto_matched",
                    PurchaseReceipt.parse_status.not_in(
                        (
                            ReceiptParseStatus.FAILED.value,
                            ReceiptParseStatus.IGNORED.value,
                        )
                    ),
                    PurchaseReceipt.line_items_complete.is_(True),
                    PurchaseReceipt.arithmetic_status == "verified",
                )
                .options(selectinload(PurchaseReceipt.items))
                .order_by(PurchaseReceipt.id)
            )
        )
        if len(receipts) != 1:
            return None
        weights: dict[str, int] = {}
        lines_by_parent: dict[str, list[PurchaseReceiptItem]] = {}
        for line in receipts[0].items:
            amount = int(line.line_total_cents or 0)
            parent = line.spending_parent_category
            if amount <= 0:
                continue
            if (
                line.classification_applied_at is None
                or line.classification_decision_state
                not in {
                    ClassificationDecisionState.FINAL.value,
                    ClassificationDecisionState.CORRECTED.value,
                }
                or float(line.classification_confidence or 0.0)
                < HIGH_CONFIDENCE_THRESHOLD
                or not parent
            ):
                return None
            if parent == SpendingParentCategory.FEES_TAXES_DISCOUNTS.value:
                continue
            weights[parent] = weights.get(parent, 0) + amount
            lines_by_parent.setdefault(parent, []).append(line)
        total = sum(weights.values())
        if total <= 0:
            return None
        ranked = sorted(weights.items(), key=lambda value: (-value[1], value[0]))
        top_parent, top_amount = ranked[0]
        evidence_confidence = min(
            float(line.classification_confidence or 0.0)
            for lines in lines_by_parent.values()
            for line in lines
        )
        if len(ranked) > 1 and top_amount / total < 0.60:
            return ClassificationDecision(
                source_type=ClassificationSourceType.TRANSACTION,
                source_entity_id=transaction.id,
                spending_parent_category=SpendingParentCategory.OTHER_UNCERTAIN,
                item_activity_type=ClassificationActivityType.UNCERTAIN,
                replenishment_eligibility=ReplenishmentEligibility.UNCERTAIN,
                confidence=evidence_confidence,
                authority=ClassificationAuthority.RECEIPT_EVIDENCE,
                provenance_codes=("linked_receipt_verified_mixed_basket",),
                decision_state=ClassificationDecisionState.FINAL,
            )
        dominant = lines_by_parent[top_parent]
        subcategories = {
            line.classification_subcategory_name
            for line in dominant
            if line.classification_subcategory_name
        }
        activities = {line.item_activity_type for line in dominant}
        replenishment = {line.replenishment_eligibility for line in dominant}
        return ClassificationDecision(
            source_type=ClassificationSourceType.TRANSACTION,
            source_entity_id=transaction.id,
            spending_parent_category=SpendingParentCategory(top_parent),
            subcategory_name=next(iter(subcategories)) if len(subcategories) == 1 else None,
            item_activity_type=(
                ClassificationActivityType(next(iter(activities)))
                if len(activities) == 1
                else ClassificationActivityType.UNCERTAIN
            ),
            replenishment_eligibility=(
                ReplenishmentEligibility(next(iter(replenishment)))
                if len(replenishment) == 1
                else ReplenishmentEligibility.UNCERTAIN
            ),
            confidence=evidence_confidence,
            authority=ClassificationAuthority.RECEIPT_EVIDENCE,
            provenance_codes=("linked_receipt_verified_weighted_spend",),
            decision_state=ClassificationDecisionState.FINAL,
        )

    def _parser_evidence_decision(
        self, line: PurchaseReceiptItem, *, grace_hours: int
    ) -> ClassificationDecision:
        classification = line.classification or "uncertain"
        confidence = float(line.classification_confidence or 0.0)
        category = _safe_parser_subcategory(
            line.category,
            merchant=line.receipt.merchant_normalized or line.receipt.merchant_raw,
            brand=line.brand,
        )
        concept = _safe_parser_concept(line.canonical_name, line.brand)
        mapping = {
            "replenishable_household": (
                SpendingParentCategory.HOUSEHOLD_HOME,
                category or "Household supplies",
                ClassificationActivityType.HOUSEHOLD_CONSUMABLE,
                ReplenishmentEligibility.REPLENISHABLE,
                concept,
            ),
            "perishable_grocery": (
                SpendingParentCategory.FOOD_DINING,
                category or "Groceries",
                ClassificationActivityType.GROCERY,
                ReplenishmentEligibility.REPLENISHABLE,
                concept,
            ),
            "routine_consumption": (
                SpendingParentCategory.FOOD_DINING,
                category or "Routine food and beverage",
                ClassificationActivityType.ROUTINE_CONSUMPTION,
                ReplenishmentEligibility.NOT_REPLENISHABLE,
                None,
            ),
            "dining_or_experience": (
                SpendingParentCategory.FOOD_DINING,
                category or "Restaurants",
                ClassificationActivityType.RESTAURANT_MEAL,
                ReplenishmentEligibility.NOT_REPLENISHABLE,
                None,
            ),
            "one_time_purchase": (
                SpendingParentCategory.LIFESTYLE_SHOPPING,
                category or "General merchandise",
                ClassificationActivityType.ONE_TIME_PURCHASE,
                ReplenishmentEligibility.NOT_REPLENISHABLE,
                None,
            ),
            "non_product_line": (
                SpendingParentCategory.OTHER_UNCERTAIN,
                None,
                ClassificationActivityType.NON_PRODUCT,
                ReplenishmentEligibility.NOT_REPLENISHABLE,
                None,
            ),
        }
        values = mapping.get(classification)
        if values is None:
            return _unresolved_decision(
                source_type=ClassificationSourceType.RECEIPT_LINE,
                source_entity_id=line.id,
                provenance_codes=("receipt_semantics_uncertain",),
                grace_hours=grace_hours,
            )
        parent, subcategory, activity, replenishment, canonical = values
        try:
            return build_classification_decision(
                source_type=ClassificationSourceType.RECEIPT_LINE,
                source_entity_id=line.id,
                spending_parent_category=parent,
                subcategory_name=subcategory,
                item_activity_type=activity,
                replenishment_eligibility=replenishment,
                canonical_concept=canonical,
                confidence=confidence,
                authority=ClassificationAuthority.MODEL_EVIDENCE,
                provenance_codes=("receipt_parser_structured",),
                grace_hours=grace_hours,
            )
        except ClassificationTaxonomyError:
            return _unresolved_decision(
                source_type=ClassificationSourceType.RECEIPT_LINE,
                source_entity_id=line.id,
                provenance_codes=("invalid_receipt_semantics",),
                grace_hours=grace_hours,
            )

    def _concept_decision(
        self,
        concept: ClassificationConcept,
        *,
        source_type: ClassificationSourceType,
        source_entity_id: int,
        authority: ClassificationAuthority,
        provenance: tuple[str, ...],
        alias_confidence: float | None = None,
    ) -> ClassificationDecision:
        subcategory = (
            self.db.scalar(
                select(ClassificationSubcategory).where(
                    ClassificationSubcategory.workspace_id == self.taxonomy.workspace_id,
                    ClassificationSubcategory.id == concept.subcategory_id,
                )
            )
            if concept.subcategory_id is not None
            else None
        )
        return ClassificationDecision(
            source_type=source_type,
            source_entity_id=source_entity_id,
            spending_parent_category=SpendingParentCategory(concept.parent_category),
            subcategory_name=subcategory.name if subcategory else None,
            item_activity_type=ClassificationActivityType(concept.item_activity_type),
            replenishment_eligibility=ReplenishmentEligibility(
                concept.replenishment_eligibility
            ),
            canonical_concept=concept.name,
            confidence=max(
                0.85,
                min(
                    float(concept.confidence),
                    float(alias_confidence)
                    if alias_confidence is not None
                    else float(concept.confidence),
                ),
            ),
            authority=authority,
            provenance_codes=provenance,
            decision_state=ClassificationDecisionState.FINAL,
        )

    def _projection_decision(
        self, target: PurchaseReceiptItem | ExpenseTransaction
    ) -> ClassificationDecision:
        is_line = isinstance(target, PurchaseReceiptItem)
        return ClassificationDecision(
            source_type=(
                ClassificationSourceType.RECEIPT_LINE
                if is_line
                else ClassificationSourceType.TRANSACTION
            ),
            source_entity_id=target.id,
            spending_parent_category=SpendingParentCategory(target.spending_parent_category),
            subcategory_name=target.classification_subcategory_name,
            item_activity_type=ClassificationActivityType(
                target.item_activity_type if is_line else target.classification_activity_type
            ),
            replenishment_eligibility=ReplenishmentEligibility(
                target.replenishment_eligibility
            ),
            canonical_concept=target.classification_concept_name,
            confidence=float(target.classification_confidence or 0.0),
            authority=ClassificationAuthority(target.classification_authority),
            provenance_codes=tuple(target.classification_provenance_json),
            decision_state=ClassificationDecisionState(target.classification_decision_state),
            auto_finalize_at=target.classification_auto_finalize_at,
        )

    def _apply(
        self,
        decision: ClassificationDecision,
        *,
        raw_alias: str | None,
        merchant: str | None,
        create_household_item: bool,
        summary: ClassificationRunSummary,
    ) -> ClassificationApplication | None:
        try:
            with self.db.begin_nested():
                application = self.taxonomy.apply_decision(
                    decision,
                    raw_alias=raw_alias,
                    merchant=merchant,
                    create_household_item=create_household_item,
                    allow_category_creation=self.settings.autonomous_category_creation_enabled,
                    commit=False,
                )
            if application.applied:
                summary.categories_auto_created += int(application.created_subcategory)
                summary.concepts_auto_created += int(application.created_concept)
                summary.household_items_auto_created += int(application.created_household_item)
                summary.aliases_auto_created += int(application.created_alias)
            else:
                summary.skipped += 1
            return application
        except (ClassificationTaxonomyError, SQLAlchemyError, ValueError) as exc:
            summary.failures += 1
            log_event(
                logger,
                "autonomous_classification_apply_failed",
                source_type=decision.source_type.value,
                error_type=type(exc).__name__,
            )
            return None

    def _apply_receipt_learning(
        self,
        receipt: PurchaseReceipt,
        line: PurchaseReceiptItem,
        application: ClassificationApplication,
        *,
        suggestion: ClassificationModelSuggestion | None,
        cadence_enabled: bool,
        summary: ClassificationRunSummary,
    ) -> None:
        if application.household_item_id is None:
            return
        if (
            application.decision.decision_state is not ClassificationDecisionState.FINAL
            or application.decision.confidence_band.value != "high"
            or application.decision.replenishment_eligibility
            is not ReplenishmentEligibility.REPLENISHABLE
        ):
            return
        if not _receipt_line_is_acquisition_safe(receipt, line):
            return
        item = self.db.scalar(
            select(HouseholdItem).where(
                HouseholdItem.workspace_id == self.taxonomy.workspace_id,
                HouseholdItem.id == application.household_item_id,
            )
        )
        if item is None:
            return
        normalizer = ItemNormalizationService(self.db)
        normalizer.learn_alias(
            item,
            line.raw_name,
            merchant=receipt.merchant_normalized or receipt.merchant_raw,
            source="autonomous_classification",
            confidence=application.decision.confidence,
        )
        before_acquisition_id = self.db.scalar(
            select(HouseholdItemAcquisition.id).where(
                HouseholdItemAcquisition.workspace_id == self.taxonomy.workspace_id,
                HouseholdItemAcquisition.receipt_item_id == line.id,
                HouseholdItemAcquisition.voided_at.is_(None),
            )
        )
        acquisition = AcquisitionService(self.db).record(
            item,
            acquired_at=receipt.purchased_at or receipt.created_at,
            quantity=line.quantity,
            unit=line.unit,
            normalized_quantity=line.normalized_quantity,
            normalized_unit=line.normalized_unit,
            package_size=line.package_size,
            quantity_confidence=line.quantity_confidence,
            merchant=receipt.merchant_normalized,
            logical_purchase_key=_logical_purchase_key(
                receipt.workspace_id,
                line.id,
            ),
            receipt_item_id=line.id,
            transaction_id=receipt.transaction_id,
            source="autonomous_receipt",
            confidence=application.decision.confidence,
            confirmed=True,
            user_confirmed=False,
            estimate_cadence=cadence_enabled,
            refresh_prediction=cadence_enabled,
            commit=False,
        )
        line.household_item_id = item.id
        line.match_status = ReceiptItemMatchStatus.MATCHED.value
        line.match_confidence = application.decision.confidence
        if before_acquisition_id is None and acquisition.id is not None:
            summary.acquisitions_auto_recorded += 1
        if not cadence_enabled or item.cadence_source not in {
            "learning",
            "category_prior",
            "model_prior",
        }:
            return
        prior = _category_prior(item)
        prior_source = HouseholdCadenceSource.CATEGORY_PRIOR
        if prior is None and suggestion is not None and suggestion.cadence_min_days is not None:
            minimum = suggestion.cadence_min_days
            maximum = suggestion.cadence_max_days or minimum
            prior = _CadencePrior(
                days=max(1, round((minimum + maximum) / 2)),
                minimum=minimum,
                maximum=maximum,
                confidence=max(0.25, min(0.6, suggestion.decision.confidence * 0.65)),
            )
            prior_source = HouseholdCadenceSource.MODEL_PRIOR
        if prior is None:
            return
        changed = AcquisitionService(self.db).apply_cadence_prior(
            item,
            cadence_days=prior.days,
            source=prior_source,
            confidence=prior.confidence,
            min_days=prior.minimum,
            max_days=prior.maximum,
            provenance={"basis": "product_type", "provisional": True},
            commit=False,
            refresh_prediction=True,
        )
        if changed:
            summary.cadence_estimates_created += 1

    def _record_corrected_acquisition(
        self,
        receipt: PurchaseReceipt,
        line: PurchaseReceiptItem,
        item: HouseholdItem,
    ) -> None:
        workspace_settings = self.taxonomy.get_settings()
        cadence_enabled = bool(
            self.settings.autonomous_cadence_estimation_enabled
            and workspace_settings is not None
            and workspace_settings.cadence_estimation_enabled
        )
        ItemNormalizationService(self.db).learn_alias(
            item,
            line.raw_name,
            merchant=receipt.merchant_normalized or receipt.merchant_raw,
            source="user_correction",
            confidence=1.0,
        )
        if not _receipt_line_is_acquisition_safe(receipt, line):
            line.household_item_id = item.id
            line.match_status = ReceiptItemMatchStatus.MATCHED.value
            line.match_confidence = 1.0
            return
        AcquisitionService(self.db).record(
            item,
            acquired_at=receipt.purchased_at or receipt.created_at,
            quantity=line.quantity,
            unit=line.unit,
            normalized_quantity=line.normalized_quantity,
            normalized_unit=line.normalized_unit,
            package_size=line.package_size,
            quantity_confidence=1.0,
            merchant=receipt.merchant_normalized,
            logical_purchase_key=_logical_purchase_key(
                receipt.workspace_id,
                line.id,
            ),
            receipt_item_id=line.id,
            transaction_id=receipt.transaction_id,
            source="classification_correction",
            confidence=1.0,
            confirmed=True,
            user_confirmed=True,
            estimate_cadence=cadence_enabled,
            refresh_prediction=cadence_enabled,
            commit=False,
        )
        line.household_item_id = item.id
        line.match_status = ReceiptItemMatchStatus.MATCHED.value
        line.match_confidence = 1.0

    def _disable_orphaned_auto_item(
        self,
        item: HouseholdItem,
    ) -> None:
        created_for_item = self.db.scalar(
            select(ClassificationDecisionRecord.id).where(
                ClassificationDecisionRecord.workspace_id == self.taxonomy.workspace_id,
                ClassificationDecisionRecord.source_type
                == ClassificationSourceType.RECEIPT_LINE.value,
                ClassificationDecisionRecord.household_item_id == item.id,
                ClassificationDecisionRecord.created_household_item.is_(True),
            )
        )
        if created_for_item is None:
            return
        active_acquisition = self.db.scalar(
            select(HouseholdItemAcquisition.id).where(
                HouseholdItemAcquisition.workspace_id == self.taxonomy.workspace_id,
                HouseholdItemAcquisition.household_item_id == item.id,
                HouseholdItemAcquisition.voided_at.is_(None),
            )
        )
        other_line = self.db.scalar(
            select(PurchaseReceiptItem.id)
            .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
            .where(
                PurchaseReceipt.workspace_id == self.taxonomy.workspace_id,
                PurchaseReceiptItem.household_item_id == item.id,
            )
        )
        if active_acquisition is None and other_line is None:
            item.enabled = False
            item.updated_at = utc_now()

    def _current_receipt_application(
        self,
        line: PurchaseReceiptItem,
    ) -> ClassificationApplication | None:
        record = self.db.scalar(
            select(ClassificationDecisionRecord).where(
                ClassificationDecisionRecord.workspace_id == self.taxonomy.workspace_id,
                ClassificationDecisionRecord.source_type
                == ClassificationSourceType.RECEIPT_LINE.value,
                ClassificationDecisionRecord.source_entity_id == line.id,
                ClassificationDecisionRecord.version == line.classification_version,
            )
        )
        if record is None:
            return None
        return ClassificationApplication(
            False,
            "repair_receipt_learning",
            self._projection_decision(line),
            subcategory_id=record.subcategory_id,
            concept_id=record.concept_id,
            household_item_id=record.household_item_id,
            decision_record_id=record.id,
            version=record.version,
        )

    def _receipt_learning_is_complete(
        self,
        receipt: PurchaseReceipt,
        line: PurchaseReceiptItem,
    ) -> bool:
        decision = self._projection_decision(line)
        if (
            decision.decision_state
            not in {
                ClassificationDecisionState.FINAL,
                ClassificationDecisionState.CORRECTED,
            }
            or decision.confidence < HIGH_CONFIDENCE_THRESHOLD
            or decision.replenishment_eligibility
            is not ReplenishmentEligibility.REPLENISHABLE
            or not _receipt_line_is_acquisition_safe(receipt, line)
        ):
            return True
        application = self._current_receipt_application(line)
        if application is None or application.household_item_id is None:
            return False
        acquisition_id = self.db.scalar(
            select(HouseholdItemAcquisition.id).where(
                HouseholdItemAcquisition.workspace_id == self.taxonomy.workspace_id,
                HouseholdItemAcquisition.receipt_item_id == line.id,
                HouseholdItemAcquisition.household_item_id == application.household_item_id,
                HouseholdItemAcquisition.voided_at.is_(None),
            )
        )
        return bool(
            acquisition_id is not None
            and line.household_item_id == application.household_item_id
            and line.match_status == ReceiptItemMatchStatus.MATCHED.value
        )

    def _restore_auto_item_for_line(
        self,
        line: PurchaseReceiptItem,
        application: ClassificationApplication,
    ) -> None:
        if application.household_item_id is None:
            return
        created_by_line = self.db.scalar(
            select(ClassificationDecisionRecord.id).where(
                ClassificationDecisionRecord.workspace_id == self.taxonomy.workspace_id,
                ClassificationDecisionRecord.source_type
                == ClassificationSourceType.RECEIPT_LINE.value,
                ClassificationDecisionRecord.source_entity_id == line.id,
                ClassificationDecisionRecord.household_item_id
                == application.household_item_id,
                ClassificationDecisionRecord.created_household_item.is_(True),
            )
        )
        if created_by_line is None:
            return
        item = self.db.scalar(
            select(HouseholdItem).where(
                HouseholdItem.workspace_id == self.taxonomy.workspace_id,
                HouseholdItem.id == application.household_item_id,
            )
        )
        if item is not None and not item.enabled:
            item.enabled = True
            item.updated_at = utc_now()

    def _restore_taxonomy_alias_for_line(
        self,
        line: PurchaseReceiptItem,
        application: ClassificationApplication,
        *,
        merchant: str | None,
    ) -> None:
        if application.concept_id is None or application.decision.confidence_band.value != "high":
            return
        concept = self.db.scalar(
            select(ClassificationConcept).where(
                ClassificationConcept.workspace_id == self.taxonomy.workspace_id,
                ClassificationConcept.id == application.concept_id,
            )
        )
        if concept is not None:
            self.taxonomy.learn_alias(
                concept,
                line.raw_name,
                merchant=merchant,
                confidence=application.decision.confidence,
                source=application.decision.authority,
            )

    def _apply_corrected_cadence_prior(self, item: HouseholdItem) -> None:
        workspace_settings = self.taxonomy.get_settings()
        if (
            not self.settings.autonomous_cadence_estimation_enabled
            or workspace_settings is None
            or not workspace_settings.cadence_estimation_enabled
        ):
            return
        prior = _category_prior(item)
        if prior is None:
            return
        AcquisitionService(self.db).apply_cadence_prior(
            item,
            cadence_days=prior.days,
            source=HouseholdCadenceSource.CATEGORY_PRIOR,
            confidence=prior.confidence,
            min_days=prior.minimum,
            max_days=prior.maximum,
            provenance={"basis": "user_corrected_product_type", "provisional": True},
            commit=False,
            refresh_prediction=True,
        )

    def _safe_apply_receipt_learning(
        self,
        receipt: PurchaseReceipt,
        line: PurchaseReceiptItem,
        application: ClassificationApplication,
        *,
        suggestion: ClassificationModelSuggestion | None,
        cadence_enabled: bool,
        summary: ClassificationRunSummary,
    ) -> None:
        try:
            with self.db.begin_nested():
                self._restore_auto_item_for_line(line, application)
                self._restore_taxonomy_alias_for_line(
                    line,
                    application,
                    merchant=receipt.merchant_normalized or receipt.merchant_raw,
                )
                self._apply_receipt_learning(
                    receipt,
                    line,
                    application,
                    suggestion=suggestion,
                    cadence_enabled=cadence_enabled,
                    summary=summary,
                )
        except (SQLAlchemyError, ValueError) as exc:
            summary.failures += 1
            log_event(
                logger,
                "autonomous_receipt_learning_failed",
                error_type=type(exc).__name__,
            )

    def _finish(
        self,
        summary: ClassificationRunSummary,
        event: str,
        *,
        commit: bool,
    ) -> None:
        log_event(logger, event, **summary.safe_metrics())
        if commit:
            self.db.commit()
        else:
            self.db.flush()


def _safe_parser_subcategory(
    value: str | None,
    *,
    merchant: str | None = None,
    brand: str | None = None,
) -> str | None:
    if not value:
        return None
    clean = " ".join(value.split())
    if not clean or len(clean) > 128:
        return None
    lowered = clean.casefold()
    if any(
        token in lowered
        for token in (
            "system",
            "developer",
            "assistant",
            "workspace",
            "splitwise",
            "secret",
            "api key",
            "http://",
            "https://",
        )
    ):
        return None
    proposed_tokens = set(normalize_taxonomy_name(clean).split())
    for entity in (merchant, brand):
        entity_tokens = {
            token
            for token in normalize_taxonomy_name(entity or "").split()
            if token not in {"co", "company", "corp", "inc", "llc", "store"}
            and not token.isdigit()
        }
        if entity_tokens and entity_tokens.issubset(proposed_tokens):
            return None
    return clean


def _unresolved_decision(
    *,
    source_type: ClassificationSourceType,
    source_entity_id: int,
    provenance_codes: tuple[str, ...],
    grace_hours: int,
) -> ClassificationDecision:
    """Stage unknown evidence for the bounded finalizer without inventing facts."""

    return ClassificationDecision(
        source_type=source_type,
        source_entity_id=source_entity_id,
        spending_parent_category=SpendingParentCategory.OTHER_UNCERTAIN,
        item_activity_type=ClassificationActivityType.UNCERTAIN,
        replenishment_eligibility=ReplenishmentEligibility.UNCERTAIN,
        confidence=0.0,
        authority=ClassificationAuthority.FALLBACK,
        provenance_codes=provenance_codes,
        decision_state=ClassificationDecisionState.PROVISIONAL,
        auto_finalize_at=utc_now()
        + timedelta(hours=max(1, min(int(grace_hours), 168))),
    )


def _safe_parser_concept(value: str | None, brand: str | None) -> str | None:
    if not value:
        return None
    clean = " ".join(value.split())
    if brand:
        brand_key = normalize_taxonomy_name(brand)
        concept_key = normalize_taxonomy_name(clean)
        if brand_key and concept_key.startswith(f"{brand_key} "):
            clean = " ".join(clean.split()[len(brand.split()) :])
    if not clean or len(clean) > 255:
        return None
    return clean


def _receipt_line_is_acquisition_safe(
    receipt: PurchaseReceipt, line: PurchaseReceiptItem
) -> bool:
    purchased_at = (
        receipt.purchased_at
        if receipt.purchased_at is None or receipt.purchased_at.tzinfo
        else receipt.purchased_at.replace(tzinfo=UTC)
    )
    ingested_at = (
        receipt.created_at
        if receipt.created_at.tzinfo
        else receipt.created_at.replace(tzinfo=UTC)
    )
    return bool(
        purchased_at is not None
        and ingested_at - timedelta(days=730) <= purchased_at <= ingested_at + timedelta(days=1)
        and receipt.arithmetic_status == "verified"
        and receipt.line_items_complete
        and receipt.failure_code is None
        and (
            receipt.parse_confidence is None
            or float(receipt.parse_confidence) >= HIGH_CONFIDENCE_THRESHOLD
        )
        and float(line.match_confidence or line.classification_confidence or 0.0)
        >= HIGH_CONFIDENCE_THRESHOLD
        and line.line_total_cents is not None
        and line.line_total_cents > 0
    )


def _logical_purchase_key(
    workspace_id: int,
    receipt_item_id: int,
) -> str:
    identity = f"receipt-item|{workspace_id}|{receipt_item_id}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _category_prior(item: HouseholdItem) -> _CadencePrior | None:
    key = normalize_taxonomy_name(item.name)
    for names, prior in _CATEGORY_PRIORS:
        if key in names:
            return prior
    if item.spending_parent_category == SpendingParentCategory.FOOD_DINING.value:
        return _CadencePrior(14, 7, 30, 0.35)
    if item.spending_parent_category == SpendingParentCategory.HOUSEHOLD_HOME.value:
        return _CadencePrior(45, 21, 90, 0.3)
    return None

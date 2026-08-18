from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings, get_settings
from app.logging_config import log_event
from app.models import (
    ClassificationSettings,
    DataConsent,
    ExpenseTransaction,
    PlaidItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReceiptParseStatus,
    SpendingParentCategory,
    TransactionStatus,
    User,
    WorkspaceMembership,
    utc_now,
)
from app.services.autonomous_classification_service import (
    AutonomousClassificationService,
    ClassificationRunSummary,
)
from app.services.classification_model_service import (
    ClassificationBatchCandidate,
    ClassificationModelError,
    ClassificationModelService,
    ClassificationModelSuggestion,
)
from app.services.classification_taxonomy_service import (
    ClassificationSourceType,
    classify_known_text,
)
from app.services.job_lease_service import acquire_job_lease, release_job_lease
from app.services.receipt_transaction_reconciliation_service import (
    ReceiptTransactionMatchStatus,
    ReceiptTransactionReconciliationService,
)

logger = logging.getLogger(__name__)

BACKFILL_JOB_NAME = "classification_backfill"


@dataclass(frozen=True)
class ClassificationBackfillResult:
    workspace_id: int
    enabled: bool
    lease_acquired: bool
    dry_run: bool
    receipt_match_scanned: int = 0
    receipts_reconciled: int = 0
    receipt_auto_matches: int = 0
    receipt_match_ambiguous: int = 0
    receipt_match_no_match: int = 0
    receipt_items_scanned: int = 0
    receipt_items_classified: int = 0
    transactions_scanned: int = 0
    transactions_classified: int = 0
    receipt_match_cursor: int = 0
    receipt_item_cursor: int = 0
    transaction_cursor: int = 0
    has_more: bool = False
    model_candidates: int = 0
    model_calls: int = 0
    model_failure_code: str | None = None
    failures: int = 0
    model_latency_ms: int | None = None
    model_input_tokens: int | None = None
    model_output_tokens: int | None = None
    model_estimated_cost_micros: int | None = None

    def safe_metrics(self) -> dict[str, int | bool | str | None]:
        return {
            "workspace_id": self.workspace_id,
            "enabled": self.enabled,
            "lease_acquired": self.lease_acquired,
            "dry_run": self.dry_run,
            "receipt_match_scanned": self.receipt_match_scanned,
            "receipts_reconciled": self.receipts_reconciled,
            "receipt_auto_matches": self.receipt_auto_matches,
            "receipt_match_ambiguous": self.receipt_match_ambiguous,
            "receipt_match_no_match": self.receipt_match_no_match,
            "receipt_items_scanned": self.receipt_items_scanned,
            "receipt_items_classified": self.receipt_items_classified,
            "transactions_scanned": self.transactions_scanned,
            "transactions_classified": self.transactions_classified,
            "receipt_match_cursor": self.receipt_match_cursor,
            "receipt_item_cursor": self.receipt_item_cursor,
            "transaction_cursor": self.transaction_cursor,
            "has_more": self.has_more,
            "model_candidates": self.model_candidates,
            "model_calls": self.model_calls,
            "model_failure_code": self.model_failure_code,
            "failures": self.failures,
            "model_latency_ms": self.model_latency_ms,
            "model_input_tokens": self.model_input_tokens,
            "model_output_tokens": self.model_output_tokens,
            "model_estimated_cost_micros": self.model_estimated_cost_micros,
        }


class ClassificationBackfillService:
    """Run one resumable, all-or-nothing page for one workspace.

    Each stream has a durable primary-key cursor. A cursor advances only in the
    same commit as all work for that page, so interruption replays the page
    safely through idempotent reconciliation/classification contracts.
    """

    def __init__(
        self,
        db: Session,
        settings: Settings | None = None,
        model_service: ClassificationModelService | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        workspace_id = db.info.get("workspace_id")
        if workspace_id is None:
            raise ValueError("Classification backfill requires a workspace scope.")
        self.workspace_id = int(workspace_id)
        self.model_service = model_service or ClassificationModelService(self.settings)

    def run_page(
        self,
        *,
        batch_size: int | None = None,
        dry_run: bool = False,
        use_model: bool = False,
        commit: bool = True,
        now: datetime | None = None,
    ) -> ClassificationBackfillResult:
        limit = batch_size or self.settings.classification_backfill_batch_size
        if not 1 <= limit <= self.settings.classification_backfill_batch_size:
            raise ValueError("Backfill batch size exceeds the configured bound.")
        if not self.settings.autonomous_classification_enabled:
            return ClassificationBackfillResult(
                self.workspace_id,
                False,
                True,
                dry_run,
            )

        settings_statement = (
            select(ClassificationSettings)
            .where(ClassificationSettings.workspace_id == self.workspace_id)
            .execution_options(populate_existing=True)
        )
        current = self.db.scalar(settings_statement)
        if current is None or not current.autonomous_enabled:
            return ClassificationBackfillResult(
                self.workspace_id,
                False,
                True,
                dry_run,
                receipt_match_cursor=(current.receipt_match_backfill_cursor if current else 0),
                receipt_item_cursor=(current.receipt_item_backfill_cursor if current else 0),
                transaction_cursor=(current.transaction_backfill_cursor if current else 0),
            )

        match_cursor = int(current.receipt_match_backfill_cursor)
        line_cursor = int(current.receipt_item_backfill_cursor)
        transaction_cursor = int(current.transaction_backfill_cursor)

        # Snapshot the bounded page without locks. Optional model I/O must never
        # hold online transaction/receipt/correction rows beyond PostgreSQL's
        # lock timeout. The same IDs are locked and re-read after the call.
        transactions = self._transaction_page(
            cursor=transaction_cursor,
            limit=limit,
            lock=False,
        )
        receipts = self._receipt_page(cursor=match_cursor, limit=limit, lock=False)
        receipt_lines = self._receipt_item_page(
            cursor=line_cursor,
            limit=limit,
            lock=False,
        )
        next_match_cursor = receipts[-1].id if receipts else match_cursor
        next_line_cursor = receipt_lines[-1].id if receipt_lines else line_cursor
        next_transaction_cursor = transactions[-1].id if transactions else transaction_cursor
        has_more = any(len(values) == limit for values in (receipts, receipt_lines, transactions))

        if dry_run:
            result = ClassificationBackfillResult(
                workspace_id=self.workspace_id,
                enabled=True,
                lease_acquired=True,
                dry_run=True,
                receipt_match_scanned=len(receipts),
                receipt_items_scanned=len(receipt_lines),
                transactions_scanned=len(transactions),
                receipt_match_cursor=next_match_cursor,
                receipt_item_cursor=next_line_cursor,
                transaction_cursor=next_transaction_cursor,
                has_more=has_more,
            )
            log_event(logger, "classification_backfill_dry_run", **result.safe_metrics())
            return result

        assert current is not None
        suggestions: dict[tuple[ClassificationSourceType, int], ClassificationModelSuggestion] = {}
        model_candidates: list[ClassificationBatchCandidate] = []
        model_calls = 0
        model_failure_code: str | None = None
        if use_model:
            model_candidates = self._model_candidates(receipt_lines, transactions)
            if model_candidates:
                model_calls = 1
                try:
                    model_values = self.model_service.classify(model_candidates)
                    suggestions = {
                        (value.decision.source_type, value.decision.source_entity_id): value
                        for value in model_values
                    }
                except ClassificationModelError as exc:
                    model_failure_code = exc.code
                    log_event(
                        logger,
                        "classification_backfill_model_fallback",
                        workspace_id=self.workspace_id,
                        error_code=exc.code,
                        retryable=exc.retryable,
                    )

        # Reacquire source rows only after provider I/O.  Source rows are always
        # locked before the workspace taxonomy namespace/checkpoint, matching
        # online correction and classification lock order.  This avoids the
        # settings -> source inversion that could otherwise deadlock a user
        # correction while a model-assisted page is being applied.
        transaction_ids = [value.id for value in transactions]
        receipt_ids_page = [value.id for value in receipts]
        receipt_line_ids = [value.id for value in receipt_lines]
        transactions = self._lock_transactions(transaction_ids)
        receipts = self._lock_receipts(receipt_ids_page)
        receipt_lines = self._lock_receipt_items(receipt_line_ids)

        # Lock and refresh the checkpoint only after all bounded source rows.
        # A concurrent owner kill switch or cursor move wins before any page
        # mutation, and the whole page remains all-or-nothing.
        current = self.db.scalar(_for_update(self.db, settings_statement))
        if current is None or not current.autonomous_enabled:
            self.db.rollback()
            return ClassificationBackfillResult(
                self.workspace_id,
                False,
                True,
                False,
                receipt_match_cursor=(current.receipt_match_backfill_cursor if current else 0),
                receipt_item_cursor=(current.receipt_item_backfill_cursor if current else 0),
                transaction_cursor=(current.transaction_backfill_cursor if current else 0),
            )
        if (
            current.receipt_match_backfill_cursor != match_cursor
            or current.receipt_item_backfill_cursor != line_cursor
            or current.transaction_backfill_cursor != transaction_cursor
        ):
            self.db.rollback()
            raise RuntimeError("Classification backfill checkpoint changed during page planning.")

        suggestions = self._retain_current_consented_suggestions(
            suggestions,
            receipt_lines=receipt_lines,
            transactions=transactions,
        )

        reconciliation = ReceiptTransactionReconciliationService(self.db)
        reconciled = 0
        match_counts = {
            ReceiptTransactionMatchStatus.AUTO_MATCHED: 0,
            ReceiptTransactionMatchStatus.AMBIGUOUS: 0,
            ReceiptTransactionMatchStatus.NO_MATCH: 0,
        }
        for receipt in receipts:
            decision = reconciliation.reconcile_receipt(receipt)
            reconciled += 1
            if decision.status in match_counts:
                match_counts[decision.status] += 1

        autonomous = AutonomousClassificationService(self.db, self.settings)
        classification_totals = ClassificationRunSummary()
        receipt_ids = sorted(
            {line.receipt_id for line in receipt_lines if line.classification_applied_at is None}
        )
        for receipt_id in receipt_ids:
            receipt = self.db.scalar(
                select(PurchaseReceipt).where(
                    PurchaseReceipt.workspace_id == self.workspace_id,
                    PurchaseReceipt.id == receipt_id,
                )
            )
            if receipt is None:
                raise RuntimeError("Backfill receipt escaped the active workspace.")
            summary = autonomous.classify_receipt(
                receipt,
                model_suggestions={
                    line.id: suggestion
                    for line in receipt.items
                    if (
                        suggestion := suggestions.get(
                            (ClassificationSourceType.RECEIPT_LINE, line.id)
                        )
                    )
                    is not None
                },
                commit=False,
            )
            classification_totals.merge(summary)

        for transaction in transactions:
            if transaction.classification_applied_at is not None:
                continue
            summary = autonomous.classify_transaction(
                transaction,
                model_suggestion=suggestions.get(
                    (ClassificationSourceType.TRANSACTION, transaction.id)
                ),
                commit=False,
            )
            classification_totals.merge(summary)

        # A page containing a failed internal classification is not checkpointed.
        # This surfaces the data problem and makes the entire page resumable.
        if classification_totals.failures:
            self.db.rollback()
            raise RuntimeError("Classification backfill page failed; no cursor was advanced.")
        self.db.flush()
        unresolved_line_ids = [
            line.id for line in receipt_lines if line.classification_applied_at is None
        ]
        unresolved_transaction_ids = [
            transaction.id
            for transaction in transactions
            if transaction.classification_applied_at is None
        ]
        if unresolved_line_ids or unresolved_transaction_ids:
            self.db.rollback()
            raise RuntimeError(
                "Classification backfill left eligible rows unresolved; no cursor was advanced."
            )

        current.receipt_match_backfill_cursor = next_match_cursor
        current.receipt_item_backfill_cursor = next_line_cursor
        current.transaction_backfill_cursor = next_transaction_cursor
        current.last_backfill_at = now or utc_now()
        current.updated_at = now or utc_now()
        self.db.flush()
        observation = (
            getattr(self.model_service, "last_observation", None) if model_calls else None
        )
        result = ClassificationBackfillResult(
            workspace_id=self.workspace_id,
            enabled=True,
            lease_acquired=True,
            dry_run=False,
            receipt_match_scanned=len(receipts),
            receipts_reconciled=reconciled,
            receipt_auto_matches=match_counts[ReceiptTransactionMatchStatus.AUTO_MATCHED],
            receipt_match_ambiguous=match_counts[ReceiptTransactionMatchStatus.AMBIGUOUS],
            receipt_match_no_match=match_counts[ReceiptTransactionMatchStatus.NO_MATCH],
            receipt_items_scanned=len(receipt_lines),
            receipt_items_classified=classification_totals.receipt_items_categorized,
            transactions_scanned=len(transactions),
            transactions_classified=classification_totals.transactions_categorized,
            receipt_match_cursor=next_match_cursor,
            receipt_item_cursor=next_line_cursor,
            transaction_cursor=next_transaction_cursor,
            has_more=has_more,
            model_candidates=len(model_candidates),
            model_calls=model_calls,
            model_failure_code=model_failure_code,
            model_latency_ms=observation.latency_ms if observation else None,
            model_input_tokens=observation.input_tokens if observation else None,
            model_output_tokens=observation.output_tokens if observation else None,
            model_estimated_cost_micros=(
                observation.estimated_cost_micros if observation else None
            ),
        )
        log_event(logger, "classification_backfill_page_completed", **result.safe_metrics())
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return result

    def _retain_current_consented_suggestions(
        self,
        suggestions: dict[
            tuple[ClassificationSourceType, int], ClassificationModelSuggestion
        ],
        *,
        receipt_lines: list[PurchaseReceiptItem],
        transactions: list[ExpenseTransaction],
    ) -> dict[tuple[ClassificationSourceType, int], ClassificationModelSuggestion]:
        if not suggestions:
            return {}
        allowed: dict[
            tuple[ClassificationSourceType, int], ClassificationModelSuggestion
        ] = {}
        for line in receipt_lines:
            key = (ClassificationSourceType.RECEIPT_LINE, line.id)
            value = suggestions.get(key)
            if value is not None and self._consent_granted(
                "model_receipt_processing",
                user_id=line.receipt.owner_user_id,
                lock=True,
            ):
                allowed[key] = value
        for transaction in transactions:
            key = (ClassificationSourceType.TRANSACTION, transaction.id)
            value = suggestions.get(key)
            if value is not None and self._consent_granted(
                "model_transaction_classification",
                user_id=self._transaction_owner_user_id(transaction),
                lock=True,
            ):
                allowed[key] = value
        return allowed

    def _receipt_page(self, *, cursor: int, limit: int, lock: bool) -> list[PurchaseReceipt]:
        statement = (
            select(PurchaseReceipt)
            .where(
                PurchaseReceipt.workspace_id == self.workspace_id,
                PurchaseReceipt.id > cursor,
                PurchaseReceipt.parse_status.not_in(
                    (ReceiptParseStatus.FAILED.value, ReceiptParseStatus.IGNORED.value)
                ),
            )
            .order_by(PurchaseReceipt.id)
            .limit(limit)
        )
        return list(self.db.scalars(_for_update(self.db, statement) if lock else statement))

    def _receipt_item_page(
        self, *, cursor: int, limit: int, lock: bool
    ) -> list[PurchaseReceiptItem]:
        statement = (
            select(PurchaseReceiptItem)
            .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
            .where(
                PurchaseReceipt.workspace_id == self.workspace_id,
                PurchaseReceiptItem.id > cursor,
                PurchaseReceipt.parse_status.not_in(
                    (ReceiptParseStatus.FAILED.value, ReceiptParseStatus.IGNORED.value)
                ),
            )
            .options(selectinload(PurchaseReceiptItem.receipt))
            .order_by(PurchaseReceiptItem.id)
            .limit(limit)
        )
        return list(
            self.db.scalars(_for_update(self.db, statement) if lock else statement).unique()
        )

    def _transaction_page(self, *, cursor: int, limit: int, lock: bool) -> list[ExpenseTransaction]:
        statement = (
            select(ExpenseTransaction)
            .where(
                ExpenseTransaction.workspace_id == self.workspace_id,
                ExpenseTransaction.id > cursor,
                ExpenseTransaction.status != TransactionStatus.REMOVED.value,
            )
            .order_by(ExpenseTransaction.id)
            .limit(limit)
        )
        return list(self.db.scalars(_for_update(self.db, statement) if lock else statement))

    def _lock_transactions(self, ids: list[int]) -> list[ExpenseTransaction]:
        if not ids:
            return []
        statement = (
            select(ExpenseTransaction)
            .where(
                ExpenseTransaction.workspace_id == self.workspace_id,
                ExpenseTransaction.id.in_(ids),
                ExpenseTransaction.status != TransactionStatus.REMOVED.value,
            )
            .order_by(ExpenseTransaction.id)
            .execution_options(populate_existing=True)
        )
        return list(self.db.scalars(_for_update(self.db, statement)))

    def _lock_receipts(self, ids: list[int]) -> list[PurchaseReceipt]:
        if not ids:
            return []
        statement = (
            select(PurchaseReceipt)
            .where(
                PurchaseReceipt.workspace_id == self.workspace_id,
                PurchaseReceipt.id.in_(ids),
                PurchaseReceipt.parse_status.not_in(
                    (ReceiptParseStatus.FAILED.value, ReceiptParseStatus.IGNORED.value)
                ),
            )
            .order_by(PurchaseReceipt.id)
            .execution_options(populate_existing=True)
        )
        return list(self.db.scalars(_for_update(self.db, statement)))

    def _lock_receipt_items(self, ids: list[int]) -> list[PurchaseReceiptItem]:
        if not ids:
            return []
        statement = (
            select(PurchaseReceiptItem)
            .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
            .where(
                PurchaseReceipt.workspace_id == self.workspace_id,
                PurchaseReceiptItem.id.in_(ids),
                PurchaseReceipt.parse_status.not_in(
                    (ReceiptParseStatus.FAILED.value, ReceiptParseStatus.IGNORED.value)
                ),
            )
            .options(selectinload(PurchaseReceiptItem.receipt))
            .order_by(PurchaseReceiptItem.id)
            .execution_options(populate_existing=True)
        )
        return list(self.db.scalars(_for_update(self.db, statement)).unique())

    def _model_candidates(
        self,
        receipt_lines: list[PurchaseReceiptItem],
        transactions: list[ExpenseTransaction],
    ) -> list[ClassificationBatchCandidate]:
        if not self.settings.openai_api_key:
            return []
        candidates: list[ClassificationBatchCandidate] = []
        for line in receipt_lines:
            if (
                len(candidates) >= self.settings.classification_batch_size
                or not self._consent_granted(
                    "model_receipt_processing",
                    user_id=line.receipt.owner_user_id,
                )
                or line.classification_applied_at is not None
                or not _receipt_line_needs_model(line)
            ):
                continue
            try:
                candidates.append(
                    ClassificationBatchCandidate(
                        source_type=ClassificationSourceType.RECEIPT_LINE,
                        source_entity_id=line.id,
                        name=line.raw_name,
                        merchant=line.receipt.merchant_normalized or line.receipt.merchant_raw,
                        receipt_category=line.category,
                        receipt_tracking_classification=line.classification,
                    )
                )
            except ValueError:
                log_event(
                    logger,
                    "classification_backfill_model_candidate_rejected",
                    workspace_id=self.workspace_id,
                    source_type=ClassificationSourceType.RECEIPT_LINE.value,
                )
        for transaction in transactions:
            if (
                len(candidates) >= self.settings.classification_batch_size
                or not self._consent_granted(
                    "model_transaction_classification",
                    user_id=self._transaction_owner_user_id(transaction),
                )
                or transaction.classification_applied_at is not None
                or not _transaction_needs_model(transaction)
            ):
                continue
            # A linked receipt has stronger code-owned authority and will be
            # composed by AutonomousClassificationService without a provider.
            if self.db.scalar(
                select(PurchaseReceipt.id).where(
                    PurchaseReceipt.workspace_id == self.workspace_id,
                    PurchaseReceipt.transaction_id == transaction.id,
                )
            ):
                continue
            try:
                candidates.append(
                    ClassificationBatchCandidate(
                        source_type=ClassificationSourceType.TRANSACTION,
                        source_entity_id=transaction.id,
                        name=transaction.merchant_name or transaction.name,
                        merchant=transaction.merchant_name,
                        provider_category=transaction.provider_category or transaction.category,
                    )
                )
            except ValueError:
                log_event(
                    logger,
                    "classification_backfill_model_candidate_rejected",
                    workspace_id=self.workspace_id,
                    source_type=ClassificationSourceType.TRANSACTION.value,
                )
        return candidates

    def _transaction_owner_user_id(self, transaction: ExpenseTransaction) -> int | None:
        return self.db.scalar(
            select(PlaidItem.owner_user_id).where(
                PlaidItem.workspace_id == self.workspace_id,
                PlaidItem.id == transaction.plaid_item_id,
            )
        )

    def _consent_granted(
        self,
        purpose: str,
        *,
        user_id: int | None,
        lock: bool = False,
    ) -> bool:
        if user_id is None:
            return False
        statement = (
            select(DataConsent.id)
            .join(
                WorkspaceMembership,
                (WorkspaceMembership.workspace_id == DataConsent.workspace_id)
                & (WorkspaceMembership.user_id == DataConsent.user_id),
            )
            .join(User, User.id == DataConsent.user_id)
            .where(
                DataConsent.workspace_id == self.workspace_id,
                DataConsent.user_id == user_id,
                DataConsent.purpose == purpose,
                DataConsent.granted.is_(True),
                DataConsent.revoked_at.is_(None),
                User.status == "active",
                User.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if lock and self.db.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update(of=DataConsent)
        return bool(self.db.scalar(statement))


def run_backfill_for_workspace(
    db: Session,
    *,
    workspace_id: int,
    settings: Settings | None = None,
    batch_size: int | None = None,
    dry_run: bool = False,
    use_model: bool = False,
    model_service: ClassificationModelService | None = None,
) -> ClassificationBackfillResult:
    """Acquire and commit the tenant lease before reading a backfill page."""

    configured = settings or get_settings()
    if db.info.get("workspace_id") != workspace_id:
        raise ValueError("Backfill workspace does not match the active tenant scope.")
    token = acquire_job_lease(
        db,
        workspace_id=workspace_id,
        job_name=BACKFILL_JOB_NAME,
        lease_seconds=1800,
    )
    if token is None:
        return ClassificationBackfillResult(
            workspace_id,
            configured.autonomous_classification_enabled,
            False,
            dry_run,
        )
    db.commit()
    try:
        return ClassificationBackfillService(
            db,
            configured,
            model_service=model_service,
        ).run_page(
            batch_size=batch_size,
            dry_run=dry_run,
            use_model=use_model,
            commit=True,
        )
    except Exception:
        db.rollback()
        raise
    finally:
        release_job_lease(
            db,
            workspace_id=workspace_id,
            job_name=BACKFILL_JOB_NAME,
            lease_token=token,
        )


def _for_update(db: Session, statement):
    # The committed per-workspace lease prevents competing backfills. Blocking
    # locks intentionally preserve contiguous cursor semantics: SKIP LOCKED
    # could otherwise advance past a temporarily locked historical row.
    if db.get_bind().dialect.name == "postgresql":
        return statement.with_for_update()
    return statement


def _receipt_line_needs_model(line: PurchaseReceiptItem) -> bool:
    rule = classify_known_text(
        source_type=ClassificationSourceType.RECEIPT_LINE,
        source_entity_id=line.id,
        text=" ".join(value for value in (line.raw_name, line.brand) if value),
        provider_category=line.category,
    )
    if rule.spending_parent_category is not SpendingParentCategory.OTHER_UNCERTAIN:
        return False
    return float(line.classification_confidence or 0.0) < 0.85


def _transaction_needs_model(transaction: ExpenseTransaction) -> bool:
    rule = classify_known_text(
        source_type=ClassificationSourceType.TRANSACTION,
        source_entity_id=transaction.id,
        text=transaction.merchant_name or transaction.name,
        provider_category=transaction.provider_category or transaction.category,
    )
    return rule.spending_parent_category is SpendingParentCategory.OTHER_UNCERTAIN

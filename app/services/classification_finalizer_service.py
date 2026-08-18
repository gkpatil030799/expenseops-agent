from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings, get_settings
from app.logging_config import log_event
from app.models import (
    ClassificationAuthority,
    ClassificationDecisionState,
    ClassificationSettings,
    DataConsent,
    ExpenseTransaction,
    PlaidItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    User,
    WorkspaceMembership,
    utc_now,
)
from app.services.autonomous_classification_service import (
    AutonomousClassificationService,
)
from app.services.classification_model_service import (
    ClassificationBatchCandidate,
    ClassificationModelError,
    ClassificationModelService,
    ClassificationModelSuggestion,
)
from app.services.classification_taxonomy_service import ClassificationSourceType
from app.services.job_lease_service import acquire_job_lease, release_job_lease

logger = logging.getLogger(__name__)

FINALIZER_JOB_NAME = "classification_finalizer"


@dataclass(frozen=True)
class ClassificationFinalizerResult:
    workspace_id: int
    enabled: bool
    lease_acquired: bool
    due: int = 0
    receipt_lines_finalized: int = 0
    receipt_lines_recovered: int = 0
    transactions_finalized: int = 0
    transactions_recovered: int = 0
    model_candidates: int = 0
    model_calls: int = 0
    deterministic_fallbacks: int = 0
    skipped: int = 0
    failures: int = 0
    model_failure_code: str | None = None
    model_latency_ms: int | None = None
    model_input_tokens: int | None = None
    model_output_tokens: int | None = None
    model_estimated_cost_micros: int | None = None

    @property
    def finalized(self) -> int:
        return self.receipt_lines_finalized + self.transactions_finalized

    def safe_metrics(self) -> dict[str, int | bool | str | None]:
        return {
            "workspace_id": self.workspace_id,
            "enabled": self.enabled,
            "lease_acquired": self.lease_acquired,
            "due": self.due,
            "receipt_lines_finalized": self.receipt_lines_finalized,
            "receipt_lines_recovered": self.receipt_lines_recovered,
            "transactions_finalized": self.transactions_finalized,
            "transactions_recovered": self.transactions_recovered,
            "model_candidates": self.model_candidates,
            "model_calls": self.model_calls,
            "deterministic_fallbacks": self.deterministic_fallbacks,
            "skipped": self.skipped,
            "failures": self.failures,
            "model_failure_code": self.model_failure_code,
            "model_latency_ms": self.model_latency_ms,
            "model_input_tokens": self.model_input_tokens,
            "model_output_tokens": self.model_output_tokens,
            "model_estimated_cost_micros": self.model_estimated_cost_micros,
        }


class ClassificationFinalizerService:
    """Finalize due provisional classifications in one tenant-bounded batch.

    Model assistance is optional, consent gated, and schema constrained by
    ``ClassificationModelService``.  Every eligible row still reaches the
    deterministic projection fallback when the provider is absent or fails.
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
            raise ValueError("Classification finalization requires a workspace scope.")
        self.workspace_id = int(workspace_id)
        self.autonomous = AutonomousClassificationService(db, self.settings)
        self.model_service = model_service or ClassificationModelService(self.settings)

    def run(
        self,
        *,
        batch_size: int | None = None,
        now: datetime | None = None,
        use_model: bool = True,
        commit: bool = True,
    ) -> ClassificationFinalizerResult:
        limit = batch_size or self.settings.classification_finalizer_batch_size
        if not 1 <= limit <= self.settings.classification_finalizer_batch_size:
            raise ValueError("Finalizer batch size exceeds the configured bound.")
        if not self.settings.autonomous_classification_enabled:
            return ClassificationFinalizerResult(self.workspace_id, False, True)
        workspace_settings = self.autonomous.taxonomy.get_settings()
        if workspace_settings is None or not workspace_settings.autonomous_enabled:
            return ClassificationFinalizerResult(self.workspace_id, False, True)

        due_at = now or utc_now()
        receipt_lines, transactions = self._claim_due(limit=limit, due_at=due_at)
        targets: list[PurchaseReceiptItem | ExpenseTransaction] = [
            *receipt_lines,
            *transactions,
        ]
        suggestions: dict[tuple[ClassificationSourceType, int], ClassificationModelSuggestion] = {}
        model_candidates = self._model_candidates(targets) if use_model else []
        model_failure_code: str | None = None
        model_calls = 0
        if model_candidates:
            model_calls = 1
            try:
                values = self.model_service.classify(model_candidates)
                suggestions = {
                    (value.decision.source_type, value.decision.source_entity_id): value
                    for value in values
                }
            except ClassificationModelError as exc:
                model_failure_code = exc.code
                log_event(
                    logger,
                    "classification_finalizer_model_fallback",
                    workspace_id=self.workspace_id,
                    error_code=exc.code,
                    retryable=exc.retryable,
                )

        # Owner controls are authoritative even when they change while the
        # optional provider request is in flight. Refresh after network I/O,
        # but do not lock the workspace namespace before a source row: online
        # corrections use source -> namespace order.
        workspace_settings = self._current_workspace_settings()
        if workspace_settings is None:
            observation = (
                getattr(self.model_service, "last_observation", None) if model_calls else None
            )
            result = ClassificationFinalizerResult(
                workspace_id=self.workspace_id,
                enabled=False,
                lease_acquired=True,
                due=len(targets),
                model_candidates=len(model_candidates),
                model_calls=model_calls,
                skipped=len(targets),
                model_failure_code=model_failure_code,
                model_latency_ms=observation.latency_ms if observation else None,
                model_input_tokens=observation.input_tokens if observation else None,
                model_output_tokens=observation.output_tokens if observation else None,
                model_estimated_cost_micros=(
                    observation.estimated_cost_micros if observation else None
                ),
            )
            log_event(
                logger,
                "classification_finalizer_disabled_after_planning",
                **result.safe_metrics(),
            )
            return result

        receipt_finalized = 0
        receipt_recovered = 0
        transaction_finalized = 0
        transaction_recovered = 0
        skipped = 0
        failures = 0
        deterministic_fallbacks = 0
        affected_transaction_ids: set[int] = set()
        disabled_after_planning = False
        for target_index, candidate_target in enumerate(targets):
            # The optional provider call happens without target-row locks. Lock
            # and refresh only now, then recheck every state predicate. A user
            # correction which raced the model therefore either wins or makes
            # this row a nonblocking skip.
            target = self._lock_current_target(candidate_target, due_at=due_at)
            if target is None:
                skipped += 1
                if commit:
                    self.db.commit()
                continue
            # Lock source first, then workspace settings.  Each production
            # target is committed independently so the namespace lock is never
            # retained while the next source row is acquired.  This is the same
            # order as interactive corrections and removes the batch deadlock
            # cycle while keeping the kill switch atomic with this target.
            workspace_settings = self._lock_workspace_settings(lock=commit)
            if workspace_settings is None or not workspace_settings.autonomous_enabled:
                skipped += len(targets) - target_index
                disabled_after_planning = True
                if commit:
                    self.db.rollback()
                break
            is_retry_target = bool(
                target.classification_retry_at is not None
                and _is_due(target.classification_retry_at, due_at)
            )
            if is_retry_target:
                target_savepoint = self.db.begin_nested()
                if isinstance(target, PurchaseReceiptItem):
                    summary = self.autonomous.classify_receipt(
                        target.receipt,
                        only_line_ids=frozenset({target.id}),
                        commit=False,
                    )
                    repaired = bool(
                        summary.failures == 0
                        and target.classification_applied_at is not None
                        and self.autonomous._receipt_learning_is_complete(
                            target.receipt,
                            target,
                        )
                    )
                else:
                    summary = self.autonomous.classify_transaction(target, commit=False)
                    repaired = bool(
                        summary.failures == 0
                        and target.classification_applied_at is not None
                    )
                failures += summary.failures
                if not repaired:
                    target_savepoint.rollback()
                    skipped += int(summary.failures == 0)
                    if commit:
                        self.db.rollback()
                    continue
                target.classification_retry_at = None
                target_savepoint.commit()
                if isinstance(target, PurchaseReceiptItem):
                    receipt_recovered += 1
                else:
                    transaction_recovered += 1
                if commit:
                    self.db.commit()
                continue
            is_deferred_receipt_line = bool(
                isinstance(target, PurchaseReceiptItem)
                and target.classification_applied_at is None
            )
            if is_deferred_receipt_line:
                target_savepoint = self.db.begin_nested()
                summary = self.autonomous.classify_receipt(
                    target.receipt,
                    only_line_ids=frozenset({target.id}),
                    commit=False,
                )
                failures += summary.failures
                if target.classification_applied_at is None or summary.failures:
                    target_savepoint.rollback()
                    skipped += int(summary.failures == 0)
                    if commit:
                        self.db.rollback()
                    continue
                target_savepoint.commit()
                receipt_recovered += 1
                if commit:
                    self.db.commit()
                continue
            is_deferred_transaction = bool(
                isinstance(target, ExpenseTransaction)
                and target.classification_applied_at is None
            )
            if is_deferred_transaction:
                target_savepoint = self.db.begin_nested()
                summary = self.autonomous.classify_transaction(target, commit=False)
                failures += summary.failures
                if target.classification_applied_at is None or summary.failures:
                    target_savepoint.rollback()
                    skipped += int(summary.failures == 0)
                    if commit:
                        self.db.rollback()
                    continue
                target_savepoint.commit()
                transaction_recovered += 1
                if commit:
                    self.db.commit()
                continue
            if (
                target.classification_decision_state
                != ClassificationDecisionState.PROVISIONAL.value
                or target.classification_authority == ClassificationAuthority.USER_CORRECTION.value
                or target.classification_auto_finalize_at is None
                or not _is_due(target.classification_auto_finalize_at, due_at)
            ):
                skipped += 1
                if commit:
                    self.db.commit()
                continue
            source_type = (
                ClassificationSourceType.RECEIPT_LINE
                if isinstance(target, PurchaseReceiptItem)
                else ClassificationSourceType.TRANSACTION
            )
            suggestion = suggestions.get((source_type, target.id))
            if suggestion is not None and not self._target_model_consent_is_current(
                target,
                lock=commit,
            ):
                suggestion = None
            if suggestion is None:
                deterministic_fallbacks += 1
            target_savepoint = self.db.begin_nested()
            summary = self.autonomous.finalize_decision(
                target,
                model_suggestion=suggestion,
                commit=False,
            )
            if suggestion is not None and summary.classifications_auto_finalized != 1:
                # A lower-authority or otherwise unusable model proposal must
                # never strand a due row. Finalize its existing projection.
                deterministic_fallbacks += 1
                summary.merge(
                    self.autonomous.finalize_decision(
                        target,
                        model_suggestion=None,
                        commit=False,
                    )
                )
            failures += summary.failures
            if summary.classifications_auto_finalized != 1:
                target_savepoint.rollback()
                skipped += summary.skipped or int(summary.failures == 0)
                if commit:
                    self.db.rollback()
                continue
            if isinstance(target, PurchaseReceiptItem):
                if target.receipt.transaction_id is not None:
                    transaction_id = target.receipt.transaction_id
                    if commit:
                        try:
                            self.autonomous.recompute_transaction_from_receipt_state(
                                transaction_id,
                                commit=False,
                            )
                        except (ValueError, RuntimeError):
                            target_savepoint.rollback()
                            failures += 1
                            self.db.rollback()
                            continue
                    else:
                        affected_transaction_ids.add(transaction_id)
                receipt_finalized += 1
            else:
                transaction_finalized += 1
            target_savepoint.commit()
            if commit:
                self.db.commit()

        for transaction_id in sorted(affected_transaction_ids):
            try:
                self.autonomous.recompute_transaction_from_receipt_state(
                    transaction_id,
                    commit=False,
                )
            except (ValueError, RuntimeError):
                failures += 1

        observation = (
            getattr(self.model_service, "last_observation", None) if model_calls else None
        )
        result = ClassificationFinalizerResult(
            workspace_id=self.workspace_id,
            enabled=not disabled_after_planning,
            lease_acquired=True,
            due=len(targets),
            receipt_lines_finalized=receipt_finalized,
            receipt_lines_recovered=receipt_recovered,
            transactions_finalized=transaction_finalized,
            transactions_recovered=transaction_recovered,
            model_candidates=len(model_candidates),
            model_calls=model_calls,
            deterministic_fallbacks=deterministic_fallbacks,
            skipped=skipped,
            failures=failures,
            model_failure_code=model_failure_code,
            model_latency_ms=observation.latency_ms if observation else None,
            model_input_tokens=observation.input_tokens if observation else None,
            model_output_tokens=observation.output_tokens if observation else None,
            model_estimated_cost_micros=(
                observation.estimated_cost_micros if observation else None
            ),
        )
        log_event(logger, "classification_finalizer_completed", **result.safe_metrics())
        if not commit:
            self.db.flush()
        return result

    def _current_workspace_settings(self) -> ClassificationSettings | None:
        statement = (
            select(ClassificationSettings)
            .where(
                ClassificationSettings.workspace_id == self.workspace_id,
            )
            .execution_options(populate_existing=True)
        )
        return self.db.scalar(statement)

    def _lock_workspace_settings(self, *, lock: bool) -> ClassificationSettings | None:
        statement = (
            select(ClassificationSettings)
            .where(ClassificationSettings.workspace_id == self.workspace_id)
            .execution_options(populate_existing=True)
        )
        if lock and self.db.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def _target_model_consent_is_current(
        self,
        target: PurchaseReceiptItem | ExpenseTransaction,
        *,
        lock: bool,
    ) -> bool:
        if isinstance(target, PurchaseReceiptItem):
            return self._consent_granted(
                "model_receipt_processing",
                user_id=target.receipt.owner_user_id,
                lock=lock,
            )
        return self._consent_granted(
            "model_transaction_classification",
            user_id=self._transaction_owner_user_id(target),
            lock=lock,
        )

    def _claim_due(
        self, *, limit: int, due_at: datetime
    ) -> tuple[list[PurchaseReceiptItem], list[ExpenseTransaction]]:
        # Pull bounded snapshots without row locks. The workspace lease prevents
        # duplicate finalizer workers, while corrections must remain available
        # during the optional provider call. Rows are locked/re-read immediately
        # before application by ``_lock_current_target``.
        line_refs = list(
            self.db.execute(
                select(
                    PurchaseReceiptItem.id,
                    PurchaseReceiptItem.classification_auto_finalize_at,
                )
                .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
                .where(
                    PurchaseReceipt.workspace_id == self.workspace_id,
                    PurchaseReceipt.parse_status.not_in(("ignored", "failed")),
                    PurchaseReceiptItem.classification_decision_state
                    == ClassificationDecisionState.PROVISIONAL.value,
                    PurchaseReceiptItem.classification_authority
                    != ClassificationAuthority.USER_CORRECTION.value,
                    PurchaseReceiptItem.classification_auto_finalize_at.is_not(None),
                    PurchaseReceiptItem.classification_auto_finalize_at <= due_at,
                )
                .order_by(
                    PurchaseReceiptItem.classification_auto_finalize_at,
                    PurchaseReceiptItem.id,
                )
                .limit(limit)
            )
        )
        deferred_line_refs = list(
            self.db.execute(
                select(
                    PurchaseReceiptItem.id,
                    PurchaseReceipt.created_at,
                )
                .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
                .where(
                    PurchaseReceipt.workspace_id == self.workspace_id,
                    PurchaseReceipt.parse_status.not_in(("ignored", "failed")),
                    PurchaseReceiptItem.classification_applied_at.is_(None),
                    PurchaseReceiptItem.classification_auto_finalize_at.is_not(None),
                    PurchaseReceiptItem.classification_auto_finalize_at <= due_at,
                    or_(
                        PurchaseReceiptItem.classification_authority.is_(None),
                        PurchaseReceiptItem.classification_authority
                        != ClassificationAuthority.USER_CORRECTION.value,
                    ),
                )
                .order_by(PurchaseReceiptItem.id)
                .limit(limit)
            )
        )
        retry_line_refs = list(
            self.db.execute(
                select(
                    PurchaseReceiptItem.id,
                    PurchaseReceiptItem.classification_retry_at,
                )
                .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
                .where(
                    PurchaseReceipt.workspace_id == self.workspace_id,
                    PurchaseReceipt.parse_status.not_in(("ignored", "failed")),
                    PurchaseReceiptItem.classification_retry_at.is_not(None),
                    PurchaseReceiptItem.classification_retry_at <= due_at,
                    or_(
                        PurchaseReceiptItem.classification_authority.is_(None),
                        PurchaseReceiptItem.classification_authority
                        != ClassificationAuthority.USER_CORRECTION.value,
                    ),
                )
                .order_by(PurchaseReceiptItem.classification_retry_at, PurchaseReceiptItem.id)
                .limit(limit)
            )
        )
        transaction_refs = list(
            self.db.execute(
                select(
                    ExpenseTransaction.id,
                    ExpenseTransaction.classification_auto_finalize_at,
                )
                .where(
                    ExpenseTransaction.workspace_id == self.workspace_id,
                    ExpenseTransaction.status != "removed",
                    ExpenseTransaction.classification_decision_state
                    == ClassificationDecisionState.PROVISIONAL.value,
                    ExpenseTransaction.classification_authority
                    != ClassificationAuthority.USER_CORRECTION.value,
                    ExpenseTransaction.classification_auto_finalize_at.is_not(None),
                    ExpenseTransaction.classification_auto_finalize_at <= due_at,
                )
                .order_by(
                    ExpenseTransaction.classification_auto_finalize_at,
                    ExpenseTransaction.id,
                )
                .limit(limit)
            )
        )
        deferred_transaction_refs = list(
            self.db.execute(
                select(
                    ExpenseTransaction.id,
                    ExpenseTransaction.created_at,
                )
                .where(
                    ExpenseTransaction.workspace_id == self.workspace_id,
                    ExpenseTransaction.status != "removed",
                    ExpenseTransaction.classification_applied_at.is_(None),
                    ExpenseTransaction.classification_auto_finalize_at.is_not(None),
                    ExpenseTransaction.classification_auto_finalize_at <= due_at,
                    or_(
                        ExpenseTransaction.classification_authority.is_(None),
                        ExpenseTransaction.classification_authority
                        != ClassificationAuthority.USER_CORRECTION.value,
                    ),
                )
                .order_by(ExpenseTransaction.id)
                .limit(limit)
            )
        )
        retry_transaction_refs = list(
            self.db.execute(
                select(
                    ExpenseTransaction.id,
                    ExpenseTransaction.classification_retry_at,
                )
                .where(
                    ExpenseTransaction.workspace_id == self.workspace_id,
                    ExpenseTransaction.status != "removed",
                    ExpenseTransaction.classification_retry_at.is_not(None),
                    ExpenseTransaction.classification_retry_at <= due_at,
                    or_(
                        ExpenseTransaction.classification_authority.is_(None),
                        ExpenseTransaction.classification_authority
                        != ClassificationAuthority.USER_CORRECTION.value,
                    ),
                )
                .order_by(ExpenseTransaction.classification_retry_at, ExpenseTransaction.id)
                .limit(limit)
            )
        )
        refs = sorted(
            [
                (value[1], ClassificationSourceType.RECEIPT_LINE, value[0])
                for value in deferred_line_refs
            ]
            + [(value[1], ClassificationSourceType.RECEIPT_LINE, value[0]) for value in line_refs]
            + [
                (value[1], ClassificationSourceType.TRANSACTION, value[0])
                for value in deferred_transaction_refs
            ]
            + [
                (value[1], ClassificationSourceType.TRANSACTION, value[0])
                for value in transaction_refs
            ]
            + [
                (value[1], ClassificationSourceType.RECEIPT_LINE, value[0])
                for value in retry_line_refs
            ]
            + [
                (value[1], ClassificationSourceType.TRANSACTION, value[0])
                for value in retry_transaction_refs
            ],
            key=lambda value: (_sort_timestamp(value[0]), value[1].value, value[2]),
        )[:limit]
        line_ids = [value[2] for value in refs if value[1] is ClassificationSourceType.RECEIPT_LINE]
        transaction_ids = [
            value[2] for value in refs if value[1] is ClassificationSourceType.TRANSACTION
        ]

        # Match live Plaid reconciliation's transaction→receipt read order.
        transactions: list[ExpenseTransaction] = []
        if transaction_ids:
            statement = (
                select(ExpenseTransaction)
                .where(
                    ExpenseTransaction.workspace_id == self.workspace_id,
                    ExpenseTransaction.id.in_(transaction_ids),
                    ExpenseTransaction.status != "removed",
                    or_(
                        and_(
                            ExpenseTransaction.classification_decision_state
                            == ClassificationDecisionState.PROVISIONAL.value,
                            ExpenseTransaction.classification_authority
                            != ClassificationAuthority.USER_CORRECTION.value,
                            ExpenseTransaction.classification_auto_finalize_at.is_not(None),
                            ExpenseTransaction.classification_auto_finalize_at <= due_at,
                        ),
                        and_(
                            ExpenseTransaction.classification_applied_at.is_(None),
                            ExpenseTransaction.classification_auto_finalize_at.is_not(None),
                            ExpenseTransaction.classification_auto_finalize_at <= due_at,
                            or_(
                                ExpenseTransaction.classification_authority.is_(None),
                                ExpenseTransaction.classification_authority
                                != ClassificationAuthority.USER_CORRECTION.value,
                            ),
                        ),
                        and_(
                            ExpenseTransaction.classification_retry_at.is_not(None),
                            ExpenseTransaction.classification_retry_at <= due_at,
                            or_(
                                ExpenseTransaction.classification_authority.is_(None),
                                ExpenseTransaction.classification_authority
                                != ClassificationAuthority.USER_CORRECTION.value,
                            ),
                        ),
                    ),
                )
                .order_by(
                    ExpenseTransaction.classification_auto_finalize_at,
                    ExpenseTransaction.id,
                )
            )
            transactions = list(
                self.db.scalars(statement.execution_options(populate_existing=True))
            )

        lines: list[PurchaseReceiptItem] = []
        if line_ids:
            statement = (
                select(PurchaseReceiptItem)
                .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
                .where(
                    PurchaseReceipt.workspace_id == self.workspace_id,
                    PurchaseReceipt.parse_status.not_in(("ignored", "failed")),
                    PurchaseReceiptItem.id.in_(line_ids),
                    or_(
                        and_(
                            PurchaseReceiptItem.classification_decision_state
                            == ClassificationDecisionState.PROVISIONAL.value,
                            PurchaseReceiptItem.classification_authority
                            != ClassificationAuthority.USER_CORRECTION.value,
                            PurchaseReceiptItem.classification_auto_finalize_at.is_not(None),
                            PurchaseReceiptItem.classification_auto_finalize_at <= due_at,
                        ),
                        and_(
                            PurchaseReceiptItem.classification_applied_at.is_(None),
                            PurchaseReceiptItem.classification_auto_finalize_at.is_not(None),
                            PurchaseReceiptItem.classification_auto_finalize_at <= due_at,
                            or_(
                                PurchaseReceiptItem.classification_authority.is_(None),
                                PurchaseReceiptItem.classification_authority
                                != ClassificationAuthority.USER_CORRECTION.value,
                            ),
                        ),
                        and_(
                            PurchaseReceiptItem.classification_retry_at.is_not(None),
                            PurchaseReceiptItem.classification_retry_at <= due_at,
                            or_(
                                PurchaseReceiptItem.classification_authority.is_(None),
                                PurchaseReceiptItem.classification_authority
                                != ClassificationAuthority.USER_CORRECTION.value,
                            ),
                        ),
                    ),
                )
                .options(selectinload(PurchaseReceiptItem.receipt))
                .order_by(
                    PurchaseReceiptItem.classification_auto_finalize_at,
                    PurchaseReceiptItem.id,
                )
            )
            lines = list(
                self.db.scalars(
                    statement.execution_options(populate_existing=True)
                ).unique()
            )
        return lines, transactions

    def _lock_current_target(
        self,
        target: PurchaseReceiptItem | ExpenseTransaction,
        *,
        due_at: datetime,
    ) -> PurchaseReceiptItem | ExpenseTransaction | None:
        if isinstance(target, ExpenseTransaction):
            statement = (
                select(ExpenseTransaction)
                .where(
                    ExpenseTransaction.workspace_id == self.workspace_id,
                    ExpenseTransaction.id == target.id,
                    ExpenseTransaction.status != "removed",
                    or_(
                        and_(
                            ExpenseTransaction.classification_decision_state
                            == ClassificationDecisionState.PROVISIONAL.value,
                            ExpenseTransaction.classification_authority
                            != ClassificationAuthority.USER_CORRECTION.value,
                            ExpenseTransaction.classification_auto_finalize_at.is_not(None),
                            ExpenseTransaction.classification_auto_finalize_at <= due_at,
                        ),
                        and_(
                            ExpenseTransaction.classification_applied_at.is_(None),
                            ExpenseTransaction.classification_auto_finalize_at.is_not(None),
                            ExpenseTransaction.classification_auto_finalize_at <= due_at,
                            or_(
                                ExpenseTransaction.classification_authority.is_(None),
                                ExpenseTransaction.classification_authority
                                != ClassificationAuthority.USER_CORRECTION.value,
                            ),
                        ),
                        and_(
                            ExpenseTransaction.classification_retry_at.is_not(None),
                            ExpenseTransaction.classification_retry_at <= due_at,
                            or_(
                                ExpenseTransaction.classification_authority.is_(None),
                                ExpenseTransaction.classification_authority
                                != ClassificationAuthority.USER_CORRECTION.value,
                            ),
                        ),
                    ),
                )
                .execution_options(populate_existing=True)
            )
            return self.db.scalar(_skip_locked(self.db, statement))
        preview = self.db.execute(
            select(PurchaseReceipt.transaction_id)
            .join(PurchaseReceiptItem, PurchaseReceiptItem.receipt_id == PurchaseReceipt.id)
            .where(
                PurchaseReceipt.workspace_id == self.workspace_id,
                PurchaseReceiptItem.id == target.id,
            )
        ).one_or_none()
        if preview is None:
            return None
        linked_transaction_id = preview[0]
        if linked_transaction_id is not None:
            transaction_statement = select(ExpenseTransaction).where(
                ExpenseTransaction.workspace_id == self.workspace_id,
                ExpenseTransaction.id == linked_transaction_id,
                ExpenseTransaction.status != "removed",
            )
            linked_transaction = self.db.scalar(
                _skip_locked(self.db, transaction_statement)
            )
            if linked_transaction is None:
                return None
        statement = (
            select(PurchaseReceiptItem)
            .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
            .where(
                PurchaseReceipt.workspace_id == self.workspace_id,
                PurchaseReceipt.parse_status.not_in(("ignored", "failed")),
                PurchaseReceiptItem.id == target.id,
                or_(
                    and_(
                        PurchaseReceiptItem.classification_decision_state
                        == ClassificationDecisionState.PROVISIONAL.value,
                        PurchaseReceiptItem.classification_authority
                        != ClassificationAuthority.USER_CORRECTION.value,
                        PurchaseReceiptItem.classification_auto_finalize_at.is_not(None),
                        PurchaseReceiptItem.classification_auto_finalize_at <= due_at,
                    ),
                    and_(
                        PurchaseReceiptItem.classification_applied_at.is_(None),
                        PurchaseReceiptItem.classification_auto_finalize_at.is_not(None),
                        PurchaseReceiptItem.classification_auto_finalize_at <= due_at,
                        or_(
                            PurchaseReceiptItem.classification_authority.is_(None),
                            PurchaseReceiptItem.classification_authority
                            != ClassificationAuthority.USER_CORRECTION.value,
                        ),
                    ),
                    and_(
                        PurchaseReceiptItem.classification_retry_at.is_not(None),
                        PurchaseReceiptItem.classification_retry_at <= due_at,
                        or_(
                            PurchaseReceiptItem.classification_authority.is_(None),
                            PurchaseReceiptItem.classification_authority
                            != ClassificationAuthority.USER_CORRECTION.value,
                        ),
                    ),
                ),
            )
            .options(selectinload(PurchaseReceiptItem.receipt))
            .execution_options(populate_existing=True)
        )
        locked = self.db.scalar(_skip_locked(self.db, statement))
        if locked is None or locked.receipt.transaction_id != linked_transaction_id:
            return None
        return locked

    def _model_candidates(
        self, targets: list[PurchaseReceiptItem | ExpenseTransaction]
    ) -> list[ClassificationBatchCandidate]:
        if not targets or not self.settings.openai_api_key:
            return []
        values: list[ClassificationBatchCandidate] = []
        for target in targets:
            if len(values) >= self.settings.classification_batch_size:
                break
            # Retry work replays deterministic/source evidence only. A prior
            # storage failure must not trigger an extra paid provider call.
            if target.classification_retry_at is not None:
                continue
            if isinstance(target, PurchaseReceiptItem):
                if target.classification_applied_at is None:
                    continue
                receipt = target.receipt
                if not self._consent_granted(
                    "model_receipt_processing",
                    user_id=receipt.owner_user_id,
                ):
                    continue
                try:
                    values.append(
                        ClassificationBatchCandidate(
                            source_type=ClassificationSourceType.RECEIPT_LINE,
                            source_entity_id=target.id,
                            name=target.raw_name,
                            merchant=receipt.merchant_normalized or receipt.merchant_raw,
                            receipt_category=target.category,
                            receipt_tracking_classification=target.classification,
                        )
                    )
                except ValueError:
                    log_event(
                        logger,
                        "classification_finalizer_model_candidate_rejected",
                        workspace_id=self.workspace_id,
                        source_type=ClassificationSourceType.RECEIPT_LINE.value,
                    )
            else:
                if target.classification_applied_at is None:
                    continue
                if not self._consent_granted(
                    "model_transaction_classification",
                    user_id=self._transaction_owner_user_id(target),
                ):
                    continue
                try:
                    values.append(
                        ClassificationBatchCandidate(
                            source_type=ClassificationSourceType.TRANSACTION,
                            source_entity_id=target.id,
                            name=target.merchant_name or target.name,
                            merchant=target.merchant_name,
                            provider_category=target.provider_category or target.category,
                        )
                    )
                except ValueError:
                    log_event(
                        logger,
                        "classification_finalizer_model_candidate_rejected",
                        workspace_id=self.workspace_id,
                        source_type=ClassificationSourceType.TRANSACTION.value,
                    )
        return values

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


def run_finalizer_for_workspace(
    db: Session,
    *,
    workspace_id: int,
    settings: Settings | None = None,
    batch_size: int | None = None,
    use_model: bool = True,
    model_service: ClassificationModelService | None = None,
) -> ClassificationFinalizerResult:
    """Acquire and commit the tenant lease before any finalizer rows are read."""

    configured = settings or get_settings()
    if db.info.get("workspace_id") != workspace_id:
        raise ValueError("Finalizer workspace does not match the active tenant scope.")
    token = acquire_job_lease(
        db,
        workspace_id=workspace_id,
        job_name=FINALIZER_JOB_NAME,
    )
    if token is None:
        return ClassificationFinalizerResult(
            workspace_id=workspace_id,
            enabled=configured.autonomous_classification_enabled,
            lease_acquired=False,
        )
    # This transaction boundary is intentional: a competing worker must be
    # able to observe the lease before classification work begins.
    db.commit()
    try:
        return ClassificationFinalizerService(
            db,
            configured,
            model_service=model_service,
        ).run(batch_size=batch_size, use_model=use_model, commit=True)
    except Exception:
        db.rollback()
        raise
    finally:
        release_job_lease(
            db,
            workspace_id=workspace_id,
            job_name=FINALIZER_JOB_NAME,
            lease_token=token,
        )


def _skip_locked(db: Session, statement):
    if db.get_bind().dialect.name == "postgresql":
        return statement.with_for_update(skip_locked=True)
    return statement


def _is_due(value: datetime, now: datetime) -> bool:
    normalized_value = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    normalized_now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return normalized_value.astimezone(UTC) <= normalized_now.astimezone(UTC)


def _sort_timestamp(value: datetime) -> float:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC).timestamp()

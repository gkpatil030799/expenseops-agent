from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from difflib import SequenceMatcher
from enum import StrEnum

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.logging_config import log_event
from app.models import (
    ExpenseTransaction,
    HouseholdItem,
    HouseholdItemAcquisition,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReceiptParseStatus,
    TransactionStatus,
    utc_now,
)
from app.services.acquisition_service import AcquisitionService

logger = logging.getLogger(__name__)

_AMOUNT_TOLERANCE_CENTS = 2
_DATE_TOLERANCE_DAYS = 2
_MERCHANT_MINIMUM = 0.60
_NEAR_TIE_MARGIN = 0.05
_PENDING_PENALTY = 0.06
_MAX_RECEIPTS_PER_TRANSACTION = 100
_MAX_TRANSACTIONS_PER_RECEIPT = 100
_ADVISORY_LOCK_NAMESPACE = 4_558_352
_POLICY_VERSION = "receipt_transaction_v1"

_MERCHANT_NOISE = frozenset(
    {
        "co",
        "company",
        "corp",
        "corporation",
        "inc",
        "llc",
        "location",
        "ltd",
        "market",
        "marketplace",
        "restaurant",
        "store",
        "supercenter",
        "the",
        "warehouse",
        "whse",
    }
)


class ReceiptTransactionMatchStatus(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    AUTO_MATCHED = "auto_matched"
    AMBIGUOUS = "ambiguous"
    NO_MATCH = "no_match"


@dataclass(frozen=True)
class ReceiptTransactionMatchDecision:
    receipt_id: int
    status: ReceiptTransactionMatchStatus
    transaction_id: int | None
    confidence: float
    evidence: dict


@dataclass(frozen=True)
class PendingReplacementMigration:
    receipt_count: int
    acquisition_count: int
    ambiguous_receipt_count: int


@dataclass(frozen=True)
class _ScoredCandidate:
    transaction: ExpenseTransaction
    confidence: float
    merchant_score: float
    amount_delta_cents: int
    date_delta_days: int
    used_authorized_date: bool


class ReceiptTransactionReconciliationService:
    """Deterministically link receipt evidence to canonical Plaid transactions.

    This service performs database-only bookkeeping. It deliberately neither
    commits nor calls a model/provider so callers can compose reconciliation
    atomically with receipt ingestion and Plaid cursor updates.
    """

    def __init__(self, db: Session):
        self.db = db

    def reconcile_receipt(
        self,
        receipt: PurchaseReceipt,
        *,
        transaction_hint: ExpenseTransaction | None = None,
    ) -> ReceiptTransactionMatchDecision:
        if receipt.id is None:
            raise ValueError("Receipt must be persisted before reconciliation.")
        self.db.flush()
        persisted = self._load_receipt(receipt)
        if persisted is None:
            raise ValueError("Receipt is outside the active workspace.")
        receipt = persisted

        if receipt.parse_status in {
            ReceiptParseStatus.FAILED.value,
            ReceiptParseStatus.IGNORED.value,
        }:
            receipt = self._lock_receipt(receipt)
            return self._apply_decision(
                receipt,
                ReceiptTransactionMatchStatus.NO_MATCH,
                None,
                0.0,
                self._evidence("receipt_not_eligible"),
            )

        existing = self._existing_link(receipt)
        if existing is not None:
            receipt = self._lock_receipt(receipt)
            return self._respect_existing_link(receipt, existing)
        if receipt.transaction_id is not None:
            # A non-null link that is not visible inside the active workspace is
            # legacy-corrupt or points at a row that no longer exists. Never
            # query across the tenant boundary to distinguish those cases.
            receipt = self._lock_receipt(receipt)
            receipt.transaction_id = None
            self._sync_receipt_acquisitions(receipt, transaction_id=None)
            return self._apply_decision(
                receipt,
                ReceiptTransactionMatchStatus.AMBIGUOUS,
                None,
                0.0,
                self._evidence("existing_link_unavailable_or_workspace_conflict"),
            )

        insufficient = self._insufficient_reason(receipt)
        if insufficient is not None:
            receipt = self._lock_receipt(receipt)
            if (existing := self._existing_link(receipt)) is not None:
                return self._respect_existing_link(receipt, existing)
            return self._apply_decision(
                receipt,
                ReceiptTransactionMatchStatus.NO_MATCH,
                None,
                0.0,
                self._evidence(insufficient),
            )

        candidates = self._candidate_transactions(receipt)
        if len(candidates) > _MAX_TRANSACTIONS_PER_RECEIPT:
            receipt = self._lock_receipt(receipt)
            if (existing := self._existing_link(receipt)) is not None:
                return self._respect_existing_link(receipt, existing)
            return self._apply_decision(
                receipt,
                ReceiptTransactionMatchStatus.AMBIGUOUS,
                None,
                0.0,
                self._evidence(
                    "candidate_limit_exceeded",
                    candidate_count=len(candidates),
                    candidate_limit=_MAX_TRANSACTIONS_PER_RECEIPT,
                ),
            )
        scored = [score for tx in candidates if (score := self._score(receipt, tx)) is not None]
        scored.sort(key=lambda candidate: (-candidate.confidence, candidate.transaction.id))
        evidence_candidates = [self._candidate_evidence(candidate) for candidate in scored]
        if not scored:
            receipt = self._lock_receipt(receipt)
            if (existing := self._existing_link(receipt)) is not None:
                return self._respect_existing_link(receipt, existing)
            return self._apply_decision(
                receipt,
                ReceiptTransactionMatchStatus.NO_MATCH,
                None,
                0.0,
                self._evidence(
                    "no_eligible_candidate",
                    candidate_count=len(candidates),
                    considered_transaction_ids=[candidate.id for candidate in candidates],
                    candidates=evidence_candidates,
                ),
            )

        top = scored[0]
        if len(scored) > 1 and top.confidence - scored[1].confidence <= _NEAR_TIE_MARGIN:
            receipt = self._lock_receipt(receipt)
            if (existing := self._existing_link(receipt)) is not None:
                return self._apply_decision(
                    receipt,
                    ReceiptTransactionMatchStatus.AMBIGUOUS,
                    None,
                    top.confidence,
                    self._evidence(
                        "near_tie_with_existing_link",
                        candidate_count=len(scored),
                        candidates=evidence_candidates,
                    ),
                    preserve_link=True,
                )
            return self._apply_decision(
                receipt,
                ReceiptTransactionMatchStatus.AMBIGUOUS,
                None,
                top.confidence,
                self._evidence(
                    "near_tie",
                    candidate_count=len(scored),
                    candidates=evidence_candidates,
                ),
            )

        # PostgreSQL advisory locking serializes ownership of one transaction
        # across concurrent receipt ingesters without taking transaction row
        # locks in the opposite order from Plaid upsert.
        self._acquire_match_slot(top.transaction.id)
        receipt = self._lock_receipt(receipt)
        if (existing := self._existing_link(receipt)) is not None:
            if existing.id != top.transaction.id:
                return self._apply_decision(
                    receipt,
                    ReceiptTransactionMatchStatus.AMBIGUOUS,
                    None,
                    top.confidence,
                    self._evidence(
                        "concurrent_existing_link_conflict",
                        selected_transaction_id=top.transaction.id,
                    ),
                    preserve_link=True,
                )
            return self._respect_existing_link(receipt, existing)

        # A Plaid row can arrive after an earlier channel representation was
        # already confirmed. Only a shared durable artifact hash proves that two
        # receipt rows are representations of the same purchase; an identical
        # parsed basket remains cardinality-ambiguous.
        for duplicate in self._unlinked_artifact_duplicates(receipt):
            locked_duplicate = self._lock_receipt(duplicate)
            if (
                locked_duplicate.transaction_id is None
                and self._same_artifact(locked_duplicate, receipt)
            ):
                self._apply_decision(
                    locked_duplicate,
                    ReceiptTransactionMatchStatus.AUTO_MATCHED,
                    top.transaction,
                    top.confidence,
                    self._evidence(
                        "duplicate_receipt_representation_linked_late",
                        selected_transaction_id=top.transaction.id,
                        canonical_receipt_id=locked_duplicate.id,
                    ),
                )
        occupied_receipt_ids = self._other_receipts_for_transaction(
            receipt,
            top.transaction.id,
        )
        if occupied_receipt_ids:
            canonical_duplicate_id = self._canonical_duplicate_receipt_id(
                receipt,
                occupied_receipt_ids,
            )
            if canonical_duplicate_id is not None:
                return self._apply_decision(
                    receipt,
                    ReceiptTransactionMatchStatus.AUTO_MATCHED,
                    top.transaction,
                    top.confidence,
                    self._evidence(
                        "duplicate_receipt_representation",
                        candidate_count=len(scored),
                        candidates=evidence_candidates,
                        selected_transaction_id=top.transaction.id,
                        canonical_receipt_id=canonical_duplicate_id,
                    ),
                )
            return self._apply_decision(
                receipt,
                ReceiptTransactionMatchStatus.AMBIGUOUS,
                None,
                top.confidence,
                self._evidence(
                    "candidate_already_linked",
                    candidate_count=len(scored),
                    candidates=evidence_candidates,
                    occupied_receipt_ids=occupied_receipt_ids,
                ),
            )

        # The hint is deliberately not privileged. It only documents that the
        # selected candidate was the transaction which triggered this attempt.
        hinted = bool(transaction_hint is not None and top.transaction.id == transaction_hint.id)
        return self._apply_decision(
            receipt,
            ReceiptTransactionMatchStatus.AUTO_MATCHED,
            top.transaction,
            top.confidence,
            self._evidence(
                "deterministic_match",
                candidate_count=len(scored),
                candidates=evidence_candidates,
                selected_transaction_id=top.transaction.id,
                transaction_triggered=hinted,
            ),
        )

    def _respect_existing_link(
        self,
        receipt: PurchaseReceipt,
        existing: ExpenseTransaction,
    ) -> ReceiptTransactionMatchDecision:
        if existing.workspace_id != receipt.workspace_id:
            receipt.transaction_id = None
            self._sync_receipt_acquisitions(receipt, transaction_id=None)
            return self._apply_decision(
                receipt,
                ReceiptTransactionMatchStatus.AMBIGUOUS,
                None,
                0.0,
                self._evidence("existing_link_workspace_conflict"),
            )
        if existing.status != TransactionStatus.REMOVED.value:
            if receipt.transaction_match_status == ReceiptTransactionMatchStatus.AUTO_MATCHED.value:
                self._sync_receipt_acquisitions(receipt, transaction_id=existing.id)
                self.db.flush()
                return self._decision_from_receipt(receipt)
            if receipt.transaction_match_status == ReceiptTransactionMatchStatus.AMBIGUOUS.value:
                return self._decision_from_receipt(receipt)
            confidence = max(
                float(receipt.transaction_match_confidence or 0.0),
                1.0,
            )
            return self._apply_decision(
                receipt,
                ReceiptTransactionMatchStatus.AUTO_MATCHED,
                existing,
                confidence,
                self._evidence(
                    "existing_link_preserved",
                    selected_transaction_id=existing.id,
                ),
            )
        replacement = self._active_replacement(existing, receipt.workspace_id)
        if replacement is not None:
            self.migrate_pending_replacement(existing, replacement)
            return self._decision_from_receipt(receipt)
        receipt.transaction_id = None
        self._sync_receipt_acquisitions(receipt, transaction_id=None)
        return self._apply_decision(
            receipt,
            ReceiptTransactionMatchStatus.NO_MATCH,
            None,
            0.0,
            self._evidence("linked_transaction_removed"),
        )

    def reconcile_for_transaction(
        self,
        transaction: ExpenseTransaction,
    ) -> list[ReceiptTransactionMatchDecision]:
        if transaction.id is None:
            raise ValueError("Transaction must be persisted before reconciliation.")
        if not self._transaction_is_eligible(transaction):
            return []
        transaction_dates = [
            value for value in (transaction.date, transaction.authorized_date) if value is not None
        ]
        if not transaction_dates:
            return []
        lower = datetime.combine(
            min(transaction_dates) - timedelta(days=_DATE_TOLERANCE_DAYS),
            time.min,
            UTC,
        )
        upper = datetime.combine(
            max(transaction_dates) + timedelta(days=_DATE_TOLERANCE_DAYS + 1),
            time.min,
            UTC,
        )
        currency = _normalize_currency(transaction.iso_currency_code)
        if not currency:
            return []
        receipts = list(
            self.db.scalars(
                select(PurchaseReceipt)
                .where(
                    PurchaseReceipt.workspace_id == transaction.workspace_id,
                    PurchaseReceipt.parse_status.not_in(
                        (
                            ReceiptParseStatus.FAILED.value,
                            ReceiptParseStatus.IGNORED.value,
                        )
                    ),
                    PurchaseReceipt.total_cents.between(
                        max(1, transaction.amount_cents - _AMOUNT_TOLERANCE_CENTS),
                        transaction.amount_cents + _AMOUNT_TOLERANCE_CENTS,
                    ),
                    func.upper(PurchaseReceipt.currency) == currency,
                    PurchaseReceipt.purchased_at >= lower,
                    PurchaseReceipt.purchased_at < upper,
                    PurchaseReceipt.merchant_normalized.is_not(None),
                    or_(
                        PurchaseReceipt.transaction_id.is_(None),
                        PurchaseReceipt.transaction_id == transaction.id,
                    ),
                )
                .order_by(PurchaseReceipt.purchased_at, PurchaseReceipt.id)
                .limit(_MAX_RECEIPTS_PER_TRANSACTION)
            )
        )
        return [
            self.reconcile_receipt(receipt, transaction_hint=transaction) for receipt in receipts
        ]

    def migrate_pending_replacement(
        self,
        prior: ExpenseTransaction,
        posted: ExpenseTransaction,
    ) -> PendingReplacementMigration:
        if prior.id is None or posted.id is None:
            raise ValueError("Both replacement transactions must be persisted.")
        if prior.workspace_id != posted.workspace_id:
            return PendingReplacementMigration(0, 0, 0)
        if not self._replacement_is_safe(prior, posted):
            return PendingReplacementMigration(0, 0, 0)
        self._acquire_match_slot(posted.id)

        receipts = list(
            self.db.scalars(
                select(PurchaseReceipt)
                .where(
                    PurchaseReceipt.workspace_id == prior.workspace_id,
                    PurchaseReceipt.transaction_id == prior.id,
                )
                .order_by(PurchaseReceipt.id)
                .with_for_update()
            )
        )
        acquisitions = list(
            self.db.scalars(
                select(HouseholdItemAcquisition)
                .where(
                    HouseholdItemAcquisition.workspace_id == prior.workspace_id,
                    HouseholdItemAcquisition.transaction_id == prior.id,
                )
                .order_by(HouseholdItemAcquisition.id)
                .with_for_update()
            )
        )
        target_receipt_ids = set(
            self.db.scalars(
                select(PurchaseReceipt.id).where(
                    PurchaseReceipt.workspace_id == posted.workspace_id,
                    PurchaseReceipt.transaction_id == posted.id,
                )
            )
        )
        migrated_receipt_ids: set[int] = set()
        ambiguous = 0
        for receipt in receipts:
            currency_matches = _normalize_currency(receipt.currency) == _normalize_currency(
                posted.iso_currency_code
            )
            collision = bool(target_receipt_ids and receipt.id not in target_receipt_ids)
            if not currency_matches or collision:
                ambiguous += 1
                self._apply_decision(
                    receipt,
                    ReceiptTransactionMatchStatus.AMBIGUOUS,
                    None,
                    0.0,
                    self._evidence(
                        "pending_replacement_conflict",
                        currency_matches=currency_matches,
                        target_already_linked=collision,
                    ),
                    preserve_link=True,
                )
                continue
            migrated_receipt_ids.add(receipt.id)
            self._apply_decision(
                receipt,
                ReceiptTransactionMatchStatus.AUTO_MATCHED,
                posted,
                max(float(receipt.transaction_match_confidence or 0.0), 0.99),
                self._evidence(
                    "plaid_pending_replacement",
                    prior_transaction_id=prior.id,
                    selected_transaction_id=posted.id,
                ),
            )
            target_receipt_ids.add(receipt.id)

        migrated_acquisitions = 0
        for acquisition in acquisitions:
            if acquisition.receipt_item_id is not None and receipts:
                receipt_id = self.db.scalar(
                    select(PurchaseReceiptItem.receipt_id).where(
                        PurchaseReceiptItem.id == acquisition.receipt_item_id
                    )
                )
                if receipt_id not in migrated_receipt_ids:
                    continue
            acquisition.transaction_id = posted.id
            migrated_acquisitions += 1

        self.db.flush()
        log_event(
            logger,
            "receipt_transaction_pending_replacement_migrated",
            prior_transaction_id=prior.id,
            posted_transaction_id=posted.id,
            receipt_count=len(migrated_receipt_ids),
            acquisition_count=migrated_acquisitions,
            ambiguous_receipt_count=ambiguous,
        )
        return PendingReplacementMigration(
            receipt_count=len(migrated_receipt_ids),
            acquisition_count=migrated_acquisitions,
            ambiguous_receipt_count=ambiguous,
        )

    def reconcile_removed_transaction(
        self,
        transaction: ExpenseTransaction,
    ) -> list[ReceiptTransactionMatchDecision]:
        if transaction.id is None or transaction.status != TransactionStatus.REMOVED.value:
            return []
        replacement = self._active_replacement(transaction, transaction.workspace_id)
        if replacement is not None:
            self.migrate_pending_replacement(transaction, replacement)
            return []

        receipts = list(
            self.db.scalars(
                select(PurchaseReceipt)
                .where(
                    PurchaseReceipt.workspace_id == transaction.workspace_id,
                    PurchaseReceipt.transaction_id == transaction.id,
                )
                .order_by(PurchaseReceipt.id)
            )
        )
        decisions: list[ReceiptTransactionMatchDecision] = []
        for receipt in receipts:
            receipt.transaction_id = None
            self._sync_receipt_acquisitions(receipt, transaction_id=None)
            decisions.append(self.reconcile_receipt(receipt))
        return decisions

    def _candidate_transactions(self, receipt: PurchaseReceipt) -> list[ExpenseTransaction]:
        purchased = _receipt_date(receipt)
        currency = _normalize_currency(receipt.currency)
        assert receipt.total_cents is not None and purchased is not None and currency
        return list(
            self.db.scalars(
                select(ExpenseTransaction)
                .where(
                    ExpenseTransaction.workspace_id == receipt.workspace_id,
                    ExpenseTransaction.amount_cents.between(
                        max(1, receipt.total_cents - _AMOUNT_TOLERANCE_CENTS),
                        receipt.total_cents + _AMOUNT_TOLERANCE_CENTS,
                    ),
                    func.upper(ExpenseTransaction.iso_currency_code) == currency,
                    ExpenseTransaction.status != TransactionStatus.REMOVED.value,
                    ExpenseTransaction.replaced_by_transaction_id.is_(None),
                    or_(
                        ExpenseTransaction.date.between(
                            purchased - timedelta(days=_DATE_TOLERANCE_DAYS),
                            purchased + timedelta(days=_DATE_TOLERANCE_DAYS),
                        ),
                        ExpenseTransaction.authorized_date.between(
                            purchased - timedelta(days=_DATE_TOLERANCE_DAYS),
                            purchased + timedelta(days=_DATE_TOLERANCE_DAYS),
                        ),
                    ),
                )
                .order_by(ExpenseTransaction.id)
                .limit(_MAX_TRANSACTIONS_PER_RECEIPT + 1)
            )
        )

    def _score(
        self,
        receipt: PurchaseReceipt,
        transaction: ExpenseTransaction,
    ) -> _ScoredCandidate | None:
        if not self._transaction_is_eligible(transaction):
            return None
        if _normalize_currency(receipt.currency) != _normalize_currency(
            transaction.iso_currency_code
        ):
            return None
        if receipt.total_cents is None:
            return None
        amount_delta = abs(receipt.total_cents - transaction.amount_cents)
        if amount_delta > _AMOUNT_TOLERANCE_CENTS:
            return None
        receipt_date = _receipt_date(receipt)
        date_match = _closest_transaction_date(receipt_date, transaction)
        if date_match is None:
            return None
        date_delta, used_authorized = date_match
        if date_delta > _DATE_TOLERANCE_DAYS:
            return None
        merchant_score = _merchant_similarity(
            receipt.merchant_normalized or receipt.merchant_raw,
            transaction.merchant_name or transaction.name,
        )
        if merchant_score < _MERCHANT_MINIMUM:
            return None
        amount_score = 1.0 - (amount_delta * 0.1)
        date_score = 1.0 - (date_delta * 0.1)
        confidence = merchant_score * 0.75 + amount_score * 0.15 + date_score * 0.10
        if transaction.pending:
            confidence -= _PENDING_PENALTY
        return _ScoredCandidate(
            transaction=transaction,
            confidence=round(max(0.0, min(confidence, 1.0)), 6),
            merchant_score=round(merchant_score, 6),
            amount_delta_cents=amount_delta,
            date_delta_days=date_delta,
            used_authorized_date=used_authorized,
        )

    def _load_receipt(self, receipt: PurchaseReceipt) -> PurchaseReceipt | None:
        return self.db.scalar(
            select(PurchaseReceipt).where(
                PurchaseReceipt.id == receipt.id,
                PurchaseReceipt.workspace_id == receipt.workspace_id,
            )
        )

    def _lock_receipt(self, receipt: PurchaseReceipt) -> PurchaseReceipt:
        locked = self.db.scalar(
            select(PurchaseReceipt)
            .where(
                PurchaseReceipt.id == receipt.id,
                PurchaseReceipt.workspace_id == receipt.workspace_id,
            )
            .with_for_update()
        )
        if locked is None:
            raise ValueError("Receipt is outside the active workspace.")
        return locked

    def _acquire_match_slot(self, transaction_id: int) -> None:
        bind = self.db.get_bind()
        if bind.dialect.name != "postgresql":
            return
        self.db.execute(
            select(func.pg_advisory_xact_lock(_ADVISORY_LOCK_NAMESPACE, transaction_id))
        )

    def _existing_link(self, receipt: PurchaseReceipt) -> ExpenseTransaction | None:
        if receipt.transaction_id is None:
            return None
        return self.db.scalar(
            select(ExpenseTransaction).where(ExpenseTransaction.id == receipt.transaction_id)
        )

    def _active_replacement(
        self,
        transaction: ExpenseTransaction,
        workspace_id: int,
    ) -> ExpenseTransaction | None:
        if transaction.replaced_by_transaction_id is None:
            return None
        return self.db.scalar(
            select(ExpenseTransaction).where(
                ExpenseTransaction.id == transaction.replaced_by_transaction_id,
                ExpenseTransaction.workspace_id == workspace_id,
                ExpenseTransaction.status != TransactionStatus.REMOVED.value,
            )
        )

    def _other_receipts_for_transaction(
        self,
        receipt: PurchaseReceipt,
        transaction_id: int,
    ) -> list[int]:
        return list(
            self.db.scalars(
                select(PurchaseReceipt.id)
                .where(
                    PurchaseReceipt.workspace_id == receipt.workspace_id,
                    PurchaseReceipt.transaction_id == transaction_id,
                    PurchaseReceipt.id != receipt.id,
                )
                .order_by(PurchaseReceipt.id)
            )
        )

    def _canonical_duplicate_receipt_id(
        self,
        receipt: PurchaseReceipt,
        occupied_receipt_ids: list[int],
    ) -> int | None:
        """Recognize only a duplicate representation with durable artifact identity.

        Amount/date/merchant/line equality is intentionally insufficient: two
        legitimate purchases can share all of those values. A non-null identical
        content hash is the only currently persisted strong identity allowed here.
        """

        if not receipt.content_sha256:
            return None
        candidates = list(
            self.db.scalars(
                select(PurchaseReceipt)
                .where(
                    PurchaseReceipt.workspace_id == receipt.workspace_id,
                    PurchaseReceipt.id.in_(occupied_receipt_ids),
                    PurchaseReceipt.parse_status.not_in(
                        (
                            ReceiptParseStatus.FAILED.value,
                            ReceiptParseStatus.IGNORED.value,
                        )
                    ),
                )
                .order_by(PurchaseReceipt.id)
            )
        )
        for candidate in candidates:
            if candidate.source == receipt.source:
                continue
            if self._same_artifact(candidate, receipt):
                return candidate.id
        return None

    def _unlinked_artifact_duplicates(
        self,
        receipt: PurchaseReceipt,
    ) -> list[PurchaseReceipt]:
        if not receipt.content_sha256:
            return []
        return list(
            self.db.scalars(
                select(PurchaseReceipt)
                .where(
                    PurchaseReceipt.workspace_id == receipt.workspace_id,
                    PurchaseReceipt.id != receipt.id,
                    PurchaseReceipt.transaction_id.is_(None),
                    PurchaseReceipt.source != receipt.source,
                    PurchaseReceipt.content_sha256 == receipt.content_sha256,
                    PurchaseReceipt.parse_status.not_in(
                        (
                            ReceiptParseStatus.FAILED.value,
                            ReceiptParseStatus.IGNORED.value,
                        )
                    ),
                )
                .order_by(PurchaseReceipt.id)
                .limit(_MAX_RECEIPTS_PER_TRANSACTION)
            )
        )

    @staticmethod
    def _same_artifact(left: PurchaseReceipt, right: PurchaseReceipt) -> bool:
        return bool(
            left.content_sha256
            and right.content_sha256
            and left.content_sha256 == right.content_sha256
        )

    def _receipt_semantic_signature(self, receipt: PurchaseReceipt) -> tuple | None:
        if (
            receipt.total_cents is None
            or receipt.purchased_at is None
        ):
            return None
        items = list(
            self.db.scalars(
                select(PurchaseReceiptItem)
                .where(PurchaseReceiptItem.receipt_id == receipt.id)
                .order_by(PurchaseReceiptItem.id)
            )
        )
        if not items:
            return None
        line_signature = tuple(
            (
                item.normalized_name.strip().casefold(),
                item.line_total_cents,
                round(float(item.quantity), 6) if item.quantity is not None else None,
                (item.unit or "").strip().casefold() or None,
            )
            for item in items
        )
        return (
            _normalize_reconciliation_merchant(
                receipt.merchant_normalized or receipt.merchant_raw
            ),
            _receipt_date(receipt),
            receipt.subtotal_cents,
            receipt.tax_cents,
            receipt.tip_cents,
            receipt.discount_cents,
            receipt.total_cents,
            _normalize_currency(receipt.currency),
            line_signature,
        )

    def _apply_decision(
        self,
        receipt: PurchaseReceipt,
        status: ReceiptTransactionMatchStatus,
        transaction: ExpenseTransaction | None,
        confidence: float,
        evidence: dict,
        *,
        preserve_link: bool = False,
    ) -> ReceiptTransactionMatchDecision:
        if status == ReceiptTransactionMatchStatus.AUTO_MATCHED and transaction is None:
            raise ValueError("An auto-matched decision requires a transaction.")
        normalized_confidence = round(max(0.0, min(confidence, 1.0)), 6)
        desired_transaction_id = None
        if status == ReceiptTransactionMatchStatus.AUTO_MATCHED:
            assert transaction is not None
            desired_transaction_id = transaction.id
        elif preserve_link:
            desired_transaction_id = receipt.transaction_id
        timestamps_consistent = (
            receipt.transaction_matched_at is not None
            if status == ReceiptTransactionMatchStatus.AUTO_MATCHED
            else receipt.transaction_matched_at is None
        )
        if (
            receipt.transaction_match_status == status.value
            and receipt.transaction_id == desired_transaction_id
            and float(receipt.transaction_match_confidence or 0.0) == normalized_confidence
            and dict(receipt.transaction_match_evidence_json or {}) == evidence
            and receipt.transaction_match_attempted_at is not None
            and timestamps_consistent
        ):
            if transaction is not None:
                self._sync_receipt_acquisitions(receipt, transaction_id=transaction.id)
                self.db.flush()
                self._consolidate_artifact_duplicate_acquisitions(
                    receipt,
                    transaction_id=transaction.id,
                )
            return self._decision_from_receipt(receipt)

        now = utc_now()
        # Set the complete decision tuple before any helper query can autoflush;
        # the database check constraint deliberately rejects half-applied links.
        receipt.transaction_match_status = status.value
        receipt.transaction_match_confidence = normalized_confidence
        receipt.transaction_match_evidence_json = evidence
        receipt.transaction_match_attempted_at = now
        receipt.updated_at = now
        if status == ReceiptTransactionMatchStatus.AUTO_MATCHED:
            assert transaction is not None
            link_changed = receipt.transaction_id != transaction.id
            receipt.transaction_id = transaction.id
            if link_changed or receipt.transaction_matched_at is None:
                receipt.transaction_matched_at = now
            self._sync_receipt_acquisitions(receipt, transaction_id=transaction.id)
            self.db.flush()
            self._consolidate_artifact_duplicate_acquisitions(
                receipt,
                transaction_id=transaction.id,
            )
        else:
            receipt.transaction_matched_at = None
            if not preserve_link:
                receipt.transaction_id = None
                self._sync_receipt_acquisitions(receipt, transaction_id=None)
        self.db.flush()
        log_event(
            logger,
            "receipt_transaction_reconciled",
            receipt_id=receipt.id,
            status=status.value,
            transaction_id=transaction.id if transaction is not None else None,
            confidence=receipt.transaction_match_confidence,
            reason=evidence.get("reason"),
        )
        return self._decision_from_receipt(receipt)

    def _decision_from_receipt(
        self,
        receipt: PurchaseReceipt,
    ) -> ReceiptTransactionMatchDecision:
        return ReceiptTransactionMatchDecision(
            receipt_id=receipt.id,
            status=ReceiptTransactionMatchStatus(receipt.transaction_match_status),
            transaction_id=receipt.transaction_id,
            confidence=float(receipt.transaction_match_confidence or 0.0),
            evidence=dict(receipt.transaction_match_evidence_json or {}),
        )

    def _sync_receipt_acquisitions(
        self,
        receipt: PurchaseReceipt,
        *,
        transaction_id: int | None,
    ) -> None:
        line_ids = list(
            self.db.scalars(
                select(PurchaseReceiptItem.id).where(PurchaseReceiptItem.receipt_id == receipt.id)
            )
        )
        if not line_ids:
            return
        acquisitions = self.db.scalars(
            select(HouseholdItemAcquisition).where(
                HouseholdItemAcquisition.workspace_id == receipt.workspace_id,
                HouseholdItemAcquisition.receipt_item_id.in_(line_ids),
            )
        )
        for acquisition in acquisitions:
            acquisition.transaction_id = transaction_id

    def _consolidate_artifact_duplicate_acquisitions(
        self,
        receipt: PurchaseReceipt,
        *,
        transaction_id: int,
    ) -> None:
        """Void learning duplicated only by proven same-artifact representations.

        The durable artifact hash proves purchase identity. The semantic signature
        is used only to map equal parsed line ordinals; it never establishes identity.
        """

        if not receipt.content_sha256:
            return
        signature = self._receipt_semantic_signature(receipt)
        if signature is None:
            return
        self._acquire_match_slot(transaction_id)
        linked_receipts = list(
            self.db.scalars(
                select(PurchaseReceipt)
                .where(
                    PurchaseReceipt.workspace_id == receipt.workspace_id,
                    PurchaseReceipt.transaction_id == transaction_id,
                    PurchaseReceipt.content_sha256 == receipt.content_sha256,
                    PurchaseReceipt.parse_status.not_in(
                        (
                            ReceiptParseStatus.FAILED.value,
                            ReceiptParseStatus.IGNORED.value,
                        )
                    ),
                )
                .order_by(PurchaseReceipt.id)
                .with_for_update()
            )
        )
        duplicate_receipts = [
            candidate
            for candidate in linked_receipts
            if self._same_artifact(candidate, receipt)
            and self._receipt_semantic_signature(candidate) == signature
        ]
        if len(duplicate_receipts) < 2 or len({row.source for row in duplicate_receipts}) < 2:
            return

        receipt_ids = [row.id for row in duplicate_receipts]
        line_rows = list(
            self.db.scalars(
                select(PurchaseReceiptItem)
                .where(PurchaseReceiptItem.receipt_id.in_(receipt_ids))
                .order_by(PurchaseReceiptItem.receipt_id, PurchaseReceiptItem.id)
                .with_for_update()
            )
        )
        lines_by_receipt: dict[int, list[PurchaseReceiptItem]] = {
            receipt_id: [] for receipt_id in receipt_ids
        }
        for line in line_rows:
            lines_by_receipt[line.receipt_id].append(line)
        expected_line_count = len(lines_by_receipt[receipt_ids[0]])
        if expected_line_count == 0 or any(
            len(lines_by_receipt[receipt_id]) != expected_line_count
            for receipt_id in receipt_ids
        ):
            return

        line_ids = [line.id for line in line_rows]
        # Acquisition mutations use HouseholdItem -> Acquisition ordering. Read
        # the candidate item IDs first, lock those items in deterministic order,
        # and only then lock/recheck the acquisition rows. Never acquire a newly
        # discovered item lock after holding an acquisition lock.
        candidate_item_ids = sorted(
            set(
                self.db.scalars(
                    select(HouseholdItemAcquisition.household_item_id).where(
                        HouseholdItemAcquisition.workspace_id == receipt.workspace_id,
                        HouseholdItemAcquisition.receipt_item_id.in_(line_ids),
                        HouseholdItemAcquisition.transaction_id == transaction_id,
                        HouseholdItemAcquisition.voided_at.is_(None),
                    )
                )
            )
        )
        if not candidate_item_ids:
            return
        locked_item_ids = set(
            self.db.scalars(
                select(HouseholdItem.id)
                .where(
                    HouseholdItem.workspace_id == receipt.workspace_id,
                    HouseholdItem.id.in_(candidate_item_ids),
                )
                .order_by(HouseholdItem.id)
                .with_for_update(of=HouseholdItem)
            )
        )
        if locked_item_ids != set(candidate_item_ids):
            return
        acquisitions = list(
            self.db.scalars(
                select(HouseholdItemAcquisition)
                .where(
                    HouseholdItemAcquisition.workspace_id == receipt.workspace_id,
                    HouseholdItemAcquisition.receipt_item_id.in_(line_ids),
                    HouseholdItemAcquisition.transaction_id == transaction_id,
                    HouseholdItemAcquisition.voided_at.is_(None),
                    HouseholdItemAcquisition.household_item_id.in_(candidate_item_ids),
                )
                .order_by(
                    HouseholdItemAcquisition.household_item_id,
                    HouseholdItemAcquisition.id,
                )
                .with_for_update()
            )
        )
        if {row.household_item_id for row in acquisitions} - locked_item_ids:
            # A concurrent correction moved a row after the optimistic pre-read.
            # Fail closed and let the next deterministic reconciliation retry.
            return
        by_line: dict[int, list[HouseholdItemAcquisition]] = {}
        for acquisition in acquisitions:
            assert acquisition.receipt_item_id is not None
            by_line.setdefault(acquisition.receipt_item_id, []).append(acquisition)

        voided_count = 0
        affected_item_ids: set[int] = set()
        now = utc_now()
        for ordinal in range(expected_line_count):
            ordinal_acquisitions: list[HouseholdItemAcquisition] = []
            valid_group = True
            for receipt_id in receipt_ids:
                line = lines_by_receipt[receipt_id][ordinal]
                active = by_line.get(line.id, [])
                # An unexpected duplicate on one source line is not evidence
                # about cross-channel identity, so fail this ordinal closed.
                if len(active) > 1:
                    valid_group = False
                    break
                ordinal_acquisitions.extend(active)
            if not valid_group or len(ordinal_acquisitions) < 2:
                continue
            by_item: dict[int, list[HouseholdItemAcquisition]] = {}
            for acquisition in ordinal_acquisitions:
                by_item.setdefault(acquisition.household_item_id, []).append(acquisition)
            for item_id, item_acquisitions in by_item.items():
                if len(item_acquisitions) < 2:
                    continue
                survivor = min(
                    item_acquisitions,
                    key=lambda row: (
                        not row.user_confirmed,
                        not row.confirmed,
                        -float(row.confidence or 0.0),
                        row.id,
                    ),
                )
                for redundant in item_acquisitions:
                    if redundant.id == survivor.id:
                        continue
                    survivor.confirmed = survivor.confirmed or redundant.confirmed
                    survivor.confidence = max(
                        float(survivor.confidence or 0.0),
                        float(redundant.confidence or 0.0),
                    )
                    if redundant.user_confirmed:
                        survivor.user_confirmed = True
                        survivor.source = redundant.source
                    redundant.voided_at = now
                    AcquisitionService._release_logical_key(redundant)
                    affected_item_ids.add(item_id)
                    voided_count += 1

        if not voided_count:
            return
        self.db.flush()
        acquisition_service = AcquisitionService(self.db)
        for item_id in sorted(affected_item_ids):
            acquisition_service._sync_item_last_acquired(item_id)
            acquisition_service._sync_learning_cadence(item_id, repair=True)
            acquisition_service.rebuild_prediction_outcomes(item_id)
            item = self.db.get(HouseholdItem, item_id)
            if item is not None:
                acquisition_service.refresh_basic_prediction(item, commit=False)
        self.db.flush()
        log_event(
            logger,
            "receipt_duplicate_acquisitions_consolidated",
            transaction_id=transaction_id,
            receipt_count=len(receipt_ids),
            acquisition_count=voided_count,
            household_item_count=len(affected_item_ids),
        )

    @staticmethod
    def _insufficient_reason(receipt: PurchaseReceipt) -> str | None:
        if receipt.total_cents is None or receipt.total_cents <= 0:
            return "missing_or_non_purchase_total"
        if _receipt_date(receipt) is None:
            return "missing_purchase_date"
        if not _normalize_currency(receipt.currency):
            return "missing_currency"
        if not _normalize_reconciliation_merchant(
            receipt.merchant_normalized or receipt.merchant_raw
        ):
            return "missing_merchant"
        return None

    @staticmethod
    def _transaction_is_eligible(transaction: ExpenseTransaction) -> bool:
        return bool(
            transaction.amount_cents > 0
            and _normalize_currency(transaction.iso_currency_code)
            and (transaction.date is not None or transaction.authorized_date is not None)
            and transaction.status != TransactionStatus.REMOVED.value
            and transaction.replaced_by_transaction_id is None
        )

    @staticmethod
    def _replacement_is_safe(
        prior: ExpenseTransaction,
        posted: ExpenseTransaction,
    ) -> bool:
        return bool(
            prior.amount_cents > 0
            and posted.amount_cents > 0
            and _normalize_currency(prior.iso_currency_code)
            == _normalize_currency(posted.iso_currency_code)
            and posted.status != TransactionStatus.REMOVED.value
        )

    @staticmethod
    def _candidate_evidence(candidate: _ScoredCandidate) -> dict:
        return {
            "transaction_id": candidate.transaction.id,
            "score": candidate.confidence,
            "merchant_score": candidate.merchant_score,
            "amount_delta_cents": candidate.amount_delta_cents,
            "date_delta_days": candidate.date_delta_days,
            "date_source": "authorized_date" if candidate.used_authorized_date else "date",
            "pending": bool(candidate.transaction.pending),
        }

    @staticmethod
    def _evidence(reason: str, **details) -> dict:
        return {
            "policy_version": _POLICY_VERSION,
            "reason": reason,
            "policy": {
                "workspace": "exact",
                "currency": "exact",
                "purchase_sign": "positive",
                "amount_tolerance_cents": _AMOUNT_TOLERANCE_CENTS,
                "date_tolerance_days": _DATE_TOLERANCE_DAYS,
                "merchant_minimum": _MERCHANT_MINIMUM,
                "near_tie_margin": _NEAR_TIE_MARGIN,
                "posted_preferred": True,
            },
            **details,
        }


def _normalize_currency(value: str | None) -> str:
    normalized = (value or "").strip().upper()
    return normalized if re.fullmatch(r"[A-Z]{3}", normalized) else ""


def _receipt_date(receipt: PurchaseReceipt) -> date | None:
    if receipt.purchased_at is None:
        return None
    if isinstance(receipt.purchased_at, datetime):
        return receipt.purchased_at.date()
    return receipt.purchased_at


def _closest_transaction_date(
    receipt_date: date | None,
    transaction: ExpenseTransaction,
) -> tuple[int, bool] | None:
    if receipt_date is None:
        return None
    candidates: list[tuple[int, bool]] = []
    if transaction.date is not None:
        candidates.append((abs((transaction.date - receipt_date).days), False))
    if transaction.authorized_date is not None:
        candidates.append((abs((transaction.authorized_date - receipt_date).days), True))
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: (candidate[0], candidate[1]))


def _normalize_reconciliation_merchant(value: str | None) -> str:
    tokens = re.findall(r"[a-z0-9]+", (value or "").casefold())
    meaningful = [
        token
        for token in tokens
        if token not in _MERCHANT_NOISE and token != "s" and not token.isdigit()
    ]
    return " ".join(meaningful)


def _merchant_similarity(left: str | None, right: str | None) -> float:
    normalized_left = _normalize_reconciliation_merchant(left)
    normalized_right = _normalize_reconciliation_merchant(right)
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left == normalized_right:
        return 1.0
    left_tokens = set(normalized_left.split())
    right_tokens = set(normalized_right.split())
    intersection = left_tokens & right_tokens
    union = left_tokens | right_tokens
    jaccard = len(intersection) / len(union) if union else 0.0
    containment = (
        len(intersection) / min(len(left_tokens), len(right_tokens))
        if left_tokens and right_tokens
        else 0.0
    )
    sequence = SequenceMatcher(None, normalized_left, normalized_right).ratio()
    if containment == 1.0:
        containment = 0.96
    return max(sequence, jaccard, containment)

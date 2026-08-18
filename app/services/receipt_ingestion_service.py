from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Lock

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.config import Settings, get_settings
from app.logging_config import log_event
from app.models import (
    ClassificationAuthority,
    ClassificationConcept,
    ClassificationDecisionState,
    DataConsent,
    HouseholdCadenceSource,
    HouseholdItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReceiptItemMatchStatus,
    ReceiptParseStatus,
    User,
    WorkspaceMembership,
    utc_now,
)
from app.services.acquisition_service import AcquisitionService
from app.services.autonomous_classification_service import AutonomousClassificationService
from app.services.classification_taxonomy_service import (
    ClassificationTaxonomyError,
    ClassificationTaxonomyService,
)
from app.services.item_normalization_service import (
    ItemNormalizationService,
    normalize_item_name,
    normalize_merchant,
)
from app.services.managed_auth_service import record_audit_once
from app.services.quantity_normalization_service import normalize_quantity
from app.services.receipt_artifact_service import ReceiptArtifactError, build_receipt_artifact
from app.services.receipt_learning_service import (
    TRACKABLE_CLASSIFICATIONS,
    classify_receipt_line,
    safe_learning_metrics,
)
from app.services.receipt_parser_service import (
    ParsedReceipt,
    ReceiptParser,
    ReceiptParserError,
    assess_parsed_receipt,
    build_receipt_parser,
)
from app.services.receipt_transaction_reconciliation_service import (
    ReceiptTransactionMatchStatus,
    ReceiptTransactionReconciliationService,
)

logger = logging.getLogger(__name__)

_RECEIPT_FINGERPRINT_LOCK_NAMESPACE = 1_165_528_402


@dataclass
class _FingerprintLockEntry:
    lock: Lock = field(default_factory=Lock)
    users: int = 0


_fingerprint_lock_guard = Lock()
_fingerprint_locks: dict[str, _FingerprintLockEntry] = {}

_REPROCESSABLE_RECEIPT_FAILURE_CODES = frozenset(
    {
        "receipt_parser_not_configured",
        "receipt_provider_timeout",
        "receipt_provider_rate_limited",
        "receipt_provider_unavailable",
        "receipt_provider_rejected",
        "receipt_parse_failed",
        "receipt_parse_empty_response",
        "receipt_schema_invalid",
    }
)


class ReceiptIngestionService:
    def __init__(
        self,
        db: Session,
        settings: Settings | None = None,
        parser: ReceiptParser | None = None,
        *,
        owner_user_id: int | None = None,
    ):
        self.db = db
        self.settings = settings or get_settings()
        self._parser_requires_stored_consent = bool(
            parser is None and self.settings.receipt_parser_provider == "openai"
        )
        self.parser = parser or build_receipt_parser(self.settings)
        self.owner_user_id = (
            owner_user_id
            if isinstance(owner_user_id, int) and owner_user_id > 0
            else _active_owner_user_id(db)
        )

    def ingest_attachment(
        self,
        *,
        source: str,
        source_external_id: str,
        content: bytes,
        mime_type: str,
        filename: str,
        auto_confirm_high_confidence: bool = False,
    ) -> PurchaseReceipt:
        started = time.monotonic()
        fingerprint = hashlib.sha256(content).hexdigest()
        existing = self._existing(
            source,
            source_external_id,
            fingerprint,
            reprocess_transient_failure=True,
        )
        if existing:
            return existing
        self._ensure_model_receipt_consent()
        try:
            artifact = build_receipt_artifact(
                source=source,
                source_external_id=source_external_id,
                content=content,
                mime_type=mime_type,
                filename=filename,
                max_bytes=self.settings.receipt_max_attachment_bytes,
            )
        except ReceiptArtifactError as exc:
            self._log_parse_result(
                "receipt_image_parse_failed",
                source=source,
                media_class="image" if mime_type.startswith("image/") else "unknown",
                started=started,
                failure_code=str(exc),
            )
            return self._failed_receipt(source, source_external_id, fingerprint, str(exc))
        log_event(
            logger,
            "receipt_image_received",
            source=source,
            media_class=artifact.media_class,
            size_bytes=artifact.size_bytes,
            orientation_corrected=artifact.orientation_corrected,
            width=artifact.width,
            height=artifact.height,
        )
        try:
            parse_artifact = getattr(self.parser, "parse_artifact", None)
            if callable(parse_artifact):
                parsed = parse_artifact(artifact)
            else:
                parsed = self.parser.parse_attachment(
                    artifact.normalized_content,
                    artifact.media_type,
                    artifact.filename,
                )
        except ReceiptParserError as exc:
            self._log_parse_result(
                "receipt_image_parse_failed",
                source=source,
                media_class=artifact.media_class,
                started=started,
                failure_code=exc.code,
                normalization_latency_ms=artifact.normalization_latency_ms,
            )
            return self._failed_receipt(source, source_external_id, fingerprint, str(exc))
        assessment = assess_parsed_receipt(parsed)
        if assessment.quality in {"unusable", "non_receipt"}:
            event = (
                "receipt_non_receipt_detected"
                if assessment.quality == "non_receipt"
                else "receipt_image_parse_failed"
            )
            self._log_parse_result(
                event,
                source=source,
                media_class=artifact.media_class,
                started=started,
                failure_code=assessment.failure_code,
                normalization_latency_ms=artifact.normalization_latency_ms,
            )
            return self._failed_receipt(
                source,
                source_external_id,
                fingerprint,
                assessment.failure_code or "receipt_image_unreadable",
            )
        self._ensure_scope_still_active()
        receipt = self._persist_serialized(
            source,
            source_external_id,
            fingerprint,
            parsed,
            failure_code=assessment.failure_code,
            auto_confirm_high_confidence=auto_confirm_high_confidence,
        )
        self._log_parse_result(
            "receipt_image_parse_partial"
            if assessment.quality == "partial"
            else "receipt_image_parse_success",
            source=source,
            media_class=artifact.media_class,
            started=started,
            failure_code=assessment.failure_code,
            normalization_latency_ms=artifact.normalization_latency_ms,
            receipt=receipt,
        )
        return receipt

    def ingest_text(
        self,
        *,
        source: str,
        source_external_id: str,
        text: str,
        auto_confirm_high_confidence: bool = False,
    ) -> PurchaseReceipt:
        fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing = self._existing(
            source,
            source_external_id,
            fingerprint,
            reprocess_transient_failure=True,
        )
        if existing:
            return existing
        self._ensure_model_receipt_consent()
        try:
            parsed = self.parser.parse_text(text)
        except ReceiptParserError as exc:
            return self._failed_receipt(source, source_external_id, fingerprint, str(exc))
        assessment = assess_parsed_receipt(parsed)
        if assessment.quality in {"unusable", "non_receipt"}:
            return self._failed_receipt(
                source,
                source_external_id,
                fingerprint,
                assessment.failure_code or "receipt_text_unreadable",
            )
        self._ensure_scope_still_active()
        return self._persist_serialized(
            source,
            source_external_id,
            fingerprint,
            parsed,
            failure_code=assessment.failure_code,
            auto_confirm_high_confidence=auto_confirm_high_confidence,
        )

    def _persist_serialized(
        self,
        source: str,
        external_id: str,
        fingerprint: str,
        parsed: ParsedReceipt,
        *,
        failure_code: str | None,
        auto_confirm_high_confidence: bool,
    ) -> PurchaseReceipt:
        """Claim one successful receipt representation per artifact/workspace.

        The provider call intentionally happens before this short critical
        section. PostgreSQL's transaction-scoped advisory lock serializes
        workers across processes; the bounded in-process lock gives SQLite
        fixtures the same behavior. A second worker always rechecks durable
        state after acquiring the claim and returns the canonical receipt.
        """

        with self._fingerprint_ingestion_lock(fingerprint):
            existing = self._existing(
                source,
                external_id,
                fingerprint,
                reprocess_transient_failure=True,
            )
            if existing is not None:
                return existing
            return self._persist(
                source,
                external_id,
                fingerprint,
                parsed,
                failure_code=failure_code,
                auto_confirm_high_confidence=auto_confirm_high_confidence,
            )

    @contextmanager
    def _fingerprint_ingestion_lock(self, fingerprint: str) -> Iterator[None]:
        workspace_id = self.db.info.get("workspace_id")
        if not isinstance(workspace_id, int) or workspace_id <= 0:
            raise ValueError("A trusted workspace is required for receipt ingestion.")
        lock_key = f"{workspace_id}:{fingerprint}"
        with _fingerprint_lock_guard:
            entry = _fingerprint_locks.setdefault(lock_key, _FingerprintLockEntry())
            entry.users += 1
        entry.lock.acquire()
        try:
            if self.db.get_bind().dialect.name == "postgresql":
                digest = hashlib.sha256(lock_key.encode("utf-8")).digest()
                signed_key = int.from_bytes(digest[:4], "big", signed=True)
                self.db.execute(
                    select(
                        func.pg_advisory_xact_lock(
                            _RECEIPT_FINGERPRINT_LOCK_NAMESPACE,
                            signed_key,
                        )
                    )
                )
            yield
        finally:
            entry.lock.release()
            with _fingerprint_lock_guard:
                entry.users -= 1
                if entry.users == 0:
                    _fingerprint_locks.pop(lock_key, None)

    def get(self, receipt_id: int) -> PurchaseReceipt:
        receipt = self.db.execute(
            select(PurchaseReceipt)
            .options(selectinload(PurchaseReceipt.items))
            .where(PurchaseReceipt.id == receipt_id)
        ).scalar_one_or_none()
        if receipt is None:
            raise ValueError("Receipt not found.")
        return receipt

    def confirm(
        self,
        receipt_id: int,
        *,
        user_confirmed: bool = True,
        acknowledge_undecided: bool = False,
        commit: bool = True,
    ) -> PurchaseReceipt:
        receipt = self.get(receipt_id)
        if receipt.parse_status == ReceiptParseStatus.IGNORED.value:
            raise ValueError("Ignored receipt cannot be confirmed.")
        undecided = sum(
            line.match_status
            not in {
                ReceiptItemMatchStatus.MATCHED.value,
                ReceiptItemMatchStatus.REJECTED.value,
                ReceiptItemMatchStatus.IRRELEVANT.value,
            }
            for line in receipt.items
        )
        if undecided and not acknowledge_undecided:
            raise ValueError("Acknowledge undecided receipt lines before confirming.")
        for line in receipt.items:
            if (
                line.household_item_id is not None
                and line.match_status == ReceiptItemMatchStatus.MATCHED.value
            ):
                if not user_confirmed and not _automatic_acquisition_safe(receipt, line):
                    continue
                household_item = self.db.scalar(
                    select(HouseholdItem).where(
                        HouseholdItem.workspace_id == receipt.workspace_id,
                        HouseholdItem.id == line.household_item_id,
                        HouseholdItem.enabled.is_(True),
                    )
                )
                if household_item is None:
                    raise ValueError("Matched household item no longer exists.")
                AcquisitionService(self.db).record(
                    household_item,
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
                    source=f"receipt_{receipt.source}",
                    confidence=line.match_confidence or 0.0,
                    confirmed=True,
                    user_confirmed=user_confirmed,
                    commit=False,
                )
                ItemNormalizationService(self.db).learn_alias(
                    household_item,
                    line.raw_name,
                    merchant=receipt.merchant_normalized,
                    source="confirmed_receipt",
                    confidence=line.match_confidence or 1.0,
                )
        taxonomy = ClassificationTaxonomyService(self.db)
        for line in receipt.items:
            if line.classification_concept_id is None:
                continue
            if not user_confirmed and not _automatic_acquisition_safe(receipt, line):
                continue
            concept = self.db.scalar(
                select(ClassificationConcept).where(
                    ClassificationConcept.workspace_id == receipt.workspace_id,
                    ClassificationConcept.id == line.classification_concept_id,
                )
            )
            if concept is None:
                continue
            try:
                with self.db.begin_nested():
                    taxonomy.learn_alias(
                        concept,
                        line.raw_name,
                        merchant=receipt.merchant_normalized or receipt.merchant_raw,
                        confidence=1.0,
                        source=ClassificationAuthority.CONFIRMED_ALIAS,
                    )
            except ClassificationTaxonomyError as exc:
                log_event(
                    logger,
                    "confirmed_receipt_classification_alias_skipped",
                    error_type=type(exc).__name__,
                )
        receipt.parse_status = ReceiptParseStatus.CONFIRMED.value
        receipt.confirmed_at = utc_now()
        receipt.updated_at = utc_now()
        self._sync_review_items(receipt)
        if commit:
            self.db.commit()
            return self.get(receipt.id)
        self.db.flush()
        return receipt

    def apply_decisions(
        self,
        receipt_id: int,
        *,
        decisions: list[dict],
        expected_updated_at: datetime,
        confirm: bool,
        acknowledge_undecided: bool,
    ) -> PurchaseReceipt:
        receipt = self.get(receipt_id)
        if receipt.parse_status != ReceiptParseStatus.NEEDS_REVIEW.value:
            raise ValueError("Only a receipt needing review can accept decisions.")
        if _aware(receipt.updated_at) != _aware(expected_updated_at):
            raise ValueError("receipt_changed_refresh_required")
        line_ids = [int(decision.get("line_id") or 0) for decision in decisions]
        if len(line_ids) != len(set(line_ids)):
            raise ValueError("Each receipt line can be decided only once.")
        known_line_ids = {line.id for line in receipt.items}
        if any(line_id not in known_line_ids for line_id in line_ids):
            raise ValueError("Receipt line not found.")
        try:
            for decision in decisions:
                line_id = int(decision["line_id"])
                action = str(decision["decision"])
                if action == "match":
                    household_item_id = decision.get("household_item_id")
                    if household_item_id is None:
                        raise ValueError("A household item is required for a match decision.")
                    self.update_line_match(
                        receipt_id,
                        line_id,
                        int(household_item_id),
                        commit=False,
                    )
                elif action == "unmatched":
                    self.update_line_match(receipt_id, line_id, None, commit=False)
                elif action == "reject":
                    self.update_line_match(
                        receipt_id,
                        line_id,
                        None,
                        rejected=True,
                        commit=False,
                    )
                elif action == "create":
                    self.track_line_as_new_household_item(
                        receipt_id,
                        line_id,
                        name=str(decision.get("name") or ""),
                        cadence_days=(
                            int(decision["cadence_days"])
                            if decision.get("cadence_days") is not None
                            else None
                        ),
                        replenishment_mode=str(decision.get("replenishment_mode") or "either"),
                        commit=False,
                    )
                else:
                    raise ValueError("Unsupported receipt decision.")
            self.db.flush()
            receipt.updated_at = utc_now()
            if confirm:
                undecided = sum(
                    line.match_status
                    not in {
                        ReceiptItemMatchStatus.MATCHED.value,
                        ReceiptItemMatchStatus.REJECTED.value,
                        ReceiptItemMatchStatus.IRRELEVANT.value,
                    }
                    for line in receipt.items
                )
                if undecided and not acknowledge_undecided:
                    raise ValueError("Acknowledge undecided receipt lines before confirming.")
                self.confirm(
                    receipt.id,
                    acknowledge_undecided=acknowledge_undecided,
                    commit=False,
                )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        applied = self.get(receipt.id)
        from app.logging_config import log_event

        log_event(
            logger,
            "receipt_learning_batch_applied",
            receipt_lines_processed=len(applied.items),
            candidates_accepted=sum(
                str(decision.get("decision")) == "create" for decision in decisions
            ),
            accepted_existing_matches=sum(
                str(decision.get("decision")) == "match" for decision in decisions
            ),
            candidates_rejected=sum(
                str(decision.get("decision")) == "reject" for decision in decisions
            ),
            manual_corrections=sum(
                str(decision.get("decision")) in {"match", "unmatched"} for decision in decisions
            ),
        )
        return applied

    def ignore(self, receipt_id: int) -> PurchaseReceipt:
        receipt = self.get(receipt_id)
        linked_transaction_id = receipt.transaction_id
        receipt.parse_status = ReceiptParseStatus.IGNORED.value
        receipt.ignored_at = utc_now()
        receipt.updated_at = utc_now()
        autonomous = AutonomousClassificationService(
            self.db,
            self.settings,
        )
        autonomous.revert_ignored_receipt_learning(receipt.id, commit=False)
        ReceiptTransactionReconciliationService(self.db).reconcile_receipt(receipt)
        if linked_transaction_id is not None:
            autonomous.recompute_transaction_from_receipt_state(
                linked_transaction_id,
                commit=False,
            )
        self._sync_review_items(receipt)
        self.db.commit()
        return self.get(receipt.id)

    def restore(self, receipt_id: int) -> PurchaseReceipt:
        receipt = self.get(receipt_id)
        if receipt.parse_status != ReceiptParseStatus.IGNORED.value:
            raise ValueError("Only an ignored receipt can be restored.")
        receipt.parse_status = ReceiptParseStatus.NEEDS_REVIEW.value
        receipt.ignored_at = None
        receipt.updated_at = utc_now()
        ReceiptTransactionReconciliationService(self.db).reconcile_receipt(receipt)
        if self.settings.autonomous_classification_enabled:
            self._classify_receipt_nonblocking(receipt)
        self._sync_review_items(receipt)
        self.db.commit()
        return self.get(receipt.id)

    def update_line_match(
        self,
        receipt_id: int,
        line_id: int,
        household_item_id: int | None,
        *,
        rejected: bool = False,
        commit: bool = True,
    ) -> PurchaseReceiptItem:
        receipt = self.get(receipt_id)
        line = next((item for item in receipt.items if item.id == line_id), None)
        if line is None:
            raise ValueError("Receipt line not found.")
        old_item = line.household_item
        active_acquisition = line.acquisition
        normalizer = ItemNormalizationService(self.db)
        if rejected:
            if active_acquisition and active_acquisition.voided_at is None:
                AcquisitionService(self.db).undo(active_acquisition.id, commit=False)
            if old_item:
                normalizer.void_alias(old_item, line.raw_name, merchant=receipt.merchant_normalized)
            line.household_item_id = None
            line.match_status = ReceiptItemMatchStatus.REJECTED.value
            line.match_confidence = 1.0
        elif household_item_id is None:
            if active_acquisition and active_acquisition.voided_at is None:
                AcquisitionService(self.db).undo(active_acquisition.id, commit=False)
            if old_item:
                normalizer.void_alias(old_item, line.raw_name, merchant=receipt.merchant_normalized)
            line.household_item_id = None
            line.match_status = ReceiptItemMatchStatus.UNMATCHED.value
            line.match_confidence = None
        else:
            item = self.db.scalar(
                select(HouseholdItem).where(
                    HouseholdItem.workspace_id == receipt.workspace_id,
                    HouseholdItem.id == household_item_id,
                )
            )
            if item is None:
                raise ValueError("Household item not found.")
            if active_acquisition and active_acquisition.voided_at is None:
                if active_acquisition.household_item_id != item.id:
                    AcquisitionService(self.db).correct(
                        active_acquisition.id, household_item=item, commit=False
                    )
            elif receipt.parse_status == ReceiptParseStatus.CONFIRMED.value:
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
                    source="correction",
                    confidence=1.0,
                    confirmed=True,
                    user_confirmed=True,
                    commit=False,
                )
            if old_item and old_item.id != item.id:
                normalizer.void_alias(old_item, line.raw_name, merchant=receipt.merchant_normalized)
            normalizer.learn_alias(
                item,
                line.raw_name,
                merchant=receipt.merchant_normalized,
                source="user_correction",
                confidence=1.0,
            )
            line.household_item_id = item.id
            line.match_status = ReceiptItemMatchStatus.MATCHED.value
            line.match_confidence = 1.0
        line.updated_at = utc_now()
        receipt.updated_at = utc_now()
        if commit:
            self.db.commit()
            self.db.refresh(line)
        else:
            self.db.flush()
        return line

    def track_line_as_new_household_item(
        self,
        receipt_id: int,
        line_id: int,
        *,
        name: str,
        cadence_days: int | None,
        replenishment_mode: str = "either",
        commit: bool = True,
    ) -> tuple[HouseholdItem, PurchaseReceiptItem]:
        receipt = self.get(receipt_id)
        line = next((item for item in receipt.items if item.id == line_id), None)
        if line is None:
            raise ValueError("Receipt line not found.")
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Household item name is required.")
        item = HouseholdItem(
            name=clean_name,
            quantity=f"{line.quantity:g}" if line.quantity is not None else None,
            unit=line.unit,
            cadence_days=cadence_days,
            cadence_source=(
                HouseholdCadenceSource.CONFIGURED.value
                if cadence_days is not None
                else HouseholdCadenceSource.LEARNING.value
            ),
            replenishment_mode=replenishment_mode,
            enabled=True,
        )
        self.db.add(item)
        self.db.flush()
        updated_line = self.update_line_match(
            receipt.id,
            line.id,
            item.id,
            commit=commit,
        )
        self.db.refresh(item)
        return item, updated_line

    def _persist(
        self,
        source: str,
        external_id: str,
        fingerprint: str,
        parsed: ParsedReceipt,
        *,
        failure_code: str | None,
        auto_confirm_high_confidence: bool,
    ) -> PurchaseReceipt:
        merchant_key = normalize_merchant(parsed.merchant)
        assessment = assess_parsed_receipt(parsed)
        arithmetic_status = {
            "reconciled": "verified",
            "mismatch": "mismatch",
            "not_checkable": "insufficient_data",
        }[assessment.arithmetic_status]
        receipt = PurchaseReceipt(
            owner_user_id=self.owner_user_id,
            source=source,
            source_external_id=external_id,
            content_sha256=fingerprint,
            merchant_raw=parsed.merchant,
            merchant_normalized=merchant_key,
            purchased_at=parsed.purchased_at,
            subtotal_cents=parsed.subtotal_cents,
            tax_cents=parsed.tax_cents,
            tip_cents=parsed.tip_cents,
            discount_cents=parsed.discount_cents,
            total_cents=parsed.total_cents,
            currency=parsed.currency,
            line_items_complete=parsed.line_items_complete,
            quality_warnings_json=list(assessment.warnings),
            arithmetic_status=arithmetic_status,
            parse_status=ReceiptParseStatus.NEEDS_REVIEW.value,
            parse_confidence=parsed.confidence,
            failure_code=failure_code,
        )
        self.db.add(receipt)
        self.db.flush()
        normalizer = ItemNormalizationService(self.db)
        automatic_alias_hits = 0
        for parsed_item in parsed.items:
            normalized = normalize_item_name(parsed_item.name)
            classification = classify_receipt_line(
                raw_name=parsed_item.name,
                category=parsed_item.category,
                model_classification=parsed_item.classification,
                model_confidence=(
                    parsed_item.classification_confidence
                    if parsed_item.classification_confidence is not None
                    else parsed_item.confidence
                ),
                model_canonical_name=parsed_item.canonical_name,
                is_household_purchase=parsed_item.is_household_purchase,
            )
            normalized_quantity = normalize_quantity(
                parsed_item.quantity,
                parsed_item.unit,
                source_confidence=parsed_item.confidence,
            )
            if classification.classification == "uncertain" and normalized:
                status = ReceiptItemMatchStatus.UNMATCHED.value
                match = None
            elif classification.classification not in TRACKABLE_CLASSIFICATIONS or not normalized:
                status = ReceiptItemMatchStatus.IRRELEVANT.value
                match = None
            else:
                match = normalizer.match(parsed_item.name, merchant_key)
                canonical_match = (
                    normalizer.match(classification.canonical_name)
                    if classification.canonical_name
                    else None
                )
                if (
                    match.household_item
                    and match.confidence >= self.settings.receipt_auto_match_confidence
                    and not match.ambiguous
                ):
                    status = ReceiptItemMatchStatus.MATCHED.value
                    if match.source == "alias":
                        automatic_alias_hits += 1
                elif (
                    match.household_item
                    and match.confidence >= self.settings.receipt_possible_match_confidence
                ):
                    status = ReceiptItemMatchStatus.POSSIBLE.value
                elif canonical_match and canonical_match.household_item:
                    # A canonical-name match is useful cross-merchant evidence, but it is
                    # never strong enough to teach a raw alias without confirmation.
                    match = canonical_match
                    status = ReceiptItemMatchStatus.POSSIBLE.value
                else:
                    status = ReceiptItemMatchStatus.UNMATCHED.value
            self.db.add(
                PurchaseReceiptItem(
                    receipt_id=receipt.id,
                    raw_name=parsed_item.name,
                    normalized_name=normalized or parsed_item.name.casefold()[:255],
                    quantity=parsed_item.quantity,
                    unit=parsed_item.unit,
                    normalized_quantity=normalized_quantity.quantity,
                    normalized_unit=normalized_quantity.unit,
                    package_size=normalized_quantity.package_size,
                    quantity_confidence=normalized_quantity.confidence,
                    unit_price_cents=parsed_item.unit_price_cents,
                    line_total_cents=parsed_item.line_total_cents,
                    brand=parsed_item.brand,
                    category=parsed_item.category,
                    classification=classification.classification,
                    classification_confidence=classification.confidence,
                    canonical_name=classification.canonical_name,
                    household_item_id=match.household_item.id
                    if match and match.household_item
                    else None,
                    match_status=status,
                    match_confidence=(
                        min(match.confidence, parsed_item.confidence)
                        if match
                        else parsed_item.confidence
                    ),
                )
            )
        self.db.flush()
        # Reconcile only after the complete line set is durable in this
        # transaction. Only a shared non-null artifact hash may establish a
        # cross-channel duplicate; equal parsed fields never collapse purchases.
        transaction_match = ReceiptTransactionReconciliationService(self.db).reconcile_receipt(
            receipt
        )
        transaction_match_ambiguous = (
            transaction_match.status == ReceiptTransactionMatchStatus.AMBIGUOUS
        )
        # Preserve the legacy API/UI disclosure while consumers migrate to the
        # explicit reconciliation projection. The match service remains the
        # source of truth for status, evidence, confidence, and timestamps.
        if transaction_match_ambiguous and receipt.failure_code is None:
            receipt.failure_code = "receipt_transaction_match_ambiguous"
        if self.settings.autonomous_classification_enabled:
            self._classify_receipt_nonblocking(receipt)
        self._sync_review_items(receipt)
        record_audit_once(
            self.db,
            event_type="first_receipt_processed",
            resource_type="purchase_receipt",
            resource_id=str(receipt.id),
        )
        try:
            self.db.commit()
        except IntegrityError:
            # A duplicate Telegram webhook or concurrent Gmail job can pass the
            # initial lookup in two sessions. The database uniqueness boundary
            # wins; return the already-created receipt instead of parsing twice.
            self.db.rollback()
            concurrent = self._existing(source, external_id, fingerprint)
            if concurrent is not None:
                return concurrent
            raise
        receipt = self.get(receipt.id)
        metrics = safe_learning_metrics(receipt)
        metrics["automatic_alias_hits"] = automatic_alias_hits
        log_event(logger, "receipt_learning_analyzed", **metrics)
        matched = [
            line
            for line in receipt.items
            if line.match_status == ReceiptItemMatchStatus.MATCHED.value
        ]
        all_confident = bool(matched) and all(
            (line.match_confidence or 0) >= self.settings.receipt_auto_match_confidence
            for line in matched
        )
        if (
            auto_confirm_high_confidence
            and failure_code is None
            and not transaction_match_ambiguous
            and parsed.confidence >= self.settings.receipt_auto_match_confidence
            and all_confident
            and all(
                line.match_status
                in {
                    ReceiptItemMatchStatus.MATCHED.value,
                    ReceiptItemMatchStatus.IRRELEVANT.value,
                    ReceiptItemMatchStatus.REJECTED.value,
                }
                for line in receipt.items
            )
        ):
            return self.confirm(receipt.id, user_confirmed=False)
        return receipt

    def _sync_review_items(self, receipt: PurchaseReceipt) -> None:
        from app.services.review_inbox_service import ReviewInboxService

        ReviewInboxService(self.db).sync_receipt(receipt, commit=False)

    def _classify_receipt_nonblocking(self, receipt: PurchaseReceipt) -> None:
        """Apply all internal receipt effects atomically or schedule repair."""

        try:
            # Internal taxonomy, household learning, and cadence estimates are
            # deliberately best-effort. A classification/storage outage must
            # never turn a readable receipt into a failed ingestion or restore.
            with self.db.begin_nested():
                classification = AutonomousClassificationService(
                    self.db, self.settings
                ).classify_receipt(receipt, commit=False)
                if classification.failures:
                    # The autonomous service converts bounded apply errors into
                    # a summary. Raising inside this outer savepoint rolls back a
                    # new FINAL projection together with incomplete learning.
                    raise ValueError("autonomous_receipt_classification_incomplete")
        except (SQLAlchemyError, ValueError) as exc:
            # New work is unapplied after savepoint rollback. A restored receipt
            # may retain its prior FINAL projection while its acquisition was
            # deliberately undone by Ignore. Both states are durable, bounded
            # repair targets; user corrections are never enrolled.
            self.db.execute(
                update(PurchaseReceiptItem)
                .where(
                    PurchaseReceiptItem.receipt_id == receipt.id,
                    or_(
                        PurchaseReceiptItem.classification_applied_at.is_(None),
                        and_(
                            PurchaseReceiptItem.classification_applied_at.is_not(None),
                            PurchaseReceiptItem.classification_decision_state
                            == ClassificationDecisionState.FINAL.value,
                        ),
                    ),
                    or_(
                        PurchaseReceiptItem.classification_authority.is_(None),
                        PurchaseReceiptItem.classification_authority
                        != ClassificationAuthority.USER_CORRECTION.value,
                    ),
                )
                .values(classification_retry_at=utc_now())
            )
            log_event(
                logger,
                "receipt_autonomous_classification_deferred",
                error_type=type(exc).__name__,
                retry_scheduled=True,
            )

    def _existing(
        self,
        source: str,
        external_id: str,
        fingerprint: str,
        *,
        reprocess_transient_failure: bool = False,
    ) -> PurchaseReceipt | None:
        # Replaying the same provider event is always idempotent, including a
        # failed event. A new Telegram message/Gmail message/web upload gets a
        # new external ID and may safely retry a transient parser failure.
        receipt = self.db.execute(
            select(PurchaseReceipt)
            .options(selectinload(PurchaseReceipt.items))
            .where(
                PurchaseReceipt.source == source, PurchaseReceipt.source_external_id == external_id
            )
        ).scalar_one_or_none()
        if receipt:
            return receipt
        receipt = (
            self.db.execute(
                select(PurchaseReceipt)
                .options(selectinload(PurchaseReceipt.items))
                .where(PurchaseReceipt.content_sha256 == fingerprint)
                # Once a later attempt recovers, it becomes the canonical
                # fingerprint result instead of the older transient failure.
                .order_by(
                    case(
                        (
                            PurchaseReceipt.parse_status == ReceiptParseStatus.FAILED.value,
                            1,
                        ),
                        else_=0,
                    ),
                    PurchaseReceipt.id,
                )
            )
            .scalars()
            .first()
        )
        if (
            receipt is not None
            and reprocess_transient_failure
            and receipt.parse_status == ReceiptParseStatus.FAILED.value
            and receipt.failure_code in _REPROCESSABLE_RECEIPT_FAILURE_CODES
        ):
            log_event(
                logger,
                "receipt_transient_failure_reprocessing",
                source=source,
                prior_failure_code=receipt.failure_code,
            )
            return None
        return receipt

    def _failed_receipt(
        self, source: str, external_id: str, fingerprint: str, code: str
    ) -> PurchaseReceipt:
        receipt = PurchaseReceipt(
            owner_user_id=self.owner_user_id,
            source=source,
            source_external_id=external_id,
            content_sha256=fingerprint,
            parse_status=ReceiptParseStatus.FAILED.value,
            failure_code=code[:64],
        )
        self.db.add(receipt)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            concurrent = self._existing(source, external_id, fingerprint)
            if concurrent is not None:
                return concurrent
            raise
        self.db.refresh(receipt)
        return receipt

    def _ensure_scope_still_active(self) -> None:
        """Recheck request/job authority after the potentially slow provider call."""

        workspace_id = self.db.info.get("workspace_id")
        user_id = self.owner_user_id
        if not isinstance(workspace_id, int) or not isinstance(user_id, int):
            return
        membership = self.db.scalar(
            select(WorkspaceMembership.id).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
            )
        )
        active_user = self.db.scalar(
            select(User.id).where(User.id == user_id, User.status == "active")
        )
        if membership is None or active_user is None:
            raise PermissionError("receipt_scope_revoked")

    def _ensure_model_receipt_consent(self) -> None:
        if not self._parser_requires_stored_consent:
            return
        workspace_id = self.db.info.get("workspace_id")
        if not isinstance(workspace_id, int):
            # Unscoped local parser harnesses retain their existing behavior;
            # every production request/job enters an explicit workspace first.
            return
        user_id = self.owner_user_id
        if not isinstance(user_id, int):
            raise PermissionError("model_receipt_processing_consent_required")
        consent = self.db.scalar(
            select(DataConsent.id).where(
                DataConsent.workspace_id == workspace_id,
                DataConsent.user_id == user_id,
                DataConsent.purpose == "model_receipt_processing",
                DataConsent.granted.is_(True),
                DataConsent.revoked_at.is_(None),
            )
        )
        if consent is None:
            raise PermissionError("model_receipt_processing_consent_required")

    def _log_parse_result(
        self,
        event: str,
        *,
        source: str,
        media_class: str,
        started: float,
        failure_code: str | None,
        normalization_latency_ms: int | None = None,
        receipt: PurchaseReceipt | None = None,
    ) -> None:
        observation = getattr(self.parser, "last_observation", None)
        request_count = int(getattr(observation, "request_count", 0) or 0)
        log_event(
            logger,
            event,
            source=source,
            media_class=media_class,
            normalization_latency_ms=normalization_latency_ms,
            provider_latency_ms=getattr(observation, "latency_ms", None),
            total_latency_ms=max(0, round((time.monotonic() - started) * 1000)),
            provider_request_count=request_count,
            input_tokens=getattr(observation, "input_tokens", None),
            output_tokens=getattr(observation, "output_tokens", None),
            estimated_cost_micros=getattr(observation, "estimated_cost_micros", None),
            retry_used=request_count > 1,
            retry_reason=getattr(observation, "retry_reason", None),
            failure_code=failure_code,
            item_count=len(receipt.items) if receipt is not None else 0,
            transaction_matched=bool(receipt and receipt.transaction_id),
        )
        if request_count > 1:
            log_event(
                logger,
                "receipt_image_retry",
                source=source,
                media_class=media_class,
                retry_reason=getattr(observation, "retry_reason", None),
            )


def _logical_purchase_key(
    workspace_id: int,
    receipt_item_id: int,
) -> str:
    identity = f"receipt-item|{workspace_id}|{receipt_item_id}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _automatic_acquisition_safe(
    receipt: PurchaseReceipt,
    line: PurchaseReceiptItem,
) -> bool:
    if receipt.purchased_at is None:
        return False
    purchased_at = _aware(receipt.purchased_at)
    ingested_at = _aware(receipt.created_at)
    return bool(
        ingested_at - timedelta(days=730) <= purchased_at <= ingested_at + timedelta(days=1)
        and receipt.arithmetic_status == "verified"
        and receipt.line_items_complete
        and line.line_total_cents is not None
        and line.line_total_cents > 0
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _active_owner_user_id(db: Session) -> int | None:
    user_id = db.info.get("user_id")
    return user_id if isinstance(user_id, int) and user_id > 0 else None

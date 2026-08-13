from __future__ import annotations

import hashlib
from datetime import timedelta
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings, get_settings
from app.models import (
    ExpenseTransaction,
    HouseholdItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReceiptItemMatchStatus,
    ReceiptParseStatus,
    utc_now,
)
from app.services.acquisition_service import AcquisitionService
from app.services.item_normalization_service import (
    ItemNormalizationService,
    normalize_item_name,
    normalize_merchant,
)
from app.services.managed_auth_service import record_audit_once
from app.services.quantity_normalization_service import normalize_quantity
from app.services.receipt_parser_service import (
    ParsedReceipt,
    ReceiptParser,
    ReceiptParserError,
    build_receipt_parser,
)


class ReceiptIngestionService:
    def __init__(
        self,
        db: Session,
        settings: Settings | None = None,
        parser: ReceiptParser | None = None,
    ):
        self.db = db
        self.settings = settings or get_settings()
        self.parser = parser or build_receipt_parser(self.settings)

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
        if len(content) > self.settings.receipt_max_attachment_bytes:
            raise ValueError("receipt_attachment_too_large")
        fingerprint = hashlib.sha256(content).hexdigest()
        existing = self._existing(source, source_external_id, fingerprint)
        if existing:
            return existing
        try:
            parsed = self.parser.parse_attachment(content, mime_type, filename)
        except ReceiptParserError as exc:
            return self._failed_receipt(source, source_external_id, fingerprint, str(exc))
        return self._persist(
            source,
            source_external_id,
            fingerprint,
            parsed,
            auto_confirm_high_confidence=auto_confirm_high_confidence,
        )

    def ingest_text(
        self,
        *,
        source: str,
        source_external_id: str,
        text: str,
        auto_confirm_high_confidence: bool = False,
    ) -> PurchaseReceipt:
        fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing = self._existing(source, source_external_id, fingerprint)
        if existing:
            return existing
        try:
            parsed = self.parser.parse_text(text)
        except ReceiptParserError as exc:
            return self._failed_receipt(source, source_external_id, fingerprint, str(exc))
        return self._persist(
            source,
            source_external_id,
            fingerprint,
            parsed,
            auto_confirm_high_confidence=auto_confirm_high_confidence,
        )

    def get(self, receipt_id: int) -> PurchaseReceipt:
        receipt = self.db.execute(
            select(PurchaseReceipt)
            .options(selectinload(PurchaseReceipt.items))
            .where(PurchaseReceipt.id == receipt_id)
        ).scalar_one_or_none()
        if receipt is None:
            raise ValueError("Receipt not found.")
        return receipt

    def confirm(self, receipt_id: int, *, user_confirmed: bool = True) -> PurchaseReceipt:
        receipt = self.get(receipt_id)
        if receipt.parse_status == ReceiptParseStatus.IGNORED.value:
            raise ValueError("Ignored receipt cannot be confirmed.")
        for line in receipt.items:
            if (
                line.household_item is not None
                and line.match_status == ReceiptItemMatchStatus.MATCHED.value
            ):
                AcquisitionService(self.db).record(
                    line.household_item,
                    acquired_at=receipt.purchased_at or receipt.created_at,
                    quantity=line.quantity,
                    unit=line.unit,
                    normalized_quantity=line.normalized_quantity,
                    normalized_unit=line.normalized_unit,
                    package_size=line.package_size,
                    quantity_confidence=line.quantity_confidence,
                    merchant=receipt.merchant_normalized,
                    logical_purchase_key=_logical_purchase_key(
                        line.household_item_id,
                        receipt.merchant_normalized,
                        receipt.purchased_at or receipt.created_at,
                        receipt.total_cents,
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
                    line.household_item,
                    line.raw_name,
                    merchant=receipt.merchant_normalized,
                    source="confirmed_receipt",
                    confidence=line.match_confidence or 1.0,
                )
        receipt.parse_status = ReceiptParseStatus.CONFIRMED.value
        receipt.confirmed_at = utc_now()
        receipt.updated_at = utc_now()
        self.db.commit()
        return self.get(receipt.id)

    def ignore(self, receipt_id: int) -> PurchaseReceipt:
        receipt = self.get(receipt_id)
        receipt.parse_status = ReceiptParseStatus.IGNORED.value
        receipt.ignored_at = utc_now()
        receipt.updated_at = utc_now()
        self.db.commit()
        return self.get(receipt.id)

    def restore(self, receipt_id: int) -> PurchaseReceipt:
        receipt = self.get(receipt_id)
        if receipt.parse_status != ReceiptParseStatus.IGNORED.value:
            raise ValueError("Only an ignored receipt can be restored.")
        receipt.parse_status = ReceiptParseStatus.NEEDS_REVIEW.value
        receipt.ignored_at = None
        receipt.updated_at = utc_now()
        self.db.commit()
        return self.get(receipt.id)

    def update_line_match(
        self,
        receipt_id: int,
        line_id: int,
        household_item_id: int | None,
        *,
        rejected: bool = False,
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
            item = self.db.get(HouseholdItem, household_item_id)
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
                        item.id,
                        receipt.merchant_normalized,
                        receipt.purchased_at or receipt.created_at,
                        receipt.total_cents,
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
        self.db.commit()
        self.db.refresh(line)
        return line

    def track_line_as_new_household_item(
        self,
        receipt_id: int,
        line_id: int,
        *,
        name: str,
        cadence_days: int,
        replenishment_mode: str = "either",
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
            replenishment_mode=replenishment_mode,
            enabled=True,
        )
        self.db.add(item)
        self.db.flush()
        updated_line = self.update_line_match(receipt.id, line.id, item.id)
        self.db.refresh(item)
        return item, updated_line

    def _persist(
        self,
        source: str,
        external_id: str,
        fingerprint: str,
        parsed: ParsedReceipt,
        *,
        auto_confirm_high_confidence: bool,
    ) -> PurchaseReceipt:
        merchant_key = normalize_merchant(parsed.merchant)
        receipt = PurchaseReceipt(
            source=source,
            source_external_id=external_id,
            content_sha256=fingerprint,
            merchant_raw=parsed.merchant,
            merchant_normalized=merchant_key,
            purchased_at=parsed.purchased_at,
            subtotal_cents=parsed.subtotal_cents,
            tax_cents=parsed.tax_cents,
            total_cents=parsed.total_cents,
            currency=parsed.currency,
            parse_status=ReceiptParseStatus.NEEDS_REVIEW.value,
            parse_confidence=parsed.confidence,
        )
        receipt.transaction_id = self._reconcile_transaction(receipt)
        self.db.add(receipt)
        self.db.flush()
        normalizer = ItemNormalizationService(self.db)
        for parsed_item in parsed.items:
            normalized = normalize_item_name(parsed_item.name)
            normalized_quantity = normalize_quantity(
                parsed_item.quantity,
                parsed_item.unit,
                source_confidence=parsed_item.confidence,
            )
            if not parsed_item.is_household_purchase or not normalized:
                status = ReceiptItemMatchStatus.IRRELEVANT.value
                match = None
            else:
                match = normalizer.match(parsed_item.name, merchant_key)
                if (
                    match.household_item
                    and match.confidence >= self.settings.receipt_auto_match_confidence
                ):
                    status = ReceiptItemMatchStatus.MATCHED.value
                elif (
                    match.household_item
                    and match.confidence >= self.settings.receipt_possible_match_confidence
                ):
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
        record_audit_once(
            self.db,
            event_type="first_receipt_processed",
            resource_type="purchase_receipt",
            resource_id=str(receipt.id),
        )
        self.db.commit()
        receipt = self.get(receipt.id)
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
            and parsed.confidence >= self.settings.receipt_auto_match_confidence
            and all_confident
        ):
            return self.confirm(receipt.id, user_confirmed=False)
        return receipt

    def _existing(self, source: str, external_id: str, fingerprint: str) -> PurchaseReceipt | None:
        receipt = self.db.execute(
            select(PurchaseReceipt)
            .options(selectinload(PurchaseReceipt.items))
            .where(
                PurchaseReceipt.source == source, PurchaseReceipt.source_external_id == external_id
            )
        ).scalar_one_or_none()
        if receipt:
            return receipt
        return (
            self.db.execute(
                select(PurchaseReceipt)
                .options(selectinload(PurchaseReceipt.items))
                .where(PurchaseReceipt.content_sha256 == fingerprint)
                .order_by(PurchaseReceipt.id)
            )
            .scalars()
            .first()
        )

    def _failed_receipt(
        self, source: str, external_id: str, fingerprint: str, code: str
    ) -> PurchaseReceipt:
        receipt = PurchaseReceipt(
            source=source,
            source_external_id=external_id,
            content_sha256=fingerprint,
            parse_status=ReceiptParseStatus.FAILED.value,
            failure_code=code[:64],
        )
        self.db.add(receipt)
        self.db.commit()
        self.db.refresh(receipt)
        return receipt

    def _reconcile_transaction(self, receipt: PurchaseReceipt) -> int | None:
        if receipt.total_cents is None or receipt.purchased_at is None:
            return None
        purchased = receipt.purchased_at.date()
        candidates = self.db.execute(
            select(ExpenseTransaction).where(
                ExpenseTransaction.amount_cents.between(
                    max(0, receipt.total_cents - 2), receipt.total_cents + 2
                ),
                ExpenseTransaction.date.between(
                    purchased - timedelta(days=2), purchased + timedelta(days=2)
                ),
            )
        ).scalars()
        best: tuple[float, ExpenseTransaction] | None = None
        for tx in candidates:
            tx_merchant = normalize_merchant(tx.merchant_name or tx.name)
            score = SequenceMatcher(None, receipt.merchant_normalized or "", tx_merchant).ratio()
            if best is None or score > best[0]:
                best = (score, tx)
        return best[1].id if best and best[0] >= 0.55 else None


def _logical_purchase_key(
    household_item_id: int,
    merchant: str | None,
    purchased_at,
    total_cents: int | None,
) -> str:
    identity = "|".join(
        [
            str(household_item_id),
            merchant or "",
            purchased_at.date().isoformat(),
            str(total_cents) if total_cents is not None else "unknown",
        ]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()

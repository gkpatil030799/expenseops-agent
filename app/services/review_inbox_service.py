from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, and_, desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.logging_config import log_event
from app.models import (
    ExpenseTransaction,
    PlaidItem,
    PurchaseReceipt,
    ReceiptParseStatus,
    ReviewItem,
    ReviewItemKind,
    ReviewItemSourceType,
    ReviewItemState,
    TransactionStatus,
    WorkspaceMembership,
    utc_now,
)
from app.services.ai_memory_service import AIInterpretationMemoryService

_ACTIONABLE_TRANSACTION_STATES = frozenset(
    {TransactionStatus.ASK_USER.value, TransactionStatus.SHARED_DRAFT.value}
)
_USABLE_RECEIPT_STATES = frozenset(
    {
        ReceiptParseStatus.PARSED.value,
        ReceiptParseStatus.NEEDS_REVIEW.value,
        ReceiptParseStatus.CONFIRMED.value,
    }
)
_ITEMIZED_ACTIVITY_TYPES = frozenset(
    {"restaurant_meal", "coffee_beverage", "food_delivery", "nightlife"}
)

logger = logging.getLogger(__name__)


class ReviewInboxError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewInboxPage:
    items: list[dict[str, Any]]
    total_open: int
    unread_count: int
    limit: int
    offset: int


def _fingerprint(*values: object) -> str:
    raw = "\x1f".join("" if value is None else str(value) for value in values)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ReviewInboxService:
    """Maintain presentation state without becoming a financial authority."""

    def __init__(self, db: Session):
        self.db = db

    def _scope(self) -> tuple[int, int]:
        workspace_id = self.db.info.get("workspace_id")
        user_id = self.db.info.get("user_id")
        if not isinstance(workspace_id, int) or not isinstance(user_id, int):
            raise ReviewInboxError("Review inbox requires an authenticated tenant scope.")
        return workspace_id, user_id

    def sync_transaction(
        self,
        tx: ExpenseTransaction,
        *,
        owner_user_id: int | None = None,
        replacement_for_transaction_id: int | None = None,
        commit: bool = False,
    ) -> ReviewItem | None:
        owner_id = owner_user_id or self._transaction_owner(tx)
        if owner_id is None or not self._is_workspace_member(tx.workspace_id, owner_id):
            return None
        item = None
        if replacement_for_transaction_id is not None:
            item = self.db.scalar(
                select(ReviewItem)
                .where(
                    ReviewItem.workspace_id == tx.workspace_id,
                    ReviewItem.owner_user_id == owner_id,
                    ReviewItem.kind == ReviewItemKind.TRANSACTION_REVIEW.value,
                    ReviewItem.source_type == ReviewItemSourceType.TRANSACTION.value,
                    ReviewItem.source_entity_id == replacement_for_transaction_id,
                )
                .with_for_update()
            )
        if item is None:
            item = self._source_item(
                workspace_id=tx.workspace_id,
                owner_user_id=owner_id,
                kind=ReviewItemKind.TRANSACTION_REVIEW,
                source_type=ReviewItemSourceType.TRANSACTION,
                source_entity_id=tx.id,
                lock=True,
            )
        previous_state = item.state if item is not None else None
        actionable = tx.status in _ACTIONABLE_TRANSACTION_STATES and tx.splitwise_expense_id is None
        now = utc_now()
        created = False
        if item is None and actionable:
            candidate = ReviewItem(
                workspace_id=tx.workspace_id,
                owner_user_id=owner_id,
                kind=ReviewItemKind.TRANSACTION_REVIEW.value,
                source_type=ReviewItemSourceType.TRANSACTION.value,
                source_entity_id=tx.id,
                state=ReviewItemState.OPEN.value,
                action_codes_json=["personal", "recommended_split", "customize"],
                source_fingerprint=self._transaction_fingerprint(tx),
            )
            item, created = self._insert_or_find(candidate)
        elif item is not None:
            if replacement_for_transaction_id is not None:
                item.source_entity_id = tx.id
            item.source_fingerprint = self._transaction_fingerprint(tx)
            item.updated_at = now
            if actionable:
                if item.state != ReviewItemState.OPEN.value:
                    item.seen_at = None
                item.state = ReviewItemState.OPEN.value
                item.resolved_at = None
                item.stale_at = None
                item.action_codes_json = ["personal", "recommended_split", "customize"]
            elif item.state == ReviewItemState.OPEN.value:
                item.state = ReviewItemState.RESOLVED.value
                item.resolved_at = now
        if item is not None:
            self.db.flush()
            if created:
                log_event(logger, "transaction_review_item_created", count=1)
            elif actionable:
                log_event(logger, "transaction_review_item_updated", count=1)
            elif previous_state == ReviewItemState.OPEN.value:
                log_event(logger, "transaction_review_item_resolved", count=1)
        if commit:
            self.db.commit()
        return item

    def sync_receipt(self, receipt: PurchaseReceipt, *, commit: bool = False) -> list[ReviewItem]:
        owner_id = receipt.owner_user_id
        if owner_id is None or not self._is_workspace_member(receipt.workspace_id, owner_id):
            return []
        desired_kind = self._receipt_task_kind(receipt)
        now = utc_now()
        existing = list(
            self.db.scalars(
                select(ReviewItem)
                .where(
                    ReviewItem.workspace_id == receipt.workspace_id,
                    ReviewItem.owner_user_id == owner_id,
                    ReviewItem.source_type == ReviewItemSourceType.RECEIPT.value,
                    ReviewItem.source_entity_id == receipt.id,
                    ReviewItem.kind.in_(
                        [
                            ReviewItemKind.ITEMIZED_SPLIT_READY.value,
                            ReviewItemKind.RECEIPT_MATCH_NEEDED.value,
                        ]
                    ),
                )
                .order_by(ReviewItem.id)
                .with_for_update()
            )
        )
        active: list[ReviewItem] = []
        for item in existing:
            if desired_kind is not None and item.kind == desired_kind.value:
                item.state = ReviewItemState.OPEN.value
                item.resolved_at = None
                item.stale_at = None
                item.updated_at = now
                item.source_fingerprint = self._receipt_fingerprint(receipt)
                active.append(item)
            elif item.state == ReviewItemState.OPEN.value:
                item.state = ReviewItemState.STALE.value
                item.stale_at = now
                item.updated_at = now
        if desired_kind is not None and not active:
            action_codes = (
                ["open_receipt_match"]
                if desired_kind is ReviewItemKind.RECEIPT_MATCH_NEEDED
                else ["itemized_split", "open_receipt"]
            )
            item = ReviewItem(
                workspace_id=receipt.workspace_id,
                owner_user_id=owner_id,
                kind=desired_kind.value,
                source_type=ReviewItemSourceType.RECEIPT.value,
                source_entity_id=receipt.id,
                state=ReviewItemState.OPEN.value,
                action_codes_json=action_codes,
                source_fingerprint=self._receipt_fingerprint(receipt),
            )
            item, created = self._insert_or_find(item)
            active.append(item)
            if created:
                log_event(
                    logger,
                    "itemized_split_review_created"
                    if desired_kind is ReviewItemKind.ITEMIZED_SPLIT_READY
                    else "receipt_match_review_created",
                    count=1,
                )
        self.db.flush()
        if commit:
            self.db.commit()
        return active

    def list_open(self, *, limit: int = 50, offset: int = 0) -> ReviewInboxPage:
        workspace_id, user_id = self._scope()
        self._reconcile_open_items(workspace_id=workspace_id, user_id=user_id)
        base = and_(
            ReviewItem.workspace_id == workspace_id,
            ReviewItem.owner_user_id == user_id,
            ReviewItem.state == ReviewItemState.OPEN.value,
        )
        total_open = int(self.db.scalar(select(func.count(ReviewItem.id)).where(base)) or 0)
        unread_count = int(
            self.db.scalar(
                select(func.count(ReviewItem.id)).where(base, ReviewItem.seen_at.is_(None))
            )
            or 0
        )
        rows = list(
            self.db.scalars(
                select(ReviewItem)
                .where(base)
                .order_by(desc(ReviewItem.created_at), desc(ReviewItem.id))
                .offset(offset)
                .limit(limit)
            )
        )
        log_event(
            logger,
            "review_badge_count",
            open_count=total_open,
            unread_count=unread_count,
        )
        return ReviewInboxPage(
            items=[self._serialize(item) for item in rows],
            total_open=total_open,
            unread_count=unread_count,
            limit=limit,
            offset=offset,
        )

    def _reconcile_open_items(self, *, workspace_id: int, user_id: int) -> None:
        """Fail closed when the source changed without passing through a normal hook."""

        rows = list(
            self.db.scalars(
                select(ReviewItem)
                .where(
                    ReviewItem.workspace_id == workspace_id,
                    ReviewItem.owner_user_id == user_id,
                    ReviewItem.state == ReviewItemState.OPEN.value,
                )
                .order_by(ReviewItem.id)
                .with_for_update()
            )
        )
        changed = False
        now = utc_now()
        for item in rows:
            current = True
            if item.source_type == ReviewItemSourceType.TRANSACTION.value:
                tx = self.db.scalar(
                    select(ExpenseTransaction).where(
                        ExpenseTransaction.workspace_id == workspace_id,
                        ExpenseTransaction.id == item.source_entity_id,
                    )
                )
                current = bool(
                    tx
                    and tx.status in _ACTIONABLE_TRANSACTION_STATES
                    and tx.splitwise_expense_id is None
                )
            elif item.source_type == ReviewItemSourceType.RECEIPT.value:
                receipt = self.db.scalar(
                    select(PurchaseReceipt)
                    .options(selectinload(PurchaseReceipt.items))
                    .where(
                        PurchaseReceipt.workspace_id == workspace_id,
                        PurchaseReceipt.id == item.source_entity_id,
                    )
                )
                current = bool(
                    receipt
                    and (desired := self._receipt_task_kind(receipt)) is not None
                    and desired.value == item.kind
                )
            if current:
                continue
            item.state = ReviewItemState.STALE.value
            item.stale_at = now
            item.updated_at = now
            changed = True
        if changed:
            self.db.commit()

    def mark_seen(self, public_id: str) -> ReviewItem:
        item = self._owned_public_item(public_id, lock=True)
        if item.state != ReviewItemState.OPEN.value:
            raise ReviewInboxError("This review item is no longer open.")
        if item.seen_at is None:
            item.seen_at = utc_now()
            item.updated_at = item.seen_at
            self.db.commit()
            self.db.refresh(item)
            log_event(logger, "transaction_review_item_seen", count=1)
        return item

    def _insert_or_find(self, candidate: ReviewItem) -> tuple[ReviewItem, bool]:
        """Insert one projection row while tolerating concurrent source evaluation."""

        savepoint = self.db.begin_nested()
        try:
            self.db.add(candidate)
            self.db.flush([candidate])
            savepoint.commit()
            return candidate, True
        except IntegrityError:
            savepoint.rollback()
            existing = self._source_item(
                workspace_id=candidate.workspace_id,
                owner_user_id=candidate.owner_user_id,
                kind=ReviewItemKind(candidate.kind),
                source_type=ReviewItemSourceType(candidate.source_type),
                source_entity_id=candidate.source_entity_id,
                lock=True,
            )
            if existing is None:
                raise
            return existing, False

    def unique_active_transaction(self) -> ExpenseTransaction | None:
        workspace_id, user_id = self._scope()
        rows = list(
            self.db.scalars(
                select(ReviewItem)
                .where(
                    ReviewItem.workspace_id == workspace_id,
                    ReviewItem.owner_user_id == user_id,
                    ReviewItem.state == ReviewItemState.OPEN.value,
                    ReviewItem.kind == ReviewItemKind.TRANSACTION_REVIEW.value,
                    ReviewItem.source_type == ReviewItemSourceType.TRANSACTION.value,
                )
                .order_by(desc(ReviewItem.created_at))
                .limit(2)
            )
        )
        if len(rows) != 1:
            return None
        tx = self.db.scalar(
            select(ExpenseTransaction).where(
                ExpenseTransaction.workspace_id == workspace_id,
                ExpenseTransaction.id == rows[0].source_entity_id,
            )
        )
        return tx if tx and tx.status in _ACTIONABLE_TRANSACTION_STATES else None

    def open_transaction_count(self) -> int:
        workspace_id, user_id = self._scope()
        return int(
            self.db.scalar(
                select(func.count(ReviewItem.id)).where(
                    ReviewItem.workspace_id == workspace_id,
                    ReviewItem.owner_user_id == user_id,
                    ReviewItem.state == ReviewItemState.OPEN.value,
                    ReviewItem.kind == ReviewItemKind.TRANSACTION_REVIEW.value,
                )
            )
            or 0
        )

    def _serialize(self, item: ReviewItem) -> dict[str, Any]:
        data: dict[str, Any] = {
            "public_id": item.public_id,
            "kind": item.kind,
            "state": item.state,
            "unread": item.seen_at is None,
            "seen_at": item.seen_at,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "available_actions": list(item.action_codes_json or []),
            "transaction": None,
            "receipt": None,
            "recommendation": None,
        }
        if item.source_type == ReviewItemSourceType.TRANSACTION.value:
            tx = self.db.scalar(
                select(ExpenseTransaction)
                .options(selectinload(ExpenseTransaction.plaid_item))
                .where(
                    ExpenseTransaction.workspace_id == item.workspace_id,
                    ExpenseTransaction.id == item.source_entity_id,
                )
            )
            if tx is not None:
                data["transaction"] = {
                    "id": tx.id,
                    "merchant_name": tx.merchant_name,
                    "name": tx.name,
                    "amount_cents": tx.amount_cents,
                    "currency": tx.iso_currency_code,
                    "date": tx.date,
                    "pending": tx.pending,
                    "status": tx.status,
                    "institution_name": tx.plaid_item.institution_name if tx.plaid_item else None,
                }
                try:
                    recommendation = AIInterpretationMemoryService(
                        self.db
                    ).recommendation_for_transaction(tx)
                except ValueError:
                    recommendation = None
                data["recommendation"] = recommendation
        elif item.source_type == ReviewItemSourceType.RECEIPT.value:
            receipt = self.db.scalar(
                select(PurchaseReceipt)
                .options(selectinload(PurchaseReceipt.items))
                .where(
                    PurchaseReceipt.workspace_id == item.workspace_id,
                    PurchaseReceipt.id == item.source_entity_id,
                )
            )
            if receipt is not None:
                data["receipt"] = {
                    "id": receipt.id,
                    "merchant_name": receipt.merchant_normalized or receipt.merchant_raw,
                    "total_cents": receipt.total_cents,
                    "currency": receipt.currency,
                    "purchased_at": receipt.purchased_at,
                    "parse_status": receipt.parse_status,
                    "transaction_match_status": receipt.transaction_match_status,
                    "transaction_id": receipt.transaction_id,
                    "line_count": len(receipt.items),
                }
        return data

    def _owned_public_item(self, public_id: str, *, lock: bool) -> ReviewItem:
        workspace_id, user_id = self._scope()
        stmt: Select[tuple[ReviewItem]] = select(ReviewItem).where(
            ReviewItem.workspace_id == workspace_id,
            ReviewItem.owner_user_id == user_id,
            ReviewItem.public_id == public_id,
        )
        if lock:
            stmt = stmt.with_for_update()
        item = self.db.scalar(stmt)
        if item is None:
            raise ReviewInboxError("Review item not found.")
        return item

    def _source_item(
        self,
        *,
        workspace_id: int,
        owner_user_id: int,
        kind: ReviewItemKind,
        source_type: ReviewItemSourceType,
        source_entity_id: int,
        lock: bool,
    ) -> ReviewItem | None:
        stmt: Select[tuple[ReviewItem]] = select(ReviewItem).where(
            ReviewItem.workspace_id == workspace_id,
            ReviewItem.owner_user_id == owner_user_id,
            ReviewItem.kind == kind.value,
            ReviewItem.source_type == source_type.value,
            ReviewItem.source_entity_id == source_entity_id,
        )
        if lock:
            stmt = stmt.with_for_update()
        return self.db.scalar(stmt)

    def _transaction_owner(self, tx: ExpenseTransaction) -> int | None:
        item = tx.plaid_item or self.db.scalar(
            select(PlaidItem).where(
                PlaidItem.workspace_id == tx.workspace_id,
                PlaidItem.id == tx.plaid_item_id,
            )
        )
        return item.owner_user_id if item is not None else None

    def _is_workspace_member(self, workspace_id: int, user_id: int) -> bool:
        return (
            self.db.scalar(
                select(WorkspaceMembership.id).where(
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.user_id == user_id,
                )
            )
            is not None
        )

    @staticmethod
    def _transaction_fingerprint(tx: ExpenseTransaction) -> str:
        return _fingerprint(
            tx.id,
            tx.plaid_transaction_id,
            tx.status,
            tx.pending,
            tx.amount_cents,
            tx.iso_currency_code,
            tx.date,
            tx.splitwise_generation,
            tx.updated_at,
        )

    @staticmethod
    def _receipt_fingerprint(receipt: PurchaseReceipt) -> str:
        return _fingerprint(
            receipt.id,
            receipt.parse_status,
            receipt.transaction_match_status,
            receipt.transaction_id,
            receipt.arithmetic_status,
            receipt.line_items_complete,
            receipt.total_cents,
            receipt.updated_at,
        )

    def _receipt_task_kind(self, receipt: PurchaseReceipt) -> ReviewItemKind | None:
        if receipt.parse_status not in _USABLE_RECEIPT_STATES:
            return None
        if receipt.transaction_match_status == "ambiguous":
            return ReviewItemKind.RECEIPT_MATCH_NEEDED
        if receipt.transaction_match_status != "auto_matched" or receipt.transaction_id is None:
            return None
        if (
            receipt.arithmetic_status != "verified"
            or not receipt.line_items_complete
            or receipt.subtotal_cents is None
            or receipt.tax_cents is None
            or receipt.total_cents is None
            or receipt.total_cents <= 0
        ):
            return None
        subtotal = int(receipt.subtotal_cents)
        tax = int(receipt.tax_cents)
        total = int(receipt.total_cents)
        if subtotal <= 0 or tax < 0 or subtotal + tax > total:
            return None
        tx = self.db.scalar(
            select(ExpenseTransaction).where(
                ExpenseTransaction.workspace_id == receipt.workspace_id,
                ExpenseTransaction.id == receipt.transaction_id,
            )
        )
        if (
            tx is None
            or tx.amount_cents <= 0
            or tx.status not in _ACTIONABLE_TRANSACTION_STATES
            or tx.splitwise_expense_id is not None
            or abs(tx.amount_cents) != total
            or (tx.iso_currency_code or "USD").upper() != (receipt.currency or "USD").upper()
        ):
            return None
        priced = []
        for line in receipt.items:
            normalized_name = " ".join(re.sub(r"[^a-z0-9]+", " ", line.raw_name.casefold()).split())
            if line.classification == "non_product_line" or re.fullmatch(
                r"(?:sub ?total|sales tax|tax|tip|gratuity|total|discount|coupon|savings)",
                normalized_name,
            ):
                continue
            if line.line_total_cents is None or line.line_total_cents <= 0:
                return None
            priced.append(line)
        if not priced or len(priced) > 20:
            return None
        if sum(int(line.line_total_cents or 0) for line in priced) != subtotal:
            return None
        category = " ".join(re.sub(r"[^a-z0-9]+", " ", (tx.category or "").casefold()).split())
        dining = any(
            line.classification == "dining_or_experience"
            or line.item_activity_type in _ITEMIZED_ACTIVITY_TYPES
            for line in priced
        ) or any(term in category for term in ("food", "dining", "restaurant", "bar", "nightlife"))
        if not dining:
            return None
        return ReviewItemKind.ITEMIZED_SPLIT_READY

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExpenseTransaction, PlaidItem
from app.security import encrypt_secret
from app.services.transaction_service import TransactionService


class SandboxTransactionSyncAdapter:
    def __init__(self, db: Session):
        self.db = db

    def ensure_app_plaid_item(
        self,
        *,
        item_id: str,
        access_token: str,
        cursor: str | None = None,
    ) -> PlaidItem:
        item = self.db.execute(
            select(PlaidItem).where(PlaidItem.item_id == item_id)
        ).scalar_one_or_none()
        encrypted = encrypt_secret(access_token)
        if item is None:
            item = PlaidItem(
                item_id=item_id,
                access_token_encrypted=encrypted,
                institution_name="Plaid Sandbox Lab",
                cursor=cursor,
            )
            self.db.add(item)
        else:
            item.access_token_encrypted = encrypted
            item.institution_name = item.institution_name or "Plaid Sandbox Lab"
            if cursor is not None:
                item.cursor = cursor
        self.db.commit()
        self.db.refresh(item)
        return item

    def get_app_plaid_item(self, item_id: str) -> PlaidItem | None:
        return self.db.execute(
            select(PlaidItem).where(PlaidItem.item_id == item_id)
        ).scalar_one_or_none()

    def transaction_ids_for_item(self, item: PlaidItem) -> set[int]:
        rows = (
            self.db.query(ExpenseTransaction.id)
            .filter(ExpenseTransaction.plaid_item_id == item.id)
            .all()
        )
        return {int(row[0]) for row in rows}

    def sync_existing_pipeline(self, item: PlaidItem) -> dict[str, int]:
        return TransactionService(self.db).sync_item(item)

    def latest_added_transactions(
        self,
        item: PlaidItem,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        rows = (
            self.db.query(ExpenseTransaction)
            .filter(ExpenseTransaction.plaid_item_id == item.id)
            .order_by(ExpenseTransaction.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": tx.id,
                "name": tx.name,
                "merchant_name": tx.merchant_name,
                "amount_cents": tx.amount_cents,
                "pending": tx.pending,
                "status": tx.status,
                "date": str(tx.date) if tx.date else None,
                "created_at": tx.created_at.isoformat() if tx.created_at else None,
            }
            for tx in rows
        ]

    def transactions_by_ids(self, ids: set[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        rows = (
            self.db.query(ExpenseTransaction)
            .filter(ExpenseTransaction.id.in_(ids))
            .order_by(ExpenseTransaction.created_at.desc())
            .all()
        )
        return [
            {
                "id": tx.id,
                "name": tx.name,
                "merchant_name": tx.merchant_name,
                "amount_cents": tx.amount_cents,
                "pending": tx.pending,
                "status": tx.status,
                "date": str(tx.date) if tx.date else None,
                "created_at": tx.created_at.isoformat() if tx.created_at else None,
            }
            for tx in rows
        ]

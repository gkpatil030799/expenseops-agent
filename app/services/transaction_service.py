from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import ExpenseTransaction, PlaidItem, TransactionStatus, utc_now
from app.security import decrypt_secret
from app.services.agent_service import classify_transaction, transaction_display_name
from app.services.notification_service import NotificationService
from app.services.plaid_service import PlaidService
from app.services.share_calculator import (
    CustomSplitInput,
    SplitMode,
    build_custom_split_shares,
    build_custom_split_shares_by_mode,
    build_equal_split_shares,
    build_splitwise_by_shares_payload,
    cents_to_decimal_string,
    decimal_to_cents,
)
from app.services.splitwise_service import SplitwiseAPIError, SplitwiseService


class TransactionError(RuntimeError):
    pass


def can_undo_transaction(tx: ExpenseTransaction) -> bool:
    return tx.status in {
        TransactionStatus.PERSONAL.value,
        TransactionStatus.POSTED.value,
        TransactionStatus.SHARED_DRAFT.value,
    }


class TransactionService:
    def __init__(
        self,
        db: Session,
        settings: Settings | None = None,
        plaid_service: PlaidService | None = None,
        splitwise_service: SplitwiseService | None = None,
        notification_service: NotificationService | None = None,
    ):
        self.db = db
        self.settings = settings or get_settings()
        self.plaid_service = plaid_service
        self.splitwise_service = splitwise_service or SplitwiseService(self.settings)
        self.notification_service = notification_service or NotificationService(self.settings)

    def sync_item(self, item: PlaidItem) -> dict[str, int]:
        plaid = self.plaid_service or PlaidService(self.settings)
        access_token = decrypt_secret(item.access_token_encrypted)
        self._ensure_item_matches_plaid_environment(item, access_token)
        original_cursor = item.cursor
        cursor = item.cursor
        added_count = 0
        modified_count = 0
        removed_count = 0

        while True:
            response = plaid.transactions_sync(access_token=access_token, cursor=cursor)
            for tx_data in response.get("added", []):
                created = self.upsert_transaction(item, tx_data)
                if created:
                    added_count += 1
            for tx_data in response.get("modified", []):
                self.upsert_transaction(item, tx_data)
                modified_count += 1
            for removed in response.get("removed", []):
                self.mark_removed(str(removed.get("transaction_id")))
                removed_count += 1

            cursor = response.get("next_cursor")
            if not response.get("has_more"):
                break

        # Plaid docs recommend restarting pagination from the original cursor if a mutation
        # happens during pagination. The generated client raises the Plaid error before this
        # code can set a partial cursor, so we only persist after a successful full loop.
        item.cursor = cursor or original_cursor
        item.updated_at = utc_now()
        self.db.commit()
        return {"added": added_count, "modified": modified_count, "removed": removed_count}

    def sync_all_items(self) -> dict[str, dict[str, int | str]]:
        results: dict[str, dict[str, int | str]] = {}
        for item in self.db.execute(select(PlaidItem)).scalars():
            try:
                results[item.item_id] = self.sync_item(item)
            except TransactionError as exc:
                results[item.item_id] = {
                    "added": 0,
                    "modified": 0,
                    "removed": 0,
                    "skipped": 1,
                    "reason": str(exc),
                }
        return results

    def _ensure_item_matches_plaid_environment(self, item: PlaidItem, access_token: str) -> None:
        token_env = _plaid_token_environment(access_token)
        if token_env and token_env != self.settings.plaid_env:
            institution = item.institution_name or item.item_id
            raise TransactionError(
                f"Skipped Plaid item {institution}: it was linked in {token_env}, "
                f"but PLAID_ENV is {self.settings.plaid_env}. Re-link this institution "
                "in the current Plaid environment."
            )

    def upsert_transaction(self, item: PlaidItem, tx_data: dict[str, Any]) -> bool:
        plaid_transaction_id = str(tx_data["transaction_id"])
        tx = self.db.execute(
            select(ExpenseTransaction).where(
                ExpenseTransaction.plaid_transaction_id == plaid_transaction_id
            )
        ).scalar_one_or_none()

        created = tx is None
        if tx is None:
            tx = ExpenseTransaction(
                plaid_item_id=item.id,
                plaid_transaction_id=plaid_transaction_id,
                name=tx_data.get("name") or tx_data.get("merchant_name") or "Unknown transaction",
                amount_cents=decimal_to_cents(Decimal(str(tx_data.get("amount", 0)))),
            )
            self.db.add(tx)

        tx.account_id = tx_data.get("account_id")
        tx.merchant_name = tx_data.get("merchant_name")
        tx.name = tx_data.get("name") or tx.merchant_name or "Unknown transaction"
        tx.amount_cents = decimal_to_cents(Decimal(str(tx_data.get("amount", 0))))
        tx.iso_currency_code = (
            tx_data.get("iso_currency_code")
            or tx_data.get("unofficial_currency_code")
            or "USD"
        )
        tx.date = _parse_date(tx_data.get("date"))
        tx.authorized_date = _parse_date(tx_data.get("authorized_date"))
        tx.pending = bool(tx_data.get("pending", False))
        tx.payment_channel = tx_data.get("payment_channel")
        tx.category = _category_to_string(
            tx_data.get("category"), tx_data.get("personal_finance_category")
        )
        tx.raw_json = json.dumps(tx_data, default=str)
        tx.updated_at = utc_now()

        if created:
            classification = classify_transaction(tx)
            tx.status = classification.status.value
            tx.agent_question = classification.question
            self.db.flush()
            if classification.status == TransactionStatus.ASK_USER:
                self.notification_service.notify_transaction_needs_review(tx)

        self.db.commit()
        return created

    def mark_removed(self, plaid_transaction_id: str) -> None:
        tx = self.db.execute(
            select(ExpenseTransaction).where(
                ExpenseTransaction.plaid_transaction_id == plaid_transaction_id
            )
        ).scalar_one_or_none()
        if tx:
            tx.status = TransactionStatus.REMOVED.value
            tx.updated_at = utc_now()
            self.db.commit()

    def mark_personal(self, tx_id: int) -> ExpenseTransaction:
        tx = self.get_transaction(tx_id)
        if tx.splitwise_expense_id:
            raise TransactionError("Transaction already posted to Splitwise; cannot mark personal.")
        tx.status = TransactionStatus.PERSONAL.value
        tx.last_error = None
        tx.updated_at = utc_now()
        self.db.commit()
        self.db.refresh(tx)
        return tx

    def mark_shared_draft(self, tx_id: int) -> ExpenseTransaction:
        tx = self.get_transaction(tx_id)
        if tx.splitwise_expense_id:
            raise TransactionError("Transaction already posted to Splitwise; cannot draft.")
        tx.status = TransactionStatus.SHARED_DRAFT.value
        tx.last_error = None
        tx.updated_at = utc_now()
        self.db.commit()
        self.db.refresh(tx)
        return tx

    def undo_transaction(self, tx_id: int) -> ExpenseTransaction:
        tx = self.get_transaction(tx_id)
        if not can_undo_transaction(tx):
            raise TransactionError("This transaction cannot be undone.")

        if tx.splitwise_expense_id:
            try:
                self.splitwise_service.delete_expense(tx.splitwise_expense_id)
            except SplitwiseAPIError as exc:
                raise TransactionError(
                    "Could not delete the Splitwise expense. Transaction was not reverted."
                ) from exc
            tx.splitwise_expense_id = None

        tx.status = TransactionStatus.ASK_USER.value
        tx.last_error = None
        tx.updated_at = utc_now()
        self.db.commit()
        self.db.refresh(tx)
        return tx

    def create_equal_split_expense(
        self,
        *,
        tx_id: int,
        friend_user_ids: list[int],
        group_id: int | None,
        description: str | None,
        details: str | None,
        currency_code: str | None,
        confirm: bool,
        post_pending: bool,
    ) -> tuple[ExpenseTransaction, dict[str, Any]]:
        tx = self.get_transaction(tx_id)
        self._ensure_can_post(tx, post_pending=post_pending)
        payer_user_id = int(self.splitwise_service.get_current_user()["id"])
        shares = build_equal_split_shares(
            total_cents=abs(tx.amount_cents),
            payer_user_id=payer_user_id,
            participant_user_ids=[payer_user_id, *friend_user_ids],
        )
        payload = self._base_splitwise_payload(
            tx=tx,
            shares=shares,
            group_id=group_id,
            description=description,
            details=details,
            currency_code=currency_code,
        )
        return self._draft_or_post(tx, payload, confirm=confirm)

    def create_custom_split_expense(
        self,
        *,
        tx_id: int,
        participant_splits: list[CustomSplitInput] | None = None,
        split_mode: SplitMode = "exact_amounts",
        payer_included: bool = True,
        payer_user_id: int | None = None,
        owed_by_user_id: dict[int, int] | None = None,
        group_id: int | None,
        description: str | None,
        details: str | None,
        currency_code: str | None,
        confirm: bool,
        post_pending: bool,
    ) -> tuple[ExpenseTransaction, dict[str, Any]]:
        tx = self.get_transaction(tx_id)
        self._ensure_can_post(tx, post_pending=post_pending)
        resolved_payer_user_id = payer_user_id or int(
            self.splitwise_service.get_current_user()["id"]
        )
        if owed_by_user_id is not None:
            shares = build_custom_split_shares(
                total_cents=abs(tx.amount_cents),
                payer_user_id=resolved_payer_user_id,
                owed_by_user_id=owed_by_user_id,
            )
        else:
            shares = build_custom_split_shares_by_mode(
                total_cents=abs(tx.amount_cents),
                payer_user_id=resolved_payer_user_id,
                payer_included=payer_included,
                split_mode=split_mode,
                participant_splits=participant_splits or [],
            )
        payload = self._base_splitwise_payload(
            tx=tx,
            shares=shares,
            group_id=group_id,
            description=description,
            details=details,
            currency_code=currency_code,
        )
        return self._draft_or_post(tx, payload, confirm=confirm)

    def get_transaction(self, tx_id: int) -> ExpenseTransaction:
        tx = self.db.get(ExpenseTransaction, tx_id)
        if tx is None:
            raise TransactionError(f"Transaction {tx_id} not found")
        return tx

    def _base_splitwise_payload(
        self, *, tx: ExpenseTransaction, shares, group_id, description, details, currency_code
    ):
        default_details = (
            details
            or f"Created by ExpenseOps from Plaid transaction {tx.plaid_transaction_id}. "
            "Review before settling."
        )
        return build_splitwise_by_shares_payload(
            total_cents=abs(tx.amount_cents),
            description=description or transaction_display_name(tx),
            details=default_details,
            date_iso=_date_to_splitwise_iso(tx.date),
            currency_code=currency_code or tx.iso_currency_code or "USD",
            shares=shares,
            group_id=group_id,
        )

    def _draft_or_post(
        self, tx: ExpenseTransaction, payload: dict[str, Any], *, confirm: bool
    ) -> tuple[ExpenseTransaction, dict[str, Any]]:
        tx.splitwise_payload_json = json.dumps(payload, default=str)
        if not confirm:
            tx.status = TransactionStatus.SHARED_DRAFT.value
            tx.last_error = None
            tx.updated_at = utc_now()
            self.db.commit()
            self.db.refresh(tx)
            return tx, {"draft": True, "payload": payload}

        if tx.splitwise_expense_id:
            raise TransactionError(
                f"Transaction already posted to Splitwise expense {tx.splitwise_expense_id}."
            )

        try:
            response = self.splitwise_service.create_expense(payload)
            expense_id = str(response.get("expenses", [{}])[0].get("id", "")) or None
            tx.status = TransactionStatus.POSTED.value
            tx.splitwise_expense_id = expense_id
            tx.last_error = None
            tx.updated_at = utc_now()
            self.db.commit()
            self.db.refresh(tx)
            self.notification_service.notify_splitwise_posted(tx, expense_id)
            return tx, response
        except SplitwiseAPIError as exc:
            tx.status = TransactionStatus.ERROR.value
            tx.last_error = str(exc)
            tx.updated_at = utc_now()
            self.db.commit()
            raise

    def _ensure_can_post(self, tx: ExpenseTransaction, *, post_pending: bool) -> None:
        if tx.status == TransactionStatus.REMOVED.value:
            raise TransactionError("Cannot post a transaction Plaid marked as removed.")
        if tx.amount_cents <= 0:
            raise TransactionError("Refunds/credits are not posted to Splitwise by default.")
        if tx.pending and not (post_pending or self.settings.allow_posting_pending_transactions):
            raise TransactionError(
                "This transaction is still pending. Approve it as a draft or pass "
                "post_pending=true "
                "if you intentionally want to post before the final amount settles."
            )


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def _date_to_splitwise_iso(value: date | None) -> str | None:
    if not value:
        return None
    return datetime(value.year, value.month, value.day, tzinfo=UTC).isoformat().replace(
        "+00:00", "Z"
    )


def _category_to_string(category: Any, personal_finance_category: Any) -> str | None:
    if personal_finance_category and isinstance(personal_finance_category, dict):
        primary = personal_finance_category.get("primary")
        detailed = personal_finance_category.get("detailed")
        return " / ".join(part for part in [primary, detailed] if part)
    if isinstance(category, list):
        return " / ".join(map(str, category))
    return str(category) if category else None


def _plaid_token_environment(access_token: str) -> str | None:
    if access_token.startswith("access-sandbox-"):
        return "sandbox"
    if access_token.startswith("access-production-"):
        return "production"
    if access_token.startswith("access-development-"):
        return "development"
    return None


def transaction_amount_string(tx: ExpenseTransaction) -> str:
    return cents_to_decimal_string(abs(tx.amount_cents))

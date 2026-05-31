from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.logging_config import log_event
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

logger = logging.getLogger(__name__)

_SCENARIO_TRACE_PATTERN = re.compile(r"\[trace:(scenario_([a-z0-9_]+)_\d{8}_[a-f0-9]+)\]")
_SANDBOX_TRACE_PATTERN = re.compile(r"\[trace:((?:scenario|reliability)_[^\]]+)\]")
_NO_NOTIFY_SCENARIO_IDS = {"create_only_no_import"}
ACTIONABLE_REVIEW_STATUSES = {
    TransactionStatus.ASK_USER.value,
    TransactionStatus.SHARED_DRAFT.value,
}
RESOLVED_TRANSACTION_STATUSES = {
    TransactionStatus.PERSONAL.value,
    TransactionStatus.POSTED.value,
    TransactionStatus.REMOVED.value,
    TransactionStatus.ERROR.value,
    "settled",
    "splitwise_posted",
    "ignored",
    "resolved",
}
_NOTIFICATION_CLAIM_LOCK = threading.Lock()


class TransactionError(RuntimeError):
    pass


@dataclass(frozen=True)
class TransactionUpsertResult:
    created: bool
    notification_eligible: bool
    notification_sent: bool
    tx_id: int | None = None

    def __bool__(self) -> bool:
        return self.created


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
        log_event(logger, "plaid_sync_started", plaid_item_db_id=item.id)
        plaid = self.plaid_service or PlaidService(self.settings)
        access_token = decrypt_secret(item.access_token_encrypted)
        self._ensure_item_matches_plaid_environment(item, access_token)
        original_cursor = item.cursor
        cursor = item.cursor
        added_count = 0
        modified_count = 0
        removed_count = 0
        notification_eligible_count = 0
        notification_sent_count = 0
        notification_skipped_count = 0

        while True:
            response = plaid.transactions_sync(access_token=access_token, cursor=cursor)
            for tx_data in response.get("added", []):
                self._log_added_transaction_seen(tx_data)
                result = self._upsert_transaction_with_result(item, tx_data)
                notification_eligible_count += int(result.notification_eligible)
                notification_sent_count += int(result.notification_sent)
                notification_skipped_count += int(
                    result.notification_eligible and not result.notification_sent
                )
                if result.created:
                    added_count += 1
            for tx_data in response.get("modified", []):
                result = self._upsert_transaction_with_result(item, tx_data)
                notification_eligible_count += int(result.notification_eligible)
                notification_sent_count += int(result.notification_sent)
                notification_skipped_count += int(
                    result.notification_eligible and not result.notification_sent
                )
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
        log_event(
            logger,
            "plaid_sync_completed",
            plaid_item_db_id=item.id,
            added_count=added_count,
            modified_count=modified_count,
            removed_count=removed_count,
            notification_eligible_count=notification_eligible_count,
            notification_sent_count=notification_sent_count,
            notification_skipped_count=notification_skipped_count,
        )
        return {
            "added": added_count,
            "modified": modified_count,
            "removed": removed_count,
            "notification_eligible": notification_eligible_count,
            "notification_sent": notification_sent_count,
            "notification_skipped": notification_skipped_count,
        }

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

    def upsert_transaction(
        self,
        item: PlaidItem,
        tx_data: dict[str, Any],
    ) -> bool:
        return self._upsert_transaction_with_result(item, tx_data).created

    def _upsert_transaction_with_result(
        self,
        item: PlaidItem,
        tx_data: dict[str, Any],
    ) -> TransactionUpsertResult:
        plaid_transaction_id = str(tx_data["transaction_id"])
        try:
            return self._upsert_transaction_with_result_once(item, tx_data)
        except IntegrityError as exc:
            self.db.rollback()
            detail = _safe_integrity_error_details(exc)
            log_event(
                logger,
                "transaction_upsert_integrity_error",
                level=logging.WARNING,
                plaid_item_db_id=item.id,
                plaid_transaction_id=plaid_transaction_id,
                account_id=tx_data.get("account_id"),
                **detail,
            )
            if _is_unique_transaction_race(detail):
                return self._retry_duplicate_transaction_upsert(item, tx_data)
            raise

    def _upsert_transaction_with_result_once(
        self,
        item: PlaidItem,
        tx_data: dict[str, Any],
    ) -> TransactionUpsertResult:
        plaid_transaction_id = str(tx_data["transaction_id"])
        tx = self._get_transaction_by_plaid_id(plaid_transaction_id)

        created = tx is None
        previous_pending = bool(tx.pending) if tx is not None else None
        previous_status = str(tx.status) if tx is not None else None
        if tx is None:
            tx = ExpenseTransaction(
                plaid_item_id=item.id,
                plaid_transaction_id=plaid_transaction_id,
                name=tx_data.get("name") or tx_data.get("merchant_name") or "Unknown transaction",
                amount_cents=decimal_to_cents(Decimal(str(tx_data.get("amount", 0)))),
            )
            self.db.add(tx)

        self._apply_plaid_transaction_fields(tx, item, tx_data)
        if not created and previous_status and _is_resolved_transaction_status(previous_status):
            log_event(
                logger,
                "transaction_status_preserved_on_plaid_update",
                tx_id=tx.id,
                plaid_transaction_id=tx.plaid_transaction_id,
                status=tx.status,
                previous_status=previous_status,
                splitwise_expense_id=tx.splitwise_expense_id,
            )

        if created:
            self.db.flush()
            log_event(
                logger,
                "transaction_classification_started",
                tx_id=tx.id,
                plaid_item_db_id=item.id,
            )
            classification = classify_transaction(tx)
            tx.status = classification.status.value
            tx.agent_question = classification.question
            log_event(
                logger,
                "transaction_classified",
                tx_id=tx.id,
                status=tx.status,
                reason=classification.reason,
                source="rule",
            )

        notification_eligible = self._should_notify_transaction_needs_review(
            tx,
            created=created,
            previous_pending=previous_pending,
        )
        self.db.commit()
        self.db.refresh(tx)

        notification_sent = False
        if notification_eligible:
            notification_sent = self._attempt_review_notification(tx)
            if previous_pending is True and not tx.pending:
                log_event(
                    logger,
                    "pending_transaction_settled_notification_sent",
                    tx_id=tx.id,
                    plaid_transaction_id=tx.plaid_transaction_id,
                )
            self.db.commit()
        else:
            skip_reason = self._notification_skip_reason(tx)
            if skip_reason == "scenario_create_only_no_import":
                self._skip_scenario_create_only_notification(tx)
                self.db.commit()
            self._log_notification_skip(tx, skip_reason)
            log_event(
                logger,
                "transaction_notification_skipped",
                tx_id=tx.id,
                reason=skip_reason,
                status=tx.status,
                pending=tx.pending,
            )
        return TransactionUpsertResult(
            created=created,
            notification_eligible=notification_eligible,
            notification_sent=notification_sent,
            tx_id=tx.id,
        )

    def _retry_duplicate_transaction_upsert(
        self,
        item: PlaidItem,
        tx_data: dict[str, Any],
    ) -> TransactionUpsertResult:
        plaid_transaction_id = str(tx_data["transaction_id"])
        tx = self._get_transaction_by_plaid_id(plaid_transaction_id)
        if tx is None:
            raise TransactionError(
                "Transaction insert conflicted, but the existing transaction could not be loaded."
            )
        previous_pending = bool(tx.pending)
        previous_status = str(tx.status)
        self._apply_plaid_transaction_fields(tx, item, tx_data)
        if _is_resolved_transaction_status(previous_status):
            log_event(
                logger,
                "transaction_status_preserved_on_plaid_update",
                tx_id=tx.id,
                plaid_transaction_id=tx.plaid_transaction_id,
                status=tx.status,
                previous_status=previous_status,
                splitwise_expense_id=tx.splitwise_expense_id,
            )
        notification_eligible = self._should_notify_transaction_needs_review(
            tx,
            created=False,
            previous_pending=previous_pending,
        )
        self.db.commit()
        self.db.refresh(tx)
        notification_sent = False
        if notification_eligible:
            notification_sent = self._attempt_review_notification(tx)
            self.db.commit()
        else:
            self._log_notification_skip(tx, self._notification_skip_reason(tx))
            log_event(
                logger,
                "transaction_upsert_skipped_duplicate",
                tx_id=tx.id,
                plaid_transaction_id=plaid_transaction_id,
                reason=self._notification_skip_reason(tx),
            )
        return TransactionUpsertResult(
            created=False,
            notification_eligible=notification_eligible,
            notification_sent=notification_sent,
            tx_id=tx.id,
        )

    def _get_transaction_by_plaid_id(self, plaid_transaction_id: str) -> ExpenseTransaction | None:
        return self.db.execute(
            select(ExpenseTransaction).where(
                ExpenseTransaction.plaid_transaction_id == plaid_transaction_id
            )
        ).scalar_one_or_none()

    def _apply_plaid_transaction_fields(
        self,
        tx: ExpenseTransaction,
        item: PlaidItem,
        tx_data: dict[str, Any],
    ) -> None:
        tx.plaid_item_id = item.id
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

    def _should_notify_transaction_needs_review(
        self,
        tx: ExpenseTransaction,
        *,
        created: bool,
        previous_pending: bool | None,
    ) -> bool:
        if not self.should_notify_for_review(tx):
            return False
        return created or previous_pending is True

    def should_notify_for_review(self, tx: ExpenseTransaction) -> bool:
        if tx.pending:
            return False
        if tx.review_notification_sent_at is not None:
            return False
        if tx.splitwise_expense_id:
            return False
        if _scenario_id_for_no_notify(tx):
            return False
        return str(tx.status) in ACTIONABLE_REVIEW_STATUSES

    def _notification_skip_reason(self, tx: ExpenseTransaction) -> str:
        if _scenario_id_for_no_notify(tx):
            return "scenario_create_only_no_import"
        if tx.pending:
            return "transaction_pending"
        if tx.status == TransactionStatus.PERSONAL.value:
            return "personal"
        if tx.status == TransactionStatus.POSTED.value or tx.splitwise_expense_id:
            return "posted"
        if _is_resolved_transaction_status(str(tx.status)):
            return "resolved"
        if str(tx.status) not in ACTIONABLE_REVIEW_STATUSES:
            return "not_actionable"
        if tx.review_notification_sent_at is not None:
            return "review_notification_already_sent"
        return "not_new_or_pending_transition"

    def _log_notification_skip(self, tx: ExpenseTransaction, skip_reason: str) -> None:
        event_by_reason = {
            "resolved": "transaction_notification_skipped_resolved",
            "personal": "transaction_notification_skipped_personal",
            "posted": "transaction_notification_skipped_posted",
            "not_actionable": "transaction_notification_skipped_not_actionable",
        }
        event_type = event_by_reason.get(skip_reason)
        if not event_type:
            return
        log_event(
            logger,
            event_type,
            tx_id=tx.id,
            plaid_transaction_id=tx.plaid_transaction_id,
            status=tx.status,
            pending=tx.pending,
            splitwise_expense_id=tx.splitwise_expense_id,
        )

    def _notify_ready_transactions_for_item(
        self,
        item: PlaidItem,
        *,
        exclude_tx_ids: set[int] | None = None,
    ) -> dict[str, int]:
        exclude_tx_ids = exclude_tx_ids or set()
        rows = (
            self.db.execute(
                select(ExpenseTransaction)
                .where(ExpenseTransaction.plaid_item_id == item.id)
                .where(ExpenseTransaction.status.in_(ACTIONABLE_REVIEW_STATUSES))
                .where(ExpenseTransaction.pending.is_(False))
                .where(ExpenseTransaction.splitwise_expense_id.is_(None))
                .where(ExpenseTransaction.review_notification_sent_at.is_(None))
                .order_by(ExpenseTransaction.created_at.asc())
            )
            .scalars()
            .all()
        )
        sent = 0
        skipped = 0
        for tx in rows:
            if tx.id in exclude_tx_ids:
                continue
            if _scenario_id_for_no_notify(tx):
                self._skip_scenario_create_only_notification(tx)
                skipped += 1
                continue
            if self._attempt_review_notification(tx):
                sent += 1
            else:
                skipped += 1
        eligible = len([tx for tx in rows if tx.id not in exclude_tx_ids])
        if eligible:
            self.db.commit()
        return {"eligible": eligible, "sent": sent, "skipped": skipped}

    def _skip_scenario_create_only_notification(self, tx: ExpenseTransaction) -> None:
        trace_id, scenario_id = _scenario_trace_metadata(tx)
        tx.review_notification_sent_at = tx.review_notification_sent_at or utc_now()
        tx.updated_at = utc_now()
        log_event(
            logger,
            "scenario_create_only_imported_later_skipped_notification",
            transaction_id=tx.id,
            plaid_transaction_id=tx.plaid_transaction_id,
            trace_id=trace_id,
            scenario_id=scenario_id,
        )
        self._sandbox_telegram_event(
            tx,
            event_type="scenario_create_only_imported_later_skipped_notification",
            status="info",
            payload={
                "transaction_id": tx.id,
                "plaid_transaction_id": tx.plaid_transaction_id,
                "trace_id": trace_id,
                "scenario_id": scenario_id,
            },
        )

    def _attempt_review_notification(self, tx: ExpenseTransaction) -> bool:
        log_event(
            logger,
            "telegram_notification_claim_started",
            transaction_id=tx.id,
            plaid_transaction_id=tx.plaid_transaction_id,
            status=tx.status,
            pending=tx.pending,
        )
        claim_result = self._claim_review_notification(tx)
        if not claim_result:
            return False
        log_event(
            logger,
            "telegram_notification_send_started",
            transaction_id=tx.id,
            plaid_transaction_id=tx.plaid_transaction_id,
        )
        self._sandbox_telegram_event(
            tx,
            event_type="sandbox_telegram_send_started",
            status="started",
        )
        if _consume_sandbox_transaction_fault(tx, "fail_next_telegram_send"):
            log_event(
                logger,
                "telegram_notification_send_failed",
                level=logging.WARNING,
                transaction_id=tx.id,
                plaid_transaction_id=tx.plaid_transaction_id,
                reason="sandbox_fault_fail_next_telegram_send",
            )
            self._sandbox_telegram_event(
                tx,
                event_type="sandbox_telegram_send_failed",
                status="failed",
                payload={"reason": "sandbox_fault_fail_next_telegram_send"},
            )
            return False
        notification_result = self.notification_service.notify_transaction_needs_review(tx)
        notification_sent = notification_result is not False
        if notification_sent:
            log_event(
                logger,
                "telegram_notification_send_succeeded",
                transaction_id=tx.id,
                plaid_transaction_id=tx.plaid_transaction_id,
            )
            self._sandbox_telegram_event(
                tx,
                event_type="sandbox_telegram_send_succeeded",
                status="succeeded",
            )
        else:
            log_event(
                logger,
                "telegram_notification_send_failed",
                level=logging.WARNING,
                transaction_id=tx.id,
                plaid_transaction_id=tx.plaid_transaction_id,
                reason="telegram_send_returned_false",
            )
            self._sandbox_telegram_event(
                tx,
                event_type="sandbox_telegram_send_failed",
                status="failed",
                payload={"reason": "telegram_send_returned_false"},
            )
        return notification_sent

    def _claim_review_notification(self, tx: ExpenseTransaction) -> bool:
        with _NOTIFICATION_CLAIM_LOCK:
            self.db.refresh(tx)
            if not self.should_notify_for_review(tx):
                reason = self._notification_skip_reason(tx)
                event_type = (
                    "transaction_notification_claim_skipped_already_sent"
                    if reason == "review_notification_already_sent"
                    else "transaction_notification_skipped_not_actionable"
                )
                log_event(
                    logger,
                    event_type,
                    transaction_id=tx.id,
                    plaid_transaction_id=tx.plaid_transaction_id,
                    reason=reason,
                    status=tx.status,
                    pending=tx.pending,
                    splitwise_expense_id=tx.splitwise_expense_id,
                    review_notification_sent_at=tx.review_notification_sent_at,
                )
                if reason == "review_notification_already_sent":
                    log_event(
                        logger,
                        "telegram_notification_skipped_duplicate",
                        transaction_id=tx.id,
                        plaid_transaction_id=tx.plaid_transaction_id,
                        reason="review_notification_already_claimed",
                        status=tx.status,
                        pending=tx.pending,
                        review_notification_sent_at=tx.review_notification_sent_at,
                    )
                return False

            duplicate_tx = self._already_notified_duplicate(tx)
            if duplicate_tx:
                claimed_at = utc_now()
                self._mark_notification_claimed_without_send(tx, claimed_at)
                log_event(
                    logger,
                    "transaction_notification_claim_skipped_already_sent",
                    transaction_id=tx.id,
                    plaid_transaction_id=tx.plaid_transaction_id,
                    duplicate_transaction_id=duplicate_tx.id,
                    duplicate_plaid_transaction_id=duplicate_tx.plaid_transaction_id,
                    reason="duplicate_transaction_already_notified",
                    status=tx.status,
                    pending=tx.pending,
                )
                self._sandbox_telegram_event(
                    tx,
                    event_type="sandbox_telegram_send_skipped_duplicate",
                    status="info",
                    payload={"reason": "duplicate_transaction_already_notified"},
                )
                return False

            claimed_at = utc_now()
            result = self.db.execute(
                update(ExpenseTransaction)
                .where(ExpenseTransaction.id == tx.id)
                .where(ExpenseTransaction.review_notification_sent_at.is_(None))
                .where(ExpenseTransaction.status.in_(ACTIONABLE_REVIEW_STATUSES))
                .where(ExpenseTransaction.pending.is_(False))
                .where(ExpenseTransaction.splitwise_expense_id.is_(None))
                .values(review_notification_sent_at=claimed_at, updated_at=utc_now())
            )
            if result.rowcount != 1:
                self.db.rollback()
                self.db.refresh(tx)
                log_event(
                    logger,
                    "transaction_notification_skipped_concurrent_claim",
                    transaction_id=tx.id,
                    plaid_transaction_id=tx.plaid_transaction_id,
                    reason="review_notification_already_claimed",
                    status=tx.status,
                    pending=tx.pending,
                    review_notification_sent_at=tx.review_notification_sent_at,
                )
                log_event(
                    logger,
                    "telegram_notification_skipped_duplicate",
                    transaction_id=tx.id,
                    plaid_transaction_id=tx.plaid_transaction_id,
                    reason="review_notification_already_claimed",
                    status=tx.status,
                    pending=tx.pending,
                    review_notification_sent_at=tx.review_notification_sent_at,
                )
                self._sandbox_telegram_event(
                    tx,
                    event_type="sandbox_telegram_send_skipped_duplicate",
                    status="info",
                    payload={"reason": "review_notification_already_claimed"},
                )
                return False
            self.db.commit()
            self.db.refresh(tx)
            log_event(
                logger,
                "transaction_notification_claimed",
                transaction_id=tx.id,
                plaid_transaction_id=tx.plaid_transaction_id,
                review_notification_sent_at=tx.review_notification_sent_at,
            )
            log_event(
                logger,
                "telegram_notification_claim_succeeded",
                transaction_id=tx.id,
                plaid_transaction_id=tx.plaid_transaction_id,
                review_notification_sent_at=tx.review_notification_sent_at,
            )
            return True

    def _mark_notification_claimed_without_send(
        self,
        tx: ExpenseTransaction,
        claimed_at: datetime,
    ) -> None:
        self.db.execute(
            update(ExpenseTransaction)
            .where(ExpenseTransaction.id == tx.id)
            .where(ExpenseTransaction.review_notification_sent_at.is_(None))
            .values(review_notification_sent_at=claimed_at, updated_at=utc_now())
        )
        self.db.commit()
        self.db.refresh(tx)

    def _already_notified_duplicate(
        self,
        tx: ExpenseTransaction,
    ) -> ExpenseTransaction | None:
        institution_name = self._institution_name_for_transaction(tx)
        display_name = _notification_dedupe_name(tx)
        if not institution_name or not display_name:
            return None
        return (
            self.db.execute(
                select(ExpenseTransaction)
                .join(PlaidItem, PlaidItem.id == ExpenseTransaction.plaid_item_id)
                .where(ExpenseTransaction.id != tx.id)
                .where(ExpenseTransaction.review_notification_sent_at.is_not(None))
                .where(ExpenseTransaction.amount_cents == tx.amount_cents)
                .where(ExpenseTransaction.iso_currency_code == tx.iso_currency_code)
                .where(ExpenseTransaction.date == tx.date)
                .where(ExpenseTransaction.pending == tx.pending)
                .where(func.lower(PlaidItem.institution_name) == institution_name.lower())
                .where(
                    func.lower(
                        func.coalesce(
                            ExpenseTransaction.merchant_name,
                            ExpenseTransaction.name,
                        )
                    )
                    == display_name
                )
                .order_by(ExpenseTransaction.review_notification_sent_at.asc())
                .limit(1)
            )
            .scalars()
            .first()
        )

    def _institution_name_for_transaction(self, tx: ExpenseTransaction) -> str | None:
        if tx.plaid_item and tx.plaid_item.institution_name:
            return tx.plaid_item.institution_name
        item = self.db.get(PlaidItem, tx.plaid_item_id)
        return item.institution_name if item else None

    def _sandbox_telegram_event(
        self,
        tx: ExpenseTransaction,
        *,
        event_type: str,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            from sandbox.backend.webhook_hooks import maybe_log_sandbox_telegram_event

            maybe_log_sandbox_telegram_event(
                tx,
                event_type=event_type,
                status=status,
                payload=payload,
            )
        except Exception:
            return

    def _log_added_transaction_seen(self, tx_data: dict[str, Any]) -> None:
        if self.settings.environment != "local":
            return
        log_event(
            logger,
            "plaid_sync_added_transaction_seen",
            transaction_id=tx_data.get("transaction_id"),
            name=tx_data.get("name"),
            merchant=tx_data.get("merchant_name"),
            pending=bool(tx_data.get("pending", False)),
            amount=tx_data.get("amount"),
            date=tx_data.get("date"),
        )

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
        log_event(
            logger,
            "splitwise_equal_split_started",
            tx_id=tx_id,
            group_id=group_id,
            participant_count=len(friend_user_ids),
        )
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
        log_event(
            logger,
            "splitwise_custom_split_started",
            tx_id=tx_id,
            group_id=group_id,
            split_mode=split_mode,
        )
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
            log_event(
                logger,
                "splitwise_expense_posted",
                tx_id=tx.id,
                splitwise_expense_id=expense_id,
            )
            return tx, response
        except SplitwiseAPIError as exc:
            log_event(
                logger,
                "splitwise_expense_post_failed",
                level=logging.WARNING,
                tx_id=tx.id,
                reason="splitwise_api_error",
            )
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


def _is_resolved_transaction_status(status: str | None) -> bool:
    return bool(status) and status in RESOLVED_TRANSACTION_STATUSES


def _notification_dedupe_name(tx: ExpenseTransaction) -> str | None:
    value = tx.merchant_name or tx.name
    normalized = " ".join(str(value or "").lower().split())
    return normalized or None


def _plaid_token_environment(access_token: str) -> str | None:
    if access_token.startswith("access-sandbox-"):
        return "sandbox"
    if access_token.startswith("access-production-"):
        return "production"
    if access_token.startswith("access-development-"):
        return "development"
    return None


def _scenario_trace_metadata(tx: ExpenseTransaction) -> tuple[str | None, str | None]:
    for value in (tx.name, tx.merchant_name):
        if not value:
            continue
        match = _SCENARIO_TRACE_PATTERN.search(str(value))
        if match:
            return match.group(1), match.group(2)
    return None, None


def _sandbox_trace_id_from_transaction(tx: ExpenseTransaction) -> str | None:
    for value in (tx.name, tx.merchant_name):
        if not value:
            continue
        match = _SANDBOX_TRACE_PATTERN.search(str(value))
        if match:
            return match.group(1)
    return None


def _consume_sandbox_transaction_fault(tx: ExpenseTransaction, fault_name: str) -> bool:
    trace_id = _sandbox_trace_id_from_transaction(tx)
    if not trace_id:
        return False
    try:
        from sandbox.backend.config import get_sandbox_settings
        from sandbox.backend.event_store import SandboxEventStore
        from sandbox.backend.fault_injection import fault_store

        settings = get_sandbox_settings()
        if not settings.enabled or settings.plaid_env != "sandbox":
            return False
        return fault_store.consume(
            name=fault_name,
            trace_id=trace_id,
            event_store=SandboxEventStore(),
        )
    except Exception:
        return False


def _scenario_id_for_no_notify(tx: ExpenseTransaction) -> str | None:
    _trace_id, scenario_id = _scenario_trace_metadata(tx)
    if scenario_id in _NO_NOTIFY_SCENARIO_IDS:
        return scenario_id
    return None


def _safe_integrity_error_details(exc: IntegrityError) -> dict[str, str | None]:
    original = str(getattr(exc, "orig", "") or exc)
    sanitized = " ".join(original.split())
    details: dict[str, str | None] = {
        "exception_class": type(exc).__name__,
        "original_error": sanitized[:500],
        "constraint_name": None,
        "table_name": None,
        "column_name": None,
    }
    marker = "UNIQUE constraint failed:"
    if marker in sanitized:
        details["constraint_name"] = "unique"
        target = sanitized.split(marker, 1)[1].strip().split()[0].strip(",")
        table, _, column = target.partition(".")
        details["table_name"] = table or None
        details["column_name"] = column or None
    marker = "NOT NULL constraint failed:"
    if marker in sanitized:
        details["constraint_name"] = "not_null"
        target = sanitized.split(marker, 1)[1].strip().split()[0].strip(",")
        table, _, column = target.partition(".")
        details["table_name"] = table or None
        details["column_name"] = column or None
    if "FOREIGN KEY constraint failed" in sanitized:
        details["constraint_name"] = "foreign_key"
    return details


def _is_unique_transaction_race(details: dict[str, str | None]) -> bool:
    return (
        details.get("constraint_name") == "unique"
        and details.get("table_name") == "expense_transactions"
        and details.get("column_name") == "plaid_transaction_id"
    )


def transaction_amount_string(tx: ExpenseTransaction) -> str:
    return cents_to_decimal_string(abs(tx.amount_cents))

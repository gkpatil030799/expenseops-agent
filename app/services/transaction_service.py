from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.job_tenancy import telegram_settings_for_workspace
from app.logging_config import get_trace_id, log_event
from app.models import (
    ClassificationAuthority,
    ExpenseTransaction,
    FinancialOperation,
    PlaidItem,
    SplitwiseIntegration,
    TelegramIdentity,
    TransactionStatus,
    WorkspaceMembership,
    utc_now,
)
from app.security import SecretConfigurationError, decrypt_secret
from app.services.agent_service import (
    classify_transaction,
    friend_display_name,
    transaction_display_name,
)
from app.services.autonomous_classification_service import AutonomousClassificationService
from app.services.managed_auth_service import record_audit
from app.services.notification_service import NotificationService
from app.services.outbox_service import enqueue_outbox_event
from app.services.plaid_service import PlaidService
from app.services.receipt_transaction_reconciliation_service import (
    ReceiptTransactionReconciliationService,
)
from app.services.share_calculator import (
    CustomSplitInput,
    SplitMode,
    SplitShare,
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
        TransactionStatus.RECONCILIATION_REQUIRED.value,
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
        session_info = getattr(db, "info", {})
        workspace_id = session_info.get("workspace_id") if settings is None else None
        if workspace_id is not None:
            self.settings = telegram_settings_for_workspace(
                db,
                int(workspace_id),
                self.settings,
                user_id=session_info.get("user_id"),
            )
        self.plaid_service = plaid_service
        self._splitwise_service_injected = splitwise_service is not None
        self.splitwise_service = splitwise_service or SplitwiseService(self.settings)
        self._notification_service_injected = notification_service is not None
        self.notification_service = notification_service or NotificationService(self.settings)

    def sync_item(self, item: PlaidItem) -> dict[str, int]:
        log_event(logger, "plaid_sync_started", plaid_item_db_id=item.id)
        if not self._notification_service_injected and item.workspace_id is not None:
            recipient_settings = telegram_settings_for_workspace(
                self.db,
                item.workspace_id,
                self.settings,
                user_id=item.owner_user_id,
            )
            self.notification_service = NotificationService(recipient_settings)
        if not self._splitwise_service_injected:
            self.splitwise_service = self._splitwise_service_for_item_owner(item)
        plaid = self.plaid_service or PlaidService(self.settings)
        if item.enabled is False or not item.access_token_encrypted:
            raise TransactionError("Plaid connection is disconnected")
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
        attempted_notification_tx_ids: set[int] = set()

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
                if result.notification_eligible and result.tx_id is not None:
                    attempted_notification_tx_ids.add(result.tx_id)
                if result.created:
                    added_count += 1
            for tx_data in response.get("modified", []):
                result = self._upsert_transaction_with_result(item, tx_data)
                notification_eligible_count += int(result.notification_eligible)
                notification_sent_count += int(result.notification_sent)
                notification_skipped_count += int(
                    result.notification_eligible and not result.notification_sent
                )
                if result.notification_eligible and result.tx_id is not None:
                    attempted_notification_tx_ids.add(result.tx_id)
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
        retried = self._notify_ready_transactions_for_item(
            item,
            exclude_tx_ids=attempted_notification_tx_ids,
        )
        notification_eligible_count += retried["eligible"]
        notification_sent_count += retried["sent"]
        notification_skipped_count += retried["skipped"]
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
        for item in self.db.execute(select(PlaidItem).where(PlaidItem.enabled.is_(True))).scalars():
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
        previous_amount_cents = int(tx.amount_cents) if tx is not None else None
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

        self._link_pending_replacement(item, tx, tx_data)
        ReceiptTransactionReconciliationService(self.db).reconcile_for_transaction(tx)
        self._classify_transaction_nonblocking(tx)
        if (
            not created
            and previous_amount_cents is not None
            and previous_amount_cents != tx.amount_cents
            and tx.splitwise_expense_id
        ):
            self._reconcile_posted_amount_change(
                tx,
                previous_amount_cents=previous_amount_cents,
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
        self._link_pending_replacement(item, tx, tx_data)
        ReceiptTransactionReconciliationService(self.db).reconcile_for_transaction(tx)
        self._classify_transaction_nonblocking(tx)
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

    def _classify_transaction_nonblocking(self, tx: ExpenseTransaction) -> None:
        """Apply internal taxonomy without making Plaid ingestion depend on it."""

        if not self.settings.autonomous_classification_enabled:
            return
        try:
            with self.db.begin_nested():
                classification = AutonomousClassificationService(
                    self.db, self.settings
                ).classify_transaction(tx, commit=False)
                if classification.failures:
                    # The classifier reports bounded internal apply failures in
                    # its summary. Roll back the savepoint and persist the same
                    # durable retry marker used for raised storage failures.
                    raise ValueError("autonomous_transaction_classification_incomplete")
        except (SQLAlchemyError, ValueError) as exc:
            if tx.classification_authority != ClassificationAuthority.USER_CORRECTION.value:
                # The surrounding Plaid upsert remains authoritative and must
                # not fail because classification is unavailable.  Persist a
                # bounded due marker in that same commit so the finalizer can
                # retry exactly this still-unclassified transaction.
                tx.classification_retry_at = utc_now()
            log_event(
                logger,
                "transaction_autonomous_classification_deferred",
                error_type=type(exc).__name__,
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
            tx_data.get("iso_currency_code") or tx_data.get("unofficial_currency_code") or "USD"
        )
        tx.date = _parse_date(tx_data.get("date"))
        tx.authorized_date = _parse_date(tx_data.get("authorized_date"))
        tx.pending = bool(tx_data.get("pending", False))
        tx.payment_channel = tx_data.get("payment_channel")
        provider_category = _category_to_string(
            tx_data.get("category"), tx_data.get("personal_finance_category")
        )
        # Keep the legacy projection during the consumer migration, while the
        # raw provider field remains separate from canonical user-correctable
        # classification fields.
        tx.category = provider_category
        tx.provider_category = provider_category
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
        if tx.review_notification_queued_at is not None:
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
        if tx.review_notification_queued_at is not None:
            return "review_notification_already_queued"
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
                .where(ExpenseTransaction.review_notification_queued_at.is_(None))
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
        if self.settings.is_production_mode and not self._notification_service_injected:
            return self._enqueue_review_notification(tx)
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
            self._release_notification_claim(tx)
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
            self._release_notification_claim(tx)
        return notification_sent

    def _enqueue_review_notification(self, tx: ExpenseTransaction) -> bool:
        self.db.refresh(tx)
        if not self.should_notify_for_review(tx):
            return False
        duplicate_tx = self._already_notified_duplicate(tx)
        if duplicate_tx is not None:
            self._mark_notification_claimed_without_send(tx, utc_now())
            return False
        queued_at = utc_now()
        enqueue_outbox_event(
            self.db,
            workspace_id=tx.workspace_id,
            event_type="telegram.review_transaction",
            aggregate_type="expense_transaction",
            aggregate_id=tx.id,
            dedupe_key=f"telegram-review:{tx.id}",
            payload={"transaction_id": tx.id},
        )
        tx.review_notification_queued_at = queued_at
        tx.updated_at = queued_at
        self.db.commit()
        self.db.refresh(tx)
        log_event(
            logger,
            "telegram_notification_queued",
            transaction_id=tx.id,
            plaid_transaction_id=tx.plaid_transaction_id,
        )
        return True

    def _release_notification_claim(self, tx: ExpenseTransaction) -> None:
        self.db.execute(
            update(ExpenseTransaction)
            .where(ExpenseTransaction.id == tx.id)
            .values(review_notification_sent_at=None, updated_at=utc_now())
        )
        self.db.commit()
        self.db.refresh(tx)
        log_event(
            logger,
            "telegram_notification_claim_released",
            transaction_id=tx.id,
            plaid_transaction_id=tx.plaid_transaction_id,
            reason="delivery_failed",
        )

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
                .where(
                    or_(
                        ExpenseTransaction.review_notification_sent_at.is_not(None),
                        ExpenseTransaction.review_notification_queued_at.is_not(None),
                    )
                )
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
                .order_by(
                    func.coalesce(
                        ExpenseTransaction.review_notification_sent_at,
                        ExpenseTransaction.review_notification_queued_at,
                    ).asc()
                )
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
        if self.settings.is_production_mode:
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
        if not tx:
            return
        if tx.splitwise_expense_id:
            try:
                self._execute_splitwise_delete(
                    tx,
                    action="splitwise_delete_removed",
                    result_status=TransactionStatus.REMOVED.value,
                )
            except (TransactionError, SplitwiseAPIError):
                # The durable operation remains visible in Recovery. Advancing the
                # Plaid cursor is safe because retries no longer depend on replaying
                # the webhook page.
                return
            ReceiptTransactionReconciliationService(self.db).reconcile_removed_transaction(tx)
            self.db.commit()
            return
        tx.status = TransactionStatus.REMOVED.value
        tx.updated_at = utc_now()
        ReceiptTransactionReconciliationService(self.db).reconcile_removed_transaction(tx)
        self._record_transaction_audit(
            tx,
            event_type="transaction_removed",
            action="remove",
        )
        self.db.commit()

    def mark_personal(self, tx_id: int) -> ExpenseTransaction:
        tx = self.get_transaction(tx_id)
        self.validate_mark_personal(tx)
        tx.status = TransactionStatus.PERSONAL.value
        tx.last_error = None
        tx.updated_at = utc_now()
        self._record_transaction_audit(
            tx,
            event_type="transaction_marked_personal",
            action="mark_personal",
        )
        self.db.commit()
        self.db.refresh(tx)
        return tx

    @staticmethod
    def validate_mark_personal(tx: ExpenseTransaction) -> None:
        """Apply the canonical dashboard/Agent transition guard without mutating state."""

        if tx.status == TransactionStatus.PERSONAL.value:
            raise TransactionError("Transaction is already marked personal.")
        if tx.status == TransactionStatus.REMOVED.value:
            raise TransactionError("A removed transaction cannot be marked personal.")
        if tx.splitwise_expense_id:
            raise TransactionError("Transaction already posted to Splitwise; cannot mark personal.")
        if tx.status in {
            TransactionStatus.POSTING.value,
            TransactionStatus.POST_AMBIGUOUS.value,
            TransactionStatus.UNDOING.value,
            TransactionStatus.UNDO_AMBIGUOUS.value,
            TransactionStatus.RECONCILIATION_REQUIRED.value,
        }:
            raise TransactionError(
                "This transaction has a financial operation in progress or recovery."
            )

    def mark_shared_draft(self, tx_id: int) -> ExpenseTransaction:
        tx = self.get_transaction(tx_id)
        if tx.splitwise_expense_id:
            raise TransactionError("Transaction already posted to Splitwise; cannot draft.")
        payload = _parse_valid_splitwise_payload(tx.splitwise_payload_json)
        _validate_splitwise_payload(payload, expected_total_cents=abs(tx.amount_cents))
        tx.status = TransactionStatus.SHARED_DRAFT.value
        tx.last_error = None
        tx.updated_at = utc_now()
        self._record_transaction_audit(
            tx,
            event_type="splitwise_draft_saved",
            action="save_draft",
        )
        self.db.commit()
        self.db.refresh(tx)
        return tx

    def undo_transaction(self, tx_id: int) -> ExpenseTransaction:
        tx = self.get_transaction(tx_id)
        if not can_undo_transaction(tx):
            raise TransactionError("This transaction cannot be undone.")

        if tx.splitwise_expense_id:
            return self._execute_splitwise_delete(tx)

        tx.status = TransactionStatus.ASK_USER.value
        tx.last_error = None
        tx.updated_at = utc_now()
        self._record_transaction_audit(
            tx,
            event_type="transaction_returned_to_review",
            action="return_to_review",
        )
        self.db.commit()
        self.db.refresh(tx)
        return tx

    def retry_financial_operation(self, tx_id: int) -> ExpenseTransaction:
        tx = self.get_transaction(tx_id)
        operation = self.db.scalar(
            select(FinancialOperation)
            .where(FinancialOperation.transaction_id == tx.id)
            .order_by(FinancialOperation.created_at.desc(), FinancialOperation.id.desc())
        )
        if operation is None or operation.state not in {
            "failed",
            "needs_reconciliation",
            "in_flight",
        }:
            raise TransactionError("This transaction has no recoverable financial operation.")
        if operation.action == "splitwise_create":
            payload = dict(operation.request_json or {})
            if not payload:
                raise TransactionError("The saved Splitwise request is unavailable for recovery.")
            self._execute_splitwise_create(tx, payload, operation=operation)
            return tx
        if operation.action == "splitwise_delete":
            return self._execute_splitwise_delete(tx, operation=operation)
        if operation.action == "splitwise_delete_removed":
            return self._execute_splitwise_delete(
                tx,
                operation=operation,
                action=operation.action,
                result_status=TransactionStatus.REMOVED.value,
            )
        if operation.action == "splitwise_update":
            payload = dict((operation.request_json or {}).get("payload") or {})
            if not payload:
                raise TransactionError("The saved Splitwise update is unavailable for recovery.")
            return self._execute_splitwise_update(tx, payload, operation=operation)
        raise TransactionError("This financial operation cannot be retried.")

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
        tx, payload, _shares, _payer_user_id = self.prepare_equal_split_expense(
            tx_id=tx_id,
            friend_user_ids=friend_user_ids,
            group_id=group_id,
            description=description,
            details=details,
            currency_code=currency_code,
            post_pending=post_pending,
        )
        log_event(
            logger,
            "splitwise_equal_split_started",
            tx_id=tx_id,
            group_id=group_id,
            participant_count=len(friend_user_ids),
        )
        return self._draft_or_post(tx, payload, confirm=confirm)

    def prepare_equal_split_expense(
        self,
        *,
        tx_id: int,
        friend_user_ids: list[int],
        group_id: int | None,
        description: str | None,
        details: str | None,
        currency_code: str | None,
        post_pending: bool,
    ) -> tuple[ExpenseTransaction, dict[str, Any], list[SplitShare], int]:
        """Build the canonical equal-split payload without changing transaction state."""

        tx = self.get_transaction(tx_id)
        self._ensure_can_post(tx, post_pending=post_pending)
        payer_user_id = self._verified_splitwise_payer_id(tx)
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
        _validate_splitwise_payload(payload, expected_total_cents=abs(tx.amount_cents))
        return tx, payload, shares, payer_user_id

    def prepare_custom_split_expense(
        self,
        *,
        tx_id: int,
        owed_by_user_id: dict[int, int],
        group_id: int | None,
        description: str | None,
        details: str | None,
        currency_code: str | None,
        post_pending: bool,
    ) -> tuple[ExpenseTransaction, dict[str, Any], list[SplitShare], int]:
        """Build one exact custom Splitwise payload without changing transaction state."""

        tx = self.get_transaction(tx_id)
        self._ensure_can_post(tx, post_pending=post_pending)
        payer_user_id = self._verified_splitwise_payer_id(tx)
        shares = build_custom_split_shares(
            total_cents=abs(tx.amount_cents),
            payer_user_id=payer_user_id,
            owed_by_user_id=dict(owed_by_user_id),
        )
        payload = self._base_splitwise_payload(
            tx=tx,
            shares=shares,
            group_id=group_id,
            description=description,
            details=details,
            currency_code=currency_code,
        )
        _validate_splitwise_payload(payload, expected_total_cents=abs(tx.amount_cents))
        return tx, payload, shares, payer_user_id

    def post_prepared_splitwise_expense(
        self,
        *,
        tx_id: int,
        payload: dict[str, Any],
        expected_payer_user_id: int,
    ) -> tuple[ExpenseTransaction, dict[str, Any]]:
        """Execute one frozen, canonical proposal through the financial-operation path."""

        tx = self.get_transaction(tx_id)
        self._ensure_can_post(tx, post_pending=False)
        payer_user_id = self._verified_splitwise_payer_id(tx)
        if payer_user_id != expected_payer_user_id:
            raise TransactionError("The verified Splitwise payer changed after confirmation.")
        frozen_payload = dict(payload)
        _validate_splitwise_payload(
            frozen_payload,
            expected_total_cents=abs(tx.amount_cents),
        )
        payer_paid_cents = _splitwise_payload_paid_cents(
            frozen_payload,
            expected_payer_user_id,
        )
        if payer_paid_cents != abs(tx.amount_cents):
            raise TransactionError("The frozen Splitwise payer allocation is invalid.")
        tx.splitwise_payload_json = json.dumps(frozen_payload, default=str)
        return self._execute_splitwise_create(tx, frozen_payload)

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
        resolved_payer_user_id = self._verified_splitwise_payer_id(
            tx, fallback_payer_user_id=payer_user_id
        )
        if payer_user_id is not None and payer_user_id != resolved_payer_user_id:
            raise TransactionError("The payer must match the verified owner of this bank account.")
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

    def _verified_splitwise_payer_id(
        self,
        tx: ExpenseTransaction,
        *,
        fallback_payer_user_id: int | None = None,
    ) -> int:
        actor_user_id = getattr(self.db, "info", {}).get("user_id")
        if actor_user_id is None:
            # Trusted jobs and focused unit tests may inject an explicit provider
            # service without a request actor. Interactive requests always carry
            # the authenticated actor in the tenant session.
            if fallback_payer_user_id is not None:
                return fallback_payer_user_id
            return int(self.splitwise_service.get_current_user()["id"])

        item = tx.plaid_item or self.db.get(PlaidItem, tx.plaid_item_id)
        if item is None:
            raise TransactionError("The transaction's bank connection could not be verified.")
        if item.owner_user_id is None:
            member_count = self.db.scalar(
                select(func.count(WorkspaceMembership.id)).where(
                    WorkspaceMembership.workspace_id == item.workspace_id
                )
            )
            if member_count != 1:
                raise TransactionError(
                    "This bank connection needs an owner before it can post to Splitwise. "
                    "Confirm ownership in Settings."
                )
            item.owner_user_id = int(actor_user_id)
            item.ownership_verified_at = utc_now()
            self.db.flush()
        if item.owner_user_id != int(actor_user_id):
            raise TransactionError(
                "Only the verified owner of this bank account can post its transactions."
            )

        integration = self.db.scalar(
            select(SplitwiseIntegration).where(
                SplitwiseIntegration.workspace_id == item.workspace_id,
                SplitwiseIntegration.user_id == int(actor_user_id),
                SplitwiseIntegration.enabled.is_(True),
            )
        )
        if integration is None:
            raise TransactionError("Connect your personal Splitwise account before posting.")
        if not integration.splitwise_user_id or integration.verified_at is None:
            identity = self.splitwise_service.get_current_user()
            integration.splitwise_user_id = str(identity.get("id") or "") or None
            integration.display_name = friend_display_name(identity)
            integration.email = str(identity.get("email") or "") or None
            integration.verified_at = utc_now()
            self.db.flush()
        if not integration.splitwise_user_id:
            raise TransactionError("Splitwise did not return a verified payer identity.")
        return int(integration.splitwise_user_id)

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
        _validate_splitwise_payload(payload, expected_total_cents=abs(tx.amount_cents))
        tx.splitwise_payload_json = json.dumps(payload, default=str)
        if not confirm:
            tx.status = TransactionStatus.SHARED_DRAFT.value
            tx.last_error = None
            tx.updated_at = utc_now()
            self._record_transaction_audit(
                tx,
                event_type="splitwise_draft_saved",
                action="save_draft",
            )
            self.db.commit()
            self.db.refresh(tx)
            return tx, {"draft": True, "payload": payload}

        if tx.splitwise_expense_id:
            raise TransactionError(
                f"Transaction already posted to Splitwise expense {tx.splitwise_expense_id}."
            )
        return self._execute_splitwise_create(tx, payload)

    def _execute_splitwise_create(
        self,
        tx: ExpenseTransaction,
        payload: dict[str, Any],
        *,
        operation: FinancialOperation | None = None,
    ) -> tuple[ExpenseTransaction, dict[str, Any]]:
        if not self._supports_operation_journal():
            return self._legacy_splitwise_create(tx, payload)
        defer_provider = self._should_defer_splitwise_provider_action()
        operation = operation or self._claim_financial_operation(
            tx,
            action="splitwise_create",
            generation=tx.splitwise_generation,
            request_json=payload,
            commit=not defer_provider,
        )
        if operation.state == "succeeded" and operation.provider_object_id:
            tx.status = TransactionStatus.POSTED.value
            tx.splitwise_expense_id = operation.provider_object_id
            tx.last_error = None
            self.db.commit()
            return tx, {"replayed": True, "expenses": [{"id": operation.provider_object_id}]}
        if operation.attempt_count > 0 or operation.state == "needs_reconciliation":
            recovered = self.splitwise_service.find_expense_by_idempotency_key(
                operation.idempotency_key
            )
            if recovered is not None:
                return self._complete_splitwise_create(tx, operation, recovered, recovered=True)
        if defer_provider:
            self._queue_splitwise_operation(
                tx,
                operation,
                status=TransactionStatus.POSTING.value,
                message="Splitwise is processing this expense in the background.",
            )
            return tx, {"queued": True, "operation_id": operation.id}
        operation = self._acquire_operation_lease(operation)
        payload = dict(payload)
        marker = f"ExpenseOps ref: {operation.idempotency_key}"
        details = str(payload.get("details") or "").strip()
        payload["details"] = f"{details}\n{marker}".strip()
        operation.request_json = payload
        tx.status = TransactionStatus.POSTING.value
        tx.last_error = None
        tx.updated_at = utc_now()
        self.db.commit()
        try:
            response = self.splitwise_service.create_expense(payload)
            expense = response.get("expenses", [{}])[0]
            return self._complete_splitwise_create(tx, operation, expense, response=response)
        except SplitwiseAPIError as exc:
            self._fail_financial_operation(tx, operation, exc, create=True)
            raise

    def _complete_splitwise_create(
        self,
        tx: ExpenseTransaction,
        operation: FinancialOperation,
        expense: dict[str, Any],
        *,
        response: dict[str, Any] | None = None,
        recovered: bool = False,
    ) -> tuple[ExpenseTransaction, dict[str, Any]]:
        expense_id = str(expense.get("id") or "") or None
        if not expense_id:
            error = SplitwiseAPIError(
                "Splitwise create succeeded without a provider expense id",
                ambiguous=True,
            )
            self._fail_financial_operation(tx, operation, error, create=True)
            raise error
        operation.state = "succeeded"
        operation.provider_object_id = expense_id
        operation.completed_at = utc_now()
        operation.updated_at = operation.completed_at
        operation.lease_token = None
        operation.lease_expires_at = None
        operation.error_code = None
        operation.error_message = None
        tx.status = TransactionStatus.POSTED.value
        tx.splitwise_expense_id = expense_id
        tx.splitwise_amount_cents = abs(tx.amount_cents)
        tx.last_error = None
        tx.updated_at = utc_now()
        record_audit(
            self.db,
            workspace_id=tx.workspace_id,
            user_id=self._actor_user_id(),
            event_type="splitwise_expense_posted",
            resource_type="financial_operation",
            resource_id=str(operation.id),
            request_id=get_trace_id(),
            metadata={
                "transaction_id": tx.id,
                "provider_object_id": expense_id,
                "idempotency_key": operation.idempotency_key,
                "recovered": recovered,
                "action": operation.action,
                "outcome": "succeeded",
                "attempt": operation.attempt_count,
                "channel": self._interaction_channel(),
                **self._agent_action_audit_metadata(),
            },
        )
        recipient_user_id = self._splitwise_notification_recipient_user_id(tx)
        if recipient_user_id is not None:
            enqueue_outbox_event(
                self.db,
                workspace_id=tx.workspace_id,
                event_type="telegram.splitwise_posted",
                aggregate_type="financial_operation",
                aggregate_id=operation.id,
                dedupe_key=f"telegram-splitwise-posted:{operation.id}",
                payload={
                    "transaction_id": tx.id,
                    "splitwise_expense_id": expense_id,
                    "recipient_user_id": recipient_user_id,
                },
            )
        self.db.commit()
        self.db.refresh(tx)
        log_event(
            logger,
            "splitwise_expense_posted",
            tx_id=tx.id,
            splitwise_expense_id=expense_id,
            recovered=recovered,
        )
        return tx, response or {"recovered": recovered, "expenses": [expense]}

    def _execute_splitwise_update(
        self,
        tx: ExpenseTransaction,
        payload: dict[str, Any],
        *,
        operation: FinancialOperation | None = None,
    ) -> ExpenseTransaction:
        if not self._supports_operation_journal():
            raise TransactionError("Splitwise reconciliation requires the operation journal.")
        expense_id = tx.splitwise_expense_id
        if not expense_id:
            raise TransactionError("The Splitwise expense is unavailable for reconciliation.")
        _validate_splitwise_payload(payload, expected_total_cents=abs(tx.amount_cents))
        defer_provider = self._should_defer_splitwise_provider_action()
        operation = operation or self._claim_financial_operation(
            tx,
            action="splitwise_update",
            generation=tx.splitwise_generation,
            request_json={"provider_object_id": expense_id, "payload": payload},
            commit=not defer_provider,
        )
        provider_id = operation.provider_object_id or str(
            (operation.request_json or {}).get("provider_object_id") or expense_id
        )
        if operation.state == "succeeded":
            return self._complete_splitwise_update(tx, operation, payload)
        if operation.attempt_count > 0 or operation.state == "needs_reconciliation":
            if self.splitwise_service.expense_matches_payload(provider_id, payload):
                return self._complete_splitwise_update(tx, operation, payload)
        if defer_provider:
            operation.provider_object_id = provider_id
            self._queue_splitwise_operation(
                tx,
                operation,
                status=TransactionStatus.RECONCILIATION_REQUIRED.value,
                message="Splitwise is reconciling the finalized bank amount.",
            )
            return tx
        operation.provider_object_id = provider_id
        operation = self._acquire_operation_lease(operation)
        tx.status = TransactionStatus.RECONCILIATION_REQUIRED.value
        tx.last_error = "Confirming the finalized bank amount with Splitwise."
        tx.updated_at = utc_now()
        self.db.commit()
        try:
            self.splitwise_service.update_expense(provider_id, payload)
            return self._complete_splitwise_update(tx, operation, payload)
        except SplitwiseAPIError as exc:
            self._fail_financial_operation(tx, operation, exc, create=False)
            return tx

    def _complete_splitwise_update(
        self,
        tx: ExpenseTransaction,
        operation: FinancialOperation,
        payload: dict[str, Any],
    ) -> ExpenseTransaction:
        operation.state = "succeeded"
        operation.completed_at = utc_now()
        operation.updated_at = operation.completed_at
        operation.lease_token = None
        operation.lease_expires_at = None
        operation.error_code = None
        operation.error_message = None
        tx.status = TransactionStatus.POSTED.value
        tx.splitwise_payload_json = json.dumps(payload, default=str)
        if tx.splitwise_amount_cents != abs(tx.amount_cents):
            tx.splitwise_generation += 1
        tx.splitwise_amount_cents = abs(tx.amount_cents)
        tx.last_error = None
        tx.updated_at = utc_now()
        record_audit(
            self.db,
            workspace_id=tx.workspace_id,
            user_id=self._actor_user_id(),
            event_type="splitwise_expense_reconciled",
            resource_type="financial_operation",
            resource_id=str(operation.id),
            request_id=get_trace_id(),
            metadata={
                "transaction_id": tx.id,
                "provider_object_id": operation.provider_object_id,
                "amount_cents": tx.splitwise_amount_cents,
                "action": operation.action,
                "outcome": "succeeded",
                "attempt": operation.attempt_count,
                "channel": self._interaction_channel(),
            },
        )
        self.db.commit()
        self.db.refresh(tx)
        return tx

    def _execute_splitwise_delete(
        self,
        tx: ExpenseTransaction,
        *,
        operation: FinancialOperation | None = None,
        action: str = "splitwise_delete",
        result_status: str = TransactionStatus.ASK_USER.value,
    ) -> ExpenseTransaction:
        if not self._supports_operation_journal():
            return self._legacy_splitwise_delete(tx)
        expense_id = tx.splitwise_expense_id
        if not expense_id and operation is None:
            raise TransactionError("The Splitwise expense is already absent.")
        defer_provider = self._should_defer_splitwise_provider_action()
        operation = operation or self._claim_financial_operation(
            tx,
            action=action,
            generation=tx.splitwise_generation,
            request_json={
                "provider_object_id": expense_id,
                "result_status": result_status,
            },
            commit=not defer_provider,
        )
        provider_id = operation.provider_object_id or expense_id
        if operation.state == "succeeded":
            return self._complete_splitwise_delete(tx, operation)
        should_reconcile = operation.attempt_count > 0 or operation.state == "needs_reconciliation"
        if should_reconcile and provider_id:
            if not self.splitwise_service.expense_exists(provider_id):
                return self._complete_splitwise_delete(tx, operation)
        if not provider_id:
            return self._complete_splitwise_delete(tx, operation)
        if defer_provider:
            operation.provider_object_id = provider_id
            self._queue_splitwise_operation(
                tx,
                operation,
                status=TransactionStatus.UNDOING.value,
                message="Splitwise is removing this expense in the background.",
            )
            return tx
        operation.provider_object_id = provider_id
        operation = self._acquire_operation_lease(operation)
        tx.status = TransactionStatus.UNDOING.value
        tx.last_error = None
        tx.updated_at = utc_now()
        self.db.commit()
        try:
            self.splitwise_service.delete_expense(provider_id)
            return self._complete_splitwise_delete(tx, operation)
        except SplitwiseAPIError as exc:
            self._fail_financial_operation(tx, operation, exc, create=False)
            raise TransactionError(
                "Could not confirm deletion of the Splitwise expense. It remains in Recovery."
            ) from exc

    def _complete_splitwise_delete(
        self,
        tx: ExpenseTransaction,
        operation: FinancialOperation,
    ) -> ExpenseTransaction:
        operation.state = "succeeded"
        operation.completed_at = utc_now()
        operation.updated_at = operation.completed_at
        operation.lease_token = None
        operation.lease_expires_at = None
        operation.error_code = None
        operation.error_message = None
        tx.splitwise_expense_id = None
        tx.splitwise_amount_cents = None
        tx.splitwise_generation += 1
        tx.status = str(
            (operation.request_json or {}).get("result_status", TransactionStatus.ASK_USER.value)
        )
        tx.last_error = None
        tx.updated_at = utc_now()
        if tx.replaced_by_transaction_id:
            replacement = self.db.get(ExpenseTransaction, tx.replaced_by_transaction_id)
            if (
                replacement is not None
                and replacement.status == TransactionStatus.RECONCILIATION_REQUIRED.value
            ):
                replacement.status = TransactionStatus.ASK_USER.value
                replacement.last_error = None
                replacement.updated_at = utc_now()
        record_audit(
            self.db,
            workspace_id=tx.workspace_id,
            user_id=self._actor_user_id(),
            event_type="splitwise_expense_deleted",
            resource_type="financial_operation",
            resource_id=str(operation.id),
            request_id=get_trace_id(),
            metadata={
                "transaction_id": tx.id,
                "provider_object_id": operation.provider_object_id,
                "idempotency_key": operation.idempotency_key,
                "action": operation.action,
                "outcome": "succeeded",
                "attempt": operation.attempt_count,
                "channel": self._interaction_channel(),
            },
        )
        self.db.commit()
        self.db.refresh(tx)
        return tx

    def _claim_financial_operation(
        self,
        tx: ExpenseTransaction,
        *,
        action: str,
        generation: int,
        request_json: dict[str, Any],
        commit: bool = True,
    ) -> FinancialOperation:
        key_material = f"{tx.workspace_id}:{tx.id}:{action}:{generation}"
        idempotency_key = hashlib.sha256(key_material.encode()).hexdigest()
        operation = self.db.scalar(
            select(FinancialOperation).where(
                FinancialOperation.transaction_id == tx.id,
                FinancialOperation.action == action,
                FinancialOperation.generation == generation,
            )
        )
        if operation is None:
            operation = FinancialOperation(
                transaction_id=tx.id,
                actor_user_id=self._actor_user_id(),
                action=action,
                generation=generation,
                idempotency_key=idempotency_key,
                request_json=request_json,
                correlation_id=self._correlation_id(),
            )
            self.db.add(operation)
            try:
                if commit:
                    self.db.commit()
                else:
                    self.db.flush()
            except IntegrityError:
                self.db.rollback()
                operation = self.db.scalar(
                    select(FinancialOperation).where(
                        FinancialOperation.transaction_id == tx.id,
                        FinancialOperation.action == action,
                        FinancialOperation.generation == generation,
                    )
                )
                if operation is None:
                    raise
        return operation

    def _should_defer_splitwise_provider_action(self) -> bool:
        session_info = getattr(self.db, "info", {})
        return (
            self.settings.is_production_mode
            and not self._splitwise_service_injected
            and not bool(session_info.get("durable_worker"))
        )

    def _queue_splitwise_operation(
        self,
        tx: ExpenseTransaction,
        operation: FinancialOperation,
        *,
        status: str,
        message: str,
    ) -> None:
        operation.state = "pending"
        operation.lease_token = None
        operation.lease_expires_at = None
        operation.updated_at = utc_now()
        tx.status = status
        tx.last_error = message
        tx.updated_at = utc_now()
        event = enqueue_outbox_event(
            self.db,
            workspace_id=tx.workspace_id,
            event_type="splitwise.execute_operation",
            aggregate_type="financial_operation",
            aggregate_id=operation.id,
            dedupe_key=f"splitwise-operation:{operation.id}",
            payload={
                "operation_id": operation.id,
                "transaction_id": tx.id,
            },
        )
        if event.state == "dead":
            event.state = "pending"
            event.attempt_count = 0
            event.available_at = utc_now()
            event.last_error = None
            event.completed_at = None
        record_audit(
            self.db,
            workspace_id=tx.workspace_id,
            user_id=self._actor_user_id(),
            event_type="financial_operation_queued",
            resource_type="financial_operation",
            resource_id=str(operation.id),
            request_id=get_trace_id(),
            metadata={
                "transaction_id": tx.id,
                "action": operation.action,
                "outcome": "queued",
                "channel": self._interaction_channel(),
                **self._agent_action_audit_metadata(),
            },
        )
        self.db.commit()
        self.db.refresh(tx)

    def _acquire_operation_lease(self, operation: FinancialOperation) -> FinancialOperation:
        if operation.state == "succeeded":
            return operation
        now = utc_now()
        lease_token = secrets.token_hex(16)
        result = self.db.execute(
            update(FinancialOperation)
            .where(
                FinancialOperation.id == operation.id,
                FinancialOperation.state != "succeeded",
                or_(
                    FinancialOperation.lease_expires_at.is_(None),
                    FinancialOperation.lease_expires_at <= now,
                ),
            )
            .values(
                state="in_flight",
                lease_token=lease_token,
                lease_expires_at=now + timedelta(seconds=60),
                attempt_count=FinancialOperation.attempt_count + 1,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise TransactionError(
                "This financial action is already being processed. Check Recovery before retrying."
            )
        self.db.commit()
        return self.db.get(FinancialOperation, operation.id)

    def _fail_financial_operation(
        self,
        tx: ExpenseTransaction,
        operation: FinancialOperation,
        error: SplitwiseAPIError,
        *,
        create: bool,
    ) -> None:
        ambiguous = error.ambiguous
        operation.state = "needs_reconciliation" if ambiguous else "failed"
        operation.error_code = "ambiguous_provider_outcome" if ambiguous else "provider_rejected"
        operation.error_message = str(error)
        operation.updated_at = utc_now()
        operation.lease_token = None
        operation.lease_expires_at = None
        if operation.action == "splitwise_update":
            tx.status = (
                TransactionStatus.POST_AMBIGUOUS.value
                if ambiguous
                else TransactionStatus.RECONCILIATION_REQUIRED.value
            )
        else:
            tx.status = (
                TransactionStatus.POST_AMBIGUOUS.value
                if ambiguous and create
                else TransactionStatus.UNDO_AMBIGUOUS.value
                if ambiguous
                else TransactionStatus.ERROR.value
            )
        tx.last_error = str(error)
        tx.updated_at = utc_now()
        record_audit(
            self.db,
            workspace_id=tx.workspace_id,
            user_id=self._actor_user_id(),
            event_type="financial_operation_needs_reconciliation"
            if ambiguous
            else "financial_operation_failed",
            resource_type="financial_operation",
            resource_id=str(operation.id),
            request_id=get_trace_id(),
            metadata={
                "transaction_id": tx.id,
                "action": operation.action,
                "idempotency_key": operation.idempotency_key,
                "error_code": operation.error_code,
                "outcome": "ambiguous" if ambiguous else "failed",
                "attempt": operation.attempt_count,
                "channel": self._interaction_channel(),
                **self._agent_action_audit_metadata(),
            },
        )
        self.db.commit()
        log_event(
            logger,
            (
                "financial_operation_needs_reconciliation"
                if ambiguous
                else "financial_operation_failed"
            ),
            level=logging.WARNING,
            tx_id=tx.id,
            operation_id=operation.id,
            action=operation.action,
        )

    def _actor_user_id(self) -> int | None:
        value = getattr(self.db, "info", {}).get("user_id")
        return int(value) if value is not None else None

    def _interaction_channel(self) -> str:
        value = str(getattr(self.db, "info", {}).get("interaction_channel") or "").strip()
        if value:
            return value
        return "dashboard" if self._actor_user_id() is not None else "system"

    def _agent_action_audit_metadata(self) -> dict[str, str]:
        proposal_id = str(
            getattr(self.db, "info", {}).get("agent_action_proposal_id") or ""
        ).strip()
        return {"agent_action_proposal_id": proposal_id[:36]} if proposal_id else {}

    def _correlation_id(self) -> str | None:
        proposal_id = str(
            getattr(self.db, "info", {}).get("agent_action_proposal_id") or ""
        ).strip()
        if proposal_id:
            return proposal_id[:64]
        return get_trace_id()

    def _record_transaction_audit(
        self,
        tx: ExpenseTransaction,
        *,
        event_type: str,
        action: str,
    ) -> None:
        # Lightweight unit-test/legacy adapters do not expose the SQLAlchemy
        # persistence contract. Real request and worker sessions always do.
        if not hasattr(self.db, "add"):
            return
        proposal_id = str(
            getattr(self.db, "info", {}).get("agent_action_proposal_id") or ""
        ).strip()
        metadata: dict[str, Any] = {
            "transaction_id": tx.id,
            "action": action,
            "outcome": "succeeded",
            "channel": self._interaction_channel(),
        }
        if proposal_id:
            metadata["agent_action_proposal_id"] = proposal_id[:36]
        record_audit(
            self.db,
            workspace_id=tx.workspace_id,
            user_id=self._actor_user_id(),
            event_type=event_type,
            resource_type="expense_transaction",
            resource_id=str(tx.id),
            request_id=get_trace_id(),
            metadata=metadata,
        )

    def _splitwise_notification_recipient_user_id(self, tx: ExpenseTransaction) -> int | None:
        item = tx.plaid_item or self.db.get(PlaidItem, tx.plaid_item_id)
        recipient_user_id = item.owner_user_id if item is not None else self._actor_user_id()
        if recipient_user_id is None:
            return None
        identity_id = self.db.scalar(
            select(TelegramIdentity.id).where(
                TelegramIdentity.workspace_id == tx.workspace_id,
                TelegramIdentity.user_id == recipient_user_id,
                TelegramIdentity.enabled.is_(True),
            )
        )
        return int(recipient_user_id) if identity_id is not None else None

    def _splitwise_service_for_item_owner(self, item: PlaidItem) -> SplitwiseService:
        if item.owner_user_id is None:
            return self._splitwise_service_without_user_credentials()
        integration = self.db.scalar(
            select(SplitwiseIntegration).where(
                SplitwiseIntegration.workspace_id == item.workspace_id,
                SplitwiseIntegration.user_id == item.owner_user_id,
                SplitwiseIntegration.enabled.is_(True),
            )
        )
        if integration is None:
            return self._splitwise_service_without_user_credentials()
        try:
            credentials = json.loads(decrypt_secret(integration.credentials_encrypted))
        except (TypeError, ValueError, SecretConfigurationError):
            return self._splitwise_service_without_user_credentials()
        if not isinstance(credentials, dict):
            return self._splitwise_service_without_user_credentials()
        return SplitwiseService(
            self.settings.model_copy(update=credentials),
            resolve_workspace_credentials=False,
        )

    def _splitwise_service_without_user_credentials(self) -> SplitwiseService:
        return SplitwiseService(
            self.settings.model_copy(
                update={
                    "splitwise_api_key": "",
                    "splitwise_access_token": "",
                    "splitwise_consumer_key": "",
                    "splitwise_consumer_secret": "",
                    "splitwise_oauth_token": "",
                    "splitwise_oauth_token_secret": "",
                }
            ),
            resolve_workspace_credentials=False,
        )

    def _link_pending_replacement(
        self,
        item: PlaidItem,
        tx: ExpenseTransaction,
        tx_data: dict[str, Any],
    ) -> None:
        pending_plaid_id = str(tx_data.get("pending_transaction_id") or "").strip()
        if not pending_plaid_id or pending_plaid_id == tx.plaid_transaction_id:
            return
        prior = self._get_transaction_by_plaid_id(pending_plaid_id)
        if prior is None or prior.id == tx.id:
            return
        if prior.workspace_id != item.workspace_id or tx.workspace_id != item.workspace_id:
            return
        tx.replaces_transaction_id = prior.id
        prior.replaced_by_transaction_id = tx.id
        ReceiptTransactionReconciliationService(self.db).migrate_pending_replacement(prior, tx)
        if prior.splitwise_expense_id:
            try:
                self._execute_splitwise_delete(
                    prior,
                    action="splitwise_delete_removed",
                    result_status=TransactionStatus.REMOVED.value,
                )
            except (TransactionError, SplitwiseAPIError):
                tx.status = TransactionStatus.RECONCILIATION_REQUIRED.value
                tx.last_error = (
                    "The pending bank transaction was replaced, but its earlier Splitwise "
                    "expense still needs reconciliation."
                )
                self.db.commit()
                return
        else:
            prior.status = TransactionStatus.REMOVED.value
            prior.updated_at = utc_now()
        record_audit(
            self.db,
            workspace_id=item.workspace_id,
            user_id=item.owner_user_id,
            event_type="plaid_pending_transaction_replaced",
            resource_type="expense_transaction",
            resource_id=str(tx.id),
            request_id=get_trace_id(),
            metadata={
                "pending_transaction_id": prior.id,
                "posted_transaction_id": tx.id,
            },
        )

    def _reconcile_posted_amount_change(
        self,
        tx: ExpenseTransaction,
        *,
        previous_amount_cents: int,
    ) -> None:
        try:
            existing_payload = _parse_valid_splitwise_payload(tx.splitwise_payload_json)
            payload = _rescale_splitwise_payload(existing_payload, abs(tx.amount_cents))
        except (TransactionError, ValueError, ArithmeticError):
            tx.status = TransactionStatus.RECONCILIATION_REQUIRED.value
            tx.last_error = (
                f"Bank finalized this transaction from "
                f"{cents_to_decimal_string(abs(previous_amount_cents))} to "
                f"{cents_to_decimal_string(abs(tx.amount_cents))}. Review the split allocation."
            )
            tx.updated_at = utc_now()
            self.db.commit()
            return
        self._execute_splitwise_update(tx, payload)

    def _supports_operation_journal(self) -> bool:
        return all(hasattr(self.db, name) for name in ("scalar", "execute", "add", "flush"))

    def _legacy_splitwise_create(
        self, tx: ExpenseTransaction, payload: dict[str, Any]
    ) -> tuple[ExpenseTransaction, dict[str, Any]]:
        try:
            response = self.splitwise_service.create_expense(payload)
            expense_id = str(response.get("expenses", [{}])[0].get("id", "")) or None
            tx.status = TransactionStatus.POSTED.value
            tx.splitwise_expense_id = expense_id
            tx.splitwise_amount_cents = abs(tx.amount_cents)
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

    def _legacy_splitwise_delete(self, tx: ExpenseTransaction) -> ExpenseTransaction:
        try:
            self.splitwise_service.delete_expense(str(tx.splitwise_expense_id))
        except SplitwiseAPIError as exc:
            raise TransactionError(
                "Could not delete the Splitwise expense. Transaction was not reverted."
            ) from exc
        tx.splitwise_expense_id = None
        tx.splitwise_amount_cents = None
        tx.status = TransactionStatus.ASK_USER.value
        tx.last_error = None
        tx.updated_at = utc_now()
        self.db.commit()
        self.db.refresh(tx)
        return tx

    def _ensure_can_post(self, tx: ExpenseTransaction, *, post_pending: bool) -> None:
        if tx.splitwise_expense_id:
            raise TransactionError(
                f"Transaction already posted to Splitwise expense {tx.splitwise_expense_id}."
            )
        if tx.status == TransactionStatus.REMOVED.value:
            raise TransactionError("Cannot post a transaction Plaid marked as removed.")
        if tx.status in {
            TransactionStatus.POSTING.value,
            TransactionStatus.POST_AMBIGUOUS.value,
            TransactionStatus.UNDOING.value,
            TransactionStatus.UNDO_AMBIGUOUS.value,
            TransactionStatus.RECONCILIATION_REQUIRED.value,
        }:
            raise TransactionError(
                "This transaction has a financial operation in progress or recovery."
            )
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


def _parse_valid_splitwise_payload(value: str | None) -> dict[str, Any]:
    if not value:
        raise TransactionError(
            "Choose at least one participant and a valid allocation before saving a draft."
        )
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise TransactionError("The saved split allocation is invalid.") from exc
    if not isinstance(payload, dict) or not payload:
        raise TransactionError(
            "Choose at least one participant and a valid allocation before saving a draft."
        )
    return payload


def _validate_splitwise_payload(payload: dict[str, Any], *, expected_total_cents: int) -> None:
    try:
        cost_cents = decimal_to_cents(Decimal(str(payload.get("cost"))))
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise TransactionError("The split total is invalid.") from exc
    if cost_cents != expected_total_cents:
        raise TransactionError("The split total must match the bank transaction amount.")
    indexes = _splitwise_payload_indexes(payload)
    if not indexes:
        raise TransactionError("Choose at least one participant before saving the split.")
    paid_total = 0
    owed_total = 0
    seen_user_ids: set[int] = set()
    for index in indexes:
        try:
            user_id = int(payload[f"users__{index}__user_id"])
            paid = decimal_to_cents(Decimal(str(payload[f"users__{index}__paid_share"])))
            owed = decimal_to_cents(Decimal(str(payload[f"users__{index}__owed_share"])))
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise TransactionError(
                "Every split participant needs valid paid and owed shares."
            ) from exc
        if user_id <= 0 or user_id in seen_user_ids or paid < 0 or owed < 0:
            raise TransactionError("The split participant allocation is invalid.")
        seen_user_ids.add(user_id)
        paid_total += paid
        owed_total += owed
    if paid_total != expected_total_cents or owed_total != expected_total_cents:
        raise TransactionError("Paid and owed shares must each match the transaction total.")


def _splitwise_payload_paid_cents(payload: dict[str, Any], user_id: int) -> int | None:
    for index in _splitwise_payload_indexes(payload):
        try:
            candidate_id = int(payload[f"users__{index}__user_id"])
            paid_cents = decimal_to_cents(Decimal(str(payload[f"users__{index}__paid_share"])))
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise TransactionError(
                "The frozen Splitwise participant allocation is invalid."
            ) from exc
        if candidate_id == user_id:
            return paid_cents
    return None


def _splitwise_payload_indexes(payload: dict[str, Any]) -> list[int]:
    indexes: list[int] = []
    index = 0
    while f"users__{index}__user_id" in payload:
        indexes.append(index)
        index += 1
    return indexes


def _rescale_splitwise_payload(payload: dict[str, Any], new_total_cents: int) -> dict[str, Any]:
    indexes = _splitwise_payload_indexes(payload)
    if not indexes or new_total_cents <= 0:
        raise TransactionError("The saved split cannot be rescaled safely.")
    paid_weights = [
        Decimal(str(payload.get(f"users__{index}__paid_share") or "0")) for index in indexes
    ]
    owed_weights = [
        Decimal(str(payload.get(f"users__{index}__owed_share") or "0")) for index in indexes
    ]
    paid_cents = _allocate_cents_by_weights(new_total_cents, paid_weights)
    owed_cents = _allocate_cents_by_weights(new_total_cents, owed_weights)
    result = dict(payload)
    result["cost"] = cents_to_decimal_string(new_total_cents)
    for offset, index in enumerate(indexes):
        result[f"users__{index}__paid_share"] = cents_to_decimal_string(paid_cents[offset])
        result[f"users__{index}__owed_share"] = cents_to_decimal_string(owed_cents[offset])
    _validate_splitwise_payload(result, expected_total_cents=new_total_cents)
    return result


def _allocate_cents_by_weights(total_cents: int, weights: list[Decimal]) -> list[int]:
    weight_total = sum(weights, Decimal("0"))
    if weight_total <= 0:
        raise TransactionError("The saved split allocation has no positive shares.")
    raw = [Decimal(total_cents) * weight / weight_total for weight in weights]
    values = [int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in raw]
    remainder = total_cents - sum(values)
    order = sorted(
        range(len(raw)),
        key=lambda index: raw[index] - Decimal(values[index]),
        reverse=True,
    )
    for index in order[:remainder]:
        values[index] += 1
    return values


def _date_to_splitwise_iso(value: date | None) -> str | None:
    if not value:
        return None
    return (
        datetime(value.year, value.month, value.day, tzinfo=UTC).isoformat().replace("+00:00", "Z")
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

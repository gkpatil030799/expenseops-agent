from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, func, select

from app.api.deps import DbSession
from app.models import AuditEvent, ExpenseTransaction, FinancialOperation, TransactionStatus, User
from app.schemas import FinancialActivityEventOut, FinancialActivityPage, TransactionOut
from app.services.share_calculator import cents_to_decimal_string
from app.services.spending_insights_service import SpendingInsightsService
from app.services.transaction_service import can_undo_transaction

router = APIRouter(prefix="/api/insights", tags=["insights"])

FINANCIAL_ACTIVITY_EVENT_TYPES = {
    "transaction_marked_personal",
    "splitwise_draft_saved",
    "transaction_returned_to_review",
    "transaction_removed",
    "splitwise_expense_posted",
    "splitwise_expense_reconciled",
    "splitwise_expense_deleted",
    "financial_operation_failed",
    "financial_operation_needs_reconciliation",
}


@router.get("/activity", response_model=list[TransactionOut])
def review_activity(
    db: DbSession,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[TransactionOut]:
    transactions = db.scalars(
        select(ExpenseTransaction)
        .where(ExpenseTransaction.status != TransactionStatus.REMOVED.value)
        .order_by(desc(ExpenseTransaction.updated_at))
        .offset(offset)
        .limit(limit)
    )
    return [
        TransactionOut.model_validate(tx).model_copy(
            update={
                "amount": cents_to_decimal_string(abs(tx.amount_cents)),
                "can_undo_transaction": can_undo_transaction(tx),
            }
        )
        for tx in transactions
    ]


@router.get("/financial-activity", response_model=FinancialActivityPage)
def financial_activity(
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> FinancialActivityPage:
    workspace_id = int(db.info["workspace_id"])
    criteria = (
        AuditEvent.workspace_id == workspace_id,
        AuditEvent.event_type.in_(FINANCIAL_ACTIVITY_EVENT_TYPES),
    )
    total = int(db.scalar(select(func.count(AuditEvent.id)).where(*criteria)) or 0)
    audit_events = list(
        db.scalars(
            select(AuditEvent)
            .where(*criteria)
            .order_by(desc(AuditEvent.created_at), desc(AuditEvent.id))
            .offset(offset)
            .limit(limit)
        )
    )
    user_ids = {event.user_id for event in audit_events if event.user_id is not None}
    users = {
        user.id: user
        for user in db.scalars(select(User).where(User.id.in_(user_ids)))
    } if user_ids else {}
    operation_ids = {
        int(event.resource_id)
        for event in audit_events
        if event.resource_type == "financial_operation"
        and event.resource_id
        and event.resource_id.isdigit()
    }
    operations = {
        operation.id: operation
        for operation in db.scalars(
            select(FinancialOperation).where(FinancialOperation.id.in_(operation_ids))
        )
    } if operation_ids else {}
    transaction_ids = {
        int(metadata["transaction_id"])
        for event in audit_events
        if isinstance((metadata := event.metadata_json), dict)
        and str(metadata.get("transaction_id") or "").isdigit()
    }
    transactions = {
        transaction.id: transaction
        for transaction in db.scalars(
            select(ExpenseTransaction).where(ExpenseTransaction.id.in_(transaction_ids))
        )
    } if transaction_ids else {}

    rows: list[FinancialActivityEventOut] = []
    for event in audit_events:
        metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
        transaction_id_value = str(metadata.get("transaction_id") or "")
        transaction_id = int(transaction_id_value) if transaction_id_value.isdigit() else None
        transaction = transactions.get(transaction_id) if transaction_id is not None else None
        operation = (
            operations.get(int(event.resource_id))
            if event.resource_type == "financial_operation"
            and event.resource_id
            and event.resource_id.isdigit()
            else None
        )
        user = users.get(event.user_id) if event.user_id is not None else None
        outcome = _activity_outcome(event.event_type, metadata)
        rows.append(
            FinancialActivityEventOut(
                id=event.id,
                event_type=event.event_type,
                action=str(metadata.get("action") or (operation.action if operation else "update")),
                outcome=outcome,
                actor_user_id=event.user_id,
                actor_display_name=user.display_name if user else "ExpenseOps system",
                channel=str(metadata.get("channel") or ("dashboard" if user else "system")),
                attempt=int(metadata.get("attempt") or operation.attempt_count)
                if metadata.get("attempt") or operation
                else None,
                transaction_id=transaction_id,
                merchant_name=(transaction.merchant_name or transaction.name)
                if transaction
                else None,
                amount_cents=transaction.amount_cents if transaction else None,
                currency_code=transaction.iso_currency_code if transaction else None,
                provider_object_id=str(
                    metadata.get("provider_object_id")
                    or (operation.provider_object_id if operation else "")
                )
                or None,
                correlation_id=event.request_id
                or (operation.correlation_id if operation else None),
                created_at=event.created_at,
            )
        )
    return FinancialActivityPage(
        events=rows,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
    )


def _activity_outcome(event_type: str, metadata: dict) -> str:
    value = str(metadata.get("outcome") or "")
    if value in {"succeeded", "failed", "ambiguous"}:
        return value
    if event_type == "financial_operation_needs_reconciliation":
        return "ambiguous"
    if event_type == "financial_operation_failed":
        return "failed"
    return "succeeded"


@router.get("/spending")
def spending_insights(
    db: DbSession,
    start_date: date,
    end_date: date,
    account_id: str | None = None,
    category: str | None = None,
    merchant: str | None = None,
    review_type: Literal["all", "personal", "shared"] = "all",
    spend_basis: Literal["card", "actual_share"] = "card",
    granularity: Literal["day", "week", "month"] = Query(default="day"),
    currency_code: str | None = Query(default=None, min_length=3, max_length=3),
) -> dict:
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="Start date must not be after end date.")
    if (end_date - start_date).days > 730:
        raise HTTPException(status_code=422, detail="Date range must be two years or less.")
    return SpendingInsightsService(db).build(
        start_date=start_date,
        end_date=end_date,
        account_id=account_id,
        category=category,
        merchant=merchant,
        review_type=review_type,
        spend_basis=spend_basis,
        granularity=granularity,
        currency_code=currency_code,
    )

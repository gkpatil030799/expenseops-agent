from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, select

from app.api.deps import DbSession
from app.models import ExpenseTransaction, TransactionStatus
from app.schemas import TransactionOut
from app.services.share_calculator import cents_to_decimal_string
from app.services.spending_insights_service import SpendingInsightsService
from app.services.transaction_service import can_undo_transaction

router = APIRouter(prefix="/api/insights", tags=["insights"])


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

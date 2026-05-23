from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, select

from app.api.deps import DbSession
from app.models import ExpenseTransaction, TransactionStatus
from app.schemas import (
    CustomSplitRequest,
    EqualSplitRequest,
    InterpretRequest,
    InterpretResponse,
    MarkPersonalResponse,
    SplitwisePostResponse,
    TransactionOut,
)
from app.services.agent_service import match_friends
from app.services.recommendation_service import classify_transaction_recommendation
from app.services.share_calculator import cents_to_decimal_string, decimal_to_cents
from app.services.splitwise_service import SplitwiseAPIError, SplitwiseService
from app.services.transaction_service import (
    TransactionError,
    TransactionService,
    can_undo_transaction,
)

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _tx_out(tx: ExpenseTransaction) -> TransactionOut:
    classification = classify_transaction_recommendation(
        merchant_name=tx.merchant_name,
        name=tx.name,
        amount_cents=tx.amount_cents,
        category=tx.category,
    )
    return TransactionOut.model_validate(tx).model_copy(
        update={
            "amount": cents_to_decimal_string(abs(tx.amount_cents)),
            "classification_suggestion": classification.suggestion,
            "classification_reason": classification.reason,
            "can_undo_transaction": can_undo_transaction(tx),
        }
    )


@router.get("", response_model=list[TransactionOut])
def list_transactions(
    db: DbSession,
    status: TransactionStatus | None = Query(default=TransactionStatus.ASK_USER),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[TransactionOut]:
    stmt = select(ExpenseTransaction).order_by(desc(ExpenseTransaction.created_at)).limit(limit)
    if status:
        stmt = stmt.where(ExpenseTransaction.status == status.value)
    return [_tx_out(tx) for tx in db.execute(stmt).scalars()]


@router.get("/{tx_id}", response_model=TransactionOut)
def get_transaction(tx_id: int, db: DbSession) -> TransactionOut:
    try:
        return _tx_out(TransactionService(db).get_transaction(tx_id))
    except TransactionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{tx_id}/personal", response_model=MarkPersonalResponse)
def mark_personal(tx_id: int, db: DbSession) -> MarkPersonalResponse:
    try:
        tx = TransactionService(db).mark_personal(tx_id)
        return MarkPersonalResponse(transaction=_tx_out(tx), message="Marked as personal.")
    except TransactionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{tx_id}/undo", response_model=MarkPersonalResponse)
def undo_transaction(tx_id: int, db: DbSession) -> MarkPersonalResponse:
    try:
        tx = TransactionService(db).undo_transaction(tx_id)
        return MarkPersonalResponse(
            transaction=_tx_out(tx),
            message="Transaction moved back to review.",
        )
    except TransactionError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post("/{tx_id}/split/equal", response_model=SplitwisePostResponse)
def split_equal(tx_id: int, payload: EqualSplitRequest, db: DbSession) -> SplitwisePostResponse:
    try:
        tx, splitwise_response = TransactionService(db).create_equal_split_expense(
            tx_id=tx_id,
            friend_user_ids=payload.friend_user_ids,
            group_id=payload.group_id,
            description=payload.description,
            details=payload.details,
            currency_code=payload.currency_code,
            confirm=payload.confirm,
            post_pending=payload.post_pending,
        )
        return SplitwisePostResponse(
            transaction=_tx_out(tx),
            splitwise_expense_id=tx.splitwise_expense_id,
            splitwise_response=splitwise_response,
        )
    except (TransactionError, SplitwiseAPIError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{tx_id}/split/custom", response_model=SplitwisePostResponse)
def split_custom(
    tx_id: int, payload: CustomSplitRequest, db: DbSession
) -> SplitwisePostResponse:
    try:
        owed_by_user_id = {
            share.user_id: decimal_to_cents(Decimal(str(share.owed_share)))
            for share in payload.shares
        }
        tx, splitwise_response = TransactionService(db).create_custom_split_expense(
            tx_id=tx_id,
            owed_by_user_id=owed_by_user_id,
            group_id=payload.group_id,
            description=payload.description,
            details=payload.details,
            currency_code=payload.currency_code,
            confirm=payload.confirm,
            post_pending=payload.post_pending,
        )
        return SplitwisePostResponse(
            transaction=_tx_out(tx),
            splitwise_expense_id=tx.splitwise_expense_id,
            splitwise_response=splitwise_response,
        )
    except (TransactionError, SplitwiseAPIError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/interpret", response_model=InterpretResponse)
def interpret_text(payload: InterpretRequest) -> InterpretResponse:
    text_l = payload.text.lower().strip()
    if "personal" in text_l or "mine" in text_l or "own expense" in text_l:
        return InterpretResponse(
            intent="personal",
            split_mode="unknown",
            explanation="The message looks like a personal-expense decision.",
        )

    if "split" in text_l or "shared" in text_l or "with" in text_l:
        friends = SplitwiseService().get_friends()
        matches = match_friends(payload.text, friends)
        return InterpretResponse(
            intent="shared",
            split_mode="equal" if "equal" in text_l or "equally" in text_l else "unknown",
            friend_matches=matches,
            explanation="The message looks like a shared-expense decision. Confirm before posting.",
        )

    return InterpretResponse(
        intent="unknown",
        split_mode="unknown",
        explanation="Could not confidently classify the instruction.",
    )

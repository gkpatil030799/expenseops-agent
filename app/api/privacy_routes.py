from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.deps import CurrentUser, CurrentWorkspace, DbSession
from app.config import get_settings
from app.services.data_lifecycle_service import (
    CONSENT_PURPOSES,
    POLICY_VERSION,
    DataLifecycleService,
    operational_retention_summary,
)
from app.services.managed_auth_service import record_audit

router = APIRouter(prefix="/api/privacy", tags=["privacy"])


class ConsentRequest(BaseModel):
    purpose: Literal[
        "gmail_receipts",
        "gmail_promotions",
        "model_receipt_processing",
        "model_transaction_classification",
        "structured_transaction_learning",
    ]
    granted: bool


class AccountDeletionRequest(BaseModel):
    confirmation: str


@router.get("")
def privacy_summary(db: DbSession, user: CurrentUser, workspace: CurrentWorkspace) -> dict:
    settings = get_settings()
    return {
        "policy_version": POLICY_VERSION,
        "privacy_url": "/legal/privacy",
        "terms_url": "/legal/terms",
        "support_email": settings.support_email,
        "retention": operational_retention_summary(settings),
        "consents": DataLifecycleService(db, settings).consent_status(
            workspace_id=workspace.id,
            user_id=user.id,
        ),
        "deletion": {
            "confirmation": f"DELETE {user.email}",
            "financial_history_retained_for_audit": True,
        },
    }


@router.post("/consents")
def update_consent(
    payload: ConsentRequest,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> dict:
    if payload.purpose not in CONSENT_PURPOSES:
        raise HTTPException(status_code=422, detail="Unsupported consent purpose")
    consent = DataLifecycleService(db).set_consent(
        workspace_id=workspace.id,
        user_id=user.id,
        purpose=payload.purpose,
        granted=payload.granted,
    )
    record_audit(
        db,
        workspace_id=workspace.id,
        user_id=user.id,
        event_type="data_consent_updated",
        resource_type="data_consent",
        resource_id=str(consent.id),
        metadata={"purpose": payload.purpose, "granted": payload.granted},
    )
    db.commit()
    return {
        "purpose": consent.purpose,
        "granted": consent.granted,
        "policy_version": consent.policy_version,
    }


@router.delete("/account", status_code=204)
def delete_account(
    payload: AccountDeletionRequest,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> Response:
    expected = f"DELETE {user.email}"
    if payload.confirmation.strip() != expected:
        raise HTTPException(status_code=422, detail="Account deletion confirmation did not match")
    try:
        record_audit(
            db,
            workspace_id=workspace.id,
            user_id=user.id,
            event_type="account_deleted",
            resource_type="user",
            resource_id=str(user.id),
        )
        DataLifecycleService(db).delete_account(user)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response = Response(status_code=204)
    response.delete_cookie(get_settings().auth_session_cookie_name)
    return response

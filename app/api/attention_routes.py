from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentUser, CurrentWorkspace, DbSession
from app.attention_schemas import (
    AttentionCenterOut,
    AttentionDeliveryOut,
    AttentionPreferenceOut,
    AttentionPreferencePatch,
)
from app.models import utc_now
from app.services.proactive_attention_service import (
    ProactiveAttentionDisabledError,
    ProactiveAttentionService,
    preference_out,
)

router = APIRouter(prefix="/api/attention", tags=["attention"])


@router.get("", response_model=AttentionCenterOut)
def get_attention_center(
    db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> dict:
    service = ProactiveAttentionService(db)
    try:
        response, preferences = service.build_center()
    except ProactiveAttentionDisabledError as exc:
        raise HTTPException(status_code=404, detail="Attention Center is disabled.") from exc
    return {
        "enabled": response is not None,
        "generated_at": utc_now(),
        "response": response,
        "preferences": preference_out(preferences),
    }


@router.get("/preferences", response_model=AttentionPreferenceOut)
def get_attention_preferences(
    db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> dict:
    return preference_out(ProactiveAttentionService(db).preferences())


@router.patch("/preferences", response_model=AttentionPreferenceOut)
def patch_attention_preferences(
    payload: AttentionPreferencePatch,
    db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> dict:
    try:
        value = ProactiveAttentionService(db).update_preferences(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return preference_out(value)


@router.post("/telegram-digest", response_model=AttentionDeliveryOut)
def send_attention_telegram_digest(
    db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> dict:
    try:
        return ProactiveAttentionService(db).deliver_telegram_digest()
    except ProactiveAttentionDisabledError as exc:
        raise HTTPException(status_code=404, detail="Attention delivery is disabled.") from exc

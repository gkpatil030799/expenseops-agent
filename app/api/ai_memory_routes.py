from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, CurrentWorkspace, DbSession
from app.schemas import (
    AIMemoryOut,
    StructuredMemoryCreate,
    StructuredMemoryFeedback,
    StructuredMemoryMetricsOut,
    StructuredMemoryPatch,
    StructuredMemorySettingsOut,
    StructuredMemorySettingsPatch,
)
from app.services.ai_memory_service import AIInterpretationMemoryService

router = APIRouter(prefix="/ai/memory", tags=["ai-memory"])


@router.get("/settings", response_model=StructuredMemorySettingsOut)
def get_memory_settings(
    db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> dict[str, bool]:
    return {"transaction_learning_enabled": AIInterpretationMemoryService(db).learning_enabled()}


@router.patch("/settings", response_model=StructuredMemorySettingsOut)
def patch_memory_settings(
    payload: StructuredMemorySettingsPatch,
    db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> dict[str, bool]:
    enabled = AIInterpretationMemoryService(db).set_learning_enabled(
        enabled=payload.transaction_learning_enabled
    )
    return {"transaction_learning_enabled": enabled}


@router.get("/metrics", response_model=StructuredMemoryMetricsOut)
def get_memory_metrics(
    db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> dict:
    return AIInterpretationMemoryService(db).metrics()


@router.post("/preferences", response_model=AIMemoryOut, status_code=201)
def create_memory_preference(
    payload: StructuredMemoryCreate,
    db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> dict:
    try:
        memory = AIInterpretationMemoryService(db).create_explicit_preference(
            merchant=payload.merchant,
            preference=payload.preference,
            participant_names=payload.participant_names,
            group_name=payload.group_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AIInterpretationMemoryService(db).to_public_memory(memory)


@router.get("", response_model=list[AIMemoryOut])
def list_ai_memories(
    db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict]:
    return AIInterpretationMemoryService(db).list_public_memories(limit=limit)


@router.patch("/{memory_id}", response_model=AIMemoryOut)
def patch_ai_memory(
    memory_id: int,
    payload: StructuredMemoryPatch,
    db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> dict:
    try:
        memory = AIInterpretationMemoryService(db).update_preference(
            memory_id,
            preference=payload.preference,
            participant_names=payload.participant_names,
            group_name=payload.group_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if memory is None:
        raise HTTPException(status_code=404, detail="Structured preference not found")
    return AIInterpretationMemoryService(db).to_public_memory(memory)


@router.post("/{memory_id}/feedback")
def record_memory_feedback(
    memory_id: int,
    payload: StructuredMemoryFeedback,
    db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> dict[str, bool]:
    if not AIInterpretationMemoryService(db).record_feedback(memory_id, outcome=payload.outcome):
        raise HTTPException(status_code=404, detail="Structured preference not found")
    return {"ok": True}


@router.delete("/{memory_id}")
def delete_ai_memory(
    memory_id: int,
    db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> dict[str, bool]:
    deleted = AIInterpretationMemoryService(db).delete_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="AI memory not found")
    return {"ok": True}

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import DbSession
from app.schemas import AIMemoryOut
from app.services.ai_memory_service import AIInterpretationMemoryService

router = APIRouter(prefix="/ai/memory", tags=["ai-memory"])


@router.get("", response_model=list[AIMemoryOut])
def list_ai_memories(
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict]:
    return AIInterpretationMemoryService(db).list_public_memories(limit=limit)


@router.delete("/{memory_id}")
def delete_ai_memory(memory_id: int, db: DbSession) -> dict[str, bool]:
    deleted = AIInterpretationMemoryService(db).delete_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="AI memory not found")
    return {"ok": True}

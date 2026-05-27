from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.deps import DbSession
from sandbox.backend.config import SandboxSettings
from sandbox.backend.event_store import SandboxEventStore, new_trace_id
from sandbox.backend.guards import require_sandbox_plaid_env
from sandbox.backend.plaid_sandbox_service import SandboxPlaidError
from sandbox.backend.sandbox_orchestrator import SandboxOrchestrator
from sandbox.backend.schemas import (
    CreateItemResponse,
    CreateTransactionRequest,
    CreateTransactionResponse,
    EventsResponse,
    FireWebhookRequest,
    FireWebhookResponse,
    InitSyncResponse,
    ResetEventsResponse,
    RunE2EResponse,
    SandboxStatusResponse,
    SyncNowRequest,
    SyncNowResponse,
)
from sandbox.backend.state import SandboxStateStore

router = APIRouter(prefix="/api/sandbox", tags=["sandbox-lab"])


def _orchestrator(db: DbSession, settings: SandboxSettings) -> SandboxOrchestrator:
    return SandboxOrchestrator(db=db, settings=settings)


@router.get("/status", response_model=SandboxStatusResponse)
def sandbox_status(
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> dict:
    return _orchestrator(db, settings).status()


@router.post("/create-item", response_model=CreateItemResponse)
def create_item(
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> dict:
    try:
        return _orchestrator(db, settings).create_item()
    except SandboxPlaidError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/init-sync", response_model=InitSyncResponse)
def init_sync(
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> dict:
    try:
        return _orchestrator(db, settings).init_sync()
    except SandboxPlaidError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/create-transaction", response_model=CreateTransactionResponse)
def create_transaction(
    payload: CreateTransactionRequest,
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> dict:
    try:
        return _orchestrator(db, settings).create_transaction(payload)
    except SandboxPlaidError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/fire-webhook", response_model=FireWebhookResponse)
def fire_webhook(
    payload: FireWebhookRequest,
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> dict:
    try:
        return _orchestrator(db, settings).fire_webhook(
            trace_id=payload.trace_id,
            webhook_type=payload.webhook_type,
            webhook_code=payload.webhook_code,
        )
    except SandboxPlaidError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/sync-now", response_model=SyncNowResponse)
def sync_now(
    db: DbSession,
    payload: SyncNowRequest = Body(default_factory=SyncNowRequest),
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> dict:
    return _orchestrator(db, settings).sync_now(trace_id=payload.trace_id)


@router.post("/run-e2e", response_model=RunE2EResponse)
def run_e2e(
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> dict:
    return _orchestrator(db, settings).run_e2e()


@router.get("/events", response_model=EventsResponse)
def events(
    trace_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    _settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> dict:
    return {"events": SandboxEventStore().read(trace_id=trace_id, limit=limit)}


@router.post("/reset-events", response_model=ResetEventsResponse)
def reset_events(_settings: SandboxSettings = Depends(require_sandbox_plaid_env)) -> dict:
    SandboxEventStore().clear()
    SandboxStateStore().clear()
    return {"cleared": True}


@router.post("/trace-id")
def create_trace_id(_settings: SandboxSettings = Depends(require_sandbox_plaid_env)) -> dict:
    return {"trace_id": new_trace_id()}

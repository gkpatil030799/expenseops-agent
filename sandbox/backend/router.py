from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.deps import DbSession
from sandbox.backend.config import SandboxSettings
from sandbox.backend.event_store import SandboxEventStore, new_trace_id
from sandbox.backend.guards import require_sandbox_plaid_env
from sandbox.backend.plaid_sandbox_service import SandboxPlaidError
from sandbox.backend.reliability_runner import ReliabilityRunner
from sandbox.backend.sandbox_orchestrator import SandboxOrchestrator
from sandbox.backend.scenario_runner import ScenarioRunner
from sandbox.backend.schemas import (
    CreateItemResponse,
    CreateTransactionRequest,
    CreateTransactionResponse,
    EventsResponse,
    FireWebhookRequest,
    FireWebhookResponse,
    InitSyncResponse,
    ReliabilityDefinition,
    ReliabilityResult,
    ReliabilityRunAggregateResponse,
    ReliabilityRunsResponse,
    ResetEventsResponse,
    RunE2EResponse,
    SandboxStatusResponse,
    ScenarioDefinition,
    ScenarioResult,
    ScenarioRunAggregateResponse,
    ScenarioRunsResponse,
    SyncNowRequest,
    SyncNowResponse,
)
from sandbox.backend.state import SandboxStateStore

router = APIRouter(prefix="/api/sandbox", tags=["sandbox-lab"])


def _orchestrator(db: DbSession, settings: SandboxSettings) -> SandboxOrchestrator:
    return SandboxOrchestrator(db=db, settings=settings)


def _scenario_runner(db: DbSession, settings: SandboxSettings) -> ScenarioRunner:
    return ScenarioRunner(db=db, settings=settings)


def _reliability_runner(db: DbSession, settings: SandboxSettings) -> ReliabilityRunner:
    return ReliabilityRunner(db=db, settings=settings)


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


@router.get("/scenarios", response_model=list[ScenarioDefinition])
def list_scenarios(
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> list[ScenarioDefinition]:
    return _scenario_runner(db, settings).list_scenarios()


@router.get("/scenarios/{scenario_id}", response_model=ScenarioDefinition)
def get_scenario(
    scenario_id: str,
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> ScenarioDefinition:
    try:
        return _scenario_runner(db, settings).get_scenario(scenario_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Scenario not found: {scenario_id}") from exc


@router.post("/scenarios/{scenario_id}/run", response_model=ScenarioResult)
def run_scenario(
    scenario_id: str,
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> ScenarioResult:
    try:
        return _scenario_runner(db, settings).run_scenario(scenario_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Scenario not found: {scenario_id}") from exc


@router.post("/scenarios/run-all", response_model=ScenarioRunAggregateResponse)
def run_all_scenarios(
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> ScenarioRunAggregateResponse:
    return _scenario_runner(db, settings).run_all()


@router.get("/scenario-runs", response_model=ScenarioRunsResponse)
def list_scenario_runs(
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=500),
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> ScenarioRunsResponse:
    return ScenarioRunsResponse(results=_scenario_runner(db, settings).list_results(limit=limit))


@router.get("/scenario-runs/{scenario_run_id}", response_model=ScenarioResult)
def get_scenario_run(
    scenario_run_id: str,
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> ScenarioResult:
    try:
        return _scenario_runner(db, settings).get_result(scenario_run_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Scenario run not found: {scenario_run_id}",
        ) from exc


@router.post("/scenario-runs/reset", response_model=ResetEventsResponse)
def reset_scenario_runs(
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> dict:
    _scenario_runner(db, settings).clear_results()
    return {"cleared": True}


@router.get("/reliability-tests", response_model=list[ReliabilityDefinition])
def list_reliability_tests(
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> list[ReliabilityDefinition]:
    return _reliability_runner(db, settings).list_tests()


@router.get("/reliability-tests/{test_id}", response_model=ReliabilityDefinition)
def get_reliability_test(
    test_id: str,
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> ReliabilityDefinition:
    try:
        return _reliability_runner(db, settings).get_test(test_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Reliability test not found: {test_id}",
        ) from exc


@router.post("/reliability-tests/{test_id}/run", response_model=ReliabilityResult)
def run_reliability_test(
    test_id: str,
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> ReliabilityResult:
    try:
        return _reliability_runner(db, settings).run_test(test_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Reliability test not found: {test_id}",
        ) from exc


@router.post("/reliability-tests/run-all", response_model=ReliabilityRunAggregateResponse)
def run_all_reliability_tests(
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> ReliabilityRunAggregateResponse:
    return _reliability_runner(db, settings).run_all()


@router.get("/reliability-runs", response_model=ReliabilityRunsResponse)
def list_reliability_runs(
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=500),
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> ReliabilityRunsResponse:
    return ReliabilityRunsResponse(
        results=_reliability_runner(db, settings).list_results(limit=limit)
    )


@router.get("/reliability-runs/{reliability_run_id}", response_model=ReliabilityResult)
def get_reliability_run(
    reliability_run_id: str,
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> ReliabilityResult:
    try:
        return _reliability_runner(db, settings).get_result(reliability_run_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Reliability run not found: {reliability_run_id}",
        ) from exc


@router.post("/reliability-runs/reset", response_model=ResetEventsResponse)
def reset_reliability_runs(
    db: DbSession,
    settings: SandboxSettings = Depends(require_sandbox_plaid_env),
) -> dict:
    _reliability_runner(db, settings).clear_results()
    return {"cleared": True}

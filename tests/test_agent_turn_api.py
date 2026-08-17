from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.api.agent_routes as agent_routes
from app.agent.contracts import (
    AgentActionConfirmationBlock,
    AgentAssistantDeltaEvent,
    AgentMessageOut,
    AgentRunCompletedEvent,
    AgentRunOut,
    AgentRunStartedEvent,
    AgentSpendingSummaryBlock,
    AgentStructuredResponse,
    AgentStructuredResponseEvent,
    AgentTextBlock,
    AgentTurnOut,
)
from app.agent.runtime import AgentRuntimeError
from app.agent.service import (
    AgentConflictError,
    AgentFeatureDisabledError,
    AgentFoundationError,
    AgentNotFoundError,
)
from app.api.deps import get_current_user, get_current_workspace
from app.config import Settings
from app.db import get_db

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


class RecordingRateLimiter:
    def __init__(self, rejection: HTTPException | None = None) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self.rejection = rejection

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        self.calls.append((key, limit, window_seconds))
        if self.rejection is not None:
            raise self.rejection


class FakeOrchestrator:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        stream_events: list[Any] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.preflight_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []
        self.error = error
        self.stream_events = stream_events

    async def run_turn(
        self,
        conversation_public_id: str,
        *,
        owner_user_id: int,
        text: str,
        client_message_id: str,
        page_context: Any,
    ) -> AgentTurnOut:
        self.calls.append(
            {
                "conversation_public_id": conversation_public_id,
                "owner_user_id": owner_user_id,
                "text": text,
                "client_message_id": client_message_id,
                "page_context": page_context,
            }
        )
        if self.error is not None:
            raise self.error
        return _turn_out(
            conversation_public_id=conversation_public_id,
            text=text,
            client_message_id=client_message_id,
        )

    async def stream_turn(
        self,
        conversation_public_id: str,
        *,
        owner_user_id: int,
        text: str,
        client_message_id: str,
        page_context: Any,
    ):
        self.stream_calls.append(
            {
                "conversation_public_id": conversation_public_id,
                "owner_user_id": owner_user_id,
                "text": text,
                "client_message_id": client_message_id,
                "page_context": page_context,
            }
        )
        if self.error is not None:
            raise self.error
        for event in self.stream_events or []:
            yield event

    def preflight_turn(
        self,
        conversation_public_id: str,
        *,
        owner_user_id: int,
        page_context: Any,
    ) -> None:
        self.preflight_calls.append(
            {
                "conversation_public_id": conversation_public_id,
                "owner_user_id": owner_user_id,
                "page_context": page_context,
            }
        )
        if self.error is not None:
            raise self.error


class FakeActionExecutor:
    def __init__(self) -> None:
        self.confirm_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[dict[str, Any]] = []

    def confirm_and_execute(
        self,
        proposal_public_id: str,
        *,
        owner_user_id: int,
        expected_version: int,
    ):
        self.confirm_calls.append(
            {
                "proposal_public_id": proposal_public_id,
                "owner_user_id": owner_user_id,
                "expected_version": expected_version,
            }
        )
        return _proposal(proposal_public_id, "completed", expected_version + 3)

    def cancel(
        self,
        proposal_public_id: str,
        *,
        owner_user_id: int,
        expected_version: int,
    ):
        self.cancel_calls.append(
            {
                "proposal_public_id": proposal_public_id,
                "owner_user_id": owner_user_id,
                "expected_version": expected_version,
            }
        )
        return _proposal(proposal_public_id, "cancelled", expected_version + 1)


def _proposal(public_id: str, status: str, version: int):
    return SimpleNamespace(
        public_id=public_id,
        tool_name="propose_mark_transaction_personal",
        preview_json={
            "title": "Mark transaction personal",
            "summary": "This transaction will be marked personal.",
            "details": [{"label": "Merchant", "value": "Costco"}],
            "confirm_label": "Mark personal",
            "cancel_label": "Cancel",
        },
        version=version,
        status=status,
        expires_at=NOW,
    )


def _client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    orchestrator: FakeOrchestrator | None = None,
    limiter: RecordingRateLimiter | None = None,
) -> tuple[TestClient, FakeOrchestrator, RecordingRateLimiter]:
    application = FastAPI()
    application.include_router(agent_routes.router)
    application.dependency_overrides[get_db] = lambda: object()
    application.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=17)
    application.dependency_overrides[get_current_workspace] = lambda: SimpleNamespace(id=29)
    fake = orchestrator or FakeOrchestrator()
    recording_limiter = limiter or RecordingRateLimiter()
    monkeypatch.setattr(agent_routes, "_build_read_only_orchestrator", lambda _db: fake)
    monkeypatch.setattr(agent_routes, "rate_limiter", recording_limiter)
    monkeypatch.setattr(
        agent_routes,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            agent_enabled=True,
            agent_read_tools_enabled=True,
        ),
    )
    return TestClient(application), fake, recording_limiter


def _turn_out(
    *,
    conversation_public_id: str,
    text: str,
    client_message_id: str,
) -> AgentTurnOut:
    return AgentTurnOut(
        run=AgentRunOut(
            public_id="run-public-1",
            status="completed",
            model_name="gpt-5-mini",
            prompt_version="expenseops-readonly-v1.0",
            input_tokens=24,
            output_tokens=12,
            total_tokens=36,
            created_at=NOW,
            started_at=NOW,
            completed_at=NOW,
        ),
        user_message=AgentMessageOut(
            public_id="message-user-1",
            conversation_public_id=conversation_public_id,
            role="user",
            text=text,
            client_message_id=client_message_id,
            created_at=NOW,
        ),
        assistant_message=AgentMessageOut(
            public_id="message-assistant-1",
            conversation_public_id=conversation_public_id,
            role="assistant",
            structured_response=AgentStructuredResponse(
                blocks=[AgentTextBlock(text="Here is the grounded answer.")]
            ),
            created_at=NOW,
        ),
    )


def test_action_decision_endpoints_send_only_proposal_version_and_never_run_orchestrator(
    monkeypatch,
):
    client, orchestrator, limiter = _client(monkeypatch)
    action_executor = FakeActionExecutor()
    monkeypatch.setattr(agent_routes, "_build_action_executor", lambda _db: action_executor)

    confirmed = client.post(
        "/api/agent/proposals/proposal-1/confirm",
        json={"proposal_version": 4},
    )
    cancelled = client.post(
        "/api/agent/proposals/proposal-2/cancel",
        json={"proposal_version": 7},
    )
    rejected_extra = client.post(
        "/api/agent/proposals/proposal-3/confirm",
        json={"proposal_version": 1, "transaction_id": 99},
    )

    assert confirmed.status_code == 200
    assert AgentActionConfirmationBlock.model_validate(confirmed.json()).status == "completed"
    assert cancelled.status_code == 200
    assert AgentActionConfirmationBlock.model_validate(cancelled.json()).status == "cancelled"
    assert rejected_extra.status_code == 422
    assert action_executor.confirm_calls == [
        {
            "proposal_public_id": "proposal-1",
            "owner_user_id": 17,
            "expected_version": 4,
        }
    ]
    assert action_executor.cancel_calls == [
        {
            "proposal_public_id": "proposal-2",
            "owner_user_id": 17,
            "expected_version": 7,
        }
    ]
    assert orchestrator.calls == []
    assert orchestrator.stream_calls == []
    assert limiter.calls == [
        ("agent-action:29:17", 10, 60),
        ("agent-action:29:17", 10, 60),
    ]


def test_turn_endpoint_passes_typed_context_and_scopes_rate_limit(monkeypatch):
    client, orchestrator, limiter = _client(monkeypatch)

    response = client.post(
        "/api/agent/conversations/conversation-1/turns",
        json={
            "text": "Show my Starbucks transactions",
            "client_message_id": "ios-turn-42",
            "page_context": {
                "surface": "expense_activity",
                "filters": {
                    "start_date": "2026-05-16",
                    "end_date": "2026-08-14",
                    "merchant": "Starbucks",
                },
            },
        },
    )

    assert response.status_code == 201
    assert response.json()["run"]["status"] == "completed"
    assert response.json()["assistant_message"]["structured_response"]["blocks"] == [
        {"block_id": None, "type": "text", "text": "Here is the grounded answer."}
    ]
    assert limiter.calls == [("agent-turn:29:17", 10, 60)]
    assert orchestrator.calls[0]["conversation_public_id"] == "conversation-1"
    assert orchestrator.calls[0]["owner_user_id"] == 17
    assert orchestrator.calls[0]["client_message_id"] == "ios-turn-42"
    assert orchestrator.calls[0]["page_context"].filters.merchant == "Starbucks"


def test_turn_endpoint_requires_idempotency_key_and_valid_page_context(monkeypatch):
    client, orchestrator, limiter = _client(monkeypatch)

    missing_key = client.post(
        "/api/agent/conversations/conversation-1/turns",
        json={"text": "How much did I spend?"},
    )
    invalid_dates = client.post(
        "/api/agent/conversations/conversation-1/turns",
        json={
            "text": "How much did I spend?",
            "client_message_id": "browser-1",
            "page_context": {
                "surface": "expense_insights",
                "filters": {
                    "start_date": "2026-08-14",
                    "end_date": "2026-08-01",
                },
            },
        },
    )

    assert missing_key.status_code == 422
    assert invalid_dates.status_code == 422
    assert orchestrator.calls == []
    assert limiter.calls == []


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (
            AgentNotFoundError("agent_not_found", "Internal row detail"),
            404,
            "Agent resource not found",
        ),
        (
            AgentFeatureDisabledError("agent_disabled", "Internal flag detail"),
            404,
            "Agent resource not found",
        ),
        (
            AgentConflictError("agent_turn_in_progress", "This agent turn is already running"),
            409,
            "This agent turn is already running",
        ),
        (
            AgentFoundationError("invalid_agent_request", "Request could not be processed"),
            422,
            "Request could not be processed",
        ),
    ],
)
def test_turn_endpoint_maps_foundation_errors(
    monkeypatch,
    error,
    expected_status,
    expected_detail,
):
    client, _orchestrator, _limiter = _client(
        monkeypatch,
        orchestrator=FakeOrchestrator(error=error),
    )

    response = client.post(
        "/api/agent/conversations/conversation-1/turns",
        json={"text": "Show spending", "client_message_id": "browser-2"},
    )

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}


def test_turn_endpoint_hides_runtime_failure_details(monkeypatch):
    client, _orchestrator, _limiter = _client(
        monkeypatch,
        orchestrator=FakeOrchestrator(
            error=AgentRuntimeError(
                "provider_failed",
                "upstream detail that must not cross the API boundary",
            )
        ),
    )

    response = client.post(
        "/api/agent/conversations/conversation-1/turns",
        json={"text": "Show spending", "client_message_id": "browser-3"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "The agent service is temporarily unavailable"}
    assert "upstream" not in response.text


def test_turn_endpoint_stops_before_orchestration_when_rate_limited(monkeypatch):
    limiter = RecordingRateLimiter(HTTPException(status_code=429, detail="Too many requests"))
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        agent_routes,
        "log_event",
        lambda _logger, event, **metadata: events.append((event, metadata)),
    )
    client, orchestrator, _limiter = _client(monkeypatch, limiter=limiter)

    response = client.post(
        "/api/agent/conversations/conversation-1/turns",
        json={"text": "Show spending", "client_message_id": "browser-4"},
    )

    assert response.status_code == 429
    assert response.json() == {"detail": "Too many requests"}
    assert limiter.calls == [("agent-turn:29:17", 10, 60)]
    assert orchestrator.calls == []
    assert events == [("agent_rate_limited", {"operation": "turn"})]
    assert "29" not in str(events)
    assert "17" not in str(events)


def test_turn_endpoint_is_indistinguishable_when_read_agent_is_disabled(monkeypatch):
    client, orchestrator, limiter = _client(monkeypatch)
    monkeypatch.setattr(
        agent_routes,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            agent_enabled=True,
            agent_read_tools_enabled=False,
        ),
    )

    response = client.post(
        "/api/agent/conversations/conversation-1/turns",
        json={"text": "Show spending", "client_message_id": "browser-disabled"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Agent resource not found"}
    assert limiter.calls == []
    assert orchestrator.calls == []


def test_stream_endpoint_uses_semantic_sse_headers_and_safe_framing(monkeypatch):
    run = _turn_out(
        conversation_public_id="conversation-1",
        text="Show spending",
        client_message_id="browser-stream-1",
    ).run
    structured = AgentStructuredResponse(
        blocks=[
            AgentTextBlock(text="Canonical safe answer."),
            AgentSpendingSummaryBlock(
                title="Canonical spending",
                start_date="2026-08-01",
                end_date="2026-08-14",
                currency_code="USD",
                spend_basis="card",
                total_cents=4_321,
                credits_cents=0,
                previous_credits_cents=0,
                unknown_share_transactions=0,
                previous_unknown_share_transactions=0,
                unknown_credit_share_transactions=0,
                previous_unknown_credit_share_transactions=0,
                change_percent=None,
            ),
        ]
    )
    stream_events = [
        AgentRunStartedEvent(
            sequence=0,
            run_public_id=run.public_id,
        ),
        AgentAssistantDeltaEvent(
            sequence=1,
            run_public_id=run.public_id,
            delta="Canonical\nsafe answer.",
        ),
        AgentStructuredResponseEvent(
            sequence=2,
            run_public_id=run.public_id,
            response=structured,
        ),
        AgentRunCompletedEvent(
            sequence=3,
            run_public_id=run.public_id,
            run=run,
        ),
    ]
    client, orchestrator, limiter = _client(
        monkeypatch,
        orchestrator=FakeOrchestrator(stream_events=stream_events),
    )

    response = client.post(
        "/api/agent/conversations/conversation-1/turns/stream",
        json={
            "text": "Show spending",
            "client_message_id": "browser-stream-1",
            "page_context": {
                "surface": "expense_insights",
                "filters": {"category": "Groceries"},
            },
        },
        headers={"Accept": "text/event-stream"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    frames = response.text.strip().split("\n\n")
    assert [frame.splitlines()[1] for frame in frames] == [
        "event: run_started",
        "event: assistant_delta",
        "event: structured_response",
        "event: run_completed",
    ]
    assert [frame.splitlines()[0] for frame in frames] == [
        "id: 0",
        "id: 1",
        "id: 2",
        "id: 3",
    ]
    # Embedded newlines remain JSON escaped and cannot inject a second SSE field/frame.
    assert '"delta":"Canonical\\nsafe answer."' in frames[1]
    # Nullable contract fields remain explicit JSON nulls for strict web/native clients.
    assert '"change_percent":null' in frames[2]
    assert limiter.calls == [("agent-turn:29:17", 10, 60)]
    assert len(orchestrator.preflight_calls) == 1
    assert orchestrator.preflight_calls[0]["conversation_public_id"] == "conversation-1"
    assert len(orchestrator.stream_calls) == 1
    stream_call = orchestrator.stream_calls[0]
    assert stream_call["conversation_public_id"] == "conversation-1"
    assert stream_call["owner_user_id"] == 17
    assert stream_call["text"] == "Show spending"
    assert stream_call["client_message_id"] == "browser-stream-1"
    assert stream_call["page_context"].filters.category == "Groceries"


def test_stream_endpoint_is_hidden_and_makes_no_agent_call_when_disabled(monkeypatch):
    client, orchestrator, limiter = _client(monkeypatch)
    monkeypatch.setattr(
        agent_routes,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            agent_enabled=False,
            agent_read_tools_enabled=False,
        ),
    )

    response = client.post(
        "/api/agent/conversations/conversation-1/turns/stream",
        json={"text": "Show spending", "client_message_id": "browser-stream-disabled"},
        headers={"Accept": "text/event-stream"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Agent resource not found"}
    assert limiter.calls == []
    assert orchestrator.preflight_calls == []
    assert orchestrator.stream_calls == []

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.contracts import (
    AgentAssistantCompletedEvent,
    AgentAssistantDeltaEvent,
    AgentRunCompletedEvent,
    AgentRunFailedEvent,
    AgentRunStartedEvent,
    AgentSpendingSummaryBlock,
    AgentStreamEvent,
    AgentStructuredResponseEvent,
    AgentTextBlock,
    AgentToolCompletedEvent,
    AgentToolStartedEvent,
)
from app.agent.runtime import (
    ReadOnlyAgentOrchestrator,
    ReadOnlyModelResponse,
    ReadToolExecutor,
    RuntimeRequest,
    RuntimeResult,
)
from app.agent.service import UnifiedAgentService
from app.config import Settings
from app.db import Base
from app.models import (
    AgentMessage,
    AgentRun,
    AgentToolCall,
    ExpenseTransaction,
    PlaidItem,
    TransactionStatus,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.tenancy import TenantContext, set_session_tenant

RuntimeBehavior = Callable[
    [RuntimeRequest, ReadToolExecutor],
    Awaitable[RuntimeResult],
]


class FakeRuntime:
    """Deterministic runtime boundary; no provider client or network is created."""

    model_name = "fake-streaming-runtime"

    def __init__(self, behavior: RuntimeBehavior) -> None:
        self.behavior = behavior
        self.calls = 0

    async def run(
        self,
        request: RuntimeRequest,
        *,
        executor: ReadToolExecutor,
    ) -> RuntimeResult:
        self.calls += 1
        return await self.behavior(request, executor)


@dataclass(frozen=True)
class StreamingFixture:
    factory: sessionmaker
    contexts: dict[str, TenantContext]


@pytest.fixture
def agent_streaming_db() -> StreamingFixture:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as db:
        owner = User(email="stream-owner@example.test", display_name="Stream owner")
        outsider = User(email="stream-outsider@example.test", display_name="Stream outsider")
        db.add_all([owner, outsider])
        db.flush()
        workspace = Workspace(name="Streaming workspace", created_by_user_id=owner.id)
        other_workspace = Workspace(
            name="Other streaming workspace",
            created_by_user_id=outsider.id,
        )
        db.add_all([workspace, other_workspace])
        db.flush()
        db.add_all(
            [
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    user_id=owner.id,
                    role="owner",
                    is_default=True,
                ),
                WorkspaceMembership(
                    workspace_id=other_workspace.id,
                    user_id=outsider.id,
                    role="owner",
                    is_default=True,
                ),
            ]
        )
        owner_item = PlaidItem(
            workspace_id=workspace.id,
            item_id="stream-owner-item",
            owner_user_id=owner.id,
            institution_name="Owner bank",
        )
        outsider_item = PlaidItem(
            workspace_id=other_workspace.id,
            item_id="stream-outsider-item",
            owner_user_id=outsider.id,
            institution_name="Outsider bank",
        )
        db.add_all([owner_item, outsider_item])
        db.flush()
        db.add_all(
            [
                _transaction(
                    workspace_id=workspace.id,
                    item_id=owner_item.id,
                    provider_id="stream-aldi",
                    merchant="Aldi",
                    amount_cents=10_000,
                    occurred_on=date(2026, 8, 12),
                    category="Groceries",
                ),
                _transaction(
                    workspace_id=other_workspace.id,
                    item_id=outsider_item.id,
                    provider_id="stream-other-secret",
                    merchant="Other Workspace Secret",
                    amount_cents=990_000,
                    occurred_on=date(2026, 8, 12),
                ),
            ]
        )
        db.commit()
        contexts = {
            "owner": TenantContext(user_id=owner.id, workspace_id=workspace.id),
            "outsider": TenantContext(
                user_id=outsider.id,
                workspace_id=other_workspace.id,
            ),
        }

    try:
        yield StreamingFixture(factory=factory, contexts=contexts)
    finally:
        engine.dispose()


def _transaction(
    *,
    workspace_id: int,
    item_id: int,
    provider_id: str,
    merchant: str,
    amount_cents: int,
    occurred_on: date,
    category: str = "Restaurants",
) -> ExpenseTransaction:
    return ExpenseTransaction(
        workspace_id=workspace_id,
        plaid_transaction_id=provider_id,
        plaid_item_id=item_id,
        account_id="stream-checking",
        merchant_name=merchant,
        name=merchant,
        amount_cents=amount_cents,
        iso_currency_code="USD",
        date=occurred_on,
        pending=False,
        category=category,
        status=TransactionStatus.PERSONAL.value,
    )


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        agent_enabled=True,
        agent_read_tools_enabled=True,
        agent_write_actions_enabled=False,
        agent_proactive_enabled=False,
        agent_purchasing_enabled=False,
        openai_model="gpt-test-streaming",
    )


def _scoped(fixture: StreamingFixture, actor: str = "owner") -> Session:
    db = fixture.factory()
    set_session_tenant(db, fixture.contexts[actor])
    return db


def _conversation(db: Session, context: TenantContext):
    return UnifiedAgentService(db, _settings()).create_conversation(
        owner_user_id=context.user_id,
        title="Streaming evaluation",
    )


def _draft(text: str = "The model draft is not canonical financial evidence.") -> RuntimeResult:
    del text
    return RuntimeResult(
        draft=ReadOnlyModelResponse(completion="evidence_collected"),
        input_tokens=20,
        output_tokens=8,
        provider_request_id="stream-provider-response",
        provider_request_count=1,
    )


async def _collect(
    orchestrator: ReadOnlyAgentOrchestrator,
    conversation_public_id: str,
    context: TenantContext,
    *,
    text: str,
    client_message_id: str,
) -> list[AgentStreamEvent]:
    return [
        event
        async for event in orchestrator.stream_turn(
            conversation_public_id,
            owner_user_id=context.user_id,
            text=text,
            client_message_id=client_message_id,
        )
    ]


def test_stream_orders_progress_then_canonical_grounded_terminal_events(
    agent_streaming_db,
):
    async def spending(
        _request: RuntimeRequest,
        executor: ReadToolExecutor,
    ) -> RuntimeResult:
        await executor.invoke(
            "get_spending_insights",
            {"start_date": "2026-08-01", "end_date": "2026-08-14"},
        )
        return RuntimeResult(
            draft=ReadOnlyModelResponse(completion="evidence_collected"),
            input_tokens=31,
            output_tokens=11,
            provider_request_count=1,
        )

    runtime = FakeRuntime(spending)
    with _scoped(agent_streaming_db) as db:
        context = agent_streaming_db.contexts["owner"]
        conversation = _conversation(db, context)
        orchestrator = ReadOnlyAgentOrchestrator(
            db,
            settings=_settings(),
            runtime=runtime,
            now=lambda: datetime(2026, 8, 14, 12, tzinfo=UTC),
        )
        events = asyncio.run(
            _collect(
                orchestrator,
                conversation.public_id,
                context,
                text="How much did I spend this month?",
                client_message_id="stream-spending-1",
            )
        )

        event_types = [event.type for event in events]
        assert event_types[:3] == ["run_started", "tool_started", "tool_completed"]
        assert event_types[-3:] == [
            "structured_response",
            "assistant_completed",
            "run_completed",
        ]
        assert [event.sequence for event in events] == list(range(len(events)))
        assert isinstance(events[0], AgentRunStartedEvent)
        assert isinstance(events[1], AgentToolStartedEvent)
        assert events[1].message == "Checking your spending…"
        assert isinstance(events[2], AgentToolCompletedEvent)
        assert events[2].message == "Spending data is ready."
        assert "get_spending_insights" not in events[1].model_dump_json()
        assert "start_date" not in events[1].model_dump_json()

        deltas = [event.delta for event in events if isinstance(event, AgentAssistantDeltaEvent)]
        structured = next(
            event for event in events if isinstance(event, AgentStructuredResponseEvent)
        )
        text_block = next(
            block for block in structured.response.blocks if isinstance(block, AgentTextBlock)
        )
        summary = next(
            block
            for block in structured.response.blocks
            if isinstance(block, AgentSpendingSummaryBlock)
        )
        assert "".join(deltas) == text_block.text
        assert summary.total_cents == 10_000
        assert summary.total_cents != 999_999_999
        assert "Other Workspace Secret" not in structured.model_dump_json()

        completed = next(
            event for event in events if isinstance(event, AgentAssistantCompletedEvent)
        )
        terminal = events[-1]
        assert isinstance(terminal, AgentRunCompletedEvent)
        assert completed.message.structured_response == structured.response
        assert completed.message.feedback_eligible is True
        assert completed.message.feedback is None
        assert terminal.run.status == "completed"
        assert runtime.calls == 1
        assert db.scalar(select(func.count(AgentMessage.id))) == 2
        assert (
            db.scalar(select(func.count(AgentMessage.id)).where(AgentMessage.role == "assistant"))
            == 1
        )
        assert db.scalar(select(func.count(AgentRun.id))) == 1
        assert db.scalar(select(func.count(AgentToolCall.id))) == 1


def test_stream_persists_no_partial_assistant_message_before_terminal_commit(
    agent_streaming_db,
):
    release_runtime = asyncio.Event()

    async def delayed(
        _request: RuntimeRequest,
        _executor: ReadToolExecutor,
    ) -> RuntimeResult:
        await release_runtime.wait()
        return _draft()

    runtime = FakeRuntime(delayed)
    with _scoped(agent_streaming_db) as db:
        context = agent_streaming_db.contexts["owner"]
        conversation = _conversation(db, context)
        orchestrator = ReadOnlyAgentOrchestrator(
            db,
            settings=_settings(),
            runtime=runtime,
            now=lambda: datetime(2026, 8, 14, 12, tzinfo=UTC),
        )

        async def scenario():
            stream = orchestrator.stream_turn(
                conversation.public_id,
                owner_user_id=context.user_id,
                text="Show my spending",
                client_message_id="stream-final-only-1",
            )
            first = await anext(stream)
            assistant_count_before = db.scalar(
                select(func.count(AgentMessage.id)).where(AgentMessage.role == "assistant")
            )
            release_runtime.set()
            remainder = [event async for event in stream]
            return first, assistant_count_before, remainder

        first, assistant_count_before, remainder = asyncio.run(scenario())
        assert isinstance(first, AgentRunStartedEvent)
        assert assistant_count_before == 0
        assert isinstance(remainder[-1], AgentRunCompletedEvent)
        assert (
            db.scalar(select(func.count(AgentMessage.id)).where(AgentMessage.role == "assistant"))
            == 1
        )


def test_terminal_retry_replays_without_reexecuting_or_duplicating_rows(agent_streaming_db):
    async def spending(
        _request: RuntimeRequest,
        executor: ReadToolExecutor,
    ) -> RuntimeResult:
        await executor.invoke(
            "get_spending_insights",
            {"start_date": "2026-08-01", "end_date": "2026-08-14"},
        )
        return _draft()

    runtime = FakeRuntime(spending)
    with _scoped(agent_streaming_db) as db:
        context = agent_streaming_db.contexts["owner"]
        conversation = _conversation(db, context)
        orchestrator = ReadOnlyAgentOrchestrator(
            db,
            settings=_settings(),
            runtime=runtime,
            now=lambda: datetime(2026, 8, 14, 12, tzinfo=UTC),
        )

        async def scenario():
            first = await _collect(
                orchestrator,
                conversation.public_id,
                context,
                text="How much did I spend?",
                client_message_id="stream-replay-1",
            )
            second = await _collect(
                orchestrator,
                conversation.public_id,
                context,
                text="How much did I spend?",
                client_message_id="stream-replay-1",
            )
            return first, second

        first, replay = asyncio.run(scenario())
        assert runtime.calls == 1
        assert isinstance(replay[0], AgentRunStartedEvent)
        assert replay[0].resumed is True
        assert not any(
            isinstance(event, (AgentToolStartedEvent, AgentToolCompletedEvent)) for event in replay
        )
        assert isinstance(replay[-1], AgentRunCompletedEvent)
        first_structured = next(
            event for event in first if isinstance(event, AgentStructuredResponseEvent)
        )
        replay_structured = next(
            event for event in replay if isinstance(event, AgentStructuredResponseEvent)
        )
        assert replay_structured.response == first_structured.response
        assert db.scalar(select(func.count(AgentMessage.id))) == 2
        assert db.scalar(select(func.count(AgentRun.id))) == 1
        assert db.scalar(select(func.count(AgentToolCall.id))) == 1


def test_stream_maps_runtime_failure_to_safe_persisted_terminal_event(agent_streaming_db):
    async def failed(
        _request: RuntimeRequest,
        _executor: ReadToolExecutor,
    ) -> RuntimeResult:
        raise RuntimeError("provider-secret-that-must-not-cross-the-boundary")

    runtime = FakeRuntime(failed)
    with _scoped(agent_streaming_db) as db:
        context = agent_streaming_db.contexts["owner"]
        conversation = _conversation(db, context)
        orchestrator = ReadOnlyAgentOrchestrator(
            db,
            settings=_settings(),
            runtime=runtime,
            now=lambda: datetime(2026, 8, 14, 12, tzinfo=UTC),
        )
        events = asyncio.run(
            _collect(
                orchestrator,
                conversation.public_id,
                context,
                text="Show my spending",
                client_message_id="stream-failure-1",
            )
        )

        assert [event.type for event in events] == [
            "run_started",
            "structured_response",
            "assistant_completed",
            "run_failed",
        ]
        failure = events[-1]
        assert isinstance(failure, AgentRunFailedEvent)
        assert failure.code == "agent_run_failed"
        assert failure.retryable is True
        assert "provider-secret" not in failure.model_dump_json()
        assert failure.run is not None and failure.run.status == "failed"
        persisted = db.scalar(select(AgentMessage).where(AgentMessage.role == "assistant"))
        assert persisted is not None
        assert "provider-secret" not in str(persisted.structured_response_json)
        assert db.scalar(select(func.count(AgentMessage.id))) == 2


def test_closing_stream_cancels_run_without_persisting_assistant_fragment(agent_streaming_db):
    entered = asyncio.Event()

    async def never_finishes(
        _request: RuntimeRequest,
        _executor: ReadToolExecutor,
    ) -> RuntimeResult:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    runtime = FakeRuntime(never_finishes)
    with _scoped(agent_streaming_db) as db:
        context = agent_streaming_db.contexts["owner"]
        conversation = _conversation(db, context)
        orchestrator = ReadOnlyAgentOrchestrator(
            db,
            settings=_settings(),
            runtime=runtime,
            now=lambda: datetime(2026, 8, 14, 12, tzinfo=UTC),
        )

        async def scenario():
            stream = orchestrator.stream_turn(
                conversation.public_id,
                owner_user_id=context.user_id,
                text="Show my spending",
                client_message_id="stream-cancel-1",
            )
            first = await anext(stream)
            await entered.wait()
            await stream.aclose()
            return first

        first = asyncio.run(scenario())
        db.expire_all()
        run = db.scalar(select(AgentRun))
        assert isinstance(first, AgentRunStartedEvent)
        assert run is not None
        assert (run.status, run.error_code) == ("cancelled", "run_cancelled")
        assert (
            db.scalar(select(func.count(AgentMessage.id)).where(AgentMessage.role == "assistant"))
            == 0
        )


def test_disconnect_then_same_id_retry_is_safe_and_does_not_duplicate(
    agent_streaming_db,
):
    entered = asyncio.Event()

    async def first_call_never_finishes(
        _request: RuntimeRequest,
        _executor: ReadToolExecutor,
    ) -> RuntimeResult:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    runtime = FakeRuntime(first_call_never_finishes)
    with _scoped(agent_streaming_db) as db:
        context = agent_streaming_db.contexts["owner"]
        conversation = _conversation(db, context)
        orchestrator = ReadOnlyAgentOrchestrator(
            db,
            settings=_settings(),
            runtime=runtime,
            now=lambda: datetime(2026, 8, 14, 12, tzinfo=UTC),
        )

        async def scenario():
            first_stream = orchestrator.stream_turn(
                conversation.public_id,
                owner_user_id=context.user_id,
                text="Show my spending",
                client_message_id="stream-disconnect-retry-1",
            )
            first_event = await anext(first_stream)
            await entered.wait()
            await first_stream.aclose()
            retry_events = await _collect(
                orchestrator,
                conversation.public_id,
                context,
                text="Show my spending",
                client_message_id="stream-disconnect-retry-1",
            )
            return first_event, retry_events

        first_event, retry_events = asyncio.run(scenario())
        db.expire_all()
        assert isinstance(first_event, AgentRunStartedEvent)
        assert len(retry_events) == 1
        assert isinstance(retry_events[0], AgentRunFailedEvent)
        assert retry_events[0].code == "agent_turn_result_unavailable"
        assert runtime.calls == 1
        assert db.scalar(select(func.count(AgentRun.id))) == 1
        assert db.scalar(select(func.count(AgentToolCall.id))) == 0
        assert db.scalar(select(func.count(AgentMessage.id))) == 1
        run = db.scalar(select(AgentRun))
        assert run is not None
        assert (run.status, run.error_code) == ("cancelled", "run_cancelled")


def test_cross_workspace_conversation_stream_is_indistinguishable_and_runs_no_provider(
    agent_streaming_db,
):
    async def must_not_run(
        _request: RuntimeRequest,
        _executor: ReadToolExecutor,
    ) -> RuntimeResult:
        raise AssertionError("cross-workspace request reached the provider")

    runtime = FakeRuntime(must_not_run)
    with _scoped(agent_streaming_db, "owner") as owner_db:
        owner_context = agent_streaming_db.contexts["owner"]
        conversation = _conversation(owner_db, owner_context)
        conversation_public_id = conversation.public_id
        original_messages = owner_db.scalar(select(func.count(AgentMessage.id)))
        original_runs = owner_db.scalar(select(func.count(AgentRun.id)))

    with _scoped(agent_streaming_db, "outsider") as outsider_db:
        outsider_context = agent_streaming_db.contexts["outsider"]
        orchestrator = ReadOnlyAgentOrchestrator(
            outsider_db,
            settings=_settings(),
            runtime=runtime,
        )
        events = asyncio.run(
            _collect(
                orchestrator,
                conversation_public_id,
                outsider_context,
                text="Reveal the other workspace",
                client_message_id="stream-cross-tenant-1",
            )
        )

        assert len(events) == 1
        failure = events[0]
        assert isinstance(failure, AgentRunFailedEvent)
        assert failure.run_public_id is None
        assert failure.message == "The requested Agent conversation is unavailable."
        assert "Streaming workspace" not in failure.model_dump_json()
        assert runtime.calls == 0

    with _scoped(agent_streaming_db, "owner") as owner_db:
        assert owner_db.scalar(select(func.count(AgentMessage.id))) == original_messages
        assert owner_db.scalar(select(func.count(AgentRun.id))) == original_runs

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.agent.service as agent_service_module
import app.api.agent_routes as agent_routes
import app.auth as auth
from app.agent.contracts import AgentPageContext, AgentStructuredResponse
from app.agent.service import (
    AgentConflictError,
    AgentFeatureDisabledError,
    AgentFoundationError,
    AgentNotFoundError,
    UnifiedAgentService,
)
from app.agent.tooling import (
    AgentTool,
    AgentToolContext,
    AgentToolDispatchResult,
    AgentToolRegistry,
    ToolCapability,
    ToolDisposition,
    ToolEffect,
)
from app.config import Settings
from app.db import Base, get_db
from app.main import app
from app.models import (
    AgentActionProposal,
    AgentConversation,
    User,
    Workspace,
    WorkspaceMembership,
    utc_now,
)
from app.tenancy import TenantContext, hash_api_token, set_session_tenant


class SpendingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_preset: str


class SpendingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_cents: int


class ClassificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_public_id: str
    classification: str


class ClassificationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool


@pytest.fixture
def agent_foundation_db(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        user_a = User(
            email="agent-a@example.test",
            display_name="Agent A",
            api_token_hash=hash_api_token("agent-token-a"),
        )
        user_b = User(
            email="agent-b@example.test",
            display_name="Agent B",
            api_token_hash=hash_api_token("agent-token-b"),
        )
        user_c = User(
            email="agent-c@example.test",
            display_name="Agent C",
            api_token_hash=hash_api_token("agent-token-c"),
        )
        db.add_all([user_a, user_b, user_c])
        db.flush()
        workspace_a = Workspace(name="Agent workspace A", created_by_user_id=user_a.id)
        workspace_b = Workspace(name="Agent workspace B", created_by_user_id=user_b.id)
        db.add_all([workspace_a, workspace_b])
        db.flush()
        db.add_all(
            [
                WorkspaceMembership(
                    workspace_id=workspace_a.id,
                    user_id=user_a.id,
                    role="owner",
                    is_default=True,
                ),
                WorkspaceMembership(
                    workspace_id=workspace_a.id,
                    user_id=user_c.id,
                    role="member",
                    is_default=True,
                ),
                WorkspaceMembership(
                    workspace_id=workspace_b.id,
                    user_id=user_b.id,
                    role="owner",
                    is_default=True,
                ),
            ]
        )
        db.commit()
        contexts = {
            "a": TenantContext(user_a.id, workspace_a.id),
            "b": TenantContext(user_b.id, workspace_b.id),
            "c": TenantContext(user_c.id, workspace_a.id),
        }

    state = {"settings": _agent_settings()}
    monkeypatch.setattr(auth, "SessionLocal", factory)
    monkeypatch.setattr(auth, "_auth_not_required", lambda _settings: False)
    monkeypatch.setattr(agent_service_module, "get_settings", lambda: state["settings"])
    monkeypatch.setattr(agent_routes, "get_settings", lambda: state["settings"])

    def override_get_db(request: Request):
        with factory() as db:
            set_session_tenant(
                db,
                TenantContext(request.state.user_id, request.state.workspace_id),
            )
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield factory, contexts, state
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _agent_settings(*, write: bool = False, purchasing: bool = False) -> Settings:
    return Settings(
        _env_file=None,
        agent_enabled=True,
        agent_read_tools_enabled=True,
        agent_write_actions_enabled=write,
        agent_purchasing_enabled=purchasing,
    )


def _scoped(factory, context: TenantContext) -> Session:
    db = factory()
    set_session_tenant(db, context)
    return db


def _prepare_read_dispatch(db: Session):
    registry = AgentToolRegistry(_agent_settings())
    registry.register(
        AgentTool(
            name="get_spending_insights",
            description="Read a bounded spending summary.",
            effect=ToolEffect.READ,
            input_model=SpendingInput,
            output_model=SpendingOutput,
            handler=lambda _context, _values: {"total_cents": 12_500},
        )
    )
    context = AgentToolContext.from_session(db, request_id="request-1")
    prepared = registry.prepare(
        "get_spending_insights",
        {"date_preset": "this_month"},
        context=context,
    )
    return registry, context, prepared


def _prepare_write_dispatch(
    db: Session,
    *,
    transaction_public_id: str = "transaction-123",
    registry: AgentToolRegistry | None = None,
):
    if registry is None:
        registry = AgentToolRegistry(_agent_settings(write=True))
        registry.register(
            AgentTool(
                name="mark_transaction_personal",
                description="Prepare a personal transaction classification.",
                effect=ToolEffect.WRITE,
                input_model=ClassificationInput,
                output_model=ClassificationOutput,
                handler=lambda _context, _values: {"accepted": True},
                confirmation_required=True,
                preview_builder=lambda _context, values: {
                    "title": "Mark as personal",
                    "summary": f"Classify {values.transaction_public_id} as personal.",
                    "details": [{"label": "Transaction", "value": values.transaction_public_id}],
                },
            )
        )
    context = AgentToolContext.from_session(db, request_id="request-1")
    prepared = registry.prepare(
        "mark_transaction_personal",
        {
            "transaction_public_id": transaction_public_id,
            "classification": "personal",
        },
        context=context,
    )
    return registry, prepared


def test_agent_flags_are_server_owned_and_disabled_resources_are_hidden(
    agent_foundation_db,
):
    factory, contexts, state = agent_foundation_db
    state["settings"] = Settings(_env_file=None)

    response = TestClient(app).get(
        "/api/agent/capabilities",
        headers={"Authorization": "Bearer agent-token-a"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1.0",
        "enabled": False,
        "read_tools_enabled": False,
        "write_actions_enabled": False,
        "proactive_enabled": False,
        "purchasing_enabled": False,
    }

    response = TestClient(app).post(
        "/api/agent/conversations",
        headers={"Authorization": "Bearer agent-token-a"},
        json={"title": "Should remain unavailable"},
    )
    assert response.status_code == 404
    with _scoped(factory, contexts["a"]) as db:
        with pytest.raises(AgentFeatureDisabledError):
            UnifiedAgentService(db, Settings(_env_file=None)).create_conversation(
                owner_user_id=contexts["a"].user_id
            )
        assert db.scalar(select(AgentConversation)) is None


def test_conversation_api_is_private_to_owner_even_inside_one_workspace(
    agent_foundation_db,
):
    _factory, _contexts, state = agent_foundation_db
    state["settings"] = _agent_settings()
    client = TestClient(app)
    created = client.post(
        "/api/agent/conversations",
        headers={"Authorization": "Bearer agent-token-a"},
        json={"title": "My private agent thread"},
    )
    assert created.status_code == 201
    conversation_id = created.json()["public_id"]

    added = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        headers={"Authorization": "Bearer agent-token-a"},
        json={"text": "What needs attention?", "client_message_id": "mobile-1"},
    )
    assert added.status_code == 201

    own = client.get(
        f"/api/agent/conversations/{conversation_id}",
        headers={"Authorization": "Bearer agent-token-a"},
    )
    assert own.status_code == 200
    assert own.json()["messages"][0]["text"] == "What needs attention?"
    assert own.json()["messages_total"] == 1
    assert own.json()["messages_offset"] == 0
    assert own.json()["messages_has_more"] is False

    for index in range(2, 4):
        response = client.post(
            f"/api/agent/conversations/{conversation_id}/messages",
            headers={"Authorization": "Bearer agent-token-a"},
            json={"text": f"Follow-up {index}", "client_message_id": f"mobile-{index}"},
        )
        assert response.status_code == 201
    paged = client.get(
        f"/api/agent/conversations/{conversation_id}",
        params={"message_limit": 1, "message_offset": 1},
        headers={"Authorization": "Bearer agent-token-a"},
    )
    assert paged.status_code == 200
    assert paged.json()["messages_total"] == 3
    assert paged.json()["messages_offset"] == 1
    assert paged.json()["messages_has_more"] is True
    assert [message["text"] for message in paged.json()["messages"]] == ["Follow-up 2"]

    same_workspace_other_user = client.get(
        f"/api/agent/conversations/{conversation_id}",
        headers={"Authorization": "Bearer agent-token-c"},
    )
    cross_workspace_user = client.get(
        f"/api/agent/conversations/{conversation_id}",
        headers={"Authorization": "Bearer agent-token-b"},
    )
    assert same_workspace_other_user.status_code == 404
    assert cross_workspace_user.status_code == 404


def test_message_idempotency_and_archival_are_durable(agent_foundation_db):
    factory, contexts, _state = agent_foundation_db
    with _scoped(factory, contexts["a"]) as db:
        service = UnifiedAgentService(db, _agent_settings())
        conversation = service.create_conversation(
            owner_user_id=contexts["a"].user_id,
            title="Mobile-safe thread",
        )
        first = service.append_user_message(
            conversation.public_id,
            owner_user_id=contexts["a"].user_id,
            text="Show this month's spending",
            client_message_id="ios-42",
        )
        duplicate = service.append_user_message(
            conversation.public_id,
            owner_user_id=contexts["a"].user_id,
            text="Show this month's spending",
            client_message_id="ios-42",
        )
        assert duplicate.id == first.id

        with pytest.raises(AgentConflictError, match="different content"):
            service.append_user_message(
                conversation.public_id,
                owner_user_id=contexts["a"].user_id,
                text="Different request",
                client_message_id="ios-42",
            )

        archived = service.archive_conversation(
            conversation.public_id,
            owner_user_id=contexts["a"].user_id,
        )
        assert archived.status == "archived"
        with pytest.raises(AgentConflictError, match="Archived conversations"):
            service.append_user_message(
                conversation.public_id,
                owner_user_id=contexts["a"].user_id,
                text="One more message",
            )


def test_run_tool_call_and_structured_response_metadata_persist_without_prompts(
    agent_foundation_db,
):
    factory, contexts, _state = agent_foundation_db
    with _scoped(factory, contexts["a"]) as db:
        service = UnifiedAgentService(db, _agent_settings())
        conversation = service.create_conversation(owner_user_id=contexts["a"].user_id)
        trigger = service.append_user_message(
            conversation.public_id,
            owner_user_id=contexts["a"].user_id,
            text="Why did dining increase?",
        )
        response = AgentStructuredResponse(
            blocks=[
                {
                    "type": "spending_summary",
                    "title": "Dining",
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-14",
                    "currency_code": "USD",
                    "total_cents": 12_500,
                }
            ]
        )
        assistant_message = service.append_assistant_message(
            conversation.public_id,
            owner_user_id=contexts["a"].user_id,
            response=response,
        )
        assert assistant_message.content is None
        assert assistant_message.structured_response_json["schema_version"] == "1.0"

        run = service.create_run(
            conversation.public_id,
            owner_user_id=contexts["a"].user_id,
            trigger_message_public_id=trigger.public_id,
            page_context=AgentPageContext(
                surface="expense_insights",
                filters={"date_preset": "this_month", "currency_code": "USD"},
            ),
            model_name="future-model",
            prompt_version="agent-foundation-v1",
            request_id="request-1",
            correlation_id="correlation-1",
        )
        assert run.page_context_json["surface"] == "expense_insights"
        assert not hasattr(run, "raw_prompt")
        run = service.start_run(run.public_id, owner_user_id=contexts["a"].user_id)
        registry, tool_context, prepared = _prepare_read_dispatch(db)
        service.tool_registry = registry
        tool_call = service.record_tool_call(
            run.public_id,
            owner_user_id=contexts["a"].user_id,
            dispatch=prepared,
        )
        assert tool_call.sequence == 0
        assert tool_call.status == "proposed"
        service.start_tool_call(
            tool_call.public_id,
            owner_user_id=contexts["a"].user_id,
        )
        executed = registry.execute_read(prepared, context=tool_context)
        assert executed.output == {"total_cents": 12_500}
        tool_call = service.complete_tool_call(
            tool_call.public_id,
            owner_user_id=contexts["a"].user_id,
            dispatch=executed,
            latency_ms=25,
        )
        assert tool_call.status == "completed"
        assert tool_call.latency_ms == 25
        assert tool_call.result_metadata_json["output_schema_validated"] is True
        assert len(tool_call.result_metadata_json["output_sha256"]) == 64
        with pytest.raises(AgentConflictError, match="cannot be completed"):
            service.complete_tool_call(
                tool_call.public_id,
                owner_user_id=contexts["a"].user_id,
                dispatch=executed,
            )
        completed = service.complete_run(
            run.public_id,
            owner_user_id=contexts["a"].user_id,
            latency_ms=250,
            input_tokens=100,
            output_tokens=25,
            estimated_cost_micros=120,
        )
        assert completed.status == "completed"
        assert completed.total_tokens == 125

        with pytest.raises(AgentNotFoundError):
            service.get_conversation("guessed-id", owner_user_id=contexts["a"].user_id)


def test_action_proposal_preserves_exact_snapshot_and_confirmation_never_executes(
    agent_foundation_db,
):
    factory, contexts, _state = agent_foundation_db
    settings = _agent_settings(write=True)
    with _scoped(factory, contexts["a"]) as db:
        service = UnifiedAgentService(db, settings)
        conversation = service.create_conversation(owner_user_id=contexts["a"].user_id)
        run = service.create_run(
            conversation.public_id,
            owner_user_id=contexts["a"].user_id,
            trigger_message_public_id=None,
            page_context=None,
            model_name=None,
            prompt_version="agent-foundation-v1",
        )
        run = service.start_run(run.public_id, owner_user_id=contexts["a"].user_id)
        registry, dispatch = _prepare_write_dispatch(db)
        service.tool_registry = registry
        tool_call = service.record_tool_call(
            run.public_id,
            owner_user_id=contexts["a"].user_id,
            dispatch=dispatch,
        )
        parameters = {
            "transaction_public_id": "transaction-123",
            "classification": "personal",
        }
        proposal = service.create_action_proposal(
            conversation.public_id,
            owner_user_id=contexts["a"].user_id,
            dispatch=dispatch,
            tool_call_public_id=tool_call.public_id,
            idempotency_key="proposal-mobile-1",
        )
        duplicate = service.create_action_proposal(
            conversation.public_id,
            owner_user_id=contexts["a"].user_id,
            dispatch=dispatch,
            tool_call_public_id=tool_call.public_id,
            idempotency_key="proposal-mobile-1",
        )
        assert duplicate.id == proposal.id
        assert proposal.normalized_parameters_json == parameters
        assert len(proposal.parameters_hash) == 64
        assert proposal.attempt_count == 0

        with pytest.raises(AgentConflictError, match="different action"):
            _registry, other_dispatch = _prepare_write_dispatch(
                db,
                transaction_public_id="transaction-456",
                registry=registry,
            )
            other_tool_call = service.record_tool_call(
                run.public_id,
                owner_user_id=contexts["a"].user_id,
                dispatch=other_dispatch,
            )
            service.create_action_proposal(
                conversation.public_id,
                owner_user_id=contexts["a"].user_id,
                dispatch=other_dispatch,
                tool_call_public_id=other_tool_call.public_id,
                idempotency_key="proposal-mobile-1",
            )

        confirmed = service.confirm_action_proposal(
            proposal.public_id,
            owner_user_id=contexts["a"].user_id,
            expected_version=1,
        )
        assert confirmed.status == "confirmed"
        assert confirmed.version == 2
        assert confirmed.normalized_parameters_json == parameters
        assert confirmed.execution_started_at is None
        assert confirmed.completed_at is None
        assert confirmed.attempt_count == 0

        with pytest.raises(AgentConflictError, match="refresh"):
            service.confirm_action_proposal(
                proposal.public_id,
                owner_user_id=contexts["a"].user_id,
                expected_version=1,
            )
        stored = db.scalar(select(AgentActionProposal).where(AgentActionProposal.id == proposal.id))
        assert stored.status == "confirmed"


def test_proposal_provenance_archive_and_integrity_are_enforced(agent_foundation_db):
    factory, contexts, _state = agent_foundation_db
    settings = _agent_settings(write=True)
    with _scoped(factory, contexts["a"]) as db:
        first = UnifiedAgentService(db, settings).create_conversation(
            owner_user_id=contexts["a"].user_id,
            title="First thread",
        )
        second = UnifiedAgentService(db, settings).create_conversation(
            owner_user_id=contexts["a"].user_id,
            title="Second thread",
        )
        registry, dispatch = _prepare_write_dispatch(db)
        service = UnifiedAgentService(db, settings, tool_registry=registry)
        run = service.create_run(
            first.public_id,
            owner_user_id=contexts["a"].user_id,
            trigger_message_public_id=None,
            page_context=None,
            model_name=None,
            prompt_version="agent-foundation-v1",
        )
        run = service.start_run(run.public_id, owner_user_id=contexts["a"].user_id)
        tool_call = service.record_tool_call(
            run.public_id,
            owner_user_id=contexts["a"].user_id,
            dispatch=dispatch,
        )
        with pytest.raises(AgentConflictError, match="does not belong"):
            service.create_action_proposal(
                second.public_id,
                owner_user_id=contexts["a"].user_id,
                dispatch=dispatch,
                tool_call_public_id=tool_call.public_id,
                idempotency_key="cross-thread",
            )

        proposal = service.create_action_proposal(
            first.public_id,
            owner_user_id=contexts["a"].user_id,
            dispatch=dispatch,
            tool_call_public_id=tool_call.public_id,
            idempotency_key="archive-cancels",
        )
        second_run = service.create_run(
            second.public_id,
            owner_user_id=contexts["a"].user_id,
            trigger_message_public_id=None,
            page_context=None,
            model_name=None,
            prompt_version="agent-foundation-v1",
        )
        second_run = service.start_run(
            second_run.public_id,
            owner_user_id=contexts["a"].user_id,
        )
        second_call = service.record_tool_call(
            second_run.public_id,
            owner_user_id=contexts["a"].user_id,
            dispatch=dispatch,
        )
        with pytest.raises(AgentConflictError, match="another conversation"):
            service.create_action_proposal(
                second.public_id,
                owner_user_id=contexts["a"].user_id,
                dispatch=dispatch,
                tool_call_public_id=second_call.public_id,
                idempotency_key="same-action-other-thread",
            )
        service.archive_conversation(first.public_id, owner_user_id=contexts["a"].user_id)
        db.expire(proposal)
        assert proposal.status == "cancelled"
        assert proposal.version == 2
        with pytest.raises(AgentConflictError, match="Archived conversations"):
            service.confirm_action_proposal(
                proposal.public_id,
                owner_user_id=contexts["a"].user_id,
                expected_version=2,
            )

        integrity_conversation = service.create_conversation(
            owner_user_id=contexts["a"].user_id,
            title="Integrity thread",
        )
        integrity_run = service.create_run(
            integrity_conversation.public_id,
            owner_user_id=contexts["a"].user_id,
            trigger_message_public_id=None,
            page_context=None,
            model_name=None,
            prompt_version="agent-foundation-v1",
        )
        integrity_run = service.start_run(
            integrity_run.public_id,
            owner_user_id=contexts["a"].user_id,
        )
        integrity_call = service.record_tool_call(
            integrity_run.public_id,
            owner_user_id=contexts["a"].user_id,
            dispatch=dispatch,
        )
        integrity_proposal = service.create_action_proposal(
            integrity_conversation.public_id,
            owner_user_id=contexts["a"].user_id,
            dispatch=dispatch,
            tool_call_public_id=integrity_call.public_id,
            idempotency_key="integrity-check",
        )
        db.execute(
            update(AgentActionProposal)
            .where(AgentActionProposal.id == integrity_proposal.id)
            .values(normalized_parameters_json={"classification": "shared"})
            .execution_options(synchronize_session=False)
        )
        db.commit()
        db.expire_all()
        with pytest.raises(AgentConflictError, match="integrity validation"):
            service.confirm_action_proposal(
                integrity_proposal.public_id,
                owner_user_id=contexts["a"].user_id,
                expected_version=1,
            )
        stored = db.scalar(
            select(AgentActionProposal).where(AgentActionProposal.id == integrity_proposal.id)
        )
        assert stored.status == "ambiguous"
        assert stored.error_code == "proposal_integrity_failed"

        _registry, preview_dispatch = _prepare_write_dispatch(
            db,
            transaction_public_id="transaction-preview",
            registry=registry,
        )
        preview_call = service.record_tool_call(
            integrity_run.public_id,
            owner_user_id=contexts["a"].user_id,
            dispatch=preview_dispatch,
        )
        preview_proposal = service.create_action_proposal(
            integrity_conversation.public_id,
            owner_user_id=contexts["a"].user_id,
            dispatch=preview_dispatch,
            tool_call_public_id=preview_call.public_id,
            idempotency_key="preview-integrity-check",
        )
        db.execute(
            update(AgentActionProposal)
            .where(AgentActionProposal.id == preview_proposal.id)
            .values(
                preview_json={
                    "title": "Misleading preview",
                    "summary": "Display an action the user did not request.",
                }
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        db.expire_all()
        with pytest.raises(AgentConflictError, match="integrity validation"):
            service.confirm_action_proposal(
                preview_proposal.public_id,
                owner_user_id=contexts["a"].user_id,
                expected_version=1,
            )


def test_proposal_idempotency_is_private_per_user_in_a_shared_workspace(
    agent_foundation_db,
):
    factory, contexts, _state = agent_foundation_db
    proposal_ids: list[int] = []
    for context_name in ("a", "c"):
        context = contexts[context_name]
        with _scoped(factory, context) as db:
            registry, dispatch = _prepare_write_dispatch(db)
            service = UnifiedAgentService(
                db,
                _agent_settings(write=True),
                tool_registry=registry,
            )
            conversation = service.create_conversation(owner_user_id=context.user_id)
            run = service.create_run(
                conversation.public_id,
                owner_user_id=context.user_id,
                trigger_message_public_id=None,
                page_context=None,
                model_name=None,
                prompt_version="agent-foundation-v1",
            )
            run = service.start_run(run.public_id, owner_user_id=context.user_id)
            call = service.record_tool_call(
                run.public_id,
                owner_user_id=context.user_id,
                dispatch=dispatch,
            )
            proposal = service.create_action_proposal(
                conversation.public_id,
                owner_user_id=context.user_id,
                dispatch=dispatch,
                tool_call_public_id=call.public_id,
                idempotency_key="mobile-common-key",
            )
            proposal_ids.append(proposal.id)

    assert len(set(proposal_ids)) == 2


def test_expired_proposal_does_not_block_a_fresh_exact_action(agent_foundation_db):
    factory, contexts, _state = agent_foundation_db
    with _scoped(factory, contexts["a"]) as db:
        registry, dispatch = _prepare_write_dispatch(db)
        service = UnifiedAgentService(
            db,
            _agent_settings(write=True),
            tool_registry=registry,
        )
        conversation = service.create_conversation(owner_user_id=contexts["a"].user_id)
        run = service.create_run(
            conversation.public_id,
            owner_user_id=contexts["a"].user_id,
            trigger_message_public_id=None,
            page_context=None,
            model_name=None,
            prompt_version="agent-foundation-v1",
        )
        run = service.start_run(run.public_id, owner_user_id=contexts["a"].user_id)
        call = service.record_tool_call(
            run.public_id,
            owner_user_id=contexts["a"].user_id,
            dispatch=dispatch,
        )
        original = service.create_action_proposal(
            conversation.public_id,
            owner_user_id=contexts["a"].user_id,
            dispatch=dispatch,
            tool_call_public_id=call.public_id,
            idempotency_key="expiring-action",
        )
        db.execute(
            update(AgentActionProposal)
            .where(AgentActionProposal.id == original.id)
            .values(expires_at=utc_now() - timedelta(seconds=1))
            .execution_options(synchronize_session=False)
        )
        db.commit()

        replacement = service.create_action_proposal(
            conversation.public_id,
            owner_user_id=contexts["a"].user_id,
            dispatch=dispatch,
            tool_call_public_id=call.public_id,
            idempotency_key="replacement-action",
        )
        db.expire(original)
        assert original.status == "expired"
        assert replacement.id != original.id
        assert replacement.status == "awaiting_confirmation"


def test_expired_same_key_transition_is_durable(agent_foundation_db):
    factory, contexts, _state = agent_foundation_db
    context = contexts["a"]
    with _scoped(factory, context) as db:
        registry, dispatch = _prepare_write_dispatch(db)
        service = UnifiedAgentService(db, _agent_settings(write=True), tool_registry=registry)
        conversation = service.create_conversation(owner_user_id=context.user_id)
        run = service.create_run(
            conversation.public_id,
            owner_user_id=context.user_id,
            trigger_message_public_id=None,
            page_context=None,
            model_name=None,
            prompt_version="agent-foundation-v1",
        )
        run = service.start_run(run.public_id, owner_user_id=context.user_id)
        call = service.record_tool_call(
            run.public_id,
            owner_user_id=context.user_id,
            dispatch=dispatch,
        )
        original = service.create_action_proposal(
            conversation.public_id,
            owner_user_id=context.user_id,
            dispatch=dispatch,
            tool_call_public_id=call.public_id,
            idempotency_key="expired-same-key",
        )
        db.execute(
            update(AgentActionProposal)
            .where(AgentActionProposal.id == original.id)
            .values(expires_at=utc_now() - timedelta(seconds=1))
            .execution_options(synchronize_session=False)
        )
        db.commit()

        reused = service.create_action_proposal(
            conversation.public_id,
            owner_user_id=context.user_id,
            dispatch=dispatch,
            tool_call_public_id=call.public_id,
            idempotency_key="expired-same-key",
        )
        assert reused.id == original.id
        assert reused.status == "expired"

    with _scoped(factory, context) as db:
        persisted = db.get(AgentActionProposal, original.id)
        assert persisted is not None
        assert persisted.status == "expired"


def test_purchasing_kill_switch_is_rechecked_at_confirmation(agent_foundation_db):
    factory, contexts, _state = agent_foundation_db
    context = contexts["a"]
    with _scoped(factory, context) as db:
        settings = _agent_settings(write=True, purchasing=True)
        registry = AgentToolRegistry(settings)
        registry.register(
            AgentTool(
                name="purchase_test_item",
                description="Prepare a future purchasing action.",
                effect=ToolEffect.EXTERNAL_ACTION,
                capability=ToolCapability.PURCHASING,
                input_model=ClassificationInput,
                output_model=ClassificationOutput,
                handler=lambda _context, _values: {"accepted": True},
                confirmation_required=True,
                preview_builder=lambda _context, values: {
                    "title": "Review purchase",
                    "summary": f"Purchase {values.transaction_public_id}.",
                },
            )
        )
        dispatch = registry.prepare(
            "purchase_test_item",
            {
                "transaction_public_id": "item-123",
                "classification": "purchase",
            },
            context=AgentToolContext.from_session(db),
        )
        service = UnifiedAgentService(db, settings, tool_registry=registry)
        conversation = service.create_conversation(owner_user_id=context.user_id)
        run = service.create_run(
            conversation.public_id,
            owner_user_id=context.user_id,
            trigger_message_public_id=None,
            page_context=None,
            model_name=None,
            prompt_version="agent-foundation-v1",
        )
        run = service.start_run(run.public_id, owner_user_id=context.user_id)
        call = service.record_tool_call(
            run.public_id,
            owner_user_id=context.user_id,
            dispatch=dispatch,
        )
        proposal = service.create_action_proposal(
            conversation.public_id,
            owner_user_id=context.user_id,
            dispatch=dispatch,
            tool_call_public_id=call.public_id,
            idempotency_key="purchasing-kill-switch",
        )

        settings.agent_purchasing_enabled = False
        with pytest.raises(AgentFeatureDisabledError) as disabled:
            service.confirm_action_proposal(
                proposal.public_id,
                owner_user_id=context.user_id,
                expected_version=proposal.version,
            )
        assert disabled.value.code == "agent_purchasing_disabled"
        db.refresh(proposal)
        assert proposal.status == "awaiting_confirmation"


def test_failed_run_and_tool_call_persist_only_redacted_errors(agent_foundation_db):
    factory, contexts, _state = agent_foundation_db
    with _scoped(factory, contexts["a"]) as db:
        service = UnifiedAgentService(db, _agent_settings())
        conversation = service.create_conversation(owner_user_id=contexts["a"].user_id)
        run = service.create_run(
            conversation.public_id,
            owner_user_id=contexts["a"].user_id,
            trigger_message_public_id=None,
            page_context=None,
            model_name="future-model",
            prompt_version="agent-foundation-v1",
        )
        run = service.start_run(run.public_id, owner_user_id=contexts["a"].user_id)
        with pytest.raises(AgentFoundationError, match="snake_case"):
            service.fail_run(
                run.public_id,
                owner_user_id=contexts["a"].user_id,
                error_code="Provider Error",
                error_message="must not be stored",
            )
        failed_run = service.fail_run(
            run.public_id,
            owner_user_id=contexts["a"].user_id,
            error_code="provider_failure",
            error_message=("Bearer session-value api_key=api-value sk-12345678 ghp_12345678"),
            latency_ms=75,
        )
        assert failed_run.status == "failed"
        assert failed_run.error_message == "The agent operation could not be completed."
        for secret in ("session-value", "api-value", "sk-12345678", "ghp_12345678"):
            assert secret not in failed_run.error_message
        with pytest.raises(AgentConflictError, match="cannot be completed"):
            service.complete_run(
                run.public_id,
                owner_user_id=contexts["a"].user_id,
            )

        second_run = service.create_run(
            conversation.public_id,
            owner_user_id=contexts["a"].user_id,
            trigger_message_public_id=None,
            page_context=None,
            model_name="future-model",
            prompt_version="agent-foundation-v1",
        )
        second_run = service.start_run(
            second_run.public_id,
            owner_user_id=contexts["a"].user_id,
        )

        def fail_handler(_context, _values):
            raise RuntimeError("provider failed")

        registry = AgentToolRegistry(_agent_settings())
        registry.register(
            AgentTool(
                name="failing_read",
                description="Exercise durable failed-tool metadata.",
                effect=ToolEffect.READ,
                input_model=SpendingInput,
                output_model=SpendingOutput,
                handler=fail_handler,
            )
        )
        service.tool_registry = registry
        tool_context = AgentToolContext.from_session(db, request_id="request-failure")
        prepared = registry.prepare(
            "failing_read",
            {"date_preset": "this_month"},
            context=tool_context,
        )
        call = service.record_tool_call(
            second_run.public_id,
            owner_user_id=contexts["a"].user_id,
            dispatch=prepared,
        )
        service.start_tool_call(call.public_id, owner_user_id=contexts["a"].user_id)
        with pytest.raises(RuntimeError, match="provider failed"):
            registry.execute_read(prepared, context=tool_context)
        failed_call = service.fail_tool_call(
            call.public_id,
            owner_user_id=contexts["a"].user_id,
            error_code="provider_failure",
            error_message="Authorization: Bearer another-session-value",
            latency_ms=20,
        )
        assert failed_call.status == "failed"
        assert "another-session-value" not in failed_call.error_message
        assert failed_call.error_message == "The agent operation could not be completed."


def test_proposals_require_the_write_flag_and_a_trusted_registry(agent_foundation_db):
    factory, contexts, _state = agent_foundation_db
    with _scoped(factory, contexts["a"]) as db:
        read_only_service = UnifiedAgentService(db, _agent_settings())
        conversation = read_only_service.create_conversation(owner_user_id=contexts["a"].user_id)
        run = read_only_service.create_run(
            conversation.public_id,
            owner_user_id=contexts["a"].user_id,
            trigger_message_public_id=None,
            page_context=None,
            model_name=None,
            prompt_version="agent-foundation-v1",
        )
        run = read_only_service.start_run(
            run.public_id,
            owner_user_id=contexts["a"].user_id,
        )
        registry, dispatch = _prepare_write_dispatch(db)
        read_only_service.tool_registry = registry
        tool_call = read_only_service.record_tool_call(
            run.public_id,
            owner_user_id=contexts["a"].user_id,
            dispatch=dispatch,
        )
        with pytest.raises(AgentFeatureDisabledError):
            read_only_service.create_action_proposal(
                conversation.public_id,
                owner_user_id=contexts["a"].user_id,
                dispatch=dispatch,
                tool_call_public_id=tool_call.public_id,
                idempotency_key="proposal-read-only",
            )

        write_service = UnifiedAgentService(
            db,
            _agent_settings(write=True),
            tool_registry=registry,
        )
        sensitive_dispatch = AgentToolDispatchResult(
            tool_name="prepare_split",
            tool_version="1.0",
            effect=ToolEffect.EXTERNAL_ACTION,
            disposition=ToolDisposition.PROPOSAL_REQUIRED,
            normalized_arguments={"oauth_token": "must-never-be-persisted"},
            preview={
                "title": "Prepare split",
                "summary": "Prepare an equal split.",
            },
        )
        with pytest.raises(AgentFoundationError, match="trusted registry"):
            write_service.create_action_proposal(
                conversation.public_id,
                owner_user_id=contexts["a"].user_id,
                dispatch=sensitive_dispatch,
                tool_call_public_id=tool_call.public_id,
                idempotency_key="proposal-sensitive",
            )
        assert db.scalar(select(AgentActionProposal)) is None


def test_run_page_context_rejects_tenant_identity(agent_foundation_db):
    factory, contexts, _state = agent_foundation_db
    with _scoped(factory, contexts["a"]) as db:
        service = UnifiedAgentService(db, _agent_settings())
        conversation = service.create_conversation(owner_user_id=contexts["a"].user_id)
        with pytest.raises(ValidationError, match="workspace_id"):
            service.create_run(
                conversation.public_id,
                owner_user_id=contexts["a"].user_id,
                trigger_message_public_id=None,
                page_context={
                    "surface": "expense_review",
                    "workspace_id": contexts["b"].workspace_id,
                },
                model_name=None,
                prompt_version="agent-foundation-v1",
            )

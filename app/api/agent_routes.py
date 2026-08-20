from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.agent.action_tools import register_action_tools
from app.agent.actions import (
    AgentActionExecutor,
    action_confirmation_block,
    refresh_action_confirmation_blocks,
)
from app.agent.contracts import (
    AgentActionConfirmationBlock,
    AgentActionDecisionRequest,
    AgentCapabilities,
    AgentConversationCreate,
    AgentConversationDetail,
    AgentConversationOut,
    AgentFeedbackOut,
    AgentFeedbackRequest,
    AgentMessageCreate,
    AgentMessageOut,
    AgentReviewCandidateSummary,
    AgentReviewReceiptSummary,
    AgentReviewSessionAdvanceRequest,
    AgentReviewSessionInterpretRequest,
    AgentReviewSessionInterpretResult,
    AgentReviewSessionOut,
    AgentReviewSessionProgress,
    AgentReviewSessionProposeRequest,
    AgentReviewSessionStartRequest,
    AgentReviewTransactionSummary,
    AgentTurnCreate,
    AgentTurnOut,
    hydrate_persisted_agent_response,
)
from app.agent.read_tools import build_read_tool_registry
from app.agent.review_session import ReviewSessionService
from app.agent.runtime import AgentRuntimeError, ReadOnlyAgentOrchestrator
from app.agent.service import (
    AgentConflictError,
    AgentFeatureDisabledError,
    AgentFoundationError,
    AgentMessageFeedbackState,
    AgentNotFoundError,
    UnifiedAgentService,
)
from app.agent.tooling import AgentActionClarificationRequired
from app.api.deps import CurrentUser, CurrentWorkspace, DbSession
from app.config import get_settings
from app.logging_config import log_event
from app.models import AgentConversation, AgentMessage, AgentReviewSession
from app.rate_limit import rate_limiter

router = APIRouter(prefix="/api/agent", tags=["agent"])
logger = logging.getLogger(__name__)


@router.get("/capabilities", response_model=AgentCapabilities)
def capabilities(
    _db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> AgentCapabilities:
    settings = get_settings()
    enabled = settings.agent_enabled
    read_enabled = enabled and settings.agent_read_tools_enabled
    write_enabled = enabled and settings.agent_write_actions_enabled
    return AgentCapabilities(
        enabled=enabled,
        read_tools_enabled=read_enabled,
        write_actions_enabled=write_enabled,
        proactive_enabled=(enabled and read_enabled and settings.agent_proactive_enabled),
        purchasing_enabled=(enabled and write_enabled and settings.agent_purchasing_enabled),
    )


@router.post(
    "/conversations",
    response_model=AgentConversationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    payload: AgentConversationCreate,
    db: DbSession,
    user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> AgentConversationOut:
    try:
        value = UnifiedAgentService(db).create_conversation(
            owner_user_id=user.id,
            title=payload.title,
        )
    except AgentFoundationError as exc:
        _raise_agent_error(exc)
    return _conversation_out(value)


@router.get("/conversations", response_model=list[AgentConversationOut])
def list_conversations(
    db: DbSession,
    user: CurrentUser,
    _workspace: CurrentWorkspace,
    include_archived: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[AgentConversationOut]:
    try:
        values = UnifiedAgentService(db).list_conversations(
            owner_user_id=user.id,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
    except AgentFoundationError as exc:
        _raise_agent_error(exc)
    return [_conversation_out(value) for value in values]


@router.get(
    "/conversations/{conversation_public_id}",
    response_model=AgentConversationDetail,
)
def get_conversation(
    conversation_public_id: str,
    db: DbSession,
    user: CurrentUser,
    _workspace: CurrentWorkspace,
    message_limit: int = Query(default=100, ge=1, le=500),
    message_offset: int = Query(default=0, ge=0),
) -> AgentConversationDetail:
    service = UnifiedAgentService(db)
    try:
        conversation = service.get_conversation(
            conversation_public_id,
            owner_user_id=user.id,
        )
        messages = service.list_messages(
            conversation_public_id,
            owner_user_id=user.id,
            limit=message_limit,
            offset=message_offset,
        )
        messages_total = service.count_messages(
            conversation_public_id,
            owner_user_id=user.id,
        )
        feedback_states = service.feedback_states_for_messages(
            conversation_public_id,
            owner_user_id=user.id,
            messages=messages,
        )
        proposal_states = service.action_proposals_for_messages(
            owner_user_id=user.id,
            messages=messages,
        )
    except AgentFoundationError as exc:
        _raise_agent_error(exc)
    return AgentConversationDetail(
        conversation=_conversation_out(conversation),
        messages=[
            _message_out(
                message,
                conversation.public_id,
                feedback_state=feedback_states.get(message.public_id),
                proposal_states=proposal_states,
            )
            for message in messages
        ],
        messages_total=messages_total,
        messages_offset=message_offset,
        messages_has_more=message_offset + len(messages) < messages_total,
    )


@router.post(
    "/conversations/{conversation_public_id}/messages",
    response_model=AgentMessageOut,
    status_code=status.HTTP_201_CREATED,
)
def append_user_message(
    conversation_public_id: str,
    payload: AgentMessageCreate,
    db: DbSession,
    user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> AgentMessageOut:
    try:
        message = UnifiedAgentService(db).append_user_message(
            conversation_public_id,
            owner_user_id=user.id,
            text=payload.text,
            client_message_id=payload.client_message_id,
        )
    except AgentFoundationError as exc:
        _raise_agent_error(exc)
    return _message_out(message, conversation_public_id)


@router.post(
    "/messages/{message_public_id}/feedback",
    response_model=AgentFeedbackOut,
)
def record_message_feedback(
    message_public_id: str,
    payload: AgentFeedbackRequest,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> AgentFeedbackOut:
    """Record one closed, privacy-minimized rating for a completed answer."""

    _check_agent_rate_limit(
        f"agent-feedback:{workspace.id}:{user.id}",
        limit=30,
        window_seconds=60,
        operation="feedback",
    )
    try:
        return UnifiedAgentService(db).upsert_message_feedback(
            message_public_id,
            owner_user_id=user.id,
            rating=payload.rating,
            reason=payload.reason,
        )
    except AgentFoundationError as exc:
        _raise_agent_error(exc)


@router.post(
    "/proposals/{proposal_public_id}/confirm",
    response_model=AgentActionConfirmationBlock,
)
def confirm_action_proposal(
    proposal_public_id: str,
    payload: AgentActionDecisionRequest,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> AgentActionConfirmationBlock:
    """Confirm and execute one frozen proposal without invoking OpenAI."""

    _check_agent_rate_limit(
        f"agent-action:{workspace.id}:{user.id}",
        limit=10,
        window_seconds=60,
        operation="confirm_action",
    )
    try:
        proposal = _build_action_executor(db).confirm_and_execute(
            proposal_public_id,
            owner_user_id=user.id,
            expected_version=payload.proposal_version,
        )
        return action_confirmation_block(proposal)
    except AgentFoundationError as exc:
        _raise_agent_error(exc)


@router.post(
    "/proposals/{proposal_public_id}/cancel",
    response_model=AgentActionConfirmationBlock,
)
def cancel_action_proposal(
    proposal_public_id: str,
    payload: AgentActionDecisionRequest,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> AgentActionConfirmationBlock:
    """Cancel one awaiting proposal; no domain mutation is performed."""

    _check_agent_rate_limit(
        f"agent-action:{workspace.id}:{user.id}",
        limit=10,
        window_seconds=60,
        operation="cancel_action",
    )
    try:
        proposal = _build_action_executor(db).cancel(
            proposal_public_id,
            owner_user_id=user.id,
            expected_version=payload.proposal_version,
        )
        return action_confirmation_block(proposal)
    except AgentFoundationError as exc:
        _raise_agent_error(exc)


@router.post(
    "/conversations/{conversation_public_id}/turns",
    response_model=AgentTurnOut,
    status_code=status.HTTP_201_CREATED,
)
async def run_read_only_turn(
    conversation_public_id: str,
    payload: AgentTurnCreate,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> AgentTurnOut:
    """Run one bounded, idempotent Agent turn under server-owned capabilities."""

    settings = get_settings()
    if not settings.agent_enabled or not settings.agent_read_tools_enabled:
        raise HTTPException(status_code=404, detail="Agent resource not found")
    _check_agent_rate_limit(
        f"agent-turn:{workspace.id}:{user.id}",
        limit=10,
        window_seconds=60,
        operation="turn",
    )
    try:
        return await _build_read_only_orchestrator(db).run_turn(
            conversation_public_id,
            owner_user_id=user.id,
            text=payload.text,
            client_message_id=payload.client_message_id,
            page_context=payload.page_context,
        )
    except AgentFoundationError as exc:
        _raise_agent_error(exc)
    except AgentRuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The agent service is temporarily unavailable",
        ) from exc


@router.post("/conversations/{conversation_public_id}/turns/stream")
async def stream_read_only_turn(
    conversation_public_id: str,
    payload: AgentTurnCreate,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> StreamingResponse:
    """Stream one bounded Agent turn using ExpenseOps-owned semantic events."""

    settings = get_settings()
    if not settings.agent_enabled or not settings.agent_read_tools_enabled:
        raise HTTPException(status_code=404, detail="Agent resource not found")
    _check_agent_rate_limit(
        f"agent-turn:{workspace.id}:{user.id}",
        limit=10,
        window_seconds=60,
        operation="stream_turn",
    )
    orchestrator = _build_read_only_orchestrator(db)
    try:
        orchestrator.preflight_turn(
            conversation_public_id,
            owner_user_id=user.id,
            page_context=payload.page_context,
        )
    except AgentFoundationError as exc:
        _raise_agent_error(exc)

    async def events():
        async for event in orchestrator.stream_turn(
            conversation_public_id,
            owner_user_id=user.id,
            text=payload.text,
            client_message_id=payload.client_message_id,
            page_context=payload.page_context,
        ):
            yield _semantic_sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/review-session/start",
    response_model=AgentReviewSessionOut,
    status_code=status.HTTP_201_CREATED,
)
def start_review_session(
    payload: AgentReviewSessionStartRequest,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> AgentReviewSessionOut:
    """Start (or resume) the Agent's own one-by-one review queue."""

    _check_agent_rate_limit(
        f"agent-review:{workspace.id}:{user.id}",
        limit=30,
        window_seconds=60,
        operation="review_session_start",
    )
    service = ReviewSessionService(db, get_settings())
    try:
        session = service.start_or_resume(payload.conversation_public_id, owner_user_id=user.id)
        session, live = service.current_candidate(session)
    except AgentFoundationError as exc:
        _raise_agent_error(exc)
    return _review_session_out(session, live)


@router.get("/review-session/{session_public_id}", response_model=AgentReviewSessionOut)
def get_review_session(
    session_public_id: str,
    db: DbSession,
    user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> AgentReviewSessionOut:
    service = ReviewSessionService(db, get_settings())
    try:
        session = service.get_session(session_public_id, owner_user_id=user.id)
        session, live = service.current_candidate(session)
    except AgentFoundationError as exc:
        _raise_agent_error(exc)
    return _review_session_out(session, live)


@router.post(
    "/review-session/{session_public_id}/propose",
    response_model=AgentActionConfirmationBlock,
)
def propose_review_session_action(
    session_public_id: str,
    payload: AgentReviewSessionProposeRequest,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> AgentActionConfirmationBlock:
    """Create a frozen proposal for the current candidate without an OpenAI call."""

    _check_agent_rate_limit(
        f"agent-review:{workspace.id}:{user.id}",
        limit=30,
        window_seconds=60,
        operation="review_session_propose",
    )
    service = ReviewSessionService(db, get_settings())
    registry = build_read_tool_registry(get_settings())
    register_action_tools(registry)
    try:
        session = service.get_session(session_public_id, owner_user_id=user.id)
        proposal = service.propose_action(
            session,
            registry=registry,
            action=payload.action,
            participant_names=payload.participant_names,
            group_name=payload.group_name,
        )
    except AgentFoundationError as exc:
        _raise_agent_error(exc)
    except AgentActionClarificationRequired as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return action_confirmation_block(proposal)


@router.post(
    "/review-session/{session_public_id}/interpret",
    response_model=AgentReviewSessionInterpretResult,
)
def interpret_review_session_message(
    session_public_id: str,
    payload: AgentReviewSessionInterpretRequest,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> AgentReviewSessionInterpretResult:
    """Resolve a free-typed chat message against the current candidate.

    Same intent-extraction + entity-resolution pipeline as Telegram's AI
    chat mode, reusing the existing propose_post_splitwise_expense tool for
    resolution rather than duplicating it.
    """

    _check_agent_rate_limit(
        f"agent-review:{workspace.id}:{user.id}",
        limit=30,
        window_seconds=60,
        operation="review_session_interpret",
    )
    service = ReviewSessionService(db, get_settings())
    registry = build_read_tool_registry(get_settings())
    register_action_tools(registry)
    try:
        session = service.get_session(session_public_id, owner_user_id=user.id)
        result = service.interpret_typed_message(session, registry=registry, text=payload.text)
    except AgentFoundationError as exc:
        _raise_agent_error(exc)
    except AgentActionClarificationRequired as exc:
        return AgentReviewSessionInterpretResult(status="clarify", message=str(exc))
    if result.status == "proposed" and result.proposal is not None:
        return AgentReviewSessionInterpretResult(
            status="proposed", confirmation=action_confirmation_block(result.proposal)
        )
    return AgentReviewSessionInterpretResult(status="clarify", message=result.message)


@router.post("/review-session/{session_public_id}/advance", response_model=AgentReviewSessionOut)
def advance_review_session(
    session_public_id: str,
    payload: AgentReviewSessionAdvanceRequest,
    db: DbSession,
    user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> AgentReviewSessionOut:
    """Record the outcome of a completed proposal and move to the next candidate."""

    service = ReviewSessionService(db, get_settings())
    try:
        session = service.get_session(session_public_id, owner_user_id=user.id)
        session = service.advance_after_proposal(
            session, proposal_public_id=payload.proposal_public_id
        )
        session, live = service.current_candidate(session)
    except AgentFoundationError as exc:
        _raise_agent_error(exc)
    return _review_session_out(session, live)


@router.post("/review-session/{session_public_id}/skip", response_model=AgentReviewSessionOut)
def skip_review_session_candidate(
    session_public_id: str,
    db: DbSession,
    user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> AgentReviewSessionOut:
    service = ReviewSessionService(db, get_settings())
    try:
        session = service.get_session(session_public_id, owner_user_id=user.id)
        session = service.skip_current(session)
        session, live = service.current_candidate(session)
    except AgentFoundationError as exc:
        _raise_agent_error(exc)
    return _review_session_out(session, live)


@router.post("/review-session/{session_public_id}/stop", response_model=AgentReviewSessionOut)
def stop_review_session(
    session_public_id: str,
    db: DbSession,
    user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> AgentReviewSessionOut:
    service = ReviewSessionService(db, get_settings())
    try:
        session = service.get_session(session_public_id, owner_user_id=user.id)
        session = service.stop(session)
    except AgentFoundationError as exc:
        _raise_agent_error(exc)
    return _review_session_out(session, None)


@router.delete(
    "/conversations/{conversation_public_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def archive_conversation(
    conversation_public_id: str,
    db: DbSession,
    user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> Response:
    try:
        UnifiedAgentService(db).archive_conversation(
            conversation_public_id,
            owner_user_id=user.id,
        )
    except AgentFoundationError as exc:
        _raise_agent_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _conversation_out(value: AgentConversation) -> AgentConversationOut:
    return AgentConversationOut(
        public_id=value.public_id,
        title=value.title,
        archived_at=value.archived_at,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _message_out(
    value: AgentMessage,
    conversation_public_id: str,
    *,
    feedback_state: AgentMessageFeedbackState | None = None,
    proposal_states: dict | None = None,
) -> AgentMessageOut:
    structured = (
        hydrate_persisted_agent_response(value.structured_response_json)
        if value.structured_response_json
        else None
    )
    if structured is not None and proposal_states:
        structured = refresh_action_confirmation_blocks(structured, proposal_states)
    return AgentMessageOut(
        public_id=value.public_id,
        conversation_public_id=conversation_public_id,
        role=value.role,
        text=value.content,
        structured_response=structured,
        client_message_id=value.client_message_id,
        feedback_eligible=feedback_state.eligible if feedback_state else False,
        feedback=feedback_state.feedback if feedback_state else None,
        created_at=value.created_at,
    )


def _review_session_out(
    session: AgentReviewSession, live: dict | None
) -> AgentReviewSessionOut:
    current = None
    if live is not None:
        transaction = None
        if live.get("transaction") is not None:
            data = live["transaction"]
            transaction = AgentReviewTransactionSummary(
                id=data["id"],
                merchant=data["merchant_name"] or data["name"] or "Purchase",
                amount_cents=data["amount_cents"],
                currency=data["currency"],
                occurred_on=data["date"],
                pending=bool(data["pending"]),
                institution_name=data["institution_name"],
            )
        receipt = None
        if live.get("receipt") is not None:
            data = live["receipt"]
            receipt = AgentReviewReceiptSummary(
                id=data["id"],
                merchant=data["merchant_name"] or "Receipt",
                total_cents=data["total_cents"],
                currency=data["currency"],
                line_count=data["line_count"],
            )
        recommendation = live.get("recommendation") or {}
        current = AgentReviewCandidateSummary(
            review_item_public_id=live["review_item_public_id"],
            kind=live["kind"],
            transaction=transaction,
            receipt=receipt,
            recommended_participant_names=recommendation.get("participant_names") or [],
            recommended_group_name=recommendation.get("group_name"),
            recommendation_label=recommendation.get("reason"),
            available_actions=live.get("available_actions") or [],
        )
    return AgentReviewSessionOut(
        public_id=session.public_id,
        conversation_public_id=session.conversation.public_id,
        progress=AgentReviewSessionProgress(**ReviewSessionService.summary(session)),
        current=current,
    )


def _build_read_only_orchestrator(db: DbSession) -> ReadOnlyAgentOrchestrator:
    """Small construction seam for deterministic route and integration tests."""

    return ReadOnlyAgentOrchestrator(db, settings=get_settings())


def _build_action_executor(db: DbSession) -> AgentActionExecutor:
    settings = get_settings()
    registry = build_read_tool_registry(settings)
    register_action_tools(registry)
    return AgentActionExecutor(db, registry=registry, settings=settings)


def _check_agent_rate_limit(
    key: str,
    *,
    limit: int,
    window_seconds: int,
    operation: str,
) -> None:
    try:
        rate_limiter.check(key, limit=limit, window_seconds=window_seconds)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            log_event(logger, "agent_rate_limited", operation=operation)
        raise


def _semantic_sse(event: BaseModel) -> str:
    event_type = getattr(event, "type", "message")
    sequence = getattr(event, "sequence", 0)
    return f"id: {sequence}\nevent: {event_type}\ndata: {event.model_dump_json()}\n\n"


def _raise_agent_error(exc: AgentFoundationError) -> None:
    if isinstance(exc, (AgentNotFoundError, AgentFeatureDisabledError)):
        raise HTTPException(status_code=404, detail="Agent resource not found") from exc
    if isinstance(exc, AgentConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc

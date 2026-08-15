from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response

from app.agent.contracts import (
    AgentCapabilities,
    AgentConversationCreate,
    AgentConversationDetail,
    AgentConversationOut,
    AgentMessageCreate,
    AgentMessageOut,
    AgentStructuredResponse,
    AgentTurnCreate,
    AgentTurnOut,
)
from app.agent.runtime import AgentRuntimeError, ReadOnlyAgentOrchestrator
from app.agent.service import (
    AgentConflictError,
    AgentFeatureDisabledError,
    AgentFoundationError,
    AgentNotFoundError,
    UnifiedAgentService,
)
from app.api.deps import CurrentUser, CurrentWorkspace, DbSession
from app.config import get_settings
from app.models import AgentConversation, AgentMessage
from app.rate_limit import rate_limiter

router = APIRouter(prefix="/api/agent", tags=["agent"])


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
    except AgentFoundationError as exc:
        _raise_agent_error(exc)
    return AgentConversationDetail(
        conversation=_conversation_out(conversation),
        messages=[_message_out(message, conversation.public_id) for message in messages],
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
    """Run one bounded, idempotent read-only agent turn."""

    settings = get_settings()
    if not settings.agent_enabled or not settings.agent_read_tools_enabled:
        raise HTTPException(status_code=404, detail="Agent resource not found")
    rate_limiter.check(
        f"agent-turn:{workspace.id}:{user.id}",
        limit=10,
        window_seconds=60,
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


def _message_out(value: AgentMessage, conversation_public_id: str) -> AgentMessageOut:
    structured = (
        AgentStructuredResponse.model_validate(value.structured_response_json)
        if value.structured_response_json
        else None
    )
    return AgentMessageOut(
        public_id=value.public_id,
        conversation_public_id=conversation_public_id,
        role=value.role,
        text=value.content,
        structured_response=structured,
        client_message_id=value.client_message_id,
        created_at=value.created_at,
    )


def _build_read_only_orchestrator(db: DbSession) -> ReadOnlyAgentOrchestrator:
    """Small construction seam for deterministic route and integration tests."""

    return ReadOnlyAgentOrchestrator(db, settings=get_settings())


def _raise_agent_error(exc: AgentFoundationError) -> None:
    if isinstance(exc, (AgentNotFoundError, AgentFeatureDisabledError)):
        raise HTTPException(status_code=404, detail="Agent resource not found") from exc
    if isinstance(exc, AgentConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc

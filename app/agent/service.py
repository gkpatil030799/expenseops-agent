from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.contracts import AgentActionPreview, AgentPageContext, AgentStructuredResponse
from app.agent.tooling import (
    AgentToolContext,
    AgentToolDispatchResult,
    AgentToolPolicyError,
    AgentToolRegistry,
    ToolDisposition,
    ToolEffect,
)
from app.config import Settings, get_settings
from app.logging_config import get_trace_id, log_event
from app.models import (
    AgentActionProposal,
    AgentConversation,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    utc_now,
)
from app.services.managed_auth_service import record_audit

logger = logging.getLogger(__name__)

_CLIENT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")
_ERROR_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,99}\Z")
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(token|secret|password|authorization|cookie|credential|api[_-]?key)",
    re.IGNORECASE,
)
_MAX_JSON_BYTES = 64 * 1024
_SAFE_FAILURE_MESSAGE = "The agent operation could not be completed."


class AgentFoundationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class AgentNotFoundError(AgentFoundationError):
    pass


class AgentConflictError(AgentFoundationError):
    pass


class AgentFeatureDisabledError(AgentFoundationError):
    pass


class UnifiedAgentService:
    """Persistence boundary for the channel-neutral ExpenseOps Agent.

    This service deliberately contains no model provider and no domain action
    executor. It persists validated conversation/run metadata and confirmation
    proposals while the existing domain services remain the source of truth.
    """

    def __init__(
        self,
        db: Session,
        settings: Settings | None = None,
        *,
        tool_registry: AgentToolRegistry | None = None,
    ):
        self.db = db
        self.settings = settings or get_settings()
        self.tool_registry = tool_registry

    def create_conversation(
        self,
        *,
        owner_user_id: int,
        title: str | None = None,
    ) -> AgentConversation:
        workspace_id = self._require_enabled_scope(owner_user_id)
        conversation = AgentConversation(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            title=_bounded_optional_text(title, 120),
            status="active",
        )
        self.db.add(conversation)
        self.db.flush()
        record_audit(
            self.db,
            workspace_id=workspace_id,
            user_id=owner_user_id,
            event_type="agent_conversation_created",
            resource_type="agent_conversation",
            resource_id=conversation.public_id,
        )
        self.db.commit()
        self.db.refresh(conversation)
        log_event(logger, "agent_conversation_created", conversation_id=conversation.public_id)
        return conversation

    def list_conversations(
        self,
        *,
        owner_user_id: int,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AgentConversation]:
        workspace_id = self._require_enabled_scope(owner_user_id)
        statement = select(AgentConversation).where(
            AgentConversation.workspace_id == workspace_id,
            AgentConversation.owner_user_id == owner_user_id,
        )
        if not include_archived:
            statement = statement.where(AgentConversation.status == "active")
        return list(
            self.db.scalars(
                statement.order_by(AgentConversation.updated_at.desc(), AgentConversation.id.desc())
                .offset(max(0, offset))
                .limit(max(1, min(limit, 100)))
            )
        )

    def get_conversation(
        self,
        public_id: str,
        *,
        owner_user_id: int,
        for_update: bool = False,
    ) -> AgentConversation:
        workspace_id = self._require_enabled_scope(owner_user_id)
        statement = select(AgentConversation).where(
            AgentConversation.workspace_id == workspace_id,
            AgentConversation.owner_user_id == owner_user_id,
            AgentConversation.public_id == public_id,
        )
        if for_update:
            statement = statement.with_for_update()
        conversation = self.db.scalar(statement)
        if conversation is None:
            raise AgentNotFoundError("conversation_not_found", "Agent conversation not found")
        return conversation

    def archive_conversation(
        self,
        public_id: str,
        *,
        owner_user_id: int,
    ) -> AgentConversation:
        workspace_id = self._require_enabled_scope(owner_user_id)
        conversation = self.get_conversation(public_id, owner_user_id=owner_user_id)
        if conversation.status != "archived":
            now = utc_now()
            conversation.status = "archived"
            conversation.archived_at = now
            conversation.updated_at = now
            self.db.execute(
                update(AgentActionProposal)
                .where(
                    AgentActionProposal.workspace_id == workspace_id,
                    AgentActionProposal.owner_user_id == owner_user_id,
                    AgentActionProposal.conversation_id == conversation.id,
                    AgentActionProposal.status == "awaiting_confirmation",
                )
                .values(
                    status="cancelled",
                    cancelled_at=now,
                    updated_at=now,
                    version=AgentActionProposal.version + 1,
                )
                .execution_options(synchronize_session=False)
            )
            record_audit(
                self.db,
                workspace_id=workspace_id,
                user_id=owner_user_id,
                event_type="agent_conversation_archived",
                resource_type="agent_conversation",
                resource_id=conversation.public_id,
            )
            self.db.commit()
            self.db.refresh(conversation)
        return conversation

    def append_user_message(
        self,
        conversation_public_id: str,
        *,
        owner_user_id: int,
        text: str,
        client_message_id: str | None = None,
    ) -> AgentMessage:
        workspace_id = self._require_enabled_scope(owner_user_id)
        conversation = self.get_conversation(
            conversation_public_id,
            owner_user_id=owner_user_id,
            for_update=True,
        )
        if conversation.status != "active":
            raise AgentConflictError(
                "conversation_archived", "Archived conversations cannot accept messages"
            )
        normalized_text = _bounded_required_text(text, 4000, "message")
        normalized_client_id = _client_message_id(client_message_id)
        if normalized_client_id:
            existing = self.db.scalar(
                select(AgentMessage).where(
                    AgentMessage.workspace_id == workspace_id,
                    AgentMessage.owner_user_id == owner_user_id,
                    AgentMessage.conversation_id == conversation.id,
                    AgentMessage.client_message_id == normalized_client_id,
                )
            )
            if existing is not None:
                if existing.role != "user" or existing.content != normalized_text:
                    raise AgentConflictError(
                        "client_message_id_conflict",
                        "Client message ID was already used for different content",
                    )
                return existing
        now = utc_now()
        message = AgentMessage(
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            owner_user_id=owner_user_id,
            role="user",
            status="completed",
            content=normalized_text,
            client_message_id=normalized_client_id,
        )
        conversation.updated_at = now
        self.db.add(message)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            if normalized_client_id:
                existing = self.db.scalar(
                    select(AgentMessage).where(
                        AgentMessage.workspace_id == workspace_id,
                        AgentMessage.owner_user_id == owner_user_id,
                        AgentMessage.conversation_id == conversation.id,
                        AgentMessage.client_message_id == normalized_client_id,
                    )
                )
                if existing is not None:
                    if existing.role == "user" and existing.content == normalized_text:
                        return existing
                    raise AgentConflictError(
                        "client_message_id_conflict",
                        "Client message ID was already used for different content",
                    ) from exc
            raise AgentConflictError(
                "message_persistence_conflict",
                "Agent message could not be persisted safely",
            ) from exc
        self.db.refresh(message)
        log_event(
            logger,
            "agent_message_persisted",
            conversation_id=conversation.public_id,
            message_id=message.public_id,
            role="user",
        )
        return message

    def append_assistant_message(
        self,
        conversation_public_id: str,
        *,
        owner_user_id: int,
        response: AgentStructuredResponse | dict[str, Any],
    ) -> AgentMessage:
        message = self.stage_assistant_message(
            conversation_public_id,
            owner_user_id=owner_user_id,
            response=response,
        )
        self.db.commit()
        self.db.refresh(message)
        return message

    def stage_assistant_message(
        self,
        conversation_public_id: str,
        *,
        owner_user_id: int,
        response: AgentStructuredResponse | dict[str, Any],
    ) -> AgentMessage:
        """Stage an assistant message for an atomic run-terminal commit."""

        workspace_id = self._require_enabled_scope(owner_user_id)
        conversation = self.get_conversation(
            conversation_public_id,
            owner_user_id=owner_user_id,
        )
        if conversation.status != "active":
            raise AgentConflictError(
                "conversation_archived", "Archived conversations cannot accept messages"
            )
        structured = _safe_json(AgentStructuredResponse.model_validate(response))
        message = AgentMessage(
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            owner_user_id=owner_user_id,
            role="assistant",
            status="completed",
            structured_response_json=structured,
        )
        conversation.updated_at = utc_now()
        self.db.add(message)
        self.db.flush()
        return message

    def list_messages(
        self,
        conversation_public_id: str,
        *,
        owner_user_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentMessage]:
        workspace_id = self._require_enabled_scope(owner_user_id)
        conversation = self.get_conversation(
            conversation_public_id,
            owner_user_id=owner_user_id,
        )
        return list(
            self.db.scalars(
                select(AgentMessage)
                .where(
                    AgentMessage.workspace_id == workspace_id,
                    AgentMessage.owner_user_id == owner_user_id,
                    AgentMessage.conversation_id == conversation.id,
                )
                .order_by(AgentMessage.created_at, AgentMessage.id)
                .offset(max(0, offset))
                .limit(max(1, min(limit, 500)))
            )
        )

    def list_recent_messages(
        self,
        conversation_public_id: str,
        *,
        owner_user_id: int,
        limit: int = 12,
    ) -> list[AgentMessage]:
        """Return the newest bounded history in chronological order."""

        workspace_id = self._require_enabled_scope(owner_user_id)
        conversation = self.get_conversation(
            conversation_public_id,
            owner_user_id=owner_user_id,
        )
        newest_first = list(
            self.db.scalars(
                select(AgentMessage)
                .where(
                    AgentMessage.workspace_id == workspace_id,
                    AgentMessage.owner_user_id == owner_user_id,
                    AgentMessage.conversation_id == conversation.id,
                )
                .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
                .limit(max(1, min(limit, 50)))
            )
        )
        return list(reversed(newest_first))

    def count_messages(
        self,
        conversation_public_id: str,
        *,
        owner_user_id: int,
    ) -> int:
        workspace_id = self._require_enabled_scope(owner_user_id)
        conversation = self.get_conversation(
            conversation_public_id,
            owner_user_id=owner_user_id,
        )
        return int(
            self.db.scalar(
                select(func.count(AgentMessage.id)).where(
                    AgentMessage.workspace_id == workspace_id,
                    AgentMessage.owner_user_id == owner_user_id,
                    AgentMessage.conversation_id == conversation.id,
                )
            )
            or 0
        )

    def create_run(
        self,
        conversation_public_id: str,
        *,
        owner_user_id: int,
        trigger_message_public_id: str | None,
        page_context: AgentPageContext | dict[str, Any] | None,
        model_name: str | None,
        prompt_version: str,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AgentRun:
        workspace_id = self._require_enabled_scope(owner_user_id)
        normalized_page_context = (
            _safe_json(AgentPageContext.model_validate(page_context))
            if page_context is not None
            else None
        )
        conversation = self.get_conversation(
            conversation_public_id,
            owner_user_id=owner_user_id,
            for_update=True,
        )
        if conversation.status != "active":
            raise AgentConflictError(
                "conversation_archived",
                "Archived conversations cannot start agent runs",
            )
        trigger_message_id = None
        if trigger_message_public_id:
            trigger = self.db.scalar(
                select(AgentMessage).where(
                    AgentMessage.workspace_id == workspace_id,
                    AgentMessage.owner_user_id == owner_user_id,
                    AgentMessage.conversation_id == conversation.id,
                    AgentMessage.public_id == trigger_message_public_id,
                )
            )
            if trigger is None:
                raise AgentNotFoundError("message_not_found", "Agent message not found")
            trigger_message_id = trigger.id
            existing = self.db.scalar(
                select(AgentRun).where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.owner_user_id == owner_user_id,
                    AgentRun.trigger_message_id == trigger_message_id,
                )
            )
            if existing is not None:
                _require_matching_turn_context(existing, normalized_page_context)
                return existing
        run = AgentRun(
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            owner_user_id=owner_user_id,
            trigger_message_id=trigger_message_id,
            status="queued",
            model_name=_bounded_optional_text(model_name, 128),
            prompt_version=_bounded_required_text(prompt_version, 64, "prompt version"),
            page_context_json=normalized_page_context,
            response_schema_version="1.0",
            request_id=_bounded_optional_text(request_id or get_trace_id(), 64),
            correlation_id=_bounded_optional_text(correlation_id or get_trace_id(), 64),
        )
        self.db.add(run)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            if trigger_message_id is not None:
                existing = self.db.scalar(
                    select(AgentRun).where(
                        AgentRun.workspace_id == workspace_id,
                        AgentRun.owner_user_id == owner_user_id,
                        AgentRun.trigger_message_id == trigger_message_id,
                    )
                )
                if existing is not None:
                    _require_matching_turn_context(existing, normalized_page_context)
                    return existing
            raise AgentConflictError(
                "run_persistence_conflict",
                "Agent run could not be persisted safely",
            ) from exc
        self.db.refresh(run)
        log_event(logger, "agent_run_queued", run_id=run.public_id)
        return run

    def start_run(self, public_id: str, *, owner_user_id: int) -> AgentRun:
        workspace_id = self._require_enabled_scope(owner_user_id)
        run = self._get_run(public_id, owner_user_id=owner_user_id)
        now = utc_now()
        result = self.db.execute(
            update(AgentRun)
            .where(
                AgentRun.id == run.id,
                AgentRun.workspace_id == workspace_id,
                AgentRun.owner_user_id == owner_user_id,
                AgentRun.status == "queued",
            )
            .values(status="running", started_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise AgentConflictError("run_state_conflict", "Agent run is not queued")
        self.db.commit()
        self.db.expire(run)
        return self._get_run(public_id, owner_user_id=owner_user_id)

    def complete_run(
        self,
        public_id: str,
        *,
        owner_user_id: int,
        latency_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        estimated_cost_micros: int | None = None,
        assistant_message_public_id: str | None = None,
        provider_request_id: str | None = None,
        provider_request_count: int | None = None,
    ) -> AgentRun:
        workspace_id = self._require_enabled_scope(owner_user_id)
        run = self._get_run(public_id, owner_user_id=owner_user_id)
        now = utc_now()
        normalized_input_tokens = _nonnegative(input_tokens)
        normalized_output_tokens = _nonnegative(output_tokens)
        total_tokens = (
            (normalized_input_tokens or 0) + (normalized_output_tokens or 0)
            if input_tokens is not None or output_tokens is not None
            else None
        )
        metadata = _run_metadata(
            run.metadata_json,
            assistant_message_public_id=assistant_message_public_id,
            provider_request_id=provider_request_id,
            provider_request_count=provider_request_count,
        )
        result = self.db.execute(
            update(AgentRun)
            .where(
                AgentRun.id == run.id,
                AgentRun.workspace_id == workspace_id,
                AgentRun.owner_user_id == owner_user_id,
                AgentRun.status.in_(("queued", "running")),
            )
            .values(
                status="completed",
                latency_ms=_nonnegative(latency_ms),
                input_tokens=normalized_input_tokens,
                output_tokens=normalized_output_tokens,
                total_tokens=total_tokens,
                estimated_cost_micros=_nonnegative(estimated_cost_micros),
                metadata_json=metadata,
                completed_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise AgentConflictError("run_state_conflict", "Agent run cannot be completed")
        self.db.commit()
        self.db.expire(run)
        completed = self._get_run(public_id, owner_user_id=owner_user_id)
        log_event(
            logger,
            "agent_run_completed",
            run_id=completed.public_id,
            latency_ms=completed.latency_ms,
            input_tokens=completed.input_tokens,
            output_tokens=completed.output_tokens,
        )
        return completed

    def fail_run(
        self,
        public_id: str,
        *,
        owner_user_id: int,
        error_code: str,
        error_message: str,
        latency_ms: int | None = None,
        assistant_message_public_id: str | None = None,
        provider_request_id: str | None = None,
        provider_request_count: int | None = None,
    ) -> AgentRun:
        workspace_id = self._require_enabled_scope(owner_user_id)
        run = self._get_run(public_id, owner_user_id=owner_user_id)
        now = utc_now()
        metadata = _run_metadata(
            run.metadata_json,
            assistant_message_public_id=assistant_message_public_id,
            provider_request_id=provider_request_id,
            provider_request_count=provider_request_count,
        )
        self.db.execute(
            update(AgentToolCall)
            .where(
                AgentToolCall.workspace_id == workspace_id,
                AgentToolCall.owner_user_id == owner_user_id,
                AgentToolCall.run_id == run.id,
                AgentToolCall.status.in_(("proposed", "running")),
            )
            .values(
                status="failed",
                error_code="run_failed",
                error_message=_SAFE_FAILURE_MESSAGE,
                completed_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        result = self.db.execute(
            update(AgentRun)
            .where(
                AgentRun.id == run.id,
                AgentRun.workspace_id == workspace_id,
                AgentRun.owner_user_id == owner_user_id,
                AgentRun.status.in_(("queued", "running")),
            )
            .values(
                status="failed",
                error_code=_safe_error_code(error_code),
                error_message=_safe_error_message(error_message),
                latency_ms=_nonnegative(latency_ms),
                metadata_json=metadata,
                completed_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise AgentConflictError("run_state_conflict", "Agent run cannot be failed")
        self.db.commit()
        self.db.expire(run)
        failed = self._get_run(public_id, owner_user_id=owner_user_id)
        log_event(
            logger,
            "agent_run_failed",
            run_id=failed.public_id,
            error_code=failed.error_code,
        )
        return failed

    def cancel_run(
        self,
        public_id: str,
        *,
        owner_user_id: int,
        latency_ms: int | None = None,
    ) -> AgentRun:
        workspace_id = self._require_enabled_scope(owner_user_id)
        run = self._get_run(public_id, owner_user_id=owner_user_id)
        now = utc_now()
        self.db.execute(
            update(AgentToolCall)
            .where(
                AgentToolCall.workspace_id == workspace_id,
                AgentToolCall.owner_user_id == owner_user_id,
                AgentToolCall.run_id == run.id,
                AgentToolCall.status.in_(("proposed", "running")),
            )
            .values(
                status="failed",
                error_code="run_cancelled",
                error_message=_SAFE_FAILURE_MESSAGE,
                completed_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        result = self.db.execute(
            update(AgentRun)
            .where(
                AgentRun.id == run.id,
                AgentRun.workspace_id == workspace_id,
                AgentRun.owner_user_id == owner_user_id,
                AgentRun.status.in_(("queued", "running")),
            )
            .values(
                status="cancelled",
                error_code="run_cancelled",
                error_message=_SAFE_FAILURE_MESSAGE,
                latency_ms=_nonnegative(latency_ms),
                completed_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise AgentConflictError("run_state_conflict", "Agent run cannot be cancelled")
        self.db.commit()
        self.db.expire(run)
        cancelled = self._get_run(public_id, owner_user_id=owner_user_id)
        log_event(logger, "agent_run_cancelled", run_id=cancelled.public_id)
        return cancelled

    def record_tool_call(
        self,
        run_public_id: str,
        *,
        owner_user_id: int,
        dispatch: AgentToolDispatchResult,
    ) -> AgentToolCall:
        workspace_id = self._require_enabled_scope(owner_user_id)
        self._validate_registry_dispatch(dispatch)
        run = self._get_run(run_public_id, owner_user_id=owner_user_id, for_update=True)
        if run.status != "running":
            raise AgentConflictError(
                "run_state_conflict",
                "Tool calls can only be recorded for a running agent run",
            )
        if dispatch.effect is ToolEffect.READ:
            if dispatch.disposition is not ToolDisposition.READY:
                raise AgentFoundationError(
                    "invalid_tool_dispatch",
                    "Read tools must be validated before they are recorded",
                )
            requires_confirmation = False
            status = "proposed"
        else:
            if dispatch.disposition is not ToolDisposition.PROPOSAL_REQUIRED:
                raise AgentFoundationError(
                    "invalid_tool_dispatch",
                    "Consequential tools must produce proposal material",
                )
            requires_confirmation = True
            status = "proposed"
        if dispatch.effect is not ToolEffect.READ and dispatch.preview is None:
            raise AgentFoundationError(
                "missing_action_preview",
                "Consequential tools require a code-generated action preview",
            )
        sequence = (
            int(
                self.db.scalar(
                    select(func.coalesce(func.max(AgentToolCall.sequence), -1)).where(
                        AgentToolCall.workspace_id == workspace_id,
                        AgentToolCall.owner_user_id == owner_user_id,
                        AgentToolCall.run_id == run.id,
                    )
                )
                or 0
            )
            + 1
        )
        call = AgentToolCall(
            workspace_id=workspace_id,
            run_id=run.id,
            owner_user_id=owner_user_id,
            sequence=sequence,
            tool_name=_bounded_required_text(dispatch.tool_name, 100, "tool name"),
            tool_version=_bounded_required_text(dispatch.tool_version, 32, "tool version"),
            operation_kind=dispatch.effect.value,
            capability=dispatch.capability.value,
            requires_confirmation=requires_confirmation,
            status=status,
            arguments_json=_safe_json(dispatch.normalized_arguments),
        )
        self.db.add(call)
        self.db.commit()
        self.db.refresh(call)
        log_event(
            logger,
            "agent_tool_call_recorded",
            run_id=run.public_id,
            tool_name=call.tool_name,
            operation_kind=call.operation_kind,
            requires_confirmation=call.requires_confirmation,
        )
        return call

    def start_tool_call(self, public_id: str, *, owner_user_id: int) -> AgentToolCall:
        workspace_id = self._require_enabled_scope(owner_user_id)
        call = self._get_tool_call(public_id, owner_user_id=owner_user_id)
        now = utc_now()
        result = self.db.execute(
            update(AgentToolCall)
            .where(
                AgentToolCall.id == call.id,
                AgentToolCall.workspace_id == workspace_id,
                AgentToolCall.owner_user_id == owner_user_id,
                AgentToolCall.status == "proposed",
                AgentToolCall.run_id.in_(
                    select(AgentRun.id).where(
                        AgentRun.workspace_id == workspace_id,
                        AgentRun.owner_user_id == owner_user_id,
                        AgentRun.status == "running",
                    )
                ),
            )
            .values(status="running", started_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise AgentConflictError("tool_call_state_conflict", "Agent tool call is not proposed")
        self.db.commit()
        self.db.expire(call)
        return self._get_tool_call(public_id, owner_user_id=owner_user_id)

    def complete_tool_call(
        self,
        public_id: str,
        *,
        owner_user_id: int,
        dispatch: AgentToolDispatchResult,
        latency_ms: int | None = None,
    ) -> AgentToolCall:
        workspace_id = self._require_enabled_scope(owner_user_id)
        self._validate_registry_dispatch(dispatch)
        call = self._get_tool_call(public_id, owner_user_id=owner_user_id)
        if (
            dispatch.disposition is not ToolDisposition.EXECUTED
            or dispatch.effect is not ToolEffect.READ
            or dispatch.output is None
            or call.tool_name != dispatch.tool_name
            or call.tool_version != dispatch.tool_version
            or call.operation_kind != dispatch.effect.value
            or call.arguments_json != dispatch.normalized_arguments
        ):
            raise AgentConflictError(
                "tool_call_result_mismatch",
                "Completed tool output does not match the recorded tool call",
            )
        now = utc_now()
        result = self.db.execute(
            update(AgentToolCall)
            .where(
                AgentToolCall.id == call.id,
                AgentToolCall.workspace_id == workspace_id,
                AgentToolCall.owner_user_id == owner_user_id,
                AgentToolCall.status.in_(("proposed", "running")),
                AgentToolCall.run_id.in_(
                    select(AgentRun.id).where(
                        AgentRun.workspace_id == workspace_id,
                        AgentRun.owner_user_id == owner_user_id,
                        AgentRun.status == "running",
                    )
                ),
            )
            .values(
                status="completed",
                result_metadata_json=_tool_result_metadata(dispatch.output),
                latency_ms=_nonnegative(latency_ms),
                completed_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise AgentConflictError(
                "tool_call_state_conflict",
                "Agent tool call cannot be completed",
            )
        self.db.commit()
        self.db.expire(call)
        completed = self._get_tool_call(public_id, owner_user_id=owner_user_id)
        log_event(
            logger,
            "agent_tool_call_completed",
            tool_call_id=completed.public_id,
            tool_name=completed.tool_name,
            latency_ms=completed.latency_ms,
        )
        return completed

    def fail_tool_call(
        self,
        public_id: str,
        *,
        owner_user_id: int,
        error_code: str,
        error_message: str,
        latency_ms: int | None = None,
    ) -> AgentToolCall:
        workspace_id = self._require_enabled_scope(owner_user_id)
        call = self._get_tool_call(public_id, owner_user_id=owner_user_id)
        now = utc_now()
        result = self.db.execute(
            update(AgentToolCall)
            .where(
                AgentToolCall.id == call.id,
                AgentToolCall.workspace_id == workspace_id,
                AgentToolCall.owner_user_id == owner_user_id,
                AgentToolCall.status.in_(("proposed", "running")),
            )
            .values(
                status="failed",
                error_code=_safe_error_code(error_code),
                error_message=_safe_error_message(error_message),
                latency_ms=_nonnegative(latency_ms),
                completed_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise AgentConflictError("tool_call_state_conflict", "Agent tool call cannot be failed")
        self.db.commit()
        self.db.expire(call)
        failed = self._get_tool_call(public_id, owner_user_id=owner_user_id)
        log_event(
            logger,
            "agent_tool_call_failed",
            tool_call_id=failed.public_id,
            tool_name=failed.tool_name,
            error_code=failed.error_code,
        )
        return failed

    def create_action_proposal(
        self,
        conversation_public_id: str,
        *,
        owner_user_id: int,
        dispatch: AgentToolDispatchResult,
        tool_call_public_id: str,
        idempotency_key: str,
        expires_at: datetime | None = None,
    ) -> AgentActionProposal:
        workspace_id = self._require_write_scope(owner_user_id)
        self._validate_registry_dispatch(dispatch)
        conversation = self.get_conversation(
            conversation_public_id,
            owner_user_id=owner_user_id,
        )
        if conversation.status != "active":
            raise AgentConflictError(
                "conversation_archived",
                "Archived conversations cannot create action proposals",
            )
        if (
            dispatch.effect not in {ToolEffect.WRITE, ToolEffect.EXTERNAL_ACTION}
            or dispatch.disposition is not ToolDisposition.PROPOSAL_REQUIRED
            or dispatch.preview is None
        ):
            raise AgentFoundationError(
                "proposal_not_required",
                "Only validated consequential tool dispatches can create proposals",
            )
        normalized_tool_name = _bounded_required_text(dispatch.tool_name, 100, "tool name")
        normalized_tool_version = _bounded_required_text(
            dispatch.tool_version,
            32,
            "tool version",
        )
        operation_kind = dispatch.effect.value
        parameters = _safe_json(dispatch.normalized_arguments)
        preview_json = _safe_json(AgentActionPreview.model_validate(dispatch.preview))
        parameters_hash = _json_hash(parameters)
        action_fingerprint = _json_hash(
            {
                "tool_name": normalized_tool_name,
                "tool_version": normalized_tool_version,
                "operation_kind": operation_kind,
                "capability": dispatch.capability.value,
                "parameters": parameters,
                "preview": preview_json,
            }
        )
        normalized_key = _bounded_required_text(idempotency_key, 128, "idempotency key")
        now = utc_now()
        expired = self.db.execute(
            update(AgentActionProposal)
            .where(
                AgentActionProposal.workspace_id == workspace_id,
                AgentActionProposal.owner_user_id == owner_user_id,
                AgentActionProposal.status == "awaiting_confirmation",
                AgentActionProposal.expires_at <= now,
            )
            .values(
                status="expired",
                updated_at=now,
                version=AgentActionProposal.version + 1,
            )
            .execution_options(synchronize_session="fetch")
        )
        if expired.rowcount:
            # Expiry is a durable lifecycle transition even when this request
            # later reuses the same idempotency key and returns early.
            self.db.commit()
        existing = self.db.scalar(
            select(AgentActionProposal).where(
                AgentActionProposal.workspace_id == workspace_id,
                AgentActionProposal.owner_user_id == owner_user_id,
                AgentActionProposal.idempotency_key == normalized_key,
            )
        )
        if existing is not None:
            if not _proposal_matches(
                existing,
                conversation_id=conversation.id,
                tool_name=normalized_tool_name,
                tool_version=normalized_tool_version,
                operation_kind=operation_kind,
                capability=dispatch.capability.value,
                parameters_hash=parameters_hash,
                action_fingerprint=action_fingerprint,
            ):
                raise AgentConflictError(
                    "idempotency_key_conflict",
                    "Idempotency key was already used for a different action",
                )
            return existing
        tool_call = self._get_tool_call(
            tool_call_public_id,
            owner_user_id=owner_user_id,
        )
        tool_run = self.db.scalar(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.owner_user_id == owner_user_id,
                AgentRun.id == tool_call.run_id,
            )
        )
        if tool_run is None or tool_run.conversation_id != conversation.id:
            raise AgentConflictError(
                "tool_call_conversation_mismatch",
                "Tool call does not belong to the proposal conversation",
            )
        if (
            tool_call.tool_name != normalized_tool_name
            or tool_call.tool_version != normalized_tool_version
            or tool_call.operation_kind != operation_kind
            or tool_call.capability != dispatch.capability.value
            or not tool_call.requires_confirmation
            or tool_call.status != "proposed"
        ):
            raise AgentConflictError(
                "tool_call_mismatch",
                "Proposal does not match the recorded consequential tool call",
            )
        if tool_call.arguments_json != parameters:
            raise AgentConflictError(
                "tool_call_parameters_mismatch",
                "Proposal parameters do not match the recorded tool call",
            )
        active_duplicate = self.db.scalar(
            select(AgentActionProposal).where(
                AgentActionProposal.workspace_id == workspace_id,
                AgentActionProposal.owner_user_id == owner_user_id,
                AgentActionProposal.action_fingerprint == action_fingerprint,
                AgentActionProposal.status.in_(("awaiting_confirmation", "confirmed", "executing")),
            )
        )
        if active_duplicate is not None:
            if active_duplicate.conversation_id == conversation.id:
                return active_duplicate
            raise AgentConflictError(
                "active_action_exists",
                "The same action is already active in another conversation",
            )
        expiry = _aware(expires_at) if expires_at is not None else utc_now() + timedelta(minutes=15)
        if expiry <= utc_now():
            raise AgentFoundationError("proposal_expired", "Proposal expiry must be in the future")
        proposal = AgentActionProposal(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            conversation_id=conversation.id,
            run_id=tool_run.id,
            tool_call_id=tool_call.id,
            tool_name=normalized_tool_name,
            tool_version=normalized_tool_version,
            operation_kind=operation_kind,
            capability=dispatch.capability.value,
            normalized_parameters_json=parameters,
            parameters_hash=parameters_hash,
            action_fingerprint=action_fingerprint,
            preview_json=preview_json,
            status="awaiting_confirmation",
            version=1,
            idempotency_key=normalized_key,
            expires_at=expiry,
        )
        self.db.add(proposal)
        try:
            self.db.flush()
            record_audit(
                self.db,
                workspace_id=workspace_id,
                user_id=owner_user_id,
                event_type="agent_action_proposed",
                resource_type="agent_action_proposal",
                resource_id=proposal.public_id,
                metadata={
                    "tool_name": proposal.tool_name,
                    "operation_kind": proposal.operation_kind,
                },
            )
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            existing = self.db.scalar(
                select(AgentActionProposal).where(
                    AgentActionProposal.workspace_id == workspace_id,
                    AgentActionProposal.owner_user_id == owner_user_id,
                    (
                        (AgentActionProposal.idempotency_key == normalized_key)
                        | (
                            (AgentActionProposal.action_fingerprint == action_fingerprint)
                            & AgentActionProposal.status.in_(
                                ("awaiting_confirmation", "confirmed", "executing")
                            )
                        )
                    ),
                )
            )
            if existing is not None and _proposal_matches(
                existing,
                conversation_id=conversation.id,
                tool_name=normalized_tool_name,
                tool_version=normalized_tool_version,
                operation_kind=operation_kind,
                capability=dispatch.capability.value,
                parameters_hash=parameters_hash,
                action_fingerprint=action_fingerprint,
            ):
                return existing
            raise AgentConflictError(
                "proposal_persistence_conflict",
                "Agent proposal could not be persisted safely",
            ) from exc
        self.db.refresh(proposal)
        return proposal

    def confirm_action_proposal(
        self,
        public_id: str,
        *,
        owner_user_id: int,
        expected_version: int,
    ) -> AgentActionProposal:
        workspace_id = self._require_write_scope(owner_user_id)
        proposal = self._get_proposal(
            public_id,
            owner_user_id=owner_user_id,
            for_update=True,
        )
        now = utc_now()
        if proposal.capability == "purchasing" and not self.settings.agent_purchasing_enabled:
            raise AgentFeatureDisabledError(
                "agent_purchasing_disabled",
                "Agent purchasing actions are not available",
            )
        expected_parameters_hash = _json_hash(proposal.normalized_parameters_json)
        expected_action_fingerprint = _json_hash(
            {
                "tool_name": proposal.tool_name,
                "tool_version": proposal.tool_version,
                "operation_kind": proposal.operation_kind,
                "capability": proposal.capability,
                "parameters": proposal.normalized_parameters_json,
                "preview": proposal.preview_json,
            }
        )
        if (
            proposal.parameters_hash != expected_parameters_hash
            or proposal.action_fingerprint != expected_action_fingerprint
        ):
            proposal.status = "ambiguous"
            proposal.error_code = "proposal_integrity_failed"
            proposal.error_message = "Stored action integrity validation failed."
            proposal.version += 1
            proposal.updated_at = now
            self.db.commit()
            raise AgentConflictError(
                "proposal_integrity_failed",
                "Agent action proposal failed integrity validation",
            )
        conversation = self.db.scalar(
            select(AgentConversation).where(
                AgentConversation.workspace_id == workspace_id,
                AgentConversation.owner_user_id == owner_user_id,
                AgentConversation.id == proposal.conversation_id,
                AgentConversation.status == "active",
            )
        )
        if conversation is None:
            raise AgentConflictError(
                "conversation_archived",
                "Archived conversations cannot confirm action proposals",
            )
        if (
            proposal.status == "awaiting_confirmation"
            and proposal.expires_at is not None
            and _aware(proposal.expires_at) <= now
        ):
            proposal.status = "expired"
            proposal.version += 1
            proposal.updated_at = now
            self.db.commit()
            raise AgentConflictError("proposal_expired", "Agent action proposal has expired")
        result = self.db.execute(
            update(AgentActionProposal)
            .where(
                AgentActionProposal.id == proposal.id,
                AgentActionProposal.workspace_id == workspace_id,
                AgentActionProposal.owner_user_id == owner_user_id,
                AgentActionProposal.status == "awaiting_confirmation",
                AgentActionProposal.version == expected_version,
                AgentActionProposal.expires_at > now,
                AgentActionProposal.parameters_hash == expected_parameters_hash,
                AgentActionProposal.action_fingerprint == expected_action_fingerprint,
            )
            .values(
                status="confirmed",
                confirmed_by_user_id=owner_user_id,
                confirmed_at=now,
                updated_at=now,
                version=expected_version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise AgentConflictError(
                "proposal_state_conflict",
                "Agent action proposal changed; refresh before confirming",
            )
        record_audit(
            self.db,
            workspace_id=workspace_id,
            user_id=owner_user_id,
            event_type="agent_action_confirmed",
            resource_type="agent_action_proposal",
            resource_id=proposal.public_id,
            metadata={"tool_name": proposal.tool_name},
        )
        self.db.commit()
        self.db.expire(proposal)
        confirmed = self._get_proposal(public_id, owner_user_id=owner_user_id)
        # Confirmation is deliberately terminal for this foundation. A future
        # executor must consume this exact stored snapshot via a separate,
        # idempotent domain adapter; this service never executes it.
        return confirmed

    def cancel_action_proposal(
        self,
        public_id: str,
        *,
        owner_user_id: int,
        expected_version: int,
    ) -> AgentActionProposal:
        workspace_id = self._require_write_scope(owner_user_id)
        proposal = self._get_proposal(public_id, owner_user_id=owner_user_id)
        now = utc_now()
        result = self.db.execute(
            update(AgentActionProposal)
            .where(
                AgentActionProposal.id == proposal.id,
                AgentActionProposal.workspace_id == workspace_id,
                AgentActionProposal.owner_user_id == owner_user_id,
                AgentActionProposal.status == "awaiting_confirmation",
                AgentActionProposal.version == expected_version,
            )
            .values(
                status="cancelled",
                cancelled_at=now,
                updated_at=now,
                version=expected_version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise AgentConflictError(
                "proposal_state_conflict", "Agent action proposal cannot be cancelled"
            )
        self.db.commit()
        self.db.expire(proposal)
        return self._get_proposal(public_id, owner_user_id=owner_user_id)

    def get_run(self, public_id: str, *, owner_user_id: int) -> AgentRun:
        return self._get_run(public_id, owner_user_id=owner_user_id)

    def get_message(self, public_id: str, *, owner_user_id: int) -> AgentMessage:
        workspace_id = self._require_enabled_scope(owner_user_id)
        message = self.db.scalar(
            select(AgentMessage).where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.owner_user_id == owner_user_id,
                AgentMessage.public_id == public_id,
            )
        )
        if message is None:
            raise AgentNotFoundError("message_not_found", "Agent message not found")
        return message

    def find_run_for_trigger_message(
        self,
        trigger_message_public_id: str,
        *,
        owner_user_id: int,
    ) -> AgentRun | None:
        workspace_id = self._require_enabled_scope(owner_user_id)
        message = self.get_message(trigger_message_public_id, owner_user_id=owner_user_id)
        return self.db.scalar(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.owner_user_id == owner_user_id,
                AgentRun.trigger_message_id == message.id,
            )
        )

    def result_message_for_run(
        self,
        run: AgentRun,
        *,
        owner_user_id: int,
    ) -> AgentMessage | None:
        self._require_enabled_scope(owner_user_id)
        value = (run.metadata_json or {}).get("assistant_message_public_id")
        if not isinstance(value, str) or not value:
            return None
        try:
            return self.get_message(value, owner_user_id=owner_user_id)
        except AgentNotFoundError:
            return None

    def _get_run(
        self,
        public_id: str,
        *,
        owner_user_id: int,
        for_update: bool = False,
    ) -> AgentRun:
        workspace_id = self._require_enabled_scope(owner_user_id)
        statement = select(AgentRun).where(
            AgentRun.workspace_id == workspace_id,
            AgentRun.owner_user_id == owner_user_id,
            AgentRun.public_id == public_id,
        )
        if for_update:
            statement = statement.with_for_update()
        run = self.db.scalar(statement)
        if run is None:
            raise AgentNotFoundError("run_not_found", "Agent run not found")
        return run

    def _get_tool_call(self, public_id: str, *, owner_user_id: int) -> AgentToolCall:
        workspace_id = self._require_enabled_scope(owner_user_id)
        call = self.db.scalar(
            select(AgentToolCall).where(
                AgentToolCall.workspace_id == workspace_id,
                AgentToolCall.owner_user_id == owner_user_id,
                AgentToolCall.public_id == public_id,
            )
        )
        if call is None:
            raise AgentNotFoundError("tool_call_not_found", "Agent tool call not found")
        return call

    def _get_proposal(
        self,
        public_id: str,
        *,
        owner_user_id: int,
        for_update: bool = False,
    ) -> AgentActionProposal:
        workspace_id = self._require_write_scope(owner_user_id)
        statement = select(AgentActionProposal).where(
            AgentActionProposal.workspace_id == workspace_id,
            AgentActionProposal.owner_user_id == owner_user_id,
            AgentActionProposal.public_id == public_id,
        )
        if for_update:
            statement = statement.with_for_update()
        proposal = self.db.scalar(statement)
        if proposal is None:
            raise AgentNotFoundError("proposal_not_found", "Agent action proposal not found")
        return proposal

    def _require_enabled_scope(self, owner_user_id: int) -> int:
        if not self.settings.agent_enabled:
            raise AgentFeatureDisabledError("agent_disabled", "Agent is not available")
        return self._require_scope(owner_user_id)

    def _require_write_scope(self, owner_user_id: int) -> int:
        workspace_id = self._require_enabled_scope(owner_user_id)
        if not self.settings.agent_write_actions_enabled:
            raise AgentFeatureDisabledError(
                "agent_write_actions_disabled", "Agent action proposals are not available"
            )
        return workspace_id

    def _require_scope(self, owner_user_id: int) -> int:
        workspace_id = self.db.info.get("workspace_id")
        authenticated_user_id = self.db.info.get("user_id")
        if workspace_id is None or authenticated_user_id != owner_user_id:
            raise AgentNotFoundError("agent_resource_not_found", "Agent resource not found")
        return int(workspace_id)

    def _validate_registry_dispatch(self, dispatch: AgentToolDispatchResult) -> None:
        if self.tool_registry is None:
            raise AgentFoundationError(
                "tool_registry_required",
                "Agent tool operations require the trusted server registry",
            )
        try:
            self.tool_registry.validate_issued(
                dispatch,
                context=AgentToolContext.from_session(
                    self.db,
                    request_id=get_trace_id(),
                ),
            )
        except AgentToolPolicyError as exc:
            raise AgentFoundationError(exc.code, str(exc)) from exc


def _safe_json(value: BaseModel | dict[str, Any]) -> dict[str, Any]:
    payload: Any = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    if not isinstance(payload, dict):
        raise AgentFoundationError(
            "invalid_structured_data",
            "Structured agent data must be an object",
        )
    _reject_sensitive_keys(payload)
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AgentFoundationError(
            "invalid_structured_data", "Structured agent data must be JSON serializable"
        ) from exc
    if len(encoded.encode("utf-8")) > _MAX_JSON_BYTES:
        raise AgentFoundationError(
            "structured_data_too_large",
            "Structured agent data is too large",
        )
    return json.loads(encoded)


def _reject_sensitive_keys(value: Any, *, depth: int = 0) -> None:
    if depth > 10:
        raise AgentFoundationError("structured_data_too_deep", "Structured agent data is too deep")
    if isinstance(value, dict):
        for key, item in value.items():
            if _SENSITIVE_KEY_PATTERN.search(str(key)):
                raise AgentFoundationError(
                    "sensitive_agent_data", "Agent data cannot contain credential fields"
                )
            _reject_sensitive_keys(item, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > 200:
            raise AgentFoundationError("structured_data_too_large", "Agent data list is too large")
        for item in value:
            _reject_sensitive_keys(item, depth=depth + 1)


def _json_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _tool_result_metadata(output: dict[str, Any]) -> dict[str, Any]:
    """Persist auditable semantics without copying financial tool payloads."""

    metadata: dict[str, Any] = {
        "output_schema_validated": True,
        "output_sha256": _json_hash(_safe_json(output)),
    }
    transactions = output.get("transactions")
    if isinstance(transactions, list):
        metadata["returned_count"] = len(transactions)
    total_count = output.get("total_count")
    if isinstance(total_count, int) and total_count >= 0:
        metadata["total_count"] = total_count
    for key in ("start_date", "end_date", "currency_code"):
        value = output.get(key)
        if isinstance(value, str):
            metadata[key] = value[:16]
    return metadata


def _run_metadata(
    current: dict[str, Any] | None,
    *,
    assistant_message_public_id: str | None,
    provider_request_id: str | None,
    provider_request_count: int | None,
) -> dict[str, Any]:
    metadata = dict(current or {})
    metadata.update({"provider": "openai", "runtime": "openai_agents_sdk"})
    if assistant_message_public_id is not None:
        metadata["assistant_message_public_id"] = _bounded_required_text(
            assistant_message_public_id,
            128,
            "assistant message ID",
        )
    if provider_request_id is not None:
        metadata["provider_request_id"] = _bounded_required_text(
            provider_request_id,
            128,
            "provider request ID",
        )
    if provider_request_count is not None:
        metadata["provider_request_count"] = _nonnegative(provider_request_count)
    return _safe_json(metadata)


def _require_matching_turn_context(
    run: AgentRun,
    page_context: dict[str, Any] | None,
) -> None:
    """Reject reuse of a turn idempotency key with a different request context."""

    if run.page_context_json != page_context:
        raise AgentConflictError(
            "client_message_id_conflict",
            "Client message ID was already used for different turn context",
        )


def _bounded_optional_text(value: str | None, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized[:maximum] or None


def _bounded_required_text(value: str, maximum: int, label: str) -> str:
    normalized = _bounded_optional_text(value, maximum)
    if normalized is None:
        raise AgentFoundationError("invalid_text", f"{label.capitalize()} is required")
    return normalized


def _client_message_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not _CLIENT_ID_PATTERN.fullmatch(value):
        raise AgentFoundationError(
            "invalid_client_message_id", "Client message ID has an invalid format"
        )
    return value


def _nonnegative(value: int | None) -> int | None:
    if value is None:
        return None
    if value < 0:
        raise AgentFoundationError("invalid_metric", "Agent run metrics cannot be negative")
    return value


def _safe_error_code(value: str) -> str:
    normalized = value.strip()
    if not _ERROR_CODE_PATTERN.fullmatch(normalized):
        raise AgentFoundationError(
            "invalid_error_code",
            "Agent error codes must be bounded snake_case identifiers",
        )
    return normalized


def _safe_error_message(_value: str) -> str:
    # Provider exceptions can hide credentials in DSNs, URLs, headers, or prose.
    # Persist only a fixed customer-safe summary. Detailed diagnostics belong in
    # controlled, redacted telemetry, never in customer data tables.
    return _SAFE_FAILURE_MESSAGE


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _proposal_matches(
    proposal: AgentActionProposal,
    *,
    conversation_id: int,
    tool_name: str,
    tool_version: str,
    operation_kind: str,
    capability: str,
    parameters_hash: str,
    action_fingerprint: str,
) -> bool:
    return (
        proposal.conversation_id == conversation_id
        and proposal.tool_name == tool_name
        and proposal.tool_version == tool_version
        and proposal.operation_kind == operation_kind
        and proposal.capability == capability
        and proposal.parameters_hash == parameters_hash
        and proposal.action_fingerprint == action_fingerprint
    )

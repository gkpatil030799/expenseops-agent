"""Agent-owned, one-by-one transaction review session.

The Agent needs its own complete review experience: list eligible
candidates, let the user pick one, walk them through Personal / Split /
Customize without leaving the Agent panel, and auto-advance. This module
owns only that queue/position state. Everything else -- eligibility,
proposal creation, confirmation, execution, the FinancialOperation
journal -- is the existing Agent action-proposal machinery in
app.agent.service / app.agent.action_tools / app.agent.actions, reused
unchanged. Web CRUD and Telegram are never called from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.agent.action_tools import MARK_PERSONAL_TOOL_NAME, POST_SPLITWISE_TOOL_NAME
from app.agent.service import (
    AgentConflictError,
    AgentFoundationError,
    AgentNotFoundError,
    UnifiedAgentService,
)
from app.agent.tooling import AgentToolContext, AgentToolRegistry
from app.config import Settings
from app.models import (
    AgentActionProposal,
    AgentReviewSession,
    AgentReviewSessionStatus,
    ExpenseTransaction,
    ReviewItemState,
    utc_now,
)
from app.services.ai_intent_extraction_service import AIIntentExtractionService
from app.services.ai_memory_service import AIInterpretationMemoryService
from app.services.review_inbox_service import ReviewInboxService

REVIEW_SESSION_PROMPT_VERSION = "agent-review-session-v1"
MAX_REVIEW_SESSION_CANDIDATES = 25
_TYPED_REVIEW_SOURCE = "typed"

_ACTIONS_BY_KIND = {
    "mark_personal": {"transaction"},
    "post_splitwise_expense": {"transaction"},
}


@dataclass(frozen=True)
class TypedInterpretationResult:
    status: Literal["proposed", "clarify"]
    proposal: AgentActionProposal | None = None
    message: str | None = None


class ReviewSessionService:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings
        self.agent_service = UnifiedAgentService(db, settings)

    def _scope(self, owner_user_id: int) -> int:
        workspace_id = self.db.info.get("workspace_id")
        session_user_id = self.db.info.get("user_id")
        if not isinstance(workspace_id, int) or session_user_id != owner_user_id:
            raise AgentFoundationError(
                "invalid_tool_context", "Review session requires an authenticated tenant scope"
            )
        return workspace_id

    def start_or_resume(
        self,
        conversation_public_id: str,
        *,
        owner_user_id: int,
    ) -> AgentReviewSession:
        workspace_id = self._scope(owner_user_id)
        conversation = self.agent_service.get_conversation(
            conversation_public_id, owner_user_id=owner_user_id
        )
        existing = self._active_session(workspace_id, owner_user_id, conversation.id)
        if existing is not None:
            return existing

        candidates = ReviewInboxService(self.db).list_agent_candidates(
            limit=MAX_REVIEW_SESSION_CANDIDATES
        )
        snapshot = [
            {
                "review_item_public_id": item["review_item_public_id"],
                "kind": item["kind"],
                "source_type": item["source_type"],
                "source_entity_id": item["source_entity_id"],
                "source_fingerprint": item["source_fingerprint"],
            }
            for item in candidates
        ]
        now = utc_now()
        session = AgentReviewSession(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            conversation_id=conversation.id,
            status=AgentReviewSessionStatus.ACTIVE.value,
            candidates_json=snapshot,
            current_index=0,
            results_json=[],
        )
        if not snapshot:
            session.status = AgentReviewSessionStatus.COMPLETED.value
            session.completed_at = now
        self.db.add(session)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing = self._active_session(workspace_id, owner_user_id, conversation.id)
            if existing is None:
                raise
            return existing
        self.db.refresh(session)
        return session

    def _active_session(
        self, workspace_id: int, owner_user_id: int, conversation_id: int
    ) -> AgentReviewSession | None:
        return self.db.scalar(
            select(AgentReviewSession).where(
                AgentReviewSession.workspace_id == workspace_id,
                AgentReviewSession.owner_user_id == owner_user_id,
                AgentReviewSession.conversation_id == conversation_id,
                AgentReviewSession.status == AgentReviewSessionStatus.ACTIVE.value,
            )
        )

    def get_session(self, public_id: str, *, owner_user_id: int) -> AgentReviewSession:
        workspace_id = self._scope(owner_user_id)
        session = self.db.scalar(
            select(AgentReviewSession).where(
                AgentReviewSession.workspace_id == workspace_id,
                AgentReviewSession.owner_user_id == owner_user_id,
                AgentReviewSession.public_id == public_id,
            )
        )
        if session is None:
            raise AgentNotFoundError("review_session_not_found", "Agent review session not found")
        return session

    def current_candidate(
        self, session: AgentReviewSession
    ) -> tuple[AgentReviewSession, dict[str, Any] | None]:
        """Skip past any candidate resolved elsewhere since the session snapshot."""
        inbox = ReviewInboxService(self.db)
        while (
            session.status == AgentReviewSessionStatus.ACTIVE.value
            and session.current_index < len(session.candidates_json)
        ):
            snapshot = session.candidates_json[session.current_index]
            live = inbox.get_agent_candidate(snapshot["review_item_public_id"])
            stale = (
                live is None
                or live.get("state") != ReviewItemState.OPEN.value
                or live["source_fingerprint"] != snapshot["source_fingerprint"]
            )
            if stale:
                session = self._record_and_advance(session, outcome="stale", snapshot=snapshot)
                continue
            return session, live
        return session, None

    def skip_current(self, session: AgentReviewSession) -> AgentReviewSession:
        session, live = self.current_candidate(session)
        if live is None:
            return session
        snapshot = session.candidates_json[session.current_index]
        return self._record_and_advance(session, outcome="skipped", snapshot=snapshot)

    def stop(self, session: AgentReviewSession) -> AgentReviewSession:
        if session.status != AgentReviewSessionStatus.ACTIVE.value:
            return session
        now = utc_now()
        session.status = AgentReviewSessionStatus.CANCELLED.value
        session.completed_at = now
        session.updated_at = now
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def propose_action(
        self,
        session: AgentReviewSession,
        *,
        registry: AgentToolRegistry,
        action: str,
        participant_names: list[str] | None = None,
        group_name: str | None = None,
        source: Literal["button", "typed"] = "button",
    ) -> AgentActionProposal:
        if action not in _ACTIONS_BY_KIND:
            raise AgentConflictError(
                "review_session_action_unsupported", f"Unsupported review action: {action}"
            )
        session, live = self.current_candidate(session)
        if live is None:
            raise AgentConflictError(
                "review_session_exhausted", "This review session has no active candidate"
            )
        if live["source_type"] not in _ACTIONS_BY_KIND[action]:
            raise AgentConflictError(
                "review_session_action_unsupported",
                "That action is not available for the current candidate",
            )
        transaction_id = live["source_entity_id"]

        if action == "mark_personal":
            tool_name = MARK_PERSONAL_TOOL_NAME
            arguments: dict[str, Any] = {
                "transaction_id": transaction_id,
                "merchant": None,
                "occurred_on": None,
            }
            synthetic_text = "Mark this transaction as personal."
        else:
            names = [name.strip() for name in (participant_names or []) if name.strip()]
            tool_name = POST_SPLITWISE_TOOL_NAME
            arguments = {
                "transaction_id": transaction_id,
                "merchant": None,
                "occurred_on": None,
                "participant_names": names,
                "group_name": group_name,
            }
            # The click IS the explicit grounding: the synthetic message
            # contains exactly (and only) the names/group the user selected,
            # so the tool's message-provenance check passes without weakening
            # it for the natural-language path.
            if group_name:
                synthetic_text = f"Split this transaction with the {group_name} group."
            elif names:
                synthetic_text = f"Split this transaction with {' and '.join(names)}."
            else:
                synthetic_text = "Split this transaction."

        context = AgentToolContext.from_session(self.db, latest_user_text=synthetic_text)
        conversation_public_id = session.conversation.public_id
        # The registry must be bound to the service for record_tool_call /
        # create_action_proposal's trusted-dispatch validation to accept it.
        agent_service = UnifiedAgentService(self.db, self.settings, tool_registry=registry)
        run = agent_service.create_run(
            conversation_public_id,
            owner_user_id=session.owner_user_id,
            trigger_message_public_id=None,
            page_context=None,
            model_name=None,
            prompt_version=REVIEW_SESSION_PROMPT_VERSION,
        )
        agent_service.start_run(run.public_id, owner_user_id=session.owner_user_id)
        dispatch = registry.prepare(tool_name, arguments, context=context)
        call = agent_service.record_tool_call(
            run.public_id, owner_user_id=session.owner_user_id, dispatch=dispatch
        )
        proposal = agent_service.create_action_proposal(
            conversation_public_id,
            owner_user_id=session.owner_user_id,
            dispatch=dispatch,
            tool_call_public_id=call.public_id,
            idempotency_key=(
                f"agent-review:{session.public_id}:{transaction_id}:"
                f"{tool_name}:{session.current_index}"
            ),
        )
        agent_service.complete_run(run.public_id, owner_user_id=session.owner_user_id)
        if source == _TYPED_REVIEW_SOURCE:
            proposal.metadata_json = {
                **(proposal.metadata_json or {}),
                "review_source": _TYPED_REVIEW_SOURCE,
            }
            self.db.add(proposal)
            self.db.commit()
            self.db.refresh(proposal)
        return proposal

    def interpret_typed_message(
        self,
        session: AgentReviewSession,
        *,
        registry: AgentToolRegistry,
        text: str,
    ) -> TypedInterpretationResult:
        """Resolve a free-typed chat message against the current candidate.

        Mirrors Telegram's AI-chat-mode pipeline: an LLM call extracts intent
        and raw name mentions only (AIIntentExtractionService), and the
        existing propose_post_splitwise_expense tool -- already wired for the
        button path -- does the actual friend/group resolution, ambiguity,
        and not-found handling. No resolution logic is duplicated here.
        """
        session, live = self.current_candidate(session)
        if live is None:
            raise AgentConflictError(
                "review_session_exhausted", "This review session has no active candidate"
            )
        intent = AIIntentExtractionService(self.settings).extract(user_message=text)
        if intent.action == "personal":
            proposal = self.propose_action(
                session, registry=registry, action="mark_personal", source=_TYPED_REVIEW_SOURCE
            )
            return TypedInterpretationResult(status="proposed", proposal=proposal)
        if intent.action == "split":
            group_name = intent.group_mentions[0] if intent.group_mentions else None
            proposal = self.propose_action(
                session,
                registry=registry,
                action="post_splitwise_expense",
                participant_names=intent.person_mentions,
                group_name=group_name,
                source=_TYPED_REVIEW_SOURCE,
            )
            return TypedInterpretationResult(status="proposed", proposal=proposal)
        message = intent.clarification_question or (
            "I can mark this personal or split it with someone -- try again, "
            "or use the buttons below."
        )
        return TypedInterpretationResult(status="clarify", message=message)

    def advance_after_proposal(
        self, session: AgentReviewSession, *, proposal_public_id: str
    ) -> AgentReviewSession:
        # Deliberately checked against the frozen snapshot, not a live
        # re-fetch: a successful proposal execution routes through the same
        # TransactionService write path that resolves the ReviewItem, so by
        # the time this runs the live candidate may already read as
        # resolved/stale -- that resolution IS this proposal's own outcome,
        # not an external change to reconcile against.
        if (
            session.status != AgentReviewSessionStatus.ACTIVE.value
            or session.current_index >= len(session.candidates_json)
        ):
            return session
        snapshot = session.candidates_json[session.current_index]
        proposal = self.agent_service.get_action_proposal(
            proposal_public_id, owner_user_id=session.owner_user_id
        )
        if proposal.status != "completed":
            return session
        params = proposal.normalized_parameters_json or {}
        matches = snapshot["source_type"] == "transaction" and params.get(
            "transaction_id"
        ) == snapshot["source_entity_id"]
        if not matches:
            return session
        outcome = "personal" if proposal.tool_name == MARK_PERSONAL_TOOL_NAME else "split"
        if (proposal.metadata_json or {}).get("review_source") == _TYPED_REVIEW_SOURCE:
            self._record_typed_memory(proposal, params)
        return self._record_and_advance(
            session, outcome=outcome, snapshot=snapshot, proposal_public_id=proposal.public_id
        )

    def _record_typed_memory(
        self, proposal: AgentActionProposal, params: dict[str, Any]
    ) -> None:
        """Best-effort: a typed split resolution improves the same merchant
        recommendation that already drives the review-session buttons, the
        same way a Telegram AI-chat-mode correction does.
        """
        transaction_id = params.get("transaction_id")
        tx = self.db.get(ExpenseTransaction, transaction_id) if transaction_id else None
        if tx is None:
            return
        if proposal.tool_name == MARK_PERSONAL_TOOL_NAME:
            final_action, final_participants, final_group_name = "personal", [], None
        else:
            final_action = "split_equal"
            payer_user_id = params.get("payer_user_id")
            final_participants = [
                {"display_name": person["display_name"]}
                for person in params.get("participants", [])
                if person.get("display_name") and person.get("user_id") != payer_user_id
            ]
            final_group_name = params.get("group_name")
        try:
            AIInterpretationMemoryService(self.db).record_agent_typed_split_memory(
                tx=tx,
                final_action=final_action,
                final_group_name=final_group_name,
                final_participants=final_participants,
                final_split_mode="equal" if final_action != "personal" else None,
            )
        except (SQLAlchemyError, TypeError, ValueError):
            return

    def _record_and_advance(
        self,
        session: AgentReviewSession,
        *,
        outcome: str,
        snapshot: dict[str, Any],
        proposal_public_id: str | None = None,
    ) -> AgentReviewSession:
        now = utc_now()
        entry = {
            "review_item_public_id": snapshot["review_item_public_id"],
            "kind": snapshot["kind"],
            "source_entity_id": snapshot["source_entity_id"],
            "outcome": outcome,
            "proposal_public_id": proposal_public_id,
        }
        session.results_json = [*session.results_json, entry]
        session.current_index = session.current_index + 1
        session.updated_at = now
        if session.current_index >= len(session.candidates_json):
            session.status = AgentReviewSessionStatus.COMPLETED.value
            session.completed_at = now
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    @staticmethod
    def summary(session: AgentReviewSession) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for entry in session.results_json:
            counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
        remaining = max(0, len(session.candidates_json) - session.current_index)
        return {
            "status": session.status,
            "reviewed": len(session.results_json),
            "personal": counts.get("personal", 0),
            "split": counts.get("split", 0),
            "skipped": counts.get("skipped", 0),
            "stale": counts.get("stale", 0),
            "remaining": remaining,
            "total_candidates": len(session.candidates_json),
        }

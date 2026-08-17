from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.action_tools import (
    MARK_PERSONAL_TOOL_NAME,
    POST_SPLITWISE_TOOL_NAME,
    MarkTransactionPersonalProposal,
    PostSplitwiseExpenseProposal,
)
from app.agent.contracts import (
    AgentActionConfirmationBlock,
    AgentActionPreview,
    AgentStructuredResponse,
)
from app.agent.service import AgentConflictError, UnifiedAgentService
from app.agent.tooling import AgentToolRegistry
from app.config import Settings, get_settings
from app.models import (
    AgentActionProposal,
    AuditEvent,
    ExpenseTransaction,
    FinancialOperation,
    SplitwiseIntegration,
    TransactionStatus,
)
from app.services.splitwise_service import SplitwiseAPIError
from app.services.transaction_service import TransactionError, TransactionService


class AgentActionExecutor:
    """Consume a confirmed, frozen proposal without invoking a model runtime."""

    def __init__(
        self,
        db: Session,
        *,
        registry: AgentToolRegistry,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.service = UnifiedAgentService(db, self.settings, tool_registry=registry)

    def confirm_and_execute(
        self,
        proposal_public_id: str,
        *,
        owner_user_id: int,
        expected_version: int,
    ) -> AgentActionProposal:
        proposal = self.service.get_action_proposal(
            proposal_public_id,
            owner_user_id=owner_user_id,
        )
        if proposal.tool_name not in {MARK_PERSONAL_TOOL_NAME, POST_SPLITWISE_TOOL_NAME}:
            raise AgentConflictError(
                "unsupported_action",
                "This Agent action is not supported by the local executor",
            )
        if proposal.status in {"completed", "failed", "ambiguous"}:
            return proposal
        if proposal.status == "awaiting_confirmation":
            proposal = self.service.confirm_action_proposal(
                proposal_public_id,
                owner_user_id=owner_user_id,
                expected_version=expected_version,
            )
        elif proposal.status not in {"confirmed", "executing"}:
            raise AgentConflictError(
                "proposal_state_conflict",
                "Agent action proposal cannot be confirmed",
            )

        proposal, claimed = self.service.claim_action_proposal_execution(
            proposal_public_id,
            owner_user_id=owner_user_id,
        )
        if proposal.status == "completed":
            return proposal
        if proposal.tool_name == POST_SPLITWISE_TOOL_NAME:
            return self._execute_splitwise(
                proposal,
                owner_user_id=owner_user_id,
                claimed=claimed,
            )
        try:
            parameters = MarkTransactionPersonalProposal.model_validate_json(
                json.dumps(proposal.normalized_parameters_json),
                strict=True,
            )
        except ValidationError as exc:
            self.service.fail_action_proposal(
                proposal_public_id,
                owner_user_id=owner_user_id,
                error_code="proposal_parameters_invalid",
                ambiguous=True,
            )
            raise AgentConflictError(
                "proposal_parameters_invalid",
                "Agent action proposal parameters are invalid",
            ) from exc

        workspace_id = self.db.info.get("workspace_id")
        transaction = self.db.scalar(
            select(ExpenseTransaction)
            .where(
                ExpenseTransaction.workspace_id == workspace_id,
                ExpenseTransaction.id == parameters.transaction_id,
            )
            .with_for_update()
        )
        if transaction is None:
            return self._fail_stale(
                proposal_public_id,
                owner_user_id=owner_user_id,
                code="action_target_not_found",
            )

        # Reconcile only a previously-started execution. A transaction manually
        # changed to personal before this confirmation is stale and is never
        # silently attributed to the Agent.
        if transaction.status == TransactionStatus.PERSONAL.value:
            latest = self.service.get_action_proposal(
                proposal_public_id,
                owner_user_id=owner_user_id,
            )
            if latest.status == "completed":
                return latest
            if not claimed and self._personal_result_belongs_to_proposal(
                proposal_public_id,
                transaction.id,
                owner_user_id=owner_user_id,
            ):
                return self.service.complete_action_proposal(
                    proposal_public_id,
                    owner_user_id=owner_user_id,
                    result_metadata=_personal_result(transaction),
                )
            return self._fail_stale(
                proposal_public_id,
                owner_user_id=owner_user_id,
                code="action_target_changed",
            )

        # Only the request that won the durable confirmed -> executing CAS may
        # perform a mutation. Followers can observe a completed transaction
        # above, but must never race the active executor.
        if not claimed:
            return self.service.get_action_proposal(
                proposal_public_id,
                owner_user_id=owner_user_id,
            )

        if transaction.status != parameters.expected_status or _aware(
            transaction.updated_at
        ) != _aware(parameters.expected_updated_at):
            return self._fail_stale(
                proposal_public_id,
                owner_user_id=owner_user_id,
                code="action_target_changed",
            )

        try:
            TransactionService.validate_mark_personal(transaction)
        except TransactionError as exc:
            self.service.fail_action_proposal(
                proposal_public_id,
                owner_user_id=owner_user_id,
                error_code="action_transition_invalid",
            )
            raise AgentConflictError(
                "action_transition_invalid",
                "The transaction can no longer be marked personal",
            ) from exc

        self.db.info["interaction_channel"] = "agent"
        self.db.info["agent_action_proposal_id"] = proposal.public_id
        try:
            transaction = TransactionService(self.db, self.settings).mark_personal(transaction.id)
        except Exception:
            self.db.rollback()
            recovered = self.db.scalar(
                select(ExpenseTransaction).where(
                    ExpenseTransaction.workspace_id == workspace_id,
                    ExpenseTransaction.id == parameters.transaction_id,
                )
            )
            if recovered is not None and recovered.status == TransactionStatus.PERSONAL.value:
                return self.service.complete_action_proposal(
                    proposal_public_id,
                    owner_user_id=owner_user_id,
                    result_metadata=_personal_result(recovered),
                )
            self.service.fail_action_proposal(
                proposal_public_id,
                owner_user_id=owner_user_id,
                error_code="action_execution_failed",
            )
            raise
        return self.service.complete_action_proposal(
            proposal_public_id,
            owner_user_id=owner_user_id,
            result_metadata=_personal_result(transaction),
        )

    def _execute_splitwise(
        self,
        proposal: AgentActionProposal,
        *,
        owner_user_id: int,
        claimed: bool,
    ) -> AgentActionProposal:
        try:
            parameters = PostSplitwiseExpenseProposal.model_validate_json(
                json.dumps(proposal.normalized_parameters_json),
                strict=True,
            )
        except ValidationError as exc:
            self.service.fail_action_proposal(
                proposal.public_id,
                owner_user_id=owner_user_id,
                error_code="proposal_parameters_invalid",
                ambiguous=True,
            )
            raise AgentConflictError(
                "proposal_parameters_invalid",
                "Agent action proposal parameters are invalid",
            ) from exc

        workspace_id = self.db.info.get("workspace_id")
        existing = self._splitwise_operation(parameters.transaction_id)
        if existing is not None:
            if existing.correlation_id != proposal.public_id:
                return self._fail_stale(
                    proposal.public_id,
                    owner_user_id=owner_user_id,
                    code="incompatible_financial_operation",
                )
            terminal = self._apply_splitwise_operation_state(
                proposal.public_id,
                existing,
                owner_user_id=owner_user_id,
            )
            if terminal is not None or existing.state in {"pending", "in_flight"}:
                return terminal or proposal

        # Provider preflight and durable operation creation belong exclusively
        # to the executor that won the proposal CAS. A concurrent confirmer may
        # only reconcile an already-recorded operation above.
        if not claimed:
            return self.service.get_action_proposal(
                proposal.public_id,
                owner_user_id=owner_user_id,
            )

        transaction = self.db.scalar(
            select(ExpenseTransaction)
            .where(
                ExpenseTransaction.workspace_id == workspace_id,
                ExpenseTransaction.id == parameters.transaction_id,
            )
            .with_for_update()
        )
        if transaction is None:
            return self._fail_stale(
                proposal.public_id,
                owner_user_id=owner_user_id,
                code="action_target_not_found",
            )
        if transaction.status != parameters.expected_status or _aware(
            transaction.updated_at
        ) != _aware(parameters.expected_updated_at):
            existing = self._splitwise_operation(parameters.transaction_id)
            if existing is not None:
                if existing.correlation_id != proposal.public_id:
                    return self._fail_stale(
                        proposal.public_id,
                        owner_user_id=owner_user_id,
                        code="incompatible_financial_operation",
                    )
                terminal = self._apply_splitwise_operation_state(
                    proposal.public_id,
                    existing,
                    owner_user_id=owner_user_id,
                )
                if terminal is not None or existing.state in {"pending", "in_flight"}:
                    return terminal or proposal
            return self._fail_stale(
                proposal.public_id,
                owner_user_id=owner_user_id,
                code="action_target_changed",
            )

        integration = self.db.scalar(
            select(SplitwiseIntegration).where(
                SplitwiseIntegration.id == parameters.splitwise_integration_id,
                SplitwiseIntegration.workspace_id == workspace_id,
                SplitwiseIntegration.user_id == owner_user_id,
                SplitwiseIntegration.enabled.is_(True),
            )
        )
        if (
            integration is None
            or integration.verified_at is None
            or integration.splitwise_user_id != str(parameters.payer_user_id)
        ):
            return self._fail_stale(
                proposal.public_id,
                owner_user_id=owner_user_id,
                code="splitwise_integration_changed",
            )

        self.db.info["interaction_channel"] = "agent"
        self.db.info["agent_action_proposal_id"] = proposal.public_id
        transaction_service = TransactionService(self.db, self.settings)
        try:
            self._revalidate_splitwise_destinations(
                transaction_service,
                parameters,
            )
        except SplitwiseAPIError:
            return self.service.fail_action_proposal(
                proposal.public_id,
                owner_user_id=owner_user_id,
                error_code="splitwise_unavailable",
            )
        except TransactionError:
            return self._fail_stale(
                proposal.public_id,
                owner_user_id=owner_user_id,
                code="splitwise_destination_changed",
            )
        try:
            transaction, _response = transaction_service.post_prepared_splitwise_expense(
                tx_id=transaction.id,
                payload=parameters.splitwise_payload,
                expected_payer_user_id=parameters.payer_user_id,
            )
        except SplitwiseAPIError as exc:
            return self.service.fail_action_proposal(
                proposal.public_id,
                owner_user_id=owner_user_id,
                error_code=("ambiguous_provider_outcome" if exc.ambiguous else "provider_rejected"),
                ambiguous=exc.ambiguous,
            )
        except (TransactionError, ValueError) as exc:
            self.service.fail_action_proposal(
                proposal.public_id,
                owner_user_id=owner_user_id,
                error_code="action_transition_invalid",
            )
            raise AgentConflictError(
                "action_transition_invalid",
                "The Splitwise expense can no longer be created safely",
            ) from exc

        operation = self._splitwise_operation(transaction.id)
        if operation is None:
            self.service.fail_action_proposal(
                proposal.public_id,
                owner_user_id=owner_user_id,
                error_code="financial_operation_missing",
                ambiguous=True,
            )
            raise AgentConflictError(
                "financial_operation_missing",
                "ExpenseOps could not verify the durable Splitwise operation",
            )
        terminal = self._apply_splitwise_operation_state(
            proposal.public_id,
            operation,
            owner_user_id=owner_user_id,
        )
        if terminal is not None:
            return terminal
        return self.service.record_action_proposal_execution_metadata(
            proposal.public_id,
            owner_user_id=owner_user_id,
            result_metadata=_splitwise_result(transaction, operation, state="queued"),
        )

    def _revalidate_splitwise_destinations(
        self,
        transaction_service: TransactionService,
        parameters: PostSplitwiseExpenseProposal,
    ) -> None:
        splitwise = transaction_service.splitwise_service
        participant_ids = {
            share.user_id
            for share in parameters.participants
            if share.user_id != parameters.payer_user_id
        }
        if parameters.group_id is None:
            friend_ids = _provider_ids(splitwise.get_friends())
            if not participant_ids.issubset(friend_ids):
                raise TransactionError("A confirmed Splitwise participant is no longer available.")
            return
        group = splitwise.get_group(parameters.group_id)
        if str(group.get("name") or group.get("id")) != parameters.group_name:
            raise TransactionError("The confirmed Splitwise group changed.")
        current_member_ids = _provider_ids(group.get("members"))
        if current_member_ids != set(parameters.group_member_ids):
            raise TransactionError("The confirmed Splitwise group membership changed.")
        if not participant_ids.issubset(current_member_ids):
            raise TransactionError("A confirmed participant is no longer in the Splitwise group.")

    def _splitwise_operation(self, transaction_id: int) -> FinancialOperation | None:
        return self.db.scalar(
            select(FinancialOperation)
            .where(
                FinancialOperation.workspace_id == self.db.info.get("workspace_id"),
                FinancialOperation.transaction_id == transaction_id,
                FinancialOperation.action == "splitwise_create",
            )
            .order_by(FinancialOperation.generation.desc(), FinancialOperation.id.desc())
        )

    def _personal_result_belongs_to_proposal(
        self,
        proposal_public_id: str,
        transaction_id: int,
        *,
        owner_user_id: int,
    ) -> bool:
        events = self.db.scalars(
            select(AuditEvent).where(
                AuditEvent.workspace_id == self.db.info.get("workspace_id"),
                AuditEvent.user_id == owner_user_id,
                AuditEvent.event_type == "transaction_marked_personal",
                AuditEvent.resource_id == str(transaction_id),
            )
        )
        return any(
            event.metadata_json.get("agent_action_proposal_id") == proposal_public_id
            for event in events
            if isinstance(event.metadata_json, dict)
        )

    def _apply_splitwise_operation_state(
        self,
        proposal_public_id: str,
        operation: FinancialOperation,
        *,
        owner_user_id: int,
    ) -> AgentActionProposal | None:
        transaction = self.db.get(ExpenseTransaction, operation.transaction_id)
        if transaction is None:
            return self.service.fail_action_proposal(
                proposal_public_id,
                owner_user_id=owner_user_id,
                error_code="action_target_not_found",
                ambiguous=True,
            )
        if operation.state == "succeeded" and operation.provider_object_id:
            return self.service.complete_action_proposal(
                proposal_public_id,
                owner_user_id=owner_user_id,
                result_metadata=_splitwise_result(transaction, operation, state="succeeded"),
            )
        if operation.state == "needs_reconciliation":
            return self.service.fail_action_proposal(
                proposal_public_id,
                owner_user_id=owner_user_id,
                error_code="ambiguous_provider_outcome",
                ambiguous=True,
            )
        if operation.state == "failed":
            return self.service.fail_action_proposal(
                proposal_public_id,
                owner_user_id=owner_user_id,
                error_code=operation.error_code or "provider_rejected",
            )
        return None

    def cancel(
        self,
        proposal_public_id: str,
        *,
        owner_user_id: int,
        expected_version: int,
    ) -> AgentActionProposal:
        return self.service.cancel_action_proposal(
            proposal_public_id,
            owner_user_id=owner_user_id,
            expected_version=expected_version,
        )

    def _fail_stale(
        self,
        proposal_public_id: str,
        *,
        owner_user_id: int,
        code: str,
    ) -> AgentActionProposal:
        self.service.fail_action_proposal(
            proposal_public_id,
            owner_user_id=owner_user_id,
            error_code=code,
        )
        raise AgentConflictError(
            code,
            "The transaction changed after this action was proposed. Nothing was changed.",
        )


def action_confirmation_block(proposal: AgentActionProposal) -> AgentActionConfirmationBlock:
    preview = AgentActionPreview.model_validate(proposal.preview_json, strict=True)
    action_by_tool = {
        MARK_PERSONAL_TOOL_NAME: "mark_transaction_personal",
        POST_SPLITWISE_TOOL_NAME: "post_splitwise_expense",
    }
    action = action_by_tool.get(proposal.tool_name)
    if action is None:
        raise AgentConflictError(
            "unsupported_action",
            "This Agent action cannot be displayed safely",
        )
    return AgentActionConfirmationBlock(
        **preview.model_dump(),
        action=action,
        proposal_id=proposal.public_id,
        proposal_version=proposal.version,
        status=proposal.status,
        expires_at=proposal.expires_at,
    )


def refresh_action_confirmation_blocks(
    response: AgentStructuredResponse,
    proposals: Mapping[str, AgentActionProposal],
) -> AgentStructuredResponse:
    blocks = []
    for block in response.blocks:
        if isinstance(block, AgentActionConfirmationBlock):
            proposal = proposals.get(block.proposal_id)
            if proposal is not None:
                blocks.append(action_confirmation_block(proposal))
                continue
        blocks.append(block)
    return AgentStructuredResponse(blocks=blocks)


def _personal_result(transaction: ExpenseTransaction) -> dict[str, Any]:
    return {
        "action": "mark_transaction_personal",
        "transaction_id": transaction.id,
        "status": transaction.status,
    }


def _splitwise_result(
    transaction: ExpenseTransaction,
    operation: FinancialOperation,
    *,
    state: str,
) -> dict[str, Any]:
    return {
        "action": "post_splitwise_expense",
        "transaction_id": transaction.id,
        "financial_operation_id": operation.id,
        "state": state,
        "provider_object_id": operation.provider_object_id,
    }


def _provider_ids(values: object) -> set[int]:
    if not isinstance(values, list):
        raise TransactionError("Splitwise returned an invalid participant list.")
    result: set[int] = set()
    for value in values:
        if not isinstance(value, dict):
            raise TransactionError("Splitwise returned an invalid participant list.")
        try:
            user_id = int(value["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TransactionError("Splitwise returned an invalid participant list.") from exc
        if user_id <= 0 or user_id in result:
            raise TransactionError("Splitwise returned an invalid participant list.")
        result.add(user_id)
    return result


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

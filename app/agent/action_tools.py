from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.agent.tooling import (
    AgentActionClarificationRequired,
    AgentTool,
    AgentToolContext,
    AgentToolRegistry,
    ToolEffect,
)
from app.models import (
    AgentActionProposal,
    ExpenseTransaction,
    HouseholdItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReceiptLineClassification,
    ReceiptParseStatus,
    SplitwiseIntegration,
    TransactionStatus,
)
from app.services.agent_service import friend_display_name, transaction_display_name
from app.services.entity_resolution_service import EntityResolutionService, normalize_mention
from app.services.receipt_learning_service import analyze_receipt_learning
from app.services.share_calculator import (
    cents_to_decimal_string,
    equal_owed_cents,
    proportional_owed_cents,
)
from app.services.splitwise_service import SplitwiseAPIError, SplitwiseService
from app.services.transaction_service import TransactionError, TransactionService

MAX_TRANSACTION_ENTITY_ID = 2_147_483_647
MARK_PERSONAL_TOOL_NAME = "propose_mark_transaction_personal"
POST_SPLITWISE_TOOL_NAME = "propose_post_splitwise_expense"
RECEIPT_LEARNING_TOOL_NAME = "propose_receipt_learning_batch"
ITEMIZED_RECEIPT_SPLIT_TOOL_NAME = "propose_itemized_receipt_split"
MAX_RECEIPT_ACTION_LINES = 20


class ActionToolModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class MarkTransactionPersonalInput(ActionToolModel):
    transaction_id: int | None = Field(default=None, ge=1, le=MAX_TRANSACTION_ENTITY_ID)
    merchant: str | None = Field(default=None, min_length=1, max_length=255)
    occurred_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    @model_validator(mode="after")
    def require_selector(self) -> MarkTransactionPersonalInput:
        if self.transaction_id is None and self.merchant is None and self.occurred_on is None:
            raise ValueError("identify the transaction by context, merchant, date, or exact id")
        if self.occurred_on is not None:
            date.fromisoformat(self.occurred_on)
        return self


class MarkTransactionPersonalProposal(ActionToolModel):
    action: Literal["mark_transaction_personal"] = "mark_transaction_personal"
    transaction_id: int = Field(ge=1, le=MAX_TRANSACTION_ENTITY_ID)
    expected_status: str = Field(min_length=1, max_length=32)
    expected_updated_at: datetime


class PostSplitwiseExpenseInput(ActionToolModel):
    transaction_id: int | None = Field(default=None, ge=1, le=MAX_TRANSACTION_ENTITY_ID)
    merchant: str | None = Field(default=None, min_length=1, max_length=255)
    occurred_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    participant_names: list[str] = Field(default_factory=list, max_length=8)
    group_name: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("participant_names")
    @classmethod
    def validate_participant_names(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            item = value.strip()
            if not item or len(item) > 120:
                raise ValueError("participant names must contain 1 to 120 characters")
            normalized.append(item)
        if len({value.casefold() for value in normalized}) != len(normalized):
            raise ValueError("participant names must be unique")
        return normalized

    @model_validator(mode="after")
    def require_split_target(self) -> PostSplitwiseExpenseInput:
        if self.transaction_id is None and self.merchant is None and self.occurred_on is None:
            raise ValueError("identify the transaction by context, merchant, date, or exact id")
        if self.occurred_on is not None:
            date.fromisoformat(self.occurred_on)
        if not self.participant_names and self.group_name is None:
            raise ValueError("name at least one participant or Splitwise group")
        return self


class SplitwiseProposalShare(ActionToolModel):
    user_id: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=255)
    paid_cents: int = Field(ge=0)
    owed_cents: int = Field(ge=0)


class PostSplitwiseExpenseProposal(ActionToolModel):
    action: Literal["post_splitwise_expense"] = "post_splitwise_expense"
    transaction_id: int = Field(ge=1, le=MAX_TRANSACTION_ENTITY_ID)
    expected_status: str = Field(min_length=1, max_length=32)
    expected_updated_at: datetime
    splitwise_integration_id: int = Field(ge=1)
    payer_user_id: int = Field(ge=1)
    payer_display_name: str = Field(min_length=1, max_length=255)
    group_id: int | None = Field(default=None, ge=1)
    group_name: str | None = Field(default=None, min_length=1, max_length=255)
    group_member_ids: list[int] = Field(default_factory=list, max_length=100)
    participants: list[SplitwiseProposalShare] = Field(min_length=2, max_length=9)
    splitwise_payload: dict


class ItemizedReceiptLineAssignmentInput(ActionToolModel):
    line_reference: str = Field(min_length=1, max_length=120)
    assignment: Literal["PERSON", "SHARED_AMONG", "ALL_PARTICIPANTS", "UNASSIGNED"]
    assignee_names: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("assignee_names")
    @classmethod
    def validate_assignee_names(cls, values: list[str]) -> list[str]:
        return _validate_unique_names(values)

    @model_validator(mode="after")
    def validate_assignment_shape(self) -> ItemizedReceiptLineAssignmentInput:
        expected = {
            "PERSON": 1,
            "SHARED_AMONG": 2,
            "ALL_PARTICIPANTS": 0,
            "UNASSIGNED": 0,
        }[self.assignment]
        if self.assignment == "SHARED_AMONG" and len(self.assignee_names) < expected:
            raise ValueError("SHARED_AMONG requires at least two named people")
        if self.assignment != "SHARED_AMONG" and len(self.assignee_names) != expected:
            raise ValueError(f"{self.assignment} requires {expected} named people")
        return self


class ItemizedReceiptSplitInput(ActionToolModel):
    receipt_id: int | None = Field(default=None, ge=1, le=MAX_TRANSACTION_ENTITY_ID)
    participant_names: list[str] = Field(default_factory=list, max_length=8)
    group_name: str | None = Field(default=None, min_length=1, max_length=255)
    assignments: list[ItemizedReceiptLineAssignmentInput] = Field(
        min_length=1,
        max_length=MAX_RECEIPT_ACTION_LINES,
    )
    tax_allocation: Literal["equal", "proportional_to_item_subtotal", "unassigned"]
    tip_allocation: Literal["equal", "proportional_to_item_subtotal", "unassigned"]

    @field_validator("participant_names")
    @classmethod
    def validate_participant_names(cls, values: list[str]) -> list[str]:
        return _validate_unique_names(values)

    @model_validator(mode="after")
    def require_receipt(self) -> ItemizedReceiptSplitInput:
        if self.receipt_id is None:
            raise ValueError("select exactly one restaurant receipt")
        return self


class ItemizedReceiptLineProposal(ActionToolModel):
    line_id: int = Field(ge=1, le=MAX_TRANSACTION_ENTITY_ID)
    name: str = Field(min_length=1, max_length=500)
    amount_cents: int = Field(gt=0)
    assignment: Literal["PERSON", "SHARED_AMONG", "ALL_PARTICIPANTS"]
    participant_user_ids: list[int] = Field(min_length=1, max_length=9)
    participant_names: list[str] = Field(min_length=1, max_length=9)

    @model_validator(mode="after")
    def validate_line_participants(self) -> ItemizedReceiptLineProposal:
        if len(set(self.participant_user_ids)) != len(self.participant_user_ids):
            raise ValueError("receipt line participant IDs must be unique")
        if len(self.participant_names) != len(self.participant_user_ids):
            raise ValueError("receipt line participant names and IDs must align")
        return self


class ItemizedReceiptParticipantProposal(SplitwiseProposalShare):
    item_names: list[str] = Field(default_factory=list, max_length=MAX_RECEIPT_ACTION_LINES)
    item_subtotal_cents: int = Field(ge=0)
    tax_cents: int = Field(ge=0)
    tip_cents: int = Field(ge=0)

    @model_validator(mode="after")
    def reconcile_participant_total(self) -> ItemizedReceiptParticipantProposal:
        if self.owed_cents != self.item_subtotal_cents + self.tax_cents + self.tip_cents:
            raise ValueError("participant item, tax, and tip shares must reconcile")
        return self


class ItemizedReceiptSplitProposal(ActionToolModel):
    action: Literal["post_itemized_receipt_split"] = "post_itemized_receipt_split"
    receipt_id: int = Field(ge=1, le=MAX_TRANSACTION_ENTITY_ID)
    receipt_snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_receipt_status: str = Field(min_length=1, max_length=32)
    expected_receipt_updated_at: datetime
    transaction_id: int = Field(ge=1, le=MAX_TRANSACTION_ENTITY_ID)
    expected_status: str = Field(min_length=1, max_length=32)
    expected_updated_at: datetime
    splitwise_integration_id: int = Field(ge=1)
    payer_user_id: int = Field(ge=1)
    payer_display_name: str = Field(min_length=1, max_length=255)
    group_id: int | None = Field(default=None, ge=1)
    group_name: str | None = Field(default=None, min_length=1, max_length=255)
    group_member_ids: list[int] = Field(default_factory=list, max_length=100)
    subtotal_cents: int = Field(gt=0)
    tax_cents: int = Field(ge=0)
    tip_cents: int = Field(ge=0)
    total_cents: int = Field(gt=0)
    tax_allocation: Literal["equal", "proportional_to_item_subtotal"]
    tip_allocation: Literal["equal", "proportional_to_item_subtotal"]
    assignments: list[ItemizedReceiptLineProposal] = Field(
        min_length=1,
        max_length=MAX_RECEIPT_ACTION_LINES,
    )
    participants: list[ItemizedReceiptParticipantProposal] = Field(min_length=2, max_length=9)
    splitwise_payload: dict

    @model_validator(mode="after")
    def reconcile_itemized_split(self) -> ItemizedReceiptSplitProposal:
        if self.subtotal_cents + self.tax_cents + self.tip_cents != self.total_cents:
            raise ValueError("receipt subtotal, tax, tip, and total must reconcile")
        if sum(line.amount_cents for line in self.assignments) != self.subtotal_cents:
            raise ValueError("assigned receipt lines must reconcile to subtotal")
        participant_ids = [person.user_id for person in self.participants]
        if len(set(participant_ids)) != len(participant_ids):
            raise ValueError("itemized participant IDs must be unique")
        if len({line.line_id for line in self.assignments}) != len(self.assignments):
            raise ValueError("each receipt line can be assigned only once")
        computed_subtotals = {user_id: 0 for user_id in participant_ids}
        for line in self.assignments:
            if any(user_id not in computed_subtotals for user_id in line.participant_user_ids):
                raise ValueError("receipt lines must reference frozen participants")
            allocations = equal_owed_cents(
                line.amount_cents,
                len(line.participant_user_ids),
            )
            for index, user_id in enumerate(line.participant_user_ids):
                computed_subtotals[user_id] += allocations[index]
        if any(person.item_subtotal_cents <= 0 for person in self.participants):
            raise ValueError("every itemized participant must have an assigned item subtotal")
        if any(
            person.item_subtotal_cents != computed_subtotals[person.user_id]
            for person in self.participants
        ):
            raise ValueError("participant item subtotals must match the frozen line assignments")
        if sum(person.tax_cents for person in self.participants) != self.tax_cents:
            raise ValueError("participant tax shares must reconcile to receipt tax")
        if sum(person.tip_cents for person in self.participants) != self.tip_cents:
            raise ValueError("participant tip shares must reconcile to receipt tip")
        if sum(person.owed_cents for person in self.participants) != self.total_cents:
            raise ValueError("participant shares must reconcile to receipt total")
        return self


class ActionProposalToolOutput(ActionToolModel):
    status: Literal["awaiting_confirmation", "clarification_required"]
    proposal_id: str | None = Field(default=None, min_length=1, max_length=128)
    proposal_version: int | None = Field(default=None, ge=1)


class ReceiptLearningEditInput(ActionToolModel):
    line_id: int = Field(ge=1, le=MAX_TRANSACTION_ENTITY_ID)
    decision: Literal[
        "match_existing",
        "create_tracked_item",
        "do_not_track",
        "leave_undecided",
    ]
    household_item_id: int | None = Field(default=None, ge=1, le=MAX_TRANSACTION_ENTITY_ID)


class ReceiptLearningBatchInput(ActionToolModel):
    receipt_id: int | None = Field(default=None, ge=1, le=MAX_TRANSACTION_ENTITY_ID)
    edits: list[ReceiptLearningEditInput] = Field(
        default_factory=list,
        max_length=MAX_RECEIPT_ACTION_LINES,
    )

    @model_validator(mode="after")
    def require_receipt(self) -> ReceiptLearningBatchInput:
        if self.receipt_id is None:
            raise ValueError("select exactly one receipt to review")
        if len({edit.line_id for edit in self.edits}) != len(self.edits):
            raise ValueError("each receipt line can be edited only once")
        return self


class ReceiptLearningLineProposal(ActionToolModel):
    line_id: int = Field(ge=1, le=MAX_TRANSACTION_ENTITY_ID)
    decision: Literal[
        "match_existing",
        "create_tracked_item",
        "do_not_track",
        "leave_undecided",
    ]
    household_item_id: int | None = Field(default=None, ge=1, le=MAX_TRANSACTION_ENTITY_ID)
    canonical_name: str | None = Field(default=None, min_length=1, max_length=255)
    classification: Literal[
        "replenishable_household",
        "perishable_grocery",
        "routine_consumption",
        "dining_or_experience",
        "one_time_purchase",
        "non_product_line",
        "uncertain",
    ]


class ReceiptLearningBatchProposal(ActionToolModel):
    action: Literal["apply_receipt_learning_batch"] = "apply_receipt_learning_batch"
    receipt_id: int = Field(ge=1, le=MAX_TRANSACTION_ENTITY_ID)
    expected_parse_status: Literal["needs_review"] = "needs_review"
    expected_updated_at: datetime
    decisions: list[ReceiptLearningLineProposal] = Field(
        min_length=1,
        max_length=MAX_RECEIPT_ACTION_LINES,
    )


def register_action_tools(registry: AgentToolRegistry) -> None:
    registry.register(
        AgentTool(
            name=MARK_PERSONAL_TOOL_NAME,
            description=(
                "Prepare, but never execute, a confirmation proposal to mark exactly one "
                "ExpenseOps transaction personal. Use the validated page transaction id for "
                "'this transaction'; otherwise provide only the merchant/date selectors the "
                "user stated. Ambiguity must be returned for clarification."
            ),
            effect=ToolEffect.WRITE,
            input_model=MarkTransactionPersonalInput,
            output_model=ActionProposalToolOutput,
            confirmation_required=True,
            proposal_model=MarkTransactionPersonalProposal,
            proposal_builder=_normalize_mark_personal,
            preview_builder=_preview_mark_personal,
            handler=_consequential_handler_must_not_run,
            version="1.0",
        )
    )
    registry.register(
        AgentTool(
            name=ITEMIZED_RECEIPT_SPLIT_TOOL_NAME,
            description=(
                "Prepare, but never execute, one exact itemized restaurant-receipt split. "
                "Pass the selected receipt, exact line phrases and people stated by the user, "
                "plus explicit equal or proportional tax/tip methods. The server resolves all "
                "entities and calculates every cent before later confirmation."
            ),
            effect=ToolEffect.EXTERNAL_ACTION,
            input_model=ItemizedReceiptSplitInput,
            output_model=ActionProposalToolOutput,
            confirmation_required=True,
            proposal_model=ItemizedReceiptSplitProposal,
            proposal_builder=_normalize_itemized_receipt_split,
            preview_builder=_preview_itemized_receipt_split,
            handler=_consequential_handler_must_not_run,
            version="1.0",
        )
    )
    registry.register(
        AgentTool(
            name=POST_SPLITWISE_TOOL_NAME,
            description=(
                "Prepare, but never execute, one equal Splitwise expense proposal for an "
                "exact ExpenseOps transaction and explicitly named people or group. Pass only "
                "names the user stated; the server resolves provider IDs, calculates shares, "
                "and requires later confirmation."
            ),
            effect=ToolEffect.EXTERNAL_ACTION,
            input_model=PostSplitwiseExpenseInput,
            output_model=ActionProposalToolOutput,
            confirmation_required=True,
            proposal_model=PostSplitwiseExpenseProposal,
            proposal_builder=_normalize_post_splitwise,
            preview_builder=_preview_post_splitwise,
            handler=_consequential_handler_must_not_run,
            version="1.0",
        )
    )
    registry.register(
        AgentTool(
            name=RECEIPT_LEARNING_TOOL_NAME,
            description=(
                "Prepare, but never execute, one receipt-learning batch for an exact receipt. "
                "Use when the user asks to learn, track, map, or reject receipt items. The "
                "server owns classifications, canonical names, defaults, and validation."
            ),
            effect=ToolEffect.WRITE,
            input_model=ReceiptLearningBatchInput,
            output_model=ActionProposalToolOutput,
            confirmation_required=True,
            proposal_model=ReceiptLearningBatchProposal,
            proposal_builder=_normalize_receipt_learning,
            preview_builder=_preview_receipt_learning,
            handler=_consequential_handler_must_not_run,
            version="1.0",
        )
    )


def _normalize_receipt_learning(
    context: AgentToolContext,
    values: ReceiptLearningBatchInput,
) -> dict:
    receipt = context.db.scalar(
        select(PurchaseReceipt)
        .options(
            selectinload(PurchaseReceipt.items).selectinload(PurchaseReceiptItem.household_item)
        )
        .where(
            PurchaseReceipt.workspace_id == context.workspace_id,
            PurchaseReceipt.id == values.receipt_id,
        )
    )
    if receipt is None:
        raise AgentActionClarificationRequired(
            "That receipt is not available.", code="action_target_not_found"
        )
    if receipt.parse_status != ReceiptParseStatus.NEEDS_REVIEW.value:
        raise AgentActionClarificationRequired(
            "That receipt no longer needs review.", code="action_target_changed"
        )
    if len(receipt.items) > MAX_RECEIPT_ACTION_LINES:
        raise AgentActionClarificationRequired(
            "This receipt has too many lines for a complete Agent preview. Open receipt review "
            "to inspect and confirm every line.",
            code="action_preview_too_large",
        )
    suggestions = {item.line_id: item for item in analyze_receipt_learning(receipt)}
    edits = {edit.line_id: edit for edit in values.edits}
    if any(line_id not in suggestions for line_id in edits):
        raise AgentActionClarificationRequired(
            "One edited receipt line is no longer available.", code="action_target_changed"
        )
    decisions = []
    for line in receipt.items:
        suggestion = suggestions[line.id]
        edit = edits.get(line.id)
        decision = edit.decision if edit else suggestion.decision
        item_id = edit.household_item_id if edit else suggestion.household_item_id
        if edit and decision == "create_tracked_item" and suggestion.decision != decision:
            raise AgentActionClarificationRequired(
                "That receipt line is not eligible to become a tracked item.",
                code="action_not_available",
            )
        if (
            edit
            and decision == "match_existing"
            and (suggestion.household_item_id is None or item_id != suggestion.household_item_id)
        ):
            raise AgentActionClarificationRequired(
                "That receipt line does not have the requested safe item match.",
                code="action_target_not_found",
            )
        if decision == "match_existing":
            if item_id is None:
                raise AgentActionClarificationRequired(
                    "Choose an existing household item for that match.",
                    code="action_target_required",
                )
            item = context.db.scalar(
                select(HouseholdItem).where(
                    HouseholdItem.workspace_id == context.workspace_id,
                    HouseholdItem.id == item_id,
                    HouseholdItem.enabled.is_(True),
                )
            )
            if item is None:
                raise AgentActionClarificationRequired(
                    "That household item is not available.", code="action_target_not_found"
                )
        else:
            item_id = None
        canonical_name = suggestion.canonical_name if decision == "create_tracked_item" else None
        if decision == "create_tracked_item" and not canonical_name:
            raise AgentActionClarificationRequired(
                "That line needs a safe household-item name before it can be tracked.",
                code="canonical_name_required",
            )
        decisions.append(
            {
                "line_id": line.id,
                "decision": decision,
                "household_item_id": item_id,
                "canonical_name": canonical_name,
                "classification": suggestion.classification,
            }
        )
    return {
        "action": "apply_receipt_learning_batch",
        "receipt_id": receipt.id,
        "expected_parse_status": "needs_review",
        "expected_updated_at": receipt.updated_at,
        "decisions": decisions,
    }


def _preview_receipt_learning(
    context: AgentToolContext,
    values: ReceiptLearningBatchProposal,
) -> dict:
    receipt = context.db.scalar(
        select(PurchaseReceipt)
        .options(
            selectinload(PurchaseReceipt.items).selectinload(PurchaseReceiptItem.household_item)
        )
        .where(
            PurchaseReceipt.workspace_id == context.workspace_id,
            PurchaseReceipt.id == values.receipt_id,
        )
        .with_for_update()
    )
    if (
        receipt is None
        or receipt.parse_status != values.expected_parse_status
        or receipt.updated_at != values.expected_updated_at
    ):
        raise AgentActionClarificationRequired(
            "That receipt changed while the review was prepared. Please try again.",
            code="action_target_changed",
        )
    counts = {
        decision: sum(item.decision == decision for item in values.decisions)
        for decision in (
            "match_existing",
            "create_tracked_item",
            "do_not_track",
            "leave_undecided",
        )
    }
    line_by_id = {line.id: line for line in receipt.items}
    line_details = []
    for index, decision in enumerate(values.decisions, start=1):
        line = line_by_id.get(decision.line_id)
        if line is None:
            raise AgentActionClarificationRequired(
                "A receipt line changed while the review was prepared.",
                code="action_target_changed",
            )
        if decision.decision == "match_existing":
            target_item = context.db.scalar(
                select(HouseholdItem).where(
                    HouseholdItem.workspace_id == context.workspace_id,
                    HouseholdItem.id == decision.household_item_id,
                )
            )
            if target_item is None:
                raise AgentActionClarificationRequired(
                    "A selected household item changed while the review was prepared.",
                    code="action_target_changed",
                )
            target = target_item.name
            effect = f"{line.raw_name} → {target}"
        elif decision.decision == "create_tracked_item":
            effect = f"{line.raw_name} → {decision.canonical_name} (Learning)"
        elif decision.decision == "do_not_track":
            effect = f"{line.raw_name} — do not track"
        else:
            effect = f"{line.raw_name} — leave undecided"
        line_details.append({"label": f"Item {index}", "value": effect[:500]})
    return {
        "title": "Learn household items from this receipt",
        "summary": (
            "Review one frozen batch. New items start in Learning with no invented cadence; "
            "nothing changes until you confirm."
        ),
        "details": [
            {"label": "Merchant", "value": receipt.merchant_raw or "Unknown merchant"},
            {"label": "Known matches", "value": str(counts["match_existing"])},
            {"label": "New Learning items", "value": str(counts["create_tracked_item"])},
            {"label": "Not tracked", "value": str(counts["do_not_track"])},
            {"label": "Needs input", "value": str(counts["leave_undecided"])},
            *line_details,
        ],
        "confirm_label": "Confirm selected",
        "cancel_label": "Cancel",
    }


def _normalize_mark_personal(
    context: AgentToolContext,
    values: MarkTransactionPersonalInput,
) -> dict:
    tx = _resolve_transaction(context, values)
    try:
        TransactionService.validate_mark_personal(tx)
    except TransactionError as exc:
        raise AgentActionClarificationRequired(str(exc), code="action_not_available") from exc
    return {
        "action": "mark_transaction_personal",
        "transaction_id": tx.id,
        "expected_status": tx.status,
        "expected_updated_at": tx.updated_at,
    }


def _preview_mark_personal(
    context: AgentToolContext,
    values: MarkTransactionPersonalProposal,
) -> dict:
    tx = context.db.scalar(
        select(ExpenseTransaction).where(
            ExpenseTransaction.workspace_id == context.workspace_id,
            ExpenseTransaction.id == values.transaction_id,
        )
    )
    if tx is None:
        raise AgentActionClarificationRequired(
            "That transaction is no longer available.",
            code="action_target_not_found",
        )
    if tx.status != values.expected_status or tx.updated_at != values.expected_updated_at:
        raise AgentActionClarificationRequired(
            "That transaction changed while the proposal was being prepared. Please try again.",
            code="action_target_changed",
        )
    merchant = transaction_display_name(tx).strip() or "Unknown merchant"
    currency = (tx.iso_currency_code or "USD").upper()
    amount = f"{currency} {cents_to_decimal_string(abs(tx.amount_cents))}"
    return {
        "title": "Mark transaction personal",
        "summary": (
            "This transaction will be marked personal and removed from shared-expense review."
        ),
        "details": [
            {"label": "Merchant", "value": merchant},
            {"label": "Date", "value": tx.date.isoformat() if tx.date else "Date unavailable"},
            {"label": "Amount", "value": amount},
            {"label": "Effect", "value": "Mark personal"},
        ],
        "confirm_label": "Mark personal",
        "cancel_label": "Cancel",
    }


def _normalize_itemized_receipt_split(
    context: AgentToolContext,
    values: ItemizedReceiptSplitInput,
) -> dict:
    user_text = context.latest_user_text or ""
    if not user_text.strip():
        raise AgentActionClarificationRequired(
            "Describe the receipt-item assignments in the current message.",
            code="itemized_assignment_required",
        )
    _validate_itemized_user_provenance(values, user_text)
    receipt = context.db.scalar(
        select(PurchaseReceipt)
        .options(selectinload(PurchaseReceipt.items))
        .where(
            PurchaseReceipt.workspace_id == context.workspace_id,
            PurchaseReceipt.id == values.receipt_id,
        )
        .with_for_update()
    )
    if receipt is None:
        raise AgentActionClarificationRequired(
            "That receipt is not available.", code="action_target_not_found"
        )
    if receipt.parse_status not in {
        ReceiptParseStatus.PARSED.value,
        ReceiptParseStatus.NEEDS_REVIEW.value,
        ReceiptParseStatus.CONFIRMED.value,
    }:
        raise AgentActionClarificationRequired(
            "That receipt is not ready for an exact itemized split.",
            code="receipt_not_ready",
        )
    if receipt.transaction_id is None:
        raise AgentActionClarificationRequired(
            "Link this receipt to its exact transaction before preparing a Splitwise split.",
            code="receipt_transaction_required",
        )
    existing_proposals = context.db.scalars(
        select(AgentActionProposal).where(
            AgentActionProposal.workspace_id == context.workspace_id,
            AgentActionProposal.owner_user_id == context.user_id,
            AgentActionProposal.tool_name == ITEMIZED_RECEIPT_SPLIT_TOOL_NAME,
            AgentActionProposal.status.in_(("awaiting_confirmation", "confirmed", "executing")),
        )
    )
    if any(
        proposal.normalized_parameters_json.get("receipt_id") == receipt.id
        for proposal in existing_proposals
        if isinstance(proposal.normalized_parameters_json, dict)
    ):
        raise AgentActionClarificationRequired(
            "This receipt already has an active itemized split proposal. "
            "Review or cancel it first.",
            code="receipt_split_already_pending",
        )
    tx = context.db.scalar(
        select(ExpenseTransaction).where(
            ExpenseTransaction.workspace_id == context.workspace_id,
            ExpenseTransaction.id == receipt.transaction_id,
        )
    )
    if tx is None:
        raise AgentActionClarificationRequired(
            "The linked transaction is not available.", code="action_target_not_found"
        )
    receipt_values = _validated_itemized_receipt_values(receipt, tx)
    lines: list[PurchaseReceiptItem] = receipt_values["lines"]
    resolved_assignments: list[tuple[PurchaseReceiptItem, ItemizedReceiptLineAssignmentInput]] = []
    used_line_ids: set[int] = set()
    for assignment in values.assignments:
        line = _resolve_receipt_line(lines, assignment.line_reference)
        if line.id in used_line_ids:
            raise AgentActionClarificationRequired(
                "One receipt item was assigned more than once.",
                code="receipt_line_assignment_duplicate",
            )
        used_line_ids.add(line.id)
        if assignment.assignment == "UNASSIGNED":
            raise AgentActionClarificationRequired(
                f"Assign {line.raw_name} before preparing the exact split.",
                code="receipt_line_unassigned",
            )
        resolved_assignments.append((line, assignment))
    missing = [line.raw_name for line in lines if line.id not in used_line_ids]
    if missing:
        raise AgentActionClarificationRequired(
            "Every priced receipt item needs an explicit assignment. Still unassigned: "
            + ", ".join(missing[:4]),
            code="receipt_line_unassigned",
        )
    integration, payer_user_id, splitwise, group, group_members, people_by_mention = (
        _resolve_itemized_people(context, values)
    )
    ordered_people: dict[int, str] = {
        payer_user_id: integration.display_name or "You",
    }
    for resolved in people_by_mention.values():
        user_id = int(resolved["id"])
        if user_id != payer_user_id:
            ordered_people.setdefault(user_id, friend_display_name(resolved))
    if len(ordered_people) < 2:
        raise AgentActionClarificationRequired(
            "Name at least one other Splitwise participant for this itemized split.",
            code="splitwise_participant_required",
        )
    if len(ordered_people) > 9:
        raise AgentActionClarificationRequired(
            "This itemized split supports at most nine participants.",
            code="splitwise_participant_limit",
        )

    item_subtotals = {user_id: 0 for user_id in ordered_people}
    item_names: dict[int, list[str]] = {user_id: [] for user_id in ordered_people}
    frozen_lines = []
    for line, assignment in resolved_assignments:
        assigned_ids = _assignment_user_ids(
            assignment,
            payer_user_id=payer_user_id,
            all_user_ids=list(ordered_people),
            people_by_mention=people_by_mention,
        )
        allocations = equal_owed_cents(int(line.line_total_cents or 0), len(assigned_ids))
        for index, user_id in enumerate(assigned_ids):
            item_subtotals[user_id] += allocations[index]
            item_names[user_id].append(line.raw_name)
        frozen_lines.append(
            {
                "line_id": line.id,
                "name": line.raw_name,
                "amount_cents": int(line.line_total_cents or 0),
                "assignment": assignment.assignment,
                "participant_user_ids": assigned_ids,
                "participant_names": [ordered_people[user_id] for user_id in assigned_ids],
            }
        )
    participants_without_items = [
        ordered_people[user_id]
        for user_id, subtotal_cents in item_subtotals.items()
        if subtotal_cents == 0
    ]
    if participants_without_items:
        raise AgentActionClarificationRequired(
            "Assign at least one receipt item to every named participant: "
            + ", ".join(participants_without_items[:4]),
            code="receipt_participant_without_items",
        )
    user_ids = list(ordered_people)
    tax_allocations = _allocate_receipt_overhead(
        receipt_values["tax_cents"],
        values.tax_allocation,
        item_subtotals,
        user_ids,
        label="tax",
    )
    tip_allocations = _allocate_receipt_overhead(
        receipt_values["tip_cents"],
        values.tip_allocation,
        item_subtotals,
        user_ids,
        label="tip",
    )
    owed_by_user_id = {
        user_id: item_subtotals[user_id] + tax_allocations[index] + tip_allocations[index]
        for index, user_id in enumerate(user_ids)
    }
    group_id = int(group["id"]) if group is not None else None
    try:
        tx, payload, shares, verified_payer_id = TransactionService(
            context.db,
            splitwise_service=splitwise,
        ).prepare_custom_split_expense(
            tx_id=tx.id,
            owed_by_user_id=owed_by_user_id,
            group_id=group_id,
            description=None,
            details="Itemized restaurant receipt split",
            currency_code=receipt.currency,
            post_pending=False,
        )
    except (TransactionError, ValueError) as exc:
        raise AgentActionClarificationRequired(
            str(exc), code="splitwise_action_not_available"
        ) from exc
    if verified_payer_id != payer_user_id:
        raise AgentActionClarificationRequired(
            "The verified Splitwise payer changed. Reconnect before splitting.",
            code="splitwise_identity_changed",
        )
    share_by_user_id = {share.user_id: share for share in shares}
    participants = []
    for index, user_id in enumerate(user_ids):
        share = share_by_user_id[user_id]
        participants.append(
            {
                "user_id": user_id,
                "display_name": ordered_people[user_id],
                "paid_cents": share.paid_cents,
                "owed_cents": share.owed_cents,
                "item_names": item_names[user_id],
                "item_subtotal_cents": item_subtotals[user_id],
                "tax_cents": tax_allocations[index],
                "tip_cents": tip_allocations[index],
            }
        )
    group_member_ids = sorted(int(member["id"]) for member in group_members)
    return {
        "action": "post_itemized_receipt_split",
        "receipt_id": receipt.id,
        "receipt_snapshot_hash": itemized_receipt_snapshot_hash(receipt),
        "expected_receipt_status": receipt.parse_status,
        "expected_receipt_updated_at": receipt.updated_at,
        "transaction_id": tx.id,
        "expected_status": tx.status,
        "expected_updated_at": tx.updated_at,
        "splitwise_integration_id": integration.id,
        "payer_user_id": payer_user_id,
        "payer_display_name": integration.display_name or "You",
        "group_id": group_id,
        "group_name": str(group.get("name") or group["id"]) if group is not None else None,
        "group_member_ids": group_member_ids,
        "subtotal_cents": receipt_values["subtotal_cents"],
        "tax_cents": receipt_values["tax_cents"],
        "tip_cents": receipt_values["tip_cents"],
        "total_cents": receipt_values["total_cents"],
        "tax_allocation": _resolved_overhead_method(
            values.tax_allocation, receipt_values["tax_cents"]
        ),
        "tip_allocation": _resolved_overhead_method(
            values.tip_allocation, receipt_values["tip_cents"]
        ),
        "assignments": frozen_lines,
        "participants": participants,
        "splitwise_payload": payload,
    }


def _preview_itemized_receipt_split(
    context: AgentToolContext,
    values: ItemizedReceiptSplitProposal,
) -> dict:
    receipt = context.db.scalar(
        select(PurchaseReceipt)
        .options(selectinload(PurchaseReceipt.items))
        .where(
            PurchaseReceipt.workspace_id == context.workspace_id,
            PurchaseReceipt.id == values.receipt_id,
        )
    )
    if (
        receipt is None
        or receipt.parse_status != values.expected_receipt_status
        or receipt.updated_at != values.expected_receipt_updated_at
        or itemized_receipt_snapshot_hash(receipt) != values.receipt_snapshot_hash
    ):
        raise AgentActionClarificationRequired(
            "That receipt changed while the itemized split was prepared. Please try again.",
            code="action_target_changed",
        )
    currency = (receipt.currency or "USD").upper()
    details = [
        {"label": "Merchant", "value": receipt.merchant_raw or "Unknown merchant"},
        {"label": "Receipt total", "value": _money(currency, values.total_cents)},
        {
            "label": "Tax / tip",
            "value": f"{_money(currency, values.tax_cents)} / {_money(currency, values.tip_cents)}",
        },
        {
            "label": "Allocation",
            "value": (
                f"Tax: {_allocation_label(values.tax_allocation)}; "
                f"tip: {_allocation_label(values.tip_allocation)}"
            ),
        },
    ]
    for index, participant in enumerate(values.participants, start=1):
        assigned = ", ".join(participant.item_names) or "No item subtotal"
        details.append(
            {
                "label": f"Person {index} — {participant.display_name}",
                "value": (
                    f"{assigned[:260]}; items {_money(currency, participant.item_subtotal_cents)}, "
                    f"tax {_money(currency, participant.tax_cents)}, "
                    f"tip {_money(currency, participant.tip_cents)}, "
                    f"final {_money(currency, participant.owed_cents)}"
                ),
            }
        )
    if values.group_name is not None:
        details.append({"label": "Splitwise group", "value": values.group_name})
    details.extend(
        [
            {"label": "Destination", "value": "Splitwise"},
            {"label": "Effect", "value": "Create one exact itemized Splitwise expense"},
        ]
    )
    return {
        "title": "Split restaurant receipt by item",
        "summary": (
            "Review every frozen item, tax/tip allocation, and final owed amount. "
            "Nothing is posted until you confirm."
        ),
        "details": details,
        "confirm_label": "Confirm itemized split",
        "cancel_label": "Cancel",
    }


def _normalize_post_splitwise(
    context: AgentToolContext,
    values: PostSplitwiseExpenseInput,
) -> dict:
    user_text = context.latest_user_text or ""
    if not user_text.strip():
        raise AgentActionClarificationRequired(
            "Describe who to split this with in the current message.",
            code="splitwise_participant_required",
        )
    _validate_post_splitwise_user_provenance(values, user_text)
    tx = _resolve_transaction(context, values)
    integration = context.db.scalar(
        select(SplitwiseIntegration).where(
            SplitwiseIntegration.workspace_id == context.workspace_id,
            SplitwiseIntegration.user_id == context.user_id,
            SplitwiseIntegration.enabled.is_(True),
        )
    )
    if (
        integration is None
        or not integration.splitwise_user_id
        or integration.verified_at is None
        or not integration.credentials_encrypted
    ):
        raise AgentActionClarificationRequired(
            "Connect and verify your personal Splitwise account in Settings before splitting.",
            code="splitwise_not_connected",
        )
    try:
        payer_user_id = int(integration.splitwise_user_id)
    except ValueError as exc:
        raise AgentActionClarificationRequired(
            "Reconnect your personal Splitwise account before splitting.",
            code="splitwise_identity_invalid",
        ) from exc

    splitwise = SplitwiseService()
    resolver = EntityResolutionService()
    try:
        friends = _provider_entities(splitwise.get_friends(), kind="friends")
        group = _resolve_group(splitwise, resolver, values.group_name)
        group_members = (
            _provider_entities(splitwise.get_group_members(int(group["id"])), kind="group members")
            if group is not None
            else []
        )
    except SplitwiseAPIError as exc:
        raise AgentActionClarificationRequired(
            "Splitwise is unavailable right now. No proposal was created.",
            code="splitwise_unavailable",
        ) from exc

    if values.participant_names:
        result = (
            resolver.resolve_people_within_group(
                values.participant_names,
                group_members,
                payer={"id": payer_user_id, "first_name": integration.display_name or "You"},
                all_friends=friends,
            )
            if group is not None
            else resolver.resolve_person_mentions(
                values.participant_names,
                friends,
                payer={"id": payer_user_id, "first_name": integration.display_name or "You"},
            )
        )
        if result.ambiguous:
            raise AgentActionClarificationRequired(
                "I found more than one Splitwise match for "
                f"{result.ambiguous[0].mention}. Which person do you mean?",
                code="splitwise_participant_ambiguous",
            )
        if result.unresolved:
            raise AgentActionClarificationRequired(
                f"I couldn't find {result.unresolved[0]} in your Splitwise account.",
                code="splitwise_participant_not_found",
            )
        selected = [item.entity for item in result.resolved if int(item.entity_id) != payer_user_id]
    else:
        selected = [member for member in group_members if int(member["id"]) != payer_user_id]

    selected_by_id = {int(person["id"]): person for person in selected}
    if not selected_by_id:
        raise AgentActionClarificationRequired(
            "Name at least one other Splitwise participant for this split.",
            code="splitwise_participant_required",
        )
    if group is not None:
        group_member_ids = {int(member["id"]) for member in group_members}
        if any(user_id not in group_member_ids for user_id in selected_by_id):
            raise AgentActionClarificationRequired(
                "One named participant is not in that Splitwise group.",
                code="splitwise_group_membership_mismatch",
            )
    else:
        group_member_ids = set()

    try:
        tx, payload, shares, verified_payer_id = TransactionService(
            context.db,
            splitwise_service=splitwise,
        ).prepare_equal_split_expense(
            tx_id=tx.id,
            friend_user_ids=sorted(selected_by_id),
            group_id=int(group["id"]) if group is not None else None,
            description=None,
            details=None,
            currency_code=None,
            post_pending=False,
        )
    except (TransactionError, ValueError) as exc:
        raise AgentActionClarificationRequired(
            str(exc), code="splitwise_action_not_available"
        ) from exc
    if verified_payer_id != payer_user_id:
        raise AgentActionClarificationRequired(
            "The verified Splitwise payer changed. Reconnect before splitting.",
            code="splitwise_identity_changed",
        )

    people = {
        payer_user_id: integration.display_name or "You",
        **{user_id: friend_display_name(person) for user_id, person in selected_by_id.items()},
    }
    return {
        "action": "post_splitwise_expense",
        "transaction_id": tx.id,
        "expected_status": tx.status,
        "expected_updated_at": tx.updated_at,
        "splitwise_integration_id": integration.id,
        "payer_user_id": payer_user_id,
        "payer_display_name": integration.display_name or "You",
        "group_id": int(group["id"]) if group is not None else None,
        "group_name": str(group.get("name") or group["id"]) if group is not None else None,
        "group_member_ids": sorted(group_member_ids),
        "participants": [
            {
                "user_id": share.user_id,
                "display_name": people.get(share.user_id, "Splitwise participant"),
                "paid_cents": share.paid_cents,
                "owed_cents": share.owed_cents,
            }
            for share in shares
        ],
        "splitwise_payload": payload,
    }


def _preview_post_splitwise(
    context: AgentToolContext,
    values: PostSplitwiseExpenseProposal,
) -> dict:
    tx = context.db.scalar(
        select(ExpenseTransaction).where(
            ExpenseTransaction.workspace_id == context.workspace_id,
            ExpenseTransaction.id == values.transaction_id,
        )
    )
    if tx is None:
        raise AgentActionClarificationRequired(
            "That transaction is no longer available.",
            code="action_target_not_found",
        )
    if tx.status != values.expected_status or tx.updated_at != values.expected_updated_at:
        raise AgentActionClarificationRequired(
            "That transaction changed while the proposal was being prepared. Please try again.",
            code="action_target_changed",
        )
    currency = (tx.iso_currency_code or "USD").upper()
    details = [
        {"label": "Merchant", "value": transaction_display_name(tx).strip() or "Unknown merchant"},
        {"label": "Date", "value": tx.date.isoformat() if tx.date else "Date unavailable"},
        {
            "label": "Total",
            "value": f"{currency} {cents_to_decimal_string(abs(tx.amount_cents))}",
        },
        {"label": "Payer", "value": values.payer_display_name},
    ]
    details.extend(
        {
            "label": f"Share {index} — {share.display_name}",
            "value": f"{currency} {cents_to_decimal_string(share.owed_cents)}",
        }
        for index, share in enumerate(values.participants, start=1)
    )
    if values.group_name is not None:
        details.append({"label": "Splitwise group", "value": values.group_name})
    details.extend(
        [
            {"label": "Destination", "value": "Splitwise"},
            {"label": "Effect", "value": "Create one equal Splitwise expense"},
        ]
    )
    return {
        "title": "Split expense",
        "summary": "This will create a Splitwise expense after you confirm.",
        "details": details,
        "confirm_label": "Confirm split",
        "cancel_label": "Cancel",
    }


def _validate_unique_names(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        item = value.strip()
        if not item or len(item) > 120:
            raise ValueError("participant names must contain 1 to 120 characters")
        normalized.append(item)
    if len({normalize_mention(value) for value in normalized}) != len(normalized):
        raise ValueError("participant names must be unique")
    return normalized


def _validate_itemized_user_provenance(
    values: ItemizedReceiptSplitInput,
    user_text: str,
) -> None:
    for assignment in values.assignments:
        if not _phrase_in_text(assignment.line_reference, user_text):
            raise AgentActionClarificationRequired(
                "Use only receipt-item phrases stated in the current message.",
                code="itemized_assignment_not_explicit",
            )
    names = [
        *values.participant_names,
        *(name for assignment in values.assignments for name in assignment.assignee_names),
    ]
    for name in names:
        if _is_payer_mention(name):
            if not re.search(r"\b(?:i|me|my|mine|myself)\b", user_text, re.IGNORECASE):
                raise AgentActionClarificationRequired(
                    "State your own item assignment explicitly.",
                    code="itemized_participant_not_explicit",
                )
        elif not _phrase_in_text(name, user_text):
            raise AgentActionClarificationRequired(
                "Use only participant names stated in the current message.",
                code="itemized_participant_not_explicit",
            )
    if values.group_name is not None and not _phrase_in_text(values.group_name, user_text):
        raise AgentActionClarificationRequired(
            "Use only a Splitwise group stated in the current message.",
            code="itemized_group_not_explicit",
        )
    methods = {values.tax_allocation, values.tip_allocation}
    if "proportional_to_item_subtotal" in methods and not re.search(
        r"\bproportion(?:al|ally)?\b", user_text, re.IGNORECASE
    ):
        raise AgentActionClarificationRequired(
            "Say explicitly if tax or tip should be proportional to assigned item subtotals.",
            code="itemized_overhead_method_not_explicit",
        )
    if "equal" in methods and not re.search(r"\bequal(?:ly)?\b", user_text, re.IGNORECASE):
        raise AgentActionClarificationRequired(
            "Say explicitly if tax or tip should be split equally.",
            code="itemized_overhead_method_not_explicit",
        )


def _validate_post_splitwise_user_provenance(
    values: PostSplitwiseExpenseInput,
    user_text: str,
) -> None:
    # Mirrors _validate_itemized_user_provenance: a model-supplied participant
    # or group name must be grounded in the exact current message (or, for
    # deterministic click-originated proposals, in the synthetic text built
    # from the user's own explicit selection) so the model can never post a
    # split naming someone the user did not actually name or select.
    for name in values.participant_names:
        if _is_payer_mention(name):
            if not re.search(r"\b(?:i|me|my|mine|myself)\b", user_text, re.IGNORECASE):
                raise AgentActionClarificationRequired(
                    "State your own participation explicitly.",
                    code="splitwise_participant_not_explicit",
                )
        elif not _phrase_in_text(name, user_text):
            raise AgentActionClarificationRequired(
                "Use only participant names stated in the current message.",
                code="splitwise_participant_not_explicit",
            )
    if values.group_name is not None and not _phrase_in_text(values.group_name, user_text):
        raise AgentActionClarificationRequired(
            "Use only a Splitwise group stated in the current message.",
            code="splitwise_group_not_explicit",
        )


def _validated_itemized_receipt_values(
    receipt: PurchaseReceipt,
    tx: ExpenseTransaction,
) -> dict:
    if receipt.subtotal_cents is None or receipt.tax_cents is None or receipt.total_cents is None:
        raise AgentActionClarificationRequired(
            "This receipt needs exact subtotal, tax, and total values before itemized splitting.",
            code="receipt_totals_incomplete",
        )
    subtotal = int(receipt.subtotal_cents)
    tax = int(receipt.tax_cents)
    total = int(receipt.total_cents)
    if subtotal <= 0 or tax < 0 or total <= 0 or subtotal + tax > total:
        raise AgentActionClarificationRequired(
            "This receipt's subtotal, tax, and total do not reconcile safely.",
            code="receipt_totals_invalid",
        )
    if abs(tx.amount_cents) != total or tx.amount_cents <= 0:
        raise AgentActionClarificationRequired(
            "The linked transaction amount does not exactly match this receipt total.",
            code="receipt_transaction_amount_mismatch",
        )
    if (tx.iso_currency_code or "USD").upper() != (receipt.currency or "USD").upper():
        raise AgentActionClarificationRequired(
            "The receipt and linked transaction currencies do not match.",
            code="receipt_transaction_currency_mismatch",
        )
    category = _normalize_phrase(tx.category or "")
    dining_evidence = any(
        line.classification == ReceiptLineClassification.DINING_OR_EXPERIENCE.value
        for line in receipt.items
    ) or any(term in category for term in ("food", "dining", "restaurant", "bar", "nightlife"))
    if not dining_evidence:
        raise AgentActionClarificationRequired(
            "This receipt is not reliably classified as restaurant or dining activity.",
            code="receipt_not_restaurant",
        )
    lines: list[PurchaseReceiptItem] = []
    for line in receipt.items:
        normalized_name = _normalize_phrase(line.raw_name)
        is_overhead = bool(
            re.fullmatch(
                r"(?:sub ?total|sales tax|tax|tip|gratuity|total|discount|coupon|savings)",
                normalized_name,
            )
        )
        if line.classification == ReceiptLineClassification.NON_PRODUCT_LINE.value or is_overhead:
            continue
        if line.line_total_cents is None or line.line_total_cents <= 0:
            raise AgentActionClarificationRequired(
                f"{line.raw_name} needs an exact positive line amount before splitting.",
                code="receipt_line_amount_incomplete",
            )
        lines.append(line)
    if not lines or len(lines) > MAX_RECEIPT_ACTION_LINES:
        raise AgentActionClarificationRequired(
            "This receipt does not have a bounded complete set of priced items.",
            code="receipt_lines_unavailable",
        )
    if sum(int(line.line_total_cents or 0) for line in lines) != subtotal:
        raise AgentActionClarificationRequired(
            "The priced receipt items do not reconcile exactly to the receipt subtotal.",
            code="receipt_lines_do_not_reconcile",
        )
    return {
        "lines": lines,
        "subtotal_cents": subtotal,
        "tax_cents": tax,
        "tip_cents": total - subtotal - tax,
        "total_cents": total,
    }


def _resolve_receipt_line(
    lines: list[PurchaseReceiptItem],
    reference: str,
) -> PurchaseReceiptItem:
    query = _normalize_phrase(reference)
    if not query:
        raise AgentActionClarificationRequired(
            "Name the receipt item to assign.", code="receipt_line_required"
        )
    exact = [line for line in lines if _normalize_phrase(line.raw_name) == query]
    if len(exact) == 1:
        return exact[0]
    query_tokens = set(query.split())
    candidates = [
        line
        for line in lines
        if query_tokens and query_tokens.issubset(set(_normalize_phrase(line.raw_name).split()))
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1 or len(exact) > 1:
        raise AgentActionClarificationRequired(
            f"More than one receipt item matches '{reference}'. Which exact item do you mean?",
            code="receipt_line_ambiguous",
        )
    raise AgentActionClarificationRequired(
        f"I couldn't find a receipt item matching '{reference}'.",
        code="receipt_line_not_found",
    )


def _resolve_itemized_people(
    context: AgentToolContext,
    values: ItemizedReceiptSplitInput,
) -> tuple[SplitwiseIntegration, int, SplitwiseService, dict | None, list[dict], dict[str, dict]]:
    integration = context.db.scalar(
        select(SplitwiseIntegration).where(
            SplitwiseIntegration.workspace_id == context.workspace_id,
            SplitwiseIntegration.user_id == context.user_id,
            SplitwiseIntegration.enabled.is_(True),
        )
    )
    if (
        integration is None
        or not integration.splitwise_user_id
        or integration.verified_at is None
        or not integration.credentials_encrypted
    ):
        raise AgentActionClarificationRequired(
            "Connect and verify your personal Splitwise account in Settings before splitting.",
            code="splitwise_not_connected",
        )
    try:
        payer_user_id = int(integration.splitwise_user_id)
    except ValueError as exc:
        raise AgentActionClarificationRequired(
            "Reconnect your personal Splitwise account before splitting.",
            code="splitwise_identity_invalid",
        ) from exc
    splitwise = SplitwiseService()
    resolver = EntityResolutionService()
    try:
        friends = _provider_entities(splitwise.get_friends(), kind="friends")
        group = _resolve_group(splitwise, resolver, values.group_name)
        group_members = (
            _provider_entities(splitwise.get_group_members(int(group["id"])), kind="group members")
            if group is not None
            else []
        )
    except SplitwiseAPIError as exc:
        raise AgentActionClarificationRequired(
            "Splitwise is unavailable right now. No proposal was created.",
            code="splitwise_unavailable",
        ) from exc
    mentions = _ordered_unique_names(
        [
            *values.participant_names,
            *(name for assignment in values.assignments for name in assignment.assignee_names),
        ]
    )
    provider_mentions = [mention for mention in mentions if not _is_payer_mention(mention)]
    result = (
        resolver.resolve_people_within_group(
            provider_mentions,
            group_members,
            payer={"id": payer_user_id, "first_name": integration.display_name or "You"},
            all_friends=friends,
        )
        if group is not None
        else resolver.resolve_person_mentions(
            provider_mentions,
            friends,
            payer={"id": payer_user_id, "first_name": integration.display_name or "You"},
        )
    )
    if result.ambiguous:
        raise AgentActionClarificationRequired(
            "I found more than one Splitwise match for "
            f"{result.ambiguous[0].mention}. Which person do you mean?",
            code="splitwise_participant_ambiguous",
        )
    if result.unresolved:
        raise AgentActionClarificationRequired(
            f"I couldn't find {result.unresolved[0]} in your Splitwise account.",
            code="splitwise_participant_not_found",
        )
    people_by_mention = {normalize_mention(item.mention): item.entity for item in result.resolved}
    for mention in mentions:
        if _is_payer_mention(mention):
            people_by_mention[normalize_mention(mention)] = {
                "id": payer_user_id,
                "first_name": integration.display_name or "You",
            }
    if group is not None:
        group_ids = {int(member["id"]) for member in group_members}
        if any(int(person["id"]) not in group_ids for person in people_by_mention.values()):
            raise AgentActionClarificationRequired(
                "One named participant is not in that Splitwise group.",
                code="splitwise_group_membership_mismatch",
            )
    return integration, payer_user_id, splitwise, group, group_members, people_by_mention


def _assignment_user_ids(
    assignment: ItemizedReceiptLineAssignmentInput,
    *,
    payer_user_id: int,
    all_user_ids: list[int],
    people_by_mention: dict[str, dict],
) -> list[int]:
    if assignment.assignment == "ALL_PARTICIPANTS":
        return all_user_ids
    ids = []
    for mention in assignment.assignee_names:
        if _is_payer_mention(mention):
            user_id = payer_user_id
        else:
            person = people_by_mention.get(normalize_mention(mention))
            if person is None:
                raise AgentActionClarificationRequired(
                    f"I couldn't resolve {mention} for that receipt item.",
                    code="splitwise_participant_not_found",
                )
            user_id = int(person["id"])
        if user_id not in ids:
            ids.append(user_id)
    if not ids:
        raise AgentActionClarificationRequired(
            "Assign every receipt item to at least one person.",
            code="receipt_line_unassigned",
        )
    return ids


def _allocate_receipt_overhead(
    amount_cents: int,
    method: str,
    item_subtotals: dict[int, int],
    user_ids: list[int],
    *,
    label: str,
) -> list[int]:
    if amount_cents == 0:
        return [0 for _ in user_ids]
    if method == "unassigned":
        raise AgentActionClarificationRequired(
            f"Choose equal or proportional allocation for {label}.",
            code=f"receipt_{label}_allocation_required",
        )
    if method == "equal":
        return equal_owed_cents(amount_cents, len(user_ids))
    return proportional_owed_cents(
        amount_cents,
        [item_subtotals[user_id] for user_id in user_ids],
    )


def _resolved_overhead_method(method: str, amount_cents: int) -> str:
    return "equal" if amount_cents == 0 and method == "unassigned" else method


def itemized_receipt_snapshot_hash(receipt: PurchaseReceipt) -> str:
    value = {
        "id": receipt.id,
        "transaction_id": receipt.transaction_id,
        "parse_status": receipt.parse_status,
        "merchant": receipt.merchant_raw,
        "subtotal_cents": receipt.subtotal_cents,
        "tax_cents": receipt.tax_cents,
        "total_cents": receipt.total_cents,
        "currency": receipt.currency,
        "items": [
            {
                "id": line.id,
                "name": line.raw_name,
                "amount_cents": line.line_total_cents,
                "classification": line.classification,
            }
            for line in sorted(receipt.items, key=lambda item: item.id)
        ],
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _ordered_unique_names(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_mention(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(value)
    return result


def _is_payer_mention(value: str) -> bool:
    return normalize_mention(value) in {"i", "me", "my", "mine", "myself", "you", "payer"}


def _normalize_phrase(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _phrase_in_text(phrase: str, text: str) -> bool:
    normalized_phrase = _normalize_phrase(phrase)
    normalized_text = _normalize_phrase(text)
    return bool(normalized_phrase) and f" {normalized_phrase} " in f" {normalized_text} "


def _allocation_label(method: str) -> str:
    return "equal" if method == "equal" else "proportional to assigned item subtotal"


def _money(currency: str, cents: int) -> str:
    return f"{currency} {cents_to_decimal_string(cents)}"


def _resolve_group(
    splitwise: SplitwiseService,
    resolver: EntityResolutionService,
    group_name: str | None,
) -> dict | None:
    if group_name is None:
        return None
    groups = _provider_entities(splitwise.get_groups(), kind="groups")
    result = resolver.resolve_group_mentions([group_name], groups)
    if result.ambiguous:
        raise AgentActionClarificationRequired(
            f"I found more than one Splitwise group matching {group_name}. "
            "Which group do you mean?",
            code="splitwise_group_ambiguous",
        )
    if result.unresolved:
        raise AgentActionClarificationRequired(
            f"I couldn't find the Splitwise group {group_name}.",
            code="splitwise_group_not_found",
        )
    return result.resolved[0].entity


def _provider_entities(values: object, *, kind: str) -> list[dict]:
    if not isinstance(values, list):
        raise SplitwiseAPIError(f"Splitwise returned invalid {kind}")
    result = []
    for value in values:
        if not isinstance(value, dict):
            raise SplitwiseAPIError(f"Splitwise returned invalid {kind}")
        try:
            entity_id = int(value["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SplitwiseAPIError(f"Splitwise returned invalid {kind}") from exc
        if entity_id <= 0:
            raise SplitwiseAPIError(f"Splitwise returned invalid {kind}")
        result.append(value)
    return result


def _resolve_transaction(
    context: AgentToolContext,
    values: MarkTransactionPersonalInput,
) -> ExpenseTransaction:
    criteria = [
        ExpenseTransaction.workspace_id == context.workspace_id,
        ExpenseTransaction.status != TransactionStatus.REMOVED.value,
    ]
    if values.transaction_id is not None:
        criteria.append(ExpenseTransaction.id == values.transaction_id)
    if values.merchant is not None:
        merchant = f"%{_escape_like(values.merchant.casefold())}%"
        criteria.append(
            or_(
                func.lower(func.coalesce(ExpenseTransaction.merchant_name, "")).like(
                    merchant,
                    escape="\\",
                ),
                func.lower(ExpenseTransaction.name).like(merchant, escape="\\"),
            )
        )
    if values.occurred_on is not None:
        criteria.append(ExpenseTransaction.date == date.fromisoformat(values.occurred_on))
    rows = list(
        context.db.scalars(
            select(ExpenseTransaction)
            .where(*criteria)
            .order_by(
                ExpenseTransaction.date.desc().nullslast(),
                ExpenseTransaction.id.desc(),
            )
            .limit(2)
        )
    )
    if not rows:
        raise AgentActionClarificationRequired(
            "I couldn't find that transaction. Which transaction should be marked personal?",
            code="action_target_not_found",
        )
    if len(rows) != 1:
        raise AgentActionClarificationRequired(
            "I found more than one matching transaction. Which transaction do you mean?",
            code="action_target_ambiguous",
        )
    return rows[0]


def _consequential_handler_must_not_run(
    _context: AgentToolContext,
    _values: BaseModel,
) -> dict:
    raise AssertionError("Consequential Agent handlers must never execute during proposal creation")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

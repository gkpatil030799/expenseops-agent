from __future__ import annotations

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
    ExpenseTransaction,
    HouseholdItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReceiptParseStatus,
    SplitwiseIntegration,
    TransactionStatus,
)
from app.services.agent_service import friend_display_name, transaction_display_name
from app.services.entity_resolution_service import EntityResolutionService
from app.services.receipt_learning_service import analyze_receipt_learning
from app.services.share_calculator import cents_to_decimal_string
from app.services.splitwise_service import SplitwiseAPIError, SplitwiseService
from app.services.transaction_service import TransactionError, TransactionService

MAX_TRANSACTION_ENTITY_ID = 2_147_483_647
MARK_PERSONAL_TOOL_NAME = "propose_mark_transaction_personal"
POST_SPLITWISE_TOOL_NAME = "propose_post_splitwise_expense"
RECEIPT_LEARNING_TOOL_NAME = "propose_receipt_learning_batch"
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


def _normalize_post_splitwise(
    context: AgentToolContext,
    values: PostSplitwiseExpenseInput,
) -> dict:
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

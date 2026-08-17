from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.agent.contracts import AgentPageContext, AgentSurface


class ContextHistoryMessage(Protocol):
    role: str
    structured_response_json: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ContextualToolPolicy:
    """Server-owned defaults derived from a validated, tenant-checked context.

    Tool/model arguments are authoritative when they are non-null. Current page
    context fills only missing values, followed by a narrowly bounded unique
    entity from canonical conversation history.
    """

    current_defaults: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    carry_defaults: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    clarification_kind: str | None = None
    referential: bool = False

    def apply(self, tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        effective = dict(arguments)
        explicit_names = {name for name, value in arguments.items() if value is not None}
        for source in (self.current_defaults, self.carry_defaults):
            for name, value in source.get(tool_name, {}).items():
                if effective.get(name) is None:
                    effective[name] = value
        if tool_name == "search_transactions":
            # These selectors are mutually exclusive. A non-null model/user
            # selector outranks the current page default for its alternate.
            explicit_review_type = "review_type" in explicit_names
            explicit_review_status = "review_status" in explicit_names
            if explicit_review_type and not explicit_review_status:
                effective["review_status"] = None
            elif explicit_review_status and not explicit_review_type:
                effective["review_type"] = None
        if tool_name == "get_receipts" and effective.get("receipt_id") is not None:
            effective["view"] = "detail"
            for name in ("merchant", "ingested_start_date", "ingested_end_date"):
                effective.pop(name, None)
        elif (
            tool_name == "get_household_replenishment"
            and effective.get("household_item_id") is not None
        ):
            effective["view"] = "item_history"
            effective.pop("query", None)
        elif tool_name == "get_errands_and_plan" and effective.get("errand_id") is not None:
            effective["status"] = "all"
        elif tool_name == "get_relevant_deals" and effective.get("deal_id") is not None:
            for name in ("category", "query"):
                if name not in explicit_names:
                    effective.pop(name, None)
        return effective


_EXPENSE_SURFACES = {
    AgentSurface.EXPENSE_REVIEW,
    AgentSurface.EXPENSE_INSIGHTS,
    AgentSurface.EXPENSE_ACTIVITY,
}
_HOUSEHOLD_SURFACES = {
    AgentSurface.HOUSEHOLD_TODAY,
    AgentSurface.HOUSEHOLD_STAPLES,
    AgentSurface.HOUSEHOLD_HISTORY,
}
_RECEIPT_SURFACES = {
    AgentSurface.HOUSEHOLD_TODAY,
    AgentSurface.HOUSEHOLD_RECEIPTS,
}
_ERRAND_SURFACES = {
    AgentSurface.HOUSEHOLD_TODAY,
    AgentSurface.HOUSEHOLD_ERRANDS,
}
_TRANSACTION_REVIEW_STATUSES = {
    "ask_user",
    "personal",
    "shared_draft",
    "approved",
    "posted",
    "posting",
    "post_ambiguous",
    "undoing",
    "undo_ambiguous",
    "reconciliation_required",
    "error",
}

_ENTITY_TOOL_ARGUMENTS: dict[str, tuple[str, dict[str, Any]]] = {
    "transaction": ("search_transactions", {}),
    "deal": ("get_relevant_deals", {}),
    "receipt": ("get_receipts", {"view": "detail"}),
    "errand": ("get_errands_and_plan", {"status": "all"}),
    "household_item": (
        "get_household_replenishment",
        {"view": "item_history"},
    ),
    "integration": ("get_integration_status", {}),
}
_ENTITY_ID_ARGUMENT = {
    "transaction": "transaction_id",
    "deal": "deal_id",
    "receipt": "receipt_id",
    "errand": "errand_id",
    "household_item": "household_item_id",
    "integration": "providers",
}
_KIND_LABEL = {
    "transaction": "transaction",
    "deal": "deal",
    "receipt": "receipt",
    "errand": "errand",
    "household_item": "household item",
    "integration": "integration",
    "spending_category": "category",
    "spending_scope": "spending scope",
    "household_selection": "household selection",
}

_TYPED_REFERENT = re.compile(
    r"\b(?:this|that|these|those)\s+"
    r"(transaction|charge|expense|deal|offer|receipt|errand|item|staple|integration|"
    r"category|transactions)\b",
    re.IGNORECASE,
)
_DESCRIBED_REFERENT = re.compile(
    r"\b(?:this|that)\s+(?:[A-Za-z0-9&'’-]+\s+){1,3}"
    r"(transaction|charge|expense|deal|offer|receipt|errand|item|staple|integration)\b",
    re.IGNORECASE,
)
_UNTYPED_REFERENT = re.compile(
    r"\b(?:about|for|of|with|save|dismiss|complete|finish|map|match|mark|tell|explain|"
    r"relevant|buy|bought|before|increase|changed)\s+(?:this|that|it)\b|"
    r"^(?:and\s+)?(?:what\s+about\s+)?(?:this|that|it)[?.!\s]*$",
    re.IGNORECASE,
)
_FOLLOW_UP_REFERENT = re.compile(r"^\s*(?:and\s+)?before\s+that\??\s*$", re.IGNORECASE)
_STANDALONE_REFERENT = re.compile(
    r"\b(?:is|was|does|did|can|could|would|should)\s+(?:this|that|it)\b|"
    r"\b(?:this|that|it)\s+(?:increase|change|mean|refer|include|contain|cost|work|matter)\b|"
    r"\b(?:this|that|it)\b[?.!\s]*$|\bhere\b",
    re.IGNORECASE,
)


def build_contextual_tool_policy(
    *,
    text: str,
    page_context: AgentPageContext | None,
    history: Sequence[ContextHistoryMessage] = (),
) -> ContextualToolPolicy:
    """Build deterministic context defaults without granting authority.

    `page_context` must already have passed contract validation and the runtime's
    tenant/entity lookup. This helper never accepts workspace or user identity.
    """

    current = _surface_filter_defaults(page_context)
    requested_kind = _requested_entity_kind(text)
    has_described_target = _DESCRIBED_REFERENT.search(text) is not None
    referential = _is_referential(text)
    if not referential:
        return ContextualToolPolicy(current_defaults=current)

    current_target: str | None = None
    if page_context is not None and page_context.entity is not None:
        entity_kind = page_context.entity.kind
        if requested_kind is None or requested_kind == entity_kind:
            current_target = entity_kind
            _merge_entity_default(
                current,
                kind=entity_kind,
                public_id=page_context.entity.public_id,
            )
    elif _spending_scope_is_unambiguous(text, page_context, requested_kind):
        current_target = "spending_scope"

    if current_target is not None:
        return ContextualToolPolicy(
            current_defaults=current,
            referential=True,
        )

    current_surface_kind = _clarification_kind_from_surface(page_context)
    if (
        page_context is not None
        and not has_described_target
        and (
            requested_kind is None
            or requested_kind == current_surface_kind
            or current_surface_kind == "household_selection"
        )
    ):
        return ContextualToolPolicy(
            current_defaults=current,
            clarification_kind=current_surface_kind or "selection",
            referential=True,
        )

    carry_candidates = _history_entity_candidates(history)
    if requested_kind is not None:
        carry_candidates = [item for item in carry_candidates if item[0] == requested_kind]
    unique_candidates = list(dict.fromkeys(carry_candidates))
    if len(unique_candidates) == 1:
        kind, public_id = unique_candidates[0]
        carry: dict[str, dict[str, Any]] = {}
        _merge_entity_default(carry, kind=kind, public_id=public_id)
        return ContextualToolPolicy(
            current_defaults=current,
            carry_defaults=carry,
            referential=True,
        )

    if has_described_target:
        return ContextualToolPolicy(current_defaults=current, referential=True)

    clarification_kind = requested_kind or _clarification_kind_from_surface(page_context)
    return ContextualToolPolicy(
        current_defaults=current,
        clarification_kind=clarification_kind or "selection",
        referential=True,
    )


def contextual_clarification_text(kind: str) -> str:
    if kind == "household_selection":
        return "Which household item, receipt, or errand do you mean?"
    label = _KIND_LABEL.get(kind)
    if label is None:
        return "Which item do you mean? Select one or describe it more specifically."
    if kind == "spending_scope":
        return "Which spending range or category do you mean?"
    return f"Which {label} do you mean? Select one or describe it more specifically."


def _surface_filter_defaults(
    page_context: AgentPageContext | None,
) -> dict[str, dict[str, Any]]:
    if page_context is None:
        return {}
    filters = page_context.filters
    values = filters.model_dump(exclude_none=True, mode="json")
    result: dict[str, dict[str, Any]] = {}

    if page_context.surface in _EXPENSE_SURFACES:
        spending_keys = {
            "start_date",
            "end_date",
            "account_id",
            "category",
            "merchant",
            "currency_code",
            "spend_basis",
        }
        search_keys = {
            "start_date",
            "end_date",
            "category",
            "merchant",
            "currency_code",
        }
        _put_selected(result, "get_spending_insights", values, spending_keys)
        _put_selected(result, "search_transactions", values, search_keys)
        status = values.get("status")
        if status in {"all", "personal", "shared"}:
            result.setdefault("get_spending_insights", {})["review_type"] = status
            result.setdefault("search_transactions", {})["review_type"] = status
        elif status == "unreviewed":
            result.setdefault("search_transactions", {})["review_type"] = status
        elif status in _TRANSACTION_REVIEW_STATUSES:
            result.setdefault("search_transactions", {})["review_status"] = status

    if page_context.surface is AgentSurface.DEALS:
        _put_selected(result, "get_relevant_deals", values, {"category", "query"})

    if page_context.surface in _HOUSEHOLD_SURFACES:
        _put_selected(result, "get_household_replenishment", values, {"query"})

    if page_context.surface in _RECEIPT_SURFACES:
        receipt_values = dict(values)
        if "start_date" in receipt_values:
            receipt_values["ingested_start_date"] = receipt_values.pop("start_date")
        if "end_date" in receipt_values:
            receipt_values["ingested_end_date"] = receipt_values.pop("end_date")
        _put_selected(
            result,
            "get_receipts",
            receipt_values,
            {"merchant", "ingested_start_date", "ingested_end_date"},
        )
        if values.get("status") == "needs_review":
            result.setdefault("get_receipts", {})["view"] = "needs_review"

    if page_context.surface in _ERRAND_SURFACES:
        status = values.get("status")
        if status in {"active", "open", "planned", "completed", "skipped", "all"}:
            result.setdefault("get_errands_and_plan", {})["status"] = status

    return result


def _put_selected(
    target: dict[str, dict[str, Any]],
    tool_name: str,
    values: Mapping[str, Any],
    keys: set[str],
) -> None:
    selected = {key: values[key] for key in keys if key in values}
    if selected:
        target.setdefault(tool_name, {}).update(selected)


def _merge_entity_default(
    target: dict[str, dict[str, Any]],
    *,
    kind: str,
    public_id: str,
) -> None:
    tool_name, supporting = _ENTITY_TOOL_ARGUMENTS[kind]
    values = target.setdefault(tool_name, {})
    values.update({key: value for key, value in supporting.items() if key not in values})
    identifier: Any = public_id
    if kind != "integration":
        identifier = int(public_id)
    else:
        identifier = [public_id]
    values.setdefault(_ENTITY_ID_ARGUMENT[kind], identifier)
    if kind == "transaction":
        for action_tool in (
            "propose_mark_transaction_personal",
            "propose_post_splitwise_expense",
        ):
            target.setdefault(action_tool, {}).setdefault(
                "transaction_id",
                identifier,
            )
    if kind in {"receipt", "household_item"}:
        # Exact-detail input contracts prohibit their broad list filters.
        allowed = {"view", _ENTITY_ID_ARGUMENT[kind]}
        target[tool_name] = {key: value for key, value in values.items() if key in allowed}


def _is_referential(text: str) -> bool:
    if re.search(
        r"\b(?:what|which)\b.{0,48}\bneeds?\s+(?:my\s+)?attention\b",
        text,
        re.IGNORECASE,
    ) and re.search(r"\bhandle\s+all\s+of\s+it\b", text, re.IGNORECASE):
        # "it" has the explicit same-message antecedent "what needs attention";
        # it is an action request, not an unresolved page entity reference.
        return False
    if (
        _TYPED_REFERENT.search(text)
        or _DESCRIBED_REFERENT.search(text)
        or _UNTYPED_REFERENT.search(text)
        or _FOLLOW_UP_REFERENT.search(text)
    ):
        return True
    without_temporal_this = re.sub(
        r"\bthis\s+(?:day|week|month|year|quarter|period|morning|afternoon|evening|time)\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    if (
        re.search(
            r"\b(?:increas(?:e|ed)|decreas(?:e|ed)|higher|lower|chang(?:e|ed))\b",
            without_temporal_this,
            re.IGNORECASE,
        )
        and re.search(r"\btransactions?\b", without_temporal_this, re.IGNORECASE)
        and re.search(r"\b(?:drive|drives|drove)\s+it\b", without_temporal_this, re.IGNORECASE)
        and re.search(r"\b(?:this|that)\b", without_temporal_this, re.IGNORECASE) is None
    ):
        # Here "it" has an explicit same-message antecedent (the named increase),
        # so it is not an unresolved entity reference requiring page/history carry.
        return False
    return bool(_STANDALONE_REFERENT.search(without_temporal_this))


def _requested_entity_kind(text: str) -> str | None:
    match = _TYPED_REFERENT.search(text) or _DESCRIBED_REFERENT.search(text)
    if match is not None:
        value = match.group(1).casefold()
        return {
            "transaction": "transaction",
            "transactions": "transaction",
            "charge": "transaction",
            "expense": "transaction",
            "deal": "deal",
            "offer": "deal",
            "receipt": "receipt",
            "errand": "errand",
            "item": "household_item",
            "staple": "household_item",
            "integration": "integration",
            "category": "spending_category",
        }[value]
    lowered = text.casefold()
    if "increase" in lowered and ("this" in lowered or "that" in lowered):
        return "spending_scope"
    if "last buy" in lowered or _FOLLOW_UP_REFERENT.search(text):
        return "household_item"
    return None


def _spending_scope_is_unambiguous(
    text: str,
    page_context: AgentPageContext | None,
    requested_kind: str | None,
) -> bool:
    if page_context is None or page_context.surface not in _EXPENSE_SURFACES:
        return False
    if requested_kind == "spending_category":
        return bool(page_context.filters.category)
    if requested_kind not in {None, "spending_scope", "transaction"}:
        return False
    if requested_kind == "transaction":
        return False
    filters = page_context.filters
    return bool(filters.category or filters.start_date or filters.end_date or filters.merchant)


def _history_entity_candidates(
    history: Sequence[ContextHistoryMessage],
) -> list[tuple[str, str]]:
    for message in reversed(history):
        if getattr(message, "role", None) != "assistant":
            continue
        payload = getattr(message, "structured_response_json", None)
        if not isinstance(payload, dict):
            continue
        # The most recent canonical assistant response is the carry-forward
        # boundary. Never skip over a clarification/empty result to revive an
        # older entity after the conversational subject has changed.
        return _response_entity_candidates(payload)
    return []


def _response_entity_candidates(payload: Mapping[str, Any]) -> list[tuple[str, str]]:
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        return []
    candidates: list[tuple[str, str]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "transaction_list":
            _extend_public_ids(candidates, "transaction", block.get("transactions"))
        elif block_type == "deal_list":
            _extend_public_ids(candidates, "deal", block.get("deals"))
        elif block_type == "receipt_summary":
            public_id = block.get("public_id")
            if isinstance(public_id, str):
                candidates.append(("receipt", public_id))
        elif block_type == "errand_summary":
            _extend_public_ids(candidates, "errand", block.get("errands"))
        elif block_type == "replenishment_summary":
            _extend_public_ids(candidates, "household_item", block.get("items"))
        elif block_type == "integration_status":
            integrations = block.get("integrations")
            if isinstance(integrations, list):
                for value in integrations:
                    if isinstance(value, dict) and isinstance(value.get("provider"), str):
                        candidates.append(("integration", value["provider"]))
    return candidates


def _extend_public_ids(
    target: list[tuple[str, str]],
    kind: str,
    values: Any,
) -> None:
    if not isinstance(values, list):
        return
    for value in values:
        if isinstance(value, dict) and isinstance(value.get("public_id"), str):
            target.append((kind, value["public_id"]))


def _clarification_kind_from_surface(page_context: AgentPageContext | None) -> str | None:
    if page_context is None:
        return None
    if page_context.surface is AgentSurface.EXPENSE_INSIGHTS:
        return "spending_scope"
    if page_context.surface in {AgentSurface.EXPENSE_REVIEW, AgentSurface.EXPENSE_ACTIVITY}:
        return "transaction"
    if page_context.surface is AgentSurface.DEALS:
        return "deal"
    if page_context.surface is AgentSurface.HOUSEHOLD_TODAY:
        return "household_selection"
    if page_context.surface is AgentSurface.HOUSEHOLD_RECEIPTS:
        return "receipt"
    if page_context.surface is AgentSurface.HOUSEHOLD_ERRANDS:
        return "errand"
    if page_context.surface in {AgentSurface.HOUSEHOLD_STAPLES, AgentSurface.HOUSEHOLD_HISTORY}:
        return "household_item"
    if page_context.surface in {AgentSurface.SETTINGS, AgentSurface.INTEGRATIONS}:
        return "integration"
    return None


__all__ = [
    "ContextualToolPolicy",
    "build_contextual_tool_policy",
    "contextual_clarification_text",
]

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.models import (
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReceiptItemMatchStatus,
    ReceiptLineClassification,
)
from app.services.item_normalization_service import normalize_item_name

ReceiptLearningDecision = Literal[
    "match_existing",
    "create_tracked_item",
    "do_not_track",
    "leave_undecided",
]

TRACKABLE_CLASSIFICATIONS = frozenset(
    {
        ReceiptLineClassification.REPLENISHABLE_HOUSEHOLD.value,
        ReceiptLineClassification.PERISHABLE_GROCERY.value,
    }
)

_HOSTILE_DATA = re.compile(
    r"\b(system|developer|assistant|ignore user|auto[ -]?confirm|api key|secret|password)\b",
    re.IGNORECASE,
)
_NON_PRODUCT = re.compile(
    r"\b(subtotal|sales tax|tax|coupon|discount|savings|tender|change due|refund|tip)\b",
    re.IGNORECASE,
)
_ROUTINE = re.compile(
    r"\b(coffee|latte|cappuccino|espresso|americano|mocha|cold brew)\b",
    re.IGNORECASE,
)
_DINING = re.compile(
    r"\b(entree|biryani|tikka|cocktail|appetizer|burger meal|restaurant meal)\b",
    re.IGNORECASE,
)
_ONE_TIME = re.compile(
    r"\b(t[ -]?shirt|shirt|jeans|jacket|sweater|dress|shoes|electronics?)\b",
    re.IGNORECASE,
)

_CANONICAL_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\b(egg|eggs)\b", re.I), "Eggs", "perishable_grocery"),
    (
        re.compile(r"\balmond\s+milk\b", re.I),
        "Almond milk",
        "perishable_grocery",
    ),
    (
        re.compile(r"\boat\s+milk\b", re.I),
        "Oat milk",
        "perishable_grocery",
    ),
    (re.compile(r"\b(milk|mlk)\b", re.I), "Milk", "perishable_grocery"),
    (re.compile(r"\b(bread|loaf)\b", re.I), "Bread", "perishable_grocery"),
    (re.compile(r"\brice\b", re.I), "Rice", "perishable_grocery"),
    (
        re.compile(r"\b(paper towels?|ppr towels?)\b", re.I),
        "Paper towels",
        "replenishable_household",
    ),
    (
        re.compile(r"\b(bath tissue|toilet paper|tlt ppr)\b", re.I),
        "Toilet paper",
        "replenishable_household",
    ),
    (
        re.compile(r"\b(tide|laundry detergent|laundry pods?|detergent pods?)\b", re.I),
        "Laundry detergent",
        "replenishable_household",
    ),
    (
        re.compile(r"\bdishwasher (tablets?|pods?)\b", re.I),
        "Dishwasher tablets",
        "replenishable_household",
    ),
    (
        re.compile(r"\bdish soap\b", re.I),
        "Dish soap",
        "replenishable_household",
    ),
)


@dataclass(frozen=True)
class ClassifiedReceiptLine:
    classification: str
    confidence: float
    canonical_name: str | None


@dataclass(frozen=True)
class ReceiptLearningSuggestion:
    line_id: int
    raw_name: str
    classification: str
    classification_confidence: float
    decision: ReceiptLearningDecision
    household_item_id: int | None
    household_item_name: str | None
    canonical_name: str | None
    reason: str


def classify_receipt_line(
    *,
    raw_name: str,
    category: str | None,
    model_classification: str | None,
    model_confidence: float,
    model_canonical_name: str | None,
    is_household_purchase: bool,
) -> ClassifiedReceiptLine:
    """Resolve model evidence through a small, closed tracking policy.

    The receipt model may suggest semantics, but only these code-owned outcomes
    can enter the durable candidate workflow. Raw receipt text is always data.
    """

    evidence = " ".join(filter(None, (raw_name, category))).strip()
    if _HOSTILE_DATA.search(evidence):
        return ClassifiedReceiptLine("uncertain", 0.0, None)
    if _NON_PRODUCT.search(evidence):
        return ClassifiedReceiptLine("non_product_line", 1.0, None)
    if _ROUTINE.search(evidence):
        return ClassifiedReceiptLine("routine_consumption", 0.99, None)
    if _DINING.search(evidence) or (category or "").casefold() in {
        "restaurant",
        "dining",
        "food service",
    }:
        return ClassifiedReceiptLine("dining_or_experience", 0.99, None)
    if _ONE_TIME.search(evidence):
        return ClassifiedReceiptLine("one_time_purchase", 0.99, None)

    for pattern, canonical_name, classification in _CANONICAL_RULES:
        if pattern.search(raw_name):
            return ClassifiedReceiptLine(classification, 0.98, canonical_name)

    allowed = {value.value for value in ReceiptLineClassification}
    classification = (
        model_classification
        if model_classification in allowed
        else (
            ReceiptLineClassification.REPLENISHABLE_HOUSEHOLD.value
            if is_household_purchase
            else ReceiptLineClassification.UNCERTAIN.value
        )
    )
    confidence = min(1.0, max(0.0, model_confidence))
    canonical_name = None
    if classification in TRACKABLE_CLASSIFICATIONS:
        canonical_name = _safe_canonical_name(model_canonical_name, raw_name)
    return ClassifiedReceiptLine(classification, confidence, canonical_name)


def analyze_receipt_learning(receipt: PurchaseReceipt) -> list[ReceiptLearningSuggestion]:
    return [suggest_receipt_line(line) for line in receipt.items]


def suggest_receipt_line(line: PurchaseReceiptItem) -> ReceiptLearningSuggestion:
    classification = line.classification or ReceiptLineClassification.UNCERTAIN.value
    confidence = float(line.classification_confidence or 0.0)
    if (
        line.match_status == ReceiptItemMatchStatus.MATCHED.value
        and line.household_item_id is not None
    ):
        decision: ReceiptLearningDecision = "match_existing"
        reason = "Known item matched with strong alias or canonical evidence."
    elif (
        line.match_status == ReceiptItemMatchStatus.POSSIBLE.value
        and line.household_item_id is not None
    ):
        decision = "leave_undecided"
        reason = "A possible match needs confirmation."
    elif classification in TRACKABLE_CLASSIFICATIONS and confidence >= 0.75:
        decision = "create_tracked_item"
        reason = "Likely replenishable purchase with no safe existing match."
    elif classification in {
        ReceiptLineClassification.ROUTINE_CONSUMPTION.value,
        ReceiptLineClassification.DINING_OR_EXPERIENCE.value,
        ReceiptLineClassification.ONE_TIME_PURCHASE.value,
        ReceiptLineClassification.NON_PRODUCT_LINE.value,
    }:
        decision = "do_not_track"
        reason = _not_tracked_reason(classification)
    else:
        decision = "leave_undecided"
        reason = "The line is uncertain and will not be learned automatically."
    return ReceiptLearningSuggestion(
        line_id=line.id,
        raw_name=line.raw_name,
        classification=classification,
        classification_confidence=confidence,
        decision=decision,
        household_item_id=line.household_item_id,
        household_item_name=line.household_item.name if line.household_item else None,
        canonical_name=line.canonical_name,
        reason=reason,
    )


def safe_learning_metrics(receipt: PurchaseReceipt) -> dict[str, int]:
    suggestions = analyze_receipt_learning(receipt)
    return {
        "receipt_lines_processed": len(suggestions),
        "auto_matches": sum(item.decision == "match_existing" for item in suggestions),
        "suggested_matches": sum(
            item.household_item_id is not None and item.decision == "leave_undecided"
            for item in suggestions
        ),
        "new_item_candidates": sum(item.decision == "create_tracked_item" for item in suggestions),
        "unmatched_lines": sum(item.decision == "leave_undecided" for item in suggestions),
    }


def _safe_canonical_name(suggested: str | None, raw_name: str) -> str | None:
    value = (suggested or "").strip()
    if value and len(value) <= 255 and not _HOSTILE_DATA.search(value):
        return value
    normalized = normalize_item_name(raw_name)
    if not normalized:
        return None
    return " ".join(word.capitalize() for word in normalized.split())[:255]


def _not_tracked_reason(classification: str) -> str:
    return {
        "routine_consumption": "Routine consumption is not household inventory.",
        "dining_or_experience": "Dining and experiences are outside replenishment.",
        "one_time_purchase": "A one-time purchase is not a recurring staple.",
        "non_product_line": "This is not a purchased product line.",
    }[classification]

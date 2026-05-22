from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Classification = Literal["likely_personal", "likely_shared", "unsure"]

PERSONAL_KEYWORDS = {
    "coffee",
    "dunkin",
    "dutch bros",
    "mcdonald",
    "mcdonald's",
    "starbucks",
}

SHARED_KEYWORDS = {
    "costco",
    "groceries",
    "grocery",
    "lyft",
    "supermarket",
    "target",
    "uber",
    "walmart",
}

RESTAURANT_KEYWORDS = {
    "bar",
    "cafe",
    "dining",
    "grill",
    "pizza",
    "restaurant",
}


@dataclass(frozen=True)
class TransactionClassification:
    suggestion: Classification
    reason: str


def classify_transaction_recommendation(
    *,
    merchant_name: str | None,
    name: str | None,
    amount_cents: int,
    category: str | None = None,
) -> TransactionClassification:
    text = " ".join(
        part.lower()
        for part in [merchant_name, name, category]
        if part and part.strip()
    )
    amount = abs(amount_cents) / 100

    if not text or text in {"unknown", "unknown merchant", "unknown transaction"}:
        return TransactionClassification("unsure", "Unknown merchant or transaction name.")

    if _contains_any(text, SHARED_KEYWORDS):
        return TransactionClassification(
            "likely_shared", "Merchant/category often maps to shared spending."
        )

    if _contains_any(text, RESTAURANT_KEYWORDS) and amount > 40:
        return TransactionClassification("likely_shared", "Restaurant/dining amount is over $40.")

    if _contains_any(text, PERSONAL_KEYWORDS):
        return TransactionClassification(
            "likely_personal", "Merchant/category often maps to personal spending."
        )

    return TransactionClassification("unsure", "No deterministic rule matched.")


def _contains_any(text: str, keywords: set[str]) -> bool:
    return any(keyword in text for keyword in keywords)

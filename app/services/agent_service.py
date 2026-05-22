from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import ExpenseTransaction, TransactionStatus
from app.schemas import FriendOut

SHARED_HINTS = {
    "costco",
    "walmart",
    "target",
    "uber",
    "lyft",
    "doordash",
    "ubereats",
    "uber eats",
    "restaurant",
    "bar",
    "airbnb",
    "hotel",
    "grocery",
}

PERSONAL_HINTS = {
    "tuition",
    "pharmacy",
    "doctor",
    "hospital",
    "dentist",
    "insurance",
    "payroll",
    "salary",
}


@dataclass(frozen=True)
class Classification:
    status: TransactionStatus
    confidence: float
    reason: str
    question: str


def transaction_display_name(tx: ExpenseTransaction) -> str:
    return tx.merchant_name or tx.name


def build_agent_question(tx: ExpenseTransaction) -> str:
    amount = abs(tx.amount_cents) / 100
    pending_prefix = "Pending " if tx.pending else ""
    return (
        f"{pending_prefix}{transaction_display_name(tx)} — "
        f"{tx.iso_currency_code} {amount:.2f}. Is this personal or shared?"
    )


def classify_transaction(tx: ExpenseTransaction) -> Classification:
    """Rule-based v1 classifier.

    The safest MVP asks the user about every outgoing charge. The hints are kept here so
    you can later use them to prioritize or auto-suggest likely shared expenses without
    auto-posting anything.
    """

    text = f"{tx.merchant_name or ''} {tx.name or ''} {tx.category or ''}".lower()
    if tx.amount_cents <= 0:
        return Classification(
            status=TransactionStatus.PERSONAL,
            confidence=0.7,
            reason="Refunds/credits are not posted to Splitwise by default.",
            question="No action needed for this refund/credit.",
        )

    if any(hint in text for hint in SHARED_HINTS):
        reason = "Merchant/category looks commonly shared, but user approval is still required."
        confidence = 0.65
    elif any(hint in text for hint in PERSONAL_HINTS):
        reason = "Merchant/category looks personal, but user approval is still required."
        confidence = 0.55
    else:
        reason = "New charge detected; asking user to classify it."
        confidence = 0.5

    return Classification(
        status=TransactionStatus.ASK_USER,
        confidence=confidence,
        reason=reason,
        question=build_agent_question(tx),
    )


def parse_friend_names_from_text(text: str) -> list[str]:
    normalized = re.sub(r"[,;]", " ", text.lower())
    for prefix in ["split equally with", "split with", "shared with", "with"]:
        normalized = normalized.replace(prefix, " ")
    tokens = [token.strip() for token in normalized.split() if len(token.strip()) >= 2]
    stopwords = {
        "split",
        "equally",
        "equal",
        "shared",
        "personal",
        "this",
        "expense",
        "and",
        "with",
    }
    return [token for token in tokens if token not in stopwords]


def friend_display_name(friend: dict) -> str:
    parts = [friend.get("first_name"), friend.get("last_name")]
    name = " ".join(part for part in parts if part).strip()
    return name or friend.get("email") or str(friend.get("id"))


def match_friends(text: str, friends: list[dict]) -> list[FriendOut]:
    names = parse_friend_names_from_text(text)
    if not names:
        return []
    matches: list[FriendOut] = []
    seen: set[int] = set()
    for friend in friends:
        display = friend_display_name(friend)
        haystack = f"{display} {friend.get('email') or ''}".lower()
        if any(name in haystack for name in names):
            friend_id = int(friend["id"])
            if friend_id not in seen:
                seen.add(friend_id)
                matches.append(
                    FriendOut(
                        id=friend_id,
                        first_name=friend.get("first_name"),
                        last_name=friend.get("last_name"),
                        email=friend.get("email"),
                        display_name=display,
                    )
                )
    return matches

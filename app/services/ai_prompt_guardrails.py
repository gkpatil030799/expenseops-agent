from __future__ import annotations

import re
from dataclasses import dataclass

MAX_AI_CHAT_MESSAGE_LENGTH = 1000
OUT_OF_SCOPE_MESSAGE = (
    "I can only help classify or split this expense. Try: split with Rahul and Akash."
)
TOO_LONG_MESSAGE = "That message is too long. Please describe the split briefly."


@dataclass(frozen=True)
class GuardrailResult:
    allowed: bool
    reason: str | None
    safe_message: str
    user_message: str | None


PROMPT_INJECTION_PATTERNS = (
    "ignore previous instructions",
    "override developer instructions",
    "system prompt",
    "jailbreak",
    "act as",
    "reveal hidden",
    "print secrets",
    "show api key",
    "bypass",
    "do not follow",
    "forget your rules",
)

DANGEROUS_ACTION_PATTERNS = (
    "delete all data",
    "delete all transactions",
    "expose tokens",
    "expose secrets",
    "export my secrets",
    "access bank credentials",
    "bank credentials",
    "post without confirmation",
    "auto approve everything",
    "auto-approve everything",
    "bypass confirmation",
    "modify plaid",
    "modify chase",
    "send my plaid token",
    "plaid token",
    "api key",
)

OUT_OF_SCOPE_PATTERNS = (
    "write me code",
    "what is the weather",
    "weather",
    "tell me a joke",
    "write a poem",
    "make a website",
    "generate image",
)

ALLOWED_SCOPE_HINTS = (
    "personal",
    "mine",
    "draft",
    "split",
    "group",
    "friend",
    "friends",
    "with",
    "percent",
    "%",
    "50-50",
    "equally",
    "equal",
    "pays",
    "pay",
    "owes",
    "rest",
    "remaining",
    "shares",
    "exclude me",
    "excluding me",
    "include me",
    "cancel",
    "never mind",
    "same as",
    "last time",
)


def validate_ai_chat_message(message: str) -> GuardrailResult:
    normalized = _normalize(message)
    if not normalized:
        return GuardrailResult(
            allowed=False,
            reason="empty",
            safe_message="",
            user_message=OUT_OF_SCOPE_MESSAGE,
        )
    if len(normalized) > MAX_AI_CHAT_MESSAGE_LENGTH:
        return GuardrailResult(
            allowed=False,
            reason="too_long",
            safe_message=normalized[:MAX_AI_CHAT_MESSAGE_LENGTH],
            user_message=TOO_LONG_MESSAGE,
        )

    lowered = normalized.lower()
    if _contains_any(lowered, PROMPT_INJECTION_PATTERNS):
        return GuardrailResult(
            allowed=False,
            reason="prompt_injection",
            safe_message=normalized,
            user_message=OUT_OF_SCOPE_MESSAGE,
        )
    if _contains_any(lowered, DANGEROUS_ACTION_PATTERNS):
        return GuardrailResult(
            allowed=False,
            reason="dangerous_action",
            safe_message=normalized,
            user_message=OUT_OF_SCOPE_MESSAGE,
        )
    if _contains_any(lowered, OUT_OF_SCOPE_PATTERNS):
        return GuardrailResult(
            allowed=False,
            reason="out_of_scope",
            safe_message=normalized,
            user_message=OUT_OF_SCOPE_MESSAGE,
        )
    if not _looks_like_expense_review(lowered):
        return GuardrailResult(
            allowed=False,
            reason="out_of_scope",
            safe_message=normalized,
            user_message=OUT_OF_SCOPE_MESSAGE,
        )

    return GuardrailResult(
        allowed=True,
        reason=None,
        safe_message=normalized,
        user_message=None,
    )


def _normalize(message: str) -> str:
    return re.sub(r"\s+", " ", message.strip())


def _contains_any(message: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in message for pattern in patterns)


def _looks_like_expense_review(message: str) -> bool:
    return any(hint in message for hint in ALLOWED_SCOPE_HINTS) or _looks_like_short_reply(message)


def _looks_like_short_reply(message: str) -> bool:
    if len(message) > 80:
        return False
    if not re.fullmatch(r"[a-z][a-z .,'-]*(?:\d+(?:\.\d+)?%?)?", message):
        return False
    return len(message.split()) <= 6

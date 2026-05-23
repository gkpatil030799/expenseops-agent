from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

AIAction = Literal["personal", "draft", "split", "clarify", "cancel"]
AITargetType = Literal["people", "group", "unknown"]
AISplitMode = Literal["equal", "exact_amounts", "percentages", "shares", "unknown"]
AIRemaining = Literal["equal_remaining", "none", "unknown"]


@dataclass(frozen=True)
class AICustomValue:
    alias: str
    amount: Decimal | None = None
    percentage: Decimal | None = None
    shares: Decimal | None = None


@dataclass(frozen=True)
class AIChatIntent:
    action: AIAction
    target_type: AITargetType = "unknown"
    group_alias: str | None = None
    participant_aliases: list[str] = field(default_factory=list)
    include_me: bool | None = None
    split_mode: AISplitMode = "unknown"
    custom_values: list[AICustomValue] = field(default_factory=list)
    remaining_split_behavior: AIRemaining = "unknown"
    clarification_question: str | None = None
    confidence: Decimal = Decimal("0")
    explanation: str = ""
    errors: list[str] = field(default_factory=list)


class LLMAIChatParser:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def parse(self, *, user_message: str, ai_context: dict[str, Any]) -> AIChatIntent:
        if not self.settings.openai_api_key:
            return AIChatIntent(
                action="clarify",
                clarification_question="Use button mode or a clearer command.",
                errors=["missing_openai_api_key"],
            )

        payload = {
            "model": self.settings.openai_model,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Parse Telegram expense commands using only aliases in context. "
                                "Never invent aliases. Never post. Return JSON only. "
                                "Use relevant_memories as correction examples when they match "
                                "the current message and available aliases. "
                                "For 50-50 with two participants use percentages. "
                                "For rest/everyone else split equally, set "
                                "remaining_split_behavior=equal_remaining."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(
                                {"message": user_message, "context": ai_context},
                                separators=(",", ":"),
                            ),
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "ai_chat_intent",
                    "schema": _schema(),
                    "strict": True,
                }
            },
        }
        try:
            with httpx.Client(timeout=10) as client:
                response = client.post(
                    "https://api.openai.com/v1/responses",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                )
                response.raise_for_status()
                data = response.json()
                parsed = json.loads(data["output"][0]["content"][0]["text"])
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError):
            logger.info("ai_chat_parse_failed")
            return AIChatIntent(
                action="clarify",
                clarification_question="I could not parse that. Try button mode.",
                errors=["llm_request_failed"],
            )

        logger.info("ai_chat_parse_success")
        return _coerce(parsed)

    @staticmethod
    def _coerce_for_test(payload: dict[str, Any]) -> AIChatIntent:
        return _coerce(payload)


def _coerce(payload: dict[str, Any]) -> AIChatIntent:
    return AIChatIntent(
        action=payload.get("action") or "clarify",
        target_type=payload.get("target_type") or "unknown",
        group_alias=payload.get("group_alias"),
        participant_aliases=[str(alias) for alias in payload.get("participant_aliases", [])],
        include_me=payload.get("include_me"),
        split_mode=payload.get("split_mode") or "unknown",
        custom_values=[
            AICustomValue(
                alias=str(item.get("alias")),
                amount=Decimal(str(item["amount"])) if item.get("amount") is not None else None,
                percentage=(
                    Decimal(str(item["percentage"]))
                    if item.get("percentage") is not None
                    else None
                ),
                shares=Decimal(str(item["shares"])) if item.get("shares") is not None else None,
            )
            for item in payload.get("custom_values", [])
        ],
        remaining_split_behavior=payload.get("remaining_split_behavior") or "unknown",
        clarification_question=payload.get("clarification_question"),
        confidence=Decimal(str(payload.get("confidence", 0))),
        explanation=str(payload.get("explanation") or ""),
        errors=[str(error) for error in payload.get("errors", [])],
    )


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["personal", "draft", "split", "clarify", "cancel"],
            },
            "target_type": {"type": "string", "enum": ["people", "group", "unknown"]},
            "group_alias": {"type": ["string", "null"]},
            "participant_aliases": {"type": "array", "items": {"type": "string"}},
            "include_me": {"type": ["boolean", "null"]},
            "split_mode": {
                "type": "string",
                "enum": ["equal", "exact_amounts", "percentages", "shares", "unknown"],
            },
            "custom_values": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "alias": {"type": "string"},
                        "amount": {"type": ["number", "null"]},
                        "percentage": {"type": ["number", "null"]},
                        "shares": {"type": ["number", "null"]},
                    },
                    "required": ["alias", "amount", "percentage", "shares"],
                },
            },
            "remaining_split_behavior": {
                "type": "string",
                "enum": ["equal_remaining", "none", "unknown"],
            },
            "clarification_question": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
            "explanation": {"type": "string"},
            "errors": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "action",
            "target_type",
            "group_alias",
            "participant_aliases",
            "include_me",
            "split_mode",
            "custom_values",
            "remaining_split_behavior",
            "clarification_question",
            "confidence",
            "explanation",
            "errors",
        ],
    }

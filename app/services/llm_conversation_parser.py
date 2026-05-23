from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

ConversationAction = Literal[
    "personal",
    "draft",
    "split_people",
    "split_group",
    "custom_split",
    "clarify",
    "cancel",
]
ConversationTarget = Literal["people", "group", "none"]
ConversationSplitMode = Literal["equal", "exact_amounts", "percentages", "shares", "unknown"]
ConversationCustomSplitMode = Literal["exact_amounts", "percentages", "shares", "unknown"]
RemainingSplitBehavior = Literal["equal_remaining", "none", "unknown"]


@dataclass(frozen=True)
class LLMConversationIntent:
    action: ConversationAction
    target_type: ConversationTarget = "none"
    group_name: str | None = None
    participant_names: list[str] = field(default_factory=list)
    split_mode: ConversationSplitMode = "unknown"
    custom_split_mode: ConversationCustomSplitMode = "unknown"
    remaining_split_behavior: RemainingSplitBehavior = "unknown"
    payer_included: bool | None = None
    custom_values_text: str | None = None
    clarification_question: str | None = None
    confidence: Decimal = Decimal("0")
    errors: list[str] = field(default_factory=list)


class LLMConversationParser:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def parse(
        self,
        *,
        user_message: str,
        transaction: dict[str, Any],
        pending_state: dict[str, Any],
        available_actions: list[str],
    ) -> LLMConversationIntent:
        if not self.settings.openai_api_key:
            logger.info("LLM conversation parser skipped: OpenAI API key is not configured")
            return LLMConversationIntent(
                action="clarify",
                clarification_question="I need a clearer command, or you can use button mode.",
                errors=["missing_openai_api_key"],
            )

        payload = self._call_openai(
            user_message=user_message,
            transaction=transaction,
            pending_state=pending_state,
            available_actions=available_actions,
        )
        return _coerce_intent(payload)

    def _call_openai(
        self,
        *,
        user_message: str,
        transaction: dict[str, Any],
        pending_state: dict[str, Any],
        available_actions: list[str],
    ) -> dict[str, Any]:
        payload = {
            "model": self.settings.openai_model,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "You parse Telegram messages for an expense approval bot. "
                                "Return only JSON matching the schema. Never decide to post "
                                "an expense. Use the output as a proposal only. For custom "
                                "splits, explicitly set custom_split_mode and "
                                "remaining_split_behavior. If uncertain, return "
                                "action=clarify with a short question."
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
                                {
                                    "message": user_message,
                                    "transaction": transaction,
                                    "current_state": pending_state,
                                    "available_actions": available_actions,
                                },
                                separators=(",", ":"),
                            ),
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "telegram_ai_intent",
                    "schema": _response_schema(),
                    "strict": True,
                }
            },
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=10) as client:
                response = client.post(
                    "https://api.openai.com/v1/responses",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
                parsed = json.loads(data["output"][0]["content"][0]["text"])
        except (KeyError, IndexError, json.JSONDecodeError, httpx.HTTPError):
            logger.warning("LLM conversation parser request failed safely")
            return {
                "action": "clarify",
                "target_type": "none",
                "group_name": None,
                "participant_names": [],
                "split_mode": "unknown",
                "custom_split_mode": "unknown",
                "remaining_split_behavior": "unknown",
                "payer_included": None,
                "custom_values_text": None,
                "clarification_question": (
                    "I could not parse that automatically. Try button mode or a clearer command."
                ),
                "confidence": 0,
                "errors": ["llm_request_failed"],
            }

        logger.info("LLM conversation parser completed")
        return parsed


def _coerce_intent(payload: dict[str, Any]) -> LLMConversationIntent:
    try:
        confidence = Decimal(str(payload.get("confidence", 0)))
    except (InvalidOperation, ValueError):
        confidence = Decimal("0")
    return LLMConversationIntent(
        action=payload.get("action") or "clarify",
        target_type=payload.get("target_type") or "none",
        group_name=payload.get("group_name"),
        participant_names=[str(name) for name in payload.get("participant_names", [])],
        split_mode=payload.get("split_mode") or "unknown",
        custom_split_mode=payload.get("custom_split_mode") or "unknown",
        remaining_split_behavior=payload.get("remaining_split_behavior") or "unknown",
        payer_included=payload.get("payer_included"),
        custom_values_text=payload.get("custom_values_text"),
        clarification_question=payload.get("clarification_question"),
        confidence=confidence,
        errors=[str(error) for error in payload.get("errors", [])],
    )


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "personal",
                    "draft",
                    "split_people",
                    "split_group",
                    "custom_split",
                    "clarify",
                    "cancel",
                ],
            },
            "target_type": {"type": "string", "enum": ["people", "group", "none"]},
            "group_name": {"type": ["string", "null"]},
            "participant_names": {"type": "array", "items": {"type": "string"}},
            "split_mode": {
                "type": "string",
                "enum": ["equal", "exact_amounts", "percentages", "shares", "unknown"],
            },
            "custom_split_mode": {
                "type": "string",
                "enum": ["exact_amounts", "percentages", "shares", "unknown"],
            },
            "remaining_split_behavior": {
                "type": "string",
                "enum": ["equal_remaining", "none", "unknown"],
            },
            "payer_included": {"type": ["boolean", "null"]},
            "custom_values_text": {"type": ["string", "null"]},
            "clarification_question": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
            "errors": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "action",
            "target_type",
            "group_name",
            "participant_names",
            "split_mode",
            "custom_split_mode",
            "remaining_split_behavior",
            "payer_included",
            "custom_values_text",
            "clarification_question",
            "confidence",
            "errors",
        ],
    }

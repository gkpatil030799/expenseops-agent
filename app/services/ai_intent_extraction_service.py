from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from app.config import Settings, get_settings
from app.logging_config import log_event

logger = logging.getLogger(__name__)

ExtractedAction = Literal["personal", "draft", "split", "cancel", "clarify", "unknown"]
ExtractedTargetType = Literal["people", "group", "unknown"]
ExtractedSplitMode = Literal["equal", "exact_amounts", "percentages", "shares", "unknown"]
RemainingSplitBehavior = Literal["equal_remaining", "none", "unknown"]


@dataclass(frozen=True)
class ExtractedAIIntent:
    action: ExtractedAction = "unknown"
    target_type: ExtractedTargetType = "unknown"
    group_mentions: list[str] = field(default_factory=list)
    person_mentions: list[str] = field(default_factory=list)
    split_mode: ExtractedSplitMode = "unknown"
    payer_included: bool | None = None
    remaining_split_behavior: RemainingSplitBehavior = "unknown"
    custom_values_text: str | None = None
    confidence_by_slot: dict[str, float] = field(default_factory=dict)
    clarification_question: str | None = None
    explanation: str = ""
    errors: list[str] = field(default_factory=list)


class AIIntentExtractionService:
    """Extract intent and raw mentions only.

    This service intentionally does not resolve Splitwise user/group IDs. The
    backend entity-resolution layer owns that step.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def extract(
        self,
        *,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> ExtractedAIIntent:
        if not self.settings.openai_api_key:
            log_event(logger, "ai_intent_extraction_failed", reason="missing_openai_api_key")
            return ExtractedAIIntent(
                action="clarify",
                clarification_question="Use Button mode or describe the split more clearly.",
                errors=["missing_openai_api_key"],
            )

        log_event(logger, "ai_intent_extraction_started")

        payload = {
            "model": self.settings.openai_model,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Extract expense-review intent and raw entity mentions.\n\n"
                                "You are only extracting intent and raw mentions. Do not "
                                "resolve people or groups to IDs. Do not invent names.\n\n"
                                "Return JSON only.\n\n"
                                "Use relevant_memories as examples of prior corrected "
                                "interpretations, but only if currently available people and "
                                "groups support them. Memory is guidance only; never imply "
                                "posting or confirmation.\n\n"
                                "Your job is to extract as much structured information as "
                                "possible from the current user message. Do not return "
                                "action=clarify just because split values are compact, "
                                "abbreviated, or need backend validation.\n\n"
                                "Keep person_mentions as the user's intended people, including "
                                "\"me\", and preserve the order they appear in the message.\n\n"
                                "For group splits:\n"
                                "- If the user mentions a group name, put it in group_mentions.\n"
                                "- Words like \"group\", \"in\", \"with\", and \"between\" are "
                                "not part of the group name unless clearly part of the name.\n\n"
                                "For custom splits:\n"
                                "- Always extract split_mode and custom_values_text when the "
                                "user mentions unequal, percentage, percent, %, amount, pays, "
                                "dollars, shares, ratio, or numeric split values.\n"
                                "- Preserve the raw value expression in custom_values_text.\n"
                                "- Do not discard compact values like \"40-60\", \"40/60\", "
                                "\"40 60\", \"70 30\", \"2-1\", or \"$20 $30\".\n"
                                "- If the split type is clear:\n"
                                "  - percent, percentage, percentages, % => "
                                "split_mode=percentages\n"
                                "  - amount, amounts, pays, pay, paid, dollar, dollars, $ => "
                                "split_mode=exact_amounts\n"
                                "  - share, shares, ratio => split_mode=shares\n"
                                "- If values exist but split type is not clear, set "
                                "split_mode=unknown and still preserve custom_values_text.\n"
                                "- Use remaining_split_behavior=equal_remaining when the user "
                                "says rest equally, remaining split equally, everyone else, "
                                "rest shared, or equivalent.\n"
                                "- Use remaining_split_behavior=none when the user gives "
                                "explicit values for all mentioned participants.\n"
                                "- Use remaining_split_behavior=unknown only when unclear.\n\n"
                                "Examples:\n"
                                "1. \"Janhavi 50 percent and rest split equally\"\n"
                                "   => action=split, split_mode=percentages, "
                                "person_mentions=[\"Janhavi\"], "
                                "custom_values_text=\"Janhavi 50 percent\", "
                                "remaining_split_behavior=equal_remaining\n\n"
                                "2. \"Rahul pays 20 and rest equally\"\n"
                                "   => action=split, split_mode=exact_amounts, "
                                "person_mentions=[\"Rahul\"], "
                                "custom_values_text=\"Rahul pays 20\", "
                                "remaining_split_behavior=equal_remaining\n\n"
                                "3. \"Split between me and Janhavi in Sugar Monkeys group "
                                "unequally in percentages 40-60\"\n"
                                "   => action=split, target_type=group, "
                                "group_mentions=[\"Sugar Monkeys\"], "
                                "person_mentions=[\"me\", \"Janhavi\"], "
                                "split_mode=percentages, payer_included=true, "
                                "custom_values_text=\"40-60\", "
                                "remaining_split_behavior=none\n\n"
                                "4. \"split me Rahul 70 30\"\n"
                                "   => action=split, target_type=people, "
                                "person_mentions=[\"me\", \"Rahul\"], split_mode=unknown, "
                                "custom_values_text=\"70 30\", "
                                "remaining_split_behavior=none\n\n"
                                "5. \"Janhavi and Rahul shares 2-1\"\n"
                                "   => action=split, target_type=people, "
                                "person_mentions=[\"Janhavi\", \"Rahul\"], "
                                "split_mode=shares, custom_values_text=\"2-1\", "
                                "remaining_split_behavior=none\n\n"
                                "6. \"split with me, Janhavi, and Yash 50-25-25 percent\"\n"
                                "   => action=split, target_type=people, "
                                "person_mentions=[\"me\", \"Janhavi\", \"Yash\"], "
                                "split_mode=percentages, custom_values_text=\"50-25-25\", "
                                "remaining_split_behavior=none\n\n"
                                "7. \"exclude me, split Janhavi and Rahul equally\"\n"
                                "   => action=split, target_type=people, "
                                "person_mentions=[\"Janhavi\", \"Rahul\"], "
                                "split_mode=equal, payer_included=false\n\n"
                                "If the user asks to mark personal, draft, cancel, or undo, "
                                "extract that action directly.\n\n"
                                "Only use action=clarify when the message has no usable "
                                "expense action or no recoverable split information."
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
                                    "context": context or {},
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
                    "name": "ai_intent_extraction",
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
        except httpx.HTTPStatusError as exc:
            log_event(
                logger,
                "ai_intent_extraction_failed",
                reason="llm_request_failed",
                status_code=exc.response.status_code,
            )
            return ExtractedAIIntent(
                action="clarify",
                clarification_question="I could not understand that split. Try Button mode.",
                errors=["llm_request_failed", f"http_status_{exc.response.status_code}"],
            )
        except httpx.HTTPError:
            log_event(
                logger,
                "ai_intent_extraction_failed",
                reason="llm_request_failed",
                error_type="http_error",
            )
            return ExtractedAIIntent(
                action="clarify",
                clarification_question="I could not understand that split. Try Button mode.",
                errors=["llm_request_failed", "http_error"],
            )
        except (KeyError, IndexError, json.JSONDecodeError, TypeError):
            log_event(logger, "ai_intent_extraction_failed", reason="llm_response_parse_failed")
            return ExtractedAIIntent(
                action="clarify",
                clarification_question="I could not understand that split. Try Button mode.",
                errors=["llm_request_failed", "llm_response_parse_failed"],
            )

        log_event(logger, "ai_intent_extraction_success")
        return _coerce(parsed)

    @staticmethod
    def _coerce_for_test(payload: dict[str, Any]) -> ExtractedAIIntent:
        return _coerce(payload)


def _coerce(payload: dict[str, Any]) -> ExtractedAIIntent:
    return ExtractedAIIntent(
        action=_enum_value(
            payload.get("action"),
            {"personal", "draft", "split", "cancel", "clarify", "unknown"},
            "unknown",
        ),
        target_type=_enum_value(
            payload.get("target_type"),
            {"people", "group", "unknown"},
            "unknown",
        ),
        group_mentions=[
            str(item).strip() for item in payload.get("group_mentions", []) if str(item).strip()
        ],
        person_mentions=[
            str(item).strip() for item in payload.get("person_mentions", []) if str(item).strip()
        ],
        split_mode=_enum_value(
            payload.get("split_mode"),
            {"equal", "exact_amounts", "percentages", "shares", "unknown"},
            "unknown",
        ),
        payer_included=payload.get("payer_included"),
        remaining_split_behavior=_enum_value(
            payload.get("remaining_split_behavior"),
            {"equal_remaining", "none", "unknown"},
            "unknown",
        ),
        custom_values_text=payload.get("custom_values_text"),
        confidence_by_slot={
            str(key): float(value)
            for key, value in dict(payload.get("confidence_by_slot") or {}).items()
        },
        clarification_question=payload.get("clarification_question"),
        explanation=str(payload.get("explanation") or ""),
        errors=[str(error) for error in payload.get("errors", [])],
    )


def _enum_value(value: Any, allowed: set[str], default: str) -> Any:
    value = str(value or default)
    return value if value in allowed else default


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["personal", "draft", "split", "cancel", "clarify", "unknown"],
            },
            "target_type": {"type": "string", "enum": ["people", "group", "unknown"]},
            "group_mentions": {"type": "array", "items": {"type": "string"}},
            "person_mentions": {"type": "array", "items": {"type": "string"}},
            "split_mode": {
                "type": "string",
                "enum": ["equal", "exact_amounts", "percentages", "shares", "unknown"],
            },
            "payer_included": {"type": ["boolean", "null"]},
            "remaining_split_behavior": {
                "type": "string",
                "enum": ["equal_remaining", "none", "unknown"],
            },
            "custom_values_text": {"type": ["string", "null"]},
            "confidence_by_slot": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {"type": "number"},
                    "target_type": {"type": "number"},
                    "group": {"type": "number"},
                    "participants": {"type": "number"},
                    "split_mode": {"type": "number"},
                    "custom_values": {"type": "number"},
                },
                "required": [
                    "action",
                    "target_type",
                    "group",
                    "participants",
                    "split_mode",
                    "custom_values",
                ],
            },
            "clarification_question": {"type": ["string", "null"]},
            "explanation": {"type": "string"},
            "errors": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "action",
            "target_type",
            "group_mentions",
            "person_mentions",
            "split_mode",
            "payer_included",
            "remaining_split_behavior",
            "custom_values_text",
            "confidence_by_slot",
            "clarification_question",
            "explanation",
            "errors",
        ],
    }

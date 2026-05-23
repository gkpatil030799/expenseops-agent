from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

LLMSplitMode = Literal["exact_amounts", "percentages", "shares"]


@dataclass(frozen=True)
class LLMSplitParticipant:
    user_id: int
    display_name: str
    amount_cents: int | None = None
    percentage: Decimal | None = None
    shares: Decimal | None = None


@dataclass(frozen=True)
class LLMSplitParseResult:
    ok: bool
    parser_confidence: Decimal = Decimal("0")
    normalized_intent: str = ""
    participant_splits: list[LLMSplitParticipant] = field(default_factory=list)
    clarification_question: str | None = None
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _AliasParticipant:
    alias: str
    user_id: int
    display_name: str


class LLMSplitParser:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def parse(
        self,
        *,
        user_message: str,
        total_amount_cents: int,
        currency_code: str,
        split_mode: LLMSplitMode,
        payer_included: bool,
        selected_participants: list[dict],
        payer: dict | None,
    ) -> LLMSplitParseResult:
        if not self.settings.openai_api_key:
            logger.info("LLM split parser skipped: OpenAI API key is not configured")
            return LLMSplitParseResult(
                ok=False,
                clarification_question=(
                    "I could not parse that automatically. Try the structured format."
                ),
                errors=["missing_openai_api_key"],
            )

        alias_participants = self._alias_participants(
            selected_participants=selected_participants,
            payer=payer if payer_included else None,
        )
        payload = self._call_openai(
            user_message=user_message,
            total_amount_cents=total_amount_cents,
            currency_code=currency_code,
            split_mode=split_mode,
            payer_included=payer_included,
            alias_participants=alias_participants,
        )
        return self._coerce_and_validate_llm_result(
            payload=payload,
            split_mode=split_mode,
            payer_included=payer_included,
            alias_participants=alias_participants,
        )

    def _alias_participants(
        self,
        *,
        selected_participants: list[dict],
        payer: dict | None,
    ) -> list[_AliasParticipant]:
        aliases: list[_AliasParticipant] = []
        seen_user_ids: set[int] = set()
        for index, participant in enumerate(selected_participants, start=1):
            user_id = int(participant["user_id"])
            if user_id in seen_user_ids:
                continue
            seen_user_ids.add(user_id)
            aliases.append(
                _AliasParticipant(
                    alias=f"p{index}",
                    user_id=user_id,
                    display_name=str(participant.get("display_name") or f"p{index}"),
                )
            )

        if payer:
            payer_user_id = int(payer["user_id"])
            if payer_user_id not in seen_user_ids:
                aliases.append(
                    _AliasParticipant(
                        alias="me",
                        user_id=payer_user_id,
                        display_name=str(payer.get("display_name") or "You"),
                    )
                )
        return aliases

    def _call_openai(
        self,
        *,
        user_message: str,
        total_amount_cents: int,
        currency_code: str,
        split_mode: LLMSplitMode,
        payer_included: bool,
        alias_participants: list[_AliasParticipant],
    ) -> dict[str, Any]:
        participants_for_prompt = [
            {"alias": item.alias, "display_name": item.display_name}
            for item in alias_participants
        ]
        payload = {
            "model": self.settings.openai_model,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Parse a custom expense split. Return only JSON matching "
                                "the schema. Use participant aliases only. Never invent "
                                "people. If the user references a person not listed, ask "
                                "for clarification. Do not decide to post anything."
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
                                    "transaction": {
                                        "total_amount_cents": total_amount_cents,
                                        "currency_code": currency_code,
                                    },
                                    "split_mode": split_mode,
                                    "payer_included": payer_included,
                                    "participants": participants_for_prompt,
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
                    "name": "custom_split_parse",
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
            logger.warning("LLM split parser request failed safely")
            return {
                "ok": False,
                "clarification_question": (
                    "I could not parse that automatically. Try the structured format."
                ),
                "errors": ["llm_request_failed"],
            }

        logger.info("LLM split parser completed")
        return parsed

    def _coerce_and_validate_llm_result(
        self,
        *,
        payload: dict[str, Any],
        split_mode: LLMSplitMode,
        payer_included: bool,
        alias_participants: list[_AliasParticipant],
    ) -> LLMSplitParseResult:
        alias_by_name = {participant.alias: participant for participant in alias_participants}
        if not payload.get("ok"):
            return LLMSplitParseResult(
                ok=False,
                clarification_question=payload.get("clarification_question"),
                errors=[str(error) for error in payload.get("errors", [])],
            )

        participant_splits: list[LLMSplitParticipant] = []
        for raw_split in payload.get("participant_splits", []):
            alias = str(raw_split.get("alias") or "")
            if alias == "me" and not payer_included:
                return LLMSplitParseResult(
                    ok=False,
                    clarification_question="Should I include you in this split?",
                    errors=["payer_not_included"],
                )
            participant = alias_by_name.get(alias)
            if participant is None:
                return LLMSplitParseResult(
                    ok=False,
                    clarification_question=(
                        "That mentions someone who is not selected. Select them first or try again."
                    ),
                    errors=["unknown_participant_alias"],
                )

            participant_splits.append(
                LLMSplitParticipant(
                    user_id=participant.user_id,
                    display_name=participant.display_name,
                    amount_cents=_optional_int(raw_split.get("amount_cents")),
                    percentage=_optional_decimal(raw_split.get("percentage")),
                    shares=_optional_decimal(raw_split.get("shares")),
                )
            )

        if not participant_splits:
            return LLMSplitParseResult(
                ok=False,
                clarification_question="Who should be included in this split?",
                errors=["empty_participant_splits"],
            )

        return LLMSplitParseResult(
            ok=True,
            parser_confidence=_optional_decimal(payload.get("parser_confidence"))
            or Decimal("0"),
            normalized_intent=str(payload.get("normalized_intent") or ""),
            participant_splits=participant_splits,
            clarification_question=payload.get("clarification_question"),
            errors=[str(error) for error in payload.get("errors", [])],
        )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "ok": {"type": "boolean"},
            "parser_confidence": {"type": "number"},
            "normalized_intent": {"type": "string"},
            "participant_splits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "alias": {"type": "string"},
                        "display_name": {"type": "string"},
                        "amount_cents": {"type": ["integer", "null"]},
                        "percentage": {"type": ["number", "null"]},
                        "shares": {"type": ["number", "null"]},
                    },
                    "required": [
                        "alias",
                        "display_name",
                        "amount_cents",
                        "percentage",
                        "shares",
                    ],
                },
            },
            "clarification_question": {"type": ["string", "null"]},
            "errors": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "ok",
            "parser_confidence",
            "normalized_intent",
            "participant_splits",
            "clarification_question",
            "errors",
        ],
    }

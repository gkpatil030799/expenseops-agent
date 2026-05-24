from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import AIInterpretationMemory, ExpenseTransaction
from app.services.agent_service import transaction_display_name
from app.services.telegram_state_service import PendingTelegramSplit

MAX_ORIGINAL_MESSAGE_LENGTH = 500


def _tokens(text: str | None) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(token) > 2
    }


def _cap_message(message: str) -> str:
    return message.strip()[:MAX_ORIGINAL_MESSAGE_LENGTH]


def _participant_names(participants: list[dict] | None) -> list[str]:
    return [
        str(participant.get("display_name") or "").strip()
        for participant in (participants or [])
        if str(participant.get("display_name") or "").strip()
    ]


def _public_custom_values(values: list[dict] | None) -> list[dict[str, Any]] | None:
    if not values:
        return None
    public_values = []
    for value in values:
        public_values.append(
            {
                "display_name": value.get("display_name"),
                "amount_cents": value.get("amount_cents"),
                "percentage": value.get("percentage"),
                "shares": value.get("shares"),
            }
        )
    return public_values


class AIInterpretationMemoryService:
    def __init__(self, db: Session):
        self.db = db

    def list_memories(self, limit: int = 20) -> list[AIInterpretationMemory]:
        stmt = (
            select(AIInterpretationMemory)
            .order_by(desc(AIInterpretationMemory.created_at))
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars())

    def list_public_memories(self, limit: int = 20) -> list[dict[str, Any]]:
        return [self.to_public_memory(memory) for memory in self.list_memories(limit=limit)]

    def to_public_memory(self, memory: AIInterpretationMemory) -> dict[str, Any]:
        return {
            "id": memory.id,
            "original_message": _cap_message(memory.original_message),
            "failure_reason": memory.failure_reason,
            "final_action": memory.final_action,
            "final_group_name": memory.final_group_name,
            "final_participants": _participant_names(memory.final_participants),
            "final_split_mode": memory.final_split_mode,
            "payer_included": memory.payer_included,
            "custom_values": _public_custom_values(memory.custom_values),
            "correction_type": memory.correction_type,
            "merchant": memory.merchant,
            "amount_cents": memory.amount_cents,
            "currency": memory.currency,
            "usage_count": memory.usage_count,
            "last_used_at": memory.last_used_at,
            "created_at": memory.created_at,
        }

    def delete_memory(self, memory_id: int) -> bool:
        memory = self.db.get(AIInterpretationMemory, memory_id)
        if not memory:
            return False
        self.db.delete(memory)
        self.db.commit()
        return True

    def record_button_fallback_memory(
        self,
        *,
        tx: ExpenseTransaction,
        pending: PendingTelegramSplit | None,
        final_action: str,
        final_group_id: int | None = None,
        final_group_name: str | None = None,
        final_participants: list[dict[str, Any]] | None = None,
        final_split_mode: str | None = None,
        payer_included: bool = True,
        custom_values: list[dict[str, Any]] | None = None,
    ) -> AIInterpretationMemory | None:
        if not pending or not pending.button_fallback_active or not pending.failed_ai_message:
            return None

        memory = AIInterpretationMemory(
            original_message=_cap_message(pending.failed_ai_message),
            failure_reason=pending.failed_ai_reason or "parse_failed",
            final_action=final_action,
            final_group_id=str(final_group_id) if final_group_id else None,
            final_group_name=final_group_name,
            final_participants=final_participants or [],
            final_split_mode=final_split_mode,
            payer_included=payer_included,
            custom_values=custom_values,
            correction_type="button_fallback_learned",
            merchant=transaction_display_name(tx),
            amount_cents=abs(tx.amount_cents),
            currency=tx.iso_currency_code or "USD",
            usage_count=0,
        )
        self.db.add(memory)
        self.db.commit()
        self.db.refresh(memory)
        return memory

    def record_ai_interpretation_memory(
        self,
        *,
        tx: ExpenseTransaction,
        pending: PendingTelegramSplit | None,
        final_action: str,
        final_group_id: int | None = None,
        final_group_name: str | None = None,
        final_participants: list[dict[str, Any]] | None = None,
        final_split_mode: str | None = None,
        payer_included: bool = True,
        custom_values: list[dict[str, Any]] | None = None,
        correction_type: str = "ai_confirmed",
    ) -> AIInterpretationMemory | None:
        if not pending or pending.button_fallback_active:
            return None
        if not pending.ai_slots or pending.ai_memory_recorded:
            return None

        original_message = (
            pending.ai_slots.get("original_user_message")
            or pending.last_ai_message
            or pending.failed_ai_message
        )
        if not original_message:
            return None

        memory = AIInterpretationMemory(
            original_message=_cap_message(str(original_message)),
            failure_reason=pending.failed_ai_reason or "none",
            final_action=final_action,
            final_group_id=str(final_group_id) if final_group_id else None,
            final_group_name=final_group_name,
            final_participants=final_participants or [],
            final_split_mode=final_split_mode,
            payer_included=payer_included,
            custom_values=custom_values,
            correction_type=correction_type,
            merchant=transaction_display_name(tx),
            amount_cents=abs(tx.amount_cents),
            currency=tx.iso_currency_code or "USD",
            usage_count=0,
        )
        self.db.add(memory)
        self.db.commit()
        self.db.refresh(memory)
        pending.ai_memory_recorded = True
        return memory

    def relevant_memories(
        self,
        *,
        merchant: str | None,
        message: str,
        limit: int = 3,
    ) -> list[AIInterpretationMemory]:
        stmt = (
            select(AIInterpretationMemory)
            .order_by(desc(AIInterpretationMemory.created_at))
            .limit(100)
        )
        memories = list(self.db.execute(stmt).scalars())
        message_tokens = _tokens(message)
        merchant_tokens = _tokens(merchant)

        scored: list[tuple[int, AIInterpretationMemory]] = []
        for memory in memories:
            score = 0
            memory_tokens = _tokens(memory.original_message)
            if merchant and memory.merchant and memory.merchant.lower() == merchant.lower():
                score += 8
            score += len(message_tokens & memory_tokens) * 3
            score += len(merchant_tokens & _tokens(memory.merchant)) * 2
            score += len(message_tokens & _tokens(memory.final_group_name)) * 3
            participant_words = _tokens(" ".join(_participant_names(memory.final_participants)))
            score += len(message_tokens & participant_words) * 2
            if memory.last_used_at:
                score += 2
            if memory.usage_count:
                score += min(int(memory.usage_count), 3)
            if score > 0:
                scored.append((score, memory))

        relevant = [
            memory
            for _score, memory in sorted(
                scored,
                key=lambda item: (item[0], item[1].created_at),
                reverse=True,
            )[:limit]
        ]
        for memory in relevant:
            memory.usage_count = (memory.usage_count or 0) + 1
            memory.last_used_at = datetime.now(UTC)
        if relevant:
            self.db.commit()
        return relevant


def memory_prompt_context(memory: AIInterpretationMemory) -> dict[str, Any]:
    return {
        "original_phrase": _cap_message(memory.original_message),
        "correct_interpretation": memory.final_action,
        "group": memory.final_group_name,
        "participants": _participant_names(memory.final_participants),
        "split_mode": memory.final_split_mode,
        "payer_included": memory.payer_included,
        "outcome": memory.correction_type,
    }

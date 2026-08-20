from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.models import AIInterpretationMemory, AuditEvent, DataConsent, ExpenseTransaction, utc_now
from app.services.agent_service import transaction_display_name
from app.services.telegram_state_service import PendingTelegramSplit

MAX_ORIGINAL_MESSAGE_LENGTH = 500
STRUCTURED_LEARNING_PURPOSE = "structured_transaction_learning"


def _tokens(text: str | None) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(token) > 2}


def _cap_message(message: str) -> str:
    return message.strip()[:MAX_ORIGINAL_MESSAGE_LENGTH]


def _structured_label(merchant: str | None, final_action: str) -> str:
    subject = (merchant or "This merchant").strip()[:255] or "This merchant"
    preference = "usually personal" if final_action == "personal" else "usually shared"
    return f"{subject}: {preference}"


def _memory_source(memory: AIInterpretationMemory) -> str:
    if memory.correction_type == "explicit_preference":
        return "explicit_preference"
    if memory.correction_type in {"button_fallback_learned", "user_edited"}:
        return "correction"
    return "confirmed_action"


def _memory_rationale(memory: AIInterpretationMemory) -> str:
    source = _memory_source(memory)
    source_text = {
        "explicit_preference": "You explicitly saved this preference.",
        "correction": "You corrected a prior transaction interpretation.",
        "confirmed_action": "This comes from a confirmed transaction action.",
    }[source]
    details = []
    if memory.final_group_name:
        details.append(f"group {memory.final_group_name}")
    participants = _participant_names(memory.final_participants)
    if participants:
        details.append("with " + ", ".join(participants))
    return source_text + (" Suggested " + " · ".join(details) + "." if details else "")


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
        _, user_id = self._scope()
        stmt = (
            select(AIInterpretationMemory)
            .where(
                or_(
                    AIInterpretationMemory.owner_user_id == user_id,
                    AIInterpretationMemory.owner_user_id.is_(None),
                )
            )
            .order_by(desc(AIInterpretationMemory.created_at))
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars())

    def list_public_memories(self, limit: int = 20) -> list[dict[str, Any]]:
        return [self.to_public_memory(memory) for memory in self.list_memories(limit=limit)]

    def to_public_memory(self, memory: AIInterpretationMemory) -> dict[str, Any]:
        label = _structured_label(memory.merchant, memory.final_action)
        return {
            "id": memory.id,
            # Retained for wire compatibility; new and migrated rows contain
            # this code-owned label, never a chat transcript.
            "original_message": label,
            "label": label,
            "rationale": _memory_rationale(memory),
            "source": _memory_source(memory),
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

    def learning_enabled(self) -> bool:
        workspace_id, user_id = self._scope()
        value = self.db.scalar(
            select(DataConsent.granted).where(
                DataConsent.workspace_id == workspace_id,
                DataConsent.user_id == user_id,
                DataConsent.purpose == STRUCTURED_LEARNING_PURPOSE,
            )
        )
        return True if value is None else bool(value)

    def set_learning_enabled(self, *, enabled: bool) -> bool:
        from app.services.data_lifecycle_service import DataLifecycleService

        workspace_id, user_id = self._scope()
        DataLifecycleService(self.db).set_consent(
            workspace_id=workspace_id,
            user_id=user_id,
            purpose=STRUCTURED_LEARNING_PURPOSE,
            granted=enabled,
        )
        self.db.commit()
        return enabled

    def create_explicit_preference(
        self,
        *,
        merchant: str,
        preference: str,
        participant_names: list[str],
        group_name: str | None,
    ) -> AIInterpretationMemory:
        self._validate_preference(
            preference=preference,
            participant_names=participant_names,
            group_name=group_name,
        )
        normalized_merchant = " ".join(merchant.split())[:255]
        if not normalized_merchant:
            raise ValueError("Merchant is required.")
        final_action = "personal" if preference == "personal" else "split_equal"
        _, user_id = self._scope()
        memory = self.db.scalar(
            select(AIInterpretationMemory)
            .where(
                AIInterpretationMemory.owner_user_id == user_id,
                func.lower(AIInterpretationMemory.merchant) == normalized_merchant.casefold(),
            )
            .order_by(desc(AIInterpretationMemory.created_at))
            .limit(1)
        )
        if memory is None:
            memory = AIInterpretationMemory(
                original_message=_structured_label(normalized_merchant, final_action),
                failure_reason="none",
                final_action=final_action,
                final_participants=[],
                payer_included=True,
                correction_type="explicit_preference",
                merchant=normalized_merchant,
                usage_count=0,
                owner_user_id=user_id,
            )
            self.db.add(memory)
        self._apply_preference(
            memory,
            final_action=final_action,
            participant_names=participant_names,
            group_name=group_name,
            correction_type="explicit_preference",
        )
        self.db.commit()
        self.db.refresh(memory)
        self._event(memory, "structured_preference_created")
        self.db.commit()
        return memory

    def update_preference(
        self,
        memory_id: int,
        *,
        preference: str,
        participant_names: list[str],
        group_name: str | None,
    ) -> AIInterpretationMemory | None:
        self._validate_preference(
            preference=preference,
            participant_names=participant_names,
            group_name=group_name,
        )
        memory = self._owned_memory(memory_id)
        if memory is None:
            return None
        self._apply_preference(
            memory,
            final_action="personal" if preference == "personal" else "split_equal",
            participant_names=participant_names,
            group_name=group_name,
            correction_type="user_edited",
        )
        self._event(memory, "structured_recommendation_edited")
        self.db.commit()
        self.db.refresh(memory)
        return memory

    def record_feedback(self, memory_id: int, *, outcome: str) -> bool:
        memory = self._owned_memory(memory_id)
        if memory is None:
            return False
        self._event(memory, f"structured_recommendation_{outcome}")
        self.db.commit()
        return True

    def metrics(self) -> dict[str, int | float | None]:
        workspace_id, user_id = self._scope()
        rows = self.db.execute(
            select(AuditEvent.event_type, func.count(AuditEvent.id))
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.user_id == user_id,
                AuditEvent.event_type.in_(
                    (
                        "structured_recommendation_shown",
                        "structured_recommendation_accepted",
                        "structured_recommendation_edited",
                        "structured_recommendation_rejected",
                    )
                ),
            )
            .group_by(AuditEvent.event_type)
        ).all()
        counts = {
            name.removeprefix("structured_recommendation_"): int(count) for name, count in rows
        }
        shown = counts.get("shown", 0)
        accepted = counts.get("accepted", 0)
        edited = counts.get("edited", 0)
        rejected = counts.get("rejected", 0)
        decided = accepted + edited + rejected
        return {
            "shown": shown,
            "accepted": accepted,
            "edited": edited,
            "rejected": rejected,
            "agreement_rate": accepted / decided if decided else None,
            "correction_rate": (edited + rejected) / decided if decided else None,
        }

    def recommendation_for_transaction(
        self,
        tx: ExpenseTransaction,
    ) -> dict[str, Any] | None:
        if not self.learning_enabled():
            return None
        merchant = transaction_display_name(tx).strip()
        if not merchant:
            return None
        _, user_id = self._scope()
        memory = self.db.scalar(
            select(AIInterpretationMemory)
            .where(
                AIInterpretationMemory.owner_user_id == user_id,
                func.lower(AIInterpretationMemory.merchant) == merchant.casefold(),
            )
            .order_by(desc(AIInterpretationMemory.created_at))
            .limit(1)
        )
        if memory is None or memory.final_action not in {"personal", "split_equal", "custom_split"}:
            return None
        shown_resource_id = f"{memory.id}:{tx.id}"
        shown_exists = self.db.scalar(
            select(AuditEvent.id).where(
                AuditEvent.workspace_id == tx.workspace_id,
                AuditEvent.user_id == user_id,
                AuditEvent.event_type == "structured_recommendation_shown",
                AuditEvent.resource_id == shown_resource_id,
            )
        )
        if shown_exists is None:
            memory.usage_count = (memory.usage_count or 0) + 1
            memory.last_used_at = utc_now()
            self._event(
                memory,
                "structured_recommendation_shown",
                transaction_id=tx.id,
                resource_id=shown_resource_id,
            )
            self.db.commit()
        suggestion = "likely_personal" if memory.final_action == "personal" else "likely_shared"
        return {
            "suggestion": suggestion,
            "reason": _memory_rationale(memory),
            "memory_id": memory.id,
            "participant_names": _participant_names(memory.final_participants)[:8],
            "group_name": memory.final_group_name,
            "split_mode": memory.final_split_mode,
        }

    def delete_memory(self, memory_id: int) -> bool:
        memory = self._owned_memory(memory_id)
        if not memory:
            return False
        self.db.delete(memory)
        self.db.commit()
        return True

    def _scope(self) -> tuple[int, int]:
        workspace_id = self.db.info.get("workspace_id")
        user_id = self.db.info.get("user_id")
        if not isinstance(workspace_id, int) or not isinstance(user_id, int):
            raise ValueError("Structured memory requires an authenticated tenant scope.")
        return workspace_id, user_id

    def _owned_memory(self, memory_id: int) -> AIInterpretationMemory | None:
        _, user_id = self._scope()
        return self.db.scalar(
            select(AIInterpretationMemory).where(
                AIInterpretationMemory.id == memory_id,
                AIInterpretationMemory.owner_user_id == user_id,
            )
        )

    @staticmethod
    def _validate_preference(
        *,
        preference: str,
        participant_names: list[str],
        group_name: str | None,
    ) -> None:
        if preference == "personal" and (participant_names or group_name):
            raise ValueError("Personal preferences cannot include split participants or a group.")
        if preference == "shared" and not participant_names and not group_name:
            raise ValueError("Shared preferences require a participant or Splitwise group.")

    @staticmethod
    def _apply_preference(
        memory: AIInterpretationMemory,
        *,
        final_action: str,
        participant_names: list[str],
        group_name: str | None,
        correction_type: str,
    ) -> None:
        memory.final_action = final_action
        memory.final_split_mode = "equal" if final_action != "personal" else None
        memory.final_participants = [
            {"display_name": name.strip()} for name in participant_names if name.strip()
        ]
        memory.final_group_name = group_name.strip() if group_name else None
        memory.final_group_id = None
        memory.correction_type = correction_type
        memory.original_message = _structured_label(memory.merchant, final_action)

    def _event(
        self,
        memory: AIInterpretationMemory,
        event_type: str,
        *,
        transaction_id: int | None = None,
        resource_id: str | None = None,
    ) -> None:
        workspace_id, user_id = self._scope()
        self.db.add(
            AuditEvent(
                workspace_id=workspace_id,
                user_id=user_id,
                event_type=event_type,
                resource_type="structured_preference",
                resource_id=resource_id or (str(memory.id) if memory.id else None),
                metadata_json={
                    "domain": "transactions",
                    "transaction_id": transaction_id,
                    "source": _memory_source(memory),
                },
            )
        )

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

        if not self.learning_enabled():
            return None
        merchant = transaction_display_name(tx)
        _, user_id = self._scope()
        memory = AIInterpretationMemory(
            original_message=_structured_label(merchant, final_action),
            failure_reason=pending.failed_ai_reason or "parse_failed",
            final_action=final_action,
            final_group_id=str(final_group_id) if final_group_id else None,
            final_group_name=final_group_name,
            final_participants=final_participants or [],
            final_split_mode=final_split_mode,
            payer_included=payer_included,
            custom_values=custom_values,
            correction_type="button_fallback_learned",
            merchant=merchant,
            amount_cents=abs(tx.amount_cents),
            currency=tx.iso_currency_code or "USD",
            usage_count=0,
            owner_user_id=user_id,
        )
        self.db.add(memory)
        self.db.commit()
        self.db.refresh(memory)
        return memory

    def record_agent_typed_split_memory(
        self,
        *,
        tx: ExpenseTransaction,
        final_action: str,
        final_group_id: int | None = None,
        final_group_name: str | None = None,
        final_participants: list[dict[str, Any]] | None = None,
        final_split_mode: str | None = None,
        payer_included: bool = True,
    ) -> AIInterpretationMemory | None:
        """Channel-agnostic counterpart to record_ai_interpretation_memory.

        The Agent review session has no PendingTelegramSplit state, so this
        writes the same structured-memory shape directly from an
        already-resolved Agent action proposal instead of gating on Telegram
        session fields.
        """
        if not self.learning_enabled():
            return None
        merchant = transaction_display_name(tx)
        _, user_id = self._scope()
        memory = AIInterpretationMemory(
            original_message=_structured_label(merchant, final_action),
            failure_reason="none",
            final_action=final_action,
            final_group_id=str(final_group_id) if final_group_id else None,
            final_group_name=final_group_name,
            final_participants=final_participants or [],
            final_split_mode=final_split_mode,
            payer_included=payer_included,
            custom_values=None,
            correction_type="agent_review_typed",
            merchant=merchant,
            amount_cents=abs(tx.amount_cents),
            currency=tx.iso_currency_code or "USD",
            usage_count=0,
            owner_user_id=user_id,
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

        if not self.learning_enabled():
            return None
        merchant = transaction_display_name(tx)
        _, user_id = self._scope()
        memory = AIInterpretationMemory(
            original_message=_structured_label(merchant, final_action),
            failure_reason=pending.failed_ai_reason or "none",
            final_action=final_action,
            final_group_id=str(final_group_id) if final_group_id else None,
            final_group_name=final_group_name,
            final_participants=final_participants or [],
            final_split_mode=final_split_mode,
            payer_included=payer_included,
            custom_values=custom_values,
            correction_type=correction_type,
            merchant=merchant,
            amount_cents=abs(tx.amount_cents),
            currency=tx.iso_currency_code or "USD",
            usage_count=0,
            owner_user_id=user_id,
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
        if not self.learning_enabled():
            return []
        _, user_id = self._scope()
        stmt = (
            select(AIInterpretationMemory)
            .where(
                or_(
                    AIInterpretationMemory.owner_user_id == user_id,
                    AIInterpretationMemory.owner_user_id.is_(None),
                )
            )
            .order_by(desc(AIInterpretationMemory.created_at))
            .limit(100)
        )
        memories = list(self.db.execute(stmt).scalars())
        message_tokens = _tokens(message)
        merchant_tokens = _tokens(merchant)

        scored: list[tuple[int, AIInterpretationMemory]] = []
        for memory in memories:
            score = 0
            if merchant and memory.merchant and memory.merchant.lower() == merchant.lower():
                score += 8
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
        # No chat transcript is retained or returned to the model.
        "original_phrase": "",
        "correct_interpretation": memory.final_action,
        "group": memory.final_group_name,
        "participants": _participant_names(memory.final_participants),
        "split_mode": memory.final_split_mode,
        "payer_included": memory.payer_included,
        "outcome": memory.correction_type,
        "rationale": _memory_rationale(memory),
    }

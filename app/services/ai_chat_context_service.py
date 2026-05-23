from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models import ExpenseTransaction
from app.services.agent_service import friend_display_name
from app.services.ai_memory_service import AIInterpretationMemoryService, memory_prompt_context
from app.services.splitwise_service import SplitwiseService
from app.services.telegram_state_service import PendingTelegramSplit


@dataclass
class AIChatContext:
    prompt_context: dict[str, Any]
    payer_by_alias: dict[str, dict] = field(default_factory=dict)
    friend_by_alias: dict[str, dict] = field(default_factory=dict)
    group_by_alias: dict[str, dict] = field(default_factory=dict)
    member_by_alias: dict[str, dict] = field(default_factory=dict)
    member_aliases_by_group_alias: dict[str, set[str]] = field(default_factory=dict)


class AIChatContextService:
    def __init__(self, splitwise: SplitwiseService | None = None):
        self.splitwise = splitwise or SplitwiseService()

    def build(
        self,
        tx: ExpenseTransaction,
        pending: PendingTelegramSplit,
        *,
        db=None,
        user_message: str | None = None,
    ) -> AIChatContext:
        payer = self.splitwise.get_current_user()
        friends = self.splitwise.get_friends()
        groups = self.splitwise.get_groups()

        payer_by_alias = {"me": payer}
        friend_by_alias: dict[str, dict] = {}
        group_by_alias: dict[str, dict] = {}
        member_by_alias: dict[str, dict] = {}
        member_aliases_by_group_alias: dict[str, set[str]] = {}

        safe_friends = []
        for index, friend in enumerate(friends[:25], start=1):
            alias = f"f{index}"
            friend_by_alias[alias] = friend
            safe_friend = {
                "alias": alias,
                "display_name": friend_display_name(friend),
            }
            if friend.get("email"):
                safe_friend["email"] = friend["email"]
            safe_friends.append(safe_friend)

        safe_groups = []
        for group_index, group in enumerate(groups[:12], start=1):
            group_alias = f"g{group_index}"
            group_by_alias[group_alias] = group
            member_aliases_by_group_alias[group_alias] = set()
            safe_members = []
            for member_index, member in enumerate(group.get("members", [])[:30], start=1):
                member_alias = f"{group_alias}m{member_index}"
                member_by_alias[member_alias] = member
                member_aliases_by_group_alias[group_alias].add(member_alias)
                safe_members.append(
                    {
                        "alias": member_alias,
                        "display_name": friend_display_name(member),
                    }
                )
            safe_groups.append(
                {
                    "alias": group_alias,
                    "name": group.get("name"),
                    "members": safe_members,
                }
            )

        memories = []
        if db is not None and user_message:
            memories = [
                memory_prompt_context(memory)
                for memory in AIInterpretationMemoryService(db).relevant_memories(
                    merchant=tx.merchant_name or tx.name,
                    message=user_message,
                )
            ]

        prompt_context = {
            "transaction": {
                "merchant": tx.merchant_name or tx.name,
                "amount_cents": abs(tx.amount_cents),
                "currency": tx.iso_currency_code or "USD",
                "date": str(tx.date) if getattr(tx, "date", None) else None,
            },
            "payer": {
                "alias": "me",
                "display_name": friend_display_name(payer),
            },
            "friends": safe_friends,
            "groups": safe_groups,
            "pending_state": {
                "selected_group": pending.selected_group_name,
                "selected_participants": list(pending.selected_friend_names_by_id.values()),
                "payer_included": pending.custom_payer_included,
                "ai_waiting_for": pending.ai_waiting_for,
            },
            "relevant_memories": memories,
        }

        return AIChatContext(
            prompt_context=prompt_context,
            payer_by_alias=payer_by_alias,
            friend_by_alias=friend_by_alias,
            group_by_alias=group_by_alias,
            member_by_alias=member_by_alias,
            member_aliases_by_group_alias=member_aliases_by_group_alias,
        )

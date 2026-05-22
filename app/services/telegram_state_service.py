from __future__ import annotations

from dataclasses import dataclass, field

from app.services.agent_service import friend_display_name


@dataclass
class PendingTelegramSplit:
    transaction_id: int
    selected_friend_ids: list[int] = field(default_factory=list)
    remaining_unresolved_names: list[str] = field(default_factory=list)
    ambiguous_matches_by_name: dict[str, list[dict]] = field(default_factory=dict)

    def add_friend_id(self, friend_id: int) -> None:
        if friend_id not in self.selected_friend_ids:
            self.selected_friend_ids.append(friend_id)

    def next_ambiguous_name(self) -> str | None:
        return self.remaining_unresolved_names[0] if self.remaining_unresolved_names else None


class TelegramSplitStateStore:
    def __init__(self):
        self._states: dict[str, PendingTelegramSplit] = {}

    def key(self, chat_id: str, user_id: str) -> str:
        return f"{chat_id}:{user_id}"

    def set_pending(self, chat_id: str, user_id: str, transaction_id: int) -> PendingTelegramSplit:
        state = PendingTelegramSplit(transaction_id=transaction_id)
        self._states[self.key(chat_id, user_id)] = state
        return state

    def get_pending(self, chat_id: str, user_id: str) -> PendingTelegramSplit | None:
        return self._states.get(self.key(chat_id, user_id))

    def clear(self, chat_id: str, user_id: str) -> None:
        self._states.pop(self.key(chat_id, user_id), None)


telegram_split_state_store = TelegramSplitStateStore()


def parse_split_names(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def find_friend_matches(name: str, friends: list[dict]) -> list[dict]:
    needle = name.strip().lower()
    if not needle:
        return []
    matches = []
    for friend in friends:
        haystack = f"{friend_display_name(friend)} {friend.get('email') or ''}".lower()
        if needle in haystack:
            matches.append(friend)
    return matches

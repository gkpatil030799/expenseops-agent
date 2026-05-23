from __future__ import annotations

from dataclasses import dataclass, field

from app.services.agent_service import friend_display_name


@dataclass
class PendingTelegramSplit:
    transaction_id: int
    mode: str = "people"
    transaction_title: str | None = None
    is_submitting: bool = False
    selected_group_id: int | None = None
    selected_group_name: str | None = None
    payer_user_id: int | None = None
    group_members: list[dict] = field(default_factory=list)
    friend_options: list[dict] = field(default_factory=list)
    group_options: list[dict] = field(default_factory=list)
    selected_friend_ids: list[int] = field(default_factory=list)
    selected_friend_names_by_id: dict[int, str] = field(default_factory=dict)
    friend_lookup_by_id: dict[int, str] = field(default_factory=dict)
    remaining_unresolved_names: list[str] = field(default_factory=list)
    ambiguous_matches_by_name: dict[str, list[dict]] = field(default_factory=dict)
    ambiguous_groups_by_name: dict[str, list[dict]] = field(default_factory=dict)
    ai_group_name: str | None = None
    ai_participant_names: list[str] = field(default_factory=list)
    ai_intent_action: str | None = None

    def add_friend(self, friend_id: int, display_name: str) -> None:
        if friend_id not in self.selected_friend_ids:
            self.selected_friend_ids.append(friend_id)
        self.friend_lookup_by_id[friend_id] = display_name
        self.selected_friend_names_by_id[friend_id] = display_name

    def toggle_friend(self, friend_id: int, display_name: str) -> bool:
        if friend_id in self.selected_friend_ids:
            self.selected_friend_ids.remove(friend_id)
            self.selected_friend_names_by_id.pop(friend_id, None)
            return False
        self.selected_friend_ids.append(friend_id)
        self.friend_lookup_by_id[friend_id] = display_name
        self.selected_friend_names_by_id[friend_id] = display_name
        return True

    def remember_friends(self, friends: list[dict]) -> None:
        for friend in friends:
            self.friend_lookup_by_id[int(friend["id"])] = friend_display_name(friend)

    def next_ambiguous_name(self) -> str | None:
        return self.remaining_unresolved_names[0] if self.remaining_unresolved_names else None


class TelegramSplitStateStore:
    def __init__(self):
        self._states: dict[str, PendingTelegramSplit] = {}

    def key(self, chat_id: str, user_id: str) -> str:
        return f"{chat_id}:{user_id}"

    def set_pending(
        self,
        chat_id: str,
        user_id: str,
        transaction_id: int,
        mode: str = "people",
    ) -> PendingTelegramSplit:
        state = PendingTelegramSplit(transaction_id=transaction_id, mode=mode)
        self._states[self.key(chat_id, user_id)] = state
        return state

    def get_pending(self, chat_id: str, user_id: str) -> PendingTelegramSplit | None:
        return self._states.get(self.key(chat_id, user_id))

    def clear(self, chat_id: str, user_id: str) -> None:
        self._states.pop(self.key(chat_id, user_id), None)


telegram_split_state_store = TelegramSplitStateStore()


@dataclass
class TelegramReviewQueue:
    initial_total: int = 0
    completed_titles: list[str] = field(default_factory=list)

    def ensure_total(self, remaining_count: int) -> None:
        total = len(self.completed_titles) + remaining_count
        if total > self.initial_total:
            self.initial_total = total

    def mark_completed(self, title: str) -> None:
        if title not in self.completed_titles:
            self.completed_titles.append(title)


class TelegramReviewQueueStore:
    def __init__(self):
        self._states: dict[str, TelegramReviewQueue] = {}

    def key(self, chat_id: str, user_id: str) -> str:
        return f"{chat_id}:{user_id}"

    def get(self, chat_id: str, user_id: str) -> TelegramReviewQueue:
        key = self.key(chat_id, user_id)
        if key not in self._states:
            self._states[key] = TelegramReviewQueue()
        return self._states[key]

    def clear(self, chat_id: str, user_id: str) -> None:
        self._states.pop(self.key(chat_id, user_id), None)


telegram_review_queue_store = TelegramReviewQueueStore()


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


def find_group_matches(name: str, groups: list[dict]) -> list[dict]:
    needle = name.strip().lower()
    if not needle:
        return []
    return [group for group in groups if needle in str(group.get("name") or "").lower()]

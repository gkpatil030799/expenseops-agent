from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import TelegramSession, utc_now
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
    ai_target_type: str | None = None
    ai_split_mode: str = "equal"
    ai_custom_values_text: str | None = None
    ai_waiting_for: str | None = None
    ai_slots: dict = field(default_factory=dict)
    ai_correction_type: str | None = None
    ai_memory_recorded: bool = False
    last_ai_message: str | None = None
    failed_ai_message: str | None = None
    failed_ai_reason: str | None = None
    failed_ai_created_at: datetime | None = None
    button_fallback_active: bool = False
    custom_split_mode: str | None = None
    custom_payer_included: bool = True
    custom_participant_splits: list[dict] = field(default_factory=list)
    split_target_mode: str | None = None
    split_value_mode: str = "equal"

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

    def remember_failed_ai_attempt(self, original_message: str, failure_reason: str) -> None:
        self.failed_ai_message = original_message.strip()[:500]
        self.failed_ai_reason = failure_reason
        self.failed_ai_created_at = datetime.now(UTC)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["failed_ai_created_at"] = (
            self.failed_ai_created_at.isoformat() if self.failed_ai_created_at else None
        )
        data["selected_friend_names_by_id"] = {
            str(key): value for key, value in self.selected_friend_names_by_id.items()
        }
        data["friend_lookup_by_id"] = {
            str(key): value for key, value in self.friend_lookup_by_id.items()
        }
        return _json_safe(data)

    @classmethod
    def from_dict(cls, data: dict) -> PendingTelegramSplit:
        payload = dict(data)
        created_at = payload.get("failed_ai_created_at")
        if isinstance(created_at, str) and created_at:
            payload["failed_ai_created_at"] = datetime.fromisoformat(created_at)
        else:
            payload["failed_ai_created_at"] = None

        payload["selected_friend_names_by_id"] = {
            int(key): value
            for key, value in dict(payload.get("selected_friend_names_by_id") or {}).items()
        }
        payload["friend_lookup_by_id"] = {
            int(key): value for key, value in dict(payload.get("friend_lookup_by_id") or {}).items()
        }

        known_fields = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in payload.items() if key in known_fields})


_current_db: ContextVar[Session | None] = ContextVar("telegram_session_db", default=None)
_current_db_available: ContextVar[bool] = ContextVar(
    "telegram_session_db_available",
    default=True,
)
_current_touched_keys: ContextVar[set[str] | None] = ContextVar(
    "telegram_session_touched_keys",
    default=None,
)


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


class TelegramSplitStateStore:
    def __init__(self):
        self._states: dict[str, PendingTelegramSplit] = {}

    @contextmanager
    def use_db(self, db: Session) -> Iterator[None]:
        token = _current_db.set(db)
        available_token = _current_db_available.set(True)
        touched_token = _current_touched_keys.set(set())
        try:
            yield
        finally:
            self.flush_current_db()
            _current_db.reset(token)
            _current_db_available.reset(available_token)
            _current_touched_keys.reset(touched_token)

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
        key = self.key(chat_id, user_id)
        self._states[key] = state
        self._mark_touched(key)
        self._save_db_state(chat_id, user_id, state)
        return state

    def update_pending(
        self,
        chat_id: str,
        user_id: str,
        state: PendingTelegramSplit,
    ) -> None:
        key = self.key(chat_id, user_id)
        self._states[key] = state
        self._mark_touched(key)
        self._save_db_state(chat_id, user_id, state)

    def get_pending(self, chat_id: str, user_id: str) -> PendingTelegramSplit | None:
        key = self.key(chat_id, user_id)
        self._mark_touched(key)
        db_state = self._load_db_state(chat_id, user_id)
        if db_state is not None:
            existing_state = self._states.get(key)
            if existing_state is not None:
                existing_state.__dict__.update(db_state.__dict__)
                return existing_state
            self._states[key] = db_state
            return db_state
        return self._states.get(key)

    def clear(self, chat_id: str, user_id: str) -> None:
        key = self.key(chat_id, user_id)
        self._states.pop(key, None)
        self._mark_touched(key)
        db = _current_db.get()
        if db is None:
            return
        session = self._get_db_session(db, chat_id, user_id)
        if session:
            db.delete(session)
            db.commit()

    def flush_current_db(self) -> None:
        db = _current_db.get()
        if db is None or not _current_db_available.get():
            return
        touched_keys = _current_touched_keys.get()
        keys_to_flush = touched_keys if touched_keys is not None else set(self._states)
        for key in list(keys_to_flush):
            state = self._states.get(key)
            if state is None:
                continue
            chat_id, separator, user_id = key.partition(":")
            if separator:
                self._save_db_state(chat_id, user_id, state, commit=False)
        if _current_db_available.get():
            db.commit()

    def _load_db_state(self, chat_id: str, user_id: str) -> PendingTelegramSplit | None:
        db = _current_db.get()
        if db is None or not _current_db_available.get():
            return None
        session = self._get_db_session(db, chat_id, user_id)
        if not session:
            return None
        try:
            return PendingTelegramSplit.from_dict(session.state_data)
        except (TypeError, ValueError):
            return None

    def _save_db_state(
        self,
        chat_id: str,
        user_id: str,
        state: PendingTelegramSplit,
        *,
        commit: bool = True,
    ) -> None:
        db = _current_db.get()
        if db is None or not _current_db_available.get():
            return
        session = self._get_db_session(db, chat_id, user_id)
        if not _current_db_available.get():
            return
        state_data = state.to_dict()
        if session is None:
            session = TelegramSession(chat_id=chat_id, user_id=user_id, state_data=state_data)
            db.add(session)
        else:
            session.state_data = state_data
            session.updated_at = utc_now()
        if commit:
            db.commit()

    def _mark_touched(self, key: str) -> None:
        touched_keys = _current_touched_keys.get()
        if touched_keys is not None:
            touched_keys.add(key)

    def _get_db_session(
        self,
        db: Session,
        chat_id: str,
        user_id: str,
    ) -> TelegramSession | None:
        try:
            return db.execute(
                select(TelegramSession).where(
                    TelegramSession.chat_id == chat_id,
                    TelegramSession.user_id == user_id,
                )
            ).scalar_one_or_none()
        except OperationalError:
            db.rollback()
            _current_db_available.set(False)
            return None


telegram_split_state_store = TelegramSplitStateStore()


TelegramSessionStore = TelegramSplitStateStore


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

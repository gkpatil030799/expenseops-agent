from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import TelegramSession
from app.services.telegram_state_service import PendingTelegramSplit, TelegramSessionStore


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_state_persists_through_store_roundtrip():
    db = _db()
    store = TelegramSessionStore()

    with store.use_db(db):
        state = store.set_pending("chat-1", "user-1", 123, mode="ai_chat")
        state.transaction_title = "$12.00 Dinner"
        state.add_friend(7, "Janhavi")
        state.remember_failed_ai_attempt("split like last time", "low_confidence")

    fresh_store = TelegramSessionStore()
    with fresh_store.use_db(db):
        restored = fresh_store.get_pending("chat-1", "user-1")

    assert restored is not None
    assert restored.transaction_id == 123
    assert restored.mode == "ai_chat"
    assert restored.transaction_title == "$12.00 Dinner"
    assert restored.selected_friend_ids == [7]
    assert restored.selected_friend_names_by_id == {7: "Janhavi"}
    assert restored.failed_ai_message == "split like last time"
    assert restored.failed_ai_reason == "low_confidence"


def test_clear_removes_state():
    db = _db()
    store = TelegramSessionStore()

    with store.use_db(db):
        store.set_pending("chat-1", "user-1", 123)
        store.clear("chat-1", "user-1")

    assert db.query(TelegramSession).count() == 0


def test_missing_state_returns_none():
    db = _db()
    store = TelegramSessionStore()

    with store.use_db(db):
        assert store.get_pending("missing-chat", "missing-user") is None


def test_in_memory_fallback_still_works_without_db_context():
    store = TelegramSessionStore()

    state = store.set_pending("chat-1", "user-1", 123)

    assert store.get_pending("chat-1", "user-1") is state


def test_early_return_mutation_persists_when_context_exits():
    db = _db()
    store = TelegramSessionStore()

    with store.use_db(db):
        state = store.set_pending("chat-1", "user-1", 123)
        state.mode = "people_search"
        state.friend_options = [{"id": 7, "first_name": "Janhavi"}]

    fresh_store = TelegramSessionStore()
    with fresh_store.use_db(db):
        restored = fresh_store.get_pending("chat-1", "user-1")

    assert restored is not None
    assert restored.mode == "people_search"
    assert restored.friend_options == [{"id": 7, "first_name": "Janhavi"}]


def test_callback_style_mutation_persists_after_request_context():
    db = _db()
    store = TelegramSessionStore()

    with store.use_db(db):
        store.set_pending("chat-1", "user-1", 123)
        state = store.get_pending("chat-1", "user-1")
        assert state is not None
        state.mode = "group_members"
        state.selected_group_id = 44
        state.selected_group_name = "Sugar Monkeys"

    fresh_store = TelegramSessionStore()
    with fresh_store.use_db(db):
        restored = fresh_store.get_pending("chat-1", "user-1")

    assert restored is not None
    assert restored.mode == "group_members"
    assert restored.selected_group_id == 44
    assert restored.selected_group_name == "Sugar Monkeys"


def test_pending_split_serialization_roundtrip():
    pending = PendingTelegramSplit(transaction_id=123, mode="ai_chat")
    pending.add_friend(7, "Janhavi")
    pending.remember_failed_ai_attempt("x", "low_confidence")
    pending.ai_slots = {
        "action": "split",
        "target_type": "people",
        "group_mentions": [],
        "participant_mentions": ["me", "janahvi", "yash"],
        "resolved_group_id": None,
        "resolved_group_name": None,
        "resolved_participants": [{"user_id": 1, "display_name": "Gunjan Patil"}],
        "unresolved_participants": ["janahvi"],
        "ambiguous_participants": [],
        "split_mode": "equal",
        "payer_included": True,
        "ai_waiting_for": "participants",
        "original_user_message": "split between me janahvi and yash equally",
        "last_ai_explanation": "Equal split with raw mentions.",
        "confidence_by_slot": {"participants": 0.8},
    }

    restored = PendingTelegramSplit.from_dict(pending.to_dict())

    assert restored.transaction_id == 123
    assert restored.selected_friend_names_by_id == {7: "Janhavi"}
    assert restored.friend_lookup_by_id == {7: "Janhavi"}
    assert restored.failed_ai_created_at is not None
    assert restored.ai_slots["participant_mentions"] == ["me", "janahvi", "yash"]
    assert restored.ai_slots["ai_waiting_for"] == "participants"

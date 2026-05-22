from app.services.telegram_state_service import (
    TelegramSplitStateStore,
    find_friend_matches,
    parse_split_names,
)


def test_starting_pending_split_state():
    store = TelegramSplitStateStore()
    state = store.set_pending("chat-1", "user-1", 123)

    assert state.transaction_id == 123
    assert store.get_pending("chat-1", "user-1") is state


def test_parse_one_friend_name():
    assert parse_split_names("Rahul") == ["Rahul"]


def test_parse_multiple_comma_separated_friend_names():
    assert parse_split_names("Rahul, Akash") == ["Rahul", "Akash"]


def test_no_match():
    assert find_friend_matches("Rahul", [{"id": 1, "first_name": "Akash"}]) == []


def test_multiple_match_needs_disambiguation():
    friends = [
        {"id": 1, "first_name": "Rahul", "last_name": "A"},
        {"id": 2, "first_name": "Rahul", "last_name": "B"},
    ]

    assert len(find_friend_matches("Rahul", friends)) == 2

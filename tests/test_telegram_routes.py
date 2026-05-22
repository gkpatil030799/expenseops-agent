from fastapi.testclient import TestClient

from app.api import telegram_routes
from app.config import Settings
from app.main import app


def test_telegram_webhook_allows_request_when_no_secret_configured(monkeypatch):
    monkeypatch.setattr(
        telegram_routes,
        "get_settings",
        lambda: Settings(telegram_webhook_secret=""),
    )

    response = TestClient(app).post("/telegram/webhook", json={})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_telegram_webhook_allows_correct_secret(monkeypatch):
    monkeypatch.setattr(
        telegram_routes,
        "get_settings",
        lambda: Settings(telegram_webhook_secret="expected-secret"),
    )

    response = TestClient(app).post("/telegram/webhook?secret=expected-secret", json={})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_telegram_webhook_rejects_missing_or_incorrect_secret(monkeypatch):
    monkeypatch.setattr(
        telegram_routes,
        "get_settings",
        lambda: Settings(telegram_webhook_secret="expected-secret"),
    )

    missing = TestClient(app).post("/telegram/webhook", json={})
    incorrect = TestClient(app).post("/telegram/webhook?secret=wrong-secret", json={})

    assert missing.status_code == 403
    assert incorrect.status_code == 403


def test_telegram_callback_personal_routes_to_transaction_service(monkeypatch):
    calls = {}
    answers = []

    class FakeTransactionService:
        def __init__(self, db):
            calls["db"] = db

        def mark_personal(self, transaction_id):
            calls["transaction_id"] = transaction_id

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

    monkeypatch.setattr(telegram_routes, "TransactionService", FakeTransactionService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)

    response = TestClient(app).post(
        "/telegram/webhook",
        json={"callback_query": {"id": "callback-1", "data": "review:personal:123"}},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert calls["transaction_id"] == 123
    assert answers == [("callback-1", "Marked as personal.")]


def test_telegram_callback_draft_requires_dashboard_selection(monkeypatch):
    answers = []

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)

    response = TestClient(app).post(
        "/telegram/webhook",
        json={"callback_query": {"id": "callback-2", "data": "review:draft:123"}},
    )

    assert response.status_code == 200
    assert answers == [("callback-2", "Select friends in the dashboard before creating a draft.")]


def test_telegram_callback_split_equal_requires_dashboard_selection(monkeypatch):
    answers = []

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)

    response = TestClient(app).post(
        "/telegram/webhook",
        json={"callback_query": {"id": "callback-3", "data": "review:split_equal:123"}},
    )

    assert response.status_code == 200
    assert answers == [("callback-3", "Select friends in the dashboard before splitting equally.")]


def test_telegram_callback_split_with_people_starts_pending_state(monkeypatch):
    messages = []
    answers = []

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.clear("chat-1", "user-1")

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "callback-4",
                "data": "review:split_people:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    pending = telegram_routes.telegram_split_state_store.get_pending("chat-1", "user-1")
    assert response.status_code == 200
    assert pending is not None
    assert pending.transaction_id == 123
    assert answers == [("callback-4", "Send friend names in this chat.")]
    assert messages == [
        (
            "chat-1",
            "Send Splitwise friend names separated by commas. Example: Rahul, Akash",
            None,
        )
    ]


def test_telegram_text_successful_equal_split_path(monkeypatch):
    calls = {}
    messages = []

    class FakeSplitwiseService:
        def get_friends(self):
            return [{"id": 7, "first_name": "Rahul", "last_name": "Shah"}]

    class FakeTransactionService:
        def __init__(self, db):
            pass

        def get_transaction(self, transaction_id):
            return type("Tx", (), {"splitwise_expense_id": None})()

        def create_equal_split_expense(self, **kwargs):
            calls.update(kwargs)

    class FakeTelegramService:
        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "SplitwiseService", FakeSplitwiseService)
    monkeypatch.setattr(telegram_routes, "TransactionService", FakeTransactionService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.set_pending("chat-1", "user-1", 123)

    response = TestClient(app).post(
        "/telegram/webhook",
        json={"message": {"chat": {"id": "chat-1"}, "from": {"id": "user-1"}, "text": "Rahul"}},
    )

    assert response.status_code == 200
    assert calls["tx_id"] == 123
    assert calls["friend_user_ids"] == [7]
    assert calls["confirm"] is True
    assert messages == [("chat-1", "Split posted to Splitwise.", None)]


def test_telegram_text_multiple_match_sends_disambiguation(monkeypatch):
    messages = []

    class FakeSplitwiseService:
        def get_friends(self):
            return [
                {"id": 7, "first_name": "Rahul", "last_name": "Shah"},
                {"id": 8, "first_name": "Rahul", "last_name": "Patel"},
            ]

    class FakeTelegramService:
        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "SplitwiseService", FakeSplitwiseService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.set_pending("chat-1", "user-1", 123)

    response = TestClient(app).post(
        "/telegram/webhook",
        json={"message": {"chat": {"id": "chat-1"}, "from": {"id": "user-1"}, "text": "Rahul"}},
    )

    assert response.status_code == 200
    assert messages[0][1] == "Multiple matches for 'Rahul'. Choose one:"
    assert messages[0][2]["inline_keyboard"][0][0]["callback_data"] == "friend:123:7"


def test_telegram_text_no_match_asks_user_to_try_again(monkeypatch):
    messages = []

    class FakeSplitwiseService:
        def get_friends(self):
            return [{"id": 9, "first_name": "Akash", "last_name": "Rao"}]

    class FakeTelegramService:
        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "SplitwiseService", FakeSplitwiseService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.set_pending("chat-1", "user-1", 123)

    response = TestClient(app).post(
        "/telegram/webhook",
        json={"message": {"chat": {"id": "chat-1"}, "from": {"id": "user-1"}, "text": "Rahul"}},
    )

    assert response.status_code == 200
    assert messages == [("chat-1", "No Splitwise friend matched 'Rahul'. Try again.", None)]


def test_telegram_ambiguous_name_preserves_resolved_friend_and_finishes_after_choice(
    monkeypatch,
):
    calls = {}
    messages = []

    class FakeSplitwiseService:
        def get_friends(self):
            return [
                {"id": 7, "first_name": "Rahul", "last_name": "Shah"},
                {"id": 8, "first_name": "Rahul", "last_name": "Patel"},
                {"id": 9, "first_name": "Akash", "last_name": "Rao"},
            ]

    class FakeTransactionService:
        def __init__(self, db):
            pass

        def get_transaction(self, transaction_id):
            return type("Tx", (), {"splitwise_expense_id": None})()

        def create_equal_split_expense(self, **kwargs):
            calls.update(kwargs)

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            messages.append(("answer", text, None))

        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "SplitwiseService", FakeSplitwiseService)
    monkeypatch.setattr(telegram_routes, "TransactionService", FakeTransactionService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.set_pending("chat-1", "user-1", 123)

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "message": {
                "chat": {"id": "chat-1"},
                "from": {"id": "user-1"},
                "text": "Rahul, Akash",
            }
        },
    )

    pending = telegram_routes.telegram_split_state_store.get_pending("chat-1", "user-1")
    assert response.status_code == 200
    assert pending is not None
    assert pending.selected_friend_ids == [9]
    assert messages[0][1] == "Multiple matches for 'Rahul'. Choose one:"

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "callback-5",
                "data": "friend:123:7",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    assert response.status_code == 200
    assert calls["friend_user_ids"] == [9, 7]
    assert calls["confirm"] is True
    assert ("chat-1", "Split posted to Splitwise.", None) in messages

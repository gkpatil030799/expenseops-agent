import pytest
from fastapi.testclient import TestClient

from app.api import telegram_routes
from app.config import Settings
from app.main import app


@pytest.fixture(autouse=True)
def allow_telegram_webhook_without_secret(monkeypatch):
    monkeypatch.setattr(
        telegram_routes,
        "get_settings",
        lambda: Settings(telegram_webhook_secret=""),
    )
    telegram_routes.telegram_split_state_store._states.clear()
    telegram_routes.telegram_review_queue_store._states.clear()


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
            return type(
                "Tx",
                (),
                {
                    "id": transaction_id,
                    "amount_cents": 633,
                    "iso_currency_code": "USD",
                    "merchant_name": "Uber",
                    "name": "Uber",
                },
            )()

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

        def send_message(self, message, reply_markup=None, chat_id=None):
            pass

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


def test_telegram_personal_success_includes_undo(monkeypatch):
    messages = []

    class FakeTransactionService:
        def __init__(self, db):
            pass

        def mark_personal(self, transaction_id):
            return type(
                "Tx",
                (),
                {
                    "id": transaction_id,
                    "amount_cents": 633,
                    "iso_currency_code": "USD",
                    "merchant_name": "Uber",
                    "name": "Uber",
                },
            )()

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            pass

        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "TransactionService", FakeTransactionService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "personal-undo",
                "data": "review:personal:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    assert response.status_code == 200
    assert messages[0][2]["inline_keyboard"][0][0]["callback_data"] == "review:undo:123"


def test_telegram_callback_draft_marks_transaction_done(monkeypatch):
    answers = []
    calls = {}

    class FakeTransactionService:
        def __init__(self, db):
            pass

        def mark_shared_draft(self, transaction_id):
            calls["transaction_id"] = transaction_id
            return type(
                "Tx",
                (),
                {
                    "id": transaction_id,
                    "amount_cents": 1200,
                    "iso_currency_code": "USD",
                    "merchant_name": "Costco",
                    "name": "Costco",
                },
            )()

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

    monkeypatch.setattr(telegram_routes, "TransactionService", FakeTransactionService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)

    response = TestClient(app).post(
        "/telegram/webhook",
        json={"callback_query": {"id": "callback-2", "data": "review:draft:123"}},
    )

    assert response.status_code == 200
    assert calls["transaction_id"] == 123
    assert answers == [("callback-2", "Draft saved.")]


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


def test_telegram_callback_button_mode_opens_existing_actions(monkeypatch):
    messages = []
    answers = []

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "button-mode",
                "data": "review:button_mode:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    assert response.status_code == 200
    assert answers == [("button-mode", "Button mode selected.")]
    assert "What do you want to do" in messages[0][1]
    assert messages[0][2]["inline_keyboard"][0][0]["callback_data"] == "review:personal:123"


def test_telegram_callback_ai_chat_starts_pending_state(monkeypatch):
    messages = []
    answers = []

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "ai-mode",
                "data": "review:ai_chat:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    pending = telegram_routes.telegram_split_state_store.get_pending("chat-1", "user-1")
    assert response.status_code == 200
    assert pending is not None
    assert pending.mode == "ai_chat"
    assert answers == [("ai-mode", "AI chat mode selected.")]
    assert "Tell me what to do" in messages[0][1]


def test_telegram_review_queue_sends_next_transaction_with_progress(monkeypatch):
    messages = []
    tx = type(
        "Tx",
        (),
        {
            "id": 456,
            "amount_cents": 3260,
            "iso_currency_code": "USD",
            "merchant_name": "Fry's Food and Drug",
            "name": "Fry's Food and Drug",
        },
    )()

    class FakeTelegramService:
        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    queue = telegram_routes.telegram_review_queue_store.get("chat-1", "user-1")
    queue.mark_completed("$6.33 Uber")
    monkeypatch.setattr(telegram_routes, "_pending_review_transactions", lambda db: [tx])

    telegram_routes._send_next_pending_transaction(
        "chat-1",
        "user-1",
        None,
        FakeTelegramService(),
    )

    assert "Transaction 2 of 2" in messages[0][1]
    assert "Done: 1 / 2" in messages[0][1]
    assert "$32.60 Fry's Food and Drug" in messages[0][1]


def test_telegram_review_queue_sends_final_completion_summary(monkeypatch):
    messages = []

    class FakeTelegramService:
        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    queue = telegram_routes.telegram_review_queue_store.get("chat-1", "user-1")
    queue.mark_completed("$6.33 Uber")
    queue.mark_completed("$32.60 Fry's Food and Drug")
    monkeypatch.setattr(telegram_routes, "_pending_review_transactions", lambda db: [])

    telegram_routes._send_next_pending_transaction(
        "chat-1",
        "user-1",
        None,
        FakeTelegramService(),
    )

    assert "All caught up" in messages[0][1]
    assert "✅ $6.33 Uber" in messages[0][1]
    assert "✅ $32.60 Fry's Food and Drug" in messages[0][1]
    assert telegram_routes.telegram_review_queue_store._states == {}


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
    assert pending.mode == "split_mode"
    assert pending.split_target_mode == "people"
    assert answers == [("callback-4", "Choose split mode.")]
    assert "How should the split work" in messages[0][1]
    assert messages[0][2]["inline_keyboard"][0][0]["callback_data"] == "review:split_mode_equal:123"


def test_telegram_callback_split_in_group_starts_group_state(monkeypatch):
    messages = []
    answers = []

    class FakeSplitwiseService:
        def get_groups(self):
            return [{"id": 44, "name": "Apartment group", "members": []}]

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "SplitwiseService", FakeSplitwiseService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.clear("chat-1", "user-1")

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "callback-group",
                "data": "review:split_group:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    pending = telegram_routes.telegram_split_state_store.get_pending("chat-1", "user-1")
    assert response.status_code == 200
    assert pending is not None
    assert pending.transaction_id == 123
    assert pending.mode == "split_mode"
    assert pending.split_target_mode == "group"
    assert answers == [("callback-group", "Choose split mode.")]
    assert "How should the split work" in messages[0][1]


def test_telegram_missing_pending_for_split_mode_returns_expired_message(monkeypatch):
    answers = []

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.clear("chat-1", "user-1")

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "expired-mode",
                "data": "review:split_mode_equal:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    assert response.status_code == 200
    assert answers == [
        ("expired-mode", "This split session expired. Please start again from the transaction.")
    ]


def test_telegram_callback_cancel_clears_pending_state(monkeypatch):
    messages = []
    answers = []

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.set_pending("chat-1", "user-1", 123)

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "callback-cancel",
                "data": "review:cancel:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    assert response.status_code == 200
    assert telegram_routes.telegram_split_state_store.get_pending("chat-1", "user-1") is None
    assert answers == [("callback-cancel", "Split flow cancelled.")]
    assert messages == [("chat-1", "✅ Split flow cancelled.", None)]


def test_telegram_selecting_and_deselecting_friend_updates_state(monkeypatch):
    messages = []
    answers = []

    class FakeSplitwiseService:
        def get_friends(self):
            return [{"id": 7, "first_name": "Rahul", "last_name": "Shah"}]

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "SplitwiseService", FakeSplitwiseService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.clear("chat-1", "user-1")

    TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "start",
                "data": "review:split_people:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )
    TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "mode",
                "data": "review:split_mode_equal:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )
    TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "select",
                "data": "friend:123:7",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )
    pending = telegram_routes.telegram_split_state_store.get_pending("chat-1", "user-1")
    assert pending.selected_friend_ids == [7]
    assert "✅ Rahul Shah" in str(messages[-1][2])

    TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "deselect",
                "data": "friend:123:7",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    assert pending.selected_friend_ids == []
    assert answers[-1] == ("deselect", "Selection updated.")


def test_telegram_done_with_no_participants_asks_for_selection(monkeypatch):
    answers = []

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.set_pending(
        "chat-1",
        "user-1",
        123,
        mode="people_select",
    )

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "done-empty",
                "data": "review:done:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    assert response.status_code == 200
    assert answers == [("done-empty", "Select at least one person before tapping Done.")]


def test_telegram_done_while_submitting_does_not_post_twice(monkeypatch):
    answers = []
    pending = telegram_routes.telegram_split_state_store.set_pending(
        "chat-1",
        "user-1",
        123,
        mode="people_select",
    )
    pending.is_submitting = True

    class FakeTransactionService:
        def __init__(self, db):
            raise AssertionError("Done should not post while already submitting")

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

    monkeypatch.setattr(telegram_routes, "TransactionService", FakeTransactionService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "done-twice",
                "data": "review:done:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    assert response.status_code == 200
    assert answers == [("done-twice", "Split already being processed.")]


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
            tx = type(
                "Tx",
                (),
                {
                    "id": kwargs["tx_id"],
                    "splitwise_expense_id": "expense-1",
                    "amount_cents": 1200,
                    "iso_currency_code": "USD",
                    "merchant_name": "Costco",
                    "name": "Costco",
                },
            )()
            return tx, {"expenses": [{"id": "expense-1"}]}

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
    assert calls == {}
    assert "Confirm split" in messages[0][1]
    assert "Rahul Shah" in messages[0][1]
    assert messages[0][2]["inline_keyboard"][0][0]["callback_data"] == "review:confirm:123"


def test_telegram_search_selection_uses_lookup_name(monkeypatch):
    messages = []

    class FakeSplitwiseService:
        def get_friends(self):
            return [{"id": 7, "first_name": "Rahul", "last_name": "Shah"}]

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            pass

        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "SplitwiseService", FakeSplitwiseService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.set_pending(
        "chat-1",
        "user-1",
        123,
        mode="people_search",
    )

    search_response = TestClient(app).post(
        "/telegram/webhook",
        json={"message": {"chat": {"id": "chat-1"}, "from": {"id": "user-1"}, "text": "Rahul"}},
    )
    select_response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "select-search",
                "data": "friend:123:7",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    pending = telegram_routes.telegram_split_state_store.get_pending("chat-1", "user-1")
    assert search_response.status_code == 200
    assert select_response.status_code == 200
    assert pending.selected_friend_names_by_id[7] == "Rahul Shah"
    assert "✅ Rahul Shah" in str(messages[-1][2])


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
    assert "Multiple matches found" in messages[0][1]
    assert "Rahul" in messages[0][1]
    assert messages[0][2]["inline_keyboard"][0][0]["callback_data"] == "friend:123:7"
    assert messages[0][2]["inline_keyboard"][-1][0]["callback_data"] == "review:cancel:123"


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


def test_ai_chat_personal_marks_transaction_personal(monkeypatch):
    calls = {}

    class FakeTransactionService:
        def __init__(self, db):
            pass

        def mark_personal(self, transaction_id):
            calls["transaction_id"] = transaction_id
            return type(
                "Tx",
                (),
                {
                    "id": transaction_id,
                    "amount_cents": 633,
                    "iso_currency_code": "USD",
                    "merchant_name": "Uber",
                    "name": "Uber",
                },
            )()

    class FakeTelegramService:
        def send_message(self, message, reply_markup=None, chat_id=None):
            pass

    monkeypatch.setattr(telegram_routes, "TransactionService", FakeTransactionService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.set_pending(
        "chat-1", "user-1", 123, mode="ai_chat"
    )

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "message": {
                "chat": {"id": "chat-1"},
                "from": {"id": "user-1"},
                "text": "mark personal",
            }
        },
    )

    assert response.status_code == 200
    assert calls["transaction_id"] == 123


def test_ai_chat_split_only_posts_after_confirm(monkeypatch):
    calls = {}
    messages = []

    class FakeSplitwiseService:
        def get_friends(self):
            return [{"id": 7, "first_name": "Rahul", "last_name": "Shah"}]

    class FakeTransactionService:
        def __init__(self, db):
            pass

        def get_transaction(self, transaction_id):
            return type(
                "Tx",
                (),
                {
                    "id": transaction_id,
                    "amount_cents": 1200,
                    "iso_currency_code": "USD",
                    "merchant_name": "Costco",
                    "name": "Costco",
                    "splitwise_expense_id": None,
                },
            )()

        def create_equal_split_expense(self, **kwargs):
            calls.update(kwargs)
            return self.get_transaction(kwargs["tx_id"]), {"expenses": [{"id": "expense-1"}]}

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            pass

        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "SplitwiseService", FakeSplitwiseService)
    monkeypatch.setattr(telegram_routes, "TransactionService", FakeTransactionService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.set_pending(
        "chat-1", "user-1", 123, mode="ai_chat"
    )

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "message": {
                "chat": {"id": "chat-1"},
                "from": {"id": "user-1"},
                "text": "split with Rahul",
            }
        },
    )

    assert response.status_code == 200
    assert calls == {}
    assert "Confirm split" in messages[0][1]
    assert messages[0][2]["inline_keyboard"][0][0]["text"] == "Confirm split"

    confirm_response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "confirm-ai",
                "data": "review:confirm:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    assert confirm_response.status_code == 200
    assert calls["tx_id"] == 123
    assert calls["friend_user_ids"] == [7]
    assert calls["confirm"] is True


def test_ai_chat_cancel_clears_pending_state(monkeypatch):
    answers = []

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

        def send_message(self, message, reply_markup=None, chat_id=None):
            pass

    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.set_pending(
        "chat-1", "user-1", 123, mode="ai_chat"
    )

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "cancel-ai",
                "data": "review:cancel:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    assert response.status_code == 200
    assert answers == [("cancel-ai", "Split flow cancelled.")]
    assert telegram_routes.telegram_split_state_store.get_pending("chat-1", "user-1") is None


def test_ai_chat_group_split_only_posts_after_confirm(monkeypatch):
    calls = {}
    messages = []

    class FakeSplitwiseService:
        def get_groups(self):
            return [
                {
                    "id": 44,
                    "name": "Apartment group",
                    "members": [
                        {"id": 7, "first_name": "Rahul", "last_name": "Shah"},
                        {"id": 9, "first_name": "Akash", "last_name": "Rao"},
                    ],
                }
            ]

    class FakeTransactionService:
        def __init__(self, db):
            pass

        def get_transaction(self, transaction_id):
            return type(
                "Tx",
                (),
                {
                    "id": transaction_id,
                    "amount_cents": 2400,
                    "iso_currency_code": "USD",
                    "merchant_name": "Costco",
                    "name": "Costco",
                    "splitwise_expense_id": None,
                },
            )()

        def create_equal_split_expense(self, **kwargs):
            calls.update(kwargs)
            return self.get_transaction(kwargs["tx_id"]), {"expenses": [{"id": "expense-1"}]}

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            pass

        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "SplitwiseService", FakeSplitwiseService)
    monkeypatch.setattr(telegram_routes, "TransactionService", FakeTransactionService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.set_pending(
        "chat-1", "user-1", 123, mode="ai_chat"
    )

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "message": {
                "chat": {"id": "chat-1"},
                "from": {"id": "user-1"},
                "text": "split in Apartment group with Rahul and Akash",
            }
        },
    )

    assert response.status_code == 200
    assert calls == {}
    assert "Confirm split" in messages[0][1]

    TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "confirm-ai-group",
                "data": "review:confirm:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    assert calls["group_id"] == 44
    assert calls["friend_user_ids"] == [7, 9]


def test_ai_chat_ambiguous_names_ask_for_button_selection(monkeypatch):
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
    telegram_routes.telegram_split_state_store.set_pending(
        "chat-1", "user-1", 123, mode="ai_chat"
    )

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "message": {
                "chat": {"id": "chat-1"},
                "from": {"id": "user-1"},
                "text": "split with Rahul",
            }
        },
    )

    assert response.status_code == 200
    assert "Multiple matches found" in messages[0][1]
    assert messages[0][2]["inline_keyboard"][0][0]["callback_data"] == "friend:123:7"


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
            tx = type(
                "Tx",
                (),
                {
                    "id": kwargs["tx_id"],
                    "splitwise_expense_id": "expense-1",
                    "amount_cents": 1200,
                    "iso_currency_code": "USD",
                    "merchant_name": "Costco",
                    "name": "Costco",
                },
            )()
            return tx, {"expenses": [{"id": "expense-1"}]}

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
    assert pending.friend_lookup_by_id[7] == "Rahul Shah"
    assert pending.friend_lookup_by_id[9] == "Akash Rao"
    assert "Multiple matches found" in messages[0][1]
    assert "Akash Rao" in messages[0][1]

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
    assert calls == {}
    confirmation = [
        message for message in messages if message[1] and "Confirm split" in message[1]
    ][0]
    assert "Akash Rao" in confirmation[1]
    assert "Rahul Shah" in confirmation[1]
    assert any("Akash Rao" in str(message[1]) for message in messages)
    assert any("Rahul Shah" in str(message[1]) for message in messages)


def test_telegram_group_name_multiple_matches_sends_group_choices(monkeypatch):
    messages = []

    class FakeSplitwiseService:
        def get_groups(self):
            return [
                {"id": 44, "name": "House", "members": []},
                {"id": 45, "name": "House Trip", "members": []},
            ]

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            messages.append(("answer", text, None))

        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "SplitwiseService", FakeSplitwiseService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.set_pending(
        "chat-1",
        "user-1",
        123,
        mode="group_name",
    )

    response = TestClient(app).post(
        "/telegram/webhook",
        json={"message": {"chat": {"id": "chat-1"}, "from": {"id": "user-1"}, "text": "House"}},
    )

    assert response.status_code == 200
    assert "Multiple groups found" in messages[0][1]
    assert messages[0][2]["inline_keyboard"][0][0]["callback_data"] == "group:123:44"
    assert messages[0][2]["inline_keyboard"][1][0]["callback_data"] == "group:123:45"


def test_telegram_group_member_selection_posts_group_split(monkeypatch):
    calls = {}
    messages = []

    class FakeSplitwiseService:
        def get_groups(self):
            return [
                {
                    "id": 44,
                    "name": "House",
                    "members": [
                        {"id": 7, "first_name": "Rahul", "last_name": "Shah"},
                        {"id": 9, "first_name": "Akash", "last_name": "Rao"},
                    ],
                }
            ]

    class FakeTransactionService:
        def __init__(self, db):
            pass

        def get_transaction(self, transaction_id):
            return type("Tx", (), {"splitwise_expense_id": None})()

        def create_equal_split_expense(self, **kwargs):
            calls.update(kwargs)
            tx = type(
                "Tx",
                (),
                {
                    "id": kwargs["tx_id"],
                    "splitwise_expense_id": "expense-1",
                    "amount_cents": 1200,
                    "iso_currency_code": "USD",
                    "merchant_name": "Costco",
                    "name": "Costco",
                },
            )()
            return tx, {"expenses": [{"id": "expense-1"}]}

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            messages.append(("answer", text, None))

        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "SplitwiseService", FakeSplitwiseService)
    monkeypatch.setattr(telegram_routes, "TransactionService", FakeTransactionService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.set_pending(
        "chat-1",
        "user-1",
        123,
        mode="group_name",
    )

    group_response = TestClient(app).post(
        "/telegram/webhook",
        json={"message": {"chat": {"id": "chat-1"}, "from": {"id": "user-1"}, "text": "House"}},
    )
    member_response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "message": {
                "chat": {"id": "chat-1"},
                "from": {"id": "user-1"},
                "text": "Rahul, Akash",
            }
        },
    )

    assert group_response.status_code == 200
    assert member_response.status_code == 200
    assert calls == {}
    assert "Confirm split" in messages[-1][1]

    confirm_response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "confirm-group-text",
                "data": "review:confirm:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    assert confirm_response.status_code == 200
    assert calls["tx_id"] == 123
    assert calls["group_id"] == 44
    assert calls["friend_user_ids"] == [7, 9]
    assert calls["confirm"] is True
    split_messages = [
        message for message in messages if message[1] and "Split posted to Splitwise" in message[1]
    ]
    assert split_messages
    assert split_messages[0][2]["inline_keyboard"][0][0]["callback_data"] == "review:undo:123"


def test_telegram_done_with_selected_participants_posts_equal_split(monkeypatch):
    calls = {}
    messages = []
    answers = []
    pending = telegram_routes.telegram_split_state_store.set_pending(
        "chat-1",
        "user-1",
        123,
        mode="people_select",
    )
    pending.add_friend(7, "Rahul Shah")

    class FakeTransactionService:
        def __init__(self, db):
            pass

        def get_transaction(self, transaction_id):
            return type("Tx", (), {"splitwise_expense_id": None})()

        def create_equal_split_expense(self, **kwargs):
            calls.update(kwargs)
            tx = type(
                "Tx",
                (),
                {
                    "id": kwargs["tx_id"],
                    "splitwise_expense_id": "expense-1",
                    "amount_cents": 1200,
                    "iso_currency_code": "USD",
                    "merchant_name": "Costco",
                    "name": "Costco",
                },
            )()
            return tx, {"expenses": [{"id": "expense-1"}]}

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "TransactionService", FakeTransactionService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "done",
                "data": "review:done:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    assert response.status_code == 200
    assert calls == {}
    assert answers == [("done", "Review and confirm the split.")]
    assert "Confirm split" in messages[0][1]

    confirm_response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "confirm",
                "data": "review:confirm:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    assert confirm_response.status_code == 200
    assert calls["friend_user_ids"] == [7]
    assert calls["group_id"] is None
    assert calls["confirm"] is True
    assert telegram_routes.telegram_split_state_store.get_pending("chat-1", "user-1") is None
    assert answers[-1] == ("confirm", "Creating split.")
    split_messages = [
        message for message in messages if message[1] and "Split posted to Splitwise" in message[1]
    ]
    assert split_messages
    assert split_messages[0][2]["inline_keyboard"][0][0]["callback_data"] == "review:undo:123"


def test_telegram_undo_callback_routes_correctly(monkeypatch):
    messages = []
    answers = []

    class FakeTransactionService:
        def __init__(self, db):
            pass

        def undo_transaction(self, transaction_id):
            assert transaction_id == 123
            return type(
                "Tx",
                (),
                {
                    "id": 123,
                    "amount_cents": 633,
                    "iso_currency_code": "USD",
                    "merchant_name": "Uber",
                    "name": "Uber",
                },
            )()

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "TransactionService", FakeTransactionService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)

    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "undo-1",
                "data": "review:undo:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    assert response.status_code == 200
    assert answers == [("undo-1", "Transaction moved back to review.")]
    assert messages[0][1] == "↩️ <b>Uber — USD 6.33</b> moved back to review."


def test_telegram_group_quick_select_and_done_posts_group_split(monkeypatch):
    calls = {}
    messages = []
    answers = []

    class FakeSplitwiseService:
        def get_groups(self):
            return [
                {
                    "id": 44,
                    "name": "Apartment group",
                    "members": [
                        {"id": 7, "first_name": "Rahul", "last_name": "Shah"},
                        {"id": 9, "first_name": "Akash", "last_name": "Rao"},
                    ],
                }
            ]

    class FakeTransactionService:
        def __init__(self, db):
            pass

        def get_transaction(self, transaction_id):
            return type("Tx", (), {"splitwise_expense_id": None})()

        def create_equal_split_expense(self, **kwargs):
            calls.update(kwargs)
            tx = type(
                "Tx",
                (),
                {
                    "id": kwargs["tx_id"],
                    "splitwise_expense_id": "expense-1",
                    "amount_cents": 1200,
                    "iso_currency_code": "USD",
                    "merchant_name": "Costco",
                    "name": "Costco",
                },
            )()
            return tx, {"expenses": [{"id": "expense-1"}]}

    class FakeTelegramService:
        def answer_callback_query(self, callback_query_id, text):
            answers.append((callback_query_id, text))

        def send_message(self, message, reply_markup=None, chat_id=None):
            messages.append((chat_id, message, reply_markup))

    monkeypatch.setattr(telegram_routes, "SplitwiseService", FakeSplitwiseService)
    monkeypatch.setattr(telegram_routes, "TransactionService", FakeTransactionService)
    monkeypatch.setattr(telegram_routes, "TelegramService", FakeTelegramService)
    telegram_routes.telegram_split_state_store.clear("chat-1", "user-1")

    TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "start-group",
                "data": "review:split_group:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )
    TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "mode-group",
                "data": "review:split_mode_equal:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )
    TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "choose-group",
                "data": "group:123:44",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )
    TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "select-member",
                "data": "friend:123:7",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )
    pending = telegram_routes.telegram_split_state_store.get_pending("chat-1", "user-1")
    assert pending.selected_friend_ids == [7]
    assert pending.selected_friend_names_by_id[7] == "Rahul Shah"
    assert "✅ Rahul Shah" in str(messages[-1][2])

    TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "deselect-member",
                "data": "friend:123:7",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )
    assert pending.selected_friend_ids == []

    TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "select-again",
                "data": "friend:123:7",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )
    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "done-group",
                "data": "review:done:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    assert response.status_code == 200
    assert calls == {}
    assert answers[-1] == ("done-group", "Review and confirm the split.")
    assert "Confirm split" in messages[-1][1]

    TestClient(app).post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "confirm-group",
                "data": "review:confirm:123",
                "message": {"chat": {"id": "chat-1"}},
                "from": {"id": "user-1"},
            }
        },
    )

    assert calls["group_id"] == 44
    assert calls["friend_user_ids"] == [7]
    assert calls["confirm"] is True
    assert answers[-1] == ("confirm-group", "Creating split.")
    split_messages = [
        message for message in messages if message[1] and "Split posted to Splitwise" in message[1]
    ]
    assert split_messages
    assert split_messages[0][2]["inline_keyboard"][0][0]["callback_data"] == "review:undo:123"

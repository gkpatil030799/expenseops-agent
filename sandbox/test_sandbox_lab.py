from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from sandbox.backend.config import SandboxSettings
from sandbox.backend.event_store import SandboxEventStore, new_trace_id
from sandbox.backend.guards import require_sandbox_lab_enabled, require_sandbox_plaid_env
from sandbox.backend.sandbox_orchestrator import SandboxOrchestrator, _parse_integrity_error_text
from sandbox.backend.state import SandboxState, SandboxStateStore, redact_cursor, redact_token
from sandbox.backend.webhook_hooks import sandbox_sync_guard_finish, sandbox_sync_guard_start


def test_sandbox_lab_disabled_guard(monkeypatch):
    monkeypatch.setenv("ENABLE_EXPENSEOPS_SANDBOX_LAB", "false")
    from sandbox.backend.config import get_sandbox_settings

    get_sandbox_settings.cache_clear()
    try:
        with pytest.raises(HTTPException) as exc:
            require_sandbox_lab_enabled()
        assert exc.value.status_code == 404
    finally:
        get_sandbox_settings.cache_clear()


def test_sandbox_lab_rejects_non_sandbox_plaid_env(monkeypatch):
    monkeypatch.setenv("ENABLE_EXPENSEOPS_SANDBOX_LAB", "true")
    monkeypatch.setenv("PLAID_ENV", "production")
    from app.config import get_settings
    from sandbox.backend.config import get_sandbox_settings

    get_settings.cache_clear()
    get_sandbox_settings.cache_clear()
    try:
        with pytest.raises(HTTPException) as exc:
            require_sandbox_plaid_env()
        assert exc.value.status_code == 403
    finally:
        get_settings.cache_clear()
        get_sandbox_settings.cache_clear()


def test_event_store_append_and_read(tmp_path):
    store = SandboxEventStore(tmp_path / "events.jsonl")
    event = store.append(
        trace_id="trace-1",
        event_type="sandbox_e2e_started",
        status="started",
    )

    rows = store.read(trace_id="trace-1")

    assert rows[0]["id"] == event.id
    assert rows[0]["event_type"] == "sandbox_e2e_started"


def test_access_token_redaction():
    assert redact_token("access-sandbox-1234567890abcd") == "access-sandbox-...abcd"


def test_cursor_redaction():
    assert redact_cursor("cursor-1234567890abcd") == "cursor-1...abcd"


def test_trace_id_generation():
    trace_id = new_trace_id()

    assert trace_id.startswith("sandbox_")


def test_transaction_date_validation_rejects_old_date(tmp_path):
    orchestrator = SandboxOrchestrator(
        db=object(),
        settings=SandboxSettings(enable_expenseops_sandbox_lab=True),
        state_store=SandboxStateStore(tmp_path / "state.json"),
        event_store=SandboxEventStore(tmp_path / "events.jsonl"),
        plaid=object(),
    )

    with pytest.raises(HTTPException):
        orchestrator._validate_sandbox_date(date.today() - timedelta(days=15))


def test_run_e2e_returns_structured_steps_with_mocked_plaid(tmp_path):
    db = _sqlite_session(tmp_path)
    fake_plaid = FakePlaid()
    orchestrator = SandboxOrchestrator(
        db=db,
        settings=SandboxSettings(
            enable_expenseops_sandbox_lab=True,
            sandbox_public_webhook_url="https://example.ngrok-free.app/plaid/webhook",
        ),
        state_store=SandboxStateStore(tmp_path / "state.json"),
        event_store=SandboxEventStore(tmp_path / "events.jsonl"),
        plaid=fake_plaid,
    )
    orchestrator.sync_now = lambda trace_id=None: {
        "trace_id": trace_id,
        "added_count": 0,
        "modified_count": 0,
        "removed_count": 0,
        "cursor_present": True,
        "cursor_updated": True,
        "next_cursor_present": True,
        "added_transactions": [],
    }

    result = orchestrator.run_e2e()

    assert result["status"] == "completed"
    assert [step.name for step in result["steps"]] == [
        "sandbox_item_ready",
        "sync_initialized",
        "transaction_created",
        "webhook_fired",
        "webhook_received",
        "sync_completed",
    ]
    db.close()


def test_create_transaction_is_create_only_with_explicit_false_flags(tmp_path):
    db = _sqlite_session(tmp_path)
    fake_plaid = FakePlaid()
    state_store = SandboxStateStore(tmp_path / "state.json")
    event_store = SandboxEventStore(tmp_path / "events.jsonl")
    state_store.save(
        SandboxState(
            item_id="sandbox-item",
            access_token="access-sandbox-token",
            latest_trace_id="trace-1",
            webhook_url="https://example.ngrok-free.app/plaid/webhook",
            webhook_attached=True,
        )
    )
    orchestrator = SandboxOrchestrator(
        db=db,
        settings=SandboxSettings(
            enable_expenseops_sandbox_lab=True,
            sandbox_public_webhook_url="https://example.ngrok-free.app/plaid/webhook",
        ),
        state_store=state_store,
        event_store=event_store,
        plaid=fake_plaid,
    )

    result = orchestrator.create_transaction(trace_id="trace-1", payload=_create_payload())
    events = event_store.read(trace_id="trace-1")

    assert result["created"] is True
    assert fake_plaid.create_transaction_calls == 1
    assert fake_plaid.fire_webhook_calls == 0
    assert fake_plaid.transactions_sync_calls == 0
    assert fake_plaid.webhook_updates == [None]
    saved_state = state_store.load()
    assert saved_state.webhook_attached is False
    assert saved_state.webhook_url is None
    event_types = {event["event_type"] for event in events}
    assert "sandbox_item_webhook_detached" in event_types
    assert "sandbox_transaction_create_succeeded" in event_types
    assert "plaid_transactions_sync_started" not in event_types
    assert "sandbox_webhook_fire_succeeded" not in event_types
    db.close()


def test_create_transaction_does_not_mutate_webhook_when_already_detached(tmp_path):
    db = _sqlite_session(tmp_path)
    fake_plaid = FakePlaid()
    state_store = SandboxStateStore(tmp_path / "state.json")
    event_store = SandboxEventStore(tmp_path / "events.jsonl")
    state_store.save(
        SandboxState(
            item_id="sandbox-item",
            access_token="access-sandbox-token",
            latest_trace_id="trace-detached",
            webhook_attached=False,
            webhook_url=None,
        )
    )
    orchestrator = SandboxOrchestrator(
        db=db,
        settings=SandboxSettings(
            enable_expenseops_sandbox_lab=True,
            sandbox_public_webhook_url="https://example.ngrok-free.app/plaid/webhook",
        ),
        state_store=state_store,
        event_store=event_store,
        plaid=fake_plaid,
    )

    result = orchestrator.create_transaction(
        trace_id="trace-detached",
        payload=_create_payload(),
    )
    events = event_store.read(trace_id="trace-detached")

    assert result["created"] is True
    assert fake_plaid.create_transaction_calls == 1
    assert fake_plaid.webhook_updates == []
    assert fake_plaid.fire_webhook_calls == 0
    assert fake_plaid.transactions_sync_calls == 0
    event_types = {event["event_type"] for event in events}
    assert "sandbox_item_webhook_detached" not in event_types
    assert "plaid_transactions_sync_started" not in event_types
    db.close()


def test_create_transaction_detaches_when_plaid_item_has_webhook_even_if_state_is_stale(
    tmp_path,
):
    db = _sqlite_session(tmp_path)
    fake_plaid = FakePlaid()
    fake_plaid.current_webhook = "https://example.ngrok-free.app/plaid/webhook"
    state_store = SandboxStateStore(tmp_path / "state.json")
    event_store = SandboxEventStore(tmp_path / "events.jsonl")
    state_store.save(
        SandboxState(
            item_id="sandbox-item",
            access_token="access-sandbox-token",
            latest_trace_id="trace-stale",
            webhook_attached=False,
            webhook_url=None,
        )
    )
    orchestrator = SandboxOrchestrator(
        db=db,
        settings=SandboxSettings(
            enable_expenseops_sandbox_lab=True,
            sandbox_public_webhook_url="https://example.ngrok-free.app/plaid/webhook",
        ),
        state_store=state_store,
        event_store=event_store,
        plaid=fake_plaid,
    )

    result = orchestrator.create_transaction(trace_id="trace-stale", payload=_create_payload())
    events = event_store.read(trace_id="trace-stale")

    assert result["created"] is True
    assert fake_plaid.webhook_updates == [None]
    assert fake_plaid.current_webhook is None
    event_types = {event["event_type"] for event in events}
    assert "sandbox_item_webhook_detached" in event_types
    assert "plaid_transactions_sync_started" not in event_types
    db.close()


def test_fire_webhook_route_calls_plaid_once_and_attaches_if_needed(tmp_path):
    db = _sqlite_session(tmp_path)
    fake_plaid = FakePlaid()
    state_store = SandboxStateStore(tmp_path / "state.json")
    event_store = SandboxEventStore(tmp_path / "events.jsonl")
    state_store.save(
        SandboxState(
            item_id="sandbox-item",
            access_token="access-sandbox-token",
            latest_trace_id="trace-1",
            webhook_attached=False,
        )
    )
    orchestrator = SandboxOrchestrator(
        db=db,
        settings=SandboxSettings(
            enable_expenseops_sandbox_lab=True,
            sandbox_public_webhook_url="https://example.ngrok-free.app/plaid/webhook",
        ),
        state_store=state_store,
        event_store=event_store,
        plaid=fake_plaid,
    )

    result = orchestrator.fire_webhook(trace_id="trace-1")
    events = event_store.read(trace_id="trace-1")

    assert result["trace_id"] == "trace-1"
    assert len(fake_plaid.webhook_updates) == 1
    assert fake_plaid.webhook_updates[0].startswith("https://")
    assert fake_plaid.fire_webhook_calls == 1
    assert any(event["event_type"] == "sandbox_webhook_fire_succeeded" for event in events)
    db.close()


def test_fire_webhook_reuses_attached_webhook_without_reattaching(tmp_path):
    db = _sqlite_session(tmp_path)
    fake_plaid = FakePlaid()
    state_store = SandboxStateStore(tmp_path / "state.json")
    event_store = SandboxEventStore(tmp_path / "events.jsonl")
    settings = SandboxSettings(
        enable_expenseops_sandbox_lab=True,
        sandbox_public_webhook_url="https://example.ngrok-free.app/plaid/webhook",
    )
    state_store.save(
        SandboxState(
            item_id="sandbox-item",
            access_token="access-sandbox-token",
            latest_trace_id="trace-1",
            webhook_url=settings.webhook_url,
            webhook_attached=True,
        )
    )
    orchestrator = SandboxOrchestrator(
        db=db,
        settings=settings,
        state_store=state_store,
        event_store=event_store,
        plaid=fake_plaid,
    )

    result = orchestrator.fire_webhook(trace_id="trace-1")
    events = event_store.read(trace_id="trace-1")

    assert result["trace_id"] == "trace-1"
    assert fake_plaid.webhook_updates == []
    assert fake_plaid.fire_webhook_calls == 1
    event_types = {event["event_type"] for event in events}
    assert "sandbox_webhook_already_attached" in event_types
    assert "sandbox_webhook_fire_succeeded" in event_types
    db.close()


def test_loop_guard_triggers_after_repeated_sync_attempts(tmp_path):
    db = _sqlite_session(tmp_path)
    fake_plaid = FakePlaid()
    state_store = SandboxStateStore(tmp_path / "state.json")
    event_store = SandboxEventStore(tmp_path / "events.jsonl")
    state_store.save(
        SandboxState(
            item_id="sandbox-item",
            access_token="access-sandbox-token",
            latest_trace_id="trace-guard",
        )
    )
    for _ in range(3):
        event_store.append(
            trace_id="trace-guard",
            event_type="plaid_transactions_sync_started",
            status="started",
            plaid_item_id="sandbox-item",
        )
    orchestrator = SandboxOrchestrator(
        db=db,
        settings=SandboxSettings(enable_expenseops_sandbox_lab=True),
        state_store=state_store,
        event_store=event_store,
        plaid=fake_plaid,
    )

    result = orchestrator.sync_now(trace_id="trace-guard")
    events = event_store.read(trace_id="trace-guard")

    assert result["skipped"] is True
    assert fake_plaid.transactions_sync_calls == 0
    assert any(event["event_type"] == "sandbox_loop_guard_triggered" for event in events)
    db.close()


def test_duplicate_sync_for_same_trace_is_skipped(tmp_path):
    db = _sqlite_session(tmp_path)
    fake_plaid = FakePlaid()
    state_store = SandboxStateStore(tmp_path / "state.json")
    event_store = SandboxEventStore(tmp_path / "events.jsonl")
    state_store.save(
        SandboxState(
            item_id="sandbox-item",
            access_token="access-sandbox-token",
            latest_trace_id="trace-running",
        )
    )
    skipped, guard = sandbox_sync_guard_start(
        "sandbox-item",
        source_action="manual_sync",
        state_store=state_store,
        event_store=event_store,
    )
    assert skipped is False
    orchestrator = SandboxOrchestrator(
        db=db,
        settings=SandboxSettings(enable_expenseops_sandbox_lab=True),
        state_store=state_store,
        event_store=event_store,
        plaid=fake_plaid,
    )
    try:
        result = orchestrator.sync_now(trace_id="trace-running")
    finally:
        sandbox_sync_guard_finish(guard)
    events = event_store.read(trace_id="trace-running")

    assert result["skipped"] is True
    assert fake_plaid.transactions_sync_calls == 0
    assert any(
        event["event_type"] == "sandbox_sync_skipped_already_running"
        for event in events
    )
    db.close()


def test_init_sync_persists_cursor_and_events(tmp_path):
    db = _sqlite_session(tmp_path)
    fake_plaid = FakePlaid()
    state_store = SandboxStateStore(tmp_path / "state.json")
    event_store = SandboxEventStore(tmp_path / "events.jsonl")
    state_store.save(
        SandboxState(
            item_id="sandbox-item",
            access_token="access-sandbox-token",
            latest_trace_id="trace-1",
        )
    )
    orchestrator = SandboxOrchestrator(
        db=db,
        settings=SandboxSettings(
            enable_expenseops_sandbox_lab=True,
            sandbox_public_webhook_url="https://example.ngrok-free.app/plaid/webhook",
        ),
        state_store=state_store,
        event_store=event_store,
        plaid=fake_plaid,
    )

    result = orchestrator.init_sync(trace_id="trace-1")
    saved_state = state_store.load()
    events = event_store.read(trace_id="trace-1")

    assert result["has_cursor"] is True
    assert result["cursor_present"] is False
    assert result["cursor_updated"] is True
    assert result["next_cursor_present"] is True
    assert saved_state.transactions_cursor == "cursor-1"
    assert any(event["event_type"] == "plaid_transactions_sync_page_received" for event in events)
    assert any(event["event_type"] == "plaid_transactions_sync_cursor_saved" for event in events)
    db.close()


def test_integrity_error_text_is_sanitized_for_debugging():
    details = _parse_integrity_error_text(
        "UNIQUE constraint failed: expense_transactions.plaid_transaction_id"
    )

    assert details["constraint_name"] == "unique"
    assert details["table_name"] == "expense_transactions"
    assert details["column_name"] == "plaid_transaction_id"
    assert details["plaid_transaction_id"] is None


class FakePlaid:
    def __init__(self):
        self.create_transaction_calls = 0
        self.fire_webhook_calls = 0
        self.transactions_sync_calls = 0
        self.webhook_updates = []
        self.current_webhook = None

    def create_public_token(self, *, webhook_url=None):
        return {"public_token": "public-sandbox-token"}

    def exchange_public_token(self, public_token):
        return {"item_id": "sandbox-item", "access_token": "access-sandbox-token"}

    def transactions_sync(self, *, access_token, cursor):
        self.transactions_sync_calls += 1
        return {
            "added": [],
            "modified": [],
            "removed": [],
            "next_cursor": "cursor-1",
            "has_more": False,
        }

    def get_item_webhook(self, *, access_token):
        return self.current_webhook

    def create_transaction(self, **kwargs):
        self.create_transaction_calls += 1
        return {"request_id": "request-1"}

    def fire_webhook(self, **kwargs):
        self.fire_webhook_calls += 1
        return {"request_id": "request-2"}

    def update_webhook(self, *, access_token, webhook_url):
        self.webhook_updates.append(webhook_url)
        self.current_webhook = webhook_url
        return {"request_id": "request-webhook"}


def _create_payload():
    from sandbox.backend.schemas import CreateTransactionRequest

    return CreateTransactionRequest(
        description="ExpenseOps Sandbox Coffee",
        amount="12.34",
        auto_fire_webhook=False,
        auto_sync_after=False,
    )


def _sqlite_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'sandbox-lab.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine)
    return session_local()

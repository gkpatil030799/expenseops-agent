from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from sandbox.backend.config import SandboxSettings
from sandbox.backend.event_store import SandboxEventStore, new_trace_id
from sandbox.backend.fault_injection import SandboxFaultStore
from sandbox.backend.guards import require_sandbox_lab_enabled, require_sandbox_plaid_env
from sandbox.backend.plaid_sandbox_service import SandboxPlaidError
from sandbox.backend.reliability_runner import (
    ReliabilityLoadError,
    ReliabilityRunner,
    assert_reliability_expectations,
    summarize_reliability_events,
)
from sandbox.backend.sandbox_orchestrator import SandboxOrchestrator, _parse_integrity_error_text
from sandbox.backend.scenario_runner import (
    ScenarioLoadError,
    ScenarioRunner,
    assert_expectations,
    is_retryable_sandbox_error,
    summarize_events,
)
from sandbox.backend.schemas import ReliabilityDefinition, ScenarioDefinition
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
    assert "sandbox_webhook_already_detached" in event_types
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
    fake_plaid.current_webhook = settings.webhook_url
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


def test_fire_webhook_attaches_when_state_says_attached_but_plaid_is_detached(tmp_path):
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
            latest_trace_id="trace-stale-attach",
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

    result = orchestrator.fire_webhook(trace_id="trace-stale-attach")
    events = event_store.read(trace_id="trace-stale-attach")

    assert result["trace_id"] == "trace-stale-attach"
    assert fake_plaid.webhook_updates == [settings.webhook_url]
    assert fake_plaid.fire_webhook_calls == 1
    event_types = {event["event_type"] for event in events}
    assert "sandbox_item_webhook_attached" in event_types
    assert "sandbox_webhook_already_attached" not in event_types
    db.close()


def test_fire_webhook_attaches_when_plaid_has_wrong_webhook(tmp_path):
    db = _sqlite_session(tmp_path)
    fake_plaid = FakePlaid()
    fake_plaid.current_webhook = "https://wrong.example/plaid/webhook"
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
            latest_trace_id="trace-wrong-attach",
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

    result = orchestrator.fire_webhook(trace_id="trace-wrong-attach")
    events = event_store.read(trace_id="trace-wrong-attach")

    assert result["trace_id"] == "trace-wrong-attach"
    assert fake_plaid.webhook_updates == [settings.webhook_url]
    assert fake_plaid.current_webhook == settings.webhook_url
    assert fake_plaid.fire_webhook_calls == 1
    event_types = {event["event_type"] for event in events}
    assert "sandbox_item_webhook_attached" in event_types
    assert "sandbox_webhook_already_attached" not in event_types
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


def test_scenario_loader_reads_json_definitions(tmp_path):
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    (scenario_dir / "create.json").write_text(
        """
        {
          "id": "create_only_no_import",
          "name": "Create only",
          "description": "Create only",
          "flow": "create_only",
          "transaction": {"description": "Coffee", "amount": 1.23, "currency": "USD"}
        }
        """,
        encoding="utf-8",
    )
    runner, _fake = _scenario_runner(
        tmp_path,
        scenarios_path=scenario_dir,
    )

    scenarios = runner.list_scenarios()

    assert [scenario.id for scenario in scenarios] == ["create_only_no_import"]
    assert scenarios[0].transaction.iso_currency_code == "USD"


def test_invalid_scenario_schema_is_rejected(tmp_path):
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    (scenario_dir / "bad.json").write_text('{"id": "bad"}', encoding="utf-8")
    runner, _fake = _scenario_runner(tmp_path, scenarios_path=scenario_dir)

    with pytest.raises(ScenarioLoadError):
        runner.list_scenarios()


def test_run_create_only_scenario_calls_create_only(tmp_path):
    runner, fake = _scenario_runner(tmp_path, _scenario("create_only_no_import", "create_only"))

    result = runner.run_scenario("create_only_no_import")

    assert result.status == "passed"
    assert fake.create_calls == 1
    assert fake.sync_calls == 0
    assert fake.fire_calls == 0
    assert result.events_summary["transaction_created"] is True


def test_create_only_assertion_fails_if_sync_event_exists():
    scenario = _scenario("create_only_no_import", "create_only")
    summary = summarize_events(
        [
            {"event_type": "sandbox_transaction_create_succeeded"},
            {"event_type": "plaid_transactions_sync_completed"},
        ],
        trace_id="trace",
    )

    assertions = assert_expectations(scenario, summary)

    assert any(
        assertion.name == "no_unexpected_sync_for_create_only" and assertion.status == "failed"
        for assertion in assertions
    )


def test_create_only_assertion_fails_if_later_sync_leak_is_logged():
    scenario = _scenario("create_only_no_import", "create_only")
    summary = summarize_events(
        [
            {"event_type": "sandbox_transaction_create_succeeded"},
            {"event_type": "scenario_create_only_imported_later_skipped_notification"},
        ],
        trace_id="trace",
    )

    assertions = assert_expectations(scenario, summary)

    assert any(
        assertion.name == "no_create_only_leak"
        and assertion.status == "failed"
        and assertion.message == "Create-only transaction leaked into later sync."
        for assertion in assertions
    )


def test_manual_sync_scenario_expects_sync_completed(tmp_path):
    runner, fake = _scenario_runner(tmp_path, _scenario("manual_sync_basic", "manual_sync"))

    result = runner.run_scenario("manual_sync_basic")

    assert result.status == "passed"
    assert fake.create_calls == 1
    assert fake.sync_calls == 1
    assert result.events_summary["sync_completed"] is True


def test_webhook_scenario_expects_webhook_received_and_sync_completed(tmp_path):
    runner, fake = _scenario_runner(tmp_path, _scenario("webhook_basic", "webhook"))

    result = runner.run_scenario("webhook_basic")

    assert result.status == "passed"
    assert fake.create_calls == 1
    assert fake.fire_calls == 1
    assert result.events_summary["webhook_received"] is True
    assert result.events_summary["sync_completed"] is True


def test_telegram_sent_max_assertion_fails_when_exceeded():
    scenario = _scenario("manual_sync_basic", "manual_sync", telegram_sent_max=1)
    summary = summarize_events(
        [
            {"event_type": "sandbox_telegram_send_succeeded"},
            {"event_type": "sandbox_telegram_send_succeeded"},
        ],
        trace_id="trace",
    )

    assertions = assert_expectations(scenario, summary)

    assert any(
        assertion.name == "telegram_sent_max" and assertion.status == "failed"
        for assertion in assertions
    )


def test_integrity_and_loop_guard_assertions_fail_when_seen():
    scenario = _scenario("manual_sync_basic", "manual_sync")
    summary = summarize_events(
        [
            {"event_type": "sandbox_integrity_error"},
            {"event_type": "sandbox_loop_guard_triggered"},
        ],
        trace_id="trace",
    )

    assertions = assert_expectations(scenario, summary)

    assert any(
        assertion.name == "no_integrity_error" and assertion.status == "failed"
        for assertion in assertions
    )
    assert any(
        assertion.name == "no_loop_guard_triggered" and assertion.status == "failed"
        for assertion in assertions
    )


def test_scenario_result_persists_to_jsonl(tmp_path):
    runner, _fake = _scenario_runner(tmp_path, _scenario("manual_sync_basic", "manual_sync"))

    result = runner.run_scenario("manual_sync_basic")
    persisted = runner.get_result(result.scenario_run_id)

    assert persisted.scenario_run_id == result.scenario_run_id
    assert len(runner.list_results()) == 1


def test_run_all_aggregates_results(tmp_path):
    runner, _fake = _scenario_runner(
        tmp_path,
        _scenario("create_only_no_import", "create_only"),
        _scenario("manual_sync_basic", "manual_sync"),
    )

    result = runner.run_all()

    assert result.total == 2
    assert result.passed == 2
    assert result.status == "passed"


def test_run_all_places_create_only_scenario_last(tmp_path):
    runner, _fake = _scenario_runner(
        tmp_path,
        _scenario("create_only_no_import", "create_only"),
        _scenario("manual_sync_basic", "manual_sync"),
        _scenario("webhook_basic", "webhook"),
    )

    result = runner.run_all()

    assert [scenario.scenario_id for scenario in result.results] == [
        "manual_sync_basic",
        "webhook_basic",
        "create_only_no_import",
    ]


def test_run_all_waits_between_scenarios(tmp_path):
    sleeps = []
    runner, _fake = _scenario_runner(
        tmp_path,
        _scenario("create_only_no_import", "create_only"),
        _scenario("manual_sync_basic", "manual_sync"),
        sleep_func=sleeps.append,
    )

    runner.run_all()

    assert sleeps == [8]


def test_rate_limit_sandbox_error_is_retryable():
    exc = SandboxPlaidError(
        'rate limit exceeded for attempts to access "sandbox/transactions/create"',
        request_id="plaid-1",
    )

    assert is_retryable_sandbox_error(exc) is True


def test_transaction_create_retry_succeeds_on_second_attempt(tmp_path):
    runner, fake = _scenario_runner(tmp_path, _scenario("manual_sync_basic", "manual_sync"))
    fake.create_failures = [
        SandboxPlaidError("rate limit exceeded, please try again later", request_id="plaid-1")
    ]

    result = runner.run_scenario("manual_sync_basic")

    assert result.status == "passed"
    assert fake.create_calls == 2
    event_types = [event["event_type"] for event in result.raw_events]
    assert "scenario_transaction_create_retry_scheduled" in event_types
    assert "scenario_transaction_create_retry_started" in event_types


def test_transaction_create_retry_exhausts_with_clear_error(tmp_path):
    runner, fake = _scenario_runner(tmp_path, _scenario("manual_sync_basic", "manual_sync"))
    fake.create_failures = [
        SandboxPlaidError("rate limit exceeded, please try again later", request_id=f"plaid-{idx}")
        for idx in range(4)
    ]

    result = runner.run_scenario("manual_sync_basic")

    assert result.status == "error"
    assert result.error_message == "Plaid Sandbox rate limit while creating transaction"
    assert result.error_details["rate_limit_error"] is True
    assert result.error_details["error_class"] == "SandboxPlaidError"
    assert result.error_details["plaid_request_id"] == "plaid-3"
    assert any(
        assertion.name == "transaction_created" and assertion.status == "failed"
        for assertion in result.assertions
    )
    event_types = [event["event_type"] for event in result.raw_events]
    assert "scenario_transaction_create_retry_exhausted" in event_types


def test_run_all_counts_rate_limit_errors(tmp_path):
    runner, fake = _scenario_runner(
        tmp_path,
        _scenario("manual_sync_basic", "manual_sync"),
        _scenario("webhook_basic", "webhook"),
    )
    fake.create_failures = [
        SandboxPlaidError("rate limit exceeded, please try again later", request_id=f"plaid-{idx}")
        for idx in range(4)
    ]

    result = runner.run_all()

    assert result.errors == 1
    assert result.rate_limit_errors == 1


def test_reliability_loader_reads_json_definitions(tmp_path):
    reliability_dir = tmp_path / "reliability"
    reliability_dir.mkdir()
    (reliability_dir / "duplicate.json").write_text(
        """
        {
          "id": "duplicate_webhook",
          "name": "Duplicate webhook",
          "description": "Duplicate webhook",
          "type": "duplicate_webhook",
          "transaction": {"description": "Coffee", "amount": 1.23, "currency": "USD"}
        }
        """,
        encoding="utf-8",
    )
    runner, _fake = _reliability_runner(tmp_path, tests_path=reliability_dir)

    tests = runner.list_tests()

    assert [test.id for test in tests] == ["duplicate_webhook"]
    assert tests[0].transaction.iso_currency_code == "USD"


def test_invalid_reliability_definition_is_rejected(tmp_path):
    reliability_dir = tmp_path / "reliability"
    reliability_dir.mkdir()
    (reliability_dir / "bad.json").write_text('{"id": "bad"}', encoding="utf-8")
    runner, _fake = _reliability_runner(tmp_path, tests_path=reliability_dir)

    with pytest.raises(ReliabilityLoadError):
        runner.list_tests()


def test_reliability_result_persists_to_jsonl(tmp_path):
    runner, _fake = _reliability_runner(
        tmp_path,
        _reliability_test("repeated_manual_sync", "repeated_manual_sync"),
    )

    result = runner.run_test("repeated_manual_sync")
    persisted = runner.get_result(result.reliability_run_id)

    assert persisted.reliability_run_id == result.reliability_run_id
    assert len(runner.list_results()) == 1


def test_duplicate_webhook_assertion_logic():
    test = _reliability_test("duplicate_webhook", "duplicate_webhook")
    summary = summarize_reliability_events(
        [
            {"event_type": "sandbox_webhook_fire_succeeded"},
            {"event_type": "sandbox_webhook_fire_succeeded"},
            {"event_type": "sandbox_webhook_fire_succeeded"},
            {"event_type": "plaid_webhook_received"},
            {"event_type": "sandbox_telegram_send_succeeded"},
        ],
        trace_id="trace",
    )

    assertions = assert_reliability_expectations(test, summary)

    assert all(assertion.status == "passed" for assertion in assertions)


def test_repeated_sync_assertion_fails_when_unbounded():
    test = _reliability_test("repeated_manual_sync", "repeated_manual_sync")
    summary = summarize_reliability_events(
        [{"event_type": "plaid_transactions_sync_started"} for _ in range(6)],
        trace_id="trace",
    )

    assertions = assert_reliability_expectations(test, summary)

    assert any(
        assertion.name == "sync_attempts_bounded" and assertion.status == "failed"
        for assertion in assertions
    )


def test_concurrent_sync_guard_assertion(tmp_path):
    runner, _fake = _reliability_runner(
        tmp_path,
        _reliability_test("concurrent_sync", "concurrent_sync"),
    )

    result = runner.run_test("concurrent_sync")

    assert result.status == "passed"
    assert result.event_summary["sync_skipped_already_running_count"] == 1


def test_fault_store_consumes_fault_once(tmp_path):
    store = SandboxFaultStore()
    event_store = SandboxEventStore(tmp_path / "events.jsonl")

    store.enable(name="fail_next_telegram_send", trace_id="trace-1", event_store=event_store)

    assert store.consume(
        name="fail_next_telegram_send",
        trace_id="trace-1",
        event_store=event_store,
    )
    assert not store.consume(
        name="fail_next_telegram_send",
        trace_id="trace-1",
        event_store=event_store,
    )
    assert not store.consume(
        name="fail_next_telegram_send",
        trace_id="trace-2",
        event_store=event_store,
    )


def test_plaid_sync_failure_assertion_logic():
    test = _reliability_test(
        "plaid_sync_failure_simulation",
        "plaid_sync_failure_simulation",
    )
    summary = summarize_reliability_events(
        [
            {"event_type": "sandbox_fault_consumed"},
            {"event_type": "plaid_transactions_sync_failed"},
        ],
        trace_id="trace",
    )

    assertions = assert_reliability_expectations(test, summary)

    assert any(
        assertion.name == "failure_logged" and assertion.status == "passed"
        for assertion in assertions
    )


def test_cursor_missing_simulation_assertion_logic():
    test = _reliability_test("cursor_missing_recovery", "cursor_missing_recovery")
    summary = summarize_reliability_events(
        [{"event_type": "reliability_cursor_missing_simulated"}],
        trace_id="trace",
    )

    assertions = assert_reliability_expectations(test, summary)

    assert any(
        assertion.name == "cursor_not_corrupted" and assertion.status == "passed"
        for assertion in assertions
    )


def test_loop_guard_assertion_logic():
    test = _reliability_test("loop_guard", "loop_guard")
    summary = summarize_reliability_events(
        [{"event_type": "sandbox_loop_guard_triggered"}],
        trace_id="trace",
    )

    assertions = assert_reliability_expectations(test, summary)

    assert any(
        assertion.name == "loop_guard_triggered" and assertion.status == "passed"
        for assertion in assertions
    )


def test_reliability_run_all_aggregates_counts(tmp_path):
    runner, _fake = _reliability_runner(
        tmp_path,
        _reliability_test("repeated_manual_sync", "repeated_manual_sync"),
        _reliability_test("webhook_observation_timeout", "webhook_observation_timeout"),
    )

    result = runner.run_all()

    assert result.total == 2
    assert result.passed == 1
    assert result.partial == 1
    assert result.partial_count == 1


def test_reliability_rate_limit_retry_reuses_classification(tmp_path):
    runner, fake = _reliability_runner(
        tmp_path,
        _reliability_test("repeated_manual_sync", "repeated_manual_sync"),
    )
    fake.create_failures = [
        SandboxPlaidError("rate limit exceeded, please try again later", request_id="plaid-1")
    ]

    result = runner.run_test("repeated_manual_sync")

    assert result.status == "passed"
    assert fake.create_calls == 2


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


class FakeScenarioOrchestrator:
    def __init__(self, event_store):
        self.event_store = event_store
        self.state_store = SandboxStateStore()
        self.create_calls = 0
        self.fire_calls = 0
        self.sync_calls = 0
        self.init_calls = 0
        self.create_failures = []

    def ensure_item_ready(self, trace_id):
        return SandboxState(item_id="sandbox-item", access_token="access-sandbox-token")

    def init_sync(self, trace_id=None):
        self.init_calls += 1
        return {"trace_id": trace_id, "has_cursor": True}

    def create_transaction(self, payload, trace_id=None):
        self.create_calls += 1
        if self.create_failures:
            raise self.create_failures.pop(0)
        self.event_store.append(
            trace_id=trace_id,
            event_type="sandbox_transaction_create_succeeded",
            status="succeeded",
        )
        return {
            "trace_id": trace_id,
            "transaction": {
                "description": payload.description,
                "amount": str(payload.amount),
                "iso_currency_code": payload.iso_currency_code,
            },
            "plaid_request_id": "request-create",
            "created": True,
            "steps": [],
        }

    def fire_webhook(
        self,
        trace_id=None,
        webhook_type="TRANSACTIONS",
        webhook_code="SYNC_UPDATES_AVAILABLE",
    ):
        self.fire_calls += 1
        self.event_store.append(
            trace_id=trace_id,
            event_type="sandbox_item_webhook_attached",
            status="succeeded",
        )
        self.event_store.append(
            trace_id=trace_id,
            event_type="sandbox_webhook_fire_succeeded",
            status="succeeded",
        )
        self.event_store.append(
            trace_id=trace_id,
            event_type="plaid_webhook_received",
            status="info",
        )
        self.event_store.append(
            trace_id=trace_id,
            event_type="plaid_transactions_sync_completed",
            status="succeeded",
        )
        return {"trace_id": trace_id, "webhook_fired": True, "plaid_request_id": "request-fire"}

    def sync_now(self, trace_id=None):
        self.sync_calls += 1
        self.event_store.append(
            trace_id=trace_id,
            event_type="plaid_transactions_sync_cursor_saved",
            status="info",
        )
        self.event_store.append(
            trace_id=trace_id,
            event_type="plaid_transactions_sync_completed",
            status="succeeded",
        )
        return {
            "trace_id": trace_id,
            "added_count": 1,
            "modified_count": 0,
            "removed_count": 0,
            "cursor_present": True,
            "cursor_updated": True,
            "next_cursor_present": True,
            "added_transactions": [{"id": 1, "status": "ask_user"}],
        }

    def run_e2e(self, trace_id=None):
        self.create_calls += 1
        self.fire_calls += 1
        self.sync_calls += 1
        for event_type in [
            "sandbox_transaction_create_succeeded",
            "sandbox_webhook_fire_succeeded",
            "plaid_transactions_sync_completed",
            "sandbox_e2e_completed",
        ]:
            self.event_store.append(trace_id=trace_id, event_type=event_type, status="succeeded")
        return {"trace_id": trace_id, "status": "completed", "steps": [], "details": {}}


def _create_payload():
    from sandbox.backend.schemas import CreateTransactionRequest

    return CreateTransactionRequest(
        description="ExpenseOps Sandbox Coffee",
        amount="12.34",
        auto_fire_webhook=False,
        auto_sync_after=False,
    )


def _scenario_runner(
    tmp_path,
    *scenarios,
    scenarios_path=None,
    sleep_func=lambda _seconds: None,
    jitter_func=lambda: 0,
):
    event_store = SandboxEventStore(tmp_path / "events.jsonl")
    scenario_dir = scenarios_path or tmp_path / "scenarios"
    scenario_dir.mkdir(exist_ok=True)
    for scenario in scenarios:
        (scenario_dir / f"{scenario.id}.json").write_text(
            scenario.model_dump_json(),
            encoding="utf-8",
        )
    fake = FakeScenarioOrchestrator(event_store)
    runner = ScenarioRunner(
        db=object(),
        settings=SandboxSettings(enable_expenseops_sandbox_lab=True),
        scenarios_path=scenario_dir,
        result_path=tmp_path / "scenario_runs.jsonl",
        event_store=event_store,
        orchestrator=fake,
        sleep_func=sleep_func,
        jitter_func=jitter_func,
    )
    return runner, fake


def _reliability_runner(
    tmp_path,
    *tests,
    tests_path=None,
    sleep_func=lambda _seconds: None,
    jitter_func=lambda: 0,
):
    event_store = SandboxEventStore(tmp_path / "reliability-events.jsonl")
    reliability_dir = tests_path or tmp_path / "reliability"
    reliability_dir.mkdir(exist_ok=True)
    for test in tests:
        (reliability_dir / f"{test.id}.json").write_text(
            test.model_dump_json(),
            encoding="utf-8",
        )
    fake = FakeScenarioOrchestrator(event_store)
    runner = ReliabilityRunner(
        db=object(),
        settings=SandboxSettings(enable_expenseops_sandbox_lab=True),
        tests_path=reliability_dir,
        result_path=tmp_path / "reliability_runs.jsonl",
        event_store=event_store,
        orchestrator=fake,
        sleep_func=sleep_func,
        jitter_func=jitter_func,
    )
    return runner, fake


def _scenario(scenario_id, flow, **expectation_overrides):
    expectations = {
        "transaction_created": True,
        "telegram_sent_min": 0,
        "telegram_sent_max": 1,
        "no_integrity_error": True,
        "no_loop_guard_triggered": True,
    }
    if flow == "manual_sync":
        expectations["sync_completed"] = True
        expectations["imported_transaction_visible"] = True
    if flow == "webhook":
        expectations["webhook_fired"] = True
        expectations["webhook_received"] = True
        expectations["sync_completed"] = True
    if flow == "create_only":
        expectations["telegram_sent_max"] = 0
        expectations["no_boundary_violation"] = True
    expectations.update(expectation_overrides)
    return ScenarioDefinition.model_validate(
        {
            "id": scenario_id,
            "name": scenario_id.replace("_", " ").title(),
            "description": "Test scenario",
            "flow": flow,
            "transaction": {
                "description": "Scenario Coffee",
                "amount": "1.23",
                "iso_currency_code": "USD",
            },
            "expectations": expectations,
            "timeout_seconds": 1,
        }
    )


def _reliability_test(test_id, test_type, **expectation_overrides):
    expectations = {
        "telegram_sent_max": 1,
        "no_integrity_error": True,
        "sync_attempts_bounded": True,
        "no_loop_runaway": True,
    }
    parameters = {"max_sync_attempts": 4}
    if test_type == "duplicate_webhook":
        parameters["webhook_fire_count"] = 3
        expectations["webhook_received"] = True
    if test_type == "repeated_manual_sync":
        parameters["sync_count"] = 3
        expectations["cursor_not_corrupted"] = True
        expectations["no_duplicate_transaction"] = True
    if test_type == "concurrent_sync":
        expectations["no_duplicate_transaction"] = True
    if test_type == "webhook_observation_timeout":
        expectations = {
            "telegram_sent_max": 0,
            "webhook_timeout_reported": True,
            "webhook_received": False,
            "no_integrity_error": True,
        }
    if test_type == "plaid_sync_failure_simulation":
        expectations = {
            "telegram_sent_max": 0,
            "failure_logged": True,
            "no_integrity_error": True,
        }
    if test_type == "cursor_missing_recovery":
        expectations = {
            "telegram_sent_max": 0,
            "cursor_not_corrupted": True,
            "no_integrity_error": True,
        }
    if test_type == "loop_guard":
        expectations = {
            "telegram_sent_max": 0,
            "loop_guard_triggered": True,
            "no_loop_runaway": True,
            "no_integrity_error": True,
        }
    expectations.update(expectation_overrides)
    return ReliabilityDefinition.model_validate(
        {
            "id": test_id,
            "name": test_id.replace("_", " ").title(),
            "description": "Reliability test",
            "type": test_type,
            "transaction": {
                "description": "Reliability Coffee",
                "amount": "1.23",
                "iso_currency_code": "USD",
            },
            "parameters": parameters,
            "expectations": expectations,
            "timeout_seconds": 1,
        }
    )


def _sqlite_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'sandbox-lab.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine)
    return session_local()

from __future__ import annotations

import json
import random
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from sandbox.backend.config import SCENARIO_RUN_LOG_PATH, SCENARIOS_PATH, SandboxSettings
from sandbox.backend.event_store import SandboxEventStore
from sandbox.backend.plaid_sandbox_service import SandboxPlaidError
from sandbox.backend.sandbox_orchestrator import SandboxOrchestrator
from sandbox.backend.schemas import (
    CreateTransactionRequest,
    ScenarioAssertionResult,
    ScenarioDefinition,
    ScenarioExpectations,
    ScenarioResult,
    ScenarioRunAggregateResponse,
)

SCENARIO_RUN_ALL_DELAY_SECONDS = 8
TRANSACTION_CREATE_MAX_ATTEMPTS = 4
TRANSACTION_CREATE_RETRY_DELAYS_SECONDS = [5, 10, 20, 30]
SCENARIO_RUN_ALL_JITTER_SECONDS = 2


class ScenarioLoadError(RuntimeError):
    pass


class ScenarioExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_details: dict[str, Any] | None = None,
        assertions: list[ScenarioAssertionResult] | None = None,
    ):
        super().__init__(message)
        self.error_details = error_details or {}
        self.assertions = assertions or []


class ScenarioRunner:
    def __init__(
        self,
        *,
        db: Session,
        settings: SandboxSettings,
        scenarios_path: Path = SCENARIOS_PATH,
        result_path: Path = SCENARIO_RUN_LOG_PATH,
        event_store: SandboxEventStore | None = None,
        orchestrator: SandboxOrchestrator | None = None,
        run_all_delay_seconds: int | None = None,
        sleep_func=time.sleep,
        jitter_func=lambda: random.uniform(0, SCENARIO_RUN_ALL_JITTER_SECONDS),
    ):
        self.db = db
        self.settings = settings
        self.scenarios_path = scenarios_path
        self.result_path = result_path
        self.event_store = event_store or SandboxEventStore()
        self.orchestrator = orchestrator or SandboxOrchestrator(
            db=db,
            settings=settings,
            event_store=self.event_store,
        )
        self.run_all_delay_seconds = (
            settings.sandbox_scenario_run_all_delay_seconds
            if run_all_delay_seconds is None
            else run_all_delay_seconds
        )
        self.sleep_func = sleep_func
        self.jitter_func = jitter_func

    def list_scenarios(self) -> list[ScenarioDefinition]:
        scenarios = [
            self._load_scenario(path)
            for path in sorted(self.scenarios_path.glob("*.json"))
        ]
        enabled = [scenario for scenario in scenarios if scenario.enabled]
        return sorted(enabled, key=lambda scenario: scenario.id == "create_only_no_import")

    def get_scenario(self, scenario_id: str) -> ScenarioDefinition:
        for scenario in self.list_scenarios():
            if scenario.id == scenario_id:
                return scenario
        raise KeyError(scenario_id)

    def run_scenario(self, scenario_id: str) -> ScenarioResult:
        scenario = self.get_scenario(scenario_id)
        return self._run(scenario)

    def run_all(self) -> ScenarioRunAggregateResponse:
        scenarios = self.list_scenarios()
        results = []
        for index, scenario in enumerate(scenarios):
            if index > 0 and self.run_all_delay_seconds > 0:
                self.sleep_func(self.run_all_delay_seconds + self.jitter_func())
            results.append(self._run(scenario))
        passed = sum(1 for result in results if result.status == "passed")
        failed = sum(1 for result in results if result.status == "failed")
        partial = sum(1 for result in results if result.status == "partial")
        errors = sum(1 for result in results if result.status == "error")
        rate_limit_errors = sum(
            1 for result in results if result.error_details.get("rate_limit_error") is True
        )
        status = "passed" if failed == 0 and errors == 0 and partial == 0 else "failed"
        if errors and not failed:
            status = "error"
        elif partial and not failed and not errors:
            status = "partial"
        return ScenarioRunAggregateResponse(
            status=status,
            total=len(results),
            passed=passed,
            failed=failed,
            partial=partial,
            errors=errors,
            rate_limit_errors=rate_limit_errors,
            passed_count=passed,
            failed_count=failed,
            error_count=errors,
            rate_limit_error_count=rate_limit_errors,
            results=results,
        )

    def list_results(self, *, limit: int = 50) -> list[ScenarioResult]:
        if not self.result_path.exists():
            return []
        rows: list[ScenarioResult] = []
        for line in self.result_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(ScenarioResult.model_validate_json(line))
            except ValidationError:
                continue
        return rows[-limit:]

    def get_result(self, scenario_run_id: str) -> ScenarioResult:
        for result in reversed(self.list_results(limit=1000)):
            if result.scenario_run_id == scenario_run_id:
                return result
        raise KeyError(scenario_run_id)

    def clear_results(self) -> None:
        if self.result_path.exists():
            self.result_path.unlink()

    def _run(self, scenario: ScenarioDefinition) -> ScenarioResult:
        started = _utc_now()
        start_monotonic = time.monotonic()
        scenario_run_id = f"run_{uuid.uuid4().hex[:12]}"
        trace_id = _scenario_trace_id(scenario.id)
        transaction_summary: dict[str, Any] = {}
        error_message = None
        error_details: dict[str, Any] = {}
        try:
            transaction_summary = self._execute_flow(scenario, trace_id)
            events = self._wait_for_events(scenario, trace_id)
            summary = summarize_events(
                events,
                trace_id=trace_id,
                transaction_summary=transaction_summary,
            )
            assertions = assert_expectations(scenario, summary)
            status = _status_from_assertions(assertions)
        except ScenarioExecutionError as exc:
            events = self.event_store.read(trace_id=trace_id, limit=1000)
            summary = summarize_events(
                events,
                trace_id=trace_id,
                transaction_summary=transaction_summary,
            )
            assertions = exc.assertions
            status = "error"
            error_message = str(exc)
            error_details = exc.error_details
        except Exception as exc:
            events = self.event_store.read(trace_id=trace_id, limit=1000)
            summary = summarize_events(
                events,
                trace_id=trace_id,
                transaction_summary=transaction_summary,
            )
            assertions = [
                _assertion(
                    "transaction_created",
                    True,
                    False,
                    False,
                    "Scenario errored before transaction_created could be verified.",
                )
            ]
            status = "error"
            error_message = str(exc) or type(exc).__name__
            error_details = {
                "error_class": type(exc).__name__,
                "error_message": str(exc),
                "retryable": False,
            }
        completed = _utc_now()
        result = ScenarioResult(
            scenario_id=scenario.id,
            scenario_name=scenario.name,
            scenario_run_id=scenario_run_id,
            trace_id=trace_id,
            status=status,
            started_at=started,
            completed_at=completed,
            duration_ms=int((time.monotonic() - start_monotonic) * 1000),
            flow=scenario.flow,
            transaction_summary=transaction_summary,
            assertions=assertions,
            events_summary=summary,
            raw_events=events,
            error_message=error_message,
            error_details=error_details,
        )
        self._persist_result(result)
        return result

    def _execute_flow(self, scenario: ScenarioDefinition, trace_id: str) -> dict[str, Any]:
        if scenario.flow == "e2e":
            result = self.orchestrator.run_e2e(trace_id=trace_id)
            return {
                "e2e_status": result.get("status"),
                "steps": [step.model_dump() for step in result["steps"]],
            }

        self.orchestrator.ensure_item_ready(trace_id)
        if scenario.flow in {"manual_sync", "webhook"}:
            state = self.orchestrator.state_store.load()
            if not state.transactions_cursor:
                self.orchestrator.init_sync(trace_id=trace_id)

        payload = _transaction_request(scenario)
        created = self._create_transaction_with_retry(payload, trace_id=trace_id)
        summary = {
            "description": created.get("transaction", {}).get("description"),
            "amount": created.get("transaction", {}).get("amount"),
            "currency": created.get("transaction", {}).get("iso_currency_code"),
            "plaid_request_id": created.get("plaid_request_id"),
        }
        if scenario.flow == "manual_sync":
            sync_result = self.orchestrator.sync_now(trace_id=trace_id)
            summary["sync_added_count"] = sync_result.get("added_count")
            summary["added_transactions"] = sync_result.get("added_transactions", [])
        elif scenario.flow == "webhook":
            webhook_result = self.orchestrator.fire_webhook(trace_id=trace_id)
            summary["webhook_request_id"] = webhook_result.get("plaid_request_id")
        return summary

    def _create_transaction_with_retry(
        self,
        payload: CreateTransactionRequest,
        *,
        trace_id: str,
    ) -> dict[str, Any]:
        max_attempts = TRANSACTION_CREATE_MAX_ATTEMPTS
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                self.event_store.append(
                    trace_id=trace_id,
                    event_type="scenario_transaction_create_retry_started",
                    status="started",
                    payload={"attempt": attempt, "max_attempts": max_attempts},
                )
            try:
                return self.orchestrator.create_transaction(payload, trace_id=trace_id)
            except SandboxPlaidError as exc:
                retryable = is_retryable_sandbox_error(exc)
                details = _sandbox_error_details(exc, attempt=attempt, retryable=retryable)
                if retryable:
                    self.event_store.append(
                        trace_id=trace_id,
                        event_type="scenario_rate_limit_detected",
                        status="info",
                        payload=details,
                        plaid_request_id=exc.request_id,
                    )
                if retryable and attempt < max_attempts:
                    delay = TRANSACTION_CREATE_RETRY_DELAYS_SECONDS[attempt - 1]
                    self.event_store.append(
                        trace_id=trace_id,
                        event_type="scenario_transaction_create_retry_scheduled",
                        status="info",
                        payload={**details, "delay_seconds": delay, "next_attempt": attempt + 1},
                        plaid_request_id=exc.request_id,
                    )
                    self.sleep_func(delay)
                    continue
                if retryable:
                    self.event_store.append(
                        trace_id=trace_id,
                        event_type="scenario_transaction_create_retry_exhausted",
                        status="failed",
                        payload=details,
                        plaid_request_id=exc.request_id,
                    )
                    raise ScenarioExecutionError(
                        "Plaid Sandbox rate limit while creating transaction",
                        error_details={
                            **details,
                            "rate_limit_error": True,
                            "max_attempts": max_attempts,
                        },
                        assertions=[
                            _assertion(
                                "transaction_created",
                                True,
                                False,
                                False,
                                "Plaid Sandbox rate limit prevented transaction creation.",
                            )
                        ],
                    ) from exc
                raise

    def _wait_for_events(self, scenario: ScenarioDefinition, trace_id: str) -> list[dict[str, Any]]:
        deadline = time.monotonic() + scenario.timeout_seconds
        while True:
            events = self.event_store.read(trace_id=trace_id, limit=1000)
            summary = summarize_events(events, trace_id=trace_id)
            if _expectations_satisfied_for_poll(scenario.expectations, summary):
                return events
            if time.monotonic() >= deadline:
                return events
            time.sleep(0.5)

    def _load_scenario(self, path: Path) -> ScenarioDefinition:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if "transaction" in data and data["transaction"] and "currency" in data["transaction"]:
                data["transaction"]["iso_currency_code"] = data["transaction"].pop("currency")
            return ScenarioDefinition.model_validate(data)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ScenarioLoadError(f"Invalid scenario file {path.name}: {exc}") from exc

    def _persist_result(self, result: ScenarioResult) -> None:
        self.result_path.parent.mkdir(parents=True, exist_ok=True)
        with self.result_path.open("a", encoding="utf-8") as handle:
            handle.write(result.model_dump_json() + "\n")


def summarize_events(
    events: list[dict[str, Any]],
    *,
    trace_id: str,
    transaction_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    types = [str(event.get("event_type") or "") for event in events]
    telegram_sent_count = types.count("sandbox_telegram_send_succeeded")
    return {
        "trace_id": trace_id,
        "transaction_created": "sandbox_transaction_create_succeeded" in types,
        "webhook_attached": (
            "sandbox_item_webhook_attached" in types
            or "sandbox_webhook_already_attached" in types
        ),
        "webhook_fired": "sandbox_webhook_fire_succeeded" in types,
        "webhook_received": "plaid_webhook_received" in types,
        "sync_started": "plaid_transactions_sync_started" in types,
        "sync_completed": "plaid_transactions_sync_completed" in types,
        "telegram_sent_count": telegram_sent_count,
        "telegram_duplicate_skipped_count": types.count("sandbox_telegram_send_skipped_duplicate"),
        "loop_guard_triggered": "sandbox_loop_guard_triggered" in types,
        "integrity_error_seen": "sandbox_integrity_error" in types,
        "create_only_leaked_later": (
            "scenario_create_only_imported_later_skipped_notification" in types
        ),
        "unexpected_sync_for_create_only": "plaid_transactions_sync_completed" in types,
        "unexpected_webhook_for_create_only": "plaid_webhook_received" in types,
        "imported_transaction_visible": bool((transaction_summary or {}).get("added_transactions")),
        "event_count": len(events),
    }


def assert_expectations(
    scenario: ScenarioDefinition,
    summary: dict[str, Any],
) -> list[ScenarioAssertionResult]:
    expectations = scenario.expectations
    assertions: list[ScenarioAssertionResult] = []

    def expect_bool(field: str, actual_key: str | None = None) -> None:
        expected = getattr(expectations, field)
        if expected is None:
            assertions.append(_skipped(field))
            return
        actual = bool(summary.get(actual_key or field))
        assertions.append(_assertion(field, expected, actual, actual == expected))

    expect_bool("transaction_created")
    expect_bool("webhook_fired")
    expect_bool("webhook_received")
    expect_bool("sync_completed")
    expect_bool("imported_transaction_visible")
    expect_bool("review_needed_visible", "imported_transaction_visible")

    if expectations.telegram_sent_min is not None:
        actual = int(summary.get("telegram_sent_count") or 0)
        assertions.append(
            _assertion(
                "telegram_sent_min",
                f">= {expectations.telegram_sent_min}",
                actual,
                actual >= expectations.telegram_sent_min,
            )
        )
    if expectations.telegram_sent_max is not None:
        actual = int(summary.get("telegram_sent_count") or 0)
        assertions.append(
            _assertion(
                "telegram_sent_max",
                f"<= {expectations.telegram_sent_max}",
                actual,
                actual <= expectations.telegram_sent_max,
            )
        )

    if expectations.no_integrity_error is not None:
        actual = not bool(summary.get("integrity_error_seen"))
        assertions.append(_assertion("no_integrity_error", True, actual, actual))
    if expectations.no_loop_guard_triggered is not None:
        actual = not bool(summary.get("loop_guard_triggered"))
        assertions.append(_assertion("no_loop_guard_triggered", True, actual, actual))
    if expectations.no_boundary_violation is not None:
        no_violation = not (
            bool(summary.get("unexpected_sync_for_create_only"))
            or bool(summary.get("unexpected_webhook_for_create_only"))
            or bool(summary.get("create_only_leaked_later"))
            or int(summary.get("telegram_sent_count") or 0) > 0
        )
        assertions.append(_assertion("no_boundary_violation", True, no_violation, no_violation))

    assertions.append(
        _assertion(
            "no_duplicate_telegram",
            "<= 1",
            int(summary.get("telegram_sent_count") or 0),
            int(summary.get("telegram_sent_count") or 0) <= 1,
        )
    )
    if scenario.flow == "create_only":
        assertions.extend(
            [
                _assertion(
                    "no_unexpected_sync_for_create_only",
                    False,
                    summary.get("unexpected_sync_for_create_only"),
                    not bool(summary.get("unexpected_sync_for_create_only")),
                ),
                _assertion(
                    "no_unexpected_webhook_for_create_only",
                    False,
                    summary.get("unexpected_webhook_for_create_only"),
                    not bool(summary.get("unexpected_webhook_for_create_only")),
                ),
                _assertion(
                    "no_create_only_leak",
                    False,
                    summary.get("create_only_leaked_later"),
                    not bool(summary.get("create_only_leaked_later")),
                    "Create-only transaction leaked into later sync.",
                ),
            ]
        )
    return assertions


def _transaction_request(scenario: ScenarioDefinition) -> CreateTransactionRequest:
    transaction = scenario.transaction
    if transaction is None:
        return CreateTransactionRequest()
    return CreateTransactionRequest(
        description=transaction.description,
        amount=transaction.amount,
        iso_currency_code=transaction.iso_currency_code,
        date_transacted=transaction.date_transacted,
        date_posted=transaction.date_posted,
        auto_fire_webhook=False,
        auto_sync_after=False,
    )


def _expectations_satisfied_for_poll(
    expectations: ScenarioExpectations,
    summary: dict[str, Any],
) -> bool:
    for field in ("transaction_created", "webhook_fired", "webhook_received", "sync_completed"):
        expected = getattr(expectations, field)
        if expected is True and not summary.get(field):
            return False
    return True


def is_retryable_sandbox_error(exc: SandboxPlaidError) -> bool:
    message = str(exc).lower()
    return (
        "rate limit" in message
        or "rate_limit" in message
        or "too many requests" in message
        or "try again later" in message
        or "temporarily unavailable" in message
    )


def _sandbox_error_details(
    exc: SandboxPlaidError,
    *,
    attempt: int,
    retryable: bool,
) -> dict[str, Any]:
    return {
        "error_class": type(exc).__name__,
        "error_message": str(exc),
        "plaid_request_id": exc.request_id,
        "attempt": attempt,
        "retryable": retryable,
    }


def _assertion(
    name: str,
    expected: Any,
    actual: Any,
    passed: bool,
    message: str | None = None,
) -> ScenarioAssertionResult:
    return ScenarioAssertionResult(
        name=name,
        status="passed" if passed else "failed",
        expected=expected,
        actual=actual,
        message="" if passed else message or f"Expected {name}={expected}, got {actual}",
    )


def _skipped(name: str) -> ScenarioAssertionResult:
    return ScenarioAssertionResult(name=name, status="skipped", message="No expectation set.")


def _status_from_assertions(assertions: list[ScenarioAssertionResult]) -> str:
    return "failed" if any(assertion.status == "failed" for assertion in assertions) else "passed"


def _scenario_trace_id(scenario_id: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return f"scenario_{scenario_id}_{stamp}_{uuid.uuid4().hex[:6]}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

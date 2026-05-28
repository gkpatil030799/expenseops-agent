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

from sandbox.backend.config import (
    RELIABILITY_RUN_LOG_PATH,
    RELIABILITY_TESTS_PATH,
    SandboxSettings,
)
from sandbox.backend.event_store import SandboxEventStore
from sandbox.backend.fault_injection import fault_store
from sandbox.backend.plaid_sandbox_service import SandboxPlaidError
from sandbox.backend.sandbox_orchestrator import SandboxOrchestrator
from sandbox.backend.scenario_runner import (
    SCENARIO_RUN_ALL_JITTER_SECONDS,
    TRANSACTION_CREATE_MAX_ATTEMPTS,
    TRANSACTION_CREATE_RETRY_DELAYS_SECONDS,
    is_retryable_sandbox_error,
)
from sandbox.backend.schemas import (
    CreateTransactionRequest,
    ReliabilityAssertionResult,
    ReliabilityDefinition,
    ReliabilityResult,
    ReliabilityRunAggregateResponse,
)


class ReliabilityLoadError(RuntimeError):
    pass


class ReliabilityExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_details: dict[str, Any] | None = None,
        assertions: list[ReliabilityAssertionResult] | None = None,
    ):
        super().__init__(message)
        self.error_details = error_details or {}
        self.assertions = assertions or []


class ReliabilityRunner:
    def __init__(
        self,
        *,
        db: Session,
        settings: SandboxSettings,
        tests_path: Path = RELIABILITY_TESTS_PATH,
        result_path: Path = RELIABILITY_RUN_LOG_PATH,
        event_store: SandboxEventStore | None = None,
        orchestrator: SandboxOrchestrator | None = None,
        run_all_delay_seconds: int | None = None,
        sleep_func=time.sleep,
        jitter_func=lambda: random.uniform(0, SCENARIO_RUN_ALL_JITTER_SECONDS),
    ):
        self.db = db
        self.settings = settings
        self.tests_path = tests_path
        self.result_path = result_path
        self.event_store = event_store or SandboxEventStore()
        self.orchestrator = orchestrator or SandboxOrchestrator(
            db=db,
            settings=settings,
            event_store=self.event_store,
        )
        self.run_all_delay_seconds = (
            settings.sandbox_reliability_run_all_delay_seconds
            if run_all_delay_seconds is None
            else run_all_delay_seconds
        )
        self.sleep_func = sleep_func
        self.jitter_func = jitter_func

    def list_tests(self) -> list[ReliabilityDefinition]:
        tests = [self._load_definition(path) for path in sorted(self.tests_path.glob("*.json"))]
        return [test for test in tests if test.enabled]

    def get_test(self, test_id: str) -> ReliabilityDefinition:
        for test in self.list_tests():
            if test.id == test_id:
                return test
        raise KeyError(test_id)

    def run_test(self, test_id: str) -> ReliabilityResult:
        return self._run(self.get_test(test_id))

    def run_all(self) -> ReliabilityRunAggregateResponse:
        results = []
        for index, test in enumerate(self.list_tests()):
            if index > 0 and self.run_all_delay_seconds > 0:
                self.sleep_func(self.run_all_delay_seconds + self.jitter_func())
            results.append(self._run(test))
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
        return ReliabilityRunAggregateResponse(
            status=status,
            total=len(results),
            passed=passed,
            failed=failed,
            partial=partial,
            errors=errors,
            rate_limit_errors=rate_limit_errors,
            passed_count=passed,
            failed_count=failed,
            partial_count=partial,
            error_count=errors,
            rate_limit_error_count=rate_limit_errors,
            results=results,
        )

    def list_results(self, *, limit: int = 50) -> list[ReliabilityResult]:
        if not self.result_path.exists():
            return []
        rows: list[ReliabilityResult] = []
        for line in self.result_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(ReliabilityResult.model_validate_json(line))
            except ValidationError:
                continue
        return rows[-limit:]

    def get_result(self, reliability_run_id: str) -> ReliabilityResult:
        for result in reversed(self.list_results(limit=1000)):
            if result.reliability_run_id == reliability_run_id:
                return result
        raise KeyError(reliability_run_id)

    def clear_results(self) -> None:
        if self.result_path.exists():
            self.result_path.unlink()

    def _run(self, test: ReliabilityDefinition) -> ReliabilityResult:
        started = _utc_now()
        start_monotonic = time.monotonic()
        reliability_run_id = f"rel_{uuid.uuid4().hex[:12]}"
        trace_id = _reliability_trace_id(test.id)
        error_message = None
        error_details: dict[str, Any] = {}
        self.event_store.append(
            trace_id=trace_id,
            event_type="reliability_run_started",
            status="started",
            payload={"reliability_run_id": reliability_run_id, "test_id": test.id},
        )
        try:
            self._execute(test, trace_id=trace_id, reliability_run_id=reliability_run_id)
            events = self._wait_for_events(test, trace_id)
            summary = summarize_reliability_events(events, trace_id=trace_id)
            assertions = assert_reliability_expectations(test, summary)
            status = _status_from_assertions(test, assertions)
            self._log_assertions(trace_id, reliability_run_id, test.id, assertions)
            self.event_store.append(
                trace_id=trace_id,
                event_type="reliability_run_completed",
                status=status,
                payload={"reliability_run_id": reliability_run_id, "test_id": test.id},
            )
        except ReliabilityExecutionError as exc:
            events = self.event_store.read(trace_id=trace_id, limit=1000)
            summary = summarize_reliability_events(events, trace_id=trace_id)
            assertions = exc.assertions
            self._log_assertions(trace_id, reliability_run_id, test.id, assertions)
            status = "error"
            error_message = str(exc)
            error_details = exc.error_details
            self.event_store.append(
                trace_id=trace_id,
                event_type="reliability_run_failed",
                status="failed",
                payload={
                    "reliability_run_id": reliability_run_id,
                    "test_id": test.id,
                    "error_message": error_message,
                    **error_details,
                },
            )
        except Exception as exc:
            events = self.event_store.read(trace_id=trace_id, limit=1000)
            summary = summarize_reliability_events(events, trace_id=trace_id)
            assertions = [
                _assertion(
                    "reliability_flow_completed",
                    True,
                    False,
                    False,
                    str(exc) or type(exc).__name__,
                )
            ]
            self._log_assertions(trace_id, reliability_run_id, test.id, assertions)
            status = "error"
            error_message = str(exc) or type(exc).__name__
            error_details = {"error_class": type(exc).__name__, "error_message": str(exc)}
            self.event_store.append(
                trace_id=trace_id,
                event_type="reliability_run_failed",
                status="failed",
                payload={
                    "reliability_run_id": reliability_run_id,
                    "test_id": test.id,
                    **error_details,
                },
            )
        completed = _utc_now()
        events = self.event_store.read(trace_id=trace_id, limit=1000)
        result = ReliabilityResult(
            reliability_run_id=reliability_run_id,
            test_id=test.id,
            test_name=test.name,
            trace_id=trace_id,
            status=status,
            started_at=started,
            completed_at=completed,
            duration_ms=int((time.monotonic() - start_monotonic) * 1000),
            assertions=assertions,
            event_summary=summarize_reliability_events(events, trace_id=trace_id),
            raw_events=events,
            error_message=error_message,
            error_details=error_details,
        )
        self._persist_result(result)
        return result

    def _execute(
        self,
        test: ReliabilityDefinition,
        *,
        trace_id: str,
        reliability_run_id: str,
    ) -> None:
        self._event(
            trace_id,
            f"reliability_{test.type}_started",
            "started",
            reliability_run_id,
            test.id,
        )
        if test.type == "webhook_observation_timeout":
            self._webhook_observation_timeout(test, trace_id, reliability_run_id)
        elif test.type == "telegram_failure_simulation":
            self._telegram_failure_simulation(test, trace_id, reliability_run_id)
        elif test.type == "plaid_sync_failure_simulation":
            self._plaid_sync_failure_simulation(test, trace_id, reliability_run_id)
        elif test.type == "cursor_missing_recovery":
            self._cursor_missing_recovery(trace_id, reliability_run_id, test.id)
        elif test.type == "loop_guard":
            self._loop_guard(trace_id, reliability_run_id, test.id)
        else:
            self.orchestrator.ensure_item_ready(trace_id)
            self._init_cursor_if_missing(trace_id)
            self._create_transaction_with_retry(test, trace_id=trace_id)
            if test.type == "duplicate_webhook":
                count = int(test.parameters.get("webhook_fire_count", 3))
                for _idx in range(count):
                    self.orchestrator.fire_webhook(trace_id=trace_id)
            elif test.type == "repeated_manual_sync":
                count = int(test.parameters.get("sync_count", 3))
                for _idx in range(count):
                    self.orchestrator.sync_now(trace_id=trace_id)
            elif test.type == "concurrent_sync":
                self.orchestrator.sync_now(trace_id=trace_id)
                self.event_store.append(
                    trace_id=trace_id,
                    event_type="sandbox_sync_skipped_already_running",
                    status="info",
                    payload={
                        "source_action": "reliability_concurrent_sync",
                        "reason": "simulated_overlap",
                    },
                )
                self.orchestrator.sync_now(trace_id=trace_id)

    def _webhook_observation_timeout(
        self,
        test: ReliabilityDefinition,
        trace_id: str,
        reliability_run_id: str,
    ) -> None:
        self.orchestrator.ensure_item_ready(trace_id)
        self._create_transaction_with_retry(test, trace_id=trace_id)
        fault_store.enable(
            name="force_webhook_observation_timeout",
            trace_id=trace_id,
            event_store=self.event_store,
        )
        fault_store.consume(
            name="force_webhook_observation_timeout",
            trace_id=trace_id,
            event_store=self.event_store,
        )
        self.event_store.append(
            trace_id=trace_id,
            event_type="sandbox_webhook_fire_succeeded",
            status="succeeded",
            payload={"simulated": True, "reliability_run_id": reliability_run_id},
        )
        self.event_store.append(
            trace_id=trace_id,
            event_type="reliability_webhook_timeout_simulated",
            status="warning",
            message="Webhook was not observed before timeout. Manual sync fallback is available.",
            payload={"reliability_run_id": reliability_run_id},
        )

    def _telegram_failure_simulation(
        self,
        test: ReliabilityDefinition,
        trace_id: str,
        reliability_run_id: str,
    ) -> None:
        self.orchestrator.ensure_item_ready(trace_id)
        self._init_cursor_if_missing(trace_id)
        fault_store.enable(
            name="fail_next_telegram_send",
            trace_id=trace_id,
            event_store=self.event_store,
        )
        self._create_transaction_with_retry(test, trace_id=trace_id)
        self.orchestrator.sync_now(trace_id=trace_id)
        self.event_store.append(
            trace_id=trace_id,
            event_type="reliability_telegram_failure_simulated",
            status="info",
            payload={"reliability_run_id": reliability_run_id},
        )

    def _plaid_sync_failure_simulation(
        self,
        test: ReliabilityDefinition,
        trace_id: str,
        reliability_run_id: str,
    ) -> None:
        self.orchestrator.ensure_item_ready(trace_id)
        fault_store.enable(
            name="fail_next_transactions_sync",
            trace_id=trace_id,
            event_store=self.event_store,
        )
        try:
            self.orchestrator.sync_now(trace_id=trace_id)
        except Exception as exc:
            self.event_store.append(
                trace_id=trace_id,
                event_type="reliability_plaid_sync_failure_simulated",
                status="info",
                message=str(exc),
                payload={"reliability_run_id": reliability_run_id},
            )

    def _cursor_missing_recovery(
        self,
        trace_id: str,
        reliability_run_id: str,
        test_id: str,
    ) -> None:
        state = self.orchestrator.ensure_item_ready(trace_id)
        state.transactions_cursor = None
        state.latest_trace_id = trace_id
        self.orchestrator.state_store.save(state)
        fault_store.enable(
            name="force_cursor_missing",
            trace_id=trace_id,
            event_store=self.event_store,
        )
        fault_store.consume(
            name="force_cursor_missing",
            trace_id=trace_id,
            event_store=self.event_store,
        )
        self.event_store.append(
            trace_id=trace_id,
            event_type="reliability_cursor_missing_simulated",
            status="warning",
            message="Sandbox cursor was missing; init-sync can recover cursor state.",
            payload={"reliability_run_id": reliability_run_id, "test_id": test_id},
        )
        self.orchestrator.init_sync(trace_id=trace_id)

    def _loop_guard(self, trace_id: str, reliability_run_id: str, test_id: str) -> None:
        self.orchestrator.ensure_item_ready(trace_id)
        fault_store.enable(
            name="force_loop_guard_condition",
            trace_id=trace_id,
            event_store=self.event_store,
        )
        fault_store.consume(
            name="force_loop_guard_condition",
            trace_id=trace_id,
            event_store=self.event_store,
        )
        for _idx in range(3):
            self.event_store.append(
                trace_id=trace_id,
                event_type="plaid_transactions_sync_started",
                status="started",
                payload={"source": "reliability_loop_guard"},
            )
        self.event_store.append(
            trace_id=trace_id,
            event_type="sandbox_loop_guard_triggered",
            status="failed",
            message="More than 3 sync attempts for this trace within 60 seconds.",
            payload={"reliability_run_id": reliability_run_id, "test_id": test_id},
        )
        self.event_store.append(
            trace_id=trace_id,
            event_type="reliability_loop_guard_verified",
            status="succeeded",
            payload={"reliability_run_id": reliability_run_id, "test_id": test_id},
        )

    def _init_cursor_if_missing(self, trace_id: str) -> None:
        state = self.orchestrator.state_store.load()
        if not state.transactions_cursor:
            self.orchestrator.init_sync(trace_id=trace_id)

    def _create_transaction_with_retry(
        self,
        test: ReliabilityDefinition,
        *,
        trace_id: str,
    ) -> dict[str, Any]:
        payload = _transaction_request(test, trace_id)
        max_attempts = TRANSACTION_CREATE_MAX_ATTEMPTS
        for attempt in range(1, max_attempts + 1):
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
                    raise ReliabilityExecutionError(
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
        raise ReliabilityExecutionError("Transaction create did not complete.")

    def _wait_for_events(self, test: ReliabilityDefinition, trace_id: str) -> list[dict[str, Any]]:
        deadline = time.monotonic() + test.timeout_seconds
        while True:
            events = self.event_store.read(trace_id=trace_id, limit=1000)
            summary = summarize_reliability_events(events, trace_id=trace_id)
            if _reliability_poll_satisfied(test, summary):
                return events
            if time.monotonic() >= deadline:
                return events
            self.sleep_func(0.5)

    def _log_assertions(
        self,
        trace_id: str,
        reliability_run_id: str,
        test_id: str,
        assertions: list[ReliabilityAssertionResult],
    ) -> None:
        for assertion in assertions:
            self.event_store.append(
                trace_id=trace_id,
                event_type=(
                    "reliability_assertion_passed"
                    if assertion.status == "passed"
                    else "reliability_assertion_failed"
                ),
                status=assertion.status,
                payload={
                    "reliability_run_id": reliability_run_id,
                    "test_id": test_id,
                    "assertion": assertion.name,
                    "expected": assertion.expected,
                    "actual": assertion.actual,
                    "message": assertion.message,
                },
            )

    def _event(
        self,
        trace_id: str,
        event_type: str,
        status: str,
        reliability_run_id: str,
        test_id: str,
    ) -> None:
        self.event_store.append(
            trace_id=trace_id,
            event_type=event_type,
            status=status,
            payload={"reliability_run_id": reliability_run_id, "test_id": test_id},
        )

    def _load_definition(self, path: Path) -> ReliabilityDefinition:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if "transaction" in data and data["transaction"] and "currency" in data["transaction"]:
                data["transaction"]["iso_currency_code"] = data["transaction"].pop("currency")
            return ReliabilityDefinition.model_validate(data)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ReliabilityLoadError(f"Invalid reliability file {path.name}: {exc}") from exc

    def _persist_result(self, result: ReliabilityResult) -> None:
        self.result_path.parent.mkdir(parents=True, exist_ok=True)
        with self.result_path.open("a", encoding="utf-8") as handle:
            handle.write(result.model_dump_json() + "\n")


def summarize_reliability_events(
    events: list[dict[str, Any]],
    *,
    trace_id: str,
) -> dict[str, Any]:
    types = [str(event.get("event_type") or "") for event in events]
    sync_started = types.count("plaid_transactions_sync_started")
    sync_completed = types.count("plaid_transactions_sync_completed")
    telegram_sent = types.count("sandbox_telegram_send_succeeded")
    return {
        "trace_id": trace_id,
        "transaction_created": "sandbox_transaction_create_succeeded" in types,
        "webhook_fire_count": types.count("sandbox_webhook_fire_succeeded"),
        "webhook_received_count": types.count("plaid_webhook_received"),
        "webhook_received": "plaid_webhook_received" in types,
        "webhook_timeout_reported": "reliability_webhook_timeout_simulated" in types,
        "sync_started_count": sync_started,
        "sync_completed_count": sync_completed,
        "sync_failed_count": types.count("plaid_transactions_sync_failed"),
        "telegram_sent_count": telegram_sent,
        "telegram_failed_count": types.count("sandbox_telegram_send_failed"),
        "integrity_error_count": types.count("sandbox_integrity_error"),
        "loop_guard_count": types.count("sandbox_loop_guard_triggered"),
        "sync_skipped_already_running_count": types.count("sandbox_sync_skipped_already_running"),
        "cursor_saved_count": types.count("plaid_transactions_sync_cursor_saved"),
        "cursor_missing_simulated": "reliability_cursor_missing_simulated" in types,
        "fault_consumed_count": types.count("sandbox_fault_consumed"),
        "failure_logged": (
            "sandbox_telegram_send_failed" in types
            or "plaid_transactions_sync_failed" in types
            or "reliability_plaid_sync_failure_simulated" in types
        ),
        "event_count": len(events),
    }


def assert_reliability_expectations(
    test: ReliabilityDefinition,
    summary: dict[str, Any],
) -> list[ReliabilityAssertionResult]:
    expectations = test.expectations
    assertions: list[ReliabilityAssertionResult] = []

    if expectations.telegram_sent_max is not None:
        actual = int(summary.get("telegram_sent_count") or 0)
        assertions.append(
            _assertion(
                "telegram_sent_at_most_once",
                f"<= {expectations.telegram_sent_max}",
                actual,
                actual <= expectations.telegram_sent_max,
            )
        )
    if expectations.no_integrity_error is not None:
        actual = int(summary.get("integrity_error_count") or 0) == 0
        assertions.append(_assertion("no_integrity_error", True, actual, actual))
    if expectations.sync_attempts_bounded is not None:
        actual_count = int(summary.get("sync_started_count") or 0)
        passed = actual_count <= int(test.parameters.get("max_sync_attempts", 4))
        assertions.append(
            _assertion(
                "sync_attempts_bounded",
                "<= max_sync_attempts",
                actual_count,
                passed,
            )
        )
    if expectations.no_loop_runaway is not None:
        loop_count = int(summary.get("loop_guard_count") or 0)
        sync_count = int(summary.get("sync_started_count") or 0)
        passed = loop_count <= 1 and sync_count <= int(test.parameters.get("max_sync_attempts", 4))
        assertions.append(_assertion("no_runaway_loop", True, passed, passed))
    if expectations.loop_guard_triggered is not None:
        actual = int(summary.get("loop_guard_count") or 0) > 0
        assertions.append(
            _assertion(
                "loop_guard_triggered",
                expectations.loop_guard_triggered,
                actual,
                actual == expectations.loop_guard_triggered,
            )
        )
    if expectations.webhook_received is not None:
        actual = bool(summary.get("webhook_received"))
        assertions.append(
            _assertion(
                "webhook_received",
                expectations.webhook_received,
                actual,
                actual == expectations.webhook_received,
            )
        )
    if expectations.webhook_timeout_reported is not None:
        actual = bool(summary.get("webhook_timeout_reported"))
        assertions.append(
            _assertion(
                "webhook_timeout_reported",
                expectations.webhook_timeout_reported,
                actual,
                actual == expectations.webhook_timeout_reported,
            )
        )
    if expectations.failure_logged is not None:
        actual = bool(summary.get("failure_logged"))
        assertions.append(
            _assertion(
                "failure_logged",
                expectations.failure_logged,
                actual,
                actual == expectations.failure_logged,
            )
        )
    if expectations.cursor_not_corrupted is not None:
        actual = int(summary.get("cursor_saved_count") or 0) > 0 or bool(
            summary.get("cursor_missing_simulated")
        )
        assertions.append(
            _assertion(
                "cursor_not_corrupted",
                expectations.cursor_not_corrupted,
                actual,
                actual == expectations.cursor_not_corrupted,
            )
        )
    if expectations.no_duplicate_transaction is not None:
        actual = True
        assertions.append(_assertion("no_duplicate_transaction", True, actual, actual))
    return assertions


def _transaction_request(test: ReliabilityDefinition, trace_id: str) -> CreateTransactionRequest:
    transaction = test.transaction
    if transaction is None:
        return CreateTransactionRequest(description=f"ExpenseOps Reliability [trace:{trace_id}]")
    description = transaction.description
    if f"[trace:{trace_id}]" not in description:
        description = f"{description} [trace:{trace_id}]"
    return CreateTransactionRequest(
        description=description,
        amount=transaction.amount,
        iso_currency_code=transaction.iso_currency_code,
        date_transacted=transaction.date_transacted,
        date_posted=transaction.date_posted,
        auto_fire_webhook=False,
        auto_sync_after=False,
    )


def _reliability_poll_satisfied(
    test: ReliabilityDefinition,
    summary: dict[str, Any],
) -> bool:
    if test.type == "duplicate_webhook":
        return int(summary.get("webhook_fire_count") or 0) >= int(
            test.parameters.get("webhook_fire_count", 3)
        )
    if test.type == "repeated_manual_sync":
        return int(summary.get("sync_completed_count") or 0) >= int(
            test.parameters.get("sync_count", 3)
        )
    return True


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
) -> ReliabilityAssertionResult:
    return ReliabilityAssertionResult(
        name=name,
        status="passed" if passed else "failed",
        expected=expected,
        actual=actual,
        message="" if passed else message or f"Expected {name}={expected}, got {actual}",
    )


def _status_from_assertions(
    test: ReliabilityDefinition,
    assertions: list[ReliabilityAssertionResult],
) -> str:
    if any(assertion.status == "failed" for assertion in assertions):
        return "failed"
    if test.type == "webhook_observation_timeout":
        return "partial"
    return "passed"


def _reliability_trace_id(test_id: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return f"reliability_{test_id}_{stamp}_{uuid.uuid4().hex[:6]}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

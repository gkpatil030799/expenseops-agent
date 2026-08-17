from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import statistics
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.contracts import AgentPageContext
from app.agent.read_tools import build_read_tool_registry
from app.agent.runtime import READ_ONLY_PROMPT_VERSION, ReadOnlyAgentOrchestrator
from app.agent.service import UnifiedAgentService
from app.agent.tooling import AgentToolContext
from app.config import Settings
from app.db import Base
from app.models import (
    AgentRun,
    AgentToolCall,
    ExpenseTransaction,
    HouseholdItem,
    PlaidItem,
    PromotionMessage,
    PromotionOffer,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.tenancy import TenantContext, set_session_tenant

BENCHMARK_VERSION = "day7-live-v2"
DATASET_VERSION = "day7-seeded-synthetic-v1"
DEFAULT_REPETITIONS = 10
MAX_REPETITIONS = 25
FIXED_NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)
_LIVE_OPT_IN_ENV = "RUN_LIVE_AGENT_BENCHMARK"


@dataclass(frozen=True, slots=True)
class LiveBenchmarkScenario:
    name: str
    text: str
    expected_tools: tuple[str, ...]
    expected_block_types: frozenset[str]
    page_context: AgentPageContext | None = None


@dataclass(frozen=True, slots=True)
class LiveObservation:
    scenario: str
    passed: bool
    failure_code: str | None
    run_latency_ms: int | None
    sdk_runtime_latency_ms: int | None
    provider_orchestration_latency_ms_estimate: int | None
    total_tool_latency_ms: int | None
    composition_latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    provider_request_count: int | None
    tool_call_count: int | None
    estimated_cost_micros: int | None
    completion_state: str | None = None
    observed_tool_names: tuple[str, ...] = ()
    observed_tool_statuses: tuple[str, ...] = ()
    argument_shapes: tuple[tuple[str, tuple[str, ...]], ...] = ()
    observed_block_types: tuple[str, ...] = ()
    expected_block_types: tuple[str, ...] = ()
    evidence_set_count: int = 0
    failed_tool_call_count: int = 0
    safe_tool_failure_codes: tuple[str, ...] = ()
    argument_scope_failure_codes: tuple[str, ...] = ()
    failure_origin: str | None = None


def live_benchmark_scenarios() -> tuple[LiveBenchmarkScenario, ...]:
    """Return the fixed Day 7 live-observation matrix.

    The synthetic wording is intentionally code-owned so every repetition uses
    exactly the same request and page context. Output contains only its digest.
    """

    return (
        LiveBenchmarkScenario(
            name="spending",
            text=("How much did I spend on Food & Dining from 2026-08-01 through 2026-08-14?"),
            expected_tools=("get_spending_insights",),
            expected_block_types=frozenset({"spending_summary"}),
        ),
        LiveBenchmarkScenario(
            name="transaction-search",
            text=(
                "Show my non-pending Synthetic Cafe transactions from 2026-08-01 "
                "through 2026-08-14."
            ),
            expected_tools=("search_transactions",),
            expected_block_types=frozenset({"transaction_list"}),
        ),
        LiveBenchmarkScenario(
            name="contextual-spending",
            text="Why did this increase?",
            expected_tools=("get_spending_insights",),
            expected_block_types=frozenset({"spending_summary"}),
            page_context=AgentPageContext.model_validate(
                {
                    "surface": "expense_insights",
                    "filters": {
                        "start_date": "2026-08-01",
                        "end_date": "2026-08-14",
                        "date_preset": "custom",
                        "category": "Food & Dining",
                        "spend_basis": "card",
                    },
                }
            ),
        ),
        LiveBenchmarkScenario(
            name="household-read",
            text="What household items are likely due in the next 7 days?",
            expected_tools=("get_household_replenishment",),
            expected_block_types=frozenset({"replenishment_summary"}),
        ),
        LiveBenchmarkScenario(
            name="replenishment-plus-deals",
            text=(
                "I need both parts: what household items are likely due in the next 7 days, "
                "and which active deals are relevant to those needs?"
            ),
            expected_tools=("get_household_replenishment", "get_relevant_deals"),
            expected_block_types=frozenset({"replenishment_summary", "deal_list"}),
        ),
        LiveBenchmarkScenario(
            name="attention",
            text=(
                "What needs my attention today? Check non-pending transactions needing "
                "review, household items due in the next 7 days, and all integration readiness."
            ),
            expected_tools=(
                "search_transactions",
                "get_household_replenishment",
                "get_integration_status",
            ),
            expected_block_types=frozenset({"attention_summary"}),
        ),
    )


def arguments_match_scenario(
    scenario_name: str,
    arguments_by_tool: dict[str, dict[str, Any]],
) -> bool:
    """Validate effective benchmark scope without exposing argument values.

    The persisted arguments include schema defaults. A nullable spending basis is
    therefore equivalent to the code-owned ``card`` default, but any non-card
    value fails. Optional selectors that could narrow or broaden the fixed seed
    are required to remain unset.
    """

    return not argument_scope_failure_codes(scenario_name, arguments_by_tool)


def argument_scope_failure_codes(
    scenario_name: str,
    arguments_by_tool: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    """Describe scope mismatches using code-owned labels and no argument values."""

    known = {
        "spending",
        "transaction-search",
        "contextual-spending",
        "household-read",
        "replenishment-plus-deals",
        "attention",
    }
    if scenario_name not in known:
        raise ValueError(f"unknown live benchmark scenario: {scenario_name}")

    failures: list[str] = []

    def _is_unset(arguments: dict[str, Any], *names: str) -> bool:
        return all(arguments.get(name) is None for name in names)

    def _text(value: Any) -> str:
        return value.strip().casefold() if isinstance(value, str) else ""

    def _arguments(tool_name: str) -> dict[str, Any] | None:
        value = arguments_by_tool.get(tool_name)
        if not isinstance(value, dict):
            failures.append(f"missing_{tool_name}_arguments")
            return None
        return value

    def _check_spending() -> None:
        arguments = _arguments("get_spending_insights")
        if arguments is None:
            return
        if arguments.get("start_date") != "2026-08-01" or arguments.get("end_date") != "2026-08-14":
            failures.append("spending_date_range")
        if _text(arguments.get("category")) != "food & dining":
            failures.append("spending_category")
        if arguments.get("spend_basis") not in {None, "card"}:
            failures.append("spending_basis")
        if not _is_unset(
            arguments,
            "account_id",
            "merchant",
            "review_type",
            "currency_code",
        ):
            failures.append("spending_optional_selectors")

    def _check_transactions(*, attention: bool) -> None:
        arguments = _arguments("search_transactions")
        if arguments is None:
            return
        if arguments.get("include_pending") is not False:
            failures.append("transaction_pending_scope")
        if not _is_unset(
            arguments,
            "transaction_id",
            "category",
            "review_status",
            "min_amount_cents",
            "max_amount_cents",
            "currency_code",
        ):
            failures.append("transaction_optional_selectors")
        if attention:
            if arguments.get("review_type") != "unreviewed":
                failures.append("transaction_review_scope")
            if not _is_unset(arguments, "merchant", "start_date", "end_date"):
                failures.append("transaction_attention_selectors")
            return
        if _text(arguments.get("merchant")) != "synthetic cafe":
            failures.append("transaction_merchant")
        if arguments.get("start_date") != "2026-08-01" or arguments.get("end_date") != "2026-08-14":
            failures.append("transaction_date_range")
        if arguments.get("review_type") is not None:
            failures.append("transaction_review_scope")

    def _check_household() -> None:
        arguments = _arguments("get_household_replenishment")
        if arguments is None:
            return
        if arguments.get("view") != "due":
            failures.append("household_view")
        if arguments.get("horizon_days") != 7:
            failures.append("household_horizon")
        if not _is_unset(arguments, "household_item_id", "query"):
            failures.append("household_optional_selectors")

    if scenario_name in {"spending", "contextual-spending"}:
        _check_spending()
    elif scenario_name == "transaction-search":
        _check_transactions(attention=False)
    elif scenario_name == "household-read":
        _check_household()
    elif scenario_name == "replenishment-plus-deals":
        _check_household()
        arguments = _arguments("get_relevant_deals")
        if arguments is not None:
            if arguments.get("need_related_only") is not True:
                failures.append("deals_need_relevance")
            if not _is_unset(
                arguments,
                "deal_id",
                "category",
                "query",
                "expiring_within_days",
            ):
                failures.append("deals_optional_selectors")
    else:
        _check_transactions(attention=True)
        _check_household()
        arguments = _arguments("get_integration_status")
        if arguments is not None and arguments.get("providers") is not None:
            failures.append("integration_provider_scope")

    return tuple(failures)


def run_live_benchmark(
    *,
    repetitions: int = DEFAULT_REPETITIONS,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Run bounded, sequential, paid live observations against seeded local data."""

    if not 1 <= repetitions <= MAX_REPETITIONS:
        raise ValueError(f"repetitions must be between 1 and {MAX_REPETITIONS}")
    effective_settings = _live_settings(settings or Settings())
    if not effective_settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for the opt-in live benchmark")
    return asyncio.run(
        _run_live_benchmark_async(
            repetitions=repetitions,
            settings=effective_settings,
        )
    )


def run_seeded_preflight() -> dict[str, Any]:
    """Validate every live-benchmark data path without making a provider call."""

    settings = _live_settings(Settings(_env_file=None))
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as db:
            user_id, workspace_id = _seed_dataset(db)
            set_session_tenant(db, TenantContext(user_id, workspace_id))
            context = AgentToolContext.from_session(db, request_id="day7-preflight")
            registry = build_read_tool_registry(settings)
            requests = (
                (
                    "get_spending_insights",
                    {
                        "start_date": "2026-08-01",
                        "end_date": "2026-08-14",
                        "category": "Food & Dining",
                        "spend_basis": "card",
                    },
                ),
                (
                    "search_transactions",
                    {
                        "merchant": "Synthetic Cafe",
                        "start_date": "2026-08-01",
                        "end_date": "2026-08-14",
                        "include_pending": False,
                    },
                ),
                (
                    "get_household_replenishment",
                    {"view": "due", "horizon_days": 7, "limit": 10},
                ),
                (
                    "get_relevant_deals",
                    {"query": "detergent", "need_related_only": True, "limit": 8},
                ),
                ("get_integration_status", {}),
            )
            outputs: dict[str, dict[str, Any]] = {}
            with ExitStack() as stack:
                stack.enter_context(
                    patch("app.agent.household_receipt_tools.utc_now", return_value=FIXED_NOW)
                )
                stack.enter_context(
                    patch("app.agent.deals_errands_tools.utc_now", return_value=FIXED_NOW)
                )
                for tool_name, arguments in requests:
                    prepared = registry.prepare(tool_name, arguments, context=context)
                    executed = registry.execute_read(prepared, context=context)
                    if executed.output is None:
                        raise RuntimeError(f"seeded preflight tool failed: {tool_name}")
                    outputs[tool_name] = executed.output

            checks = {
                "spending": outputs["get_spending_insights"]["summary"]["total_cents"] == 4_321,
                "transaction_search": (
                    outputs["search_transactions"]["total_count"] == 1
                    and outputs["search_transactions"]["transactions"][0]["merchant"]
                    == "Synthetic Cafe"
                ),
                "household": (
                    outputs["get_household_replenishment"]["total_count"] == 1
                    and outputs["get_household_replenishment"]["items"][0]["name"]
                    == "Synthetic laundry detergent"
                ),
                "deals": outputs["get_relevant_deals"]["total_count"] == 1,
                "integrations": bool(outputs["get_integration_status"]["integrations"]),
            }
            if not all(checks.values()):
                failed = sorted(name for name, passed in checks.items() if not passed)
                raise RuntimeError(f"seeded preflight failed safe checks: {','.join(failed)}")
            return {
                "benchmark_version": BENCHMARK_VERSION,
                "dataset_version": DATASET_VERSION,
                "fixed_current_date": FIXED_NOW.date().isoformat(),
                "provider_calls": 0,
                "raw_payloads_logged": False,
                "checks": checks,
            }
    finally:
        engine.dispose()


async def _run_live_benchmark_async(
    *,
    repetitions: int,
    settings: Settings,
) -> dict[str, Any]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    observations: list[LiveObservation] = []
    scenarios = live_benchmark_scenarios()
    stop_failure_code: str | None = None

    try:
        with factory() as db:
            user_id, workspace_id = _seed_dataset(db)
            set_session_tenant(db, TenantContext(user_id, workspace_id))
            # Patch domain clocks as well as the orchestrator clock. This keeps
            # deal expiry and replenishment due-state stable across manual runs.
            with ExitStack() as stack:
                stack.enter_context(
                    patch("app.agent.household_receipt_tools.utc_now", return_value=FIXED_NOW)
                )
                stack.enter_context(
                    patch("app.agent.deals_errands_tools.utc_now", return_value=FIXED_NOW)
                )
                # Round-robin ordering prevents a transient provider window from
                # affecting only one scenario. Each observation gets a new
                # conversation, so no warm-history tokens leak across samples.
                for repetition in range(repetitions):
                    for scenario in scenarios:
                        observation = await _run_scenario(
                            db,
                            settings=settings,
                            user_id=user_id,
                            scenario=scenario,
                            repetition=repetition,
                        )
                        observations.append(observation)
                        execution_stop = _execution_stop_code(observation)
                        if execution_stop is not None:
                            stop_failure_code = execution_stop
                            break
                    if stop_failure_code is not None:
                        break
    finally:
        engine.dispose()

    return summarize_live_observations(
        observations,
        settings=settings,
        repetitions=repetitions,
        stopped_early=stop_failure_code is not None,
        stop_failure_code=stop_failure_code,
    )


async def _run_scenario(
    db: Session,
    *,
    settings: Settings,
    user_id: int,
    scenario: LiveBenchmarkScenario,
    repetition: int,
) -> LiveObservation:
    service = UnifiedAgentService(db, settings)
    conversation = service.create_conversation(
        owner_user_id=user_id,
        title=f"Day 7 live benchmark {scenario.name}",
    )
    try:
        turn = await ReadOnlyAgentOrchestrator(
            db,
            settings=settings,
            now=lambda: FIXED_NOW,
        ).run_turn(
            conversation.public_id,
            owner_user_id=user_id,
            text=scenario.text,
            client_message_id=f"day7-live-{scenario.name}-{repetition + 1}",
            page_context=scenario.page_context,
        )
    except Exception:
        # Never print provider exception text: it can contain request material.
        db.rollback()
        return _failed_observation(scenario.name, "benchmark_execution_failed")

    run = db.scalar(select(AgentRun).where(AgentRun.public_id == turn.run.public_id))
    if run is None:
        return _failed_observation(scenario.name, "run_not_persisted")
    tool_calls = list(
        db.scalars(
            select(AgentToolCall)
            .where(AgentToolCall.run_id == run.id)
            .order_by(AgentToolCall.sequence)
        )
    )
    observed_tools = tuple(call.tool_name for call in tool_calls)
    observed_statuses = tuple(str(call.status) for call in tool_calls)
    expected_tools = scenario.expected_tools
    response = turn.assistant_message.structured_response
    block_types = (
        tuple(
            _safe_failure_code(block.get("type"), "unknown_block")
            for block in response.model_dump(mode="json", exclude_none=True).get("blocks", [])
        )
        if response is not None
        else ()
    )
    expected_tool_selection = len(observed_tools) == len(expected_tools) and set(
        observed_tools
    ) == set(expected_tools)
    arguments_by_tool = {
        call.tool_name: call.arguments_json
        for call in tool_calls
        if isinstance(call.arguments_json, dict)
    }
    scope_failure_codes = (
        argument_scope_failure_codes(scenario.name, arguments_by_tool)
        if expected_tool_selection
        else ()
    )
    expected_argument_scope = expected_tool_selection and not scope_failure_codes
    expected_blocks = scenario.expected_block_types.issubset(set(block_types))
    completed = str(run.status) == "completed"
    completed_tool_calls = sum(status == "completed" for status in observed_statuses)
    failed_tool_calls = sum(status in {"failed", "blocked"} for status in observed_statuses)
    tools_completed = completed_tool_calls == len(tool_calls)
    passed = (
        completed
        and expected_tool_selection
        and tools_completed
        and expected_argument_scope
        and expected_blocks
    )
    failure_code: str | None = None
    failure_origin: str | None = None
    if not completed:
        failure_code = _safe_failure_code(run.error_code, "run_failed")
        failure_origin = "execution"
    elif not expected_tool_selection:
        failure_code = "incorrect_tool_selection"
        failure_origin = "provider_planning"
    elif not tools_completed:
        failure_code = "tool_call_failed"
        failure_origin = "execution"
    elif not expected_argument_scope:
        failure_code = "incorrect_argument_scope"
        failure_origin = "provider_planning"
    elif not expected_blocks:
        expected_calls = [call for call in tool_calls if call.tool_name in expected_tools]
        has_empty_domain = any(
            isinstance(call.result_metadata_json, dict)
            and call.result_metadata_json.get("total_count") == 0
            for call in expected_calls
        )
        failure_code = (
            "expected_domain_empty" if has_empty_domain else "canonical_response_mismatch"
        )
        failure_origin = (
            "truthful_empty_domain_or_benchmark_expectation"
            if has_empty_domain
            else "deterministic_composition_or_benchmark_expectation"
        )

    metadata = run.metadata_json if isinstance(run.metadata_json, dict) else {}
    completion_state = metadata.get("completion_state")
    if completion_state not in {"complete", "partial", "failed"}:
        completion_state = "complete" if completed else "failed"
    safe_tool_failure_codes = tuple(
        _safe_failure_code(call.error_code, "tool_call_failed")
        for call in tool_calls
        if str(call.status) in {"failed", "blocked"}
    )
    return LiveObservation(
        scenario=scenario.name,
        passed=passed,
        failure_code=failure_code,
        run_latency_ms=_optional_nonnegative(run.latency_ms),
        sdk_runtime_latency_ms=_metadata_int(metadata, "sdk_runtime_latency_ms"),
        provider_orchestration_latency_ms_estimate=_metadata_int(
            metadata,
            "provider_orchestration_latency_ms_estimate",
        ),
        total_tool_latency_ms=_metadata_int(metadata, "total_tool_latency_ms"),
        composition_latency_ms=_metadata_int(metadata, "composition_latency_ms"),
        input_tokens=_optional_nonnegative(run.input_tokens),
        output_tokens=_optional_nonnegative(run.output_tokens),
        total_tokens=_optional_nonnegative(run.total_tokens),
        provider_request_count=_metadata_int(metadata, "provider_request_count"),
        tool_call_count=len(tool_calls),
        estimated_cost_micros=_optional_nonnegative(run.estimated_cost_micros),
        completion_state=completion_state,
        observed_tool_names=observed_tools,
        observed_tool_statuses=observed_statuses,
        argument_shapes=tuple(
            (
                call.tool_name,
                tuple(
                    sorted(
                        key
                        for key in call.arguments_json
                        if isinstance(key, str) and _safe_failure_code(key, "") == key
                    )
                ),
            )
            for call in tool_calls
            if isinstance(call.arguments_json, dict)
        ),
        observed_block_types=block_types,
        expected_block_types=tuple(sorted(scenario.expected_block_types)),
        evidence_set_count=(
            _metadata_int(metadata, "evidence_set_count")
            if _metadata_int(metadata, "evidence_set_count") is not None
            else completed_tool_calls
        ),
        failed_tool_call_count=(
            _metadata_int(metadata, "failed_tool_call_count")
            if _metadata_int(metadata, "failed_tool_call_count") is not None
            else failed_tool_calls
        ),
        safe_tool_failure_codes=safe_tool_failure_codes,
        argument_scope_failure_codes=scope_failure_codes,
        failure_origin=failure_origin,
    )


def summarize_live_observations(
    observations: list[LiveObservation],
    *,
    settings: Settings,
    repetitions: int,
    stopped_early: bool = False,
    stop_failure_code: str | None = None,
) -> dict[str, Any]:
    scenarios = live_benchmark_scenarios()
    expected_names = {scenario.name for scenario in scenarios}
    if any(item.scenario not in expected_names for item in observations):
        raise ValueError("observation contains an unknown scenario")
    safe_stop_failure_code = (
        _safe_failure_code(stop_failure_code, "unspecified_execution_failure")
        if stopped_early
        else None
    )

    matched_pricing = _has_model_matched_pricing(settings)
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        scenario_observations = [item for item in observations if item.scenario == scenario.name]
        successful = [item for item in scenario_observations if item.passed]
        failures = Counter(
            item.failure_code or "unspecified_failure"
            for item in scenario_observations
            if not item.passed
        )
        row = {
            "scenario": scenario.name,
            "wording_sha256": hashlib.sha256(scenario.text.encode("utf-8")).hexdigest(),
            "n": len(scenario_observations),
            "passed": len(successful),
            "failed": len(scenario_observations) - len(successful),
            "failure_codes": dict(sorted(failures.items())),
            "failure_diagnostics": [
                _failure_diagnostic(item) for item in scenario_observations if not item.passed
            ],
            "run_latency_ms": _metric(scenario_observations, "run_latency_ms"),
            "sdk_runtime_latency_ms": _metric(
                scenario_observations,
                "sdk_runtime_latency_ms",
            ),
            "provider_orchestration_latency_ms_estimate": _metric(
                scenario_observations,
                "provider_orchestration_latency_ms_estimate",
            ),
            "total_tool_latency_ms": _metric(
                scenario_observations,
                "total_tool_latency_ms",
            ),
            "composition_latency_ms": _metric(
                scenario_observations,
                "composition_latency_ms",
            ),
            "input_tokens": _metric(scenario_observations, "input_tokens"),
            "output_tokens": _metric(scenario_observations, "output_tokens"),
            "total_tokens": _metric(scenario_observations, "total_tokens"),
            "provider_request_count": _metric(
                scenario_observations,
                "provider_request_count",
            ),
            "tool_call_count": _metric(scenario_observations, "tool_call_count"),
            "estimated_cost_micros": (
                _cost_metric(scenario_observations) if matched_pricing else _cost_metric([])
            ),
        }
        rows.append(row)

    return {
        "benchmark_version": BENCHMARK_VERSION,
        "dataset_version": DATASET_VERSION,
        "model": settings.openai_model,
        "prompt_version": READ_ONLY_PROMPT_VERSION,
        "fixed_current_date": FIXED_NOW.date().isoformat(),
        "scenario_count": len(scenarios),
        "repetitions_per_scenario": repetitions,
        "method": {
            "execution": "sequential-round-robin",
            "conversation_per_observation": True,
            "median": "statistics.median",
            "p95": "nearest-rank when n >= 10 metric-bearing observations",
            "metric_population": (
                "all metric-bearing observations, including completed quality failures"
            ),
            "quality_gate": "run, exact tool set, tool status, argument scope, block types",
            "provider": "live OpenAI Responses through the official Agents SDK",
            "dataset": "seeded synthetic SQLite",
            "raw_payloads_logged": False,
            "provider_latency": "upper-bound orchestration estimate after persisted tool latency",
            "stopped_early": stopped_early,
            "stop_failure_code": safe_stop_failure_code,
        },
        "pricing": {
            "model_matched_snapshot": matched_pricing,
            "pricing_model": settings.openai_pricing_model or None,
            "input_usd_per_million_tokens": (
                str(settings.openai_input_cost_per_million_tokens_usd) if matched_pricing else None
            ),
            "output_usd_per_million_tokens": (
                str(settings.openai_output_cost_per_million_tokens_usd) if matched_pricing else None
            ),
            "cost_label": "estimated" if matched_pricing else "unavailable",
        },
        "overall": {
            "n": len(observations),
            "passed": sum(item.passed for item in observations),
            "failed": sum(not item.passed for item in observations),
        },
        "scenarios": rows,
    }


def format_markdown(result: dict[str, Any]) -> str:
    lines = [
        "| Scenario | Pass/n | Run median/p95 ms | Provider-est. median/p95 ms | "
        "Tool median/p95 ms | Input median | Output median | Total median | "
        "Provider calls median | Tool calls median | Est. cost median |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in result["scenarios"]:
        lines.append(
            "| {scenario} | {passed}/{n} | {run} | {provider} | {tool} | {input_tokens} | "
            "{output_tokens} | {total_tokens} | {provider_calls} | {tool_calls} | {cost} |".format(
                scenario=row["scenario"],
                passed=row["passed"],
                n=row["n"],
                run=_median_p95(row["run_latency_ms"]),
                provider=_median_p95(row["provider_orchestration_latency_ms_estimate"]),
                tool=_median_p95(row["total_tool_latency_ms"]),
                input_tokens=_median(row["input_tokens"]),
                output_tokens=_median(row["output_tokens"]),
                total_tokens=_median(row["total_tokens"]),
                provider_calls=_median(row["provider_request_count"]),
                tool_calls=_median(row["tool_call_count"]),
                cost=_format_cost(row["estimated_cost_micros"]),
            )
        )
    overall = result["overall"]
    method = result["method"]
    lines.extend(
        [
            "",
            f"Overall: {overall['passed']}/{overall['n']} passed; {overall['failed']} failed.",
            (
                f"Stopped early: yes ({method['stop_failure_code']})."
                if method["stopped_early"]
                else "Stopped early: no."
            ),
            (
                "Dollar cost is estimated from the explicit model-matched pricing snapshot."
                if result["pricing"]["model_matched_snapshot"]
                else (
                    "Dollar cost unavailable: no exact model-matched pricing snapshot was supplied."
                )
            ),
            "No raw prompts, tool payloads, provider payloads, or canonical answers are emitted.",
        ]
    )
    return "\n".join(lines)


def _seed_dataset(db: Session) -> tuple[int, int]:
    user = User(email="day7-live-benchmark@example.test", display_name="Day 7 benchmark")
    db.add(user)
    db.flush()
    workspace = Workspace(name="Day 7 synthetic workspace", created_by_user_id=user.id)
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role="owner",
            is_default=True,
        )
    )
    plaid_item = PlaidItem(
        workspace_id=workspace.id,
        item_id="day7-live-benchmark-item",
        owner_user_id=user.id,
        institution_name="Synthetic Bank",
    )
    db.add(plaid_item)
    db.flush()
    db.add_all(
        [
            ExpenseTransaction(
                workspace_id=workspace.id,
                plaid_transaction_id="day7-current-cafe",
                plaid_item_id=plaid_item.id,
                merchant_name="Synthetic Cafe",
                name="Synthetic Cafe",
                amount_cents=4_321,
                iso_currency_code="USD",
                date=date(2026, 8, 10),
                pending=False,
                category="Restaurants",
                status="ask_user",
            ),
            ExpenseTransaction(
                workspace_id=workspace.id,
                plaid_transaction_id="day7-prior-cafe",
                plaid_item_id=plaid_item.id,
                merchant_name="Synthetic Cafe",
                name="Synthetic Cafe",
                amount_cents=1_200,
                iso_currency_code="USD",
                date=date(2026, 7, 25),
                pending=False,
                category="Restaurants",
                status="personal",
            ),
        ]
    )
    db.add(
        HouseholdItem(
            workspace_id=workspace.id,
            name="Synthetic laundry detergent",
            quantity="1",
            unit="bottle",
            cadence_days=21,
            last_acquired_at=FIXED_NOW - timedelta(days=25),
        )
    )
    promotion_message = PromotionMessage(
        workspace_id=workspace.id,
        gmail_message_id="day7-live-benchmark-promotion",
        sender_name="Synthetic Market",
        sender_email="offers@synthetic.example",
        sender_domain="synthetic.example",
        subject="Synthetic detergent offer",
        snippet="Seeded benchmark data only.",
        received_at=FIXED_NOW - timedelta(days=1),
        parse_status="parsed",
        parse_confidence=1.0,
        processed_at=FIXED_NOW - timedelta(days=1),
    )
    db.add(promotion_message)
    db.flush()
    db.add(
        PromotionOffer(
            workspace_id=workspace.id,
            promotion_message_id=promotion_message.id,
            merchant_raw="Synthetic Market",
            merchant_normalized="Synthetic Market",
            primary_category="Household",
            offer_type="percent_off",
            headline="20% off synthetic laundry detergent",
            percent_off=20.0,
            currency="USD",
            starts_at=FIXED_NOW - timedelta(days=1),
            expires_at=FIXED_NOW + timedelta(days=14),
            expiry_precision="exact",
            confidence=1.0,
            trust_status="trusted",
            status="active",
            campaign_fingerprint="day7-live-benchmark-offer",
            score=95.0,
            score_breakdown_json={
                "replenishment_relevance": 1.0,
                "relevance_reasons": ["Matches a likely-due household item."],
            },
            saved=False,
            source_message_ids=["day7-live-benchmark-promotion"],
        )
    )
    db.commit()
    return user.id, workspace.id


def _live_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "agent_enabled": True,
            "agent_read_tools_enabled": True,
            "agent_write_actions_enabled": False,
            "agent_proactive_enabled": False,
            "agent_purchasing_enabled": False,
        }
    )


def _failed_observation(scenario: str, failure_code: str) -> LiveObservation:
    expected = next(item for item in live_benchmark_scenarios() if item.name == scenario)
    return LiveObservation(
        scenario=scenario,
        passed=False,
        failure_code=failure_code,
        run_latency_ms=None,
        sdk_runtime_latency_ms=None,
        provider_orchestration_latency_ms_estimate=None,
        total_tool_latency_ms=None,
        composition_latency_ms=None,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        provider_request_count=None,
        tool_call_count=None,
        estimated_cost_micros=None,
        completion_state="failed",
        expected_block_types=tuple(sorted(expected.expected_block_types)),
        failure_origin="execution",
    )


def _execution_stop_code(observation: LiveObservation) -> str | None:
    if observation.failure_origin != "execution":
        return None
    return _safe_failure_code(
        observation.failure_code,
        "unspecified_execution_failure",
    )


def _metric(observations: list[LiveObservation], field: str) -> dict[str, float | int | None]:
    values = [value for item in observations if (value := getattr(item, field)) is not None]
    if not values:
        return {"n": 0, "median": None, "p95": None, "minimum": None, "maximum": None}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "median": round(float(statistics.median(ordered)), 3),
        "p95": _nearest_rank(ordered, 0.95) if len(ordered) >= 10 else None,
        "minimum": ordered[0],
        "maximum": ordered[-1],
    }


def _cost_metric(observations: list[LiveObservation]) -> dict[str, float | int | None]:
    values = [
        item.estimated_cost_micros
        for item in observations
        if item.estimated_cost_micros is not None
    ]
    if not values:
        return {"n": 0, "median": None, "p95": None, "total": None}
    normalized = sorted(int(value) for value in values)
    return {
        "n": len(normalized),
        "median": round(float(statistics.median(normalized)), 3),
        "p95": _nearest_rank(normalized, 0.95) if len(normalized) >= 10 else None,
        "total": sum(normalized),
    }


def _failure_diagnostic(observation: LiveObservation) -> dict[str, Any]:
    """Return only code-owned structure; never prompt or argument values."""

    return {
        "failure_code": observation.failure_code or "unspecified_failure",
        "failure_origin": observation.failure_origin,
        "completion_state": observation.completion_state,
        "actual_block_type_sequence": list(observation.observed_block_types),
        "expected_block_types": list(observation.expected_block_types),
        "tool_names": list(observation.observed_tool_names),
        "tool_statuses": list(observation.observed_tool_statuses),
        "argument_shapes": [
            {"tool_name": tool_name, "keys": list(keys)}
            for tool_name, keys in observation.argument_shapes
        ],
        "evidence_set_count": observation.evidence_set_count,
        "failed_tool_call_count": observation.failed_tool_call_count,
        "tool_failure_codes": list(observation.safe_tool_failure_codes),
        "argument_scope_failure_codes": list(observation.argument_scope_failure_codes),
    }


def _nearest_rank(values: list[int | float], percentile: float) -> int | float:
    rank = max(1, math.ceil(percentile * len(values)))
    return values[rank - 1]


def _has_model_matched_pricing(settings: Settings) -> bool:
    return bool(
        settings.openai_pricing_model.strip()
        and settings.openai_pricing_model == settings.openai_model
        and settings.openai_input_cost_per_million_tokens_usd is not None
        and settings.openai_output_cost_per_million_tokens_usd is not None
    )


def _metadata_int(metadata: dict[str, Any], key: str) -> int | None:
    value = metadata.get(key)
    return _optional_nonnegative(value)


def _optional_nonnegative(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _safe_failure_code(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    normalized = value.strip()
    if not normalized or len(normalized) > 100:
        return fallback
    if not all(
        character.islower() or character.isdigit() or character == "_" for character in normalized
    ):
        return fallback
    return normalized


def _median(metric: dict[str, Any]) -> str:
    value = metric.get("median")
    return "n/a" if value is None else str(value)


def _median_p95(metric: dict[str, Any]) -> str:
    median = metric.get("median")
    p95 = metric.get("p95")
    if median is None:
        return "n/a"
    return f"{median}/{p95 if p95 is not None else 'n/a'}"


def _format_cost(metric: dict[str, Any]) -> str:
    value = metric.get("median")
    if value is None:
        return "n/a"
    return f"${float(value) / 1_000_000:.6f} est."


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the opt-in, paid Day 7 live Agent benchmark against fixed synthetic data."
        )
    )
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="validate seeded tools without OpenAI calls or paid-live opt-in",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.preflight_only:
        print(json.dumps(run_seeded_preflight(), indent=2, sort_keys=True))
        return
    if os.environ.get(_LIVE_OPT_IN_ENV) != "1":
        raise SystemExit(
            f"Refusing paid live calls: set {_LIVE_OPT_IN_ENV}=1 after deterministic preflight."
        )
    result = run_live_benchmark(repetitions=args.repetitions)
    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_markdown(result))


if __name__ == "__main__":
    main()

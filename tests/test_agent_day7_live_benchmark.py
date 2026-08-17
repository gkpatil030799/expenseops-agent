from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

import scripts.benchmark_agent_day7_live as live_benchmark
from app.config import Settings
from scripts.benchmark_agent_day7_live import (
    BENCHMARK_VERSION,
    DATASET_VERSION,
    LiveObservation,
    _execution_stop_code,
    argument_scope_failure_codes,
    arguments_match_scenario,
    format_markdown,
    live_benchmark_scenarios,
    main,
    run_live_benchmark,
    run_seeded_preflight,
    summarize_live_observations,
)

EXPECTED_SCENARIOS = (
    "spending",
    "transaction-search",
    "contextual-spending",
    "household-read",
    "replenishment-plus-deals",
    "attention",
)


def _valid_arguments() -> dict[str, dict[str, dict[str, object]]]:
    spending = {
        "get_spending_insights": {
            "start_date": "2026-08-01",
            "end_date": "2026-08-14",
            "account_id": None,
            "category": "Food & Dining",
            "merchant": None,
            "review_type": None,
            "spend_basis": None,
            "currency_code": None,
        }
    }
    transactions = {
        "search_transactions": {
            "transaction_id": None,
            "merchant": "Synthetic Cafe",
            "start_date": "2026-08-01",
            "end_date": "2026-08-14",
            "category": None,
            "review_type": None,
            "review_status": None,
            "min_amount_cents": None,
            "max_amount_cents": None,
            "currency_code": None,
            "include_pending": False,
            "limit": 20,
        }
    }
    household = {
        "get_household_replenishment": {
            "view": "due",
            "household_item_id": None,
            "query": None,
            "horizon_days": 7,
            "limit": 10,
        }
    }
    deals = {
        "get_relevant_deals": {
            "deal_id": None,
            "category": None,
            "query": None,
            "expiring_within_days": None,
            "need_related_only": True,
            "limit": 8,
        }
    }
    attention_transactions = deepcopy(transactions)
    attention_transactions["search_transactions"].update(
        {
            "merchant": None,
            "start_date": None,
            "end_date": None,
            "review_type": "unreviewed",
        }
    )
    return {
        "spending": spending,
        "contextual-spending": deepcopy(spending),
        "transaction-search": transactions,
        "household-read": household,
        "replenishment-plus-deals": {**deepcopy(household), **deals},
        "attention": {
            **attention_transactions,
            **deepcopy(household),
            "get_integration_status": {"providers": None},
        },
    }


def _observation(
    scenario: str,
    value: int,
    *,
    passed: bool = True,
    cost: int | None = 10,
) -> LiveObservation:
    return LiveObservation(
        scenario=scenario,
        passed=passed,
        failure_code=None if passed else "incorrect_tool_selection",
        run_latency_ms=value * 100,
        sdk_runtime_latency_ms=value * 90,
        provider_orchestration_latency_ms_estimate=value * 80,
        total_tool_latency_ms=value * 10,
        composition_latency_ms=value,
        input_tokens=value * 1_000,
        output_tokens=value * 10,
        total_tokens=value * 1_010,
        provider_request_count=2,
        tool_call_count=1,
        estimated_cost_micros=cost,
    )


def test_live_matrix_is_fixed_bounded_and_covers_cost_and_latency_scenarios() -> None:
    scenarios = live_benchmark_scenarios()

    assert tuple(item.name for item in scenarios) == EXPECTED_SCENARIOS
    assert len({item.text for item in scenarios}) == len(scenarios)
    assert all(1 <= len(item.expected_tools) <= 3 for item in scenarios)
    assert scenarios[2].page_context is not None
    assert scenarios[4].expected_tools == (
        "get_household_replenishment",
        "get_relevant_deals",
    )
    assert len(scenarios[5].expected_tools) == 3


def test_argument_scope_validator_accepts_only_the_fixed_effective_scope() -> None:
    valid = _valid_arguments()

    assert all(arguments_match_scenario(name, arguments) for name, arguments in valid.items())

    invalid = deepcopy(valid)
    invalid["spending"]["get_spending_insights"]["end_date"] = "2026-08-13"
    invalid["contextual-spending"]["get_spending_insights"]["category"] = "Restaurants"
    invalid["transaction-search"]["search_transactions"]["include_pending"] = True
    invalid["household-read"]["get_household_replenishment"]["horizon_days"] = 8
    invalid["replenishment-plus-deals"]["get_relevant_deals"]["query"] = "detergent"
    invalid["attention"]["get_integration_status"]["providers"] = ["plaid"]

    assert all(not arguments_match_scenario(name, arguments) for name, arguments in invalid.items())
    assert argument_scope_failure_codes("spending", invalid["spending"]) == ("spending_date_range",)
    assert argument_scope_failure_codes(
        "replenishment-plus-deals",
        invalid["replenishment-plus-deals"],
    ) == ("deals_optional_selectors",)
    with pytest.raises(ValueError, match="unknown live benchmark scenario"):
        arguments_match_scenario("unknown", {})


def test_live_summary_uses_nearest_rank_and_emits_only_aggregate_data() -> None:
    observations = [
        _observation(scenario, value) for value in range(1, 11) for scenario in EXPECTED_SCENARIOS
    ]
    settings = Settings(
        _env_file=None,
        openai_model="gpt-4.1-mini",
        openai_pricing_model="gpt-4.1-mini",
        openai_input_cost_per_million_tokens_usd=Decimal("0.40"),
        openai_output_cost_per_million_tokens_usd=Decimal("1.60"),
    )

    result = summarize_live_observations(
        observations,
        settings=settings,
        repetitions=10,
    )

    assert result["dataset_version"] == DATASET_VERSION
    assert result["scenario_count"] == 6
    assert result["overall"] == {"n": 60, "passed": 60, "failed": 0}
    assert result["method"]["execution"] == "sequential-round-robin"
    assert result["method"]["conversation_per_observation"] is True
    assert result["method"]["raw_payloads_logged"] is False
    assert result["pricing"] == {
        "model_matched_snapshot": True,
        "pricing_model": "gpt-4.1-mini",
        "input_usd_per_million_tokens": "0.40",
        "output_usd_per_million_tokens": "1.60",
        "cost_label": "estimated",
    }
    first = result["scenarios"][0]
    assert first["n"] == 10
    assert first["passed"] == 10
    assert first["run_latency_ms"] == {
        "n": 10,
        "median": 550.0,
        "p95": 1_000,
        "minimum": 100,
        "maximum": 1_000,
    }
    assert first["input_tokens"]["median"] == 5_500.0
    assert first["estimated_cost_micros"] == {
        "n": 10,
        "median": 10.0,
        "p95": 10,
        "total": 100,
    }
    serialized = json.dumps(result, sort_keys=True)
    assert "How much did I spend" not in serialized
    assert "Synthetic Cafe" not in serialized
    assert "raw_prompt" not in serialized
    assert "tool_output" not in serialized
    assert "provider_payload" not in serialized

    rendered = format_markdown(result)
    assert "| spending | 10/10 | 550.0/1000 |" in rendered
    assert "| Input median | Output median | Total median |" in rendered
    assert "Stopped early: no." in rendered
    assert "$0.000010 est." in rendered


def test_p95_and_cost_stay_unavailable_when_sample_or_pricing_is_insufficient() -> None:
    # Even a stale/precomputed observation cannot emit dollars without this
    # invocation's exact model-matched pricing inputs.
    observations = [_observation(scenario, 1, cost=10) for scenario in EXPECTED_SCENARIOS]
    settings = Settings(
        _env_file=None,
        openai_model="gpt-4.1-mini",
        openai_pricing_model="different-model",
        openai_input_cost_per_million_tokens_usd=Decimal("0.40"),
        openai_output_cost_per_million_tokens_usd=Decimal("1.60"),
    )

    result = summarize_live_observations(
        observations,
        settings=settings,
        repetitions=1,
    )

    assert result["pricing"] == {
        "model_matched_snapshot": False,
        "pricing_model": "different-model",
        "input_usd_per_million_tokens": None,
        "output_usd_per_million_tokens": None,
        "cost_label": "unavailable",
    }
    assert result["scenarios"][0]["run_latency_ms"]["p95"] is None
    assert result["scenarios"][0]["estimated_cost_micros"] == {
        "n": 0,
        "median": None,
        "p95": None,
        "total": None,
    }
    assert "Dollar cost unavailable" in format_markdown(result)


def test_quality_failure_remains_in_latency_token_and_cost_population() -> None:
    observations = [_observation("replenishment-plus-deals", value) for value in range(1, 10)]
    observations.append(
        replace(
            _observation("replenishment-plus-deals", 10, passed=False, cost=20),
            failure_code="incorrect_argument_scope",
            completion_state="complete",
            observed_tool_names=("get_household_replenishment", "get_relevant_deals"),
            observed_tool_statuses=("completed", "completed"),
            argument_shapes=(
                ("get_household_replenishment", ("horizon_days", "view")),
                ("get_relevant_deals", ("need_related_only",)),
            ),
            observed_block_types=("replenishment_summary", "deal_list"),
            expected_block_types=("deal_list", "replenishment_summary"),
            evidence_set_count=2,
            failure_origin="provider_planning",
        )
    )
    settings = Settings(
        _env_file=None,
        openai_model="gpt-4.1-mini",
        openai_pricing_model="gpt-4.1-mini",
        openai_input_cost_per_million_tokens_usd=Decimal("0.40"),
        openai_output_cost_per_million_tokens_usd=Decimal("1.60"),
    )

    result = summarize_live_observations(observations, settings=settings, repetitions=10)
    row = next(
        item for item in result["scenarios"] if item["scenario"] == "replenishment-plus-deals"
    )

    assert (row["n"], row["passed"], row["failed"]) == (10, 9, 1)
    assert row["run_latency_ms"]["n"] == 10
    assert row["run_latency_ms"]["p95"] == 1_000
    assert row["input_tokens"]["n"] == 10
    assert row["estimated_cost_micros"] == {
        "n": 10,
        "median": 10.0,
        "p95": 20,
        "total": 110,
    }
    assert row["failure_diagnostics"] == [
        {
            "failure_code": "incorrect_argument_scope",
            "failure_origin": "provider_planning",
            "completion_state": "complete",
            "actual_block_type_sequence": ["replenishment_summary", "deal_list"],
            "expected_block_types": ["deal_list", "replenishment_summary"],
            "tool_names": ["get_household_replenishment", "get_relevant_deals"],
            "tool_statuses": ["completed", "completed"],
            "argument_shapes": [
                {
                    "tool_name": "get_household_replenishment",
                    "keys": ["horizon_days", "view"],
                },
                {"tool_name": "get_relevant_deals", "keys": ["need_related_only"]},
            ],
            "evidence_set_count": 2,
            "failed_tool_call_count": 0,
            "tool_failure_codes": [],
            "argument_scope_failure_codes": [],
        }
    ]


def test_execution_anomaly_stops_early_and_is_explicit_in_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quality_failure = replace(
        _observation("spending", 1, passed=False),
        failure_code="incorrect_argument_scope",
        failure_origin="provider_planning",
    )
    execution_failure = replace(
        _observation("transaction-search", 1, passed=False, cost=None),
        failure_code="agent_provider_timeout",
        failure_origin="execution",
    )

    assert _execution_stop_code(quality_failure) is None
    assert _execution_stop_code(execution_failure) == "agent_provider_timeout"

    calls: list[str] = []

    async def fake_run_scenario(
        _db,
        *,
        settings: Settings,
        user_id: int,
        scenario,
        repetition: int,
    ) -> LiveObservation:
        del settings, user_id, repetition
        calls.append(scenario.name)
        return quality_failure if len(calls) == 1 else execution_failure

    monkeypatch.setattr(live_benchmark, "_run_scenario", fake_run_scenario)
    result = asyncio.run(
        live_benchmark._run_live_benchmark_async(
            repetitions=10,
            settings=Settings(_env_file=None),
        )
    )

    assert calls == ["spending", "transaction-search"]
    assert result["overall"] == {"n": 2, "passed": 0, "failed": 2}
    assert result["method"]["stopped_early"] is True
    assert result["method"]["stop_failure_code"] == "agent_provider_timeout"
    assert "Stopped early: yes (agent_provider_timeout)." in format_markdown(result)


def test_invalid_repetition_count_fails_before_any_provider_call() -> None:
    with pytest.raises(ValueError, match="repetitions must be between"):
        run_live_benchmark(
            repetitions=0,
            settings=Settings(_env_file=None, openai_api_key="unused-test-key"),
        )


def test_seeded_preflight_exercises_every_tool_without_provider_calls() -> None:
    result = run_seeded_preflight()

    assert result == {
        "benchmark_version": BENCHMARK_VERSION,
        "dataset_version": DATASET_VERSION,
        "fixed_current_date": "2026-08-14",
        "provider_calls": 0,
        "raw_payloads_logged": False,
        "checks": {
            "spending": True,
            "transaction_search": True,
            "household": True,
            "deals": True,
            "integrations": True,
        },
    }


def test_cli_requires_explicit_paid_live_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUN_LIVE_AGENT_BENCHMARK", raising=False)
    monkeypatch.setattr("sys.argv", ["benchmark_agent_day7_live.py"])

    with pytest.raises(SystemExit, match="Refusing paid live calls"):
        main()

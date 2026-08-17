from __future__ import annotations

from scripts.benchmark_agent_day6 import benchmark_scenarios, format_markdown, run_benchmark

EXPECTED_SCENARIOS = (
    "spending-only",
    "transaction-only",
    "replenishment-only",
    "deal-only",
    "contextual-single-domain",
    "replenishment-plus-deals",
    "spending-plus-transactions",
    "attention-summary-multi-domain",
    "partial-tool-failure",
    "maximum-legal-bounded-response",
)


def test_day6_benchmark_has_exact_bounded_scenario_matrix():
    scenarios = benchmark_scenarios()

    assert tuple(scenario.name for scenario in scenarios) == EXPECTED_SCENARIOS
    assert all(1 <= len(scenario.evidence) <= 3 for scenario in scenarios)
    assert scenarios[4].page_context is not None
    assert len(scenarios[7].evidence) == 3
    receipt, errand, _integration = scenarios[7].evidence
    assert receipt.tool_version == "1.1"
    assert receipt.output["receipts"][0]["confirmed_household_item_ids"] == ["101"]
    assert errand.tool_version == "1.1"
    assert errand.output["errands"][0]["household_item_ids"] == ["101"]
    assert len(scenarios[8].failures) == 1
    assert len(scenarios[9].evidence) == 3


def test_day6_benchmark_reports_median_p95_and_only_aggregate_metrics():
    result = run_benchmark(repetitions=3, warmups=0)

    assert result["benchmark_version"] == "day6-v2"
    assert result["scenario_count"] == 10
    assert result["repetitions_per_scenario"] == 3
    assert result["method"] == {
        "clock": "time.perf_counter_ns",
        "median": "statistics.median",
        "p95": "nearest-rank",
        "network": False,
        "provider": False,
        "raw_payloads_logged": False,
        "canonical_response_projection": "model_dump_json(exclude_none=True)",
        "response_payload_projection": "model_dump_json()",
    }
    assert result["tool_schema_bytes"] > 0
    assert result["provider_completion_schema_bytes"] > 0
    assert {row["scenario"] for row in result["scenarios"]} == set(EXPECTED_SCENARIOS)
    assert result["scenarios"][8]["failure_count"] == 1
    assert result["scenarios"][8]["completion_state"] == "partial"
    assert result["scenarios"][8]["tool_call_count"] == 3
    assert result["overall"]["partial_scenario_count"] == 1
    assert result["overall"]["seeded_partial_scenario_rate"] == 0.1
    maximum = result["scenarios"][9]
    assert maximum["tool_call_count"] == 3
    assert maximum["evidence_set_count"] == 3
    assert maximum["canonical_response_bytes"] < maximum["evidence_bytes"]
    assert maximum["response_payload_bytes"] < maximum["evidence_bytes"]
    assert all(row["canonical_response_bytes"] > 0 for row in result["scenarios"])
    assert all(row["response_payload_bytes"] > 0 for row in result["scenarios"])
    assert all(
        row["response_payload_bytes"] >= row["canonical_response_bytes"]
        for row in result["scenarios"]
    )
    assert all(row["evidence_bytes"] > 0 for row in result["scenarios"])
    assert all(
        row[metric][quantile] >= 0
        for row in result["scenarios"]
        for metric in ("application_processing_ms", "total_ms")
        for quantile in ("median", "p95")
    )
    assert "evidence" not in result
    assert "prompt" not in result
    assert "output" not in result

    rendered = format_markdown(result)
    assert "| partial-tool-failure |" in rendered
    assert "| Compact bytes | Payload bytes |" in rendered
    assert "Overall application processing" in rendered
    assert "Strict provider-completion schema" in rendered

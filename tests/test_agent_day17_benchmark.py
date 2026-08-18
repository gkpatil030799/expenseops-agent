from __future__ import annotations

from scripts.benchmark_agent_day17 import (
    FOLLOW_UP_PROMPTS,
    _markdown,
    paraphrase_cases,
    real_user_cases,
    run_benchmark,
)


def test_day17_benchmark_keeps_the_exact_real_user_corpus_and_followups() -> None:
    assert [case.prompt for case in real_user_cases()] == [
        "what category did i spend the most on this month?",
        "what are my top 5 merchants this month?",
        "how much did i spend this month?",
        "what's my typical restaurant check?",
        "why did restaurant spending increase?",
        "what did i buy recently that could become a household staple?",
        "what did ExpenseOps learn today?",
        "are my spendings increased compared to last week ?",
        "did i spent more this week then last?",
        "how much money went to coffee recently?",
        "anything you're unsure about?",
        "show restrant spendng frm last mnth",
        "why did this increase?",
    ]
    assert FOLLOW_UP_PROMPTS == (
        "how much did i spend on dining this month?",
        "What about last month?",
        "Which merchants caused the difference?",
        "Show the actual transactions.",
    )


def test_day17_paraphrase_corpus_covers_every_required_realistic_typo() -> None:
    corpus = " ".join(
        [case.prompt.casefold() for case in (*real_user_cases(), *paraphrase_cases())]
    )

    for typo in ("spendings", "then", "restrant", "frm", "mnth", "reciept", "catagory", "cofee"):
        assert typo in corpus


def test_day17_benchmark_passes_every_route_and_direct_answer_separately() -> None:
    result = run_benchmark(repetitions=3, warmups=1)

    assert result["after"] == {
        "full_acceptance_passed": 13,
        "full_acceptance_total": 13,
        "full_acceptance_accuracy": 1.0,
        "routing_passed": 26,
        "routing_total": 26,
        "routing_accuracy": 1.0,
        "wrong_domain_routes": 0,
        "unnecessary_clarifications": 0,
        "unsupported_responses": 0,
        "maximum_tools_exposed_per_supported_turn": 1,
        "maximum_tool_calls_per_supported_turn": 1,
        "write_tool_exposures": 0,
        "provider_turns_in_deterministic_benchmark": 0,
        "provider_input_tokens": 0,
        "provider_output_tokens": 0,
        "provider_cost_usd": 0,
        "production_runtime_projection": (
            "two provider requests in one bounded SDK loop and one canonical read call"
        ),
    }
    assert all(item["passed"] for item in result["real_user_regressions"])
    assert all(item["passed"] for item in result["paraphrases"])
    assert result["follow_up_chain"]["passed"] is True
    assert result["follow_up_chain"]["turns"][3]["arguments"] == {
        "start_date": "2026-08-01",
        "end_date": "2026-08-17",
        "comparison_start_date": "2026-07-01",
        "comparison_end_date": "2026-07-31",
        "include_pending": False,
        "limit": 20,
        "lifestyle_activity_type": "all",
    }
    assert result["temporal_semantics"]["passed"] is True
    assert result["hostile_control_strings"]["passed"] is True


def test_day17_benchmark_checks_rank_limits_periods_and_structured_blocks() -> None:
    result = run_benchmark(repetitions=1, warmups=0)
    rows = {item["case_id"]: item for item in result["real_user_regressions"]}

    top_category = rows["01_top_category"]
    assert top_category["response"]["block_type"] == "spending_summary"
    assert top_category["response"]["direct_answer"].startswith("Food & Dining")
    top_merchants = rows["02_top_merchants"]
    assert top_merchants["arguments"] == {
        "start_date": "2026-08-01",
        "end_date": "2026-08-17",
    }
    assert "Alpha Cafe" in top_merchants["response"]["direct_answer"]
    assert rows["04_typical_restaurant_check"]["response"]["block_type"] == ("lifestyle_summary")
    assert (
        "average restaurant check"
        in rows["04_typical_restaurant_check"]["response"]["direct_answer"]
    )
    assert "Purchase count changed" in rows["05_restaurant_increase"]["response"]["direct_answer"]
    assert rows["06_recent_staple_candidates"]["actual_tool"] == ("get_classification_activity")
    assert rows["06_recent_staple_candidates"]["arguments"]["view"] == "staple_candidates"
    assert rows["08b_week_comparison_grammar"]["arguments"]["comparison_mode"] == (
        "same_weekdays_last_week"
    )
    assert rows["11_typo_restaurant_last_month"]["resolved_period"] == {
        "preset": "last_month",
        "start_date": "2026-07-01",
        "end_date": "2026-07-31",
        "timezone": "America/Phoenix",
    }


def test_day17_benchmark_reports_tool_surface_and_local_latency_without_fake_data() -> None:
    result = run_benchmark(repetitions=3, warmups=1)
    before = result["before"]
    surface = result["tool_surface"]
    performance = result["performance"]

    assert before["source_commit"] == "72d4705"
    assert before["typed_objective_available"] is False
    assert before["deterministic_single_tool_routes"] == 5
    assert before["mean_tools_exposed"] == 5.923
    assert before["provider_latency_ms"] is None
    assert before["input_tokens"] is None
    assert before["output_tokens"] is None
    assert surface["registered_read_tools"] == 9
    assert surface["registered_schema_bytes"] == 15_022
    assert surface["registered_schema_estimated_tokens"] == 3_756
    assert surface["registered_total_tools"] == 13
    assert surface["total_tool_schema_bytes"] == 20_199
    assert surface["total_tool_schema_estimated_tokens"] == 5_050
    assert surface["registered_schema_growth_vs_day16_bytes"] == 2_972
    assert surface["total_schema_growth_vs_day16_bytes"] == 2_972
    assert surface["mean_exposed_schema_bytes"] == 2_292.5
    assert surface["mean_exposed_schema_estimated_tokens"] == 574
    assert surface["mean_exposed_schema_reduction_vs_full_percent"] == 84.7
    assert performance["network_or_provider_included"] is False
    assert performance["database_query_included"] is False
    assert performance["query_objective_and_routing"]["median_ms"] >= 0
    assert performance["date_resolution"]["p95_ms"] >= 0
    assert performance["canonical_composition"]["median_ms"] >= 0


def test_day17_markdown_report_keeps_measurement_boundaries_visible() -> None:
    report = _markdown(run_benchmark(repetitions=1, warmups=0))

    assert "Full exact acceptance: 13/13" in report
    assert "All routing cases: 26/26" in report
    assert "Registered schema: 15022 bytes; mean exposed: 2292.5 bytes" in report
    assert "Registered total schema: 20199 bytes" in report
    assert "Query objective + routing:" in report
    assert "Date resolution:" in report
    assert "Canonical composition:" in report
    assert "Route + composition:" in report
    assert "Provider/network and database work included: no" in report

from scripts.benchmark_review_inbox_day18 import run_benchmark


def test_day18_review_benchmark_requires_no_search_prompt_or_provider_call() -> None:
    result = run_benchmark()

    assert result["measurement_boundary"] == "in_process_sqlite_projection_and_read"
    assert result["seeded_transactions"] == 5
    assert result["review_items_expected"] == result["review_items_observed"] == 2
    assert result["manual_searches_required"] == 0
    assert result["prompts_required_for_discovery"] == 0
    assert result["open_after_web_resolution"] == 1
    assert result["open_after_telegram_resolution"] == 0
    assert result["provider_calls"] == 0
    assert float(result["visibility_latency_ms"]) >= 0
    assert float(result["two_decision_resolution_latency_ms"]) >= 0

from scripts.benchmark_lifestyle_day10 import run_benchmark


def test_day10_benchmark_uses_production_service_and_reports_bounded_metrics():
    result = run_benchmark(repetitions=3, warmups=1)

    assert result["registered_read_tools"] == 8
    assert result["tool_schema_bytes"] > 0
    assert set(result["scenarios"]) == {
        "coffee",
        "restaurants",
        "delivery",
        "nightlife",
        "all",
    }
    assert all(value["median_ms"] >= 0 for value in result["scenarios"].values())

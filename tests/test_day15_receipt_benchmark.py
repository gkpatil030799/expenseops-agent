from scripts.benchmark_receipt_day15 import benchmark_cases, run_benchmark


def test_day15_benchmark_contains_the_exact_required_twenty_scenarios():
    cases = benchmark_cases()
    assert [case.case_id for case in cases] == [f"{index:02d}" for index in range(1, 21)]
    assert [case.name for case in cases] == [
        "clean grocery receipt",
        "normal phone photo",
        "slightly blurry",
        "strongly blurry but human-readable",
        "rotated 90 degrees",
        "rotated 180 degrees",
        "shadow",
        "perspective angle",
        "long receipt",
        "crumpled receipt",
        "restaurant receipt",
        "receipt with tip",
        "discounts and coupons",
        "tax",
        "quantity and package counts",
        "partially cut edge",
        "non-receipt image",
        "duplicate receipt image",
        "same receipt through two channels",
        "hostile prompt injection in receipt",
    ]


def test_day15_benchmark_exercises_quality_arithmetic_and_false_ready_regression():
    result = run_benchmark()
    assert result.scenario_count == 20
    assert result.merchant_accuracy_percent == 100
    assert result.date_accuracy_percent == 100
    assert result.total_accuracy_percent == 100
    assert result.line_item_extraction_percent == 100
    assert result.arithmetic_reconciliation_percent > 80
    assert result.partial_success_rate_percent > 0
    assert result.hard_failure_rate_percent == 5
    assert result.legacy_false_ready_count == 1
    assert result.day15_false_ready_count == 0
    assert result.median_normalization_latency_ms >= 0
    assert {item.quality for item in result.results} >= {"complete", "partial", "non_receipt"}

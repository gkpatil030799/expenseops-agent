from scripts.benchmark_receipt_learning_day9 import run_benchmark


def test_day9_seeded_receipts_materially_reduce_line_decisions_without_extra_model_calls():
    result = run_benchmark()

    assert result.scenario_count == 4
    assert result.baseline_manual_line_decisions == 5
    assert result.day9_manual_line_decisions == 1
    assert result.manual_line_decisions_avoided == 4
    assert result.manual_line_decision_reduction_percent == 80.0
    assert result.baseline_cadence_entries == 4
    assert result.day9_cadence_entries == 0
    assert result.explicit_batch_confirmations == 2
    assert result.automatic_alias_hits == 3
    assert result.suggested_cross_merchant_matches == 1
    assert result.tracked_item_count == 4
    assert result.confirmed_acquisition_count == 8
    assert result.provider_requests == 4
    assert result.candidate_generation_latency_ms_median >= 0
    assert result.batch_confirmation_latency_ms_median >= 0

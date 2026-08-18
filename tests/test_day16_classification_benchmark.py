from scripts.benchmark_autonomous_classification_day16 import (
    RECEIPT_CORPUS,
    TRANSACTION_CORPUS,
    run_benchmark,
)


def test_day16_benchmark_covers_required_receipt_and_transaction_domains() -> None:
    receipt_names = {case.name for case in RECEIPT_CORPUS}
    transaction_names = {case.name for case in TRANSACTION_CORPUS}

    assert len(RECEIPT_CORPUS) == 30
    assert {
        "Organic eggs",
        "Whole milk",
        "Paper towels",
        "Laundry detergent",
        "Shampoo",
        "Prescription medication",
        "Dog pet food",
        "Cotton T-shirt",
        "Laptop computer",
        "Paneer tikka restaurant",
        "Starbucks latte",
        "Sales tax",
        "Gratuity",
        "Coupon savings",
        "Return credit",
        "HOME 24",
    } <= receipt_names
    assert len(TRANSACTION_CORPUS) == 21
    assert {
        "Starbucks",
        "Trader Joe's",
        "Costco",
        "Target",
        "Shell",
        "Uber",
        "Netflix",
        "CVS",
        "Delta airline",
        "Marriott hotel",
        "DoorDash",
        "ZXQ Mystery Vendor",
        "Account transfer",
        "Card payment",
        "Coffee refund",
    } <= transaction_names


def test_day16_benchmark_measures_quality_manual_reduction_reconciliation_and_cadence() -> None:
    result = run_benchmark()

    receipt = result["corpus"]["receipt_items"]
    transactions = result["corpus"]["transactions"]
    assert result["corpus"]["total_decisions"] == 51
    assert receipt["parent_category_precision_percent"] == 100
    assert receipt["replenishment_precision_percent"] == 100
    assert receipt["canonical_concept_evaluated"] == 19
    assert receipt["canonical_concept_precision_percent"] == 100
    assert receipt["subcategory_evaluated"] == 17
    assert receipt["subcategory_precision_percent"] == 100
    assert receipt["false_specific_category_count"] == 0
    assert transactions["parent_category_precision_percent"] == 100
    assert transactions["activity_precision_percent"] == 100
    assert transactions["false_specific_category_count"] == 0
    assert receipt["failures"] == []
    assert transactions["failures"] == []

    routing = result["routing"]
    assert routing["decision_count"] == 25
    assert routing["deterministic_or_provider_count"] == 23
    assert routing["deterministic_or_provider_percent"] == 92
    assert routing["model_candidate_count"] == 2
    assert routing["model_calls"] == 0
    assert routing["provisional_count"] == 2
    assert routing["uncertain_projection_count"] == 2

    manual = result["manual_work"]
    assert manual["before_required_actions"] == 49
    assert manual["after_required_actions"] == 0
    assert manual["required_action_reduction"] == 49
    assert manual["required_action_reduction_percent"] == 100
    assert manual["after"]["optional_uncertain_rows_available_for_review"] == 2

    week = result["autonomous_week"]
    assert week["meaningful_receipt_lines"] == 18
    assert week["categorized_receipt_lines"] == 18
    assert week["eligible_transactions"] == 7
    assert week["categorized_transactions"] == 7
    assert week["active_household_items"] == 10
    assert week["active_acquisitions"] == 10
    assert week["false_staple_count"] == 0
    assert week["classification_error_count"] == 0
    assert week["classification_errors"] == []
    assert week["auto_apply_precision_percent"] == 100
    assert week["false_auto_category_creation_count"] == 0
    assert week["false_auto_concept_creation_count"] == 0
    assert week["trader_joes_match_status"] == "auto_matched"
    assert week["trader_joes_match_correct"] is True
    assert week["review_was_required"] is False

    plaid = result["plaid_reconciliation"]
    assert plaid["scenario_count"] == 9
    assert plaid["outcome_accuracy_percent"] == 100
    assert plaid["auto_match_precision_percent"] == 100
    assert plaid["auto_match_recall_percent"] == 100
    assert plaid["false_auto_match_count"] == 0
    assert all(value["correct"] for value in plaid["results"])

    cadence = result["cadence"]
    assert cadence["first_purchase_prior_count"] == 10
    assert cadence["observed_history_replaced_prior"] is True
    assert cadence["observed_interval_days"] == 10
    assert cadence["observed_cadence_days"] == 10
    assert cadence["absolute_error_days"] == 0
    assert cadence["model_prior_evaluated"] is False
    assert cadence["irregular_interval_evaluated"] is False


def test_day16_benchmark_reports_cost_safe_fields_and_exact_tool_growth() -> None:
    result = run_benchmark()
    performance = result["performance_cost"]
    assert performance["model_calls"] == 0
    assert performance["input_tokens"] == 0
    assert performance["output_tokens"] == 0
    assert performance["estimated_cost_usd"] == 0
    assert performance["classification_correction_rate_percent"] is None
    assert performance["finalizer_runtime_ms"] is None
    assert performance["backfill_rows_per_second"] is None
    assert performance["receipt_to_categorized_latency_ms"] >= 0
    assert performance["plaid_to_categorized_latency_ms"] >= 0
    assert performance["average_candidates_per_receipt_invocation"] == 6

    tools = result["tool_surface"]
    assert tools["registered_read_tools"] == 9
    assert tools["registered_total_tools"] == 13
    assert tools["read_tool_schema_bytes"] == 12_050
    assert tools["total_tool_schema_bytes"] == 17_227
    assert tools["day16_total_tool_growth"] == 1
    assert tools["day16_total_schema_growth_bytes"] == 996
    assert tools["day16_approx_schema_growth_tokens"] == 249
    assert tools["classification_activity_tool_present"] is True

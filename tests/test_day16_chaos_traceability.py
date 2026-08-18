from pathlib import Path

from scripts.day16_chaos_traceability import (
    CHAOS_SCENARIOS,
    EXPECTED_NAMES,
    validate_manifest,
)

REPOSITORY = Path(__file__).resolve().parents[1]


def test_day16_chaos_manifest_has_the_exact_named_release_scenarios() -> None:
    assert tuple(value.scenario_id for value in CHAOS_SCENARIOS) == tuple(range(1, 31))
    assert tuple(value.name for value in CHAOS_SCENARIOS) == EXPECTED_NAMES
    assert EXPECTED_NAMES == (
        "OpenAI unavailable",
        "malformed model output",
        "invalid enum",
        "medium confidence not reviewed",
        "auto-finalizer retry",
        "finalizer concurrent workers",
        "user corrects before finalization",
        "user corrects after finalization",
        "duplicate Gmail receipt",
        "duplicate Telegram photo",
        "duplicate Plaid webhook",
        "pending to posted",
        "exact Plaid match",
        "ambiguous Plaid match",
        "no Plaid match",
        "category collision",
        "subcategory duplicate",
        "concept alias collision",
        "wrong staple corrected",
        "acquisition correction",
        "cadence recalculation",
        "user disables autonomous classification",
        "consent revoked",
        "cross-workspace receipt",
        "cross-workspace transaction",
        "hostile receipt text",
        "hostile merchant",
        "DB failure during auto-apply",
        "job interruption",
        "backfill resume",
    )


def test_day16_chaos_manifest_resolves_to_executable_assertions_not_count_only() -> None:
    evidence = validate_manifest(REPOSITORY)

    assert evidence["scenario_count"] == 30
    assert evidence["mapped_test_references"] >= 30
    assert evidence["mapped_assertion_or_raises_count"] >= 30
    assert all(value.invariant.endswith(".") for value in CHAOS_SCENARIOS)
    assert all(value.coverage in {"direct", "composite"} for value in CHAOS_SCENARIOS)


def test_high_risk_chaos_scenarios_map_to_the_intended_regression_assertions() -> None:
    by_name = {value.name: value for value in CHAOS_SCENARIOS}

    assert by_name["auto-finalizer retry"].nodeids == (
        "tests/test_day16_chaos_regressions.py::test_repeated_finalizer_delivery_is_idempotent",
    )
    assert by_name["pending to posted"].nodeids == (
        "tests/test_receipt_transaction_reconciliation.py::"
        "test_pending_to_posted_migrates_receipt_and_acquisition_atomically",
    )
    assert by_name["user corrects before finalization"].nodeids == (
        "tests/test_day16_classification_jobs.py::"
        "test_user_correction_during_model_planning_wins_before_finalizer_apply",
    )
    assert by_name["DB failure during auto-apply"].nodeids == (
        "tests/test_day16_classification_jobs.py::"
        "test_live_ingestion_classification_failure_is_retried_by_finalizer",
        "tests/test_day16_classification_jobs.py::"
        "test_plaid_upsert_classification_failure_is_retried_by_finalizer",
        "tests/test_day16_classification_jobs.py::"
        "test_backfill_failure_rolls_back_cursor_and_page_work",
    )
    assert by_name["cross-workspace receipt"].nodeids == (
        "tests/test_receipt_transaction_reconciliation.py::"
        "test_cross_workspace_candidate_never_matches",
    )

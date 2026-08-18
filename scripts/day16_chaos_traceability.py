from __future__ import annotations

import argparse
import ast
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChaosScenario:
    scenario_id: int
    name: str
    invariant: str
    nodeids: tuple[str, ...]
    coverage: str = "direct"


CHAOS_SCENARIOS: tuple[ChaosScenario, ...] = (
    ChaosScenario(
        1,
        "OpenAI unavailable",
        "The provider error is retryable and ingestion/finalization uses a safe fallback.",
        (
            "tests/test_day16_classification_model.py::"
            "test_provider_failure_is_retryable_and_never_returns_partial_decisions",
            "tests/test_day16_classification_jobs.py::"
            "test_model_requires_consent_and_outage_uses_deterministic_fallback",
        ),
        "composite",
    ),
    ChaosScenario(
        2,
        "malformed model output",
        "Malformed structured output fails closed without returning partial decisions.",
        (
            "tests/test_day16_classification_model.py::"
            "test_invalid_or_inconsistent_model_output_fails_closed",
        ),
    ),
    ChaosScenario(
        3,
        "invalid enum",
        "A parent or replenishment enum outside the closed contract is rejected.",
        (
            "tests/test_day16_classification_model.py::"
            "test_invalid_or_inconsistent_model_output_fails_closed",
        ),
    ),
    ChaosScenario(
        4,
        "medium confidence not reviewed",
        "A due provisional receipt line finalizes after the grace period without review.",
        (
            "tests/test_day16_classification_jobs.py::"
            "test_finalizer_claims_and_finalizes_due_receipt_lines",
        ),
    ),
    ChaosScenario(
        5,
        "auto-finalizer retry",
        "A second finalizer delivery observes no due row and appends no decision.",
        (
            "tests/test_day16_chaos_regressions.py::"
            "test_repeated_finalizer_delivery_is_idempotent",
        ),
    ),
    ChaosScenario(
        6,
        "finalizer concurrent workers",
        "A committed workspace lease excludes a concurrent finalizer worker.",
        (
            "tests/test_day16_classification_jobs.py::"
            "test_workspace_runner_commits_lease_before_finalizer_work",
        ),
    ),
    ChaosScenario(
        7,
        "user corrects before finalization",
        "A user correction made during model planning wins before finalizer apply.",
        (
            "tests/test_day16_classification_jobs.py::"
            "test_user_correction_during_model_planning_wins_before_finalizer_apply",
        ),
    ),
    ChaosScenario(
        8,
        "user corrects after finalization",
        "A final decision remains correctable and correction repairs downstream learning.",
        (
            "tests/test_autonomous_classification.py::"
            "test_due_provisional_finalization_preserves_user_corrections",
            "tests/test_autonomous_classification.py::"
            "test_user_correction_repairs_then_undoes_autonomous_learning",
        ),
        "composite",
    ),
    ChaosScenario(
        9,
        "duplicate Gmail receipt",
        "A repeated source external ID is idempotent and does not reparse.",
        (
            "tests/test_replenishment_learning.py::"
            "test_duplicate_external_id_is_idempotent_and_skips_reparse",
        ),
    ),
    ChaosScenario(
        10,
        "duplicate Telegram photo",
        "The same content hash through another channel deduplicates to one receipt.",
        (
            "tests/test_replenishment_learning.py::"
            "test_duplicate_receipt_hash_across_sources_is_deduplicated",
        ),
    ),
    ChaosScenario(
        11,
        "duplicate Plaid webhook",
        "A verified webhook replay cannot enqueue duplicate work.",
        (
            "tests/test_plaid_routes.py::"
            "test_verified_plaid_webhook_replay_does_not_enqueue_duplicate_work",
        ),
    ),
    ChaosScenario(
        12,
        "pending to posted",
        "Receipt and acquisition links migrate atomically to the posted transaction.",
        (
            "tests/test_receipt_transaction_reconciliation.py::"
            "test_pending_to_posted_migrates_receipt_and_acquisition_atomically",
        ),
    ),
    ChaosScenario(
        13,
        "exact Plaid match",
        "Generic grocery, mixed-retail, restaurant, retail, and Trader Joe's matches link.",
        (
            "tests/test_receipt_transaction_reconciliation.py::"
            "test_generic_merchant_matrix_auto_matches_deterministically",
        ),
    ),
    ChaosScenario(
        14,
        "ambiguous Plaid match",
        "Equivalent candidates remain ambiguous and no transaction is linked.",
        (
            "tests/test_receipt_transaction_reconciliation.py::"
            "test_equivalent_posted_candidates_are_ambiguous_and_never_forced",
        ),
    ),
    ChaosScenario(
        15,
        "no Plaid match",
        "An amount outside tolerance and a credit candidate produce NO_MATCH.",
        (
            "tests/test_receipt_transaction_reconciliation.py::"
            "test_amount_outside_boundary_and_non_purchase_sign_do_not_match",
        ),
    ),
    ChaosScenario(
        16,
        "category collision",
        "Concurrent normalized taxonomy creation reuses one tenant-scoped category.",
        (
            "tests/test_classification_taxonomy_service.py::"
            "test_two_sessions_reuse_normalized_taxonomy_and_active_household_key",
        ),
    ),
    ChaosScenario(
        17,
        "subcategory duplicate",
        "Normalized duplicate subcategory creation is idempotent across sessions.",
        (
            "tests/test_classification_taxonomy_service.py::"
            "test_two_sessions_reuse_normalized_taxonomy_and_active_household_key",
        ),
    ),
    ChaosScenario(
        18,
        "concept alias collision",
        "Conflicting active aliases fail rather than selecting a concept arbitrarily.",
        (
            "tests/test_classification_taxonomy_service.py::"
            "test_alias_and_concept_collisions_never_choose_arbitrarily",
        ),
    ),
    ChaosScenario(
        19,
        "wrong staple corrected",
        "A correction disables the orphaned auto item and creates the corrected concept.",
        (
            "tests/test_autonomous_classification.py::"
            "test_user_correction_repairs_then_undoes_autonomous_learning",
        ),
    ),
    ChaosScenario(
        20,
        "acquisition correction",
        "The wrong acquisition is voided and superseded without deleting audit history.",
        (
            "tests/test_autonomous_classification.py::"
            "test_user_correction_repairs_then_undoes_autonomous_learning",
        ),
    ),
    ChaosScenario(
        21,
        "cadence recalculation",
        "Observed and quantity-adjusted history replace priors and refresh prediction.",
        (
            "tests/test_classification_taxonomy_service.py::"
            "test_quantity_adjusted_history_replaces_prior_and_refreshes_prediction",
        ),
    ),
    ChaosScenario(
        22,
        "user disables autonomous classification",
        "Global and workspace kill switches leave provisional state untouched.",
        (
            "tests/test_day16_classification_jobs.py::"
            "test_global_and_workspace_kill_switches_leave_provisional_state_untouched",
        ),
    ),
    ChaosScenario(
        23,
        "consent revoked",
        "Revoked consent or inactive membership blocks model classification.",
        (
            "tests/test_day16_classification_jobs.py::"
            "test_model_consent_requires_an_active_current_workspace_member",
            "tests/test_day16_receipt_privacy.py::"
            "test_direct_image_model_path_requires_owner_scoped_consent_before_provider",
        ),
        "composite",
    ),
    ChaosScenario(
        24,
        "cross-workspace receipt",
        "A receipt cannot match a candidate transaction in another workspace.",
        (
            "tests/test_receipt_transaction_reconciliation.py::"
            "test_cross_workspace_candidate_never_matches",
        ),
    ),
    ChaosScenario(
        25,
        "cross-workspace transaction",
        "The finalizer cannot claim another workspace's transaction row.",
        (
            "tests/test_day16_classification_jobs.py::"
            "test_finalizer_never_claims_another_workspace_row",
        ),
    ),
    ChaosScenario(
        26,
        "hostile receipt text",
        "Prompt-injection text is data and falls back to Other / Uncertain.",
        (
            "tests/test_classification_taxonomy_service.py::"
            "test_low_confidence_and_hostile_external_data_fail_to_other_uncertain",
        ),
    ),
    ChaosScenario(
        27,
        "hostile merchant",
        "Hostile external text cannot expand the model output contract or action authority.",
        (
            "tests/test_day16_classification_model.py::"
            "test_hostile_external_text_stays_data_and_cannot_expand_output_contract",
        ),
    ),
    ChaosScenario(
        28,
        "DB failure during auto-apply",
        "A live apply failure is retried durably and a backfill failure rolls back its page.",
        (
            "tests/test_day16_classification_jobs.py::"
            "test_live_ingestion_classification_failure_is_retried_by_finalizer",
            "tests/test_day16_classification_jobs.py::"
            "test_plaid_upsert_classification_failure_is_retried_by_finalizer",
            "tests/test_day16_classification_jobs.py::"
            "test_backfill_failure_rolls_back_cursor_and_page_work",
        ),
        "composite",
    ),
    ChaosScenario(
        29,
        "job interruption",
        "Interrupted bounded work retains the last committed checkpoint and no partial page.",
        (
            "tests/test_day16_classification_jobs.py::"
            "test_backfill_failure_rolls_back_cursor_and_page_work",
            "tests/test_day16_classification_jobs.py::"
            "test_workspace_runner_commits_lease_before_finalizer_work",
        ),
        "composite",
    ),
    ChaosScenario(
        30,
        "backfill resume",
        "A committed page advances bounded cursors and a replay scans zero completed rows.",
        (
            "tests/test_day16_classification_jobs.py::"
            "test_backfill_dry_run_is_read_only_then_checkpointed_page_is_idempotent_and_scoped",
        ),
    ),
)


EXPECTED_NAMES: tuple[str, ...] = (
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


def validate_manifest(repository: Path) -> dict[str, int]:
    if tuple(value.scenario_id for value in CHAOS_SCENARIOS) != tuple(range(1, 31)):
        raise ValueError("Day 16 chaos scenario IDs must be exactly 1 through 30")
    if tuple(value.name for value in CHAOS_SCENARIOS) != EXPECTED_NAMES:
        raise ValueError("Day 16 chaos scenario names do not match the release contract")
    assertion_count = 0
    function_count = 0
    for scenario in CHAOS_SCENARIOS:
        if scenario.coverage not in {"direct", "composite"}:
            raise ValueError(f"unsupported coverage label for scenario {scenario.scenario_id}")
        if not scenario.invariant.strip() or not scenario.nodeids:
            raise ValueError(f"scenario {scenario.scenario_id} has no evidence contract")
        for nodeid in scenario.nodeids:
            path_text, separator, function_name = nodeid.partition("::")
            if separator != "::" or not function_name.startswith("test_"):
                raise ValueError(f"invalid pytest node ID: {nodeid}")
            path = repository / path_text
            if not path.is_file():
                raise ValueError(f"missing test module: {path_text}")
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            functions = [
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == function_name
            ]
            if len(functions) != 1:
                raise ValueError(f"test function not found exactly once: {nodeid}")
            function_count += 1
            assertions = sum(
                isinstance(node, ast.Assert) for node in ast.walk(functions[0])
            )
            raises = sum(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "raises"
                for node in ast.walk(functions[0])
            )
            if assertions + raises < 1:
                raise ValueError(f"mapped test contains no executable assertion: {nodeid}")
            assertion_count += assertions + raises
    return {
        "scenario_count": len(CHAOS_SCENARIOS),
        "mapped_test_references": function_count,
        "mapped_assertion_or_raises_count": assertion_count,
    }


def run_manifest(repository: Path) -> int:
    validate_manifest(repository)
    nodeids = list(
        dict.fromkeys(nodeid for scenario in CHAOS_SCENARIOS for nodeid in scenario.nodeids)
    )
    completed = subprocess.run(
        [str(repository / ".venv/bin/python"), "-m", "pytest", "-q", *nodeids],
        cwd=repository,
        check=False,
    )
    return completed.returncode


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate or execute the exact Day 16 30-scenario chaos evidence map."
    )
    parser.add_argument("--run", action="store_true", help="Run every mapped pytest node.")
    parser.add_argument("--list", action="store_true", help="Print scenario mappings as JSON.")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    evidence = validate_manifest(repository)
    if args.list:
        print(json.dumps([asdict(value) for value in CHAOS_SCENARIOS], indent=2))
    else:
        print(json.dumps(evidence, sort_keys=True))
    if args.run:
        raise SystemExit(run_manifest(repository))


if __name__ == "__main__":
    main()

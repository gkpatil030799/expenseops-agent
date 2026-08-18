from __future__ import annotations

import argparse
import json
import math
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.agent.action_tools import register_action_tools
from app.agent.read_tools import build_read_tool_registry
from app.config import Settings
from app.db import Base
from app.models import (
    ClassificationDecisionRecord,
    ClassificationSettings,
    ExpenseTransaction,
    HouseholdItem,
    HouseholdItemAcquisition,
    PlaidItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReplenishmentEligibility,
    SpendingParentCategory,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.services.autonomous_classification_service import AutonomousClassificationService
from app.services.classification_taxonomy_service import (
    ClassificationSourceType,
    classify_known_text,
)
from app.services.item_normalization_service import normalize_merchant
from app.services.receipt_transaction_reconciliation_service import (
    ReceiptTransactionMatchStatus,
    ReceiptTransactionReconciliationService,
)

DAY13_TOTAL_TOOL_COUNT = 12
DAY13_TOTAL_TOOL_SCHEMA_BYTES = 16_231
DAY16_READ_TOOL_SCHEMA_BYTES = 12_050
DAY16_TOTAL_TOOL_COUNT = 13
DAY16_TOTAL_TOOL_SCHEMA_BYTES = 17_227


@dataclass(frozen=True)
class GoldenCase:
    name: str
    expected_parent: str
    expected_activity: str
    expected_replenishment: str
    provider_category: str | None = None


@dataclass(frozen=True)
class ReconciliationCaseResult:
    name: str
    expected_status: str
    actual_status: str
    expected_transaction_id: int | None
    actual_transaction_id: int | None

    @property
    def correct(self) -> bool:
        return (
            self.actual_status == self.expected_status
            and self.actual_transaction_id == self.expected_transaction_id
        )


RECEIPT_CORPUS: tuple[GoldenCase, ...] = (
    GoldenCase("Organic eggs", "food_dining", "grocery", "replenishable"),
    GoldenCase("Whole milk", "food_dining", "grocery", "replenishable"),
    GoldenCase("Bread", "food_dining", "grocery", "replenishable"),
    GoldenCase("Basmati rice", "food_dining", "grocery", "replenishable"),
    GoldenCase("Fresh vegetables", "food_dining", "grocery", "replenishable"),
    GoldenCase("Chicken breast", "food_dining", "grocery", "replenishable"),
    GoldenCase(
        "Paper towels", "household_home", "household_consumable", "replenishable"
    ),
    GoldenCase(
        "Toilet paper", "household_home", "household_consumable", "replenishable"
    ),
    GoldenCase(
        "Laundry detergent", "household_home", "household_consumable", "replenishable"
    ),
    GoldenCase("Dish soap", "household_home", "household_consumable", "replenishable"),
    GoldenCase(
        "Dishwasher tablets", "household_home", "household_consumable", "replenishable"
    ),
    GoldenCase("Trash bags", "household_home", "household_consumable", "replenishable"),
    GoldenCase(
        "Shampoo", "personal_care", "personal_care", "potentially_replenishable"
    ),
    GoldenCase(
        "Toothpaste", "personal_care", "personal_care", "potentially_replenishable"
    ),
    GoldenCase(
        "Prescription medication", "health", "pharmacy", "potentially_replenishable"
    ),
    GoldenCase("Beauty product", "personal_care", "beauty", "potentially_replenishable"),
    GoldenCase("Dog pet food", "pets", "pet_supply", "potentially_replenishable"),
    GoldenCase(
        "Office supplies", "education_office", "education_office", "potentially_replenishable"
    ),
    GoldenCase("Cotton T-shirt", "lifestyle_shopping", "apparel", "not_replenishable"),
    GoldenCase(
        "Laptop computer", "lifestyle_shopping", "electronics", "not_replenishable"
    ),
    GoldenCase(
        "Paneer tikka restaurant", "food_dining", "restaurant_meal", "not_replenishable"
    ),
    GoldenCase("Starbucks latte", "food_dining", "coffee_beverage", "not_replenishable"),
    GoldenCase("Sports bar", "food_dining", "nightlife", "not_replenishable"),
    GoldenCase("Sales tax", "fees_taxes_discounts", "tax", "not_replenishable"),
    GoldenCase("Gratuity", "fees_taxes_discounts", "tip", "not_replenishable"),
    GoldenCase(
        "Coupon savings", "fees_taxes_discounts", "discount", "not_replenishable"
    ),
    GoldenCase("Delivery fee", "fees_taxes_discounts", "fee", "not_replenishable"),
    GoldenCase("Return credit", "fees_taxes_discounts", "refund", "not_replenishable"),
    GoldenCase("Cleaning service", "services", "service", "not_replenishable"),
    GoldenCase("HOME 24", "other_uncertain", "uncertain", "uncertain"),
)


TRANSACTION_CORPUS: tuple[GoldenCase, ...] = (
    GoldenCase("Starbucks", "food_dining", "coffee_beverage", "not_replenishable"),
    GoldenCase("Trader Joe's", "food_dining", "grocery", "uncertain"),
    GoldenCase("Safeway grocery store", "food_dining", "grocery", "uncertain"),
    GoldenCase("Costco", "lifestyle_shopping", "uncertain", "uncertain"),
    GoldenCase("Target", "lifestyle_shopping", "uncertain", "uncertain"),
    GoldenCase("Walmart", "lifestyle_shopping", "uncertain", "uncertain"),
    GoldenCase("Amazon", "lifestyle_shopping", "uncertain", "uncertain"),
    GoldenCase("Shell", "transportation", "automotive", "not_replenishable"),
    GoldenCase("Uber", "transportation", "transportation", "not_replenishable"),
    GoldenCase("Netflix", "subscriptions", "subscription", "not_replenishable"),
    GoldenCase("Electric bill utility", "household_home", "service", "not_replenishable"),
    GoldenCase(
        "CVS",
        "health",
        "pharmacy",
        "potentially_replenishable",
        "MEDICAL / PHARMACY",
    ),
    GoldenCase("Delta airline", "travel", "travel", "not_replenishable"),
    GoldenCase("Marriott hotel", "travel", "travel", "not_replenishable"),
    GoldenCase("Chipotle", "food_dining", "restaurant_meal", "not_replenishable"),
    GoldenCase("DoorDash", "food_dining", "food_delivery", "not_replenishable"),
    GoldenCase("ZXQ Mystery Vendor", "other_uncertain", "uncertain", "uncertain"),
    GoldenCase(
        "Account transfer",
        "other_uncertain",
        "non_product",
        "not_replenishable",
        "TRANSFER / ACCOUNT_TRANSFER",
    ),
    GoldenCase(
        "Card payment",
        "other_uncertain",
        "non_product",
        "not_replenishable",
        "PAYMENTS / CREDIT_CARD_PAYMENT",
    ),
    GoldenCase("Coffee refund", "fees_taxes_discounts", "refund", "not_replenishable"),
    GoldenCase("Target superstore", "lifestyle_shopping", "uncertain", "uncertain"),
)


CONCEPT_GOLDEN: dict[str, str] = {
    "Organic eggs": "Eggs",
    "Whole milk": "Milk",
    "Bread": "Bread",
    "Basmati rice": "Rice",
    "Fresh vegetables": "Vegetables",
    "Chicken breast": "Chicken",
    "Paper towels": "Paper towels",
    "Toilet paper": "Toilet paper",
    "Laundry detergent": "Laundry detergent",
    "Dish soap": "Dish soap",
    "Dishwasher tablets": "Dishwasher tablets",
    "Trash bags": "Household bags",
    "Paneer tikka restaurant": "Restaurant meal",
    "Starbucks latte": "Coffee beverage",
    "Sales tax": "Sales tax",
    "Gratuity": "Tip",
    "Coupon savings": "Discount",
    "Delivery fee": "Fee",
    "Return credit": "Refund",
}


SUBCATEGORY_GOLDEN: dict[str, str] = {
    "Paper towels": "Paper goods",
    "Laundry detergent": "Laundry supplies",
    "Dish soap": "Dishwashing supplies",
    "Trash bags": "Food storage and waste bags",
    "Shampoo": "Personal care supplies",
    "Prescription medication": "Pharmacy",
    "Office supplies": "Office supplies",
    "Cotton T-shirt": "Apparel",
    "Laptop computer": "Electronics",
    "Paneer tikka restaurant": "Restaurants",
    "Starbucks latte": "Coffee",
    "Sports bar": "Bars and nightlife",
    "Sales tax": "Taxes",
    "Gratuity": "Tips",
    "Coupon savings": "Discounts",
    "Delivery fee": "Fees",
    "Return credit": "Refunds",
}


def run_benchmark() -> dict[str, Any]:
    started = time.perf_counter()
    receipt_quality = _run_rule_corpus(RECEIPT_CORPUS, ClassificationSourceType.RECEIPT_LINE)
    transaction_quality = _run_transaction_corpus()
    week = _run_realistic_week()
    reconciliation = _run_reconciliation_corpus()
    tool_surface = _tool_surface()
    elapsed_ms = max(0.0, (time.perf_counter() - started) * 1_000)
    decisions = receipt_quality["total"] + transaction_quality["total"]
    return {
        "corpus": {
            "receipt_items": receipt_quality,
            "transactions": transaction_quality,
            "total_decisions": decisions,
        },
        "routing": week["routing"],
        "manual_work": week["manual_work"],
        "autonomous_week": week["autonomous_week"],
        "cadence": week["cadence"],
        "plaid_reconciliation": reconciliation,
        "performance_cost": {
            "benchmark_elapsed_ms": round(elapsed_ms, 3),
            "classification_latency_ms": week["classification_latency_ms"],
            "average_candidates_per_receipt_invocation": week[
                "average_candidates_per_receipt_invocation"
            ],
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0.0,
            "finalizer_runtime_ms": None,
            "backfill_rows_per_second": None,
            "receipt_to_categorized_latency_ms": week["receipt_to_categorized_latency_ms"],
            "plaid_to_categorized_latency_ms": week["plaid_to_categorized_latency_ms"],
            "classification_correction_rate_percent": None,
            "notes": (
                "No provider call is made by this deterministic benchmark. Finalizer, backfill, "
                "and real-user correction metrics require an instrumented staged rollout."
            ),
        },
        "tool_surface": tool_surface,
    }


def _run_rule_corpus(
    corpus: tuple[GoldenCase, ...], source_type: ClassificationSourceType
) -> dict[str, Any]:
    correct_parent = 0
    correct_activity = 0
    correct_replenishment = 0
    correct_concept = 0
    concept_evaluated = 0
    correct_subcategory = 0
    subcategory_evaluated = 0
    deterministic = 0
    uncertain = 0
    false_specific = 0
    latencies: list[float] = []
    failures: list[dict[str, str]] = []
    for index, case in enumerate(corpus, start=1):
        started = time.perf_counter()
        decision = classify_known_text(
            source_type=source_type,
            source_entity_id=index,
            text=case.name,
            provider_category=case.provider_category,
        )
        latencies.append((time.perf_counter() - started) * 1_000)
        parent = decision.spending_parent_category.value
        activity = decision.item_activity_type.value
        replenishment = decision.replenishment_eligibility.value
        if case.name in CONCEPT_GOLDEN:
            concept_evaluated += 1
            correct_concept += int(
                decision.canonical_concept == CONCEPT_GOLDEN[case.name]
            )
        if case.name in SUBCATEGORY_GOLDEN:
            subcategory_evaluated += 1
            correct_subcategory += int(
                decision.subcategory_name == SUBCATEGORY_GOLDEN[case.name]
            )
        matches = (
            parent == case.expected_parent,
            activity == case.expected_activity,
            replenishment == case.expected_replenishment,
        )
        correct_parent += int(matches[0])
        correct_activity += int(matches[1])
        correct_replenishment += int(matches[2])
        if parent == SpendingParentCategory.OTHER_UNCERTAIN.value:
            uncertain += 1
        else:
            deterministic += 1
        if case.expected_parent == "other_uncertain" and parent != "other_uncertain":
            false_specific += 1
        if not all(matches):
            failures.append(
                {
                    "name": case.name,
                    "expected": "/".join(
                        (
                            case.expected_parent,
                            case.expected_activity,
                            case.expected_replenishment,
                        )
                    ),
                    "actual": "/".join((parent, activity, replenishment)),
                }
            )
    total = len(corpus)
    return {
        "total": total,
        "parent_category_precision_percent": _percent(correct_parent, total),
        "activity_precision_percent": _percent(correct_activity, total),
        "replenishment_precision_percent": _percent(correct_replenishment, total),
        "canonical_concept_precision_percent": _percent(
            correct_concept, concept_evaluated
        ),
        "canonical_concept_evaluated": concept_evaluated,
        "subcategory_precision_percent": _percent(
            correct_subcategory, subcategory_evaluated
        ),
        "subcategory_evaluated": subcategory_evaluated,
        "false_specific_category_count": false_specific,
        "false_specific_category_rate_percent": _percent(false_specific, total),
        "specific_parent": deterministic,
        "other_parent": uncertain,
        "median_rule_latency_ms": round(float(statistics.median(latencies)), 6),
        "p95_rule_latency_ms": round(_percentile(latencies, 0.95), 6),
        "failures": failures,
    }


def _run_transaction_corpus() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="expenseops-day16-transactions-") as directory:
        engine = create_engine(f"sqlite:///{Path(directory) / 'transactions.db'}")
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as db:
            values = _seed_workspace(db)
            transactions: list[tuple[GoldenCase, ExpenseTransaction]] = []
            for index, case in enumerate(TRANSACTION_CORPUS, start=1):
                amount = -1_000 if case.name == "Coffee refund" else 1_000
                transaction = ExpenseTransaction(
                    workspace_id=values["workspace_id"],
                    plaid_item_id=values["plaid_item_id"],
                    plaid_transaction_id=f"day16-corpus-{index}",
                    merchant_name=case.name,
                    name=case.name,
                    amount_cents=amount,
                    iso_currency_code="USD",
                    date=date(2026, 8, 17),
                    provider_category=case.provider_category,
                    category=case.provider_category,
                )
                db.add(transaction)
                transactions.append((case, transaction))
            db.commit()
            service = AutonomousClassificationService(db, _enabled_settings())
            latencies: list[float] = []
            for _case, transaction in transactions:
                started = time.perf_counter()
                service.classify_transaction(transaction)
                latencies.append((time.perf_counter() - started) * 1_000)

            correct_parent = 0
            correct_activity = 0
            correct_replenishment = 0
            deterministic = 0
            uncertain = 0
            false_specific = 0
            failures: list[dict[str, str]] = []
            for case, transaction in transactions:
                db.refresh(transaction)
                parent = transaction.spending_parent_category
                activity = transaction.classification_activity_type
                replenishment = transaction.replenishment_eligibility
                matches = (
                    parent == case.expected_parent,
                    activity == case.expected_activity,
                    replenishment == case.expected_replenishment,
                )
                correct_parent += int(matches[0])
                correct_activity += int(matches[1])
                correct_replenishment += int(matches[2])
                if parent == "other_uncertain":
                    uncertain += 1
                else:
                    deterministic += 1
                if case.expected_parent == "other_uncertain" and parent != "other_uncertain":
                    false_specific += 1
                if not all(matches):
                    failures.append(
                        {
                            "name": case.name,
                            "expected": "/".join(
                                (
                                    case.expected_parent,
                                    case.expected_activity,
                                    case.expected_replenishment,
                                )
                            ),
                            "actual": "/".join((parent, activity, replenishment)),
                        }
                    )
            total = len(transactions)
            result = {
                "total": total,
                "parent_category_precision_percent": _percent(correct_parent, total),
                "activity_precision_percent": _percent(correct_activity, total),
                "replenishment_precision_percent": _percent(correct_replenishment, total),
                "false_specific_category_count": false_specific,
                "false_specific_category_rate_percent": _percent(false_specific, total),
                "specific_parent": deterministic,
                "other_parent": uncertain,
                "median_service_latency_ms": round(float(statistics.median(latencies)), 6),
                "p95_service_latency_ms": round(_percentile(latencies, 0.95), 6),
                "failures": failures,
            }
        engine.dispose()
    return result


def _run_realistic_week() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="expenseops-day16-week-") as directory:
        engine = create_engine(f"sqlite:///{Path(directory) / 'week.db'}")
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as db:
            values = _seed_workspace(db)
            plaid_transactions = _week_transactions(db, values)
            receipts = _week_receipts(db, values)
            trader_receipt = receipts[0]
            trader_transaction = plaid_transactions[0]
            match = ReceiptTransactionReconciliationService(db).reconcile_receipt(
                trader_receipt
            )
            db.commit()
            receipt_timings: list[float] = []
            summary_counts = {
                "receipt_items_categorized": 0,
                "transactions_categorized": 0,
                "categories_auto_created": 0,
                "household_items_auto_created": 0,
                "acquisitions_auto_recorded": 0,
            }
            service = AutonomousClassificationService(db, _enabled_settings())
            for receipt in receipts:
                started = time.perf_counter()
                summary = service.classify_receipt(receipt)
                receipt_timings.append((time.perf_counter() - started) * 1_000)
                for key in summary_counts:
                    summary_counts[key] += int(getattr(summary, key))
            transaction_timings: list[float] = []
            for transaction in plaid_transactions:
                started = time.perf_counter()
                transaction_summary = service.classify_transaction(transaction)
                transaction_timings.append((time.perf_counter() - started) * 1_000)
                summary_counts["transactions_categorized"] += int(
                    transaction_summary.transactions_categorized
                )

            all_lines = list(
                db.scalars(select(PurchaseReceiptItem).order_by(PurchaseReceiptItem.id))
            )
            all_transactions = list(
                db.scalars(select(ExpenseTransaction).order_by(ExpenseTransaction.id))
            )
            all_items = list(
                db.scalars(select(HouseholdItem).where(HouseholdItem.enabled.is_(True)))
            )
            all_acquisitions = list(
                db.scalars(
                    select(HouseholdItemAcquisition).where(
                        HouseholdItemAcquisition.voided_at.is_(None)
                    )
                )
            )
            false_staples = [
                line.raw_name
                for line in all_lines
                if line.household_item_id is not None
                and line.replenishment_eligibility != ReplenishmentEligibility.REPLENISHABLE.value
            ]
            receipt_golden = {case.name: case for case in RECEIPT_CORPUS}
            receipt_golden["Pizza"] = GoldenCase(
                "Pizza",
                "food_dining",
                "restaurant_meal",
                "not_replenishable",
            )
            line_errors = [
                line.raw_name
                for line in all_lines
                if (
                    line.spending_parent_category,
                    line.item_activity_type,
                    line.replenishment_eligibility,
                )
                != (
                    receipt_golden[line.raw_name].expected_parent,
                    receipt_golden[line.raw_name].expected_activity,
                    receipt_golden[line.raw_name].expected_replenishment,
                )
            ]
            transaction_expected_parent = {
                "Trader Joe's": "food_dining",
                "Target": "lifestyle_shopping",
                "Local restaurant": "food_dining",
                "Starbucks": "food_dining",
                "Shell": "transportation",
                "Netflix": "subscriptions",
                "ZXQ Unknown": "other_uncertain",
            }
            transaction_errors = [
                transaction.merchant_name or transaction.name
                for transaction in all_transactions
                if transaction.spending_parent_category
                != transaction_expected_parent[transaction.merchant_name or transaction.name]
            ]
            required_corrections = len(line_errors) + len(transaction_errors)
            lines_by_id = {line.id: line for line in all_lines}
            transactions_by_id = {
                transaction.id: transaction for transaction in all_transactions
            }
            creation_records = list(
                db.scalars(
                    select(ClassificationDecisionRecord).where(
                        (
                            ClassificationDecisionRecord.created_subcategory.is_(True)
                        )
                        | (ClassificationDecisionRecord.created_concept.is_(True))
                    )
                )
            )
            false_subcategory_creations = 0
            false_concept_creations = 0
            for record in creation_records:
                if record.source_type == "receipt_line":
                    line = lines_by_id[record.source_entity_id]
                    expected_parent = receipt_golden[line.raw_name].expected_parent
                else:
                    transaction = transactions_by_id[record.source_entity_id]
                    expected_parent = transaction_expected_parent[
                        transaction.merchant_name or transaction.name
                    ]
                incorrect_creation = (
                    expected_parent == "other_uncertain"
                    or record.spending_parent_category != expected_parent
                )
                false_subcategory_creations += int(
                    record.created_subcategory and incorrect_creation
                )
                false_concept_creations += int(record.created_concept and incorrect_creation)
            routed_rows = [*all_lines, *all_transactions]
            deterministic_or_provider = sum(
                value.classification_authority not in {"fallback", "model_evidence"}
                for value in routed_rows
            )
            provisional = sum(
                value.classification_decision_state == "provisional"
                for value in routed_rows
            )
            uncertain = sum(
                value.spending_parent_category == "other_uncertain"
                for value in routed_rows
            )
            routing = {
                "decision_count": len(routed_rows),
                "deterministic_or_provider_count": deterministic_or_provider,
                "deterministic_or_provider_percent": _percent(
                    deterministic_or_provider, len(routed_rows)
                ),
                "model_candidate_count": provisional,
                "model_candidate_percent": _percent(provisional, len(routed_rows)),
                "model_calls": 0,
                "model_average_batch_size": 0.0,
                "provisional_count": provisional,
                "provisional_percent": _percent(provisional, len(routed_rows)),
                "uncertain_projection_count": uncertain,
                "uncertain_projection_percent": _percent(uncertain, len(routed_rows)),
            }
            cadence_before_second = {
                item.name: item.cadence_source
                for item in all_items
                if item.cadence_days is not None
            }
            eggs = next(item for item in all_items if item.name.casefold() == "eggs")
            second = _receipt(
                db,
                workspace_id=values["workspace_id"],
                owner_user_id=values["user_id"],
                source="gmail",
                external_id="week-eggs-second",
                merchant="Trader Joe's",
                purchased_at=datetime.now(UTC),
                line_names=("Organic eggs",),
            )
            service.classify_receipt(second)
            db.refresh(eggs)
            observed_error_days = (
                abs(int(eggs.cadence_days) - 10) if eggs.cadence_days is not None else None
            )

            before = {
                "receipt_items_manually_categorized": len(all_lines),
                "staples_manually_created": 10,
                "cadence_values_manually_entered": 10,
                "receipt_confirmations": len(receipts),
                "transaction_categories_manually_set": len(all_transactions),
                "receipt_plaid_links_manually_made": 1,
            }
            # The second eggs receipt is a cadence evaluation record, not part of the
            # simulated week whose baseline is measured above.
            before_total = sum(before.values())
            after = {
                "required_user_corrections": required_corrections,
                "required_setup_or_confirmation_actions": 0,
                "optional_uncertain_rows_available_for_review": sum(
                    line.spending_parent_category == "other_uncertain" for line in all_lines
                )
                + sum(
                    transaction.spending_parent_category == "other_uncertain"
                    for transaction in all_transactions
                ),
            }
            after_required = after["required_user_corrections"]
            manual_work = {
                "scope": (
                    "Synthetic supported week: three receipts (Gmail, web photo, restaurant), "
                    "seven Plaid rows, one unique receipt match, and zero pre-created staples."
                ),
                "before": before,
                "before_required_actions": before_total,
                "after": after,
                "after_required_actions": after_required,
                "required_action_reduction": before_total - after_required,
                "required_action_reduction_percent": _percent(
                    before_total - after_required, before_total
                ),
                "interpretation": (
                    "Other / Uncertain is a valid completed category and is counted as optional "
                    "review, not mandatory manual work. This synthetic result is not a measured "
                    "production correction rate."
                ),
            }
            week = {
                **summary_counts,
                "meaningful_receipt_lines": len(all_lines),
                "categorized_receipt_lines": sum(
                    line.classification_applied_at is not None for line in all_lines
                ),
                "eligible_transactions": len(all_transactions),
                "categorized_transactions": sum(
                    transaction.classification_applied_at is not None
                    for transaction in all_transactions
                ),
                "active_household_items": len(all_items),
                "active_acquisitions": len(all_acquisitions),
                "false_staple_count": len(false_staples),
                "false_staples": false_staples,
                "classification_error_count": required_corrections,
                "classification_errors": [*line_errors, *transaction_errors],
                "auto_apply_precision_percent": _percent(
                    len(routed_rows) - required_corrections, len(routed_rows)
                ),
                "false_auto_category_creation_count": false_subcategory_creations,
                "false_auto_concept_creation_count": false_concept_creations,
                "trader_joes_match_status": match.status.value,
                "trader_joes_match_correct": (
                    match.status is ReceiptTransactionMatchStatus.AUTO_MATCHED
                    and match.transaction_id == trader_transaction.id
                ),
                "review_was_required": False,
            }
            cadence = {
                "first_purchase_prior_count": len(cadence_before_second),
                "first_purchase_sources": dict(sorted(cadence_before_second.items())),
                "observed_history_replaced_prior": eggs.cadence_source == "observed",
                "observed_interval_days": 10,
                "observed_cadence_days": eggs.cadence_days,
                "absolute_error_days": observed_error_days,
                "model_prior_evaluated": False,
                "irregular_interval_evaluated": False,
                "notes": (
                    "The fixed benchmark measures category-prior coverage and one exact observed "
                    "interval transition. Model-prior and irregular-interval quality remain gated "
                    "by their dedicated tests and later production observations."
                ),
            }
            result = {
                "manual_work": manual_work,
                "autonomous_week": week,
                "cadence": cadence,
                "routing": routing,
                "classification_latency_ms": {
                    "receipt_median": round(float(statistics.median(receipt_timings)), 3),
                    "receipt_p95": round(_percentile(receipt_timings, 0.95), 3),
                    "transaction_median": round(float(statistics.median(transaction_timings)), 3),
                    "transaction_p95": round(_percentile(transaction_timings, 0.95), 3),
                },
                "average_candidates_per_receipt_invocation": round(
                    len(all_lines) / len(receipts), 3
                ),
                "receipt_to_categorized_latency_ms": round(sum(receipt_timings), 3),
                "plaid_to_categorized_latency_ms": round(sum(transaction_timings), 3),
            }
        engine.dispose()
    return result


def _run_reconciliation_corpus() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="expenseops-day16-reconciliation-") as directory:
        engine = create_engine(f"sqlite:///{Path(directory) / 'reconciliation.db'}")
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as db:
            values = _seed_workspace(db)
            plaid_item_id = values["plaid_item_id"]
            workspace_id = values["workspace_id"]
            results: list[ReconciliationCaseResult] = []

            exact_tx = _transaction(
                db, workspace_id, plaid_item_id, "recon-exact", "TRADER JOES #177", 5_483
            )
            exact_receipt = _plain_receipt(
                db, workspace_id, "recon-exact", "Trader Joe's", 5_483
            )
            results.append(
                _reconcile_result(
                    db,
                    "exact Trader Joe's",
                    exact_receipt,
                    "auto_matched",
                    exact_tx.id,
                )
            )

            _transaction(db, workspace_id, plaid_item_id, "recon-different", "Shell", 3_123)
            different_receipt = _plain_receipt(
                db, workspace_id, "recon-different", "Acme Bakery", 3_123
            )
            results.append(
                _reconcile_result(
                    db,
                    "exact amount different merchant",
                    different_receipt,
                    "no_match",
                )
            )

            _transaction(db, workspace_id, plaid_item_id, "recon-tie-a", "Target", 4_321)
            _transaction(db, workspace_id, plaid_item_id, "recon-tie-b", "Target", 4_321)
            ambiguous_receipt = _plain_receipt(
                db, workspace_id, "recon-ambiguous", "Target", 4_321
            )
            results.append(
                _reconcile_result(db, "near-tied candidates", ambiguous_receipt, "ambiguous")
            )

            no_match_receipt = _plain_receipt(
                db, workspace_id, "recon-none", "Corner Market", 9_876
            )
            results.append(
                _reconcile_result(db, "no Plaid candidate", no_match_receipt, "no_match")
            )

            generic = (
                ("Costco Wholesale", "COSTCO WHSE #123", 6_101, "grocery"),
                ("Target", "Target Store 1843", 6_102, "mixed retailer"),
                ("Olive Garden", "Olive Garden Italian Restaurant", 6_103, "restaurant"),
                ("Best Buy", "BEST BUY #102", 6_104, "ordinary retail"),
            )
            for index, (receipt_merchant, transaction_merchant, amount, label) in enumerate(
                generic, start=1
            ):
                transaction = _transaction(
                    db,
                    workspace_id,
                    plaid_item_id,
                    f"recon-generic-tx-{index}",
                    transaction_merchant,
                    amount,
                )
                receipt = _plain_receipt(
                    db,
                    workspace_id,
                    f"recon-generic-receipt-{index}",
                    receipt_merchant,
                    amount,
                )
                results.append(
                    _reconcile_result(db, label, receipt, "auto_matched", transaction.id)
                )

            pending = _transaction(
                db,
                workspace_id,
                plaid_item_id,
                "recon-pending",
                "Costco",
                6_105,
                pending=True,
            )
            posted = _transaction(
                db,
                workspace_id,
                plaid_item_id,
                "recon-posted",
                "Costco",
                6_105,
            )
            posted_receipt = _plain_receipt(
                db, workspace_id, "recon-posted-receipt", "Costco", 6_105
            )
            results.append(
                _reconcile_result(
                    db,
                    "posted preferred to equivalent pending",
                    posted_receipt,
                    "auto_matched",
                    posted.id,
                )
            )
            assert pending.id != posted.id
            auto_results = [result for result in results if result.actual_status == "auto_matched"]
            expected_auto = [
                result for result in results if result.expected_status == "auto_matched"
            ]
            correct_auto = sum(result.correct for result in auto_results)
            correct = sum(result.correct for result in results)
            ambiguous = sum(result.actual_status == "ambiguous" for result in results)
            output = {
                "scenario_count": len(results),
                "outcome_accuracy_percent": _percent(correct, len(results)),
                "auto_match_precision_percent": _percent(correct_auto, len(auto_results)),
                "auto_match_recall_percent": _percent(correct_auto, len(expected_auto)),
                "false_auto_match_count": sum(
                    result.actual_status == "auto_matched" and not result.correct
                    for result in results
                ),
                "ambiguous_match_rate_percent": _percent(ambiguous, len(results)),
                "results": [asdict(result) | {"correct": result.correct} for result in results],
            }
        engine.dispose()
    return output


def _seed_workspace(db: Session) -> dict[str, int]:
    user = User(email="day16-benchmark@example.test", display_name="Day 16 Benchmark")
    db.add(user)
    db.flush()
    workspace = Workspace(name="Day 16 Benchmark", created_by_user_id=user.id)
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role="owner",
            is_default=True,
        )
    )
    plaid = PlaidItem(
        workspace_id=workspace.id,
        item_id=f"day16-benchmark-{workspace.id}",
        owner_user_id=user.id,
    )
    db.add(plaid)
    db.add(
        ClassificationSettings(
            workspace_id=workspace.id,
            autonomous_enabled=True,
            category_creation_enabled=True,
            cadence_estimation_enabled=True,
            grace_hours=24,
        )
    )
    db.flush()
    db.info.update(workspace_id=workspace.id, user_id=user.id)
    return {
        "user_id": user.id,
        "workspace_id": workspace.id,
        "plaid_item_id": plaid.id,
    }


def _week_transactions(db: Session, values: dict[str, int]) -> list[ExpenseTransaction]:
    cases = (
        ("Trader Joe's", 800, "Food and Drink / Groceries"),
        ("Target", 1_400, "General Merchandise / Superstores"),
        ("Local restaurant", 400, "Food and Drink / Restaurants"),
        ("Starbucks", 650, "Food and Drink / Coffee Shop"),
        ("Shell", 4_500, "Transportation / Gas"),
        ("Netflix", 1_599, "Entertainment / Subscription"),
        ("ZXQ Unknown", 999, None),
    )
    values_out: list[ExpenseTransaction] = []
    for index, (merchant, amount, category) in enumerate(cases, start=1):
        transaction = ExpenseTransaction(
            workspace_id=values["workspace_id"],
            plaid_item_id=values["plaid_item_id"],
            plaid_transaction_id=f"week-tx-{index}",
            merchant_name=merchant,
            name=merchant,
            amount_cents=amount,
            iso_currency_code="USD",
            date=date.today() - timedelta(days=10 if index == 1 else 2),
            provider_category=category,
            category=category,
        )
        db.add(transaction)
        values_out.append(transaction)
    db.flush()
    return values_out


def _week_receipts(db: Session, values: dict[str, int]) -> list[PurchaseReceipt]:
    return [
        _receipt(
            db,
            workspace_id=values["workspace_id"],
            owner_user_id=values["user_id"],
            source="gmail",
            external_id="week-trader-joes",
            merchant="Trader Joe's",
            purchased_at=datetime.now(UTC) - timedelta(days=10),
            line_names=(
                "Organic eggs",
                "Whole milk",
                "Bread",
                "Basmati rice",
                "Fresh vegetables",
                "Chicken breast",
                "Paper towels",
                "Laundry detergent",
            ),
        ),
        _receipt(
            db,
            workspace_id=values["workspace_id"],
            owner_user_id=values["user_id"],
            source="web",
            external_id="week-target-photo",
            merchant="Target",
            purchased_at=datetime.now(UTC) - timedelta(days=2),
            line_names=(
                "Dish soap",
                "Trash bags",
                "Shampoo",
                "Cotton T-shirt",
                "Laptop computer",
                "HOME 24",
            ),
        ),
        _receipt(
            db,
            workspace_id=values["workspace_id"],
            owner_user_id=values["user_id"],
            source="gmail",
            external_id="week-restaurant",
            merchant="Local restaurant",
            purchased_at=datetime.now(UTC) - timedelta(days=1),
            line_names=("Paneer tikka restaurant", "Pizza", "Gratuity", "Sales tax"),
        ),
    ]


def _receipt(
    db: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    source: str,
    external_id: str,
    merchant: str,
    purchased_at: datetime,
    line_names: tuple[str, ...],
) -> PurchaseReceipt:
    total = len(line_names) * 100
    receipt = PurchaseReceipt(
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        source=source,
        source_external_id=external_id,
        merchant_raw=merchant,
        merchant_normalized=normalize_merchant(merchant),
        purchased_at=purchased_at,
        subtotal_cents=total,
        total_cents=total,
        currency="USD",
        line_items_complete=True,
        arithmetic_status="verified",
        parse_status="needs_review",
        parse_confidence=0.99,
    )
    db.add(receipt)
    db.flush()
    for name in line_names:
        db.add(
            PurchaseReceiptItem(
                receipt_id=receipt.id,
                raw_name=name,
                normalized_name=" ".join(name.casefold().split()),
                line_total_cents=100,
                classification_confidence=0.99,
                match_confidence=0.99,
            )
        )
    db.flush()
    return receipt


def _transaction(
    db: Session,
    workspace_id: int,
    plaid_item_id: int,
    external_id: str,
    merchant: str,
    amount_cents: int,
    *,
    pending: bool = False,
) -> ExpenseTransaction:
    transaction = ExpenseTransaction(
        workspace_id=workspace_id,
        plaid_item_id=plaid_item_id,
        plaid_transaction_id=external_id,
        merchant_name=merchant,
        name=merchant,
        amount_cents=amount_cents,
        iso_currency_code="USD",
        date=date.today(),
        pending=pending,
    )
    db.add(transaction)
    db.flush()
    return transaction


def _plain_receipt(
    db: Session,
    workspace_id: int,
    external_id: str,
    merchant: str,
    total_cents: int,
) -> PurchaseReceipt:
    receipt = PurchaseReceipt(
        workspace_id=workspace_id,
        source="web",
        source_external_id=external_id,
        merchant_raw=merchant,
        merchant_normalized=normalize_merchant(merchant),
        purchased_at=datetime.now(UTC),
        total_cents=total_cents,
        currency="USD",
        parse_status="needs_review",
    )
    db.add(receipt)
    db.flush()
    return receipt


def _reconcile_result(
    db: Session,
    name: str,
    receipt: PurchaseReceipt,
    expected_status: str,
    expected_transaction_id: int | None = None,
) -> ReconciliationCaseResult:
    decision = ReceiptTransactionReconciliationService(db).reconcile_receipt(receipt)
    db.flush()
    return ReconciliationCaseResult(
        name=name,
        expected_status=expected_status,
        actual_status=decision.status.value,
        expected_transaction_id=expected_transaction_id,
        actual_transaction_id=decision.transaction_id,
    )


def _enabled_settings() -> Settings:
    return Settings(
        _env_file=None,
        autonomous_classification_enabled=True,
        autonomous_category_creation_enabled=True,
        autonomous_cadence_estimation_enabled=True,
    )


def _tool_surface() -> dict[str, Any]:
    settings = Settings(
        _env_file=None,
        agent_enabled=True,
        agent_read_tools_enabled=True,
        agent_write_actions_enabled=True,
        agent_proactive_enabled=False,
        agent_purchasing_enabled=False,
    )
    registry = build_read_tool_registry(settings)
    read_metadata = [value.model_dump(mode="json") for value in registry.metadata()]
    read_bytes = _json_bytes(read_metadata)
    register_action_tools(registry)
    all_metadata = [value.model_dump(mode="json") for value in registry.metadata()]
    all_bytes = _json_bytes(all_metadata)
    return {
        "registered_read_tools": len(read_metadata),
        "read_tool_schema_bytes": read_bytes,
        "registered_total_tools": len(all_metadata),
        "total_tool_schema_bytes": all_bytes,
        "day13_baseline_total_tools": DAY13_TOTAL_TOOL_COUNT,
        "day13_baseline_total_schema_bytes": DAY13_TOTAL_TOOL_SCHEMA_BYTES,
        "day16_checkpoint_read_tool_schema_bytes": DAY16_READ_TOOL_SCHEMA_BYTES,
        "day16_checkpoint_total_tools": DAY16_TOTAL_TOOL_COUNT,
        "day16_checkpoint_total_tool_schema_bytes": DAY16_TOTAL_TOOL_SCHEMA_BYTES,
        "day16_total_tool_growth": DAY16_TOTAL_TOOL_COUNT - DAY13_TOTAL_TOOL_COUNT,
        "day16_total_schema_growth_bytes": (
            DAY16_TOTAL_TOOL_SCHEMA_BYTES - DAY13_TOTAL_TOOL_SCHEMA_BYTES
        ),
        "day16_approx_schema_growth_tokens": round(
            (DAY16_TOTAL_TOOL_SCHEMA_BYTES - DAY13_TOTAL_TOOL_SCHEMA_BYTES) / 4
        ),
        "day17_read_schema_growth_bytes": read_bytes - DAY16_READ_TOOL_SCHEMA_BYTES,
        "day17_total_schema_growth_bytes": all_bytes - DAY16_TOTAL_TOOL_SCHEMA_BYTES,
        "day17_approx_total_schema_growth_tokens": round(
            (all_bytes - DAY16_TOTAL_TOOL_SCHEMA_BYTES) / 4
        ),
        "classification_activity_tool_present": any(
            value["name"] == "get_classification_activity" for value in read_metadata
        ),
    }


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 3)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return float(ordered[rank - 1])


def _json_bytes(value: Any) -> int:
    return len(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the deterministic Day 16 quality benchmark.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    args = parser.parse_args()
    print(json.dumps(run_benchmark(), indent=2 if args.pretty else None, sort_keys=True))


if __name__ == "__main__":
    main()

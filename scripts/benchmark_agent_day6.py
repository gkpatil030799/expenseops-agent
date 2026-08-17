from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from agents.strict_schema import ensure_strict_json_schema
from pydantic import BaseModel

from app.agent.context import build_contextual_tool_policy
from app.agent.contracts import AgentPageContext, AgentPageEntity, AgentSurface
from app.agent.deals_errands_tools import ErrandsAndPlanOutput, RelevantDealsOutput
from app.agent.household_receipt_tools import HouseholdReplenishmentOutput, ReceiptsOutput
from app.agent.integration_read_tool import IntegrationStatusToolOutput
from app.agent.read_tools import (
    SpendingInsightsOutput,
    TransactionSearchOutput,
    build_read_tool_registry,
)
from app.agent.runtime import (
    ReadOnlyModelResponse,
    ReadToolEvidence,
    ReadToolFailure,
    build_run_evidence_bundle,
    compose_grounded_response,
)
from app.config import Settings

DEFAULT_REPETITIONS = 250
DEFAULT_WARMUPS = 25
BENCHMARK_VERSION = "day6-v2"

_OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "get_spending_insights": SpendingInsightsOutput,
    "search_transactions": TransactionSearchOutput,
    "get_household_replenishment": HouseholdReplenishmentOutput,
    "get_receipts": ReceiptsOutput,
    "get_relevant_deals": RelevantDealsOutput,
    "get_errands_and_plan": ErrandsAndPlanOutput,
    "get_integration_status": IntegrationStatusToolOutput,
}


@dataclass(frozen=True, slots=True)
class SeedEvidence:
    tool_name: str
    arguments: dict[str, Any]
    output: dict[str, Any]
    tool_version: str = "1.0"


@dataclass(frozen=True, slots=True)
class SeedFailure:
    tool_name: str
    code: str
    partial_recoverable: bool = True


@dataclass(frozen=True, slots=True)
class BenchmarkScenario:
    name: str
    evidence: tuple[SeedEvidence, ...]
    failures: tuple[SeedFailure, ...] = ()
    page_context: AgentPageContext | None = None
    contextual_tool: str | None = None
    contextual_arguments: dict[str, Any] | None = None
    user_text: str = "Show the relevant ExpenseOps information."


@dataclass(frozen=True, slots=True)
class IterationMeasurement:
    application_processing_ms: float
    total_ms: float
    canonical_response_bytes: int
    response_payload_bytes: int
    evidence_bytes: int
    tool_call_count: int
    evidence_set_count: int
    failure_count: int
    completion_state: str


def benchmark_scenarios() -> tuple[BenchmarkScenario, ...]:
    """Return the exact ten deterministic Day 6 benchmark scenarios."""

    spending = SeedEvidence(
        "get_spending_insights",
        {
            "start_date": "2026-08-01",
            "end_date": "2026-08-16",
            "account_id": None,
            "category": "Food & Dining",
            "merchant": None,
            "review_type": None,
            "spend_basis": "card",
            "comparison_mode": "immediately_preceding",
            "currency_code": "USD",
        },
        _spending_output(),
        tool_version="1.2",
    )
    transactions = SeedEvidence(
        "search_transactions",
        {
            "start_date": "2026-08-01",
            "end_date": "2026-08-16",
            "transaction_id": None,
            "merchant": None,
            "category": "Food & Dining",
            "review_type": None,
            "review_status": None,
            "min_amount_cents": None,
            "max_amount_cents": None,
            "currency_code": "USD",
            "include_pending": False,
            "limit": 20,
        },
        _transaction_output(),
    )
    replenishment = SeedEvidence(
        "get_household_replenishment",
        {"view": "due", "horizon_days": 7, "limit": 10},
        _replenishment_output(),
    )
    deals = SeedEvidence(
        "get_relevant_deals",
        {"query": "detergent", "need_related_only": True, "limit": 8},
        _deal_output(),
    )
    contextual_deal = SeedEvidence(
        "get_relevant_deals",
        {"deal_id": 41, "limit": 8},
        _deal_output(public_id="41"),
    )
    receipts = SeedEvidence(
        "get_receipts",
        {"view": "needs_review", "limit": 10, "line_limit": 25},
        _receipt_output(),
        tool_version="1.2",
    )
    errands = SeedEvidence(
        "get_errands_and_plan",
        {"status": "active", "include_latest_plan": True, "limit": 20},
        _errand_output(),
        tool_version="1.1",
    )
    integrations = SeedEvidence(
        "get_integration_status",
        {"providers": None},
        _integration_output(),
    )

    return (
        BenchmarkScenario(
            "spending-only",
            (spending,),
            user_text="How much did I spend this month?",
        ),
        BenchmarkScenario(
            "transaction-only",
            (transactions,),
            user_text="Show my recent Food & Dining transactions.",
        ),
        BenchmarkScenario(
            "replenishment-only",
            (replenishment,),
            user_text="What household items are due?",
        ),
        BenchmarkScenario(
            "deal-only",
            (deals,),
            user_text="Show useful current deals.",
        ),
        BenchmarkScenario(
            "contextual-single-domain",
            (contextual_deal,),
            page_context=AgentPageContext(
                surface=AgentSurface.DEALS,
                entity=AgentPageEntity(kind="deal", public_id="41"),
            ),
            contextual_tool="get_relevant_deals",
            contextual_arguments={"limit": 8},
            user_text="Tell me more about this.",
        ),
        BenchmarkScenario(
            "replenishment-plus-deals",
            (replenishment, deals),
            user_text="Do I probably need detergent, and is there a useful deal?",
        ),
        BenchmarkScenario(
            "spending-plus-transactions",
            (spending, transactions),
            user_text=("Why was Food & Dining higher this month, and which transactions drove it?"),
        ),
        BenchmarkScenario(
            "attention-summary-multi-domain",
            (receipts, errands, integrations),
            user_text="What needs my attention today?",
        ),
        BenchmarkScenario(
            "partial-tool-failure",
            (transactions, receipts),
            failures=(SeedFailure("get_relevant_deals", "tool_execution_failed"),),
            user_text="What needs my attention today?",
        ),
        BenchmarkScenario(
            "maximum-legal-bounded-response",
            (
                SeedEvidence(
                    "search_transactions",
                    {"limit": 25},
                    _transaction_output(count=25),
                ),
                SeedEvidence(
                    "get_household_replenishment",
                    {"view": "due", "horizon_days": 90, "limit": 20},
                    _replenishment_output(count=20),
                ),
                SeedEvidence(
                    "get_relevant_deals",
                    {"limit": 12},
                    _deal_output(count=12),
                ),
            ),
            user_text="Show bounded transaction, household, and deal details.",
        ),
    )


def run_benchmark(
    *,
    repetitions: int = DEFAULT_REPETITIONS,
    warmups: int = DEFAULT_WARMUPS,
) -> dict[str, Any]:
    if not 1 <= repetitions <= 10_000:
        raise ValueError("repetitions must be between 1 and 10,000")
    if not 0 <= warmups <= 1_000:
        raise ValueError("warmups must be between 0 and 1,000")

    scenarios = benchmark_scenarios()
    schema_bytes = _tool_schema_bytes()
    provider_completion_schema_bytes = _provider_completion_schema_bytes()
    suite_started = time.perf_counter_ns()
    results: list[dict[str, Any]] = []
    all_processing: list[float] = []
    all_total: list[float] = []

    for scenario in scenarios:
        for _ in range(warmups):
            _run_iteration(scenario)
        measurements = [_run_iteration(scenario) for _ in range(repetitions)]
        processing = [value.application_processing_ms for value in measurements]
        total = [value.total_ms for value in measurements]
        all_processing.extend(processing)
        all_total.extend(total)
        first = measurements[0]
        if any(
            (
                value.canonical_response_bytes,
                value.response_payload_bytes,
                value.evidence_bytes,
                value.tool_call_count,
                value.evidence_set_count,
                value.failure_count,
                value.completion_state,
            )
            != (
                first.canonical_response_bytes,
                first.response_payload_bytes,
                first.evidence_bytes,
                first.tool_call_count,
                first.evidence_set_count,
                first.failure_count,
                first.completion_state,
            )
            for value in measurements[1:]
        ):
            raise RuntimeError(f"scenario '{scenario.name}' produced non-deterministic output")
        results.append(
            {
                "scenario": scenario.name,
                "repetitions": repetitions,
                "application_processing_ms": _summary(processing),
                "total_ms": _summary(total),
                "canonical_response_bytes": first.canonical_response_bytes,
                "response_payload_bytes": first.response_payload_bytes,
                "evidence_bytes": first.evidence_bytes,
                "tool_call_count": first.tool_call_count,
                "evidence_set_count": first.evidence_set_count,
                "failure_count": first.failure_count,
                "completion_state": first.completion_state,
                "context_bytes": _json_bytes(
                    scenario.page_context.model_dump(mode="json", exclude_none=True)
                )
                if scenario.page_context
                else 0,
            }
        )

    suite_elapsed_ms = (time.perf_counter_ns() - suite_started) / 1_000_000
    partial_scenarios = sum(1 for row in results if row["completion_state"] == "partial")
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "scenario_count": len(scenarios),
        "repetitions_per_scenario": repetitions,
        "warmups_per_scenario": warmups,
        "tool_schema_bytes": schema_bytes,
        "provider_completion_schema_bytes": provider_completion_schema_bytes,
        "method": {
            "clock": "time.perf_counter_ns",
            "median": "statistics.median",
            "p95": "nearest-rank",
            "network": False,
            "provider": False,
            "raw_payloads_logged": False,
            "canonical_response_projection": "model_dump_json(exclude_none=True)",
            "response_payload_projection": "model_dump_json()",
        },
        "overall": {
            "application_processing_ms": _summary(all_processing),
            "total_ms": _summary(all_total),
            "suite_wall_ms": round(suite_elapsed_ms, 3),
            "partial_scenario_count": partial_scenarios,
            "seeded_partial_scenario_rate": round(partial_scenarios / len(results), 6),
        },
        "scenarios": results,
    }


def format_markdown(result: dict[str, Any]) -> str:
    lines = [
        "| Scenario | App median ms | App p95 ms | Total median ms | Total p95 ms | "
        "State | Calls | Evidence sets | Failures | Evidence bytes | Compact bytes | "
        "Payload bytes |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in result["scenarios"]:
        lines.append(
            "| {scenario} | {app_median:.3f} | {app_p95:.3f} | {total_median:.3f} | "
            "{total_p95:.3f} | {completion_state} | {tool_call_count} | "
            "{evidence_set_count} | {failure_count} | {evidence_bytes} | "
            "{canonical_response_bytes} | {response_payload_bytes} |".format(
                scenario=row["scenario"],
                app_median=row["application_processing_ms"]["median"],
                app_p95=row["application_processing_ms"]["p95"],
                total_median=row["total_ms"]["median"],
                total_p95=row["total_ms"]["p95"],
                completion_state=row["completion_state"],
                tool_call_count=row["tool_call_count"],
                evidence_set_count=row["evidence_set_count"],
                failure_count=row["failure_count"],
                evidence_bytes=row["evidence_bytes"],
                canonical_response_bytes=row["canonical_response_bytes"],
                response_payload_bytes=row["response_payload_bytes"],
            )
        )
    overall = result["overall"]
    lines.extend(
        [
            "",
            (
                f"Overall application processing: median "
                f"{overall['application_processing_ms']['median']:.3f} ms, p95 "
                f"{overall['application_processing_ms']['p95']:.3f} ms."
            ),
            (
                f"Overall deterministic iteration: median {overall['total_ms']['median']:.3f} ms, "
                f"p95 {overall['total_ms']['p95']:.3f} ms."
            ),
            f"Measured suite wall time: {overall['suite_wall_ms']:.3f} ms.",
            f"Registered tool-schema projection: {result['tool_schema_bytes']} bytes.",
            (
                "Strict provider-completion schema: "
                f"{result['provider_completion_schema_bytes']} bytes."
            ),
        ]
    )
    return "\n".join(lines)


def _run_iteration(scenario: BenchmarkScenario) -> IterationMeasurement:
    total_started = time.perf_counter_ns()
    raw_evidence = deepcopy(scenario.evidence)
    raw_failures = deepcopy(scenario.failures)

    processing_started = time.perf_counter_ns()
    if scenario.page_context is not None:
        context = AgentPageContext.model_validate(
            scenario.page_context.model_dump(mode="json", exclude_none=True)
        )
        policy = build_contextual_tool_policy(
            text=scenario.user_text,
            page_context=context,
            history=(),
        )
        if scenario.contextual_tool is None:
            raise RuntimeError("contextual benchmark scenario is missing its tool")
        effective = policy.apply(
            scenario.contextual_tool,
            deepcopy(scenario.contextual_arguments or {}),
        )
        expected = raw_evidence[0].arguments
        if effective != expected:
            raise RuntimeError(
                f"contextual arguments changed: expected {expected!r}, got {effective!r}"
            )

    evidence = tuple(
        _validated_evidence(item, sequence=sequence) for sequence, item in enumerate(raw_evidence)
    )
    failures = tuple(
        ReadToolFailure(
            tool_name=item.tool_name,
            code=item.code,
            sequence=len(evidence) + sequence,
            latency_ms=0,
            partial_recoverable=item.partial_recoverable,
        )
        for sequence, item in enumerate(raw_failures)
    )
    bundle = build_run_evidence_bundle(evidence, failures)
    response = compose_grounded_response(
        bundle,
        user_text=scenario.user_text,
        current_date=date(2026, 8, 16),
    )
    _assert_scenario_response(
        scenario,
        response.model_dump(mode="json", exclude_none=True),
    )
    canonical_response_json = response.model_dump_json(exclude_none=True)
    response_payload_json = response.model_dump_json()
    # Both projections must remain valid. The latter mirrors the default
    # structured-response JSON persisted by the service, not its SSE envelope.
    type(response).model_validate_json(canonical_response_json)
    type(response).model_validate_json(response_payload_json)
    processing_ms = (time.perf_counter_ns() - processing_started) / 1_000_000
    evidence_projection = [
        {
            "tool_name": item.tool_name,
            "tool_version": getattr(item, "tool_version", "1.0"),
            "arguments": item.arguments,
            "output": item.output,
        }
        for item in bundle.evidence_sets
    ]
    evidence_set_count = len(
        {
            hashlib.sha256(_canonical_json(item).encode("utf-8")).hexdigest()
            for item in evidence_projection
        }
    )
    evidence_bytes = _json_bytes(evidence_projection)
    canonical_response_bytes = len(canonical_response_json.encode("utf-8"))
    response_payload_bytes = len(response_payload_json.encode("utf-8"))
    total_ms = (time.perf_counter_ns() - total_started) / 1_000_000
    return IterationMeasurement(
        application_processing_ms=processing_ms,
        total_ms=total_ms,
        canonical_response_bytes=canonical_response_bytes,
        response_payload_bytes=response_payload_bytes,
        evidence_bytes=evidence_bytes,
        tool_call_count=len(evidence) + len(failures),
        evidence_set_count=evidence_set_count,
        failure_count=len(bundle.failures),
        completion_state=bundle.completion_state,
    )


def _assert_scenario_response(
    scenario: BenchmarkScenario,
    response: dict[str, Any],
) -> None:
    blocks = list(response.get("blocks") or [])
    types = [block.get("type") for block in blocks]
    expected_types = {
        "spending-only": {"text", "spending_summary"},
        "transaction-only": {"text", "transaction_list"},
        "replenishment-only": {"text", "replenishment_summary"},
        "deal-only": {"text", "deal_list"},
        "contextual-single-domain": {"text", "deal_list"},
        "replenishment-plus-deals": {
            "text",
            "replenishment_summary",
            "deal_list",
        },
        "spending-plus-transactions": {
            "text",
            "spending_summary",
            "transaction_list",
        },
        "attention-summary-multi-domain": {"text", "attention_summary"},
        "partial-tool-failure": {"text", "attention_summary"},
        "maximum-legal-bounded-response": {
            "text",
            "transaction_list",
            "replenishment_summary",
            "deal_list",
        },
    }[scenario.name]
    if set(types) != expected_types:
        raise RuntimeError(f"scenario '{scenario.name}' produced unexpected block types {types!r}")
    if len(blocks) > 12:
        raise RuntimeError(f"scenario '{scenario.name}' exceeded the response block budget")

    text = " ".join(str(block.get("text") or "") for block in blocks if block.get("type") == "text")
    if scenario.name == "spending-plus-transactions" and "supporting detail" not in text:
        raise RuntimeError("aligned transaction evidence was not labeled as supporting detail")
    if scenario.name == "partial-tool-failure":
        attention = next(block for block in blocks if block.get("type") == "attention_summary")
        if attention.get("status") != "partial" or attention.get("unavailable_domains") != [
            "deals"
        ]:
            raise RuntimeError("partial benchmark coverage was not explicit")
    if scenario.name == "maximum-legal-bounded-response":
        transaction_block = next(
            block for block in blocks if block.get("type") == "transaction_list"
        )
        replenishment_block = next(
            block for block in blocks if block.get("type") == "replenishment_summary"
        )
        deal_block = next(block for block in blocks if block.get("type") == "deal_list")
        if (
            len(transaction_block.get("transactions") or []) > 8
            or len(replenishment_block.get("items") or []) > 8
            or len(deal_block.get("deals") or []) > 6
        ):
            raise RuntimeError("maximum benchmark response exceeded multi-domain row caps")


def _validated_evidence(seed: SeedEvidence, *, sequence: int) -> ReadToolEvidence:
    model = _OUTPUT_MODELS[seed.tool_name]
    output = model.model_validate(seed.output, strict=True).model_dump(mode="json")
    values: dict[str, Any] = {
        "tool_name": seed.tool_name,
        "arguments": deepcopy(seed.arguments),
        "output": output,
    }
    # Day 6 evidence records tool identity/version. This compatibility branch
    # lets the benchmark remain runnable while older Day 5 rows are inspected.
    if "tool_version" in ReadToolEvidence.__dataclass_fields__:
        values["tool_version"] = seed.tool_version
    if "sequence" in ReadToolEvidence.__dataclass_fields__:
        values["sequence"] = sequence
    if "latency_ms" in ReadToolEvidence.__dataclass_fields__:
        values["latency_ms"] = 0
    return ReadToolEvidence(**values)


def _summary(values: list[float]) -> dict[str, float]:
    if not values:
        raise ValueError("at least one observation is required")
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return {
        "median": round(float(statistics.median(ordered)), 6),
        "p95": round(float(ordered[rank - 1]), 6),
        "minimum": round(float(ordered[0]), 6),
        "maximum": round(float(ordered[-1]), 6),
    }


def _tool_schema_bytes() -> int:
    settings = Settings(
        _env_file=None,
        agent_enabled=True,
        agent_read_tools_enabled=True,
        agent_write_actions_enabled=False,
        agent_proactive_enabled=False,
        agent_purchasing_enabled=False,
    )
    metadata = [
        item.model_dump(mode="json") for item in build_read_tool_registry(settings).metadata()
    ]
    return _json_bytes(metadata)


def _provider_completion_schema_bytes() -> int:
    schema = ensure_strict_json_schema(ReadOnlyModelResponse.model_json_schema())
    return _json_bytes(schema)


def _json_bytes(value: Any) -> int:
    return len(_canonical_json(value).encode("utf-8"))


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


def _aggregate(total: int, *, count: int) -> dict[str, Any]:
    return {
        "total_cents": total,
        "personal_cents": total // 2,
        "shared_cents": total - (total // 2),
        "classified_cents": total - 1_200,
        "unreviewed_cents": 1_200,
        "credits_cents": 0,
        "unknown_share_transactions": 0,
        "unknown_credit_share_transactions": 0,
        "transaction_count": count,
        "average_cents": total // max(1, count),
    }


def _spending_output() -> dict[str, Any]:
    return {
        "start_date": "2026-08-01",
        "end_date": "2026-08-16",
        "previous_start_date": "2026-07-16",
        "previous_end_date": "2026-07-31",
        "currency_code": "USD",
        "spend_basis": "card",
        "comparison_mode": "immediately_preceding",
        "summary": _aggregate(18_000, count=4),
        "comparison": _aggregate(12_000, count=3),
        "categories": [
            {
                "name": "Food & Dining",
                "amount_cents": 13_500,
                "transaction_count": 3,
                "percentage": 75.0,
                "previous_amount_cents": 7_500,
            }
        ],
        "merchants": [
            {
                "name": "Aldi",
                "amount_cents": 8_000,
                "transaction_count": 1,
                "percentage": 44.44,
                "previous_amount_cents": 3_000,
            }
        ],
        "notable_changes": [
            {
                "kind": "category",
                "direction": "up",
                "label": "Food & Dining",
                "amount_cents": 6_000,
                "detail": "Food & Dining was USD 60.00 higher than the comparable period.",
            }
        ],
        "available_currencies": ["USD"],
        "excluded_other_currency_transactions": 0,
        "pending_transactions_excluded": True,
    }


def _transaction_output(*, count: int = 3) -> dict[str, Any]:
    rows = [
        {
            "public_id": str(index + 1),
            "merchant": f"Food merchant {index + 1}",
            "occurred_on": f"2026-08-{(index % 16) + 1:02d}",
            "amount_cents": 2_000 + (index * 125),
            "currency_code": "USD",
            "category": "Food & Dining",
            "status": "personal" if index % 2 == 0 else "ask_user",
            "pending": False,
        }
        for index in range(count)
    ]
    return {
        "transactions": rows,
        "total_count": count,
        "result_limit": 25 if count == 25 else 20,
        "truncated": False,
    }


def _replenishment_output(*, count: int = 1) -> dict[str, Any]:
    items = [
        {
            "public_id": str(index + 101),
            "name": "Laundry detergent" if index == 0 else f"Household item {index + 1}",
            "quantity": "1",
            "unit": "package",
            "due_state": "likely_due" if index % 2 == 0 else "probably_due",
            "predicted_due_on": f"2026-08-{(index % 14) + 16:02d}",
            "confidence_level": "high" if index % 2 == 0 else "medium",
            "evidence_basis": "purchase_pattern",
            "reason": "Based on confirmed acquisition cadence.",
            "last_acquired_on": "2026-07-01",
            "confirmed_acquisition_count": 4,
            "snoozed": False,
        }
        for index in range(count)
    ]
    return {
        "view": "due",
        "as_of": datetime(2026, 8, 16, 12, tzinfo=UTC),
        "items": items,
        "item": None,
        "acquisitions": [],
        "learning": None,
        "total_count": count,
        "result_limit": 20 if count == 20 else 10,
        "truncated": False,
    }


def _deal_output(*, public_id: str = "41", count: int = 1) -> dict[str, Any]:
    deals = [
        {
            "public_id": public_id if index == 0 else str(41 + index),
            "merchant": "Target" if index == 0 else f"Merchant {index + 1}",
            "headline": "Detergent offer" if index == 0 else f"Bounded offer {index + 1}",
            "category": "Household",
            "offer_type": "percent_off",
            "percent_off": 15.0,
            "amount_off_cents": None,
            "currency_code": "USD",
            "minimum_spend_cents": 3_000,
            "promo_code": None,
            "expires_at": datetime(2026, 8, 23, 23, 59, tzinfo=UTC),
            "score": 88.0 - index,
            "saved": False,
            "trust_status": "trusted",
            "relevant_to_need": True,
            "relevance_reasons": ["Linked to an existing household need"],
        }
        for index in range(count)
    ]
    return {
        "deals": deals,
        "total_count": count,
        "result_limit": 12 if count == 12 else 8,
        "truncated": False,
    }


def _receipt_output() -> dict[str, Any]:
    return {
        "view": "needs_review",
        "receipts": [
            {
                "public_id": "501",
                "merchant": "Corner Market",
                "purchased_at": datetime(2026, 8, 15, 18, 30, tzinfo=UTC),
                "ingested_at": datetime(2026, 8, 15, 18, 35, tzinfo=UTC),
                "total_cents": 5_499,
                "currency_code": "USD",
                "status": "needs_review",
                "matched_line_count": 1,
                "ignored_line_count": 0,
                "unmatched_line_count": 1,
                "total_line_count": 2,
                "transaction_linked": True,
                "confirmed_household_item_ids": ["101"],
                "confirmed_household_item_ids_truncated": False,
            }
        ],
        "receipt": None,
        "total_count": 1,
        "result_limit": 10,
        "truncated": False,
    }


def _errand_output() -> dict[str, Any]:
    return {
        "errands": [
            {
                "public_id": "601",
                "title": "Aldi pickup",
                "errand_type": "pickup",
                "status": "open",
                "priority": "high",
                "due_on": "2026-08-16",
                "estimated_duration_minutes": 15,
                "included_in_next_plan": True,
                "place_resolution_status": "resolved",
                "resolved_place_name": "Aldi",
                "household_items": ["Laundry detergent"],
                "household_item_ids": ["101"],
            }
        ],
        "total_count": 1,
        "result_limit": 20,
        "truncated": False,
        "plan": None,
    }


def _integration_output() -> dict[str, Any]:
    return {
        "integrations": [
            {
                "provider": "gmail",
                "label": "Gmail receipts",
                "scope": "personal",
                "status": "attention_required",
                "message": "Reconnect Gmail to resume receipt sync.",
                "last_successful_sync_at": datetime(2026, 8, 14, 8, tzinfo=UTC),
            },
            {
                "provider": "plaid",
                "label": "Plaid",
                "scope": "personal",
                "status": "connected",
                "message": "Connected.",
                "last_successful_sync_at": datetime(2026, 8, 16, 10, tzinfo=UTC),
            },
        ]
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the deterministic, provider-free Day 6 Agent benchmark."
    )
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--warmups", type=int, default=DEFAULT_WARMUPS)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = run_benchmark(repetitions=args.repetitions, warmups=args.warmups)
    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_markdown(result))


if __name__ == "__main__":
    main()

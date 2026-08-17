#!/usr/bin/env python3
"""Deterministic Day 9 cold-start receipt-learning benchmark.

The benchmark uses the production classifier, matcher, receipt confirmation,
alias learning, and acquisition services against an isolated in-memory database.
It counts line-level choices separately from one bounded batch confirmation.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import Base
from app.models import HouseholdItem, HouseholdItemAcquisition, ReceiptItemMatchStatus
from app.services.receipt_ingestion_service import ReceiptIngestionService
from app.services.receipt_learning_service import analyze_receipt_learning
from app.services.receipt_parser_service import ParsedReceipt, ParsedReceiptItem


@dataclass(frozen=True)
class ReceiptLearningBenchmark:
    scenario_count: int
    baseline_manual_line_decisions: int
    day9_manual_line_decisions: int
    manual_line_decisions_avoided: int
    manual_line_decision_reduction_percent: float
    baseline_cadence_entries: int
    day9_cadence_entries: int
    explicit_batch_confirmations: int
    automatic_alias_hits: int
    suggested_cross_merchant_matches: int
    tracked_item_count: int
    confirmed_acquisition_count: int
    provider_requests: int
    candidate_generation_latency_ms_median: float
    batch_confirmation_latency_ms_median: float


class _SequenceParser:
    def __init__(self, receipts: list[ParsedReceipt]):
        self._receipts = iter(receipts)
        self.calls = 0

    def parse_text(self, _text: str) -> ParsedReceipt:
        self.calls += 1
        return next(self._receipts)

    def parse_attachment(self, _content: bytes, _mime: str, _filename: str) -> ParsedReceipt:
        return self.parse_text("attachment")


def run_benchmark() -> ReceiptLearningBenchmark:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    try:
        with factory() as db:
            return _run(db)
    finally:
        engine.dispose()


def _run(db: Session) -> ReceiptLearningBenchmark:
    receipts = _seeded_receipts()
    parser = _SequenceParser(receipts)
    service = ReceiptIngestionService(
        db,
        Settings(
            _env_file=None,
            receipt_auto_match_confidence=0.9,
            receipt_possible_match_confidence=0.65,
        ),
        parser,
    )

    candidate_latencies: list[float] = []
    confirmation_latencies: list[float] = []

    started = time.perf_counter()
    first = service.ingest_text(
        source="gmail",
        source_external_id="day9-benchmark-1",
        text="seed-one",
    )
    candidate_latencies.append((time.perf_counter() - started) * 1000)
    first_suggestions = analyze_receipt_learning(first)
    first_decisions = [
        {
            "line_id": item.line_id,
            "decision": "create",
            "name": item.canonical_name,
            "cadence_days": None,
            "replenishment_mode": "either",
        }
        for item in first_suggestions
        if item.decision == "create_tracked_item"
    ]
    started = time.perf_counter()
    service.apply_decisions(
        first.id,
        decisions=first_decisions,
        expected_updated_at=first.updated_at,
        confirm=True,
        acknowledge_undecided=True,
    )
    confirmation_latencies.append((time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    second = service.ingest_text(
        source="telegram",
        source_external_id="day9-benchmark-2",
        text="seed-two",
        auto_confirm_high_confidence=True,
    )
    candidate_latencies.append((time.perf_counter() - started) * 1000)
    automatic_alias_hits = sum(
        line.match_status == ReceiptItemMatchStatus.MATCHED.value for line in second.items
    )

    started = time.perf_counter()
    third = service.ingest_text(
        source="gmail",
        source_external_id="day9-benchmark-3",
        text="seed-three",
    )
    candidate_latencies.append((time.perf_counter() - started) * 1000)
    third_suggestions = analyze_receipt_learning(third)
    possible = [
        item
        for item in third_suggestions
        if item.decision == "leave_undecided" and item.household_item_id is not None
    ]
    if len(possible) != 1:
        raise RuntimeError("benchmark expected one bounded cross-merchant suggestion")
    started = time.perf_counter()
    service.apply_decisions(
        third.id,
        decisions=[
            {
                "line_id": possible[0].line_id,
                "decision": "match",
                "household_item_id": possible[0].household_item_id,
            }
        ],
        expected_updated_at=third.updated_at,
        confirm=True,
        acknowledge_undecided=False,
    )
    confirmation_latencies.append((time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    fourth = service.ingest_text(
        source="telegram",
        source_external_id="day9-benchmark-4",
        text="seed-four",
        auto_confirm_high_confidence=True,
    )
    candidate_latencies.append((time.perf_counter() - started) * 1000)
    automatic_alias_hits += sum(
        line.match_status == ReceiptItemMatchStatus.MATCHED.value for line in fourth.items
    )

    # The pre-Day-9 flow already learned exact merchant aliases. Its remaining
    # cold-start work was four manual item creations on receipt one and one
    # manual cross-merchant resolution on receipt three. Day 9 turns the four
    # safe creations into batch defaults; only the cross-merchant suggestion is
    # still a line-level choice. Uncertain HOME 24 is left undecided in both
    # policies and is therefore not counted as a completed line decision.
    baseline_manual = 5
    day9_manual = len(possible)
    avoided = baseline_manual - day9_manual
    tracked_count = int(db.scalar(select(func.count(HouseholdItem.id))) or 0)
    acquisition_count = int(db.scalar(select(func.count(HouseholdItemAcquisition.id))) or 0)
    return ReceiptLearningBenchmark(
        scenario_count=4,
        baseline_manual_line_decisions=baseline_manual,
        day9_manual_line_decisions=day9_manual,
        manual_line_decisions_avoided=avoided,
        manual_line_decision_reduction_percent=round(avoided / baseline_manual * 100, 1),
        baseline_cadence_entries=4,
        day9_cadence_entries=0,
        explicit_batch_confirmations=2,
        automatic_alias_hits=automatic_alias_hits,
        suggested_cross_merchant_matches=len(possible),
        tracked_item_count=tracked_count,
        confirmed_acquisition_count=acquisition_count,
        provider_requests=parser.calls,
        candidate_generation_latency_ms_median=round(statistics.median(candidate_latencies), 3),
        batch_confirmation_latency_ms_median=round(statistics.median(confirmation_latencies), 3),
    )


def _seeded_receipts() -> list[ParsedReceipt]:
    def receipt(
        merchant: str,
        purchased_at: datetime,
        names: list[str],
        total_cents: int,
    ) -> ParsedReceipt:
        return ParsedReceipt(
            merchant=merchant,
            purchased_at=purchased_at,
            subtotal_cents=total_cents,
            tax_cents=0,
            total_cents=total_cents,
            currency="USD",
            confidence=0.99,
            items=[
                ParsedReceiptItem(
                    name=name,
                    quantity=1,
                    unit="each",
                    confidence=0.95,
                    classification=("uncertain" if name == "HOME 24" else None),
                    classification_confidence=(0.4 if name == "HOME 24" else None),
                )
                for name in names
            ],
        )

    return [
        receipt(
            "Costco",
            datetime(2026, 7, 27, tzinfo=UTC),
            [
                "KS EGGS 24CT",
                "ORG 2% MLK GAL",
                "TIDE PODS 42CT",
                "KS PAPER TOWELS 12RL",
                "FOOD COURT COFFEE",
                "COTTON T-SHIRT",
                "SALES TAX",
                "HOME 24",
            ],
            8_015,
        ),
        receipt(
            "Costco",
            datetime(2026, 8, 3, tzinfo=UTC),
            ["KS EGGS 24CT", "TIDE PODS 42CT", "FOOD COURT COFFEE"],
            3_047,
        ),
        receipt(
            "Target",
            datetime(2026, 8, 10, tzinfo=UTC),
            ["TIDE PODS SPRING 42CT"],
            1_899,
        ),
        receipt(
            "Target",
            datetime(2026, 8, 17, tzinfo=UTC),
            ["TIDE PODS SPRING 42CT"],
            1_899,
        ),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    result = run_benchmark()
    if args.format == "json":
        print(json.dumps(asdict(result), sort_keys=True, separators=(",", ":")))
        return
    print("| Metric | Result |")
    print("|---|---:|")
    for name, value in asdict(result).items():
        print(f"| {name.replace('_', ' ')} | {value} |")


if __name__ == "__main__":
    main()

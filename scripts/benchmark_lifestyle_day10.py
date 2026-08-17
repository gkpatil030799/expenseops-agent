from __future__ import annotations

import argparse
import json
import math
import statistics
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.agent.read_tools import build_read_tool_registry
from app.config import Settings
from app.db import Base
from app.models import ExpenseTransaction, PlaidItem, User
from app.services.lifestyle_dining_service import LifestyleDiningService


def run_benchmark(*, repetitions: int = 100, warmups: int = 10) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="expenseops-day10-") as directory:
        engine = create_engine(f"sqlite:///{Path(directory) / 'day10.db'}")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            user = User(email="day10-benchmark@example.test", display_name="Day 10")
            db.add(user)
            db.flush()
            item = PlaidItem(workspace_id=1, item_id="day10-benchmark", institution_name="Bank")
            db.add(item)
            db.flush()
            db.info.update(workspace_id=1, user_id=user.id)
            categories = (
                ("Coffee", "FOOD_AND_DRINK / COFFEE", 650),
                ("Bistro", "FOOD_AND_DRINK / RESTAURANT", 3_500),
                ("Delivery", "FOOD_AND_DRINK / FOOD_DELIVERY", 2_200),
                ("Nightlife", "FOOD_AND_DRINK / BAR", 2_800),
                ("Groceries", "FOOD_AND_DRINK / GROCERIES", 4_500),
                ("Uncertain", "FOOD_AND_DRINK", 1_200),
            )
            for index in range(120):
                merchant, category, amount = categories[index % len(categories)]
                db.add(
                    ExpenseTransaction(
                        workspace_id=1,
                        plaid_transaction_id=f"day10-{index}",
                        plaid_item_id=item.id,
                        account_id="card",
                        merchant_name=f"{merchant} {index % 5}",
                        name=merchant,
                        amount_cents=amount,
                        iso_currency_code="USD",
                        date=date(2026, 8, 16) - timedelta(days=index % 60),
                        pending=False,
                        category=category,
                        status="personal" if index % 3 else "ask_user",
                    )
                )
            db.commit()
            service = LifestyleDiningService(db)
            activities = ("coffee", "restaurants", "delivery", "nightlife", "all")
            for _ in range(warmups):
                for activity in activities:
                    _run_one(service, activity)
            timings: dict[str, list[float]] = {activity: [] for activity in activities}
            for _ in range(repetitions):
                for activity in activities:
                    started = time.perf_counter()
                    result = _run_one(service, activity)
                    timings[activity].append((time.perf_counter() - started) * 1_000)
                    _validate(result)
        engine.dispose()
    return {
        "repetitions": repetitions,
        "warmups": warmups,
        "scenarios": {name: _metrics(values) for name, values in timings.items()},
        "registered_read_tools": _tool_projection()[0],
        "tool_schema_bytes": _tool_projection()[1],
    }


def _run_one(service: LifestyleDiningService, activity: str) -> dict:
    return service.build(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 16),
        activity_type=activity,  # type: ignore[arg-type]
        spend_basis="card",
    )


def _validate(result: dict) -> None:
    summary = result["summary"]
    assert summary["total_cents"] >= 0
    assert summary["credits_cents"] >= 0
    assert summary["total_cents"] == (
        summary["personal_cents"] + summary["shared_cents"] + summary["unreviewed_cents"]
    )
    assert summary["total_cents"] == summary["weekday_cents"] + summary["weekend_cents"]


def _metrics(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return {
        "median_ms": round(float(statistics.median(ordered)), 3),
        "p95_ms": round(float(ordered[rank - 1]), 3),
    }


def _tool_projection() -> tuple[int, int]:
    settings = Settings(
        _env_file=None,
        agent_enabled=True,
        agent_read_tools_enabled=True,
        agent_write_actions_enabled=False,
        agent_proactive_enabled=False,
        agent_purchasing_enabled=False,
    )
    values = [
        item.model_dump(mode="json") for item in build_read_tool_registry(settings).metadata()
    ]
    encoded = json.dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return len(values), len(encoded)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=100)
    parser.add_argument("--warmups", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(run_benchmark(repetitions=args.repetitions, warmups=args.warmups), indent=2))


if __name__ == "__main__":
    main()

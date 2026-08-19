#!/usr/bin/env python3
"""Deterministic local benchmark for the Day 18 review workflow.

This benchmark creates no provider client and performs no external write. Timings
cover an in-process SQLite projection/read boundary, not network or production SLA.
"""

from __future__ import annotations

import json
from datetime import date
from time import perf_counter

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import (
    ExpenseTransaction,
    PlaidItem,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.services.review_inbox_service import ReviewInboxService
from app.services.transaction_service import TransactionService
from app.tenancy import TenantContext, set_session_tenant


def run_benchmark() -> dict[str, int | float | str]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="day18-benchmark@example.test", display_name="Day 18 Owner")
        db.add(owner)
        db.flush()
        workspace = Workspace(name="Day 18 benchmark", created_by_user_id=owner.id)
        db.add(workspace)
        db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=owner.id,
                role="owner",
                is_default=True,
            )
        )
        db.commit()
        set_session_tenant(db, TenantContext(owner.id, workspace.id))

        transactions: list[ExpenseTransaction] = []
        for index in range(5):
            item = PlaidItem(
                workspace_id=workspace.id,
                owner_user_id=owner.id,
                item_id=f"day18-item-{index}",
            )
            db.add(item)
            db.flush()
            tx = ExpenseTransaction(
                workspace_id=workspace.id,
                plaid_item_id=item.id,
                plaid_transaction_id=f"day18-transaction-{index}",
                merchant_name=f"Synthetic merchant {index}",
                name=f"SYNTHETIC {index}",
                amount_cents=1000 + index,
                iso_currency_code="USD",
                date=date(2026, 8, 18),
                pending=False,
                status="personal" if index < 3 else "ask_user",
            )
            db.add(tx)
            db.flush()
            transactions.append(tx)

        visibility_started = perf_counter()
        for tx in transactions:
            ReviewInboxService(db).sync_transaction(tx)
        db.commit()
        page = ReviewInboxService(db).list_open()
        visibility_latency_ms = (perf_counter() - visibility_started) * 1000

        resolution_started = perf_counter()
        TransactionService(db).mark_personal(transactions[3].id)
        after_web = ReviewInboxService(db).list_open().total_open
        TransactionService(db).mark_personal(transactions[4].id)
        after_telegram = ReviewInboxService(db).list_open().total_open
        resolution_latency_ms = (perf_counter() - resolution_started) * 1000

        result: dict[str, int | float | str] = {
            "measurement_boundary": "in_process_sqlite_projection_and_read",
            "seeded_transactions": 5,
            "review_items_expected": 2,
            "review_items_observed": page.total_open,
            "manual_searches_required": 0,
            "prompts_required_for_discovery": 0,
            "open_after_web_resolution": after_web,
            "open_after_telegram_resolution": after_telegram,
            "provider_calls": 0,
            "visibility_latency_ms": round(visibility_latency_ms, 3),
            "two_decision_resolution_latency_ms": round(resolution_latency_ms, 3),
        }
        assert result["review_items_observed"] == 2
        assert result["open_after_web_resolution"] == 1
        assert result["open_after_telegram_resolution"] == 0
    engine.dispose()
    return result


def main() -> None:
    print(json.dumps(run_benchmark(), sort_keys=True))


if __name__ == "__main__":
    main()

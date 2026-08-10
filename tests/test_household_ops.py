from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app
from app.models import Errand, ErrandHouseholdItem
from app.services.errand_service import ErrandService
from app.services.replenishment_service import ReplenishmentService, due_score, due_state
from app.services.route_planning_service import RoutePlanningService


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'household.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine)
    session = testing_session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_due_score_calculation_and_thresholds():
    now = datetime(2026, 8, 9, tzinfo=UTC)

    assert due_score(now - timedelta(days=5), 10, now=now) == 0.5
    assert due_score(now - timedelta(days=20), 10, now=now) == 1.0
    assert due_state(0.9) == "likely_due"
    assert due_state(0.7) == "probably_due"
    assert due_state(0.6999) == "not_due"
    assert due_state(0, has_history=False) == "unknown"


def test_snoozed_and_disabled_items_do_not_surface(db):
    now = datetime(2026, 8, 9, tzinfo=UTC)
    service = ReplenishmentService(db, settings=SimpleNamespace(household_snooze_days=7))
    snoozed = service.create_item(
        {
            "name": "Milk",
            "cadence_days": 7,
            "last_acquired_at": now - timedelta(days=7),
            "snoozed_until": now + timedelta(days=2),
        }
    )
    disabled = service.create_item(
        {
            "name": "Eggs",
            "cadence_days": 7,
            "last_acquired_at": now - timedelta(days=7),
            "enabled": False,
        }
    )

    assert service.should_surface(snoozed, now=now) is False
    assert service.should_surface(disabled, now=now) is False
    assert service.due_items(now=now) == []


def test_refresh_is_idempotent_and_prevents_duplicate_generated_errands(db):
    now = datetime(2026, 8, 9, tzinfo=UTC)
    service = ReplenishmentService(db, settings=SimpleNamespace(household_snooze_days=7))
    item = service.create_item(
        {
            "name": "Laundry detergent",
            "cadence_days": 30,
            "last_acquired_at": now - timedelta(days=30),
            "preferred_place_name": "Costco",
            "replenishment_mode": "errand",
        }
    )

    _, first = service.refresh(now=now)
    _, second = service.refresh(now=now)

    assert first[0]["action"] == "created_errand"
    assert second[0]["errand_id"] == first[0]["errand_id"]
    assert db.scalar(select(func.count(Errand.id))) == 1
    assert db.scalar(select(func.count(ErrandHouseholdItem.id))) == 1
    assert service.active_linked_errand(item.id) is not None


def test_due_item_is_added_to_an_existing_matching_stop(db):
    now = datetime(2026, 8, 9, tzinfo=UTC)
    errands = ErrandService(db)
    costco_return = errands.create_errand(
        {
            "title": "Return blender",
            "errand_type": "return",
            "place_name": "Costco",
            "place_resolution_status": "resolved",
            "resolved_place_name": "Costco Wholesale",
            "resolved_place_address": "4502 E Oak St, Phoenix, AZ 85008",
            "resolved_provider_place_id": "costco-1",
            "priority": "normal",
            "included_in_next_plan": True,
        }
    )
    service = ReplenishmentService(db, settings=SimpleNamespace(household_snooze_days=7))
    service.create_item(
        {
            "name": "Paper towels",
            "cadence_days": 35,
            "last_acquired_at": now - timedelta(days=35),
            "preferred_place_name": "Costco",
            "replenishment_mode": "either",
        }
    )

    _, suggestions = service.refresh(now=now)

    assert suggestions[0]["action"] == "added_to_existing_stop"
    assert suggestions[0]["errand_id"] == costco_return.id
    assert db.scalar(select(func.count(Errand.id))) == 1


def test_plan_groups_locations_and_orders_deterministically(db):
    now = datetime(2026, 8, 9, tzinfo=UTC)
    errands = ErrandService(db)
    errands.create_errand(
        {
            "title": "Paper towels",
            "errand_type": "purchase",
            "place_name": "Costco",
            "place_resolution_status": "resolved",
            "resolved_place_name": "Costco Wholesale",
            "resolved_place_address": "4502 E Oak St, Phoenix, AZ 85008",
            "resolved_provider_place_id": "costco-1",
            "priority": "normal",
            "included_in_next_plan": True,
        }
    )
    errands.create_errand(
        {
            "title": "Return blender",
            "errand_type": "return",
            "place_name": "Costco",
            "place_resolution_status": "resolved",
            "resolved_place_name": "Costco Wholesale",
            "resolved_place_address": "4502 E Oak St, Phoenix, AZ 85008",
            "resolved_provider_place_id": "costco-1",
            "priority": "normal",
            "included_in_next_plan": True,
        }
    )
    errands.create_errand(
        {
            "title": "Prescription",
            "errand_type": "pickup",
            "place_name": "CVS",
            "place_resolution_status": "resolved",
            "resolved_place_name": "CVS Pharmacy",
            "resolved_place_address": "123 W Main St, Phoenix, AZ 85001",
            "resolved_provider_place_id": "cvs-1",
            "priority": "high",
            "included_in_next_plan": True,
        }
    )
    errands.create_errand(
        {
            "title": "Amazon return",
            "errand_type": "return",
            "place_name": "UPS",
            "place_resolution_status": "resolved",
            "resolved_place_name": "The UPS Store",
            "resolved_place_address": "456 E Main St, Phoenix, AZ 85001",
            "resolved_provider_place_id": "ups-1",
            "due_at": now + timedelta(days=1),
            "priority": "normal",
            "included_in_next_plan": True,
        }
    )
    service = RoutePlanningService(db, settings=SimpleNamespace(household_base_location=""))

    plan = service.create_plan()
    output = service.to_dict(plan)

    assert output["stop_count"] == 3
    assert [stop["place_name"] for stop in output["stops"]] == [
        "The UPS Store",
        "CVS Pharmacy",
        "Costco Wholesale",
    ]
    assert len(output["stops"][2]["errands"]) == 2
    assert output["routing_provider"] == "deterministic_fallback"
    assert output["routing_is_optimized"] is False
    assert output["route_url"].startswith("https://www.google.com/maps/dir/")


def test_bought_updates_history_and_still_have_only_snoozes(db):
    now = datetime(2026, 8, 9, tzinfo=UTC)
    original = now - timedelta(days=30)
    service = ReplenishmentService(db, settings=SimpleNamespace(household_snooze_days=5))
    item = service.create_item(
        {"name": "Toothpaste", "cadence_days": 30, "last_acquired_at": original}
    )

    snoozed = service.still_have(item.id, now=now)
    assert _as_utc(snoozed.last_acquired_at) == original
    assert _as_utc(snoozed.snoozed_until) == now + timedelta(days=5)

    bought = service.mark_bought(item.id, now=now)
    assert _as_utc(bought.last_acquired_at) == now
    assert bought.snoozed_until is None


def test_completing_errand_can_mark_linked_item_acquired(db):
    old = datetime(2026, 7, 1, tzinfo=UTC)
    item = ReplenishmentService(db).create_item(
        {"name": "Paper towels", "cadence_days": 35, "last_acquired_at": old}
    )
    errand = ErrandService(db).create_errand(
        {
            "title": "Buy paper towels",
            "errand_type": "purchase",
            "priority": "normal",
            "included_in_next_plan": True,
        },
        household_item=item,
    )

    completed = ErrandService(db).complete_errand(
        errand.id,
        mark_linked_items_acquired=True,
    )

    assert completed.status == "completed"
    acquired = ReplenishmentService(db).get_item(item.id).last_acquired_at
    assert _as_utc(acquired) > old


def test_household_crud_validation_and_actions(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'routes.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine)

    def override_db():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        invalid_item = client.post("/api/household/items", json={"name": "", "cadence_days": 0})
        assert invalid_item.status_code == 422
        created = client.post(
            "/api/household/items",
            json={"name": "Milk", "cadence_days": 7, "replenishment_mode": "delivery"},
        )
        assert created.status_code == 201
        item_id = created.json()["id"]

        updated = client.patch(
            f"/api/household/items/{item_id}", json={"preferred_place_name": "Fry's"}
        )
        assert updated.status_code == 200
        assert updated.json()["preferred_place_name"] == "Fry's"

        assert client.post("/api/household/errands", json={"title": ""}).status_code == 422
        errand = client.post(
            "/api/household/errands",
            json={"title": "Pick up prescription", "errand_type": "pickup"},
        )
        assert errand.status_code == 201
        errand_id = errand.json()["id"]
        assert (
            client.patch(
                f"/api/household/errands/{errand_id}",
                json={"included_in_next_plan": False},
            ).json()["included_in_next_plan"]
            is False
        )
        assert client.post(f"/api/household/errands/{errand_id}/skip").json()["status"] == "skipped"
        assert client.delete(f"/api/household/items/{item_id}").status_code == 204
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _as_utc(value: datetime | None) -> datetime:
    assert value is not None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

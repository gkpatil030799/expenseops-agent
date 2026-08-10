from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.household_schemas import PlanningLocationInput
from app.main import app
from app.models import Errand, HouseholdItem
from app.services.errand_service import HouseholdOpsError
from app.services.place_optimizer_service import TripPlaceOptimizer
from app.services.place_resolution_service import (
    GooglePlacesSearchProvider,
    PlaceCandidate,
    PlaceResolutionService,
    PlaceSearchError,
)
from app.services.route_planning_service import (
    DeterministicFallbackRouteProvider,
    GoogleMapsRouteProvider,
    RouteMatrixResult,
    RouteResult,
    RouteStopInput,
    RoutingProviderError,
)
from app.services.saved_location_service import SavedLocationService
from app.services.while_out_service import WhileOutService


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'location-aware.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


class MeasuredProvider:
    name = "measured_test"

    def geocode(self, address):
        del address
        return None

    def calculate_route(
        self,
        *,
        base_location,
        stops,
        final_destination=None,
        optimize=False,
    ):
        del base_location
        duration = 20 + (5 * len(stops))
        return RouteResult(
            stops=list(reversed(stops)) if optimize and len(stops) >= 2 else stops,
            provider=self.name,
            is_optimized=optimize and len(stops) >= 2,
            route_url="https://maps.example/route",
            duration_minutes=duration,
            distance_meters=10_000 + (1_000 * len(stops)),
        )


class FailingProvider(MeasuredProvider):
    name = "failing_test"

    def calculate_route(self, **kwargs):
        del kwargs
        raise RoutingProviderError("provider unavailable")


def settings():
    return SimpleNamespace(
        household_snooze_days=7,
        household_max_incremental_minutes=10,
        household_probably_due_incremental_minutes=5,
        household_routing_provider="fallback",
        household_place_search_provider="fallback",
        google_maps_api_key="",
        household_base_location="",
    )


def test_fallback_provider_never_claims_route_metrics_or_optimization():
    stop = RouteStopInput(
        key="costco", place_name="Costco", place_address="4502 E Oak St, Phoenix, AZ 85008"
    )
    result = DeterministicFallbackRouteProvider().calculate_route(
        base_location="Home",
        stops=[stop],
        optimize=True,
    )

    assert result.provider == "deterministic_fallback"
    assert result.is_optimized is False
    assert result.duration_minutes is None
    assert result.distance_meters is None


def test_google_provider_geocodes_and_uses_measured_optimized_route():
    client = FakeGoogleClient()
    provider = GoogleMapsRouteProvider("test-key", client=client, cache_ttl_seconds=0)

    geocoded = provider.geocode("Costco")
    route = provider.calculate_route(
        base_location="Home",
        stops=[
            RouteStopInput("cvs", "CVS", "123 W Main St, Phoenix, AZ 85001"),
            RouteStopInput("ups", "UPS", "456 E Main St, Phoenix, AZ 85001"),
        ],
        final_destination=RouteStopInput("home", "Home", "123 Example Road"),
        optimize=True,
    )

    assert geocoded is not None
    assert geocoded.latitude == 33.45
    assert [stop.key for stop in route.stops] == ["ups", "cvs"]
    assert route.duration_minutes == 11
    assert route.distance_meters == 12_345
    assert route.is_optimized is True
    assert client.route_body["optimizeWaypointOrder"] is True


def test_google_geocoding_zero_results_is_graceful():
    provider = GoogleMapsRouteProvider(
        "test-key", client=FakeGoogleClient(zero_results=True), cache_ttl_seconds=0
    )

    assert provider.geocode("missing place") is None


def test_google_place_search_returns_concrete_opening_hour_candidates():
    client = FakeGooglePlacesClient()
    provider = GooglePlacesSearchProvider("test-key", client=client, cache_ttl_seconds=0)

    candidates = provider.search_places("Aldi", latitude=33.45, longitude=-112.07)

    assert len(candidates) == 2
    assert candidates[0].full_address == "1801 E Bell Rd, Phoenix, AZ 85022"
    assert candidates[0].latitude == 33.64
    assert candidates[0].open_now is True
    assert "places.currentOpeningHours" in client.headers["X-Goog-FieldMask"]
    assert client.body["locationBias"]["circle"]["center"]["latitude"] == 33.45


def test_google_route_matrix_parses_measured_edges():
    client = FakeGoogleClient()
    provider = GoogleMapsRouteProvider("matrix-test-key", client=client, cache_ttl_seconds=0)
    points = [
        RouteStopInput("home", "Home", "1 Home St"),
        RouteStopInput("aldi", "ALDI", "2 Store St"),
    ]

    matrix = provider.calculate_matrix(points)

    assert matrix is not None
    assert matrix.duration("home", "aldi") == 7
    assert matrix.distance("home", "aldi") == 3_200
    assert client.matrix_calls == 1
    assert "condition" in client.matrix_headers["X-Goog-FieldMask"]


def test_google_provider_cache_reuses_identical_place_and_route_requests():
    places_client = FakeGooglePlacesClient()
    places = GooglePlacesSearchProvider(
        "cache-place-key", client=places_client, cache_ttl_seconds=900
    )
    places.search_places("Aldi", latitude=33.45, longitude=-112.07)
    places.search_places("Aldi", latitude=33.45, longitude=-112.07)

    routes_client = FakeGoogleClient()
    routes = GoogleMapsRouteProvider("cache-route-key", client=routes_client, cache_ttl_seconds=900)
    stop = RouteStopInput("aldi", "ALDI", "2 Store St")
    routes.calculate_route(base_location="1 Home St", stops=[stop])
    routes.calculate_route(base_location="1 Home St", stops=[stop])

    assert places_client.request_calls == 1
    assert routes_client.route_calls == 1


def test_generic_destination_cannot_become_a_maps_waypoint():
    with pytest.raises(RoutingProviderError, match="concrete destination"):
        DeterministicFallbackRouteProvider().calculate_route(
            base_location="123 Home St, Phoenix, AZ 85001",
            stops=[RouteStopInput("aldi", "Aldi", None)],
        )


def test_haircut_and_aldi_require_resolution_before_planning(db):
    haircut = Errand(title="Get a haircut", errand_type="service", source="manual")
    aldi = Errand(title="Shop at Aldi", place_name="Aldi", errand_type="purchase", source="manual")
    db.add_all([haircut, aldi])
    db.commit()

    assert haircut.place_resolution_status == "unresolved"
    assert aldi.place_resolution_status == "unresolved"
    with pytest.raises(HouseholdOpsError, match="2 errands need locations"):
        WhileOutService(db, settings=settings()).plan(
            origin={"address": "123 Home St, Phoenix, AZ 85001"},
            include_replenishment=False,
        )


def test_place_candidates_preference_override_and_end_to_end_route(db):
    haircut = Errand(title="Get a haircut", errand_type="service", source="manual")
    aldi = Errand(title="Shop at Aldi", place_name="Aldi", errand_type="purchase", source="manual")
    db.add_all([haircut, aldi])
    db.commit()
    provider = FakePlaceProvider()
    places = PlaceResolutionService(db, settings=settings(), provider=provider)

    salon_search = places.search(haircut.id, latitude=33.4, longitude=-112.0)
    aldi_search = places.search(aldi.id, latitude=33.4, longitude=-112.0)
    assert len(salon_search["candidates"]) == 2
    assert len(aldi_search["candidates"]) == 3

    salon = salon_search["candidates"][0]
    aldi_a = aldi_search["candidates"][0]
    places.resolve(haircut.id, salon, remember_preference=True)
    places.resolve(aldi.id, aldi_a, remember_preference=True)
    preferred = places.search(aldi.id)["candidates"][0]
    assert preferred["is_preferred"] is True

    aldi_b = aldi_search["candidates"][1]
    overridden = places.resolve(aldi.id, aldi_b, remember_preference=True)
    assert overridden.resolved_place_address == aldi_b["full_address"]
    remembered = places.search(aldi.id)["candidates"][0]
    assert remembered["full_address"] == aldi_b["full_address"]
    places.resolve(aldi.id, remembered, remember_preference=False)

    plan, _summary = WhileOutService(db, settings=settings()).plan(
        origin={"address": "123 Home St, Phoenix, AZ 85001"},
        final_destination={"label": "Home", "address": "123 Home St, Phoenix, AZ 85001"},
        include_replenishment=False,
    )
    assert {stop.place_name for stop in plan.stops} == {
        salon["canonical_name"],
        aldi_b["canonical_name"],
    }
    query = parse_qs(urlparse(plan.route_url).query)
    destinations = "|".join([*query.get("waypoints", []), *query.get("destination", [])])
    assert salon["full_address"] in destinations
    assert aldi_b["full_address"] in destinations
    assert "Unspecified" not in destinations


def test_place_search_failure_is_graceful(db):
    errand = Errand(title="Get a haircut", errand_type="service", source="manual")
    db.add(errand)
    db.commit()

    result = PlaceResolutionService(
        db, settings=settings(), provider=FailingPlaceProvider()
    ).search(errand.id)

    assert result["candidates"] == []
    assert result["message"] == "Place search is temporarily unavailable."
    assert db.get(Errand, errand.id).place_resolution_status == "failed"


def test_while_out_automatically_selects_best_full_trip_place_combination(db):
    haircut = Errand(
        title="Get a haircut",
        errand_type="service",
        estimated_duration_minutes=25,
        priority="high",
        source="manual",
    )
    aldi = Errand(
        title="Shop at Aldi",
        place_name="Aldi",
        errand_type="purchase",
        estimated_duration_minutes=20,
        source="manual",
    )
    db.add_all([haircut, aldi])
    db.commit()

    plan, summary = WhileOutService(
        db,
        settings=settings(),
        route_provider=CombinationRouteProvider(),
        place_search_provider=FakePlaceProvider(),
    ).plan(
        origin={"label": "Home", "address": "123 Home St, Phoenix, AZ 85001"},
        final_destination={"label": "Work", "address": "500 Work Ave, Phoenix, AZ 85004"},
        available_minutes=90,
        include_replenishment=False,
    )

    db.refresh(haircut)
    db.refresh(aldi)
    assert haircut.place_resolution_method == "automatic"
    assert haircut.resolved_provider_place_id == "salon-a"
    assert aldi.place_resolution_method == "automatic"
    assert aldi.resolved_provider_place_id == "aldi-b"
    assert plan.routing_is_optimized is True
    assert plan.travel_duration_minutes == 18
    assert any(
        "Compared 6 complete location combinations" in reason
        for reason in summary["recommendation_reasons"]
    )


def test_place_optimizer_scores_combinations_with_one_matrix_and_one_final_route(db):
    haircut = Errand(
        title="Get a haircut",
        errand_type="service",
        estimated_duration_minutes=25,
        source="manual",
    )
    aldi = Errand(
        title="Shop at Aldi",
        place_name="Aldi",
        errand_type="purchase",
        estimated_duration_minutes=20,
        source="manual",
    )
    db.add_all([haircut, aldi])
    db.commit()
    route_provider = MatrixCountingProvider()
    place_resolution = PlaceResolutionService(
        db,
        settings=settings(),
        provider=FakePlaceProvider(),
    )
    optimizer = TripPlaceOptimizer(
        settings=settings(),
        place_resolution=place_resolution,
        route_provider=route_provider,
    )

    result = optimizer.resolve_for_trip(
        errands=[haircut, aldi],
        origin=RouteStopInput("home", "Home", "123 Home St, Phoenix, AZ 85001"),
        primary=None,
        final=RouteStopInput("work", "Work", "500 Work Ave, Phoenix, AZ 85004"),
        available_minutes=90,
    )

    db.refresh(haircut)
    db.refresh(aldi)
    assert result.compared_combinations == 6
    assert route_provider.matrix_calls == 1
    assert route_provider.route_calls == 0
    assert haircut.resolved_provider_place_id == "salon-a"
    assert aldi.resolved_provider_place_id == "aldi-b"
    assert result.chosen_route is not None
    assert [stop.place_name for stop in result.chosen_route.stops] == [
        "Copper & Sage Salon",
        "ALDI",
    ]


def test_while_out_uses_matrices_for_scoring_and_one_detailed_final_route(db):
    haircut = Errand(title="Get a haircut", errand_type="service", source="manual")
    aldi = Errand(
        title="Shop at Aldi",
        place_name="Aldi",
        errand_type="purchase",
        source="manual",
    )
    db.add_all([haircut, aldi])
    db.commit()
    route_provider = MatrixCountingProvider()

    plan, _summary = WhileOutService(
        db,
        settings=settings(),
        route_provider=route_provider,
        place_search_provider=FakePlaceProvider(),
    ).plan(
        origin={"label": "Home", "address": "123 Home St, Phoenix, AZ 85001"},
        final_destination={"label": "Work", "address": "500 Work Ave, Phoenix, AZ 85004"},
        include_replenishment=False,
    )

    assert route_provider.matrix_calls == 2
    assert route_provider.route_calls == 1
    assert plan.routing_is_optimized is True
    assert plan.travel_duration_minutes == 10


def test_haircut_and_aldi_api_flow_generates_only_concrete_destinations(db):
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    try:
        haircut = client.post(
            "/api/household/errands",
            json={"title": "Get a haircut", "errand_type": "service"},
        ).json()
        aldi = client.post(
            "/api/household/errands",
            json={
                "title": "Shop at Aldi",
                "errand_type": "purchase",
                "place_name": "Aldi",
            },
        ).json()
        blocked = client.post(
            "/api/household/errand-plans/while-out",
            json={
                "origin": {"address": "123 Home St, Phoenix, AZ 85001"},
                "include_replenishment": False,
            },
        )
        assert blocked.status_code == 400
        assert "2 errands need locations" in blocked.json()["detail"]

        resolutions = [
            (
                haircut["id"],
                "Copper & Sage Salon",
                "100 N 1st Ave, Phoenix, AZ 85003",
                "salon-a",
            ),
            (
                aldi["id"],
                "ALDI",
                "1401 E Warner Rd, Gilbert, AZ 85296",
                "aldi-b",
            ),
        ]
        for errand_id, name, address, place_id in resolutions:
            response = client.post(
                f"/api/household/errands/{errand_id}/resolve-place",
                json={
                    "canonical_name": name,
                    "full_address": address,
                    "provider_place_id": place_id,
                    "remember_preference": True,
                },
            )
            assert response.status_code == 200
            assert response.json()["place_resolution_status"] == "resolved"

        planned = client.post(
            "/api/household/errand-plans/while-out",
            json={
                "origin": {"address": "123 Home St, Phoenix, AZ 85001"},
                "final_destination": {
                    "label": "Home",
                    "address": "123 Home St, Phoenix, AZ 85001",
                },
                "include_replenishment": False,
            },
        )
        assert planned.status_code == 200
        route_url = planned.json()["plan"]["route_url"]
        route_values = parse_qs(urlparse(route_url).query)
        concrete = "|".join(
            [*route_values.get("waypoints", []), *route_values.get("destination", [])]
        )
        assert "100 N 1st Ave, Phoenix, AZ 85003" in concrete
        assert "1401 E Warner Rd, Gilbert, AZ 85296" in concrete
        assert "Unspecified" not in concrete
    finally:
        app.dependency_overrides.clear()


def test_location_crud_and_single_home_work_constraint(db):
    service = SavedLocationService(db)
    home = service.create_location(
        {"label": "Home", "address": "Example home", "location_type": "home"}
    )
    work = service.create_location(
        {"label": "Work", "address": "Example office", "location_type": "work"}
    )

    assert [location.id for location in service.list_locations()] == [home.id, work.id]
    assert service.update_location(home.id, {"label": "My home"}).label == "My home"
    with pytest.raises(RuntimeError, match="Only one active Home"):
        service.create_location(
            {"label": "Other", "address": "Other home", "location_type": "home"}
        )
    service.delete_location(work.id)
    assert [location.location_type for location in service.list_locations()] == ["home"]


def test_current_location_requires_both_coordinates():
    with pytest.raises(ValidationError, match="latitude and longitude"):
        PlanningLocationInput(latitude=33.4)

    current = PlanningLocationInput(latitude=33.4, longitude=-112.1)
    assert current.address is None


def test_location_and_while_out_api_work_with_current_location(db):
    db.add(
        Errand(
            title="Urgent pharmacy pickup",
            errand_type="pickup",
            place_name="CVS",
            priority="high",
            status="open",
            source="manual",
            included_in_next_plan=True,
            **resolved("CVS Pharmacy", "123 W Main St, Phoenix, AZ 85001", "cvs-1"),
        )
    )
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    try:
        location = client.post(
            "/api/household/locations",
            json={"label": "Home", "address": "Example address", "location_type": "home"},
        )
        assert location.status_code == 201
        duplicate = client.post(
            "/api/household/locations",
            json={"label": "Second home", "address": "Other", "location_type": "home"},
        )
        assert duplicate.status_code == 400

        response = client.post(
            "/api/household/errand-plans/while-out",
            json={
                "origin": {"latitude": 33.45, "longitude": -112.07},
                "primary_destination": {"label": "Home", "address": "Example address"},
                "include_replenishment": False,
            },
        )
        assert response.status_code == 200
        assert response.json()["estimates_are_routed"] is False
        assert response.json()["plan"]["routing_is_optimized"] is False
    finally:
        app.dependency_overrides.clear()


def test_route_comparison_uses_measured_incremental_time(db):
    result = WhileOutService(db, settings=settings(), route_provider=MeasuredProvider()).compare(
        origin={"address": "Work"},
        destination={"address": "Home"},
        candidate={"address": "CVS"},
    )

    assert result["has_measured_comparison"] is True
    assert result["baseline_duration_minutes"] == 20
    assert result["candidate_duration_minutes"] == 25
    assert result["incremental_duration_minutes"] == 5


def test_replenishment_confidence_is_applied_to_existing_store_trip(db):
    now = datetime(2026, 8, 9, tzinfo=UTC)
    db.add(
        Errand(
            title="Costco return",
            errand_type="return",
            place_name="Costco",
            priority="normal",
            status="open",
            source="manual",
            included_in_next_plan=True,
            **resolved("Costco Wholesale", "4502 E Oak St, Phoenix, AZ 85008", "costco-1"),
        )
    )
    db.add_all(
        [
            HouseholdItem(
                name="Paper towels",
                cadence_days=100,
                last_acquired_at=now - timedelta(days=96),
                preferred_place_name="Costco",
                preferred_place_address="4502 E Oak St, Phoenix, AZ 85008",
                replenishment_mode="either",
            ),
            HouseholdItem(
                name="Detergent",
                cadence_days=100,
                last_acquired_at=now - timedelta(days=74),
                preferred_place_name="Costco",
                preferred_place_address="4502 E Oak St, Phoenix, AZ 85008",
                replenishment_mode="either",
            ),
            HouseholdItem(
                name="Toothpaste",
                cadence_days=100,
                last_acquired_at=now - timedelta(days=55),
                preferred_place_name="Target",
                preferred_place_address="21001 N Tatum Blvd, Phoenix, AZ 85050",
                replenishment_mode="either",
            ),
        ]
    )
    db.commit()

    plan, summary = WhileOutService(
        db, settings=settings(), route_provider=MeasuredProvider()
    ).plan(
        origin={"address": "Home"},
        primary_destination={"label": "Costco", "address": "Costco"},
        now=now,
    )
    output = _plan_items(plan)

    assert {item.name for item in output} == {"Paper towels", "Detergent"}
    assert any(item["title"] == "Toothpaste" for item in summary["excluded"])


def test_time_budget_filters_infeasible_candidate_and_is_truthful(db):
    now = datetime(2026, 8, 9, tzinfo=UTC)
    db.add(
        Errand(
            title="Prescription",
            errand_type="pickup",
            place_name="CVS",
            due_at=now + timedelta(days=1),
            estimated_duration_minutes=15,
            priority="high",
            status="open",
            source="manual",
            included_in_next_plan=True,
            **resolved("CVS Pharmacy", "123 W Main St, Phoenix, AZ 85001", "cvs-1"),
        )
    )
    db.commit()

    plan, summary = WhileOutService(
        db, settings=settings(), route_provider=MeasuredProvider()
    ).plan(
        origin={"address": "Work"},
        primary_destination={"address": "Home", "label": "Home"},
        available_minutes=30,
        include_replenishment=False,
        now=now,
    )

    planned_titles = [link.errand.title for stop in plan.stops for link in stop.errand_links]
    assert "Prescription" not in planned_titles
    assert summary["time_budget_applied"] is True
    assert any("30-minute budget" in item["reason"] for item in summary["excluded"])


def test_provider_failure_falls_back_without_time_or_optimization_claims(db):
    db.add(
        Errand(
            title="Urgent pickup",
            errand_type="pickup",
            place_name="CVS",
            priority="high",
            status="open",
            source="manual",
            included_in_next_plan=True,
            **resolved("CVS Pharmacy", "123 W Main St, Phoenix, AZ 85001", "cvs-1"),
        )
    )
    db.commit()

    plan, summary = WhileOutService(db, settings=settings(), route_provider=FailingProvider()).plan(
        origin={"address": "Work"},
        primary_destination={"address": "Home", "label": "Home"},
        include_replenishment=False,
    )

    assert plan.routing_provider == "deterministic_fallback"
    assert plan.routing_is_optimized is False
    assert plan.travel_duration_minutes is None
    assert summary["estimates_are_routed"] is False
    assert summary["time_budget_applied"] is False


def _plan_items(plan):
    return [link.household_item for stop in plan.stops for link in stop.household_item_links]


def resolved(name: str, address: str, place_id: str) -> dict:
    return {
        "place_resolution_status": "resolved",
        "resolved_place_name": name,
        "resolved_place_address": address,
        "resolved_provider_place_id": place_id,
    }


class FakePlaceProvider:
    name = "fake_places"

    def search_places(self, query, **kwargs):
        del kwargs
        if "haircut" in query.casefold():
            return [
                PlaceCandidate(
                    "Copper & Sage Salon",
                    "100 N 1st Ave, Phoenix, AZ 85003",
                    provider_place_id="salon-a",
                ),
                PlaceCandidate(
                    "Roosevelt Barbers",
                    "810 N 2nd St, Phoenix, AZ 85004",
                    provider_place_id="salon-b",
                ),
            ]
        return [
            PlaceCandidate("ALDI", "1801 E Bell Rd, Phoenix, AZ 85022", provider_place_id="aldi-a"),
            PlaceCandidate(
                "ALDI", "1401 E Warner Rd, Gilbert, AZ 85296", provider_place_id="aldi-b"
            ),
            PlaceCandidate(
                "ALDI", "5775 W Baseline Rd, Laveen, AZ 85339", provider_place_id="aldi-c"
            ),
        ]

    def get_place_details(self, provider_place_id):
        del provider_place_id
        return None


class FailingPlaceProvider(FakePlaceProvider):
    def search_places(self, query, **kwargs):
        del query, kwargs
        raise PlaceSearchError("Place search is temporarily unavailable.")


class CombinationRouteProvider(MeasuredProvider):
    name = "combination_routes"

    def calculate_route(
        self,
        *,
        base_location,
        stops,
        final_destination=None,
        optimize=False,
    ):
        del base_location
        addresses = {stop.place_address for stop in stops}
        if (
            "100 N 1st Ave, Phoenix, AZ 85003" in addresses
            and "1401 E Warner Rd, Gilbert, AZ 85296" in addresses
        ):
            duration = 18
        else:
            duration = 28 + len(stops)
        return RouteResult(
            stops=list(reversed(stops)) if optimize and len(stops) >= 2 else stops,
            provider=self.name,
            is_optimized=optimize and len(stops) >= 2,
            route_url="https://maps.example/concrete-route",
            duration_minutes=duration,
            distance_meters=duration * 800,
        )


class MatrixCountingProvider(MeasuredProvider):
    name = "matrix_counting"

    def __init__(self):
        self.matrix_calls = 0
        self.route_calls = 0

    def calculate_matrix(self, points):
        self.matrix_calls += 1
        durations = {}
        distances = {}
        preferred_edges = {
            ("home", "candidate:1:salon-a"): 3,
            ("candidate:1:salon-a", "candidate:2:aldi-b"): 4,
            ("candidate:2:aldi-b", "work"): 3,
            ("origin", "errand:1"): 3,
            ("errand:1", "errand:2"): 4,
            ("errand:2", "final"): 3,
        }
        for origin in points:
            for destination in points:
                if origin.key == destination.key:
                    continue
                duration = preferred_edges.get((origin.key, destination.key), 20)
                durations[(origin.key, destination.key)] = duration
                distances[(origin.key, destination.key)] = duration * 700
        return RouteMatrixResult(self.name, durations, distances)

    def calculate_route(
        self,
        *,
        base_location,
        stops,
        final_destination=None,
        optimize=False,
    ):
        del base_location, final_destination, optimize
        self.route_calls += 1
        return RouteResult(
            stops=stops,
            provider=self.name,
            is_optimized=True,
            route_url="https://maps.example/matrix-route",
            duration_minutes=10,
            distance_meters=7_000,
        )


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeGoogleClient:
    def __init__(self, zero_results=False):
        self.zero_results = zero_results
        self.route_body = None
        self.route_calls = 0
        self.matrix_calls = 0
        self.matrix_headers = {}

    def get(self, url, params):
        del url, params
        if self.zero_results:
            return FakeResponse({"status": "ZERO_RESULTS", "results": []})
        return FakeResponse(
            {
                "status": "OK",
                "results": [
                    {
                        "formatted_address": "Costco, Phoenix, AZ",
                        "geometry": {"location": {"lat": 33.45, "lng": -112.07}},
                    }
                ],
            }
        )

    def post(self, url, headers, json):
        if "computeRouteMatrix" in url:
            self.matrix_calls += 1
            self.matrix_headers = headers
            return FakeResponse(
                [
                    {
                        "originIndex": 0,
                        "destinationIndex": 1,
                        "status": {},
                        "condition": "ROUTE_EXISTS",
                        "duration": "420s",
                        "distanceMeters": 3_200,
                    },
                    {
                        "originIndex": 1,
                        "destinationIndex": 0,
                        "status": {},
                        "condition": "ROUTE_EXISTS",
                        "duration": "480s",
                        "distanceMeters": 3_400,
                    },
                ]
            )
        del headers
        self.route_calls += 1
        self.route_body = json
        return FakeResponse(
            {
                "routes": [
                    {
                        "duration": "601s",
                        "distanceMeters": 12_345,
                        "optimizedIntermediateWaypointIndex": [1, 0],
                    }
                ]
            }
        )


class FakeGooglePlacesClient:
    def __init__(self):
        self.headers = {}
        self.body = {}
        self.request_calls = 0

    def request(self, method, url, headers, **kwargs):
        del method, url
        self.request_calls += 1
        self.headers = headers
        self.body = kwargs.get("json", {})
        return FakeResponse(
            {
                "places": [
                    {
                        "id": "aldi-a",
                        "displayName": {"text": "ALDI"},
                        "formattedAddress": "1801 E Bell Rd, Phoenix, AZ 85022",
                        "location": {"latitude": 33.64, "longitude": -112.04},
                        "currentOpeningHours": {
                            "openNow": True,
                            "weekdayDescriptions": ["Monday: 9:00 AM – 8:00 PM"],
                        },
                    },
                    {
                        "id": "aldi-b",
                        "displayName": {"text": "ALDI"},
                        "formattedAddress": "1401 E Warner Rd, Gilbert, AZ 85296",
                        "location": {"latitude": 33.33, "longitude": -111.76},
                        "currentOpeningHours": {"openNow": False},
                    },
                ]
            }
        )

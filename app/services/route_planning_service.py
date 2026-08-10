from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from urllib.parse import urlencode

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings, get_settings
from app.models import (
    Errand,
    ErrandPlan,
    ErrandPlanStop,
    ErrandPlanStopErrand,
    ErrandPlanStopHouseholdItem,
    ErrandPriority,
    ErrandStatus,
    utc_now,
)
from app.services.errand_service import HouseholdOpsError, HouseholdOpsNotFound, errand_to_dict
from app.services.replenishment_service import due_score
from app.services.ttl_cache import TTLCache


@dataclass(frozen=True)
class RouteStopInput:
    key: str
    place_name: str
    place_address: str | None
    latitude: float | None = None
    longitude: float | None = None

    @property
    def destination(self) -> str:
        if self.latitude is not None and self.longitude is not None:
            return f"{self.latitude},{self.longitude}"
        if self.place_address:
            return self.place_address
        raise RoutingProviderError(f"{self.place_name} has no concrete destination.")

    @property
    def is_concrete(self) -> bool:
        return bool(
            self.place_address or (self.latitude is not None and self.longitude is not None)
        )


@dataclass(frozen=True)
class RouteResult:
    stops: list[RouteStopInput]
    provider: str
    is_optimized: bool
    route_url: str | None
    duration_minutes: int | None = None
    distance_meters: int | None = None


@dataclass(frozen=True)
class GeocodedLocation:
    address: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class RouteMatrixResult:
    provider: str
    durations_minutes: dict[tuple[str, str], int]
    distances_meters: dict[tuple[str, str], int]

    def duration(self, origin_key: str, destination_key: str) -> int | None:
        return self.durations_minutes.get((origin_key, destination_key))

    def distance(self, origin_key: str, destination_key: str) -> int | None:
        return self.distances_meters.get((origin_key, destination_key))


_GEOCODE_CACHE: TTLCache[GeocodedLocation] = TTLCache()
_ROUTE_CACHE: TTLCache[RouteResult] = TTLCache()
_MATRIX_CACHE: TTLCache[RouteMatrixResult] = TTLCache()


class RoutingProviderError(RuntimeError):
    pass


class RouteProvider(Protocol):
    name: str

    def geocode(self, address: str) -> GeocodedLocation | None: ...

    def calculate_route(
        self,
        *,
        base_location: str | RouteStopInput | None,
        stops: list[RouteStopInput],
        final_destination: RouteStopInput | None = None,
        optimize: bool = False,
    ) -> RouteResult: ...

    def calculate_matrix(self, points: list[RouteStopInput]) -> RouteMatrixResult | None: ...


class DeterministicFallbackRouteProvider:
    name = "deterministic_fallback"

    def geocode(self, address: str) -> GeocodedLocation | None:
        del address
        return None

    def calculate_route(
        self,
        *,
        base_location: str | RouteStopInput | None,
        stops: list[RouteStopInput],
        final_destination: RouteStopInput | None = None,
        optimize: bool = False,
    ) -> RouteResult:
        del optimize
        return RouteResult(
            stops=stops,
            provider=self.name,
            is_optimized=False,
            route_url=_google_maps_url(base_location, stops, final_destination),
        )

    def calculate_matrix(self, points: list[RouteStopInput]) -> RouteMatrixResult | None:
        del points
        return None


class GoogleMapsRouteProvider:
    name = "google_maps"

    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        cache_ttl_seconds: int = 900,
    ):
        if not api_key:
            raise ValueError("Google Maps API key is required.")
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=15)
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_namespace = sha256(api_key.encode()).hexdigest()[:12]

    def geocode(self, address: str) -> GeocodedLocation | None:
        cache_key = f"{self.cache_namespace}:geocode:{address.casefold().strip()}"
        if self.cache_ttl_seconds and (cached := _GEOCODE_CACHE.get(cache_key)):
            return cached
        try:
            response = self.client.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"address": address, "key": self.api_key},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RoutingProviderError("Google Maps geocoding request failed.") from exc
        if payload.get("status") == "ZERO_RESULTS":
            return None
        if payload.get("status") != "OK" or not payload.get("results"):
            raise RoutingProviderError(
                f"Google Maps geocoding failed: {payload.get('status', 'unknown error')}."
            )
        result = payload["results"][0]
        location = result["geometry"]["location"]
        geocoded = GeocodedLocation(
            address=result.get("formatted_address") or address,
            latitude=float(location["lat"]),
            longitude=float(location["lng"]),
        )
        if self.cache_ttl_seconds:
            _GEOCODE_CACHE.set(cache_key, geocoded, self.cache_ttl_seconds)
        return geocoded

    def calculate_route(
        self,
        *,
        base_location: str | RouteStopInput | None,
        stops: list[RouteStopInput],
        final_destination: RouteStopInput | None = None,
        optimize: bool = False,
    ) -> RouteResult:
        if base_location is None:
            raise RoutingProviderError("A route origin is required for measured routing.")
        if not stops and final_destination is None:
            raise RoutingProviderError("At least one route destination is required.")
        destination = final_destination or stops[-1]
        intermediates = stops if final_destination is not None else stops[:-1]
        can_optimize = optimize and len(intermediates) >= 2
        cache_key = _route_cache_key(
            self.cache_namespace,
            base_location,
            stops,
            final_destination,
            optimize,
        )
        if self.cache_ttl_seconds and (cached := _ROUTE_CACHE.get(cache_key)):
            return cached
        body = {
            "origin": _google_waypoint(base_location),
            "destination": _google_waypoint(destination),
            "intermediates": [_google_waypoint(stop) for stop in intermediates],
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE",
            "computeAlternativeRoutes": False,
            "optimizeWaypointOrder": can_optimize,
        }
        try:
            response = self.client.post(
                "https://routes.googleapis.com/directions/v2:computeRoutes",
                headers={
                    "X-Goog-Api-Key": self.api_key,
                    "X-Goog-FieldMask": (
                        "routes.duration,routes.distanceMeters,"
                        "routes.optimizedIntermediateWaypointIndex"
                    ),
                },
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RoutingProviderError("Google Maps route request failed.") from exc
        routes = payload.get("routes") or []
        if not routes:
            raise RoutingProviderError("Google Maps did not return a route.")
        route = routes[0]
        order = route.get("optimizedIntermediateWaypointIndex") or []
        ordered_intermediates = (
            [intermediates[index] for index in order]
            if can_optimize and len(order) == len(intermediates)
            else intermediates
        )
        ordered_stops = [*ordered_intermediates]
        if final_destination is None:
            ordered_stops.append(destination)
        else:
            ordered_stops = ordered_intermediates
        result = RouteResult(
            stops=ordered_stops,
            provider=self.name,
            is_optimized=bool(can_optimize and len(order) == len(intermediates)),
            route_url=_google_maps_url(base_location, ordered_stops, final_destination),
            duration_minutes=_duration_minutes(route.get("duration")),
            distance_meters=int(route["distanceMeters"]) if route.get("distanceMeters") else None,
        )
        if self.cache_ttl_seconds:
            _ROUTE_CACHE.set(cache_key, result, self.cache_ttl_seconds)
        return result

    def calculate_matrix(self, points: list[RouteStopInput]) -> RouteMatrixResult | None:
        if len(points) < 2:
            return None
        cache_key = f"{self.cache_namespace}:matrix:" + "|".join(
            _stop_signature(point) for point in points
        )
        if self.cache_ttl_seconds and (cached := _MATRIX_CACHE.get(cache_key)):
            return cached
        body = {
            "origins": [{"waypoint": _google_waypoint(point)} for point in points],
            "destinations": [{"waypoint": _google_waypoint(point)} for point in points],
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE",
        }
        try:
            response = self.client.post(
                "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix",
                headers={
                    "X-Goog-Api-Key": self.api_key,
                    "X-Goog-FieldMask": (
                        "originIndex,destinationIndex,status,condition,duration,distanceMeters"
                    ),
                },
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RoutingProviderError("Google Maps route-matrix request failed.") from exc
        if not isinstance(payload, list):
            raise RoutingProviderError("Google Maps returned an invalid route matrix.")
        durations: dict[tuple[str, str], int] = {}
        distances: dict[tuple[str, str], int] = {}
        for element in payload:
            status = element.get("status") or {}
            if element.get("condition") != "ROUTE_EXISTS" or status.get("code", 0) != 0:
                continue
            origin_index = element.get("originIndex", 0)
            destination_index = element.get("destinationIndex", 0)
            if origin_index >= len(points) or destination_index >= len(points):
                continue
            key = (points[origin_index].key, points[destination_index].key)
            duration = _duration_minutes(element.get("duration"))
            if duration is not None:
                durations[key] = duration
            if element.get("distanceMeters") is not None:
                distances[key] = int(element["distanceMeters"])
        result = RouteMatrixResult(self.name, durations, distances)
        if self.cache_ttl_seconds:
            _MATRIX_CACHE.set(cache_key, result, self.cache_ttl_seconds)
        return result


class RoutePlanningService:
    def __init__(
        self,
        db: Session,
        settings: Settings | None = None,
        route_provider: RouteProvider | None = None,
    ):
        self.db = db
        self.settings = settings or get_settings()
        self.route_provider = route_provider or build_route_provider(self.settings)

    def create_plan(
        self,
        *,
        planned_for: datetime | None = None,
        base_location: str | None = None,
    ) -> ErrandPlan:
        errands = self._eligible_errands()
        if not errands:
            raise HouseholdOpsError("No open errands are included in the next trip.")
        unresolved = [errand for errand in errands if not _errand_has_resolved_place(errand)]
        if unresolved:
            raise HouseholdOpsError(_resolution_required_message(unresolved))

        groups = _group_errands(errands)
        ordered_groups = sorted(groups.values(), key=_group_sort_key)
        stop_inputs = [
            RouteStopInput(
                key=group["key"],
                place_name=group["place_name"],
                place_address=group["place_address"],
                latitude=group["latitude"],
                longitude=group["longitude"],
            )
            for group in ordered_groups
        ]
        resolved_base = (base_location or self.settings.household_base_location).strip() or None
        try:
            route = self.route_provider.calculate_route(
                base_location=resolved_base,
                stops=stop_inputs,
                optimize=True,
            )
        except RoutingProviderError:
            route = DeterministicFallbackRouteProvider().calculate_route(
                base_location=resolved_base,
                stops=stop_inputs,
            )
        groups_by_key = {group["key"]: group for group in ordered_groups}
        plan = ErrandPlan(
            planned_for=planned_for,
            base_location=resolved_base,
            routing_provider=route.provider,
            routing_is_optimized=route.is_optimized,
            route_url=route.route_url,
            estimated_stop_minutes=sum(
                errand.estimated_duration_minutes or 0 for errand in errands
            ),
            travel_duration_minutes=route.duration_minutes,
            distance_meters=route.distance_meters,
        )
        self.db.add(plan)
        self.db.flush()

        for order, route_stop in enumerate(route.stops, start=1):
            group = groups_by_key[route_stop.key]
            stop = ErrandPlanStop(
                plan_id=plan.id,
                stop_order=order,
                place_name=route_stop.place_name,
                place_address=route_stop.place_address,
            )
            self.db.add(stop)
            self.db.flush()
            for errand in sorted(group["errands"], key=_errand_sort_key):
                self.db.add(ErrandPlanStopErrand(stop_id=stop.id, errand_id=errand.id))
                errand.status = ErrandStatus.PLANNED.value
                errand.updated_at = utc_now()
        self.db.commit()
        return self.get_plan(plan.id)

    def get_plan(self, plan_id: int) -> ErrandPlan:
        stmt = (
            select(ErrandPlan)
            .where(ErrandPlan.id == plan_id)
            .options(
                selectinload(ErrandPlan.stops)
                .selectinload(ErrandPlanStop.errand_links)
                .selectinload(ErrandPlanStopErrand.errand)
                .selectinload(Errand.household_links),
                selectinload(ErrandPlan.stops)
                .selectinload(ErrandPlanStop.household_item_links)
                .selectinload(ErrandPlanStopHouseholdItem.household_item),
            )
        )
        plan = self.db.execute(stmt).scalar_one_or_none()
        if plan is None:
            raise HouseholdOpsNotFound("Errand plan not found.")
        return plan

    def latest_plan(self) -> ErrandPlan | None:
        plan_id = self.db.execute(
            select(ErrandPlan.id)
            .order_by(desc(ErrandPlan.created_at), desc(ErrandPlan.id))
            .limit(1)
        ).scalar_one_or_none()
        return self.get_plan(plan_id) if plan_id is not None else None

    def to_dict(self, plan: ErrandPlan) -> dict:
        stops = []
        for stop in sorted(plan.stops, key=lambda value: value.stop_order):
            stops.append(
                {
                    "id": stop.id,
                    "stop_order": stop.stop_order,
                    "place_name": stop.place_name,
                    "place_address": stop.place_address,
                    "errands": [
                        errand_to_dict(link.errand)
                        for link in sorted(
                            stop.errand_links,
                            key=lambda value: _errand_sort_key(value.errand),
                        )
                    ],
                    "household_items": [
                        {
                            "id": link.household_item.id,
                            "name": link.household_item.name,
                            "quantity": link.household_item.quantity,
                            "unit": link.household_item.unit,
                            "due_score": due_score(
                                link.household_item.last_acquired_at,
                                link.household_item.cadence_days,
                            ),
                            "reason": link.reason,
                        }
                        for link in stop.household_item_links
                    ],
                }
            )
        return {
            "id": plan.id,
            "planned_for": plan.planned_for,
            "base_location": plan.base_location,
            "status": plan.status,
            "routing_provider": plan.routing_provider,
            "routing_is_optimized": plan.routing_is_optimized,
            "route_url": plan.route_url,
            "estimated_stop_minutes": plan.estimated_stop_minutes,
            "travel_duration_minutes": plan.travel_duration_minutes,
            "distance_meters": plan.distance_meters,
            "baseline_travel_duration_minutes": plan.baseline_travel_duration_minutes,
            "incremental_travel_duration_minutes": plan.incremental_travel_duration_minutes,
            "available_minutes": plan.available_minutes,
            "planning_mode": plan.planning_mode,
            "primary_destination": plan.primary_destination,
            "final_destination": plan.final_destination,
            "stop_count": len(stops),
            "stops": stops,
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
        }

    def _eligible_errands(self) -> list[Errand]:
        return list(
            self.db.execute(
                select(Errand)
                .where(
                    Errand.status.in_([ErrandStatus.OPEN.value, ErrandStatus.PLANNED.value]),
                    Errand.included_in_next_plan.is_(True),
                )
                .order_by(Errand.id)
            ).scalars()
        )


def _group_errands(errands: list[Errand]) -> dict[str, dict]:
    groups: dict[str, dict] = {}
    for errand in errands:
        key = _location_key(errand)
        group = groups.setdefault(
            key,
            {
                "key": key,
                "place_name": errand.resolved_place_name,
                "place_address": errand.resolved_place_address,
                "latitude": errand.resolved_latitude,
                "longitude": errand.resolved_longitude,
                "errands": [],
            },
        )
        group["errands"].append(errand)
    return groups


def _location_key(errand: Errand) -> str:
    if errand.resolved_provider_place_id:
        return f"place:{errand.resolved_provider_place_id}"
    if errand.resolved_place_address:
        return f"address:{' '.join(errand.resolved_place_address.casefold().split())}"
    raise HouseholdOpsError(f"{errand.title} needs a resolved location.")


def _errand_has_resolved_place(errand: Errand) -> bool:
    return bool(
        errand.place_resolution_status == "resolved"
        and errand.resolved_place_name
        and errand.resolved_place_address
    )


def _resolution_required_message(errands: list[Errand]) -> str:
    count = len(errands)
    titles = "; ".join(errand.title for errand in errands)
    noun = "errand needs" if count == 1 else "errands need"
    return f"{count} {noun} locations before the route can be created: {titles}."


def _group_sort_key(group: dict) -> tuple:
    errands: list[Errand] = group["errands"]
    due_dates = [_aware(errand.due_at) for errand in errands if errand.due_at is not None]
    earliest_due = min(due_dates) if due_dates else datetime.max.replace(tzinfo=UTC)
    highest_priority = min(_priority_rank(errand.priority) for errand in errands)
    return (
        not bool(due_dates),
        earliest_due,
        highest_priority,
        group["key"],
        min(errand.id for errand in errands),
    )


def _errand_sort_key(errand: Errand) -> tuple:
    due = _aware(errand.due_at) if errand.due_at is not None else datetime.max.replace(tzinfo=UTC)
    return (
        errand.due_at is None,
        due,
        _priority_rank(errand.priority),
        errand.title.casefold(),
        errand.id,
    )


def _priority_rank(priority: str) -> int:
    return {
        ErrandPriority.HIGH.value: 0,
        ErrandPriority.NORMAL.value: 1,
        ErrandPriority.LOW.value: 2,
    }.get(priority, 1)


def build_route_provider(settings: Settings) -> RouteProvider:
    provider_name = getattr(settings, "household_routing_provider", "fallback")
    api_key = getattr(settings, "google_maps_api_key", "")
    if provider_name == "google_maps" and api_key:
        return GoogleMapsRouteProvider(
            api_key,
            cache_ttl_seconds=getattr(settings, "household_provider_cache_ttl_seconds", 900),
        )
    return DeterministicFallbackRouteProvider()


def _google_maps_url(
    base_location: str | RouteStopInput | None,
    stops: list[RouteStopInput],
    final_destination: RouteStopInput | None = None,
) -> str | None:
    if not stops and final_destination is None:
        return None
    if any(not stop.is_concrete for stop in stops) or (
        final_destination is not None and not final_destination.is_concrete
    ):
        raise RoutingProviderError("Every Maps waypoint must be a concrete destination.")
    destinations = [stop.destination for stop in stops]
    destination = final_destination.destination if final_destination else destinations[-1]
    waypoints = destinations if final_destination else destinations[:-1]
    params = {"api": "1", "destination": destination}
    if base_location:
        params["origin"] = (
            base_location.destination
            if isinstance(base_location, RouteStopInput)
            else base_location
        )
    if waypoints:
        params["waypoints"] = "|".join(waypoints)
    return f"https://www.google.com/maps/dir/?{urlencode(params)}"


def _google_waypoint(value: str | RouteStopInput) -> dict:
    if isinstance(value, str):
        return {"address": value}
    if value.latitude is not None and value.longitude is not None:
        return {"location": {"latLng": {"latitude": value.latitude, "longitude": value.longitude}}}
    if not value.place_address:
        raise RoutingProviderError("Every routed waypoint must have a concrete destination.")
    return {"address": value.place_address}


def _duration_minutes(value: str | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)s", value)
    if not match:
        raise RoutingProviderError("Routing provider returned an invalid duration.")
    return math.ceil(float(match.group(1)) / 60)


def _stop_signature(value: str | RouteStopInput) -> str:
    if isinstance(value, str):
        return f"address:{' '.join(value.casefold().split())}"
    return f"{value.key}:{value.destination.casefold()}"


def _route_cache_key(
    namespace: str,
    base_location: str | RouteStopInput,
    stops: list[RouteStopInput],
    final_destination: RouteStopInput | None,
    optimize: bool,
) -> str:
    values = [
        namespace,
        "route",
        _stop_signature(base_location),
        *(f"stop:{_stop_signature(stop)}" for stop in stops),
        f"final:{_stop_signature(final_destination)}" if final_destination else "final:none",
        f"optimize:{optimize}",
    ]
    return "|".join(values)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

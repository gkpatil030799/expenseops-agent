from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    Errand,
    ErrandPlan,
    ErrandPlanStop,
    ErrandPlanStopErrand,
    ErrandPlanStopHouseholdItem,
    ErrandPriority,
    ErrandStatus,
    HouseholdItem,
    ReplenishmentMode,
    SavedLocation,
    utc_now,
)
from app.services.errand_service import HouseholdOpsError
from app.services.place_optimizer_service import MatrixTrip, TripPlaceOptimizer, best_matrix_trip
from app.services.place_resolution_service import (
    PlaceResolutionService,
    PlaceSearchProvider,
    build_place_search_provider,
)
from app.services.replenishment_service import ReplenishmentService, due_score
from app.services.route_planning_service import (
    DeterministicFallbackRouteProvider,
    RouteProvider,
    RouteResult,
    RouteStopInput,
    RoutingProviderError,
    build_route_provider,
    plan_input_fingerprint,
)
from app.services.saved_location_service import SavedLocationService


@dataclass
class CandidateStop:
    key: str
    route_stop: RouteStopInput
    errands: list[Errand] = field(default_factory=list)
    household_items: list[tuple[HouseholdItem, float, str]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    is_primary: bool = False

    @property
    def stop_minutes(self) -> int:
        return sum(errand.estimated_duration_minutes or 0 for errand in self.errands)


class WhileOutService:
    def __init__(
        self,
        db: Session,
        settings: Settings | None = None,
        route_provider: RouteProvider | None = None,
        place_search_provider: PlaceSearchProvider | None = None,
    ):
        self.db = db
        self.settings = settings or get_settings()
        self.route_provider = route_provider or build_route_provider(self.settings)
        self.place_resolution = PlaceResolutionService(
            db,
            settings=self.settings,
            provider=place_search_provider or build_place_search_provider(self.settings),
        )

    def plan(
        self,
        *,
        origin: dict,
        primary_destination: dict | None = None,
        final_destination: dict | None = None,
        available_minutes: int | None = None,
        errand_ids: list[int] | None = None,
        include_replenishment: bool = True,
        now: datetime | None = None,
    ) -> tuple[ErrandPlan, dict]:
        current = now or utc_now()
        origin_stop = self.resolve_location(origin, key="origin")
        primary_stop = (
            self.resolve_location(primary_destination, key="primary")
            if primary_destination
            else None
        )
        final_stop = (
            self.resolve_location(final_destination, key="final") if final_destination else None
        )
        place_result = TripPlaceOptimizer(
            settings=self.settings,
            place_resolution=self.place_resolution,
            route_provider=self.route_provider,
        ).resolve_for_trip(
            errands=self._load_errands(errand_ids or []),
            origin=origin_stop,
            primary=primary_stop,
            final=final_stop,
            available_minutes=available_minutes,
        )
        if place_result.unresolved_errands:
            detail = " ".join(place_result.reasons)
            raise HouseholdOpsError(
                f"{_resolution_required_message(place_result.unresolved_errands)} {detail}".strip()
            )
        candidates, excluded = self._build_candidates(
            primary_stop=primary_stop,
            errand_ids=errand_ids or [],
            include_replenishment=include_replenishment,
            now=current,
        )
        if not candidates:
            raise HouseholdOpsError("No useful errands or replenishment stops are available.")

        selected, route, baseline, provider_failed, selection_excluded = self._select_candidates(
            origin=origin_stop,
            primary=primary_stop,
            final=final_stop,
            candidates=candidates,
            available_minutes=available_minutes,
        )
        excluded.extend(selection_excluded)
        if not selected:
            raise HouseholdOpsError("No candidates fit the requested trip constraints.")
        if provider_failed:
            fallback = DeterministicFallbackRouteProvider()
            route = self._route_for(
                fallback,
                origin_stop,
                selected,
                primary_stop,
                final_stop,
                optimize=False,
            )

        plan = self._persist_plan(
            origin=origin_stop,
            primary=primary_stop,
            final=final_stop,
            selected=selected,
            route=route,
            baseline=baseline,
            available_minutes=available_minutes,
            excluded=excluded,
            extra_reasons=place_result.reasons,
            include_replenishment=include_replenishment,
        )
        summary = plan.recommendation_summary or {}
        return plan, summary

    def _load_errands(self, errand_ids: list[int]) -> list[Errand]:
        stmt = select(Errand).where(
            Errand.status.in_([ErrandStatus.OPEN.value, ErrandStatus.PLANNED.value]),
            Errand.included_in_next_plan.is_(True),
        )
        if errand_ids:
            stmt = stmt.where(Errand.id.in_(errand_ids))
        return list(self.db.execute(stmt.order_by(Errand.id)).scalars())

    def compare(self, *, origin: dict, destination: dict, candidate: dict) -> dict:
        origin_stop = self.resolve_location(origin, key="origin")
        destination_stop = self.resolve_location(destination, key="destination")
        candidate_stop = self.resolve_location(candidate, key="candidate")
        if self.route_provider.name == DeterministicFallbackRouteProvider.name:
            return _empty_comparison(self.route_provider.name)
        try:
            baseline = self.route_provider.calculate_route(
                base_location=origin_stop,
                stops=[],
                final_destination=destination_stop,
            )
            with_candidate = self.route_provider.calculate_route(
                base_location=origin_stop,
                stops=[candidate_stop],
                final_destination=destination_stop,
            )
        except RoutingProviderError:
            return _empty_comparison(DeterministicFallbackRouteProvider.name)
        if baseline.duration_minutes is None or with_candidate.duration_minutes is None:
            return _empty_comparison(self.route_provider.name)
        return {
            "routing_provider": self.route_provider.name,
            "has_measured_comparison": True,
            "baseline_duration_minutes": baseline.duration_minutes,
            "candidate_duration_minutes": with_candidate.duration_minutes,
            "incremental_duration_minutes": max(
                0, with_candidate.duration_minutes - baseline.duration_minutes
            ),
            "baseline_distance_meters": baseline.distance_meters,
            "candidate_distance_meters": with_candidate.distance_meters,
        }

    def resolve_location(self, value: dict, *, key: str) -> RouteStopInput:
        saved_location_id = value.get("saved_location_id")
        if saved_location_id:
            location = SavedLocationService(self.db).get_location(saved_location_id)
            stop = _saved_location_stop(location, key)
        else:
            latitude = value.get("latitude")
            longitude = value.get("longitude")
            label = value.get("label") or (
                "Current location" if latitude is not None else "Location"
            )
            stop = RouteStopInput(
                key=key,
                place_name=label,
                place_address=value.get("address"),
                latitude=latitude,
                longitude=longitude,
            )
        if stop.latitude is not None and stop.longitude is not None:
            return stop
        address = (stop.place_address or "").strip()
        try:
            verified = self.route_provider.geocode(address)
        except (AttributeError, RoutingProviderError) as exc:
            raise HouseholdOpsError(
                f"{stop.place_name} could not be verified. Try again or use current coordinates."
            ) from exc
        if verified is None:
            raise HouseholdOpsError(
                f"{stop.place_name} must be a verified full address or coordinates, "
                "not a generic place name."
            )
        return RouteStopInput(
            key=key,
            place_name=stop.place_name,
            place_address=verified.address,
            latitude=verified.latitude,
            longitude=verified.longitude,
            source_saved_location_id=stop.source_saved_location_id,
        )

    def _build_candidates(
        self,
        *,
        primary_stop: RouteStopInput | None,
        errand_ids: list[int],
        include_replenishment: bool,
        now: datetime,
    ) -> tuple[list[CandidateStop], list[dict]]:
        stmt = select(Errand).where(
            Errand.status.in_([ErrandStatus.OPEN.value, ErrandStatus.PLANNED.value]),
            Errand.included_in_next_plan.is_(True),
        )
        if errand_ids:
            stmt = stmt.where(Errand.id.in_(errand_ids))
        errands = list(self.db.execute(stmt.order_by(Errand.id)).scalars())
        unresolved = [errand for errand in errands if not _errand_has_resolved_place(errand)]
        if unresolved:
            raise HouseholdOpsError(_resolution_required_message(unresolved))
        candidates: dict[str, CandidateStop] = {}
        primary_key = _stop_key(primary_stop) if primary_stop else None
        if primary_stop:
            candidates[primary_key] = CandidateStop(
                key=primary_key,
                route_stop=primary_stop,
                reasons=["This is your stated destination."],
                is_primary=True,
            )
        for errand in errands:
            stop = RouteStopInput(
                key=f"errand:{errand.id}",
                place_name=errand.resolved_place_name or "Resolved destination",
                place_address=errand.resolved_place_address,
                latitude=errand.resolved_latitude,
                longitude=errand.resolved_longitude,
            )
            key = _stop_key(stop)
            candidate = candidates.setdefault(
                key,
                CandidateStop(key=key, route_stop=stop),
            )
            candidate.errands.append(errand)
            candidate.reasons.append(_errand_reason(errand, now))

        excluded: list[dict] = []
        if include_replenishment:
            replenishment = ReplenishmentService(self.db, self.settings)
            for item in replenishment.list_items():
                score = due_score(item.last_acquired_at, item.cadence_days, now=now)
                if not item.enabled or item.last_acquired_at is None:
                    continue
                if item.snoozed_until and _aware(item.snoozed_until) > _aware(now):
                    continue
                if item.cadence_days is None:
                    excluded.append(
                        _excluded_item(
                            item,
                            "Still learning its purchase interval; it is not marked due yet.",
                        )
                    )
                    continue
                if score < 0.7:
                    excluded.append(
                        _excluded_item(
                            item,
                            f"Only {round(score * 100)}% through its normal replenishment cadence.",
                        )
                    )
                    continue
                if item.replenishment_mode == ReplenishmentMode.DELIVERY.value:
                    excluded.append(
                        _excluded_item(item, "Configured for delivery, not an errand stop.")
                    )
                    continue
                if not item.preferred_place_address:
                    excluded.append(
                        _excluded_item(
                            item,
                            "Its preferred store has not been resolved to a full address.",
                        )
                    )
                    continue
                stop = RouteStopInput(
                    key=f"item:{item.id}",
                    place_name=(
                        item.preferred_place_name or item.preferred_place_address or item.name
                    ),
                    place_address=item.preferred_place_address,
                )
                key = _stop_key(stop)
                already_on_trip = key in candidates
                if score < 0.9 and not already_on_trip:
                    candidate = candidates.setdefault(
                        key,
                        CandidateStop(key=key, route_stop=stop),
                    )
                    candidate.household_items.append(
                        (item, score, "Probably due; include only if route cost is low.")
                    )
                else:
                    candidate = candidates.setdefault(
                        key,
                        CandidateStop(key=key, route_stop=stop),
                    )
                    reason = (
                        "Probably due and its preferred store is already on this trip."
                        if score < 0.9
                        else "Likely due based on its replenishment cadence."
                    )
                    candidate.household_items.append((item, score, reason))
                    candidate.reasons.append(reason)
        return sorted(candidates.values(), key=_candidate_sort_key), excluded

    def _select_candidates(
        self,
        *,
        origin: RouteStopInput,
        primary: RouteStopInput | None,
        final: RouteStopInput | None,
        candidates: list[CandidateStop],
        available_minutes: int | None,
    ) -> tuple[list[CandidateStop], RouteResult, RouteResult | None, bool, list[dict]]:
        fallback_mode = self.route_provider.name == DeterministicFallbackRouteProvider.name
        selected = [candidate for candidate in candidates if candidate.is_primary]
        remaining = [candidate for candidate in candidates if not candidate.is_primary]
        excluded: list[dict] = []
        baseline = None
        provider_failed = False
        provider = self.route_provider

        if not fallback_mode:
            matrix_selection = self._select_candidates_with_matrix(
                provider=provider,
                origin=origin,
                primary=primary,
                final=final,
                candidates=candidates,
                available_minutes=available_minutes,
            )
            if matrix_selection is not None:
                return matrix_selection

        try:
            current_route = self._route_for(
                provider, origin, selected, primary, final, optimize=False
            )
            baseline = current_route if primary or final else None
        except RoutingProviderError:
            provider_failed = True
            fallback_mode = True
            provider = DeterministicFallbackRouteProvider()
            current_route = self._route_for(
                provider, origin, selected, primary, final, optimize=False
            )

        for candidate in remaining:
            if fallback_mode:
                include = primary is None or _fallback_is_important(candidate)
                if include:
                    selected.append(candidate)
                else:
                    excluded.extend(
                        _excluded_candidate(
                            candidate,
                            "No routing data is available to assess its detour.",
                        )
                    )
                continue
            try:
                trial = self._route_for(
                    provider,
                    origin,
                    [*selected, candidate],
                    primary,
                    final,
                    optimize=False,
                )
            except RoutingProviderError:
                provider_failed = True
                break
            incremental = _incremental_minutes(current_route, trial)
            probably_only = (
                bool(candidate.household_items)
                and not candidate.errands
                and all(score < 0.9 for _, score, _ in candidate.household_items)
            )
            worth_detour = (
                primary is None
                or _fallback_is_important(candidate)
                or (
                    incremental is not None
                    and incremental
                    <= (
                        getattr(
                            self.settings,
                            "household_probably_due_incremental_minutes",
                            5,
                        )
                        if probably_only
                        else getattr(self.settings, "household_max_incremental_minutes", 10)
                    )
                )
            )
            estimated_total = (
                trial.duration_minutes + sum(item.stop_minutes for item in [*selected, candidate])
                if trial.duration_minutes is not None
                else None
            )
            fits_budget = available_minutes is None or (
                estimated_total is not None and estimated_total <= available_minutes
            )
            if worth_detour and fits_budget:
                if incremental is not None:
                    candidate.reasons.append(f"Adds approximately {incremental} routed minutes.")
                selected.append(candidate)
                current_route = trial
            else:
                reason = (
                    f"Estimated trip would exceed the {available_minutes}-minute budget."
                    if worth_detour and available_minutes is not None
                    else "Its measured detour is too large for this trip."
                )
                excluded.extend(_excluded_candidate(candidate, reason))

        if provider_failed:
            selected = [candidate for candidate in candidates if candidate.is_primary]
            for candidate in remaining:
                if primary is None or _fallback_is_important(candidate):
                    selected.append(candidate)
                else:
                    excluded.extend(
                        _excluded_candidate(
                            candidate, "Routing failed, so no travel-time claim was made."
                        )
                    )
            current_route = self._route_for(
                DeterministicFallbackRouteProvider(),
                origin,
                selected,
                primary,
                final,
                optimize=False,
            )
        elif not fallback_mode:
            try:
                current_route = self._route_for(
                    provider, origin, selected, primary, final, optimize=True
                )
            except RoutingProviderError:
                provider_failed = True
                current_route = self._route_for(
                    DeterministicFallbackRouteProvider(),
                    origin,
                    selected,
                    primary,
                    final,
                    optimize=False,
                )
        else:
            current_route = self._route_for(
                provider, origin, selected, primary, final, optimize=False
            )
        return selected, current_route, baseline, provider_failed, excluded

    def _select_candidates_with_matrix(
        self,
        *,
        provider: RouteProvider,
        origin: RouteStopInput,
        primary: RouteStopInput | None,
        final: RouteStopInput | None,
        candidates: list[CandidateStop],
        available_minutes: int | None,
    ) -> tuple[list[CandidateStop], RouteResult, RouteResult | None, bool, list[dict]] | None:
        calculate_matrix = getattr(provider, "calculate_matrix", None)
        if calculate_matrix is None:
            return None
        destination = final or primary
        points = _unique_route_points(
            [
                origin,
                *(candidate.route_stop for candidate in candidates),
                *([destination] if destination is not None else []),
            ]
        )
        try:
            matrix = calculate_matrix(points)
        except RoutingProviderError:
            return None
        if matrix is None:
            return None

        selected = [candidate for candidate in candidates if candidate.is_primary]
        remaining = [candidate for candidate in candidates if not candidate.is_primary]
        initial_trip = _matrix_trip_for_selection(matrix, origin, selected, primary, final)
        if initial_trip is None:
            return None
        current_route = _matrix_route_result(provider.name, initial_trip)
        baseline = current_route if primary or final else None
        excluded: list[dict] = []

        for candidate in remaining:
            trial_selection = [*selected, candidate]
            trial_trip = _matrix_trip_for_selection(
                matrix,
                origin,
                trial_selection,
                primary,
                final,
            )
            if trial_trip is None:
                return None
            trial = _matrix_route_result(provider.name, trial_trip)
            incremental = _incremental_minutes(current_route, trial)
            probably_only = (
                bool(candidate.household_items)
                and not candidate.errands
                and all(score < 0.9 for _, score, _ in candidate.household_items)
            )
            threshold = (
                getattr(self.settings, "household_probably_due_incremental_minutes", 5)
                if probably_only
                else getattr(self.settings, "household_max_incremental_minutes", 10)
            )
            worth_detour = (
                primary is None
                or _fallback_is_important(candidate)
                or (incremental is not None and incremental <= threshold)
            )
            estimated_total = trial.duration_minutes + sum(
                item.stop_minutes for item in trial_selection
            )
            fits_budget = available_minutes is None or estimated_total <= available_minutes
            if worth_detour and fits_budget:
                if incremental is not None:
                    candidate.reasons.append(f"Adds approximately {incremental} routed minutes.")
                selected = trial_selection
                current_route = trial
            else:
                reason = (
                    f"Estimated trip would exceed the {available_minutes}-minute budget."
                    if worth_detour and available_minutes is not None
                    else "Its measured detour is too large for this trip."
                )
                excluded.extend(_excluded_candidate(candidate, reason))

        final_trip = _matrix_trip_for_selection(matrix, origin, selected, primary, final)
        if final_trip is None:
            return None
        selected = _order_selected_from_trip(selected, final_trip.ordered_stops)
        try:
            route = self._route_for(
                provider,
                origin,
                selected,
                primary,
                final,
                optimize=False,
            )
            route = replace(
                route,
                is_optimized=len(final_trip.ordered_stops) + int(destination is not None) > 1,
            )
            return selected, route, baseline, False, excluded
        except RoutingProviderError:
            fallback_route = self._route_for(
                DeterministicFallbackRouteProvider(),
                origin,
                selected,
                primary,
                final,
                optimize=False,
            )
            return selected, fallback_route, baseline, True, excluded

    def _route_for(
        self,
        provider: RouteProvider,
        origin: RouteStopInput,
        selected: list[CandidateStop],
        primary: RouteStopInput | None,
        final: RouteStopInput | None,
        *,
        optimize: bool,
    ) -> RouteResult:
        stops = [candidate.route_stop for candidate in selected]
        if primary and final is None:
            stops = [stop for stop in stops if _stop_key(stop) != _stop_key(primary)]
            destination = primary
        else:
            destination = final
        if not stops and destination is None:
            if selected:
                stops = [selected[0].route_stop]
            else:
                return RouteResult([], provider.name, False, None)
        return provider.calculate_route(
            base_location=origin,
            stops=stops,
            final_destination=destination,
            optimize=optimize,
        )

    def _persist_plan(
        self,
        *,
        origin: RouteStopInput,
        primary: RouteStopInput | None,
        final: RouteStopInput | None,
        selected: list[CandidateStop],
        route: RouteResult,
        baseline: RouteResult | None,
        available_minutes: int | None,
        excluded: list[dict],
        extra_reasons: list[str] | None = None,
        include_replenishment: bool,
    ) -> ErrandPlan:
        candidate_by_key = {candidate.key: candidate for candidate in selected}
        ordered_keys = [_stop_key(stop) for stop in route.stops]
        if (
            primary
            and _stop_key(primary) in candidate_by_key
            and _stop_key(primary) not in ordered_keys
        ):
            ordered_keys.append(_stop_key(primary))
        for candidate in selected:
            if candidate.key not in ordered_keys:
                ordered_keys.append(candidate.key)
        ordered_candidates = [
            candidate_by_key[key] for key in ordered_keys if key in candidate_by_key
        ]
        baseline_minutes = baseline.duration_minutes if baseline else None
        incremental = (
            max(0, route.duration_minutes - baseline_minutes)
            if route.duration_minutes is not None and baseline_minutes is not None
            else None
        )
        reasons = list(
            dict.fromkeys(
                [*(extra_reasons or [])]
                + [reason for item in ordered_candidates for reason in item.reasons]
            )
        )
        estimates_are_routed = route.duration_minutes is not None
        summary = {
            "recommendation_reasons": reasons,
            "excluded": excluded,
            "estimates_are_routed": estimates_are_routed,
            "time_budget_applied": bool(available_minutes is not None and estimates_are_routed),
        }
        plan = ErrandPlan(
            base_location=origin.destination,
            routing_provider=route.provider,
            routing_is_optimized=route.is_optimized,
            route_url=route.route_url,
            estimated_stop_minutes=sum(candidate.stop_minutes for candidate in ordered_candidates),
            travel_duration_minutes=route.duration_minutes,
            distance_meters=route.distance_meters,
            baseline_travel_duration_minutes=baseline_minutes,
            incremental_travel_duration_minutes=incremental,
            available_minutes=available_minutes,
            planning_mode="while_out",
            primary_destination=primary.destination if primary else None,
            final_destination=final.destination if final else None,
            recommendation_summary=summary,
            input_snapshot={
                "base_location": origin.destination,
                "primary_destination": primary.destination if primary else None,
                "final_destination": final.destination if final else None,
                "available_minutes": available_minutes,
                "include_replenishment": include_replenishment,
                "saved_location_ids": sorted(
                    {
                        stop.source_saved_location_id
                        for stop in (origin, primary, final)
                        if stop is not None and stop.source_saved_location_id is not None
                    }
                ),
            },
        )
        self.db.add(plan)
        self.db.flush()
        for order, candidate in enumerate(ordered_candidates, start=1):
            stop = ErrandPlanStop(
                plan_id=plan.id,
                stop_order=order,
                place_name=candidate.route_stop.place_name,
                place_address=candidate.route_stop.place_address,
            )
            self.db.add(stop)
            self.db.flush()
            for errand in candidate.errands:
                self.db.add(ErrandPlanStopErrand(stop_id=stop.id, errand_id=errand.id))
                errand.status = ErrandStatus.PLANNED.value
                errand.updated_at = utc_now()
            for item, _score, reason in candidate.household_items:
                self.db.add(
                    ErrandPlanStopHouseholdItem(
                        stop_id=stop.id,
                        household_item_id=item.id,
                        reason=reason,
                    )
                )
        plan.input_fingerprint = plan_input_fingerprint(self.db, plan.input_snapshot)
        self.db.commit()
        from app.services.route_planning_service import RoutePlanningService

        return RoutePlanningService(
            self.db, settings=self.settings, route_provider=self.route_provider
        ).get_plan(plan.id)


def _saved_location_stop(location: SavedLocation, key: str) -> RouteStopInput:
    return RouteStopInput(
        key=key,
        place_name=location.label,
        place_address=location.address,
        latitude=location.latitude,
        longitude=location.longitude,
        source_saved_location_id=location.id,
    )


def _errand_has_resolved_place(errand: Errand) -> bool:
    return bool(
        errand.place_resolution_status == "resolved"
        and errand.resolved_place_name
        and errand.resolved_place_address
        and (
            errand.resolved_provider_place_id
            or (errand.resolved_latitude is not None and errand.resolved_longitude is not None)
        )
    )


def _resolution_required_message(errands: list[Errand]) -> str:
    count = len(errands)
    titles = "; ".join(errand.title for errand in errands)
    noun = "errand needs" if count == 1 else "errands need"
    return f"{count} {noun} locations before the route can be created: {titles}."


def _stop_key(stop: RouteStopInput | None) -> str:
    if stop is None:
        return "none"
    if stop.place_address:
        return f"address:{' '.join(stop.place_address.casefold().split())}"
    if stop.latitude is not None and stop.longitude is not None:
        return f"coordinates:{stop.latitude:.5f},{stop.longitude:.5f}"
    return f"name:{' '.join(stop.place_name.casefold().split())}"


def _unique_route_points(points: list[RouteStopInput]) -> list[RouteStopInput]:
    output: list[RouteStopInput] = []
    seen: set[str] = set()
    for point in points:
        if point.key in seen:
            continue
        seen.add(point.key)
        output.append(point)
    return output


def _matrix_trip_for_selection(
    matrix,
    origin: RouteStopInput,
    selected: list[CandidateStop],
    primary: RouteStopInput | None,
    final: RouteStopInput | None,
) -> MatrixTrip | None:
    destination = final or primary
    seen_destinations = {origin.destination}
    if destination is not None:
        seen_destinations.add(destination.destination)
    stops: list[RouteStopInput] = []
    for candidate in selected:
        stop = candidate.route_stop
        if stop.destination in seen_destinations:
            continue
        seen_destinations.add(stop.destination)
        stops.append(stop)
    return best_matrix_trip(matrix, origin, stops, destination)


def _matrix_route_result(provider: str, trip: MatrixTrip) -> RouteResult:
    return RouteResult(
        stops=trip.ordered_stops,
        provider=provider,
        is_optimized=len(trip.ordered_stops) > 1,
        route_url=None,
        duration_minutes=trip.duration_minutes,
        distance_meters=trip.distance_meters,
    )


def _order_selected_from_trip(
    selected: list[CandidateStop], ordered_stops: list[RouteStopInput]
) -> list[CandidateStop]:
    by_key = {candidate.key: candidate for candidate in selected}
    ordered = [by_key[key] for stop in ordered_stops if (key := _stop_key(stop)) in by_key]
    ordered_ids = {id(candidate) for candidate in ordered}
    ordered.extend(candidate for candidate in selected if id(candidate) not in ordered_ids)
    return ordered


def _candidate_sort_key(candidate: CandidateStop) -> tuple:
    due_dates = [_aware(errand.due_at) for errand in candidate.errands if errand.due_at]
    earliest = min(due_dates) if due_dates else datetime.max.replace(tzinfo=UTC)
    priority = min((_priority_rank(errand.priority) for errand in candidate.errands), default=2)
    high_due_score = max((score for _, score, _ in candidate.household_items), default=0)
    return (
        not candidate.is_primary,
        not bool(due_dates),
        earliest,
        priority,
        -high_due_score,
        candidate.key,
    )


def _fallback_is_important(candidate: CandidateStop) -> bool:
    has_urgent_errand = any(
        errand.due_at or errand.priority == ErrandPriority.HIGH.value
        for errand in candidate.errands
    )
    has_likely_due_item = any(score >= 0.9 for _, score, _ in candidate.household_items)
    return has_urgent_errand or has_likely_due_item


def _errand_reason(errand: Errand, now: datetime) -> str:
    if errand.due_at and _aware(errand.due_at) < _aware(now):
        return f"{errand.title} is overdue."
    if errand.due_at:
        return f"{errand.title} has a due date."
    if errand.priority == ErrandPriority.HIGH.value:
        return f"{errand.title} is high priority."
    return f"{errand.title} is already in your errand inbox."


def _priority_rank(priority: str) -> int:
    return {"high": 0, "normal": 1, "low": 2}.get(priority, 1)


def _incremental_minutes(baseline: RouteResult, candidate: RouteResult) -> int | None:
    if baseline.duration_minutes is None or candidate.duration_minutes is None:
        return None
    return max(0, candidate.duration_minutes - baseline.duration_minutes)


def _excluded_item(item: HouseholdItem, reason: str) -> dict:
    return {"kind": "household_item", "id": item.id, "title": item.name, "reason": reason}


def _excluded_candidate(candidate: CandidateStop, reason: str) -> list[dict]:
    return [
        *[
            {"kind": "errand", "id": errand.id, "title": errand.title, "reason": reason}
            for errand in candidate.errands
        ],
        *[_excluded_item(item, reason) for item, _score, _item_reason in candidate.household_items],
    ]


def _empty_comparison(provider: str) -> dict:
    return {
        "routing_provider": provider,
        "has_measured_comparison": False,
        "baseline_duration_minutes": None,
        "candidate_duration_minutes": None,
        "incremental_duration_minutes": None,
        "baseline_distance_meters": None,
        "candidate_distance_meters": None,
    }


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

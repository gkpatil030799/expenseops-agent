from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from itertools import islice, product

from app.config import Settings
from app.models import Errand, PreferredPlace
from app.services.place_resolution_service import (
    PlaceCandidate,
    PlaceResolutionService,
    PlaceSearchError,
    UnavailablePlaceSearchProvider,
    preference_key,
)
from app.services.route_planning_service import (
    DeterministicFallbackRouteProvider,
    RouteMatrixResult,
    RouteProvider,
    RouteResult,
    RouteStopInput,
    RoutingProviderError,
)


@dataclass
class PlaceOptimizationResult:
    resolved_errand_ids: list[int] = field(default_factory=list)
    unresolved_errands: list[Errand] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    compared_combinations: int = 0
    chosen_route: RouteResult | None = None


@dataclass(frozen=True)
class MatrixTrip:
    duration_minutes: int
    distance_meters: int | None
    ordered_stops: list[RouteStopInput]


class TripPlaceOptimizer:
    def __init__(
        self,
        *,
        settings: Settings,
        place_resolution: PlaceResolutionService,
        route_provider: RouteProvider,
    ):
        self.settings = settings
        self.place_resolution = place_resolution
        self.route_provider = route_provider

    def resolve_for_trip(
        self,
        *,
        errands: list[Errand],
        origin: RouteStopInput,
        primary: RouteStopInput | None,
        final: RouteStopInput | None,
        available_minutes: int | None,
    ) -> PlaceOptimizationResult:
        unresolved = [errand for errand in errands if not _is_resolved(errand)]
        if not unresolved:
            return PlaceOptimizationResult()

        bias_latitude, bias_longitude = self._origin_bias(origin)
        candidate_sets: list[tuple[Errand, list[PlaceCandidate]]] = []
        missing: list[Errand] = []
        reasons: list[str] = []
        cap = getattr(self.settings, "household_place_candidates_per_errand", 3)
        for errand in unresolved:
            candidates, search_message = self._candidates(
                errand,
                latitude=bias_latitude,
                longitude=bias_longitude,
            )
            candidates = [candidate for candidate in candidates if candidate.open_now is not False]
            if not candidates:
                missing.append(errand)
                reasons.append(
                    search_message
                    or f"No currently open physical locations were found for {errand.title}."
                )
                continue
            candidate_sets.append((errand, candidates[:cap]))

        if missing:
            return PlaceOptimizationResult(unresolved_errands=missing, reasons=reasons)

        if self.route_provider.name == DeterministicFallbackRouteProvider.name:
            for errand, candidates in candidate_sets:
                preferred = next(
                    (candidate for candidate in candidates if candidate.is_preferred), None
                )
                if preferred is None and len(candidates) != 1:
                    missing.append(errand)
                    continue
                chosen = preferred or candidates[0]
                self._save(errand, chosen)
                reasons.append(
                    f"Used your preferred place for {errand.title}."
                    if chosen.is_preferred
                    else f"Resolved {errand.title} to its only concrete search result."
                )
            return PlaceOptimizationResult(
                resolved_errand_ids=[
                    errand.id for errand, _ in candidate_sets if errand not in missing
                ],
                unresolved_errands=missing,
                reasons=reasons,
            )

        fixed = _fixed_stops(errands)
        endpoint = final or primary
        required_stops = [*fixed]
        if primary is not None and (
            endpoint is None or primary.destination != endpoint.destination
        ):
            required_stops.append(primary)
        required_stops = _deduplicate_stops(required_stops, origin, endpoint)
        stop_minutes = sum(errand.estimated_duration_minutes or 0 for errand in errands)
        max_combinations = getattr(self.settings, "household_max_place_combinations", 27)
        combinations = list(
            islice(
                product(*(candidates for _, candidates in candidate_sets)),
                max_combinations,
            )
        )
        matrix_points = _deduplicate_points(
            [
                origin,
                *required_stops,
                *(
                    _candidate_stop(errand, candidate)
                    for errand, candidates in candidate_sets
                    for candidate in candidates
                ),
                *([endpoint] if endpoint is not None else []),
            ]
        )
        matrix = self._route_matrix(matrix_points)
        if matrix is None:
            return self._resolve_with_route_fallback(
                candidate_sets=candidate_sets,
                combinations=combinations,
                fixed=fixed,
                origin=origin,
                primary=primary,
                final=final,
                available_minutes=available_minutes,
                stop_minutes=stop_minutes,
                reasons=reasons,
                unresolved=unresolved,
            )

        best: tuple[float, tuple[PlaceCandidate, ...], MatrixTrip, int] | None = None
        compared = 0
        for combination in combinations:
            stops = _deduplicate_stops(
                [
                    *required_stops,
                    *[
                        _candidate_stop(errand, candidate)
                        for (errand, _), candidate in zip(candidate_sets, combination, strict=True)
                    ],
                ],
                origin,
                endpoint,
            )
            trip = best_matrix_trip(matrix, origin, stops, endpoint)
            if trip is None:
                continue
            compared += 1
            total_minutes = trip.duration_minutes + stop_minutes
            preferred_count = sum(candidate.is_preferred for candidate in combination)
            bias = getattr(self.settings, "household_preferred_place_bias_minutes", 3)
            over_budget = (
                max(0, total_minutes - available_minutes) if available_minutes is not None else 0
            )
            score = float(total_minutes + (over_budget * 2) - (preferred_count * bias))
            if best is None or score < best[0]:
                best = (score, combination, trip, total_minutes)

        if best is None:
            return PlaceOptimizationResult(
                unresolved_errands=unresolved,
                reasons=[
                    "Live place candidates were found, but no complete route fit the "
                    "trip constraints."
                ],
                compared_combinations=compared,
            )

        _score, chosen_candidates, matrix_trip, total_minutes = best
        chosen_route = RouteResult(
            stops=matrix_trip.ordered_stops,
            provider=matrix.provider,
            is_optimized=len(matrix_trip.ordered_stops) + int(endpoint is not None) > 1,
            route_url=None,
            duration_minutes=matrix_trip.duration_minutes,
            distance_meters=matrix_trip.distance_meters,
        )
        return self._finalize_choice(
            candidate_sets,
            chosen_candidates,
            chosen_route,
            total_minutes,
            stop_minutes,
            compared,
            reasons,
        )

    def _route_matrix(self, points: list[RouteStopInput]) -> RouteMatrixResult | None:
        calculate_matrix = getattr(self.route_provider, "calculate_matrix", None)
        if calculate_matrix is None:
            return None
        try:
            return calculate_matrix(points)
        except RoutingProviderError:
            return None

    def _resolve_with_route_fallback(
        self,
        *,
        candidate_sets: list[tuple[Errand, list[PlaceCandidate]]],
        combinations: list[tuple[PlaceCandidate, ...]],
        fixed: list[RouteStopInput],
        origin: RouteStopInput,
        primary: RouteStopInput | None,
        final: RouteStopInput | None,
        available_minutes: int | None,
        stop_minutes: int,
        reasons: list[str],
        unresolved: list[Errand],
    ) -> PlaceOptimizationResult:
        best: tuple[float, tuple[PlaceCandidate, ...], RouteResult, int] | None = None
        compared = 0
        for combination in combinations:
            stops = [
                *fixed,
                *[
                    _candidate_stop(errand, candidate)
                    for (errand, _), candidate in zip(candidate_sets, combination, strict=True)
                ],
            ]
            route = self._best_route(origin, stops, primary, final)
            if route is None or route.duration_minutes is None:
                continue
            compared += 1
            total_minutes = route.duration_minutes + stop_minutes
            preferred_count = sum(candidate.is_preferred for candidate in combination)
            bias = getattr(self.settings, "household_preferred_place_bias_minutes", 3)
            over_budget = (
                max(0, total_minutes - available_minutes) if available_minutes is not None else 0
            )
            score = float(total_minutes + (over_budget * 2) - (preferred_count * bias))
            if best is None or score < best[0]:
                best = (score, combination, route, total_minutes)
        if best is None:
            return PlaceOptimizationResult(
                unresolved_errands=unresolved,
                reasons=[
                    "Live place candidates were found, but no complete route fit the "
                    "trip constraints."
                ],
                compared_combinations=compared,
            )
        _score, chosen_candidates, chosen_route, total_minutes = best
        return self._finalize_choice(
            candidate_sets,
            chosen_candidates,
            chosen_route,
            total_minutes,
            stop_minutes,
            compared,
            reasons,
        )

    def _finalize_choice(
        self,
        candidate_sets: list[tuple[Errand, list[PlaceCandidate]]],
        chosen_candidates: tuple[PlaceCandidate, ...],
        chosen_route: RouteResult,
        total_minutes: int,
        stop_minutes: int,
        compared: int,
        reasons: list[str],
    ) -> PlaceOptimizationResult:
        for (errand, _), candidate in zip(candidate_sets, chosen_candidates, strict=True):
            self._save(errand, candidate)
            reasons.append(
                f"Kept your preferred {candidate.canonical_name} for {errand.title}."
                if candidate.is_preferred
                else (
                    f"Selected {candidate.canonical_name} for {errand.title} after "
                    "comparing full-trip routes."
                )
            )
            if candidate.open_now is True:
                reasons.append(f"{candidate.canonical_name} was reported open by the provider.")
        reasons.append(
            f"Compared {compared} complete location combination"
            f"{'s' if compared != 1 else ''}; the selected trip is approximately "
            f"{chosen_route.duration_minutes} driving minutes plus {stop_minutes} known stop "
            f"minutes ({total_minutes} minutes total)."
        )
        return PlaceOptimizationResult(
            resolved_errand_ids=[errand.id for errand, _ in candidate_sets],
            reasons=reasons,
            compared_combinations=compared,
            chosen_route=chosen_route,
        )

    def _candidates(
        self,
        errand: Errand,
        *,
        latitude: float | None,
        longitude: float | None,
    ) -> tuple[list[PlaceCandidate], str | None]:
        preferred = self.place_resolution.get_preferred(errand)
        candidates = [_preferred_candidate(preferred)] if preferred else []
        message = None
        if self.place_resolution.provider.name != UnavailablePlaceSearchProvider.name:
            try:
                candidates.extend(
                    self.place_resolution.provider.search_places(
                        errand.place_name or preference_key(errand),
                        latitude=latitude,
                        longitude=longitude,
                    )
                )
            except PlaceSearchError as exc:
                message = str(exc)
        else:
            message = "Automatic place search is not configured."
        return _deduplicate(candidates), message

    def _origin_bias(self, origin: RouteStopInput) -> tuple[float | None, float | None]:
        if origin.latitude is not None and origin.longitude is not None:
            return origin.latitude, origin.longitude
        try:
            geocoded = self.route_provider.geocode(origin.destination)
        except RoutingProviderError:
            return None, None
        if geocoded is None:
            return None, None
        return geocoded.latitude, geocoded.longitude

    def _best_route(
        self,
        origin: RouteStopInput,
        stops: list[RouteStopInput],
        primary: RouteStopInput | None,
        final: RouteStopInput | None,
    ) -> RouteResult | None:
        variants: list[tuple[list[RouteStopInput], RouteStopInput | None]] = []
        if final is not None:
            variants.append((stops, final))
        elif primary is not None:
            variants.append(
                ([stop for stop in stops if stop.destination != primary.destination], primary)
            )
        else:
            variants.extend(
                ([stop for index, stop in enumerate(stops) if index != final_index], destination)
                for final_index, destination in enumerate(stops)
            )
        best = None
        for intermediates, destination in variants:
            try:
                route = self.route_provider.calculate_route(
                    base_location=origin,
                    stops=intermediates,
                    final_destination=destination,
                    optimize=True,
                )
            except RoutingProviderError:
                continue
            if route.duration_minutes is not None and (
                best is None or route.duration_minutes < best.duration_minutes
            ):
                best = route
        return best

    def _save(self, errand: Errand, candidate: PlaceCandidate) -> None:
        self.place_resolution.resolve(
            errand.id,
            asdict(candidate),
            remember_preference=False,
            method="automatic",
        )


def _is_resolved(errand: Errand) -> bool:
    return bool(
        errand.place_resolution_status == "resolved"
        and errand.resolved_place_name
        and errand.resolved_place_address
    )


def _fixed_stops(errands: list[Errand]) -> list[RouteStopInput]:
    stops: dict[str, RouteStopInput] = {}
    for errand in errands:
        if not _is_resolved(errand):
            continue
        stop = RouteStopInput(
            key=f"errand:{errand.id}",
            place_name=errand.resolved_place_name or "Resolved place",
            place_address=errand.resolved_place_address,
            latitude=errand.resolved_latitude,
            longitude=errand.resolved_longitude,
        )
        stops.setdefault(stop.destination, stop)
    return list(stops.values())


def _candidate_stop(errand: Errand, candidate: PlaceCandidate) -> RouteStopInput:
    identity = candidate.provider_place_id or " ".join(candidate.full_address.casefold().split())
    return RouteStopInput(
        key=f"candidate:{errand.id}:{identity}",
        place_name=candidate.canonical_name,
        place_address=candidate.full_address,
        latitude=candidate.latitude,
        longitude=candidate.longitude,
    )


def _preferred_candidate(place: PreferredPlace) -> PlaceCandidate:
    return PlaceCandidate(
        canonical_name=place.canonical_name,
        full_address=place.full_address,
        latitude=place.latitude,
        longitude=place.longitude,
        provider_place_id=place.provider_place_id,
        is_preferred=True,
    )


def _deduplicate(candidates: list[PlaceCandidate]) -> list[PlaceCandidate]:
    output: list[PlaceCandidate] = []
    seen: dict[str, int] = {}
    for candidate in candidates:
        key = candidate.provider_place_id or candidate.full_address.casefold()
        if key not in seen:
            seen[key] = len(output)
            output.append(candidate)
        elif output[seen[key]].is_preferred:
            output[seen[key]] = replace(
                output[seen[key]],
                open_now=candidate.open_now,
                opening_hours=candidate.opening_hours,
            )
    return output


def _deduplicate_points(points: list[RouteStopInput]) -> list[RouteStopInput]:
    output: list[RouteStopInput] = []
    seen_keys: set[str] = set()
    for point in points:
        if point.key in seen_keys:
            continue
        seen_keys.add(point.key)
        output.append(point)
    return output


def _deduplicate_stops(
    stops: list[RouteStopInput],
    origin: RouteStopInput,
    endpoint: RouteStopInput | None,
) -> list[RouteStopInput]:
    output: list[RouteStopInput] = []
    seen_destinations = {origin.destination}
    if endpoint is not None:
        seen_destinations.add(endpoint.destination)
    for stop in stops:
        if stop.destination in seen_destinations:
            continue
        seen_destinations.add(stop.destination)
        output.append(stop)
    return output


def best_matrix_trip(
    matrix: RouteMatrixResult,
    origin: RouteStopInput,
    stops: list[RouteStopInput],
    endpoint: RouteStopInput | None,
) -> MatrixTrip | None:
    if not stops:
        if endpoint is None:
            return MatrixTrip(0, 0, [])
        duration = matrix.duration(origin.key, endpoint.key)
        if duration is None:
            return None
        return MatrixTrip(duration, matrix.distance(origin.key, endpoint.key), [])

    # Held-Karp shortest Hamiltonian path. Candidate counts are capped by settings,
    # so this stays small while avoiding any extra provider calls.
    states: dict[tuple[int, int], tuple[int, int | None, tuple[int, ...]]] = {}
    for index, stop in enumerate(stops):
        duration = matrix.duration(origin.key, stop.key)
        if duration is not None:
            states[(1 << index, index)] = (
                duration,
                matrix.distance(origin.key, stop.key),
                (index,),
            )

    for mask in range(1, 1 << len(stops)):
        for last in range(len(stops)):
            current = states.get((mask, last))
            if current is None:
                continue
            for next_index, next_stop in enumerate(stops):
                if mask & (1 << next_index):
                    continue
                edge_duration = matrix.duration(stops[last].key, next_stop.key)
                if edge_duration is None:
                    continue
                edge_distance = matrix.distance(stops[last].key, next_stop.key)
                candidate = (
                    current[0] + edge_duration,
                    _sum_distance(current[1], edge_distance),
                    (*current[2], next_index),
                )
                state_key = (mask | (1 << next_index), next_index)
                existing = states.get(state_key)
                if existing is None or _trip_rank(candidate) < _trip_rank(existing):
                    states[state_key] = candidate

    full_mask = (1 << len(stops)) - 1
    best: tuple[int, int | None, tuple[int, ...]] | None = None
    for last in range(len(stops)):
        current = states.get((full_mask, last))
        if current is None:
            continue
        candidate = current
        if endpoint is not None:
            edge_duration = matrix.duration(stops[last].key, endpoint.key)
            if edge_duration is None:
                continue
            candidate = (
                current[0] + edge_duration,
                _sum_distance(current[1], matrix.distance(stops[last].key, endpoint.key)),
                current[2],
            )
        if best is None or _trip_rank(candidate) < _trip_rank(best):
            best = candidate
    if best is None:
        return None
    return MatrixTrip(best[0], best[1], [stops[index] for index in best[2]])


def _sum_distance(left: int | None, right: int | None) -> int | None:
    return left + right if left is not None and right is not None else None


def _trip_rank(value: tuple[int, int | None, tuple[int, ...]]) -> tuple[int, int]:
    return value[0], value[1] if value[1] is not None else 2**63 - 1

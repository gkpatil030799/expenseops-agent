from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Errand, PlaceResolutionStatus, PreferredPlace, utc_now
from app.services.errand_service import HouseholdOpsError, HouseholdOpsNotFound
from app.services.ttl_cache import TTLCache


@dataclass(frozen=True)
class PlaceCandidate:
    canonical_name: str
    full_address: str
    latitude: float | None = None
    longitude: float | None = None
    provider_place_id: str | None = None
    is_preferred: bool = False
    open_now: bool | None = None
    opening_hours: list[str] | None = None


_PLACE_SEARCH_CACHE: TTLCache[list[PlaceCandidate]] = TTLCache()
_PLACE_DETAILS_CACHE: TTLCache[PlaceCandidate] = TTLCache()


class PlaceSearchError(RuntimeError):
    pass


class PlaceSearchProvider(Protocol):
    name: str

    def search_places(
        self,
        query: str,
        *,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> list[PlaceCandidate]: ...

    def get_place_details(self, provider_place_id: str) -> PlaceCandidate | None: ...


class UnavailablePlaceSearchProvider:
    name = "not_configured"

    def search_places(self, query: str, **kwargs) -> list[PlaceCandidate]:
        del query, kwargs
        return []

    def get_place_details(self, provider_place_id: str) -> PlaceCandidate | None:
        del provider_place_id
        return None


class GooglePlacesSearchProvider:
    name = "google_places"

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

    def search_places(
        self,
        query: str,
        *,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> list[PlaceCandidate]:
        cache_key = (
            f"{self.cache_namespace}:search:{query.casefold().strip()}:"
            f"{latitude if latitude is None else round(latitude, 4)}:"
            f"{longitude if longitude is None else round(longitude, 4)}"
        )
        if self.cache_ttl_seconds and (cached := _PLACE_SEARCH_CACHE.get(cache_key)):
            return cached
        body: dict = {"textQuery": query, "maxResultCount": 8}
        if latitude is not None and longitude is not None:
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": latitude, "longitude": longitude},
                    "radius": 25_000.0,
                }
            }
        payload = self._request(
            "POST",
            "https://places.googleapis.com/v1/places:searchText",
            field_mask=(
                "places.id,places.displayName,places.formattedAddress,places.location,"
                "places.currentOpeningHours"
            ),
            json=body,
        )
        candidates = [
            candidate
            for place in payload.get("places", [])
            if (candidate := _google_candidate(place))
        ]
        if self.cache_ttl_seconds:
            _PLACE_SEARCH_CACHE.set(cache_key, candidates, self.cache_ttl_seconds)
        return candidates

    def get_place_details(self, provider_place_id: str) -> PlaceCandidate | None:
        cache_key = f"{self.cache_namespace}:details:{provider_place_id}"
        if self.cache_ttl_seconds and (cached := _PLACE_DETAILS_CACHE.get(cache_key)):
            return cached
        payload = self._request(
            "GET",
            f"https://places.googleapis.com/v1/places/{provider_place_id}",
            field_mask="id,displayName,formattedAddress,location,currentOpeningHours",
        )
        candidate = _google_candidate(payload)
        if candidate is not None and self.cache_ttl_seconds:
            _PLACE_DETAILS_CACHE.set(cache_key, candidate, self.cache_ttl_seconds)
        return candidate

    def _request(self, method: str, url: str, *, field_mask: str, **kwargs) -> dict:
        try:
            response = self.client.request(
                method,
                url,
                headers={
                    "X-Goog-Api-Key": self.api_key,
                    "X-Goog-FieldMask": field_mask,
                },
                **kwargs,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PlaceSearchError("Place search is temporarily unavailable.") from exc


class PlaceResolutionService:
    def __init__(
        self,
        db: Session,
        settings: Settings | None = None,
        provider: PlaceSearchProvider | None = None,
    ):
        self.db = db
        self.settings = settings or get_settings()
        self.provider = provider or build_place_search_provider(self.settings)

    def search(
        self,
        errand_id: int,
        *,
        query: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> dict:
        errand = self._get_errand(errand_id)
        search_query = (query or errand.place_name or errand.title).strip()
        preferred = self.get_preferred(errand)
        candidates = [_preferred_candidate(preferred)] if preferred else []
        message = None
        try:
            candidates.extend(
                self.provider.search_places(
                    search_query,
                    latitude=latitude,
                    longitude=longitude,
                )
            )
        except PlaceSearchError as exc:
            message = str(exc)
        candidates = _deduplicate(candidates)
        errand.place_resolution_status = (
            PlaceResolutionStatus.NEEDS_USER_CHOICE.value
            if candidates
            else PlaceResolutionStatus.FAILED.value
        )
        errand.updated_at = utc_now()
        self.db.commit()
        if not candidates and message is None:
            message = (
                "No place candidates were found. Enter a concrete business name and full "
                "address manually."
                if self.provider.name != UnavailablePlaceSearchProvider.name
                else "Place search is not configured. Enter the concrete destination manually."
            )
        return {
            "errand_id": errand.id,
            "query": search_query,
            "provider": self.provider.name,
            "candidates": [asdict(candidate) for candidate in candidates],
            "message": message,
        }

    def resolve(
        self,
        errand_id: int,
        values: dict,
        *,
        remember_preference: bool,
        method: str = "user",
    ) -> Errand:
        errand = self._get_errand(errand_id)
        name = values.get("canonical_name", "").strip()
        address = values.get("full_address", "").strip()
        latitude = values.get("latitude")
        longitude = values.get("longitude")
        if not name or not address:
            raise HouseholdOpsError("A canonical place name and full address are required.")
        if (latitude is None) != (longitude is None):
            raise HouseholdOpsError("Latitude and longitude must be provided together.")
        errand.resolved_place_name = name
        errand.resolved_place_address = address
        errand.resolved_latitude = latitude
        errand.resolved_longitude = longitude
        errand.resolved_provider_place_id = values.get("provider_place_id")
        errand.resolved_open_now = values.get("open_now")
        errand.resolved_opening_hours = values.get("opening_hours")
        errand.place_resolution_method = method
        errand.place_resolution_status = PlaceResolutionStatus.RESOLVED.value
        errand.updated_at = utc_now()
        if remember_preference:
            self._remember(errand, values)
        self.db.commit()
        return errand

    def clear_resolution(self, errand: Errand) -> None:
        errand.place_resolution_status = PlaceResolutionStatus.UNRESOLVED.value
        errand.resolved_place_name = None
        errand.resolved_place_address = None
        errand.resolved_latitude = None
        errand.resolved_longitude = None
        errand.resolved_provider_place_id = None
        errand.resolved_open_now = None
        errand.resolved_opening_hours = None
        errand.place_resolution_method = None

    def get_preferred(self, errand: Errand) -> PreferredPlace | None:
        return self.db.execute(
            select(PreferredPlace).where(PreferredPlace.preference_key == preference_key(errand))
        ).scalar_one_or_none()

    def _remember(self, errand: Errand, values: dict) -> None:
        key = preference_key(errand)
        preferred = self.db.execute(
            select(PreferredPlace).where(PreferredPlace.preference_key == key)
        ).scalar_one_or_none()
        if preferred is None:
            preferred = PreferredPlace(preference_key=key)
            self.db.add(preferred)
        preferred.canonical_name = values["canonical_name"].strip()
        preferred.full_address = values["full_address"].strip()
        preferred.latitude = values.get("latitude")
        preferred.longitude = values.get("longitude")
        preferred.provider_place_id = values.get("provider_place_id")
        preferred.updated_at = utc_now()

    def _get_errand(self, errand_id: int) -> Errand:
        errand = self.db.get(Errand, errand_id)
        if errand is None:
            raise HouseholdOpsNotFound("Errand not found.")
        return errand


def build_place_search_provider(settings: Settings) -> PlaceSearchProvider:
    if getattr(
        settings, "household_place_search_provider", "fallback"
    ) == "google_places" and getattr(settings, "google_maps_api_key", ""):
        return GooglePlacesSearchProvider(
            settings.google_maps_api_key,
            cache_ttl_seconds=getattr(settings, "household_provider_cache_ttl_seconds", 900),
        )
    return UnavailablePlaceSearchProvider()


def preference_key(errand: Errand) -> str:
    source = errand.place_name or errand.title
    words = re.findall(r"[a-z0-9]+", source.casefold())
    ignored = {"a", "an", "at", "buy", "get", "go", "pick", "shop", "the", "to", "up"}
    normalized = [word for word in words if word not in ignored]
    return " ".join(normalized or words)


def _google_candidate(place: dict) -> PlaceCandidate | None:
    name = (place.get("displayName") or {}).get("text")
    address = place.get("formattedAddress")
    if not name or not address:
        return None
    location = place.get("location") or {}
    opening = place.get("currentOpeningHours") or {}
    return PlaceCandidate(
        canonical_name=name,
        full_address=address,
        latitude=location.get("latitude"),
        longitude=location.get("longitude"),
        provider_place_id=place.get("id"),
        open_now=opening.get("openNow"),
        opening_hours=opening.get("weekdayDescriptions"),
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

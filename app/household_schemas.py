from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ErrandTypeValue = Literal["purchase", "return", "pickup", "service", "other"]
ErrandPriorityValue = Literal["low", "normal", "high"]
ErrandStatusValue = Literal["open", "planned", "completed", "skipped"]
ReplenishmentModeValue = Literal["errand", "delivery", "either"]


class HouseholdItemCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    quantity: str | None = Field(default=None, max_length=64)
    unit: str | None = Field(default=None, max_length=64)
    preferred_place_name: str | None = Field(default=None, max_length=255)
    preferred_place_address: str | None = Field(default=None, max_length=1000)
    replenishment_mode: ReplenishmentModeValue = "either"
    cadence_days: int = Field(..., ge=1, le=3650)
    last_acquired_at: datetime | None = None
    snoozed_until: datetime | None = None
    enabled: bool = True
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator(
        "name",
        "quantity",
        "unit",
        "preferred_place_name",
        "preferred_place_address",
        "notes",
    )
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class HouseholdItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    quantity: str | None = Field(default=None, max_length=64)
    unit: str | None = Field(default=None, max_length=64)
    preferred_place_name: str | None = Field(default=None, max_length=255)
    preferred_place_address: str | None = Field(default=None, max_length=1000)
    replenishment_mode: ReplenishmentModeValue | None = None
    cadence_days: int | None = Field(default=None, ge=1, le=3650)
    last_acquired_at: datetime | None = None
    snoozed_until: datetime | None = None
    enabled: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_change(self) -> HouseholdItemUpdate:
        if not self.model_fields_set:
            raise ValueError("provide at least one field to update")
        return self


class HouseholdItemOut(BaseModel):
    id: int
    name: str
    quantity: str | None
    unit: str | None
    preferred_place_name: str | None
    preferred_place_address: str | None
    replenishment_mode: ReplenishmentModeValue
    cadence_days: int
    last_acquired_at: datetime | None
    snoozed_until: datetime | None
    enabled: bool
    notes: str | None
    due_score: float
    due_state: Literal["likely_due", "probably_due", "not_due", "unknown"]
    should_surface: bool
    linked_errand_id: int | None = None
    created_at: datetime
    updated_at: datetime


class StillHaveRequest(BaseModel):
    snooze_days: int | None = Field(default=None, ge=1, le=90)


class ErrandCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    errand_type: ErrandTypeValue = "other"
    place_name: str | None = Field(default=None, max_length=255)
    place_address: str | None = Field(default=None, max_length=1000)
    due_at: datetime | None = None
    estimated_duration_minutes: int | None = Field(default=None, ge=0, le=1440)
    priority: ErrandPriorityValue = "normal"
    notes: str | None = Field(default=None, max_length=2000)
    included_in_next_plan: bool = True

    @field_validator("title", "place_name", "place_address", "notes")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ErrandUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    errand_type: ErrandTypeValue | None = None
    place_name: str | None = Field(default=None, max_length=255)
    place_address: str | None = Field(default=None, max_length=1000)
    due_at: datetime | None = None
    estimated_duration_minutes: int | None = Field(default=None, ge=0, le=1440)
    priority: ErrandPriorityValue | None = None
    status: ErrandStatusValue | None = None
    notes: str | None = Field(default=None, max_length=2000)
    included_in_next_plan: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> ErrandUpdate:
        if not self.model_fields_set:
            raise ValueError("provide at least one field to update")
        return self


class LinkedHouseholdItemOut(BaseModel):
    id: int
    name: str
    quantity: str | None
    unit: str | None


class ErrandOut(BaseModel):
    id: int
    title: str
    errand_type: ErrandTypeValue
    place_name: str | None
    place_address: str | None
    place_resolution_status: Literal["unresolved", "needs_user_choice", "resolved", "failed"]
    resolved_place_name: str | None
    resolved_place_address: str | None
    resolved_latitude: float | None
    resolved_longitude: float | None
    resolved_provider_place_id: str | None
    resolved_open_now: bool | None
    resolved_opening_hours: list[str] | None
    place_resolution_method: str | None
    due_at: datetime | None
    estimated_duration_minutes: int | None
    priority: ErrandPriorityValue
    status: ErrandStatusValue
    source: Literal["manual", "replenishment"]
    notes: str | None
    included_in_next_plan: bool
    linked_household_items: list[LinkedHouseholdItemOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PlaceSearchRequest(BaseModel):
    query: str | None = Field(default=None, max_length=255)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def coordinates_are_paired(self) -> PlaceSearchRequest:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class PlaceCandidateOut(BaseModel):
    canonical_name: str
    full_address: str
    latitude: float | None = None
    longitude: float | None = None
    provider_place_id: str | None = None
    is_preferred: bool = False
    open_now: bool | None = None
    opening_hours: list[str] | None = None


class PlaceSearchOut(BaseModel):
    errand_id: int
    query: str
    provider: str
    candidates: list[PlaceCandidateOut]
    message: str | None = None


class ResolveErrandPlaceRequest(PlaceCandidateOut):
    remember_preference: bool = False

    @model_validator(mode="after")
    def validate_concrete_place(self) -> ResolveErrandPlaceRequest:
        if not self.canonical_name.strip() or not self.full_address.strip():
            raise ValueError("canonical name and full address are required")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class CompleteErrandRequest(BaseModel):
    mark_linked_items_acquired: bool = False


class ReplenishmentSuggestionOut(BaseModel):
    household_item_id: int
    household_item_name: str
    action: Literal["created_errand", "added_to_existing_stop", "delivery_only", "due_no_trip"]
    errand_id: int | None = None
    message: str


class ReplenishmentRefreshOut(BaseModel):
    due_items: list[HouseholdItemOut]
    suggestions: list[ReplenishmentSuggestionOut]
    created_errand_count: int


class AddItemToErrandsRequest(BaseModel):
    errand_id: int | None = Field(default=None, gt=0)


class ErrandPlanCreate(BaseModel):
    planned_for: datetime | None = None
    base_location: str | None = Field(default=None, max_length=1000)


class PlannedHouseholdItemOut(BaseModel):
    id: int
    name: str
    quantity: str | None
    unit: str | None
    due_score: float
    reason: str | None = None


class ErrandPlanStopOut(BaseModel):
    id: int
    stop_order: int
    place_name: str
    place_address: str | None
    errands: list[ErrandOut]
    household_items: list[PlannedHouseholdItemOut] = Field(default_factory=list)


class ErrandPlanOut(BaseModel):
    id: int
    planned_for: datetime | None
    base_location: str | None
    status: Literal["planned", "started", "completed"]
    routing_provider: str
    routing_is_optimized: bool
    route_url: str | None
    estimated_stop_minutes: int
    travel_duration_minutes: int | None = None
    distance_meters: int | None = None
    baseline_travel_duration_minutes: int | None = None
    incremental_travel_duration_minutes: int | None = None
    available_minutes: int | None = None
    planning_mode: str = "standard"
    primary_destination: str | None = None
    final_destination: str | None = None
    stop_count: int
    stops: list[ErrandPlanStopOut]
    created_at: datetime
    updated_at: datetime


class SavedLocationCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=100)
    address: str = Field(..., min_length=1, max_length=1000)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    location_type: Literal["home", "work", "custom"] = "custom"

    @field_validator("label", "address")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value

    @model_validator(mode="after")
    def coordinates_are_paired(self) -> SavedLocationCreate:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class SavedLocationUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    address: str | None = Field(default=None, min_length=1, max_length=1000)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    location_type: Literal["home", "work", "custom"] | None = None

    @model_validator(mode="after")
    def validate_update(self) -> SavedLocationUpdate:
        if not self.model_fields_set:
            raise ValueError("provide at least one field to update")
        coordinate_fields = {"latitude", "longitude"} & self.model_fields_set
        if coordinate_fields and coordinate_fields != {"latitude", "longitude"}:
            raise ValueError("latitude and longitude must be updated together")
        return self


class SavedLocationOut(BaseModel):
    id: int
    label: str
    address: str
    latitude: float | None
    longitude: float | None
    location_type: Literal["home", "work", "custom"]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PlanningLocationInput(BaseModel):
    saved_location_id: int | None = Field(default=None, gt=0)
    label: str | None = Field(default=None, max_length=100)
    address: str | None = Field(default=None, max_length=1000)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def validate_location(self) -> PlanningLocationInput:
        has_coordinates = self.latitude is not None or self.longitude is not None
        if has_coordinates and (self.latitude is None or self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        if not self.saved_location_id and not self.address and not has_coordinates:
            raise ValueError("provide a saved location, address, or coordinates")
        return self


class WhileOutPlanRequest(BaseModel):
    origin: PlanningLocationInput
    primary_destination: PlanningLocationInput | None = None
    final_destination: PlanningLocationInput | None = None
    available_minutes: int | None = Field(default=None, ge=1, le=1440)
    errand_ids: list[int] = Field(default_factory=list)
    include_replenishment: bool = True


class ExcludedRecommendationOut(BaseModel):
    kind: Literal["errand", "household_item"]
    id: int
    title: str
    reason: str


class WhileOutPlanOut(BaseModel):
    plan: ErrandPlanOut
    recommendation_reasons: list[str]
    excluded: list[ExcludedRecommendationOut]
    estimates_are_routed: bool
    time_budget_applied: bool


class RouteComparisonRequest(BaseModel):
    origin: PlanningLocationInput
    destination: PlanningLocationInput
    candidate: PlanningLocationInput


class RouteComparisonOut(BaseModel):
    routing_provider: str
    has_measured_comparison: bool
    baseline_duration_minutes: int | None = None
    candidate_duration_minutes: int | None = None
    incremental_duration_minutes: int | None = None
    baseline_distance_meters: int | None = None
    candidate_distance_meters: int | None = None

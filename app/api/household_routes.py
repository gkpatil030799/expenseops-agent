from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import DbSession
from app.household_schemas import (
    AddItemToErrandsRequest,
    CompleteErrandRequest,
    ErrandCreate,
    ErrandOut,
    ErrandPlanCreate,
    ErrandPlanOut,
    ErrandUpdate,
    HouseholdItemCreate,
    HouseholdItemOut,
    HouseholdItemUpdate,
    PlaceSearchOut,
    PlaceSearchRequest,
    ReplenishmentRefreshOut,
    ResolveErrandPlaceRequest,
    RouteComparisonOut,
    RouteComparisonRequest,
    SavedLocationCreate,
    SavedLocationOut,
    SavedLocationUpdate,
    StillHaveRequest,
    WhileOutPlanOut,
    WhileOutPlanRequest,
)
from app.services.errand_service import (
    ErrandService,
    HouseholdOpsError,
    HouseholdOpsNotFound,
    errand_to_dict,
)
from app.services.place_resolution_service import PlaceResolutionService
from app.services.replenishment_service import ReplenishmentService
from app.services.route_planning_service import RoutePlanningService
from app.services.saved_location_service import SavedLocationService
from app.services.while_out_service import WhileOutService

router = APIRouter(prefix="/api/household", tags=["household-ops"])


@router.get("/locations", response_model=list[SavedLocationOut])
def list_saved_locations(db: DbSession) -> list[SavedLocationOut]:
    return [
        SavedLocationOut.model_validate(location)
        for location in SavedLocationService(db).list_locations()
    ]


@router.post("/locations", response_model=SavedLocationOut, status_code=status.HTTP_201_CREATED)
def create_saved_location(payload: SavedLocationCreate, db: DbSession) -> SavedLocationOut:
    try:
        location = SavedLocationService(db).create_location(payload.model_dump())
        return SavedLocationOut.model_validate(location)
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc


@router.patch("/locations/{location_id}", response_model=SavedLocationOut)
def update_saved_location(
    location_id: int,
    payload: SavedLocationUpdate,
    db: DbSession,
) -> SavedLocationOut:
    try:
        location = SavedLocationService(db).update_location(
            location_id, payload.model_dump(exclude_unset=True)
        )
        return SavedLocationOut.model_validate(location)
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc


@router.delete("/locations/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved_location(location_id: int, db: DbSession) -> Response:
    try:
        SavedLocationService(db).delete_location(location_id)
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/items", response_model=list[HouseholdItemOut])
def list_household_items(
    db: DbSession,
    due_only: bool = Query(default=False),
) -> list[HouseholdItemOut]:
    service = ReplenishmentService(db)
    items = service.due_items() if due_only else service.list_items()
    return [HouseholdItemOut.model_validate(service.to_dict(item)) for item in items]


@router.post("/items", response_model=HouseholdItemOut, status_code=status.HTTP_201_CREATED)
def create_household_item(payload: HouseholdItemCreate, db: DbSession) -> HouseholdItemOut:
    service = ReplenishmentService(db)
    item = service.create_item(payload.model_dump())
    return HouseholdItemOut.model_validate(service.to_dict(item))


@router.patch("/items/{item_id}", response_model=HouseholdItemOut)
def update_household_item(
    item_id: int,
    payload: HouseholdItemUpdate,
    db: DbSession,
) -> HouseholdItemOut:
    service = ReplenishmentService(db)
    try:
        item = service.update_item(item_id, payload.model_dump(exclude_unset=True))
        return HouseholdItemOut.model_validate(service.to_dict(item))
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_household_item(item_id: int, db: DbSession) -> Response:
    try:
        ReplenishmentService(db).delete_item(item_id)
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/items/{item_id}/bought", response_model=HouseholdItemOut)
def mark_household_item_bought(item_id: int, db: DbSession) -> HouseholdItemOut:
    service = ReplenishmentService(db)
    try:
        item = service.mark_bought(item_id)
        return HouseholdItemOut.model_validate(service.to_dict(item))
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc


@router.post("/items/{item_id}/still-have", response_model=HouseholdItemOut)
def mark_household_item_still_available(
    item_id: int,
    payload: StillHaveRequest,
    db: DbSession,
) -> HouseholdItemOut:
    service = ReplenishmentService(db)
    try:
        item = service.still_have(item_id, snooze_days=payload.snooze_days)
        return HouseholdItemOut.model_validate(service.to_dict(item))
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc


@router.post("/items/{item_id}/skip-once", response_model=HouseholdItemOut)
def skip_household_item_once(item_id: int, db: DbSession) -> HouseholdItemOut:
    service = ReplenishmentService(db)
    try:
        item = service.skip_once(item_id)
        return HouseholdItemOut.model_validate(service.to_dict(item))
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc


@router.post("/items/{item_id}/disable", response_model=HouseholdItemOut)
def disable_household_item(item_id: int, db: DbSession) -> HouseholdItemOut:
    service = ReplenishmentService(db)
    try:
        item = service.disable(item_id)
        return HouseholdItemOut.model_validate(service.to_dict(item))
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc


@router.post("/items/{item_id}/add-to-errands", response_model=ErrandOut)
def add_household_item_to_errands(
    item_id: int,
    payload: AddItemToErrandsRequest,
    db: DbSession,
) -> ErrandOut:
    try:
        errand = ReplenishmentService(db).add_to_errands(item_id, payload.errand_id)
        return ErrandOut.model_validate(errand_to_dict(errand))
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc


@router.post("/replenishment/refresh", response_model=ReplenishmentRefreshOut)
def refresh_replenishment(db: DbSession) -> ReplenishmentRefreshOut:
    service = ReplenishmentService(db)
    due, suggestions = service.refresh()
    return ReplenishmentRefreshOut(
        due_items=[HouseholdItemOut.model_validate(service.to_dict(item)) for item in due],
        suggestions=suggestions,
        created_errand_count=sum(item["action"] == "created_errand" for item in suggestions),
    )


@router.get("/errands", response_model=list[ErrandOut])
def list_errands(
    db: DbSession,
    errand_status: str | None = Query(default=None, alias="status"),
) -> list[ErrandOut]:
    errands = ErrandService(db).list_errands(status=errand_status)
    return [ErrandOut.model_validate(errand_to_dict(errand)) for errand in errands]


@router.post("/errands", response_model=ErrandOut, status_code=status.HTTP_201_CREATED)
def create_errand(payload: ErrandCreate, db: DbSession) -> ErrandOut:
    errand = ErrandService(db).create_errand(payload.model_dump())
    return ErrandOut.model_validate(errand_to_dict(errand))


@router.patch("/errands/{errand_id}", response_model=ErrandOut)
def update_errand(errand_id: int, payload: ErrandUpdate, db: DbSession) -> ErrandOut:
    try:
        errand = ErrandService(db).update_errand(
            errand_id,
            payload.model_dump(exclude_unset=True),
        )
        return ErrandOut.model_validate(errand_to_dict(errand))
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc


@router.post("/errands/{errand_id}/place-search", response_model=PlaceSearchOut)
def search_errand_places(
    errand_id: int,
    payload: PlaceSearchRequest,
    db: DbSession,
) -> PlaceSearchOut:
    try:
        result = PlaceResolutionService(db).search(
            errand_id,
            query=payload.query,
            latitude=payload.latitude,
            longitude=payload.longitude,
        )
        return PlaceSearchOut.model_validate(result)
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc


@router.post("/errands/{errand_id}/resolve-place", response_model=ErrandOut)
def resolve_errand_place(
    errand_id: int,
    payload: ResolveErrandPlaceRequest,
    db: DbSession,
) -> ErrandOut:
    try:
        values = payload.model_dump(exclude={"remember_preference", "is_preferred"})
        errand = PlaceResolutionService(db).resolve(
            errand_id,
            values,
            remember_preference=payload.remember_preference,
        )
        return ErrandOut.model_validate(errand_to_dict(errand))
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc


@router.delete("/errands/{errand_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_errand(errand_id: int, db: DbSession) -> Response:
    try:
        ErrandService(db).delete_errand(errand_id)
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/errands/{errand_id}/complete", response_model=ErrandOut)
def complete_errand(
    errand_id: int,
    payload: CompleteErrandRequest,
    db: DbSession,
) -> ErrandOut:
    try:
        errand = ErrandService(db).complete_errand(
            errand_id,
            mark_linked_items_acquired=payload.mark_linked_items_acquired,
        )
        return ErrandOut.model_validate(errand_to_dict(errand))
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc


@router.post("/errands/{errand_id}/skip", response_model=ErrandOut)
def skip_errand(errand_id: int, db: DbSession) -> ErrandOut:
    try:
        errand = ErrandService(db).skip_errand(errand_id)
        return ErrandOut.model_validate(errand_to_dict(errand))
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc


@router.post("/errand-plans", response_model=ErrandPlanOut, status_code=status.HTTP_201_CREATED)
def create_errand_plan(payload: ErrandPlanCreate, db: DbSession) -> ErrandPlanOut:
    service = RoutePlanningService(db)
    try:
        plan = service.create_plan(
            planned_for=payload.planned_for,
            base_location=payload.base_location,
        )
        return ErrandPlanOut.model_validate(service.to_dict(plan))
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc


@router.get("/errand-plans/latest", response_model=ErrandPlanOut | None)
def get_latest_errand_plan(db: DbSession) -> ErrandPlanOut | None:
    service = RoutePlanningService(db)
    plan = service.latest_plan()
    return ErrandPlanOut.model_validate(service.to_dict(plan)) if plan else None


@router.post("/errand-plans/while-out", response_model=WhileOutPlanOut)
def create_while_out_plan(payload: WhileOutPlanRequest, db: DbSession) -> WhileOutPlanOut:
    while_out = WhileOutService(db)
    route_plans = RoutePlanningService(db)
    try:
        plan, summary = while_out.plan(
            origin=payload.origin.model_dump(),
            primary_destination=(
                payload.primary_destination.model_dump() if payload.primary_destination else None
            ),
            final_destination=(
                payload.final_destination.model_dump() if payload.final_destination else None
            ),
            available_minutes=payload.available_minutes,
            errand_ids=payload.errand_ids,
            include_replenishment=payload.include_replenishment,
        )
        return WhileOutPlanOut(
            plan=ErrandPlanOut.model_validate(route_plans.to_dict(plan)),
            recommendation_reasons=summary.get("recommendation_reasons", []),
            excluded=summary.get("excluded", []),
            estimates_are_routed=bool(summary.get("estimates_are_routed")),
            time_budget_applied=bool(summary.get("time_budget_applied")),
        )
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc


@router.post("/errand-plans/compare", response_model=RouteComparisonOut)
def compare_errand_route(
    payload: RouteComparisonRequest,
    db: DbSession,
) -> RouteComparisonOut:
    result = WhileOutService(db).compare(
        origin=payload.origin.model_dump(),
        destination=payload.destination.model_dump(),
        candidate=payload.candidate.model_dump(),
    )
    return RouteComparisonOut.model_validate(result)


@router.get("/errand-plans/{plan_id}", response_model=ErrandPlanOut)
def get_errand_plan(plan_id: int, db: DbSession) -> ErrandPlanOut:
    service = RoutePlanningService(db)
    try:
        return ErrandPlanOut.model_validate(service.to_dict(service.get_plan(plan_id)))
    except HouseholdOpsError as exc:
        raise _http_error(exc) from exc


def _http_error(exc: HouseholdOpsError) -> HTTPException:
    return HTTPException(
        status_code=404 if isinstance(exc, HouseholdOpsNotFound) else 400,
        detail=str(exc),
    )

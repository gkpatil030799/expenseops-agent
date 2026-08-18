from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import (
    CurrentUser,
    CurrentWorkspace,
    CurrentWorkspaceOwner,
    DbSession,
)
from app.config import get_settings
from app.models import (
    ClassificationActivityType,
    ReplenishmentEligibility,
    SpendingParentCategory,
)
from app.services.autonomous_classification_service import AutonomousClassificationService
from app.services.classification_taxonomy_service import (
    MAX_CONCEPT_LIST_RESULTS,
    MAX_SUBCATEGORY_LIST_RESULTS,
    ClassificationCollisionError,
    ClassificationTaxonomyError,
    ClassificationTaxonomyService,
)
from app.services.household_item_merge_service import HouseholdItemMergeService
from app.services.managed_auth_service import record_audit

router = APIRouter(prefix="/api/classification", tags=["classification"])


class ClassificationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    autonomous_enabled: bool


class ClassificationSettingsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    autonomous_enabled: bool
    global_rollout_enabled: bool
    effective_autonomous_enabled: bool


class ClassificationCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    spending_parent_category: SpendingParentCategory
    item_activity_type: ClassificationActivityType
    replenishment_eligibility: ReplenishmentEligibility
    subcategory_name: str | None = Field(default=None, min_length=1, max_length=128)
    canonical_concept: str | None = Field(default=None, min_length=1, max_length=255)


class ClassificationCorrectionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applied: bool
    reason: str
    version: int | None


class ClassificationConceptRename(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)


class ClassificationConceptMerge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_concept_id: int = Field(strict=True, ge=1)


class ClassificationSubcategoryRename(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)


class ClassificationSubcategoryMerge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_subcategory_id: int = Field(strict=True, ge=1)


class HouseholdItemMerge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_household_item_id: int = Field(strict=True, ge=1)


class HouseholdItemMergeUndo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merge_event_id: int = Field(strict=True, ge=1)


class ClassificationConceptOut(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=255)
    parent_category: SpendingParentCategory
    subcategory_id: int | None = Field(default=None, ge=1)
    subcategory_name: str | None = Field(default=None, min_length=1, max_length=128)
    item_activity_type: ClassificationActivityType
    replenishment_eligibility: ReplenishmentEligibility
    linked_household_item_count: int = Field(ge=0)
    can_merge_as_source: bool


class ClassificationConceptListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concepts: list[ClassificationConceptOut]
    has_more: bool


class ClassificationConceptMutationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applied: bool
    source_concept_id: int = Field(ge=1)
    target_concept_id: int = Field(ge=1)
    target_name: str = Field(min_length=1, max_length=255)
    aliases_moved: int = Field(ge=0)
    receipt_items_updated: int = Field(ge=0)
    transactions_updated: int = Field(ge=0)
    household_items_merged: bool = False


class ClassificationSubcategoryOut(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=128)
    parent_category: SpendingParentCategory
    concept_count: int = Field(ge=0)


class ClassificationSubcategoryListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subcategories: list[ClassificationSubcategoryOut]
    has_more: bool


class ClassificationSubcategoryMutationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applied: bool
    source_subcategory_id: int = Field(ge=1)
    target_subcategory_id: int = Field(ge=1)
    target_name: str = Field(min_length=1, max_length=128)
    concepts_updated: int = Field(ge=0)
    receipt_items_updated: int = Field(ge=0)
    transactions_updated: int = Field(ge=0)


class HouseholdItemMergeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applied: bool
    source_household_item_id: int = Field(ge=1)
    target_household_item_id: int = Field(ge=1)
    target_name: str = Field(min_length=1, max_length=255)
    merge_event_id: int = Field(ge=1)
    aliases_moved: int = Field(ge=0)
    receipt_items_updated: int = Field(ge=0)
    acquisitions_moved: int = Field(ge=0)
    errand_links_updated: int = Field(ge=0)
    plan_links_updated: int = Field(ge=0)
    predictions_invalidated: int = Field(ge=0)
    reverted: bool


def _settings_out(*, autonomous_enabled: bool) -> ClassificationSettingsOut:
    global_enabled = get_settings().autonomous_classification_enabled
    return ClassificationSettingsOut(
        autonomous_enabled=autonomous_enabled,
        global_rollout_enabled=global_enabled,
        effective_autonomous_enabled=global_enabled and autonomous_enabled,
    )


def _concept_mutation_out(value) -> ClassificationConceptMutationOut:
    return ClassificationConceptMutationOut(
        applied=value.applied,
        source_concept_id=value.source_concept_id,
        target_concept_id=value.target_concept_id,
        target_name=value.target_name,
        aliases_moved=value.aliases_moved,
        receipt_items_updated=value.receipt_items_updated,
        transactions_updated=value.transactions_updated,
        household_items_merged=False,
    )


def _subcategory_mutation_out(value) -> ClassificationSubcategoryMutationOut:
    return ClassificationSubcategoryMutationOut.model_validate(value, from_attributes=True)


def _household_item_mutation_out(value) -> HouseholdItemMergeOut:
    return HouseholdItemMergeOut.model_validate(value, from_attributes=True)


def _raise_taxonomy_http_error(exc: ClassificationTaxonomyError) -> NoReturn:
    if "not found" in str(exc).casefold():
        status_code = 404
    elif isinstance(exc, ClassificationCollisionError):
        status_code = 409
    else:
        status_code = 400
    raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/settings", response_model=ClassificationSettingsOut)
def classification_settings(
    db: DbSession,
    _owner: CurrentWorkspaceOwner,
) -> ClassificationSettingsOut:
    settings = ClassificationTaxonomyService(db).get_settings(create=False)
    return _settings_out(
        autonomous_enabled=settings.autonomous_enabled if settings is not None else False
    )


@router.patch("/settings", response_model=ClassificationSettingsOut)
def update_classification_settings(
    payload: ClassificationSettingsUpdate,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    _owner: CurrentWorkspaceOwner,
) -> ClassificationSettingsOut:
    settings = ClassificationTaxonomyService(db).get_settings(create=True)
    assert settings is not None
    previous = settings.autonomous_enabled
    settings.autonomous_enabled = payload.autonomous_enabled
    record_audit(
        db,
        workspace_id=workspace.id,
        user_id=user.id,
        event_type="classification_settings_updated",
        resource_type="classification_settings",
        resource_id=str(settings.id),
        metadata={
            "autonomous_enabled": payload.autonomous_enabled,
            "previous_autonomous_enabled": previous,
        },
    )
    db.commit()
    return _settings_out(autonomous_enabled=settings.autonomous_enabled)


@router.get("/concepts", response_model=ClassificationConceptListOut)
def list_classification_concepts(
    db: DbSession,
    _owner: CurrentWorkspaceOwner,
    limit: int = Query(default=100, ge=1, le=MAX_CONCEPT_LIST_RESULTS),
) -> ClassificationConceptListOut:
    concepts, has_more = ClassificationTaxonomyService(db).list_active_concepts(limit=limit)
    return ClassificationConceptListOut(
        concepts=[
            ClassificationConceptOut(
                id=value.id,
                name=value.name,
                parent_category=value.parent_category,
                subcategory_id=value.subcategory_id,
                subcategory_name=value.subcategory_name,
                item_activity_type=value.item_activity_type,
                replenishment_eligibility=value.replenishment_eligibility,
                linked_household_item_count=value.linked_household_item_count,
                can_merge_as_source=True,
            )
            for value in concepts
        ],
        has_more=has_more,
    )


@router.patch(
    "/concepts/{concept_id}",
    response_model=ClassificationConceptMutationOut,
)
def rename_classification_concept(
    concept_id: Annotated[int, Path(ge=1)],
    payload: ClassificationConceptRename,
    db: DbSession,
    _owner: CurrentWorkspaceOwner,
) -> ClassificationConceptMutationOut:
    try:
        result = ClassificationTaxonomyService(db).rename_concept(
            concept_id,
            name=payload.name,
        )
    except ClassificationTaxonomyError as exc:
        _raise_taxonomy_http_error(exc)
    return _concept_mutation_out(result)


@router.post(
    "/concepts/{source_concept_id}/merge",
    response_model=ClassificationConceptMutationOut,
)
def merge_classification_concepts(
    source_concept_id: Annotated[int, Path(ge=1)],
    payload: ClassificationConceptMerge,
    db: DbSession,
    _owner: CurrentWorkspaceOwner,
) -> ClassificationConceptMutationOut:
    try:
        result = ClassificationTaxonomyService(db).merge_concepts(
            source_concept_id,
            target_concept_id=payload.target_concept_id,
        )
    except ClassificationTaxonomyError as exc:
        _raise_taxonomy_http_error(exc)
    return _concept_mutation_out(result)


@router.get("/subcategories", response_model=ClassificationSubcategoryListOut)
def list_classification_subcategories(
    db: DbSession,
    _owner: CurrentWorkspaceOwner,
    limit: int = Query(default=100, ge=1, le=MAX_SUBCATEGORY_LIST_RESULTS),
) -> ClassificationSubcategoryListOut:
    values, has_more = ClassificationTaxonomyService(db).list_active_subcategories(limit=limit)
    return ClassificationSubcategoryListOut(
        subcategories=[
            ClassificationSubcategoryOut.model_validate(value, from_attributes=True)
            for value in values
        ],
        has_more=has_more,
    )


@router.patch(
    "/subcategories/{subcategory_id}",
    response_model=ClassificationSubcategoryMutationOut,
)
def rename_classification_subcategory(
    subcategory_id: Annotated[int, Path(ge=1)],
    payload: ClassificationSubcategoryRename,
    db: DbSession,
    _owner: CurrentWorkspaceOwner,
) -> ClassificationSubcategoryMutationOut:
    try:
        result = ClassificationTaxonomyService(db).rename_subcategory(
            subcategory_id,
            name=payload.name,
        )
    except ClassificationTaxonomyError as exc:
        _raise_taxonomy_http_error(exc)
    return _subcategory_mutation_out(result)


@router.post(
    "/subcategories/{source_subcategory_id}/merge",
    response_model=ClassificationSubcategoryMutationOut,
)
def merge_classification_subcategories(
    source_subcategory_id: Annotated[int, Path(ge=1)],
    payload: ClassificationSubcategoryMerge,
    db: DbSession,
    _owner: CurrentWorkspaceOwner,
) -> ClassificationSubcategoryMutationOut:
    try:
        result = ClassificationTaxonomyService(db).merge_subcategories(
            source_subcategory_id,
            target_subcategory_id=payload.target_subcategory_id,
        )
    except ClassificationTaxonomyError as exc:
        _raise_taxonomy_http_error(exc)
    return _subcategory_mutation_out(result)


@router.post(
    "/household-items/{source_item_id}/merge",
    response_model=HouseholdItemMergeOut,
)
def merge_household_items(
    source_item_id: Annotated[int, Path(ge=1)],
    payload: HouseholdItemMerge,
    db: DbSession,
    _owner: CurrentWorkspaceOwner,
) -> HouseholdItemMergeOut:
    try:
        result = HouseholdItemMergeService(db).merge(
            source_item_id,
            target_item_id=payload.target_household_item_id,
        )
    except ClassificationTaxonomyError as exc:
        _raise_taxonomy_http_error(exc)
    return _household_item_mutation_out(result)


@router.post(
    "/household-items/{source_item_id}/merge/undo",
    response_model=HouseholdItemMergeOut,
)
def undo_household_item_merge(
    source_item_id: Annotated[int, Path(ge=1)],
    payload: HouseholdItemMergeUndo,
    db: DbSession,
    _owner: CurrentWorkspaceOwner,
) -> HouseholdItemMergeOut:
    try:
        result = HouseholdItemMergeService(db).undo(
            source_item_id,
            merge_event_id=payload.merge_event_id,
        )
    except ClassificationTaxonomyError as exc:
        _raise_taxonomy_http_error(exc)
    return _household_item_mutation_out(result)


@router.patch(
    "/receipt-lines/{line_id}",
    response_model=ClassificationCorrectionOut,
)
def correct_receipt_line_classification(
    line_id: int,
    payload: ClassificationCorrection,
    db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> ClassificationCorrectionOut:
    try:
        application = AutonomousClassificationService(db).correct_receipt_line(
            line_id,
            **payload.model_dump(),
        )
    except ClassificationTaxonomyError as exc:
        _raise_taxonomy_http_error(exc)
    return ClassificationCorrectionOut(
        applied=application.applied,
        reason=application.reason,
        version=application.version,
    )


@router.patch(
    "/transactions/{transaction_id}",
    response_model=ClassificationCorrectionOut,
)
def correct_transaction_classification(
    transaction_id: int,
    payload: ClassificationCorrection,
    db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> ClassificationCorrectionOut:
    try:
        application = AutonomousClassificationService(db).correct_transaction(
            transaction_id,
            **payload.model_dump(),
        )
    except ClassificationTaxonomyError as exc:
        _raise_taxonomy_http_error(exc)
    return ClassificationCorrectionOut(
        applied=application.applied,
        reason=application.reason,
        version=application.version,
    )

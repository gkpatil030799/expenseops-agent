from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import (
    ClassificationActivityType,
    ClassificationAuthority,
    ClassificationConfidenceBand,
    ClassificationDecisionState,
    HouseholdCadenceSource,
    ReplenishmentEligibility,
    SpendingParentCategory,
)

MAX_CLASSIFICATION_ACTIVITY_RANGE_DAYS = 90

ClassificationActivityView = Literal[
    "summary",
    "categories",
    "new_categories",
    "matches",
    "staples",
    "cadence",
    "uncertain",
]
ClassificationActivitySection = Literal[
    "transactions",
    "receipt_items",
    "categories",
    "new_categories",
    "receipt_matches",
    "new_household_items",
    "cadence_updates",
    "uncertain",
]
ClassificationActivityRangeView = Literal[
    "summary",
    "categories",
    "new_categories",
    "matches",
    "staples",
    "staple_candidates",
    "aliases",
    "cadence",
    "uncertain",
]
ClassificationActivityRangeSection = Literal[
    "transactions",
    "receipt_items",
    "categories",
    "new_categories",
    "receipt_matches",
    "new_household_items",
    "staple_candidates",
    "aliases",
    "cadence_updates",
    "uncertain",
]
ClassificationUncertainKind = Literal["transaction", "receipt_item", "receipt_match"]
ClassificationUncertaintyReason = Literal[
    "low_confidence",
    "provisional",
    "other_uncertain",
    "replenishment_uncertain",
    "ambiguous_receipt_match",
    "no_receipt_match",
]


class ClassificationActivityModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
        use_enum_values=True,
    )


class ClassificationTransactionActivity(ClassificationActivityModel):
    decision_public_id: str = Field(min_length=1, max_length=128)
    public_id: str = Field(min_length=1, max_length=128)
    source_available: bool
    version: int = Field(ge=1)
    merchant: str = Field(min_length=1, max_length=255)
    occurred_on: date | None = None
    parent_category: SpendingParentCategory
    subcategory: str | None = Field(default=None, min_length=1, max_length=128)
    concept: str | None = Field(default=None, min_length=1, max_length=255)
    activity_type: ClassificationActivityType
    replenishment_eligibility: ReplenishmentEligibility
    confidence: float = Field(ge=0, le=1)
    confidence_band: ClassificationConfidenceBand
    authority: ClassificationAuthority
    decision_state: ClassificationDecisionState
    provenance_codes: list[str] = Field(min_length=1, max_length=16)
    auto_finalize_at: datetime | None = None
    finalized_at: datetime | None = None
    corrects_decision_public_id: str | None = Field(default=None, min_length=1, max_length=128)
    created_subcategory: bool
    created_concept: bool
    created_household_item: bool
    applied_at: datetime


class ClassificationReceiptItemActivity(ClassificationActivityModel):
    decision_public_id: str = Field(min_length=1, max_length=128)
    public_id: str = Field(min_length=1, max_length=128)
    receipt_public_id: str = Field(min_length=1, max_length=128)
    source_available: bool
    version: int = Field(ge=1)
    merchant: str | None = Field(default=None, min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=500)
    parent_category: SpendingParentCategory
    subcategory: str | None = Field(default=None, min_length=1, max_length=128)
    concept: str | None = Field(default=None, min_length=1, max_length=255)
    activity_type: ClassificationActivityType
    replenishment_eligibility: ReplenishmentEligibility
    confidence: float = Field(ge=0, le=1)
    confidence_band: ClassificationConfidenceBand
    authority: ClassificationAuthority
    decision_state: ClassificationDecisionState
    provenance_codes: list[str] = Field(min_length=1, max_length=16)
    auto_finalize_at: datetime | None = None
    finalized_at: datetime | None = None
    corrects_decision_public_id: str | None = Field(default=None, min_length=1, max_length=128)
    created_subcategory: bool
    created_concept: bool
    created_household_item: bool
    household_item_public_id: str | None = Field(default=None, min_length=1, max_length=128)
    household_item_name: str | None = Field(default=None, min_length=1, max_length=255)
    applied_at: datetime

    @model_validator(mode="after")
    def validate_household_item_link(self) -> ClassificationReceiptItemActivity:
        if (self.household_item_public_id is None) != (self.household_item_name is None):
            raise ValueError("household item identifier and name must appear together")
        return self


class ClassificationCategoryActivity(ClassificationActivityModel):
    parent_category: SpendingParentCategory
    transaction_count: int = Field(ge=0)
    receipt_item_count: int = Field(ge=0)
    total_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_total(self) -> ClassificationCategoryActivity:
        if self.total_count != self.transaction_count + self.receipt_item_count:
            raise ValueError("category count must reconcile")
        return self


class ClassificationNewCategoryActivity(ClassificationActivityModel):
    decision_public_id: str = Field(min_length=1, max_length=128)
    parent_category: SpendingParentCategory
    subcategory: str = Field(min_length=1, max_length=128)
    source_type: Literal["transaction", "receipt_line"]
    authority: ClassificationAuthority
    created_at: datetime


class ClassificationReceiptMatchActivity(ClassificationActivityModel):
    receipt_public_id: str = Field(min_length=1, max_length=128)
    merchant: str | None = Field(default=None, min_length=1, max_length=255)
    status: Literal["auto_matched", "ambiguous", "no_match"]
    confidence: float = Field(ge=0, le=1)
    transaction_public_id: str | None = Field(default=None, min_length=1, max_length=128)
    reason_code: Literal[
        "matched_by_receipt_evidence",
        "multiple_possible_transactions",
        "no_eligible_transaction",
        "linked_transaction_unavailable",
    ]
    attempted_at: datetime
    matched_at: datetime | None = None

    @model_validator(mode="after")
    def validate_match(self) -> ClassificationReceiptMatchActivity:
        if self.status != "auto_matched" and self.transaction_public_id is not None:
            raise ValueError("only an automatic match may expose a transaction identifier")
        if self.status != "auto_matched" and self.matched_at is not None:
            raise ValueError("only an automatic match may have a matched timestamp")
        return self


class ClassificationHouseholdItemActivity(ClassificationActivityModel):
    created_by_decision_public_id: str | None = Field(default=None, min_length=1, max_length=128)
    public_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    parent_category: SpendingParentCategory
    replenishment_eligibility: ReplenishmentEligibility
    classification_confidence: float = Field(ge=0, le=1)
    cadence_source: HouseholdCadenceSource
    cadence_days: int | None = Field(default=None, ge=1, le=3650)
    cadence_min_days: int | None = Field(default=None, ge=1, le=3650)
    cadence_max_days: int | None = Field(default=None, ge=1, le=3650)
    cadence_confidence: float = Field(ge=0, le=1)
    activity_at: datetime

    @model_validator(mode="after")
    def validate_cadence_range(self) -> ClassificationHouseholdItemActivity:
        if (
            self.cadence_min_days is not None
            and self.cadence_max_days is not None
            and self.cadence_min_days > self.cadence_max_days
        ):
            raise ValueError("cadence minimum must not exceed maximum")
        return self


class ClassificationStapleCandidateActivity(ClassificationActivityModel):
    decision_public_id: str = Field(min_length=1, max_length=128)
    receipt_item_public_id: str = Field(min_length=1, max_length=128)
    receipt_public_id: str = Field(min_length=1, max_length=128)
    source_available: bool
    merchant: str | None = Field(default=None, min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=500)
    parent_category: SpendingParentCategory
    subcategory: str | None = Field(default=None, min_length=1, max_length=128)
    concept: str | None = Field(default=None, min_length=1, max_length=255)
    activity_type: ClassificationActivityType
    replenishment_eligibility: Literal["replenishable", "potentially_replenishable"]
    confidence: float = Field(ge=0, le=1)
    confidence_band: ClassificationConfidenceBand
    decision_state: ClassificationDecisionState
    created_household_item: bool
    household_item_public_id: str | None = Field(default=None, min_length=1, max_length=128)
    household_item_name: str | None = Field(default=None, min_length=1, max_length=255)
    learning_state: Literal["candidate", "learning", "tracked"]
    applied_at: datetime

    @model_validator(mode="after")
    def validate_learning_state(self) -> ClassificationStapleCandidateActivity:
        linked = self.household_item_public_id is not None
        if linked != (self.household_item_name is not None):
            raise ValueError("household item identifier and name must appear together")
        if (self.learning_state == "candidate") == linked:
            raise ValueError("only learning or tracked candidates may link a household item")
        return self


class ClassificationAliasActivity(ClassificationActivityModel):
    public_id: str = Field(min_length=1, max_length=128)
    concept: str = Field(min_length=1, max_length=255)
    parent_category: SpendingParentCategory
    raw_pattern: str = Field(min_length=1, max_length=500)
    merchant: str | None = Field(default=None, min_length=1, max_length=255)
    confidence: float = Field(ge=0, le=1)
    authority: ClassificationAuthority
    active: bool
    created_at: datetime


class ClassificationUncertainActivity(ClassificationActivityModel):
    kind: ClassificationUncertainKind
    public_id: str = Field(min_length=1, max_length=128)
    receipt_public_id: str | None = Field(default=None, min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=500)
    reasons: list[ClassificationUncertaintyReason] = Field(min_length=1, max_length=6)
    confidence_band: ClassificationConfidenceBand | None = None
    decision_state: ClassificationDecisionState | None = None
    observed_at: datetime

    @model_validator(mode="after")
    def validate_kind(self) -> ClassificationUncertainActivity:
        if self.kind == "receipt_item" and self.receipt_public_id is None:
            raise ValueError("receipt items require a receipt identifier")
        if self.kind != "receipt_item" and self.receipt_public_id is not None:
            raise ValueError("receipt identifier is only valid for receipt items")
        classification_fields = (self.confidence_band, self.decision_state)
        if self.kind == "receipt_match" and any(
            value is not None for value in classification_fields
        ):
            raise ValueError("receipt matches cannot expose classification state")
        if self.kind != "receipt_match" and any(value is None for value in classification_fields):
            raise ValueError("classification uncertainty requires confidence and decision state")
        return self


class ClassificationActivityCounts(ClassificationActivityModel):
    transactions: int = Field(ge=0)
    receipt_items: int = Field(ge=0)
    categories: int = Field(ge=0)
    new_categories: int = Field(ge=0)
    receipt_matches: int = Field(ge=0)
    new_household_items: int = Field(ge=0)
    cadence_updates: int = Field(ge=0)
    uncertain: int = Field(ge=0)


class ClassificationActivityOut(ClassificationActivityModel):
    schema_version: Literal["1.0"] = "1.0"
    view: ClassificationActivityView
    activity_date: date
    timezone: Literal["UTC"] = "UTC"
    as_of: datetime
    counts: ClassificationActivityCounts
    transactions: list[ClassificationTransactionActivity] = Field(
        default_factory=list, max_length=20
    )
    receipt_items: list[ClassificationReceiptItemActivity] = Field(
        default_factory=list, max_length=20
    )
    categories: list[ClassificationCategoryActivity] = Field(default_factory=list, max_length=20)
    new_categories: list[ClassificationNewCategoryActivity] = Field(
        default_factory=list, max_length=20
    )
    receipt_matches: list[ClassificationReceiptMatchActivity] = Field(
        default_factory=list, max_length=20
    )
    new_household_items: list[ClassificationHouseholdItemActivity] = Field(
        default_factory=list, max_length=20
    )
    cadence_updates: list[ClassificationHouseholdItemActivity] = Field(
        default_factory=list, max_length=20
    )
    uncertain: list[ClassificationUncertainActivity] = Field(default_factory=list, max_length=20)
    truncated_sections: list[ClassificationActivitySection] = Field(
        default_factory=list, max_length=8
    )


class ClassificationActivityRangeCounts(ClassificationActivityModel):
    transactions: int = Field(ge=0)
    receipt_items: int = Field(ge=0)
    categories: int = Field(ge=0)
    new_categories: int = Field(ge=0)
    receipt_matches: int = Field(ge=0)
    new_household_items: int = Field(ge=0)
    staple_candidates: int = Field(ge=0)
    aliases: int = Field(ge=0)
    cadence_updates: int = Field(ge=0)
    uncertain: int = Field(ge=0)


class ClassificationActivityRangeOut(ClassificationActivityModel):
    """Agent-only bounded local-date retrospective; public v1.0 stays unchanged."""

    schema_version: Literal["1.1"] = "1.1"
    view: ClassificationActivityRangeView
    start_date: date
    end_date: date
    timezone: str = Field(min_length=1, max_length=64)
    as_of: datetime
    counts: ClassificationActivityRangeCounts
    transactions: list[ClassificationTransactionActivity] = Field(
        default_factory=list, max_length=20
    )
    receipt_items: list[ClassificationReceiptItemActivity] = Field(
        default_factory=list, max_length=20
    )
    categories: list[ClassificationCategoryActivity] = Field(default_factory=list, max_length=20)
    new_categories: list[ClassificationNewCategoryActivity] = Field(
        default_factory=list, max_length=20
    )
    receipt_matches: list[ClassificationReceiptMatchActivity] = Field(
        default_factory=list, max_length=20
    )
    new_household_items: list[ClassificationHouseholdItemActivity] = Field(
        default_factory=list, max_length=20
    )
    staple_candidates: list[ClassificationStapleCandidateActivity] = Field(
        default_factory=list, max_length=20
    )
    aliases: list[ClassificationAliasActivity] = Field(default_factory=list, max_length=20)
    cadence_updates: list[ClassificationHouseholdItemActivity] = Field(
        default_factory=list, max_length=20
    )
    uncertain: list[ClassificationUncertainActivity] = Field(default_factory=list, max_length=20)
    truncated_sections: list[ClassificationActivityRangeSection] = Field(
        default_factory=list, max_length=10
    )

    @model_validator(mode="after")
    def validate_range_and_view(self) -> ClassificationActivityRangeOut:
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        if (self.end_date - self.start_date).days >= MAX_CLASSIFICATION_ACTIVITY_RANGE_DAYS:
            raise ValueError(
                "classification activity range cannot exceed "
                f"{MAX_CLASSIFICATION_ACTIVITY_RANGE_DAYS} days"
            )
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc

        rows = {
            "transactions": self.transactions,
            "receipt_items": self.receipt_items,
            "categories": self.categories,
            "new_categories": self.new_categories,
            "receipt_matches": self.receipt_matches,
            "new_household_items": self.new_household_items,
            "staple_candidates": self.staple_candidates,
            "aliases": self.aliases,
            "cadence_updates": self.cadence_updates,
            "uncertain": self.uncertain,
        }
        allowed = (
            set(rows)
            if self.view == "summary"
            else {
                "categories": {"categories"},
                "new_categories": {"new_categories"},
                "matches": {"receipt_matches"},
                "staples": {"new_household_items"},
                "staple_candidates": {"staple_candidates"},
                "aliases": {"aliases"},
                "cadence": {"cadence_updates"},
                "uncertain": {"uncertain"},
            }[self.view]
        )
        if any(rows[name] for name in set(rows) - allowed):
            raise ValueError("classification activity view contains unrelated rows")
        counts = self.counts.model_dump()
        if any(len(values) > counts[name] for name, values in rows.items()):
            raise ValueError("classification activity rows cannot exceed total counts")
        expected_truncated = {name for name in allowed if counts[name] > len(rows[name])}
        if set(self.truncated_sections) != expected_truncated:
            raise ValueError("classification activity truncation must match visible counts")
        if len(set(self.truncated_sections)) != len(self.truncated_sections):
            raise ValueError("classification activity truncated sections must be unique")
        return self

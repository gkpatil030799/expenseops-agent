from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.config import get_settings as get_app_settings
from app.models import (
    AuditEvent,
    ClassificationActivityType,
    ClassificationAuthority,
    ClassificationConcept,
    ClassificationConceptAlias,
    ClassificationConfidenceBand,
    ClassificationDecisionRecord,
    ClassificationDecisionState,
    ClassificationSettings,
    ClassificationSubcategory,
    ExpenseTransaction,
    HouseholdCadenceSource,
    HouseholdItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReceiptLineClassification,
    ReplenishmentEligibility,
    SpendingParentCategory,
    utc_now,
)
from app.services.item_normalization_service import normalize_merchant

HIGH_CONFIDENCE_THRESHOLD = 0.85
MEDIUM_CONFIDENCE_THRESHOLD = 0.65
MAX_PROVENANCE_CODES = 16
MAX_CONCEPT_MUTATION_PROJECTIONS = 250
MAX_CONCEPT_MUTATION_ALIASES = 250
MAX_CONCEPT_LIST_RESULTS = 200
MAX_SUBCATEGORY_LIST_RESULTS = 200
MAX_SUBCATEGORY_MUTATION_CONCEPTS = 250

PARENT_CATEGORY_LABELS: dict[SpendingParentCategory, str] = {
    SpendingParentCategory.FOOD_DINING: "Food & Dining",
    SpendingParentCategory.HOUSEHOLD_HOME: "Household & Home",
    SpendingParentCategory.LIFESTYLE_SHOPPING: "Lifestyle & Shopping",
    SpendingParentCategory.PERSONAL_CARE: "Personal Care",
    SpendingParentCategory.HEALTH: "Health",
    SpendingParentCategory.TRANSPORTATION: "Transportation",
    SpendingParentCategory.TRAVEL: "Travel",
    SpendingParentCategory.ENTERTAINMENT: "Entertainment",
    SpendingParentCategory.SUBSCRIPTIONS: "Subscriptions",
    SpendingParentCategory.PETS: "Pets",
    SpendingParentCategory.EDUCATION_OFFICE: "Education / Office",
    SpendingParentCategory.SERVICES: "Services",
    SpendingParentCategory.FEES_TAXES_DISCOUNTS: "Fees / Taxes / Discounts",
    SpendingParentCategory.OTHER_UNCERTAIN: "Other / Uncertain",
}


class ClassificationSourceType(StrEnum):
    RECEIPT_LINE = "receipt_line"
    TRANSACTION = "transaction"


class ClassificationTaxonomyError(ValueError):
    pass


class ClassificationCollisionError(ClassificationTaxonomyError):
    pass


@dataclass(frozen=True)
class ClassificationDecision:
    """Code-owned semantic decision shared by receipt and transaction projections.

    The contract contains no workspace, SQL, provider payload, URL, or action field.
    A service-scoped workspace and an allowlisted source type select the target row.
    """

    source_type: ClassificationSourceType
    source_entity_id: int
    spending_parent_category: SpendingParentCategory
    item_activity_type: ClassificationActivityType
    replenishment_eligibility: ReplenishmentEligibility
    confidence: float
    authority: ClassificationAuthority
    provenance_codes: tuple[str, ...]
    decision_state: ClassificationDecisionState
    subcategory_name: str | None = None
    canonical_concept: str | None = None
    auto_finalize_at: datetime | None = None

    def __post_init__(self) -> None:
        enum_fields = {
            "source_type": ClassificationSourceType,
            "spending_parent_category": SpendingParentCategory,
            "item_activity_type": ClassificationActivityType,
            "replenishment_eligibility": ReplenishmentEligibility,
            "authority": ClassificationAuthority,
            "decision_state": ClassificationDecisionState,
        }
        for field_name, enum_type in enum_fields.items():
            try:
                object.__setattr__(self, field_name, enum_type(getattr(self, field_name)))
            except ValueError as exc:
                raise ClassificationTaxonomyError(f"invalid {field_name}") from exc
        if self.source_entity_id < 1:
            raise ClassificationTaxonomyError("source entity id must be positive")
        if not math.isfinite(float(self.confidence)) or not 0 <= float(self.confidence) <= 1:
            raise ClassificationTaxonomyError("classification confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", float(self.confidence))
        codes = tuple(dict.fromkeys(self.provenance_codes))
        if not codes or len(codes) > MAX_PROVENANCE_CODES:
            raise ClassificationTaxonomyError("classification provenance is invalid")
        if any(not re.fullmatch(r"[a-z0-9_]{1,64}", code) for code in codes):
            raise ClassificationTaxonomyError("classification provenance code is invalid")
        object.__setattr__(self, "provenance_codes", codes)
        for field_name in ("subcategory_name", "canonical_concept"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _safe_dynamic_label(value, maximum=255))
        is_safe_generic_non_product = bool(
            self.item_activity_type is ClassificationActivityType.NON_PRODUCT
            and self.replenishment_eligibility is ReplenishmentEligibility.NOT_REPLENISHABLE
            and self.subcategory_name is None
            and self.canonical_concept is None
        )
        if (
            self.spending_parent_category is SpendingParentCategory.OTHER_UNCERTAIN
            and not is_safe_generic_non_product
            and (
                self.item_activity_type is not ClassificationActivityType.UNCERTAIN
                or self.replenishment_eligibility is not ReplenishmentEligibility.UNCERTAIN
                or self.subcategory_name is not None
                or self.canonical_concept is not None
            )
        ):
            raise ClassificationTaxonomyError(
                "Other / Uncertain cannot carry specific semantic dimensions"
            )
        if self.replenishment_eligibility is ReplenishmentEligibility.REPLENISHABLE and (
            self.item_activity_type not in _PRODUCT_ACTIVITY_TYPES
            or self.spending_parent_category in _NON_PRODUCT_PARENT_CATEGORIES
        ):
            raise ClassificationTaxonomyError(
                "non-product activity cannot be classified as replenishable"
            )
        if (
            self.authority is not ClassificationAuthority.USER_CORRECTION
            and self.confidence < MEDIUM_CONFIDENCE_THRESHOLD
            and (
                self.spending_parent_category is not SpendingParentCategory.OTHER_UNCERTAIN
                or self.item_activity_type is not ClassificationActivityType.UNCERTAIN
                or self.replenishment_eligibility is not ReplenishmentEligibility.UNCERTAIN
                or self.subcategory_name is not None
                or self.canonical_concept is not None
            )
        ):
            raise ClassificationTaxonomyError("low-confidence decisions must use Other / Uncertain")

    @property
    def confidence_band(self) -> ClassificationConfidenceBand:
        return confidence_band(self.confidence)


@dataclass(frozen=True)
class ClassificationApplication:
    applied: bool
    reason: str
    decision: ClassificationDecision
    subcategory_id: int | None = None
    concept_id: int | None = None
    household_item_id: int | None = None
    decision_record_id: int | None = None
    version: int | None = None
    created_subcategory: bool = False
    created_concept: bool = False
    created_alias: bool = False
    created_household_item: bool = False


@dataclass(frozen=True)
class ResolvedClassificationAlias:
    """A tenant-scoped alias match without upgrading its evidence authority."""

    alias_id: int
    concept: ClassificationConcept
    stored_authority: ClassificationAuthority
    confidence: float
    merchant_normalized: str

    @property
    def is_confirmed(self) -> bool:
        return self.stored_authority in {
            ClassificationAuthority.CONFIRMED_ALIAS,
            ClassificationAuthority.USER_CORRECTION,
        }

    @property
    def decision_authority(self) -> ClassificationAuthority:
        if self.is_confirmed:
            return ClassificationAuthority.CONFIRMED_ALIAS
        return self.stored_authority


@dataclass(frozen=True)
class ClassificationConceptSummary:
    id: int
    name: str
    parent_category: SpendingParentCategory
    subcategory_id: int | None
    subcategory_name: str | None
    item_activity_type: ClassificationActivityType
    replenishment_eligibility: ReplenishmentEligibility
    linked_household_item_count: int


@dataclass(frozen=True)
class ClassificationConceptMutation:
    applied: bool
    source_concept_id: int
    target_concept_id: int
    target_name: str
    aliases_moved: int
    receipt_items_updated: int
    transactions_updated: int


@dataclass(frozen=True)
class ClassificationSubcategorySummary:
    id: int
    name: str
    parent_category: SpendingParentCategory
    concept_count: int


@dataclass(frozen=True)
class ClassificationSubcategoryMutation:
    applied: bool
    source_subcategory_id: int
    target_subcategory_id: int
    target_name: str
    concepts_updated: int
    receipt_items_updated: int
    transactions_updated: int


@dataclass(frozen=True)
class _TaxonomyRule:
    pattern: re.Pattern[str]
    parent: SpendingParentCategory
    subcategory: str
    activity: ClassificationActivityType
    replenishment: ReplenishmentEligibility
    concept: str | None
    authority: ClassificationAuthority = ClassificationAuthority.DETERMINISTIC_EXACT


def _rule(
    pattern: str,
    parent: SpendingParentCategory,
    subcategory: str,
    activity: ClassificationActivityType,
    replenishment: ReplenishmentEligibility,
    concept: str | None,
    authority: ClassificationAuthority = ClassificationAuthority.DETERMINISTIC_EXACT,
) -> _TaxonomyRule:
    return _TaxonomyRule(
        re.compile(pattern, re.IGNORECASE),
        parent,
        subcategory,
        activity,
        replenishment,
        concept,
        authority,
    )


_RULES: tuple[_TaxonomyRule, ...] = (
    _rule(
        r"\b(sales tax|tax)\b",
        SpendingParentCategory.FEES_TAXES_DISCOUNTS,
        "Taxes",
        ClassificationActivityType.TAX,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        "Sales tax",
    ),
    _rule(
        r"\b(tip|gratuity)\b",
        SpendingParentCategory.FEES_TAXES_DISCOUNTS,
        "Tips",
        ClassificationActivityType.TIP,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        "Tip",
    ),
    _rule(
        r"\b(coupon|discount|savings)\b",
        SpendingParentCategory.FEES_TAXES_DISCOUNTS,
        "Discounts",
        ClassificationActivityType.DISCOUNT,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        "Discount",
    ),
    _rule(
        r"\b(service fee|delivery fee|processing fee|fee)\b",
        SpendingParentCategory.FEES_TAXES_DISCOUNTS,
        "Fees",
        ClassificationActivityType.FEE,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        "Fee",
    ),
    _rule(
        r"\b(refund|return credit)\b",
        SpendingParentCategory.FEES_TAXES_DISCOUNTS,
        "Refunds",
        ClassificationActivityType.REFUND,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        "Refund",
    ),
    _rule(
        r"\b(dishwasher (tablet|tablets|pod|pods))\b",
        SpendingParentCategory.HOUSEHOLD_HOME,
        "Dishwashing supplies",
        ClassificationActivityType.HOUSEHOLD_CONSUMABLE,
        ReplenishmentEligibility.REPLENISHABLE,
        "Dishwasher tablets",
    ),
    _rule(
        r"\b(dish soap|dishwashing liquid)\b",
        SpendingParentCategory.HOUSEHOLD_HOME,
        "Dishwashing supplies",
        ClassificationActivityType.HOUSEHOLD_CONSUMABLE,
        ReplenishmentEligibility.REPLENISHABLE,
        "Dish soap",
    ),
    _rule(
        r"\b(laundry detergent|tide pods?|laundry pods?)\b",
        SpendingParentCategory.HOUSEHOLD_HOME,
        "Laundry supplies",
        ClassificationActivityType.HOUSEHOLD_CONSUMABLE,
        ReplenishmentEligibility.REPLENISHABLE,
        "Laundry detergent",
    ),
    _rule(
        r"\b(paper towels?|ppr towels?)\b",
        SpendingParentCategory.HOUSEHOLD_HOME,
        "Paper goods",
        ClassificationActivityType.HOUSEHOLD_CONSUMABLE,
        ReplenishmentEligibility.REPLENISHABLE,
        "Paper towels",
    ),
    _rule(
        r"\b(toilet paper|bath tissue|tlt ppr)\b",
        SpendingParentCategory.HOUSEHOLD_HOME,
        "Paper goods",
        ClassificationActivityType.HOUSEHOLD_CONSUMABLE,
        ReplenishmentEligibility.REPLENISHABLE,
        "Toilet paper",
    ),
    _rule(
        r"\b(trash bags?|garbage bags?|freezer bags?|storage bags?)\b",
        SpendingParentCategory.HOUSEHOLD_HOME,
        "Food storage and waste bags",
        ClassificationActivityType.HOUSEHOLD_CONSUMABLE,
        ReplenishmentEligibility.REPLENISHABLE,
        "Household bags",
    ),
    _rule(
        r"\b(pet food|dog food|cat food|pet supply|pet supplies)\b",
        SpendingParentCategory.PETS,
        "Pet supplies",
        ClassificationActivityType.PET_SUPPLY,
        ReplenishmentEligibility.POTENTIALLY_REPLENISHABLE,
        None,
    ),
    _rule(
        r"\b(eggs?|milk|bread|rice|vegetables?|produce|chicken|beef|meat)\b",
        SpendingParentCategory.FOOD_DINING,
        "Groceries",
        ClassificationActivityType.GROCERY,
        ReplenishmentEligibility.REPLENISHABLE,
        None,
    ),
    _rule(
        r"\b(coffee maker|espresso machine|milk frother)\b",
        SpendingParentCategory.LIFESTYLE_SHOPPING,
        "Kitchen appliances",
        ClassificationActivityType.ONE_TIME_PURCHASE,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        "Coffee appliance",
    ),
    _rule(
        r"\b(starbucks|coffee|latte|cappuccino|espresso|cafe)\b",
        SpendingParentCategory.FOOD_DINING,
        "Coffee",
        ClassificationActivityType.COFFEE_BEVERAGE,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        "Coffee beverage",
    ),
    _rule(
        r"\b(doordash|uber eats|ubereats|grubhub|food delivery)\b",
        SpendingParentCategory.FOOD_DINING,
        "Food delivery",
        ClassificationActivityType.FOOD_DELIVERY,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        "Food delivery",
    ),
    _rule(
        r"\b(pub|nightlife|night ?club|cocktail lounge|sports bar|wine bar|bar (?:and|&) grill)\b",
        SpendingParentCategory.FOOD_DINING,
        "Bars and nightlife",
        ClassificationActivityType.NIGHTLIFE,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        "Nightlife",
    ),
    _rule(
        r"\b(restaurant|chipotle|entree|entr[eé]e|biryani|tikka|burger|pizza)\b",
        SpendingParentCategory.FOOD_DINING,
        "Restaurants",
        ClassificationActivityType.RESTAURANT_MEAL,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        "Restaurant meal",
    ),
    _rule(
        r"\b(trader joe'?s|safeway|aldi|supermarket|grocery store)\b",
        SpendingParentCategory.FOOD_DINING,
        "Groceries",
        ClassificationActivityType.GROCERY,
        ReplenishmentEligibility.UNCERTAIN,
        None,
        ClassificationAuthority.PROVIDER_EVIDENCE,
    ),
    _rule(
        r"\b(shampoo|conditioner|toothpaste|toothbrush|deodorant)\b",
        SpendingParentCategory.PERSONAL_CARE,
        "Personal care supplies",
        ClassificationActivityType.PERSONAL_CARE,
        ReplenishmentEligibility.POTENTIALLY_REPLENISHABLE,
        None,
    ),
    _rule(
        r"\b(makeup|cosmetic|skincare|beauty product)\b",
        SpendingParentCategory.PERSONAL_CARE,
        "Beauty",
        ClassificationActivityType.BEAUTY,
        ReplenishmentEligibility.POTENTIALLY_REPLENISHABLE,
        None,
    ),
    _rule(
        r"\b(pharmacy|medication|medicine|prescription|vitamin)\b",
        SpendingParentCategory.HEALTH,
        "Pharmacy",
        ClassificationActivityType.PHARMACY,
        ReplenishmentEligibility.POTENTIALLY_REPLENISHABLE,
        None,
    ),
    _rule(
        r"\b(office supplies?|printer paper|notebook|pens?|pencils?)\b",
        SpendingParentCategory.EDUCATION_OFFICE,
        "Office supplies",
        ClassificationActivityType.EDUCATION_OFFICE,
        ReplenishmentEligibility.POTENTIALLY_REPLENISHABLE,
        None,
    ),
    _rule(
        r"\b(t-?shirt|shirt|jeans|jacket|sweater|dress|shoes|apparel|clothing)\b",
        SpendingParentCategory.LIFESTYLE_SHOPPING,
        "Apparel",
        ClassificationActivityType.APPAREL,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        None,
    ),
    _rule(
        r"\b(television|tv|laptop|computer|electronics?|headphones?)\b",
        SpendingParentCategory.LIFESTYLE_SHOPPING,
        "Electronics",
        ClassificationActivityType.ELECTRONICS,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        None,
    ),
    _rule(
        r"\b(shell|chevron|exxon|gas station|gasoline|fuel)\b",
        SpendingParentCategory.TRANSPORTATION,
        "Gas",
        ClassificationActivityType.AUTOMOTIVE,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        None,
    ),
    _rule(
        r"\b(uber|lyft|taxi|rideshare)\b",
        SpendingParentCategory.TRANSPORTATION,
        "Rideshare",
        ClassificationActivityType.TRANSPORTATION,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        None,
    ),
    _rule(
        r"\b(netflix|spotify|hulu|subscription|streaming)\b",
        SpendingParentCategory.SUBSCRIPTIONS,
        "Entertainment subscriptions",
        ClassificationActivityType.SUBSCRIPTION,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        None,
    ),
    _rule(
        r"\b(airline|flight|hotel|lodging)\b",
        SpendingParentCategory.TRAVEL,
        "Travel",
        ClassificationActivityType.TRAVEL,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        None,
    ),
    _rule(
        r"\b(utility|electric bill|water bill|internet bill|phone bill)\b",
        SpendingParentCategory.HOUSEHOLD_HOME,
        "Utilities",
        ClassificationActivityType.SERVICE,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        None,
    ),
    _rule(
        r"\b(repair service|cleaning service|professional service)\b",
        SpendingParentCategory.SERVICES,
        "Services",
        ClassificationActivityType.SERVICE,
        ReplenishmentEligibility.NOT_REPLENISHABLE,
        None,
    ),
    _rule(
        r"\b(costco|target|walmart|amazon|general merchandise|superstore)\b",
        SpendingParentCategory.LIFESTYLE_SHOPPING,
        "Mixed retail",
        ClassificationActivityType.UNCERTAIN,
        ReplenishmentEligibility.UNCERTAIN,
        None,
        ClassificationAuthority.PROVIDER_EVIDENCE,
    ),
)

_PRODUCT_ACTIVITY_TYPES = {
    ClassificationActivityType.GROCERY,
    ClassificationActivityType.HOUSEHOLD_CONSUMABLE,
    ClassificationActivityType.PHARMACY,
    ClassificationActivityType.PERSONAL_CARE,
    ClassificationActivityType.BEAUTY,
    ClassificationActivityType.PET_SUPPLY,
    ClassificationActivityType.EDUCATION_OFFICE,
}
_NON_PRODUCT_PARENT_CATEGORIES = {
    SpendingParentCategory.TRANSPORTATION,
    SpendingParentCategory.TRAVEL,
    SpendingParentCategory.ENTERTAINMENT,
    SpendingParentCategory.SUBSCRIPTIONS,
    SpendingParentCategory.SERVICES,
    SpendingParentCategory.FEES_TAXES_DISCOUNTS,
    SpendingParentCategory.OTHER_UNCERTAIN,
}


def _legacy_receipt_classification(
    decision: ClassificationDecision,
) -> ReceiptLineClassification:
    """Keep the pre-Day16 receipt projection coherent for remaining consumers."""

    activity = decision.item_activity_type
    if activity is ClassificationActivityType.HOUSEHOLD_CONSUMABLE:
        return ReceiptLineClassification.REPLENISHABLE_HOUSEHOLD
    if activity is ClassificationActivityType.GROCERY:
        return ReceiptLineClassification.PERISHABLE_GROCERY
    if activity is ClassificationActivityType.ROUTINE_CONSUMPTION:
        return ReceiptLineClassification.ROUTINE_CONSUMPTION
    if activity in {
        ClassificationActivityType.RESTAURANT_MEAL,
        ClassificationActivityType.COFFEE_BEVERAGE,
        ClassificationActivityType.FOOD_DELIVERY,
        ClassificationActivityType.NIGHTLIFE,
    }:
        return ReceiptLineClassification.DINING_OR_EXPERIENCE
    if activity in {
        ClassificationActivityType.TAX,
        ClassificationActivityType.TIP,
        ClassificationActivityType.DISCOUNT,
        ClassificationActivityType.FEE,
        ClassificationActivityType.REFUND,
        ClassificationActivityType.NON_PRODUCT,
    }:
        return ReceiptLineClassification.NON_PRODUCT_LINE
    if activity is ClassificationActivityType.UNCERTAIN:
        return ReceiptLineClassification.UNCERTAIN
    return ReceiptLineClassification.ONE_TIME_PURCHASE


_HOSTILE_DATA = re.compile(
    r"\b(system|developer|assistant|ignore (?:the )?(?:user|policy)|auto[ -]?approve|"
    r"auto[ -]?confirm|api key|secret|password|change workspace|post to splitwise)\b",
    re.IGNORECASE,
)
_PACKAGE_POLLUTION = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:ct|count|oz|ounce|lb|pound|ml|liter|pack|pk|rolls?)\b",
    re.IGNORECASE,
)
_PRODUCT_ACCESSORY_CONTEXT = re.compile(
    r"\b(holder|dispenser|cooker|maker|frother|thermometer|peeler|toy|costume|rack|"
    r"container)\b",
    re.IGNORECASE,
)
_AMBIGUOUS_GROCERY_CONCEPT_CONTEXT = re.compile(
    r"\b(chocolate|candy|cracker|cake|pudding|crumbs?|broth|stock|soup|"
    r"dog food|cat food|pet food)\b",
    re.IGNORECASE,
)
_PROVENANCE_RANK = {
    ClassificationAuthority.FALLBACK: 0,
    ClassificationAuthority.MODEL_EVIDENCE: 10,
    ClassificationAuthority.PROVIDER_EVIDENCE: 20,
    ClassificationAuthority.RECEIPT_EVIDENCE: 30,
    ClassificationAuthority.DETERMINISTIC_EXACT: 40,
    ClassificationAuthority.CONFIRMED_ALIAS: 50,
    ClassificationAuthority.USER_CORRECTION: 100,
}


def confidence_band(value: float) -> ClassificationConfidenceBand:
    if value >= HIGH_CONFIDENCE_THRESHOLD:
        return ClassificationConfidenceBand.HIGH
    if value >= MEDIUM_CONFIDENCE_THRESHOLD:
        return ClassificationConfidenceBand.MEDIUM
    return ClassificationConfidenceBand.LOW


def build_classification_decision(
    *,
    source_type: ClassificationSourceType,
    source_entity_id: int,
    spending_parent_category: SpendingParentCategory,
    item_activity_type: ClassificationActivityType,
    replenishment_eligibility: ReplenishmentEligibility,
    confidence: float,
    authority: ClassificationAuthority,
    provenance_codes: Iterable[str],
    subcategory_name: str | None = None,
    canonical_concept: str | None = None,
    grace_hours: int = 24,
) -> ClassificationDecision:
    authority = ClassificationAuthority(authority)
    parent = SpendingParentCategory(spending_parent_category)
    confidence_value = float(confidence)
    if not math.isfinite(confidence_value) or not 0 <= confidence_value <= 1:
        raise ClassificationTaxonomyError(
            "classification confidence must be finite and between 0 and 1"
        )
    now = utc_now()
    if (
        authority is not ClassificationAuthority.USER_CORRECTION
        and confidence_band(confidence_value) is ClassificationConfidenceBand.LOW
    ):
        return ClassificationDecision(
            source_type=source_type,
            source_entity_id=source_entity_id,
            spending_parent_category=SpendingParentCategory.OTHER_UNCERTAIN,
            item_activity_type=ClassificationActivityType.UNCERTAIN,
            replenishment_eligibility=ReplenishmentEligibility.UNCERTAIN,
            confidence=confidence_value,
            authority=ClassificationAuthority.FALLBACK,
            provenance_codes=tuple(provenance_codes) + ("low_confidence_fallback",),
            decision_state=ClassificationDecisionState.FINAL,
        )
    is_medium = confidence_band(confidence_value) is ClassificationConfidenceBand.MEDIUM
    return ClassificationDecision(
        source_type=source_type,
        source_entity_id=source_entity_id,
        spending_parent_category=parent,
        subcategory_name=subcategory_name,
        item_activity_type=item_activity_type,
        replenishment_eligibility=replenishment_eligibility,
        canonical_concept=canonical_concept,
        confidence=confidence_value,
        authority=authority,
        provenance_codes=tuple(provenance_codes),
        decision_state=(
            ClassificationDecisionState.CORRECTED
            if authority is ClassificationAuthority.USER_CORRECTION
            else ClassificationDecisionState.PROVISIONAL
            if is_medium
            else ClassificationDecisionState.FINAL
        ),
        auto_finalize_at=now + timedelta(hours=max(1, min(grace_hours, 168)))
        if is_medium
        else None,
    )


def classify_known_text(
    *,
    source_type: ClassificationSourceType,
    source_entity_id: int,
    text: str,
    provider_category: str | None = None,
) -> ClassificationDecision:
    raw_text = (text or "").strip()
    evidence = " ".join(part for part in (raw_text, provider_category) if part).strip()
    if not evidence or _HOSTILE_DATA.search(evidence):
        reason = "hostile_external_data" if evidence else "missing_evidence"
        return build_classification_decision(
            source_type=source_type,
            source_entity_id=source_entity_id,
            spending_parent_category=SpendingParentCategory.OTHER_UNCERTAIN,
            item_activity_type=ClassificationActivityType.UNCERTAIN,
            replenishment_eligibility=ReplenishmentEligibility.UNCERTAIN,
            confidence=0.0,
            authority=ClassificationAuthority.FALLBACK,
            provenance_codes=(reason,),
        )
    for rule in _RULES:
        match = rule.pattern.search(evidence)
        if not match:
            continue
        concept = rule.concept
        if (
            concept is not None
            and rule.replenishment is ReplenishmentEligibility.REPLENISHABLE
            and _PRODUCT_ACCESSORY_CONTEXT.search(evidence)
        ):
            continue
        if concept is None and rule.replenishment is ReplenishmentEligibility.REPLENISHABLE:
            concept = _canonical_grocery_concept(evidence)
        text_is_exact_evidence = bool(raw_text and rule.pattern.search(raw_text))
        authority = (
            rule.authority if text_is_exact_evidence else ClassificationAuthority.PROVIDER_EVIDENCE
        )
        return build_classification_decision(
            source_type=source_type,
            source_entity_id=source_entity_id,
            spending_parent_category=rule.parent,
            subcategory_name=rule.subcategory,
            item_activity_type=rule.activity,
            replenishment_eligibility=rule.replenishment,
            canonical_concept=concept,
            confidence=0.98,
            authority=authority,
            provenance_codes=(
                "deterministic_taxonomy_rule"
                if authority is ClassificationAuthority.DETERMINISTIC_EXACT
                else "bounded_provider_evidence",
            ),
        )
    return build_classification_decision(
        source_type=source_type,
        source_entity_id=source_entity_id,
        spending_parent_category=SpendingParentCategory.OTHER_UNCERTAIN,
        item_activity_type=ClassificationActivityType.UNCERTAIN,
        replenishment_eligibility=ReplenishmentEligibility.UNCERTAIN,
        confidence=0.0,
        authority=ClassificationAuthority.FALLBACK,
        provenance_codes=("no_supported_rule",),
    )


class ClassificationTaxonomyService:
    def __init__(self, db: Session, settings: Settings | None = None):
        self.db = db
        self.app_settings = settings or get_app_settings()
        workspace_id = db.info.get("workspace_id")
        if workspace_id is None and db.get_bind().dialect.name == "sqlite":
            # Legacy local/unit SQLite sessions predate tenant context; production
            # PostgreSQL remains fail-closed when no authenticated workspace exists.
            workspace_id = 1
        if not isinstance(workspace_id, int) or workspace_id < 1:
            raise ClassificationTaxonomyError("classification requires an authenticated workspace")
        self.workspace_id = workspace_id
        self._created_subcategory_ids: set[int] = set()
        self._created_concept_ids: set[int] = set()
        self._created_alias_ids: set[int] = set()
        self._created_household_item_ids: set[int] = set()

    def get_settings(self, *, create: bool = True) -> ClassificationSettings | None:
        """Return the active workspace controls without ever consulting another tenant."""

        settings = self.db.scalar(
            select(ClassificationSettings)
            .where(ClassificationSettings.workspace_id == self.workspace_id)
            .execution_options(populate_existing=True)
        )
        if settings is not None or not create:
            return settings
        settings = ClassificationSettings(
            grace_hours=self.app_settings.autonomous_classification_grace_hours,
        )
        try:
            with self.db.begin_nested():
                self.db.add(settings)
                self.db.flush()
            return settings
        except IntegrityError:
            settings = self.db.scalar(
                select(ClassificationSettings)
                .where(ClassificationSettings.workspace_id == self.workspace_id)
                .execution_options(populate_existing=True)
            )
            if settings is None:
                raise ClassificationCollisionError(
                    "classification settings creation conflicted"
                ) from None
            return settings

    def _lock_taxonomy_namespace(self) -> None:
        """Serialize fuzzy taxonomy resolution within one workspace transaction."""

        settings = self.get_settings()
        if settings is None:
            raise ClassificationTaxonomyError("classification settings are unavailable")
        self.db.scalar(
            select(ClassificationSettings.id)
            .where(ClassificationSettings.workspace_id == self.workspace_id)
            .with_for_update(of=ClassificationSettings)
        )

    def list_active_concepts(
        self,
        *,
        limit: int = 100,
    ) -> tuple[list[ClassificationConceptSummary], bool]:
        """Return a bounded owner-management view of active workspace concepts."""

        if not 1 <= limit <= MAX_CONCEPT_LIST_RESULTS:
            raise ClassificationTaxonomyError("concept list limit is out of range")
        concepts = list(
            self.db.scalars(
                select(ClassificationConcept)
                .where(
                    ClassificationConcept.workspace_id == self.workspace_id,
                    ClassificationConcept.merged_into_id.is_(None),
                )
                .order_by(ClassificationConcept.name, ClassificationConcept.id)
                .limit(limit + 1)
            )
        )
        has_more = len(concepts) > limit
        concepts = concepts[:limit]
        concept_ids = [concept.id for concept in concepts]
        subcategory_ids = {
            concept.subcategory_id for concept in concepts if concept.subcategory_id is not None
        }
        subcategories = (
            {
                value.id: value.name
                for value in self.db.scalars(
                    select(ClassificationSubcategory).where(
                        ClassificationSubcategory.workspace_id == self.workspace_id,
                        ClassificationSubcategory.id.in_(subcategory_ids),
                    )
                )
            }
            if subcategory_ids
            else {}
        )
        household_counts = (
            {
                int(concept_id): int(count)
                for concept_id, count in self.db.execute(
                    select(
                        HouseholdItem.classification_concept_id,
                        func.count(HouseholdItem.id),
                    )
                    .where(
                        HouseholdItem.workspace_id == self.workspace_id,
                        HouseholdItem.classification_concept_id.in_(concept_ids),
                    )
                    .group_by(HouseholdItem.classification_concept_id)
                )
            }
            if concept_ids
            else {}
        )
        return (
            [
                ClassificationConceptSummary(
                    id=concept.id,
                    name=concept.name,
                    parent_category=SpendingParentCategory(concept.parent_category),
                    subcategory_id=concept.subcategory_id,
                    subcategory_name=subcategories.get(concept.subcategory_id),
                    item_activity_type=ClassificationActivityType(concept.item_activity_type),
                    replenishment_eligibility=ReplenishmentEligibility(
                        concept.replenishment_eligibility
                    ),
                    linked_household_item_count=household_counts.get(concept.id, 0),
                )
                for concept in concepts
            ],
            has_more,
        )

    def list_active_subcategories(
        self,
        *,
        limit: int = 100,
    ) -> tuple[list[ClassificationSubcategorySummary], bool]:
        """Return a bounded owner-management view of active workspace subcategories."""

        if not 1 <= limit <= MAX_SUBCATEGORY_LIST_RESULTS:
            raise ClassificationTaxonomyError("subcategory list limit is out of range")
        values = list(
            self.db.scalars(
                select(ClassificationSubcategory)
                .where(
                    ClassificationSubcategory.workspace_id == self.workspace_id,
                    ClassificationSubcategory.merged_into_id.is_(None),
                )
                .order_by(
                    ClassificationSubcategory.parent_category,
                    ClassificationSubcategory.name,
                    ClassificationSubcategory.id,
                )
                .limit(limit + 1)
            )
        )
        has_more = len(values) > limit
        values = values[:limit]
        ids = [value.id for value in values]
        concept_counts = (
            {
                int(subcategory_id): int(count)
                for subcategory_id, count in self.db.execute(
                    select(
                        ClassificationConcept.subcategory_id,
                        func.count(ClassificationConcept.id),
                    )
                    .where(
                        ClassificationConcept.workspace_id == self.workspace_id,
                        ClassificationConcept.subcategory_id.in_(ids),
                        ClassificationConcept.merged_into_id.is_(None),
                    )
                    .group_by(ClassificationConcept.subcategory_id)
                )
            }
            if ids
            else {}
        )
        return (
            [
                ClassificationSubcategorySummary(
                    id=value.id,
                    name=value.name,
                    parent_category=SpendingParentCategory(value.parent_category),
                    concept_count=concept_counts.get(value.id, 0),
                )
                for value in values
            ],
            has_more,
        )

    def rename_subcategory(
        self,
        subcategory_id: int,
        *,
        name: str,
        commit: bool = True,
    ) -> ClassificationSubcategoryMutation:
        if subcategory_id < 1:
            raise ClassificationTaxonomyError("subcategory id must be positive")
        if (
            self.db.scalar(
                select(ClassificationSubcategory.id).where(
                    ClassificationSubcategory.workspace_id == self.workspace_id,
                    ClassificationSubcategory.id == subcategory_id,
                )
            )
            is None
        ):
            raise ClassificationTaxonomyError("classification subcategory not found")
        self._locked_subcategory_projections(subcategory_id)
        self._lock_taxonomy_namespace()
        receipt_items, transactions = self._locked_subcategory_projections(subcategory_id)
        subcategories = self._locked_subcategories((subcategory_id,))
        if len(subcategories) != 1:
            raise ClassificationTaxonomyError("classification subcategory not found")
        source = subcategories[0]
        if source.merged_into_id is not None:
            raise ClassificationCollisionError("an already-merged subcategory cannot be renamed")
        clean = _safe_dynamic_label(name, maximum=128)
        normalized = normalize_taxonomy_name(clean)
        if not normalized:
            raise ClassificationTaxonomyError("subcategory name is empty after normalization")
        collision = self.db.scalar(
            select(ClassificationSubcategory.id).where(
                ClassificationSubcategory.workspace_id == self.workspace_id,
                ClassificationSubcategory.parent_category == source.parent_category,
                ClassificationSubcategory.normalized_name == normalized,
                ClassificationSubcategory.id != source.id,
            )
        )
        if collision is not None:
            raise ClassificationCollisionError(
                "another subcategory already uses that name; merge the subcategories instead"
            )
        concepts = self._locked_subcategory_concepts(source.id)
        if source.name == clean and source.normalized_name == normalized:
            return ClassificationSubcategoryMutation(
                applied=False,
                source_subcategory_id=source.id,
                target_subcategory_id=source.id,
                target_name=source.name,
                concepts_updated=0,
                receipt_items_updated=0,
                transactions_updated=0,
            )
        old_name = source.name
        now = utc_now()
        try:
            with self.db.begin_nested():
                source.name = clean
                source.normalized_name = normalized
                source.source = ClassificationAuthority.USER_CORRECTION.value
                source.confidence = 1.0
                source.updated_at = now
                self._append_subcategory_projection_corrections(
                    source,
                    receipt_items=receipt_items,
                    transactions=transactions,
                    now=now,
                    provenance="subcategory_rename",
                )
                self.db.add(
                    AuditEvent(
                        workspace_id=self.workspace_id,
                        user_id=self.db.info.get("user_id"),
                        event_type="classification_subcategory_renamed",
                        resource_type="classification_subcategory",
                        resource_id=str(source.id),
                        metadata_json={
                            "old_name": old_name,
                            "new_name": clean,
                            "concepts_updated": len(concepts),
                            "receipt_items_updated": len(receipt_items),
                            "transactions_updated": len(transactions),
                        },
                    )
                )
                self.db.flush()
        except IntegrityError:
            raise ClassificationCollisionError(
                "subcategory rename conflicted; retry safely"
            ) from None
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return ClassificationSubcategoryMutation(
            applied=True,
            source_subcategory_id=source.id,
            target_subcategory_id=source.id,
            target_name=source.name,
            concepts_updated=len(concepts),
            receipt_items_updated=len(receipt_items),
            transactions_updated=len(transactions),
        )

    def merge_subcategories(
        self,
        source_subcategory_id: int,
        *,
        target_subcategory_id: int,
        commit: bool = True,
    ) -> ClassificationSubcategoryMutation:
        if source_subcategory_id < 1 or target_subcategory_id < 1:
            raise ClassificationTaxonomyError("subcategory ids must be positive")
        if source_subcategory_id == target_subcategory_id:
            raise ClassificationCollisionError("a subcategory cannot be merged into itself")
        preview = list(
            self.db.scalars(
                select(ClassificationSubcategory).where(
                    ClassificationSubcategory.workspace_id == self.workspace_id,
                    ClassificationSubcategory.id.in_(
                        (source_subcategory_id, target_subcategory_id)
                    ),
                )
            )
        )
        if len(preview) != 2:
            raise ClassificationTaxonomyError("classification subcategory not found")
        self._locked_subcategory_projections(source_subcategory_id)
        self._lock_taxonomy_namespace()
        receipt_items, transactions = self._locked_subcategory_projections(source_subcategory_id)
        values = self._locked_subcategories((source_subcategory_id, target_subcategory_id))
        by_id = {value.id: value for value in values}
        source = by_id.get(source_subcategory_id)
        target = by_id.get(target_subcategory_id)
        if source is None or target is None:
            raise ClassificationTaxonomyError("classification subcategory not found")
        self._assert_subcategory_merge_chain_is_safe(source, target)
        if source.merged_into_id is not None or target.merged_into_id is not None:
            raise ClassificationCollisionError(
                "already-merged subcategories cannot be merged again"
            )
        if source.parent_category != target.parent_category:
            raise ClassificationCollisionError("subcategory parent categories are incompatible")
        concepts = self._locked_subcategory_concepts(source.id)
        now = utc_now()
        try:
            with self.db.begin_nested():
                for concept in concepts:
                    concept.subcategory_id = target.id
                    concept.updated_at = now
                self._append_subcategory_projection_corrections(
                    target,
                    receipt_items=receipt_items,
                    transactions=transactions,
                    now=now,
                    provenance="subcategory_merge",
                )
                source.merged_into_id = target.id
                source.updated_at = now
                self.db.add(
                    AuditEvent(
                        workspace_id=self.workspace_id,
                        user_id=self.db.info.get("user_id"),
                        event_type="classification_subcategory_merged",
                        resource_type="classification_subcategory",
                        resource_id=str(source.id),
                        metadata_json={
                            "source_subcategory_id": source.id,
                            "source_name": source.name,
                            "target_subcategory_id": target.id,
                            "target_name": target.name,
                            "concepts_updated": len(concepts),
                            "receipt_items_updated": len(receipt_items),
                            "transactions_updated": len(transactions),
                        },
                    )
                )
                self.db.flush()
        except IntegrityError:
            raise ClassificationCollisionError(
                "subcategory merge conflicted; retry safely"
            ) from None
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return ClassificationSubcategoryMutation(
            applied=True,
            source_subcategory_id=source.id,
            target_subcategory_id=target.id,
            target_name=target.name,
            concepts_updated=len(concepts),
            receipt_items_updated=len(receipt_items),
            transactions_updated=len(transactions),
        )

    def rename_concept(
        self,
        concept_id: int,
        *,
        name: str,
        commit: bool = True,
    ) -> ClassificationConceptMutation:
        """Rename one concept without renaming or merging HouseholdItem history."""

        if concept_id < 1:
            raise ClassificationTaxonomyError("concept id must be positive")
        preview = self.db.scalar(
            select(ClassificationConcept).where(
                ClassificationConcept.workspace_id == self.workspace_id,
                ClassificationConcept.id == concept_id,
            )
        )
        if preview is None:
            raise ClassificationTaxonomyError("classification concept not found")
        self._locked_concept_projections(concept_id)
        # Classification writes lock their source projection before the workspace
        # taxonomy namespace. Match that order, then rescan while holding the
        # namespace lock so a just-finished classification cannot be missed.
        self._lock_taxonomy_namespace()
        receipt_items, transactions = self._locked_concept_projections(concept_id)
        concepts = self._locked_concepts((concept_id,))
        if len(concepts) != 1:
            raise ClassificationTaxonomyError("classification concept not found")
        concept = concepts[0]
        if concept.merged_into_id is not None:
            raise ClassificationCollisionError("an already-merged concept cannot be renamed")
        clean = _safe_dynamic_label(name, maximum=255)
        normalized = normalize_taxonomy_name(clean)
        if not normalized:
            raise ClassificationTaxonomyError("concept name is empty after normalization")
        collision = self.db.scalar(
            select(ClassificationConcept.id).where(
                ClassificationConcept.workspace_id == self.workspace_id,
                ClassificationConcept.normalized_name == normalized,
                ClassificationConcept.id != concept.id,
            )
        )
        if collision is not None:
            raise ClassificationCollisionError(
                "another concept already uses that name; merge the concepts instead"
            )
        if concept.name == clean and concept.normalized_name == normalized:
            return ClassificationConceptMutation(
                applied=False,
                source_concept_id=concept.id,
                target_concept_id=concept.id,
                target_name=concept.name,
                aliases_moved=0,
                receipt_items_updated=0,
                transactions_updated=0,
            )
        aliases = self._locked_active_aliases((concept.id,))
        if len(aliases) >= MAX_CONCEPT_MUTATION_ALIASES:
            raise ClassificationTaxonomyError("concept has too many aliases for an online rename")
        old_name = concept.name
        old_normalized = concept.normalized_name
        now = utc_now()
        try:
            with self.db.begin_nested():
                concept.name = clean
                concept.normalized_name = normalized
                concept.source = ClassificationAuthority.USER_CORRECTION.value
                concept.confidence = 1.0
                concept.updated_at = now
                alias = self.learn_alias(
                    concept,
                    old_name,
                    merchant=None,
                    confidence=1.0,
                    source=ClassificationAuthority.USER_CORRECTION,
                )
                self._append_concept_projection_corrections(
                    concept,
                    receipt_items=receipt_items,
                    transactions=transactions,
                    now=now,
                    provenance="concept_rename",
                )
                self.db.add(
                    AuditEvent(
                        workspace_id=self.workspace_id,
                        user_id=self.db.info.get("user_id"),
                        event_type="classification_concept_renamed",
                        resource_type="classification_concept",
                        resource_id=str(concept.id),
                        metadata_json={
                            "old_name": old_name,
                            "old_normalized_name": old_normalized,
                            "new_name": clean,
                            "receipt_items_updated": len(receipt_items),
                            "transactions_updated": len(transactions),
                            "household_items_renamed": False,
                            "household_history_changed": False,
                        },
                    )
                )
                self.db.flush()
        except IntegrityError:
            raise ClassificationCollisionError("concept rename conflicted; retry safely") from None
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return ClassificationConceptMutation(
            applied=True,
            source_concept_id=concept.id,
            target_concept_id=concept.id,
            target_name=concept.name,
            aliases_moved=1 if alias.id in self._created_alias_ids else 0,
            receipt_items_updated=len(receipt_items),
            transactions_updated=len(transactions),
        )

    def merge_concepts(
        self,
        source_concept_id: int,
        *,
        target_concept_id: int,
        commit: bool = True,
    ) -> ClassificationConceptMutation:
        """Merge classification semantics only; never combine HouseholdItem history."""

        if source_concept_id < 1 or target_concept_id < 1:
            raise ClassificationTaxonomyError("concept ids must be positive")
        if source_concept_id == target_concept_id:
            raise ClassificationCollisionError("a concept cannot be merged into itself")
        preview = list(
            self.db.scalars(
                select(ClassificationConcept).where(
                    ClassificationConcept.workspace_id == self.workspace_id,
                    ClassificationConcept.id.in_((source_concept_id, target_concept_id)),
                )
            )
        )
        if len(preview) != 2:
            raise ClassificationTaxonomyError("classification concept not found")
        self._locked_concept_projections(source_concept_id)
        self._lock_taxonomy_namespace()
        receipt_items, transactions = self._locked_concept_projections(source_concept_id)
        concepts = self._locked_concepts((source_concept_id, target_concept_id))
        by_id = {concept.id: concept for concept in concepts}
        source = by_id.get(source_concept_id)
        target = by_id.get(target_concept_id)
        if source is None or target is None:
            raise ClassificationTaxonomyError("classification concept not found")
        self._assert_concept_merge_chain_is_safe(source, target)
        if source.merged_into_id is not None or target.merged_into_id is not None:
            raise ClassificationCollisionError("already-merged concepts cannot be merged again")
        if self._concept_dimension_key(source) != self._concept_dimension_key(target):
            raise ClassificationCollisionError(
                "concept dimensions are incompatible; correct them before merging"
            )
        linked_household_items = list(
            self.db.scalars(
                select(HouseholdItem)
                .where(
                    HouseholdItem.workspace_id == self.workspace_id,
                    HouseholdItem.classification_concept_id == source.id,
                )
                .order_by(HouseholdItem.id)
                .limit(MAX_CONCEPT_MUTATION_PROJECTIONS + 1)
                .with_for_update(of=HouseholdItem)
            )
        )
        if len(linked_household_items) > MAX_CONCEPT_MUTATION_PROJECTIONS:
            raise ClassificationTaxonomyError(
                "concept has too many household items for an online merge"
            )
        aliases = self._locked_active_aliases((source.id, target.id))
        if len(aliases) > MAX_CONCEPT_MUTATION_ALIASES:
            raise ClassificationTaxonomyError("concepts have too many aliases for an online merge")
        seen_aliases: dict[tuple[str, str], int] = {}
        for alias in aliases:
            key = (alias.merchant_normalized, alias.normalized_alias)
            existing_concept_id = seen_aliases.setdefault(key, alias.concept_id)
            if existing_concept_id != alias.concept_id:
                raise ClassificationCollisionError("concept aliases conflict and cannot be merged")
        now = utc_now()
        moved_aliases = [alias for alias in aliases if alias.concept_id == source.id]
        try:
            with self.db.begin_nested():
                for alias in moved_aliases:
                    alias.concept_id = target.id
                for household_item in linked_household_items:
                    household_item.classification_concept_id = target.id
                    household_item.spending_parent_category = target.parent_category
                    household_item.replenishment_eligibility = target.replenishment_eligibility
                    household_item.classification_confidence = 1.0
                    household_item.classification_provenance_json = ["concept_merge"]
                    household_item.updated_at = now
                self._append_concept_projection_corrections(
                    target,
                    receipt_items=receipt_items,
                    transactions=transactions,
                    now=now,
                    provenance="concept_merge",
                )
                source.merged_into_id = target.id
                source.updated_at = now
                self.db.add(
                    AuditEvent(
                        workspace_id=self.workspace_id,
                        user_id=self.db.info.get("user_id"),
                        event_type="classification_concept_merged",
                        resource_type="classification_concept",
                        resource_id=str(source.id),
                        metadata_json={
                            "source_concept_id": source.id,
                            "source_name": source.name,
                            "target_concept_id": target.id,
                            "target_name": target.name,
                            "aliases_moved": len(moved_aliases),
                            "receipt_items_updated": len(receipt_items),
                            "transactions_updated": len(transactions),
                            "household_items_retargeted": len(linked_household_items),
                            "household_items_merged": False,
                            "household_history_changed": False,
                        },
                    )
                )
                self.db.flush()
        except IntegrityError:
            raise ClassificationCollisionError("concept merge conflicted; retry safely") from None
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return ClassificationConceptMutation(
            applied=True,
            source_concept_id=source.id,
            target_concept_id=target.id,
            target_name=target.name,
            aliases_moved=len(moved_aliases),
            receipt_items_updated=len(receipt_items),
            transactions_updated=len(transactions),
        )

    def ensure_subcategory(
        self,
        *,
        parent: SpendingParentCategory,
        name: str,
        confidence: float,
        source: ClassificationAuthority,
        allow_create: bool = True,
    ) -> ClassificationSubcategory | None:
        self._lock_taxonomy_namespace()
        parent = SpendingParentCategory(parent)
        clean = _safe_dynamic_label(name, maximum=128)
        normalized = normalize_taxonomy_name(clean)
        existing = self._subcategory(parent, normalized)
        if existing is not None:
            return existing
        if (
            not allow_create
            or confidence < HIGH_CONFIDENCE_THRESHOLD
            or parent is SpendingParentCategory.OTHER_UNCERTAIN
        ):
            return None
        value = ClassificationSubcategory(
            parent_category=parent.value,
            name=clean,
            normalized_name=normalized,
            source=ClassificationAuthority(source).value,
            confidence=confidence,
        )
        try:
            with self.db.begin_nested():
                self.db.add(value)
                self.db.flush()
            self._created_subcategory_ids.add(value.id)
            return value
        except IntegrityError:
            existing = self._subcategory(parent, normalized)
            if existing is None:
                raise ClassificationCollisionError("subcategory creation conflicted") from None
            return existing

    def ensure_concept(
        self,
        decision: ClassificationDecision,
        *,
        subcategory: ClassificationSubcategory | None,
        allow_create: bool = True,
    ) -> ClassificationConcept | None:
        if not decision.canonical_concept:
            return None
        self._lock_taxonomy_namespace()
        clean = _safe_dynamic_label(decision.canonical_concept, maximum=255)
        normalized = normalize_taxonomy_name(clean)
        existing = self._concept(normalized)
        if existing is not None:
            return self._resolve_existing_concept_for_decision(
                existing=existing,
                clean=clean,
                normalized=normalized,
                decision=decision,
                subcategory=subcategory,
                allow_create=allow_create,
            )
        if (
            not allow_create
            or decision.confidence_band is not ClassificationConfidenceBand.HIGH
            or decision.spending_parent_category is SpendingParentCategory.OTHER_UNCERTAIN
        ):
            return None
        if subcategory is not None and subcategory.workspace_id != self.workspace_id:
            raise ClassificationTaxonomyError("subcategory is outside the active workspace")
        value = ClassificationConcept(
            subcategory_id=subcategory.id if subcategory else None,
            parent_category=decision.spending_parent_category.value,
            name=clean,
            normalized_name=normalized,
            item_activity_type=decision.item_activity_type.value,
            replenishment_eligibility=decision.replenishment_eligibility.value,
            source=decision.authority.value,
            confidence=decision.confidence,
        )
        try:
            with self.db.begin_nested():
                self.db.add(value)
                self.db.flush()
            self._created_concept_ids.add(value.id)
            return value
        except IntegrityError:
            existing = self._concept(normalized)
            if existing is None:
                raise ClassificationCollisionError("concept creation conflicted") from None
            return self._resolve_existing_concept_for_decision(
                existing=existing,
                clean=clean,
                normalized=normalized,
                decision=decision,
                subcategory=subcategory,
                allow_create=allow_create,
            )

    def _resolve_existing_concept_for_decision(
        self,
        *,
        existing: ClassificationConcept,
        clean: str,
        normalized: str,
        decision: ClassificationDecision,
        subcategory: ClassificationSubcategory | None,
        allow_create: bool,
    ) -> ClassificationConcept:
        if (
            decision.authority is ClassificationAuthority.USER_CORRECTION
            and not self._concept_dimensions_match(existing, decision, subcategory=subcategory)
            and self._concept_has_other_current_support(existing, decision)
        ):
            return self._ensure_dimensioned_corrected_concept(
                clean=clean,
                normalized=normalized,
                decision=decision,
                subcategory=subcategory,
                allow_create=allow_create,
            )
        self._repair_unshared_concept_for_user_correction(
            existing,
            decision,
            subcategory=subcategory,
        )
        self._validate_concept_dimensions(existing, decision, subcategory=subcategory)
        return existing

    @staticmethod
    def _concept_dimensions_match(
        concept: ClassificationConcept,
        decision: ClassificationDecision,
        *,
        subcategory: ClassificationSubcategory | None,
    ) -> bool:
        return bool(
            concept.parent_category == decision.spending_parent_category.value
            and concept.item_activity_type == decision.item_activity_type.value
            and concept.replenishment_eligibility == decision.replenishment_eligibility.value
            and (subcategory is None or concept.subcategory_id == subcategory.id)
        )

    def _concept_has_other_current_support(
        self,
        concept: ClassificationConcept,
        decision: ClassificationDecision,
    ) -> bool:
        receipt_statement = (
            select(PurchaseReceiptItem.id)
            .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
            .where(
                PurchaseReceipt.workspace_id == self.workspace_id,
                PurchaseReceiptItem.classification_concept_id == concept.id,
            )
        )
        if decision.source_type is ClassificationSourceType.RECEIPT_LINE:
            receipt_statement = receipt_statement.where(
                PurchaseReceiptItem.id != decision.source_entity_id
            )
        if self.db.scalar(receipt_statement.limit(1)) is not None:
            return True
        transaction_statement = select(ExpenseTransaction.id).where(
            ExpenseTransaction.workspace_id == self.workspace_id,
            ExpenseTransaction.classification_concept_id == concept.id,
        )
        if decision.source_type is ClassificationSourceType.TRANSACTION:
            transaction_statement = transaction_statement.where(
                ExpenseTransaction.id != decision.source_entity_id
            )
        return self.db.scalar(transaction_statement.limit(1)) is not None

    def _ensure_dimensioned_corrected_concept(
        self,
        *,
        clean: str,
        normalized: str,
        decision: ClassificationDecision,
        subcategory: ClassificationSubcategory | None,
        allow_create: bool,
    ) -> ClassificationConcept:
        if not allow_create:
            raise ClassificationCollisionError(
                "corrected concept dimensions require category creation"
            )
        identity = "|".join(
            (
                normalized,
                decision.spending_parent_category.value,
                decision.item_activity_type.value,
                decision.replenishment_eligibility.value,
                str(subcategory.id if subcategory is not None else 0),
            )
        )
        suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        dimensioned_name = f"{normalized[:237]}--{suffix}"
        existing = self._concept(dimensioned_name)
        if existing is not None:
            self._validate_concept_dimensions(existing, decision, subcategory=subcategory)
            return existing
        value = ClassificationConcept(
            subcategory_id=subcategory.id if subcategory else None,
            parent_category=decision.spending_parent_category.value,
            name=clean,
            normalized_name=dimensioned_name,
            item_activity_type=decision.item_activity_type.value,
            replenishment_eligibility=decision.replenishment_eligibility.value,
            source=ClassificationAuthority.USER_CORRECTION.value,
            confidence=decision.confidence,
        )
        try:
            with self.db.begin_nested():
                self.db.add(value)
                self.db.flush()
            self._created_concept_ids.add(value.id)
            return value
        except IntegrityError:
            existing = self._concept(dimensioned_name)
            if existing is None:
                raise ClassificationCollisionError(
                    "corrected concept creation conflicted"
                ) from None
            self._validate_concept_dimensions(existing, decision, subcategory=subcategory)
            return existing

    def _repair_unshared_concept_for_user_correction(
        self,
        concept: ClassificationConcept,
        decision: ClassificationDecision,
        *,
        subcategory: ClassificationSubcategory | None,
    ) -> None:
        if decision.authority is not ClassificationAuthority.USER_CORRECTION:
            return
        if self._concept_dimensions_match(concept, decision, subcategory=subcategory):
            return
        if self._concept_has_other_current_support(concept, decision):
            raise ClassificationCollisionError(
                "concept dimensions have other supporting classifications"
            )
        concept.parent_category = decision.spending_parent_category.value
        concept.subcategory_id = subcategory.id if subcategory else None
        concept.item_activity_type = decision.item_activity_type.value
        concept.replenishment_eligibility = decision.replenishment_eligibility.value
        concept.source = ClassificationAuthority.USER_CORRECTION.value
        concept.confidence = decision.confidence
        concept.updated_at = utc_now()
        linked_items = list(
            self.db.scalars(
                select(HouseholdItem).where(
                    HouseholdItem.workspace_id == self.workspace_id,
                    HouseholdItem.classification_concept_id == concept.id,
                )
            )
        )
        for item in linked_items:
            item.spending_parent_category = decision.spending_parent_category.value
            item.replenishment_eligibility = decision.replenishment_eligibility.value
            item.classification_confidence = decision.confidence
            item.classification_provenance_json = list(decision.provenance_codes)
            if decision.replenishment_eligibility is not ReplenishmentEligibility.REPLENISHABLE:
                item.enabled = False
            item.updated_at = utc_now()

    def resolve_alias(
        self, raw_pattern: str, *, merchant: str | None = None
    ) -> ClassificationConcept | None:
        resolved = self.resolve_alias_evidence(raw_pattern, merchant=merchant)
        return resolved.concept if resolved is not None else None

    def resolve_alias_evidence(
        self, raw_pattern: str, *, merchant: str | None = None
    ) -> ResolvedClassificationAlias | None:
        """Resolve an alias with its persisted authority and confidence intact."""

        normalized = normalize_taxonomy_name(raw_pattern)
        if not normalized or _HOSTILE_DATA.search(raw_pattern):
            return None
        merchant_key = normalize_merchant(merchant)
        alias = self.db.scalar(
            select(ClassificationConceptAlias)
            .where(
                ClassificationConceptAlias.workspace_id == self.workspace_id,
                ClassificationConceptAlias.normalized_alias == normalized,
                ClassificationConceptAlias.merchant_normalized.in_((merchant_key, "")),
                ClassificationConceptAlias.voided_at.is_(None),
            )
            .order_by(ClassificationConceptAlias.merchant_normalized.desc())
        )
        if alias is None:
            return None
        concept = self.db.scalar(
            select(ClassificationConcept).where(
                ClassificationConcept.workspace_id == self.workspace_id,
                ClassificationConcept.id == alias.concept_id,
            )
        )
        if concept is None:
            raise ClassificationTaxonomyError("concept alias target is unavailable")
        concept = self._resolved_concept(concept)
        try:
            stored_authority = ClassificationAuthority(alias.source)
        except ValueError as exc:
            raise ClassificationTaxonomyError("concept alias authority is invalid") from exc
        return ResolvedClassificationAlias(
            alias_id=alias.id,
            concept=concept,
            stored_authority=stored_authority,
            confidence=float(alias.confidence),
            merchant_normalized=alias.merchant_normalized,
        )

    def learn_alias(
        self,
        concept: ClassificationConcept,
        raw_pattern: str,
        *,
        merchant: str | None,
        confidence: float,
        source: ClassificationAuthority,
        replace_collision: bool = False,
    ) -> ClassificationConceptAlias:
        if concept.workspace_id != self.workspace_id:
            raise ClassificationTaxonomyError("concept is outside the active workspace")
        _safe_label(raw_pattern, maximum=500)
        if _HOSTILE_DATA.search(raw_pattern):
            raise ClassificationTaxonomyError("hostile external data cannot become an alias")
        normalized = normalize_taxonomy_name(raw_pattern)
        if not normalized:
            raise ClassificationTaxonomyError("concept alias is empty")
        merchant_key = normalize_merchant(merchant)
        existing = self.db.scalar(
            select(ClassificationConceptAlias).where(
                ClassificationConceptAlias.workspace_id == self.workspace_id,
                ClassificationConceptAlias.merchant_normalized == merchant_key,
                ClassificationConceptAlias.normalized_alias == normalized,
                ClassificationConceptAlias.voided_at.is_(None),
            )
        )
        if existing is not None:
            if existing.concept_id == concept.id:
                source = ClassificationAuthority(source)
                existing.confidence = max(existing.confidence, confidence)
                existing_authority = ClassificationAuthority(existing.source)
                if _PROVENANCE_RANK[source] > _PROVENANCE_RANK[existing_authority]:
                    existing.source = source.value
                return existing
            if not replace_collision:
                raise ClassificationCollisionError(
                    "concept alias already belongs to another concept"
                )
            existing.voided_at = utc_now()
            self.db.flush()
        value = ClassificationConceptAlias(
            concept_id=concept.id,
            merchant_normalized=merchant_key,
            raw_pattern=raw_pattern.strip(),
            normalized_alias=normalized,
            confidence=min(1.0, max(0.0, confidence)),
            source=ClassificationAuthority(source).value,
        )
        try:
            with self.db.begin_nested():
                self.db.add(value)
                self.db.flush()
            self._created_alias_ids.add(value.id)
            return value
        except IntegrityError:
            existing = self.db.scalar(
                select(ClassificationConceptAlias).where(
                    ClassificationConceptAlias.workspace_id == self.workspace_id,
                    ClassificationConceptAlias.merchant_normalized == merchant_key,
                    ClassificationConceptAlias.normalized_alias == normalized,
                    ClassificationConceptAlias.voided_at.is_(None),
                )
            )
            if existing is None or existing.concept_id != concept.id:
                raise ClassificationCollisionError("concept alias creation conflicted") from None
            incoming_authority = ClassificationAuthority(source)
            existing_authority = ClassificationAuthority(existing.source)
            existing.confidence = max(existing.confidence, confidence)
            if _PROVENANCE_RANK[incoming_authority] > _PROVENANCE_RANK[existing_authority]:
                existing.source = incoming_authority.value
            return existing

    def void_alias(
        self,
        concept_id: int,
        raw_pattern: str,
        *,
        merchant: str | None,
        alias_id: int | None = None,
        allowed_sources: Iterable[ClassificationAuthority] = (
            ClassificationAuthority.FALLBACK,
            ClassificationAuthority.MODEL_EVIDENCE,
            ClassificationAuthority.PROVIDER_EVIDENCE,
            ClassificationAuthority.RECEIPT_EVIDENCE,
            ClassificationAuthority.DETERMINISTIC_EXACT,
        ),
        commit: bool = False,
    ) -> bool:
        """Void only an exact tenant/concept alias with an explicitly safe authority."""

        normalized = normalize_taxonomy_name(raw_pattern)
        if not normalized:
            return False
        allowed = {ClassificationAuthority(source).value for source in allowed_sources}
        if not allowed:
            return False
        concept_exists = self.db.scalar(
            select(ClassificationConcept.id).where(
                ClassificationConcept.workspace_id == self.workspace_id,
                ClassificationConcept.id == concept_id,
            )
        )
        if concept_exists is None:
            raise ClassificationTaxonomyError("concept is outside the active workspace")
        statement = select(ClassificationConceptAlias).where(
            ClassificationConceptAlias.workspace_id == self.workspace_id,
            ClassificationConceptAlias.normalized_alias == normalized,
            ClassificationConceptAlias.source.in_(allowed),
            ClassificationConceptAlias.voided_at.is_(None),
        )
        if alias_id is None:
            statement = statement.where(
                ClassificationConceptAlias.concept_id == concept_id,
                ClassificationConceptAlias.merchant_normalized == normalize_merchant(merchant),
            )
        else:
            statement = statement.where(ClassificationConceptAlias.id == alias_id)
        alias = self.db.scalar(statement)
        if alias is None:
            return False
        alias_concept = self.db.scalar(
            select(ClassificationConcept).where(
                ClassificationConcept.workspace_id == self.workspace_id,
                ClassificationConcept.id == alias.concept_id,
            )
        )
        if alias_concept is None or self._resolved_concept(alias_concept).id != concept_id:
            return False
        alias.voided_at = utc_now()
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return True

    def ensure_household_item(
        self,
        decision: ClassificationDecision,
        concept: ClassificationConcept | None,
    ) -> HouseholdItem | None:
        if (
            concept is None
            or decision.replenishment_eligibility is not ReplenishmentEligibility.REPLENISHABLE
            or decision.confidence_band is not ClassificationConfidenceBand.HIGH
            or decision.decision_state
            not in {
                ClassificationDecisionState.FINAL,
                ClassificationDecisionState.CORRECTED,
            }
        ):
            return None
        existing_items = list(
            self.db.scalars(
                select(HouseholdItem).where(
                    HouseholdItem.workspace_id == self.workspace_id,
                    (HouseholdItem.classification_concept_id == concept.id)
                    | (HouseholdItem.canonical_key == concept.normalized_name),
                )
            )
        )
        existing = next((item for item in existing_items if item.enabled), None)
        active_items = [item for item in existing_items if item.enabled]
        if len(active_items) > 1:
            raise ClassificationCollisionError(
                "concept is linked to multiple active household items; merge them first"
            )
        if existing is not None:
            return existing
        if existing_items:
            # A disabled canonical item is an explicit negative preference. Do not
            # silently recreate or re-enable it from later autonomous evidence.
            if decision.authority is not ClassificationAuthority.USER_CORRECTION:
                return None
            exact = [
                item for item in existing_items if item.classification_concept_id == concept.id
            ]
            candidates = exact or existing_items
            if len(candidates) != 1:
                raise ClassificationCollisionError(
                    "multiple disabled household items match the corrected concept"
                )
            corrected = candidates[0]
            corrected.enabled = True
            corrected.updated_at = utc_now()
            return corrected
        value = HouseholdItem(
            name=concept.name,
            canonical_key=concept.normalized_name,
            classification_concept_id=concept.id,
            spending_parent_category=decision.spending_parent_category.value,
            replenishment_eligibility=decision.replenishment_eligibility.value,
            classification_confidence=decision.confidence,
            classification_provenance_json=list(decision.provenance_codes),
            cadence_days=None,
            cadence_source=HouseholdCadenceSource.LEARNING.value,
            cadence_confidence=0.0,
            cadence_provenance_json={"source": "insufficient"},
            enabled=True,
        )
        try:
            with self.db.begin_nested():
                self.db.add(value)
                self.db.flush()
            self._created_household_item_ids.add(value.id)
            return value
        except IntegrityError:
            existing = self.db.scalar(
                select(HouseholdItem).where(
                    HouseholdItem.workspace_id == self.workspace_id,
                    HouseholdItem.enabled.is_(True),
                    HouseholdItem.canonical_key == concept.normalized_name,
                )
            )
            if existing is None:
                raise ClassificationCollisionError("household item creation conflicted") from None
            return existing

    def _disabled_household_item_exists(
        self,
        *,
        concept_id: int,
        canonical_concept: str | None,
    ) -> bool:
        canonical_key = normalize_taxonomy_name(canonical_concept) if canonical_concept else None
        identity = HouseholdItem.classification_concept_id == concept_id
        if canonical_key:
            identity = identity | (HouseholdItem.canonical_key == canonical_key)
        return bool(
            self.db.scalar(
                select(HouseholdItem.id).where(
                    HouseholdItem.workspace_id == self.workspace_id,
                    HouseholdItem.enabled.is_(False),
                    identity,
                )
            )
        )

    def apply_decision(
        self,
        decision: ClassificationDecision,
        *,
        raw_alias: str | None = None,
        merchant: str | None = None,
        create_household_item: bool = False,
        allow_category_creation: bool = True,
        allow_receipt_evidence_cleanup: bool = False,
        commit: bool = True,
    ) -> ClassificationApplication:
        settings = self.get_settings()
        assert settings is not None
        target = self._target(decision)
        current_authority = ClassificationAuthority(
            getattr(target, "classification_authority", ClassificationAuthority.FALLBACK.value)
            or ClassificationAuthority.FALLBACK.value
        )
        receipt_evidence_cleanup = bool(
            allow_receipt_evidence_cleanup
            and decision.source_type is ClassificationSourceType.TRANSACTION
            and current_authority is ClassificationAuthority.RECEIPT_EVIDENCE
            and decision.authority
            in {
                ClassificationAuthority.DETERMINISTIC_EXACT,
                ClassificationAuthority.PROVIDER_EVIDENCE,
                ClassificationAuthority.FALLBACK,
            }
            and "receipt_evidence_recomputed" in decision.provenance_codes
        )
        if (
            not settings.autonomous_enabled
            and decision.authority is not ClassificationAuthority.USER_CORRECTION
            and not receipt_evidence_cleanup
        ):
            return ClassificationApplication(False, "autonomy_disabled", decision)
        if (
            _PROVENANCE_RANK[current_authority] > _PROVENANCE_RANK[decision.authority]
            and not receipt_evidence_cleanup
        ):
            return ClassificationApplication(False, "higher_authority_decision_preserved", decision)
        latest_record = self.db.scalar(
            select(ClassificationDecisionRecord)
            .where(
                ClassificationDecisionRecord.workspace_id == self.workspace_id,
                ClassificationDecisionRecord.source_type == decision.source_type.value,
                ClassificationDecisionRecord.source_entity_id == decision.source_entity_id,
            )
            .order_by(ClassificationDecisionRecord.version.desc())
        )
        existing_alias = (
            self.resolve_alias_evidence(raw_alias, merchant=merchant) if raw_alias else None
        )
        existing_alias_concept = existing_alias.concept if existing_alias is not None else None
        target_concept_id = getattr(target, "classification_concept_id", None)
        alias_side_effect_needed = bool(
            raw_alias
            and target_concept_id is not None
            and decision.confidence_band is ClassificationConfidenceBand.HIGH
            and (existing_alias_concept is None or existing_alias_concept.id != target_concept_id)
        )
        alias_cleanup_needed = bool(
            decision.authority is ClassificationAuthority.USER_CORRECTION
            and raw_alias
            and existing_alias_concept is not None
            and target_concept_id is None
        )
        household_side_effect_possible = bool(
            create_household_item
            and target_concept_id is not None
            and decision.confidence_band is ClassificationConfidenceBand.HIGH
            and decision.replenishment_eligibility is ReplenishmentEligibility.REPLENISHABLE
            and decision.decision_state
            in (ClassificationDecisionState.FINAL, ClassificationDecisionState.CORRECTED)
            and (
                decision.authority is ClassificationAuthority.USER_CORRECTION
                or not self._disabled_household_item_exists(
                    concept_id=target_concept_id,
                    canonical_concept=decision.canonical_concept,
                )
            )
        )
        household_side_effect_needed = bool(
            household_side_effect_possible
            and (
                latest_record is None
                or latest_record.household_item_id is None
                or (
                    decision.source_type is ClassificationSourceType.RECEIPT_LINE
                    and getattr(target, "household_item_id", None) is None
                )
            )
        )
        if (
            self._projection_matches(target, decision)
            and latest_record is not None
            and not alias_side_effect_needed
            and not alias_cleanup_needed
            and not household_side_effect_needed
        ):
            return ClassificationApplication(
                False,
                "already_applied",
                decision,
                subcategory_id=latest_record.subcategory_id,
                concept_id=latest_record.concept_id,
                household_item_id=latest_record.household_item_id,
                decision_record_id=latest_record.id,
                version=latest_record.version,
            )
        old_projection = {
            "parent": getattr(target, "spending_parent_category", None),
            "subcategory_id": getattr(target, "classification_subcategory_id", None),
            "concept_id": getattr(target, "classification_concept_id", None),
            "state": getattr(target, "classification_decision_state", None),
            "authority": getattr(target, "classification_authority", None),
            "version": int(getattr(target, "classification_version", 1) or 1),
            "legacy_classification": getattr(target, "classification", None),
        }
        created_before = {
            "subcategory": set(self._created_subcategory_ids),
            "concept": set(self._created_concept_ids),
            "alias": set(self._created_alias_ids),
            "household_item": set(self._created_household_item_ids),
        }
        category_creation_allowed = bool(
            decision.authority is ClassificationAuthority.USER_CORRECTION
            or (allow_category_creation and settings.category_creation_enabled)
        )
        subcategory = None
        if decision.subcategory_name:
            subcategory = self.ensure_subcategory(
                parent=decision.spending_parent_category,
                name=decision.subcategory_name,
                confidence=decision.confidence,
                source=decision.authority,
                allow_create=(
                    category_creation_allowed
                    and decision.confidence_band is ClassificationConfidenceBand.HIGH
                ),
            )
        concept = self.ensure_concept(
            decision,
            subcategory=subcategory,
            allow_create=(
                category_creation_allowed
                and decision.confidence_band is ClassificationConfidenceBand.HIGH
            ),
        )
        if (
            decision.authority is ClassificationAuthority.USER_CORRECTION
            and raw_alias
            and existing_alias_concept is not None
            and (concept is None or existing_alias_concept.id != concept.id)
        ):
            self.void_alias(
                existing_alias_concept.id,
                raw_alias,
                merchant=merchant,
                alias_id=existing_alias.alias_id if existing_alias is not None else None,
                allowed_sources=tuple(ClassificationAuthority),
                commit=False,
            )
        old_version = int(getattr(target, "classification_version", 1) or 1)
        previously_applied = getattr(target, "classification_applied_at", None) is not None
        version = old_version + 1 if previously_applied else old_version
        target.spending_parent_category = decision.spending_parent_category.value
        target.classification_subcategory_id = subcategory.id if subcategory else None
        target.classification_subcategory_name = decision.subcategory_name
        target.classification_concept_id = concept.id if concept else None
        target.classification_concept_name = decision.canonical_concept
        activity_field = (
            "item_activity_type"
            if decision.source_type is ClassificationSourceType.RECEIPT_LINE
            else "classification_activity_type"
        )
        setattr(target, activity_field, decision.item_activity_type.value)
        target.replenishment_eligibility = decision.replenishment_eligibility.value
        target.classification_confidence = decision.confidence
        target.classification_confidence_band = decision.confidence_band.value
        target.classification_authority = decision.authority.value
        target.classification_provenance_json = list(decision.provenance_codes)
        target.classification_decision_state = decision.decision_state.value
        target.classification_applied_at = utc_now()
        target.classification_auto_finalize_at = decision.auto_finalize_at
        target.classification_finalized_at = (
            utc_now()
            if decision.decision_state
            in (ClassificationDecisionState.FINAL, ClassificationDecisionState.CORRECTED)
            else None
        )
        target.classification_corrected_at = (
            utc_now()
            if decision.authority is ClassificationAuthority.USER_CORRECTION
            else getattr(target, "classification_corrected_at", None)
        )
        target.classification_corrects_version = (
            old_version if decision.authority is ClassificationAuthority.USER_CORRECTION else None
        )
        target.classification_version = version
        if decision.source_type is ClassificationSourceType.RECEIPT_LINE:
            target.classification = _legacy_receipt_classification(decision).value
            target.canonical_name = decision.canonical_concept
        household_item = None
        alias = None
        if (
            concept is not None
            and raw_alias
            and decision.confidence_band is ClassificationConfidenceBand.HIGH
        ):
            alias = self.learn_alias(
                concept,
                raw_alias,
                merchant=merchant,
                confidence=decision.confidence,
                source=decision.authority,
                replace_collision=decision.authority is ClassificationAuthority.USER_CORRECTION,
            )
        if create_household_item:
            household_item = self.ensure_household_item(decision, concept)
            if (
                household_item is not None
                and decision.source_type is ClassificationSourceType.RECEIPT_LINE
            ):
                target.household_item_id = household_item.id
        created_subcategory = bool(
            subcategory is not None
            and subcategory.id in self._created_subcategory_ids - created_before["subcategory"]
        )
        created_concept = bool(
            concept is not None
            and concept.id in self._created_concept_ids - created_before["concept"]
        )
        created_alias = bool(
            alias is not None and alias.id in self._created_alias_ids - created_before["alias"]
        )
        created_household_item = bool(
            household_item is not None
            and household_item.id
            in self._created_household_item_ids - created_before["household_item"]
        )
        record = ClassificationDecisionRecord(
            source_type=decision.source_type.value,
            source_entity_id=decision.source_entity_id,
            version=version,
            spending_parent_category=decision.spending_parent_category.value,
            subcategory_id=subcategory.id if subcategory else None,
            subcategory_name=decision.subcategory_name,
            concept_id=concept.id if concept else None,
            concept_name=decision.canonical_concept,
            item_activity_type=decision.item_activity_type.value,
            replenishment_eligibility=decision.replenishment_eligibility.value,
            confidence=decision.confidence,
            confidence_band=decision.confidence_band.value,
            authority=decision.authority.value,
            provenance_json=list(decision.provenance_codes),
            decision_state=decision.decision_state.value,
            auto_finalize_at=decision.auto_finalize_at,
            finalized_at=target.classification_finalized_at,
            corrects_decision_id=(
                latest_record.id
                if decision.authority is ClassificationAuthority.USER_CORRECTION
                and latest_record is not None
                else None
            ),
            household_item_id=household_item.id if household_item else None,
            created_subcategory=created_subcategory,
            created_concept=created_concept,
            created_alias=created_alias,
            created_household_item=created_household_item,
        )
        self.db.add(record)
        self.db.flush()
        self.db.add(
            AuditEvent(
                workspace_id=self.workspace_id,
                user_id=self.db.info.get("user_id"),
                event_type=(
                    "classification_corrected"
                    if decision.authority is ClassificationAuthority.USER_CORRECTION
                    else "classification_applied"
                ),
                resource_type=decision.source_type.value,
                resource_id=str(decision.source_entity_id),
                metadata_json={
                    "parent": decision.spending_parent_category.value,
                    "subcategory_id": subcategory.id if subcategory else None,
                    "concept_id": concept.id if concept else None,
                    "confidence_band": decision.confidence_band.value,
                    "authority": decision.authority.value,
                    "state": decision.decision_state.value,
                    "version": version,
                    "decision_record_id": record.id,
                    "household_item_id": household_item.id if household_item else None,
                    "created_subcategory": created_subcategory,
                    "created_concept": created_concept,
                    "created_alias": created_alias,
                    "created_household_item": created_household_item,
                    "old_parent": old_projection["parent"],
                    "old_subcategory_id": old_projection["subcategory_id"],
                    "old_concept_id": old_projection["concept_id"],
                    "old_state": old_projection["state"],
                    "old_authority": old_projection["authority"],
                    "old_version": old_projection["version"],
                    "old_legacy_classification": old_projection["legacy_classification"],
                    "provenance_codes": list(decision.provenance_codes),
                },
            )
        )
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return ClassificationApplication(
            True,
            "applied",
            decision,
            subcategory_id=subcategory.id if subcategory else None,
            concept_id=concept.id if concept else None,
            household_item_id=household_item.id if household_item else None,
            decision_record_id=record.id,
            version=version,
            created_subcategory=created_subcategory,
            created_concept=created_concept,
            created_alias=created_alias,
            created_household_item=created_household_item,
        )

    @staticmethod
    def _projection_matches(target, decision: ClassificationDecision) -> bool:
        activity_field = (
            "item_activity_type"
            if decision.source_type is ClassificationSourceType.RECEIPT_LINE
            else "classification_activity_type"
        )
        legacy_projection_matches = bool(
            decision.source_type is not ClassificationSourceType.RECEIPT_LINE
            or (
                getattr(target, "classification", None)
                == _legacy_receipt_classification(decision).value
                and getattr(target, "canonical_name", None) == decision.canonical_concept
            )
        )
        return bool(
            getattr(target, "classification_applied_at", None) is not None
            and getattr(target, "spending_parent_category", None)
            == decision.spending_parent_category.value
            and getattr(target, "classification_subcategory_name", None)
            == decision.subcategory_name
            and getattr(target, "classification_concept_name", None) == decision.canonical_concept
            and getattr(target, activity_field, None) == decision.item_activity_type.value
            and getattr(target, "replenishment_eligibility", None)
            == decision.replenishment_eligibility.value
            and float(getattr(target, "classification_confidence", 0.0) or 0.0)
            == decision.confidence
            and getattr(target, "classification_authority", None) == decision.authority.value
            and getattr(target, "classification_provenance_json", None)
            == list(decision.provenance_codes)
            and getattr(target, "classification_decision_state", None)
            == decision.decision_state.value
            and legacy_projection_matches
        )

    def _target(self, decision: ClassificationDecision):
        if decision.source_type is ClassificationSourceType.RECEIPT_LINE:
            target = self.db.scalar(
                select(PurchaseReceiptItem)
                .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
                .where(
                    PurchaseReceipt.workspace_id == self.workspace_id,
                    PurchaseReceiptItem.id == decision.source_entity_id,
                )
                .with_for_update(of=PurchaseReceiptItem)
                .execution_options(populate_existing=True)
            )
        else:
            target = self.db.scalar(
                select(ExpenseTransaction)
                .where(
                    ExpenseTransaction.workspace_id == self.workspace_id,
                    ExpenseTransaction.id == decision.source_entity_id,
                )
                .with_for_update(of=ExpenseTransaction)
                .execution_options(populate_existing=True)
            )
        if target is None:
            raise ClassificationTaxonomyError("classification target not found")
        return target

    def _locked_concepts(self, concept_ids: Iterable[int]) -> list[ClassificationConcept]:
        ordered_ids = sorted(set(concept_ids))
        return list(
            self.db.scalars(
                select(ClassificationConcept)
                .where(
                    ClassificationConcept.workspace_id == self.workspace_id,
                    ClassificationConcept.id.in_(ordered_ids),
                )
                .order_by(ClassificationConcept.id)
                .with_for_update(of=ClassificationConcept)
            )
        )

    def _locked_subcategories(
        self,
        subcategory_ids: Iterable[int],
    ) -> list[ClassificationSubcategory]:
        ordered_ids = sorted(set(subcategory_ids))
        return list(
            self.db.scalars(
                select(ClassificationSubcategory)
                .where(
                    ClassificationSubcategory.workspace_id == self.workspace_id,
                    ClassificationSubcategory.id.in_(ordered_ids),
                )
                .order_by(ClassificationSubcategory.id)
                .with_for_update(of=ClassificationSubcategory)
            )
        )

    def _locked_subcategory_concepts(
        self,
        subcategory_id: int,
    ) -> list[ClassificationConcept]:
        values = list(
            self.db.scalars(
                select(ClassificationConcept)
                .where(
                    ClassificationConcept.workspace_id == self.workspace_id,
                    ClassificationConcept.subcategory_id == subcategory_id,
                    ClassificationConcept.merged_into_id.is_(None),
                )
                .order_by(ClassificationConcept.id)
                .limit(MAX_SUBCATEGORY_MUTATION_CONCEPTS + 1)
                .with_for_update(of=ClassificationConcept)
            )
        )
        if len(values) > MAX_SUBCATEGORY_MUTATION_CONCEPTS:
            raise ClassificationTaxonomyError(
                "subcategory has too many concepts for an online mutation"
            )
        return values

    def _locked_active_aliases(
        self,
        concept_ids: Iterable[int],
    ) -> list[ClassificationConceptAlias]:
        ordered_ids = sorted(set(concept_ids))
        return list(
            self.db.scalars(
                select(ClassificationConceptAlias)
                .where(
                    ClassificationConceptAlias.workspace_id == self.workspace_id,
                    ClassificationConceptAlias.concept_id.in_(ordered_ids),
                    ClassificationConceptAlias.voided_at.is_(None),
                )
                .order_by(ClassificationConceptAlias.id)
                .limit(MAX_CONCEPT_MUTATION_ALIASES + 1)
                .with_for_update(of=ClassificationConceptAlias)
            )
        )

    def _locked_concept_projections(
        self,
        concept_id: int,
    ) -> tuple[list[PurchaseReceiptItem], list[ExpenseTransaction]]:
        # Receipt correction acquires a linked transaction before its receipt line.
        # Taxonomy-wide mutations must use the same transaction -> line order.
        transactions = list(
            self.db.scalars(
                select(ExpenseTransaction)
                .where(
                    ExpenseTransaction.workspace_id == self.workspace_id,
                    ExpenseTransaction.classification_concept_id == concept_id,
                )
                .order_by(ExpenseTransaction.id)
                .limit(MAX_CONCEPT_MUTATION_PROJECTIONS + 1)
                .with_for_update(of=ExpenseTransaction)
            )
        )
        remaining = MAX_CONCEPT_MUTATION_PROJECTIONS - len(transactions)
        receipt_items = list(
            self.db.scalars(
                select(PurchaseReceiptItem)
                .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
                .where(
                    PurchaseReceipt.workspace_id == self.workspace_id,
                    PurchaseReceiptItem.classification_concept_id == concept_id,
                )
                .order_by(PurchaseReceiptItem.id)
                .limit(max(0, remaining) + 1)
                .with_for_update(of=PurchaseReceiptItem)
            )
        )
        if len(receipt_items) + len(transactions) > MAX_CONCEPT_MUTATION_PROJECTIONS:
            raise ClassificationTaxonomyError(
                "concept has too many current classifications for an online mutation"
            )
        return receipt_items, transactions

    def _locked_subcategory_projections(
        self,
        subcategory_id: int,
    ) -> tuple[list[PurchaseReceiptItem], list[ExpenseTransaction]]:
        transactions = list(
            self.db.scalars(
                select(ExpenseTransaction)
                .where(
                    ExpenseTransaction.workspace_id == self.workspace_id,
                    ExpenseTransaction.classification_subcategory_id == subcategory_id,
                )
                .order_by(ExpenseTransaction.id)
                .limit(MAX_CONCEPT_MUTATION_PROJECTIONS + 1)
                .with_for_update(of=ExpenseTransaction)
            )
        )
        remaining = MAX_CONCEPT_MUTATION_PROJECTIONS - len(transactions)
        receipt_items = list(
            self.db.scalars(
                select(PurchaseReceiptItem)
                .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
                .where(
                    PurchaseReceipt.workspace_id == self.workspace_id,
                    PurchaseReceiptItem.classification_subcategory_id == subcategory_id,
                )
                .order_by(PurchaseReceiptItem.id)
                .limit(max(0, remaining) + 1)
                .with_for_update(of=PurchaseReceiptItem)
            )
        )
        if len(receipt_items) + len(transactions) > MAX_CONCEPT_MUTATION_PROJECTIONS:
            raise ClassificationTaxonomyError(
                "subcategory has too many current classifications for an online mutation"
            )
        return receipt_items, transactions

    def _append_subcategory_projection_corrections(
        self,
        subcategory: ClassificationSubcategory,
        *,
        receipt_items: list[PurchaseReceiptItem],
        transactions: list[ExpenseTransaction],
        now: datetime,
        provenance: str,
    ) -> None:
        for source_type, values in (
            (ClassificationSourceType.RECEIPT_LINE, receipt_items),
            (ClassificationSourceType.TRANSACTION, transactions),
        ):
            for value in values:
                latest = self.db.scalar(
                    select(ClassificationDecisionRecord)
                    .where(
                        ClassificationDecisionRecord.workspace_id == self.workspace_id,
                        ClassificationDecisionRecord.source_type == source_type.value,
                        ClassificationDecisionRecord.source_entity_id == value.id,
                    )
                    .order_by(ClassificationDecisionRecord.version.desc())
                    .limit(1)
                    .with_for_update(of=ClassificationDecisionRecord)
                )
                current_version = int(getattr(value, "classification_version", 1) or 1)
                if (
                    latest is None
                    or latest.version != current_version
                    or latest.subcategory_id
                    != getattr(value, "classification_subcategory_id", None)
                ):
                    raise ClassificationTaxonomyError(
                        "classification projection and ledger are inconsistent"
                    )
                version = max(current_version, latest.version) + 1
                value.classification_subcategory_id = subcategory.id
                value.classification_subcategory_name = subcategory.name
                value.classification_confidence = 1.0
                value.classification_confidence_band = ClassificationConfidenceBand.HIGH.value
                value.classification_authority = ClassificationAuthority.USER_CORRECTION.value
                value.classification_provenance_json = [provenance]
                value.classification_decision_state = ClassificationDecisionState.CORRECTED.value
                value.classification_applied_at = now
                value.classification_auto_finalize_at = None
                value.classification_finalized_at = now
                value.classification_corrected_at = now
                value.classification_corrects_version = latest.version
                value.classification_version = version
                activity = (
                    value.item_activity_type
                    if source_type is ClassificationSourceType.RECEIPT_LINE
                    else value.classification_activity_type
                )
                self.db.add(
                    ClassificationDecisionRecord(
                        source_type=source_type.value,
                        source_entity_id=value.id,
                        version=version,
                        spending_parent_category=value.spending_parent_category,
                        subcategory_id=subcategory.id,
                        subcategory_name=subcategory.name,
                        concept_id=value.classification_concept_id,
                        concept_name=value.classification_concept_name,
                        item_activity_type=activity,
                        replenishment_eligibility=value.replenishment_eligibility,
                        confidence=1.0,
                        confidence_band=ClassificationConfidenceBand.HIGH.value,
                        authority=ClassificationAuthority.USER_CORRECTION.value,
                        provenance_json=[provenance],
                        decision_state=ClassificationDecisionState.CORRECTED.value,
                        auto_finalize_at=None,
                        finalized_at=now,
                        corrects_decision_id=latest.id,
                        household_item_id=(
                            value.household_item_id
                            if source_type is ClassificationSourceType.RECEIPT_LINE
                            else None
                        ),
                        created_subcategory=False,
                        created_concept=False,
                        created_alias=False,
                        created_household_item=False,
                    )
                )

    def _append_concept_projection_corrections(
        self,
        concept: ClassificationConcept,
        *,
        receipt_items: list[PurchaseReceiptItem],
        transactions: list[ExpenseTransaction],
        now: datetime,
        provenance: str,
    ) -> None:
        subcategory = (
            self.db.scalar(
                select(ClassificationSubcategory).where(
                    ClassificationSubcategory.workspace_id == self.workspace_id,
                    ClassificationSubcategory.id == concept.subcategory_id,
                )
            )
            if concept.subcategory_id is not None
            else None
        )
        if concept.subcategory_id is not None and subcategory is None:
            raise ClassificationTaxonomyError("concept subcategory is unavailable")
        subcategory_name = subcategory.name if subcategory is not None else None
        for source_type, values in (
            (ClassificationSourceType.RECEIPT_LINE, receipt_items),
            (ClassificationSourceType.TRANSACTION, transactions),
        ):
            for value in values:
                latest = self.db.scalar(
                    select(ClassificationDecisionRecord)
                    .where(
                        ClassificationDecisionRecord.workspace_id == self.workspace_id,
                        ClassificationDecisionRecord.source_type == source_type.value,
                        ClassificationDecisionRecord.source_entity_id == value.id,
                    )
                    .order_by(ClassificationDecisionRecord.version.desc())
                    .limit(1)
                    .with_for_update(of=ClassificationDecisionRecord)
                )
                current_version = int(getattr(value, "classification_version", 1) or 1)
                if (
                    latest is None
                    or latest.version != current_version
                    or latest.concept_id != getattr(value, "classification_concept_id", None)
                ):
                    raise ClassificationTaxonomyError(
                        "classification projection and ledger are inconsistent"
                    )
                previous_version = max(
                    current_version,
                    latest.version,
                )
                version = previous_version + 1
                value.spending_parent_category = concept.parent_category
                value.classification_subcategory_id = concept.subcategory_id
                value.classification_subcategory_name = subcategory_name
                value.classification_concept_id = concept.id
                value.classification_concept_name = concept.name
                if source_type is ClassificationSourceType.RECEIPT_LINE:
                    value.item_activity_type = concept.item_activity_type
                    value.canonical_name = concept.name
                else:
                    value.classification_activity_type = concept.item_activity_type
                value.replenishment_eligibility = concept.replenishment_eligibility
                value.classification_confidence = 1.0
                value.classification_confidence_band = ClassificationConfidenceBand.HIGH.value
                value.classification_authority = ClassificationAuthority.USER_CORRECTION.value
                value.classification_provenance_json = [provenance]
                value.classification_decision_state = ClassificationDecisionState.CORRECTED.value
                value.classification_applied_at = now
                value.classification_auto_finalize_at = None
                value.classification_finalized_at = now
                value.classification_corrected_at = now
                value.classification_corrects_version = previous_version
                value.classification_version = version
                self.db.add(
                    ClassificationDecisionRecord(
                        source_type=source_type.value,
                        source_entity_id=value.id,
                        version=version,
                        spending_parent_category=concept.parent_category,
                        subcategory_id=concept.subcategory_id,
                        subcategory_name=subcategory_name,
                        concept_id=concept.id,
                        concept_name=concept.name,
                        item_activity_type=concept.item_activity_type,
                        replenishment_eligibility=concept.replenishment_eligibility,
                        confidence=1.0,
                        confidence_band=ClassificationConfidenceBand.HIGH.value,
                        authority=ClassificationAuthority.USER_CORRECTION.value,
                        provenance_json=[provenance],
                        decision_state=ClassificationDecisionState.CORRECTED.value,
                        auto_finalize_at=None,
                        finalized_at=now,
                        corrects_decision_id=latest.id,
                        household_item_id=(
                            value.household_item_id
                            if source_type is ClassificationSourceType.RECEIPT_LINE
                            else None
                        ),
                        created_subcategory=False,
                        created_concept=False,
                        created_alias=False,
                        created_household_item=False,
                    )
                )

    def retarget_receipt_line_household_item(
        self,
        line: PurchaseReceiptItem,
        *,
        household_item_id: int,
        provenance: str,
        now: datetime | None = None,
    ) -> bool:
        """Retarget current household linkage and append a correcting ledger version.

        Unclassified legacy lines have no classification fact to version, so only their
        current linkage is repaired. Classified projections must reconcile exactly with
        their immutable ledger before a merge is allowed to proceed.
        """

        receipt_workspace = self.db.scalar(
            select(PurchaseReceipt.workspace_id).where(PurchaseReceipt.id == line.receipt_id)
        )
        if receipt_workspace != self.workspace_id:
            raise ClassificationTaxonomyError("receipt line is outside the active workspace")
        if line.household_item_id == household_item_id:
            return False
        line.household_item_id = household_item_id
        line.updated_at = now or utc_now()
        if line.classification_applied_at is None:
            return True
        latest = self.db.scalar(
            select(ClassificationDecisionRecord)
            .where(
                ClassificationDecisionRecord.workspace_id == self.workspace_id,
                ClassificationDecisionRecord.source_type
                == ClassificationSourceType.RECEIPT_LINE.value,
                ClassificationDecisionRecord.source_entity_id == line.id,
            )
            .order_by(ClassificationDecisionRecord.version.desc())
            .limit(1)
            .with_for_update(of=ClassificationDecisionRecord)
        )
        current_version = int(line.classification_version or 1)
        if latest is None or latest.version != current_version:
            raise ClassificationTaxonomyError(
                "classification projection and ledger are inconsistent"
            )
        changed_at = now or utc_now()
        version = current_version + 1
        line.classification_confidence = 1.0
        line.classification_confidence_band = ClassificationConfidenceBand.HIGH.value
        line.classification_authority = ClassificationAuthority.USER_CORRECTION.value
        line.classification_provenance_json = [provenance]
        line.classification_decision_state = ClassificationDecisionState.CORRECTED.value
        line.classification_applied_at = changed_at
        line.classification_auto_finalize_at = None
        line.classification_finalized_at = changed_at
        line.classification_corrected_at = changed_at
        line.classification_corrects_version = current_version
        line.classification_version = version
        self.db.add(
            ClassificationDecisionRecord(
                source_type=ClassificationSourceType.RECEIPT_LINE.value,
                source_entity_id=line.id,
                version=version,
                spending_parent_category=line.spending_parent_category,
                subcategory_id=line.classification_subcategory_id,
                subcategory_name=line.classification_subcategory_name,
                concept_id=line.classification_concept_id,
                concept_name=line.classification_concept_name,
                item_activity_type=line.item_activity_type,
                replenishment_eligibility=line.replenishment_eligibility,
                confidence=1.0,
                confidence_band=ClassificationConfidenceBand.HIGH.value,
                authority=ClassificationAuthority.USER_CORRECTION.value,
                provenance_json=[provenance],
                decision_state=ClassificationDecisionState.CORRECTED.value,
                auto_finalize_at=None,
                finalized_at=changed_at,
                corrects_decision_id=latest.id,
                household_item_id=household_item_id,
                created_subcategory=False,
                created_concept=False,
                created_alias=False,
                created_household_item=False,
            )
        )
        return True

    def _assert_concept_merge_chain_is_safe(
        self,
        source: ClassificationConcept,
        target: ClassificationConcept,
    ) -> None:
        for start in (source, target):
            value = start
            seen: set[int] = set()
            while value.merged_into_id is not None:
                if value.id in seen or value.merged_into_id == value.id:
                    raise ClassificationCollisionError("concept merge cycle detected")
                seen.add(value.id)
                if start.id == target.id and value.merged_into_id == source.id:
                    raise ClassificationCollisionError("concept merge would create a cycle")
                value = self.db.scalar(
                    select(ClassificationConcept).where(
                        ClassificationConcept.workspace_id == self.workspace_id,
                        ClassificationConcept.id == value.merged_into_id,
                    )
                )
                if value is None:
                    raise ClassificationCollisionError("concept merge target is unavailable")

    def _assert_subcategory_merge_chain_is_safe(
        self,
        source: ClassificationSubcategory,
        target: ClassificationSubcategory,
    ) -> None:
        for start in (source, target):
            value = start
            seen: set[int] = set()
            while value.merged_into_id is not None:
                if value.id in seen or value.merged_into_id == value.id:
                    raise ClassificationCollisionError("subcategory merge cycle detected")
                seen.add(value.id)
                if start.id == target.id and value.merged_into_id == source.id:
                    raise ClassificationCollisionError("subcategory merge would create a cycle")
                value = self.db.scalar(
                    select(ClassificationSubcategory).where(
                        ClassificationSubcategory.workspace_id == self.workspace_id,
                        ClassificationSubcategory.id == value.merged_into_id,
                    )
                )
                if value is None:
                    raise ClassificationCollisionError("subcategory merge target is unavailable")

    @staticmethod
    def _concept_dimension_key(concept: ClassificationConcept) -> tuple[str, int | None, str, str]:
        return (
            concept.parent_category,
            concept.subcategory_id,
            concept.item_activity_type,
            concept.replenishment_eligibility,
        )

    def _subcategory(
        self, parent: SpendingParentCategory, normalized: str
    ) -> ClassificationSubcategory | None:
        value = self.db.scalar(
            select(ClassificationSubcategory).where(
                ClassificationSubcategory.workspace_id == self.workspace_id,
                ClassificationSubcategory.parent_category == parent.value,
                ClassificationSubcategory.normalized_name == normalized,
            )
        )
        return self._resolved_subcategory(value)

    def _concept(self, normalized: str) -> ClassificationConcept | None:
        value = self.db.scalar(
            select(ClassificationConcept).where(
                ClassificationConcept.workspace_id == self.workspace_id,
                ClassificationConcept.normalized_name == normalized,
            )
        )
        return self._resolved_concept(value)

    def _resolved_subcategory(
        self, value: ClassificationSubcategory | None
    ) -> ClassificationSubcategory | None:
        seen: set[int] = set()
        while value is not None and value.merged_into_id is not None:
            if value.id in seen or value.merged_into_id == value.id:
                raise ClassificationCollisionError("subcategory merge cycle detected")
            seen.add(value.id)
            value = self.db.scalar(
                select(ClassificationSubcategory).where(
                    ClassificationSubcategory.workspace_id == self.workspace_id,
                    ClassificationSubcategory.id == value.merged_into_id,
                )
            )
            if value is None:
                raise ClassificationCollisionError("subcategory merge target is unavailable")
        return value

    def _resolved_concept(
        self, value: ClassificationConcept | None
    ) -> ClassificationConcept | None:
        seen: set[int] = set()
        while value is not None and value.merged_into_id is not None:
            if value.id in seen or value.merged_into_id == value.id:
                raise ClassificationCollisionError("concept merge cycle detected")
            seen.add(value.id)
            value = self.db.scalar(
                select(ClassificationConcept).where(
                    ClassificationConcept.workspace_id == self.workspace_id,
                    ClassificationConcept.id == value.merged_into_id,
                )
            )
            if value is None:
                raise ClassificationCollisionError("concept merge target is unavailable")
        return value

    @staticmethod
    def _validate_concept_dimensions(
        concept: ClassificationConcept,
        decision: ClassificationDecision,
        *,
        subcategory: ClassificationSubcategory | None,
    ) -> None:
        if concept.parent_category != decision.spending_parent_category.value:
            raise ClassificationCollisionError("concept exists under another parent category")
        if concept.item_activity_type != decision.item_activity_type.value:
            raise ClassificationCollisionError("concept exists with another activity type")
        if concept.replenishment_eligibility != decision.replenishment_eligibility.value:
            raise ClassificationCollisionError(
                "concept exists with another replenishment eligibility"
            )
        if subcategory is not None and concept.subcategory_id != subcategory.id:
            raise ClassificationCollisionError("concept exists under another subcategory")


def normalize_taxonomy_name(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    parts: list[str] = []
    current: list[str] = []
    for character in folded:
        category = unicodedata.category(character)[:1]
        if category in {"L", "N"} or (category == "M" and current):
            current.append(character)
        elif current:
            parts.append("".join(current))
            current = []
    if current:
        parts.append("".join(current))
    return " ".join(parts)


def _safe_label(value: str, *, maximum: int) -> str:
    clean = " ".join(str(value).split())
    if not clean or len(clean) > maximum or any(ord(char) < 32 for char in clean):
        raise ClassificationTaxonomyError("classification label is invalid")
    return clean


def _safe_dynamic_label(value: str, *, maximum: int) -> str:
    clean = _safe_label(value, maximum=maximum)
    if not normalize_taxonomy_name(clean):
        raise ClassificationTaxonomyError("classification label has no semantic characters")
    if _HOSTILE_DATA.search(clean):
        raise ClassificationTaxonomyError("hostile external data cannot create taxonomy")
    if re.search(r"https?://|www\.|@", clean, re.IGNORECASE):
        raise ClassificationTaxonomyError("external identifiers cannot create taxonomy")
    if _PACKAGE_POLLUTION.search(clean):
        raise ClassificationTaxonomyError("package or SKU detail cannot create taxonomy")
    return clean


def _canonical_grocery_concept(value: str) -> str | None:
    normalized = normalize_taxonomy_name(value)
    if _PRODUCT_ACCESSORY_CONTEXT.search(normalized):
        return None
    if _AMBIGUOUS_GROCERY_CONCEPT_CONTEXT.search(normalized):
        return None
    for pattern, concept in (
        (r"\beggs?\b", "Eggs"),
        (r"\bmilk\b", "Milk"),
        (r"\bbread\b", "Bread"),
        (r"\brice\b", "Rice"),
        (r"\bvegetables?\b|\bproduce\b", "Vegetables"),
        (r"\bchicken\b", "Chicken"),
        (r"\bbeef\b", "Beef"),
        (r"\bmeat\b", "Meat"),
    ):
        if re.search(pattern, normalized):
            return concept
    return "Grocery item"

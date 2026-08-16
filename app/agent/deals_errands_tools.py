from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.agent.tooling import AgentTool, AgentToolContext, AgentToolRegistry, ToolEffect
from app.config import Settings
from app.models import (
    Errand,
    ErrandHouseholdItem,
    ErrandPlan,
    ErrandPlanStop,
    ErrandPlanStopErrand,
    ErrandPlanStopHouseholdItem,
    ErrandStatus,
    PromotionOffer,
    PromotionSettings,
    utc_now,
)
from app.services.route_planning_service import plan_input_fingerprint

MAX_DEAL_RESULTS = 12
MAX_ERRAND_RESULTS = 25
MAX_PLAN_STOPS = 12
MAX_STOP_ERRANDS = 20
MAX_STOP_HOUSEHOLD_ITEMS = 20
MAX_ERRAND_HOUSEHOLD_ITEMS = 20
MAX_RELEVANCE_REASONS = 3
MAX_ENTITY_ID = 2_147_483_647


class DomainReadModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class RelevantDealsInput(DomainReadModel):
    deal_id: int | None = Field(default=None, ge=1, le=MAX_ENTITY_ID)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    query: str | None = Field(default=None, min_length=1, max_length=100)
    expiring_within_days: int | None = Field(default=None, ge=1, le=90)
    need_related_only: bool = False
    limit: int = Field(default=8, ge=1, le=MAX_DEAL_RESULTS)


class RelevantDeal(DomainReadModel):
    public_id: str = Field(min_length=1, max_length=128)
    merchant: str = Field(min_length=1, max_length=255)
    headline: str = Field(min_length=1, max_length=500)
    category: str = Field(min_length=1, max_length=64)
    offer_type: str = Field(min_length=1, max_length=32)
    percent_off: float | None = Field(default=None, ge=0, le=100)
    amount_off_cents: int | None = Field(default=None, ge=0)
    currency_code: str | None = Field(
        default=None,
        min_length=3,
        max_length=8,
        pattern=r"^[A-Z]{3,8}$",
    )
    minimum_spend_cents: int | None = Field(default=None, ge=0)
    promo_code: str | None = Field(default=None, min_length=1, max_length=128)
    expires_at: datetime | None = None
    score: float = Field(ge=0, le=100)
    saved: bool
    trust_status: Literal["trusted", "review"]
    relevant_to_need: bool
    relevance_reasons: list[str] = Field(max_length=MAX_RELEVANCE_REASONS)


class RelevantDealsOutput(DomainReadModel):
    deals: list[RelevantDeal] = Field(max_length=MAX_DEAL_RESULTS)
    total_count: int = Field(ge=0)
    result_limit: int = Field(ge=1, le=MAX_DEAL_RESULTS)
    truncated: bool


class ErrandsAndPlanInput(DomainReadModel):
    errand_id: int | None = Field(default=None, ge=1, le=MAX_ENTITY_ID)
    plan_id: int | None = Field(default=None, ge=1, le=MAX_ENTITY_ID)
    status: Literal["active", "open", "planned", "completed", "skipped", "all"] = "active"
    included_in_next_plan_only: bool = False
    include_latest_plan: bool = False
    limit: int = Field(default=20, ge=1, le=MAX_ERRAND_RESULTS)

    @model_validator(mode="after")
    def validate_entity_selection(self) -> ErrandsAndPlanInput:
        if self.errand_id is not None and self.plan_id is not None:
            raise ValueError("errand_id and plan_id cannot both be specified")
        return self


class ErrandSummary(DomainReadModel):
    public_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    errand_type: str = Field(min_length=1, max_length=32)
    status: str = Field(min_length=1, max_length=32)
    priority: str = Field(min_length=1, max_length=32)
    due_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    estimated_duration_minutes: int | None = Field(default=None, ge=0, le=1_440)
    included_in_next_plan: bool
    place_resolution_status: str = Field(min_length=1, max_length=32)
    resolved_place_name: str | None = Field(default=None, min_length=1, max_length=255)
    household_items: list[str] = Field(max_length=MAX_ERRAND_HOUSEHOLD_ITEMS)


class ErrandPlanStopSummary(DomainReadModel):
    order: int = Field(ge=1)
    place_name: str = Field(min_length=1, max_length=255)
    errands: list[str] = Field(max_length=MAX_STOP_ERRANDS)
    errands_truncated: bool
    household_items: list[str] = Field(max_length=MAX_STOP_HOUSEHOLD_ITEMS)
    household_items_truncated: bool


class ErrandPlanSummary(DomainReadModel):
    public_id: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=32)
    planned_for: datetime | None = None
    routing_provider: str = Field(min_length=1, max_length=64)
    routing_is_optimized: bool
    is_stale: bool
    stale_reason: str | None = Field(default=None, min_length=1, max_length=255)
    estimated_stop_minutes: int = Field(ge=0)
    travel_duration_minutes: int | None = Field(default=None, ge=0)
    distance_meters: int | None = Field(default=None, ge=0)
    available_minutes: int | None = Field(default=None, ge=0)
    stops: list[ErrandPlanStopSummary] = Field(max_length=MAX_PLAN_STOPS)
    total_stop_count: int = Field(ge=0)
    stops_truncated: bool


class ErrandsAndPlanOutput(DomainReadModel):
    errands: list[ErrandSummary] = Field(max_length=MAX_ERRAND_RESULTS)
    total_count: int = Field(ge=0)
    result_limit: int = Field(ge=1, le=MAX_ERRAND_RESULTS)
    truncated: bool
    plan: ErrandPlanSummary | None = None


def build_deals_errands_tools(settings: Settings) -> tuple[AgentTool, AgentTool]:
    """Build the two bounded Day 4 read tools without mutating the registry."""

    return (
        AgentTool(
            name="get_relevant_deals",
            description=(
                "Read current ranked ExpenseOps promotions using bounded deal, category, "
                "search, expiry, and existing replenishment-relevance filters."
            ),
            effect=ToolEffect.READ,
            input_model=RelevantDealsInput,
            output_model=RelevantDealsOutput,
            handler=partial(_get_relevant_deals, settings=settings),
        ),
        AgentTool(
            name="get_errands_and_plan",
            description=(
                "Read bounded ExpenseOps errands and, when requested, a stored current or "
                "specific plan with canonical freshness and stop order."
            ),
            effect=ToolEffect.READ,
            input_model=ErrandsAndPlanInput,
            output_model=ErrandsAndPlanOutput,
            handler=_get_errands_and_plan,
        ),
    )


def register_deals_errands_tools(
    registry: AgentToolRegistry,
    settings: Settings,
) -> None:
    for tool in build_deals_errands_tools(settings):
        registry.register(tool)


def _get_relevant_deals(
    context: AgentToolContext,
    values: RelevantDealsInput,
    *,
    settings: Settings,
) -> dict:
    now = utc_now()
    minimum_score = context.db.scalar(
        select(PromotionSettings.minimum_score).where(
            PromotionSettings.workspace_id == context.workspace_id
        )
    )
    if minimum_score is None:
        minimum_score = settings.promotions_min_score

    criteria = [
        PromotionOffer.workspace_id == context.workspace_id,
        PromotionOffer.status == "active",
        PromotionOffer.trust_status != "suppressed",
        or_(PromotionOffer.starts_at.is_(None), PromotionOffer.starts_at <= now),
        or_(PromotionOffer.expires_at.is_(None), PromotionOffer.expires_at >= now),
        or_(PromotionOffer.score >= float(minimum_score), PromotionOffer.saved.is_(True)),
    ]
    if values.deal_id is not None:
        criteria.append(PromotionOffer.id == values.deal_id)
    if values.category:
        criteria.append(func.lower(PromotionOffer.primary_category) == values.category.casefold())
    if values.query:
        pattern = f"%{_escape_like(values.query.casefold())}%"
        criteria.append(
            or_(
                func.lower(PromotionOffer.merchant_normalized).like(pattern, escape="\\"),
                func.lower(PromotionOffer.headline).like(pattern, escape="\\"),
            )
        )
    if values.expiring_within_days is not None:
        criteria.extend(
            [
                PromotionOffer.expires_at.is_not(None),
                PromotionOffer.expires_at <= now + timedelta(days=values.expiring_within_days),
            ]
        )
    if values.need_related_only:
        relevance = PromotionOffer.score_breakdown_json["replenishment_relevance"].as_float()
        criteria.append(func.coalesce(relevance, 0.0) > 0.0)

    total_count = int(
        context.db.scalar(select(func.count(PromotionOffer.id)).where(*criteria)) or 0
    )
    offers = list(
        context.db.scalars(
            select(PromotionOffer)
            .where(*criteria)
            .order_by(
                PromotionOffer.saved.desc(),
                PromotionOffer.score.desc(),
                PromotionOffer.expires_at.is_(None),
                PromotionOffer.expires_at,
                PromotionOffer.id.desc(),
            )
            .limit(values.limit)
        )
    )
    return {
        "deals": [_deal_summary(offer) for offer in offers],
        "total_count": total_count,
        "result_limit": values.limit,
        "truncated": total_count > len(offers),
    }


def _get_errands_and_plan(
    context: AgentToolContext,
    values: ErrandsAndPlanInput,
) -> dict:
    criteria = [Errand.workspace_id == context.workspace_id]
    if values.errand_id is not None:
        criteria.append(Errand.id == values.errand_id)
    if values.status == "active":
        criteria.append(Errand.status.in_([ErrandStatus.OPEN.value, ErrandStatus.PLANNED.value]))
    elif values.status != "all":
        criteria.append(Errand.status == values.status)
    if values.included_in_next_plan_only:
        criteria.append(Errand.included_in_next_plan.is_(True))

    total_count = int(context.db.scalar(select(func.count(Errand.id)).where(*criteria)) or 0)
    errands = list(
        context.db.scalars(
            select(Errand)
            .where(*criteria)
            .options(
                selectinload(Errand.household_links).selectinload(
                    ErrandHouseholdItem.household_item
                )
            )
            .order_by(Errand.created_at.desc(), Errand.id.desc())
            .limit(values.limit)
        )
    )

    plan = None
    if values.plan_id is not None:
        plan = _load_plan(context, plan_id=values.plan_id)
    elif values.include_latest_plan:
        plan = _load_plan(context)

    return {
        "errands": [_errand_summary(errand) for errand in errands],
        "total_count": total_count,
        "result_limit": values.limit,
        "truncated": total_count > len(errands),
        "plan": _plan_summary(context, plan) if plan is not None else None,
    }


def _load_plan(
    context: AgentToolContext,
    *,
    plan_id: int | None = None,
) -> ErrandPlan | None:
    criteria = [ErrandPlan.workspace_id == context.workspace_id]
    if plan_id is not None:
        criteria.append(ErrandPlan.id == plan_id)
    stmt = (
        select(ErrandPlan)
        .where(*criteria)
        .options(
            selectinload(ErrandPlan.stops)
            .selectinload(ErrandPlanStop.errand_links)
            .selectinload(ErrandPlanStopErrand.errand),
            selectinload(ErrandPlan.stops)
            .selectinload(ErrandPlanStop.household_item_links)
            .selectinload(ErrandPlanStopHouseholdItem.household_item),
        )
    )
    if plan_id is None:
        stmt = stmt.order_by(ErrandPlan.created_at.desc(), ErrandPlan.id.desc()).limit(1)
    return context.db.scalars(stmt).first()


def _deal_summary(offer: PromotionOffer) -> dict:
    breakdown = offer.score_breakdown_json if isinstance(offer.score_breakdown_json, dict) else {}
    relevant_to_need = _number(breakdown.get("replenishment_relevance")) > 0
    return {
        "public_id": str(offer.id),
        "merchant": _text(offer.merchant_normalized, 255, "Unknown merchant"),
        "headline": _text(offer.headline, 500, "Promotion"),
        "category": _text(offer.primary_category, 64, "Other"),
        "offer_type": _text(offer.offer_type, 32, "other"),
        "percent_off": _bounded_number(offer.percent_off, upper=100),
        "amount_off_cents": _money_cents(offer.amount_off),
        "currency_code": _currency(offer.currency),
        "minimum_spend_cents": _money_cents(offer.minimum_spend),
        "promo_code": _optional_text(offer.promo_code, 128),
        "expires_at": offer.expires_at,
        "score": _bounded_number(offer.score, upper=100) or 0.0,
        "saved": bool(offer.saved),
        "trust_status": "trusted" if offer.trust_status == "trusted" else "review",
        "relevant_to_need": relevant_to_need,
        "relevance_reasons": _relevance_reasons(offer, breakdown),
    }


def _relevance_reasons(offer: PromotionOffer, breakdown: dict) -> list[str]:
    reasons: list[str] = []
    if _number(breakdown.get("replenishment_relevance")) > 0:
        reasons.append("Relevant to a household item ExpenseOps currently considers due soon.")
    if _number(breakdown.get("preference")) > 0:
        reasons.append("Matches your saved deal preferences.")
    elif _number(breakdown.get("merchant_affinity")) > 0:
        reasons.append("Matches a merchant or category in your purchase history.")
    if _number(breakdown.get("deal_value")) > 0:
        reasons.append("This promotion has a meaningful discount.")
    if _number(breakdown.get("urgency")) > 0 and offer.expires_at is not None:
        reasons.append("The offer expires soon.")
    if _number(breakdown.get("minimum_spend_penalty")) < 0:
        reasons.append("A minimum spend is required.")
    if _number(breakdown.get("feedback")) > 0:
        reasons.append("Matches your previous deal feedback.")
    if offer.trust_status == "trusted":
        reasons.append("The destination passed ExpenseOps trust checks.")
    return reasons[:MAX_RELEVANCE_REASONS]


def _errand_summary(errand: Errand) -> dict:
    household_items = sorted(
        {
            _text(link.household_item.name, 255, "Tracked item")
            for link in errand.household_links
            if link.household_item is not None
            and link.household_item.workspace_id == errand.workspace_id
        },
        key=str.casefold,
    )
    resolved_place_name = None
    if errand.place_resolution_status == "resolved":
        resolved_place_name = _optional_text(errand.resolved_place_name, 255)
    return {
        "public_id": str(errand.id),
        "title": _text(errand.title, 255, "Untitled errand"),
        "errand_type": _text(errand.errand_type, 32, "other"),
        "status": _text(errand.status, 32, "open"),
        "priority": _text(errand.priority, 32, "normal"),
        "due_on": _as_utc(errand.due_at).date().isoformat() if errand.due_at else None,
        "estimated_duration_minutes": errand.estimated_duration_minutes,
        "included_in_next_plan": bool(errand.included_in_next_plan),
        "place_resolution_status": _text(
            errand.place_resolution_status,
            32,
            "unresolved",
        ),
        "resolved_place_name": resolved_place_name,
        "household_items": household_items[:MAX_ERRAND_HOUSEHOLD_ITEMS],
    }


def _plan_summary(context: AgentToolContext, plan: ErrandPlan) -> dict:
    is_stale, stale_reason = _plan_freshness(context, plan)
    ordered_stops = sorted(plan.stops, key=lambda stop: (stop.stop_order, stop.id))
    stops = [
        _plan_stop_summary(stop, workspace_id=context.workspace_id)
        for stop in ordered_stops[:MAX_PLAN_STOPS]
    ]
    return {
        "public_id": str(plan.id),
        "status": _text(plan.status, 32, "planned"),
        "planned_for": plan.planned_for,
        "routing_provider": _text(plan.routing_provider, 64, "unknown"),
        "routing_is_optimized": bool(plan.routing_is_optimized),
        "is_stale": is_stale,
        "stale_reason": stale_reason,
        "estimated_stop_minutes": max(0, int(plan.estimated_stop_minutes or 0)),
        "travel_duration_minutes": _nonnegative_int(plan.travel_duration_minutes),
        "distance_meters": _nonnegative_int(plan.distance_meters),
        "available_minutes": _nonnegative_int(plan.available_minutes),
        "stops": stops,
        "total_stop_count": len(ordered_stops),
        "stops_truncated": len(ordered_stops) > len(stops),
    }


def _plan_stop_summary(stop: ErrandPlanStop, *, workspace_id: int) -> dict:
    errand_titles = sorted(
        {
            _text(link.errand.title, 255, "Untitled errand")
            for link in stop.errand_links
            if link.errand is not None and link.errand.workspace_id == workspace_id
        },
        key=str.casefold,
    )
    household_items = sorted(
        {
            _text(link.household_item.name, 255, "Tracked item")
            for link in stop.household_item_links
            if link.household_item is not None and link.household_item.workspace_id == workspace_id
        },
        key=str.casefold,
    )
    return {
        "order": max(1, int(stop.stop_order)),
        "place_name": _text(stop.place_name, 255, "Planned stop"),
        "errands": errand_titles[:MAX_STOP_ERRANDS],
        "errands_truncated": len(errand_titles) > MAX_STOP_ERRANDS,
        "household_items": household_items[:MAX_STOP_HOUSEHOLD_ITEMS],
        "household_items_truncated": len(household_items) > MAX_STOP_HOUSEHOLD_ITEMS,
    }


def _plan_freshness(
    context: AgentToolContext,
    plan: ErrandPlan,
) -> tuple[bool, str | None]:
    if plan.input_snapshot is None or not plan.input_fingerprint:
        return (
            True,
            "This route predates freshness verification. Recalculate before starting.",
        )
    current_fingerprint = plan_input_fingerprint(context.db, plan.input_snapshot)
    if current_fingerprint != plan.input_fingerprint:
        return (
            True,
            "The route inputs or included errands changed. Recalculate before starting.",
        )
    return False, None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    return number if math.isfinite(number) else 0.0


def _bounded_number(value: object, *, upper: float) -> float | None:
    if value is None:
        return None
    number = _number(value)
    return round(max(0.0, min(upper, number)), 2)


def _money_cents(value: object) -> int | None:
    if value is None:
        return None
    return max(0, round(_number(value) * 100))


def _currency(value: str | None) -> str | None:
    normalized = (value or "").strip().upper()
    return normalized if re.fullmatch(r"[A-Z]{3,8}", normalized) else None


def _text(value: str | None, maximum: int, fallback: str) -> str:
    normalized = (value or "").strip()
    return (normalized or fallback)[:maximum]


def _optional_text(value: str | None, maximum: int) -> str | None:
    normalized = (value or "").strip()
    return normalized[:maximum] if normalized else None


def _nonnegative_int(value: int | None) -> int | None:
    return max(0, int(value)) if value is not None else None


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

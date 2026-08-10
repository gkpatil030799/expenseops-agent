from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    ExpenseTransaction,
    HouseholdItem,
    PromotionFeedback,
    PromotionOffer,
    PromotionSettings,
)
from app.services.replenishment_service import due_score


class PromotionRankingService:
    def __init__(self, db: Session, settings: Settings | None = None):
        self.db = db
        self.app_settings = settings or get_settings()

    def score(self, offer: PromotionOffer, *, now: datetime | None = None) -> float:
        current = now or datetime.now(UTC)
        settings = self.settings()
        merchant = offer.merchant_normalized.casefold()
        if (
            merchant in {v.casefold() for v in settings.muted_merchants}
            or offer.primary_category in settings.muted_categories
        ):
            offer.status = "suppressed"
            offer.score = 0
            offer.score_breakdown_json = {"suppressed": "muted preference", "final_score": 0}
            return 0
        breakdown: dict[str, float | str | list[str]] = {}
        reasons: list[str] = []
        need_points = 0.0
        for item in self.db.execute(
            select(HouseholdItem).where(HouseholdItem.enabled.is_(True))
        ).scalars():
            if _related(
                item.name, f"{offer.headline} {offer.description or ''} {offer.primary_category}"
            ):
                score = (
                    due_score(item.last_acquired_at, item.cadence_days, now=current)
                    if item.last_acquired_at
                    else 0
                )
                if score >= 0.7:
                    need_points = max(need_points, 30 if score >= 0.9 else 24)
                    reasons.append(f"{item.name} may be due soon")
        breakdown["replenishment_relevance"] = need_points
        txs = list(
            self.db.execute(
                select(ExpenseTransaction.merchant_name, ExpenseTransaction.category)
            ).all()
        )
        merchant_hits = sum(1 for name, _ in txs if name and merchant in name.casefold())
        category_hits = sum(
            1
            for _, category in txs
            if category and offer.primary_category.casefold() in category.casefold()
        )
        affinity = min(15.0, merchant_hits * 4.0 + category_hits * 1.5)
        if affinity:
            reasons.append("You have shopped with this merchant or category before")
        breakdown["merchant_affinity"] = affinity
        deal = min(18.0, (offer.percent_off or 0) * 0.3 + (offer.amount_off or 0) * 0.8)
        if offer.offer_type in {"bogo", "free_item"}:
            deal = max(deal, 12.0)
        breakdown["deal_value"] = deal
        urgency = 0.0
        if offer.expires_at:
            days = (_aware(offer.expires_at) - current).total_seconds() / 86400
            urgency = 8.0 if 0 <= days <= 3 else 4.0 if days <= 7 else 0.0
            if urgency:
                reasons.append(f"Offer expires in {max(0, int(days) + 1)} days")
        breakdown["urgency"] = urgency
        trust = (
            10.0
            if offer.trust_status == "trusted"
            else 2.0
            if offer.trust_status == "review"
            else -50.0
        )
        breakdown["trust"] = trust
        breakdown["extraction_confidence"] = round(offer.confidence * 8, 2)
        burden = -min(15.0, (offer.minimum_spend or 0) / 10) if offer.minimum_spend else 0.0
        breakdown["minimum_spend_penalty"] = burden
        preference = (
            8.0
            if merchant in {v.casefold() for v in settings.preferred_merchants}
            or offer.primary_category in settings.preferred_categories
            else 0.0
        )
        breakdown["preference"] = preference
        feedback = (
            list(
                self.db.execute(
                    select(PromotionFeedback.feedback_type).where(
                        PromotionFeedback.promotion_offer_id == offer.id
                    )
                ).scalars()
            )
            if offer.id
            else []
        )
        feedback_points = (
            8.0
            if "more_like_this" in feedback
            else -12.0
            if any(v in feedback for v in ("not_relevant", "less_like_this"))
            else 0.0
        )
        breakdown["feedback"] = feedback_points
        final = max(
            0.0,
            min(
                100.0,
                8
                + need_points
                + affinity
                + deal
                + urgency
                + trust
                + offer.confidence * 8
                + burden
                + preference
                + feedback_points
                + (8 if offer.saved else 0),
            ),
        )
        breakdown["reasons"] = reasons
        breakdown["final_score"] = round(final, 2)
        offer.score = round(final, 2)
        offer.score_breakdown_json = breakdown
        return offer.score

    def rescore_all(self) -> int:
        count = 0
        for offer in self.db.execute(
            select(PromotionOffer).where(
                PromotionOffer.status.in_(["active", "upcoming", "suppressed", "expired"])
            )
        ).scalars():
            if (
                offer.expires_at
                and offer.message
                and offer.expires_at.date() < offer.message.received_at.date()
            ):
                offer.expires_at = None
                offer.expiry_precision = "unknown"
                if offer.status == "expired":
                    offer.status = "suppressed" if offer.trust_status == "suppressed" else "active"
            if offer.expires_at and _aware(offer.expires_at) < datetime.now(UTC):
                offer.status = "expired"
                continue
            self.score(offer)
            count += 1
        self.db.commit()
        return count

    def settings(self) -> PromotionSettings:
        value = (
            self.db.execute(select(PromotionSettings).order_by(PromotionSettings.id))
            .scalars()
            .first()
        )
        if value is None:
            value = PromotionSettings(
                minimum_score=self.app_settings.promotions_min_score,
                maximum_deals_per_digest=self.app_settings.promotions_digest_max_deals,
                digest_cadence=self.app_settings.promotions_digest_cadence,
                digest_time=self.app_settings.promotions_digest_local_hour,
                timezone=self.app_settings.promotions_digest_timezone,
            )
            self.db.add(value)
            self.db.flush()
        return value


def _related(item: str, offer: str) -> bool:
    stop = {"the", "and", "for", "with", "products", "items"}
    a = {x for x in re.findall(r"[a-z0-9]+", item.casefold()) if len(x) > 2 and x not in stop}
    b = set(re.findall(r"[a-z0-9]+", offer.casefold()))
    synonyms = {"detergent": {"laundry"}, "paper": {"household"}, "towels": {"paper"}}
    return bool(a & b or any(synonyms.get(x, set()) & b for x in a))


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

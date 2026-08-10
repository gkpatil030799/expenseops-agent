from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import or_, select

from app.api.deps import DbSession
from app.models import PromotionFeedback, PromotionOffer, PromotionSettings, utc_now
from app.promotion_schemas import (
    PromotionFeedbackRequest,
    PromotionSettingsPatch,
    PromotionSyncRequest,
)
from app.services.gmail_promotion_ingestion_service import GmailPromotionIngestionService
from app.services.promotion_digest_service import PromotionDigestService
from app.services.promotion_extraction_service import CATEGORIES
from app.services.promotion_ranking_service import PromotionRankingService

router = APIRouter(prefix="/api/promotions", tags=["promotions"])


@router.get("")
def list_promotions(
    db: DbSession,
    category: str | None = None,
    search: str | None = None,
    status: str = "active",
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    ranking = PromotionRankingService(db)
    ranking.rescore_all()
    stmt = select(PromotionOffer).where(PromotionOffer.status == status)
    if status == "active":
        minimum_score = ranking.settings().minimum_score
        stmt = stmt.where(
            or_(PromotionOffer.score >= minimum_score, PromotionOffer.saved.is_(True))
        )
    if category:
        stmt = stmt.where(PromotionOffer.primary_category == category)
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            PromotionOffer.merchant_normalized.ilike(pattern)
            | PromotionOffer.headline.ilike(pattern)
        )
    values = db.execute(
        stmt.order_by(PromotionOffer.saved.desc(), PromotionOffer.score.desc()).limit(limit)
    ).scalars()
    return [_offer(v) for v in values]


@router.get("/categories")
def categories() -> list[str]:
    return list(CATEGORIES)


@router.get("/settings")
def get_settings(db: DbSession) -> dict:
    return _settings(PromotionRankingService(db).settings())


@router.patch("/settings")
def patch_settings(payload: PromotionSettingsPatch, db: DbSession) -> dict:
    value = PromotionRankingService(db).settings()
    for key, item in payload.model_dump(exclude_none=True).items():
        setattr(value, key, item)
    value.updated_at = utc_now()
    db.commit()
    PromotionRankingService(db).rescore_all()
    return _settings(value)


@router.post("/sync")
def sync(payload: PromotionSyncRequest, db: DbSession) -> dict:
    try:
        result = GmailPromotionIngestionService(db).sync(max_results=payload.max_results)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    return result.__dict__


@router.post("/rescore")
def rescore(db: DbSession) -> dict:
    return {"rescored": PromotionRankingService(db).rescore_all()}


@router.post("/{offer_id}/save")
def save(offer_id: int, db: DbSession) -> dict:
    offer = _get(db, offer_id)
    offer.saved = True
    db.add(PromotionFeedback(promotion_offer_id=offer.id, feedback_type="saved"))
    db.commit()
    PromotionRankingService(db).rescore_all()
    return _offer(offer)


@router.post("/{offer_id}/dismiss")
def dismiss(offer_id: int, db: DbSession) -> dict:
    offer = _get(db, offer_id)
    offer.status = "dismissed"
    db.add(PromotionFeedback(promotion_offer_id=offer.id, feedback_type="dismissed"))
    db.commit()
    return _offer(offer)


@router.post("/{offer_id}/feedback")
def feedback(offer_id: int, payload: PromotionFeedbackRequest, db: DbSession) -> dict:
    offer = _get(db, offer_id)
    db.add(
        PromotionFeedback(
            promotion_offer_id=offer.id,
            feedback_type=payload.feedback_type,
            metadata_json=payload.metadata,
        )
    )
    if payload.feedback_type == "mute_merchant":
        prefs = PromotionRankingService(db).settings()
        prefs.muted_merchants = list(
            dict.fromkeys([*prefs.muted_merchants, offer.merchant_normalized])
        )
    if payload.feedback_type in {"dismissed", "not_relevant"}:
        offer.status = "dismissed"
    db.commit()
    PromotionRankingService(db).rescore_all()
    return _offer(offer)


@router.post("/digest/preview")
def digest_preview(db: DbSession) -> dict:
    result = PromotionDigestService(db).preview()
    return {"message": result["message"], "offers": [_offer(v) for v in result["offers"]]}


@router.post("/digest/send")
def digest_send(db: DbSession) -> dict:
    run = PromotionDigestService(db).send(force=True)
    return {
        "id": run.id,
        "delivery_status": run.delivery_status,
        "offers_included": run.offers_included,
    }


@router.get("/{offer_id}")
def get_offer(offer_id: int, db: DbSession) -> dict:
    return _offer(_get(db, offer_id))


def _get(db, offer_id: int) -> PromotionOffer:
    value = db.get(PromotionOffer, offer_id)
    if value is None:
        raise HTTPException(404, "Promotion not found")
    return value


def _offer(v: PromotionOffer) -> dict:
    return {
        "id": v.id,
        "merchant": v.merchant_normalized,
        "category": v.primary_category,
        "headline": v.headline,
        "description": v.description,
        "offer_type": v.offer_type,
        "percent_off": v.percent_off,
        "amount_off": v.amount_off,
        "minimum_spend": v.minimum_spend,
        "promo_code": v.promo_code,
        "expires_at": v.expires_at,
        "expiry_precision": v.expiry_precision,
        "destination_url": v.destination_url,
        "terms_summary": v.terms_summary,
        "trust_status": v.trust_status,
        "status": v.status,
        "score": v.score,
        "saved": v.saved,
        "why": v.score_breakdown_json.get("reasons", []),
        "source_count": len(v.source_message_ids),
    }


def _settings(v: PromotionSettings) -> dict:
    return {
        key: getattr(v, key)
        for key in (
            "preferred_categories",
            "muted_categories",
            "preferred_merchants",
            "muted_merchants",
            "minimum_score",
            "maximum_deals_per_digest",
            "digest_cadence",
            "digest_time",
            "timezone",
            "include_minimum_spend",
        )
    }

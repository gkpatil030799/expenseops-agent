from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import PromotionDigestRun, PromotionOffer
from app.services.promotion_ranking_service import PromotionRankingService
from app.services.telegram_service import TelegramService


class PromotionDigestService:
    def __init__(
        self, db: Session, settings: Settings | None = None, telegram: TelegramService | None = None
    ):
        self.db = db
        self.settings = settings or get_settings()
        self.telegram = telegram or TelegramService(self.settings)

    def qualifying(self) -> list[PromotionOffer]:
        prefs = PromotionRankingService(self.db, self.settings).settings()
        limit = min(prefs.maximum_deals_per_digest, self.settings.promotions_digest_max_deals)
        minimum = max(prefs.minimum_score, self.settings.promotions_min_score)
        return list(
            self.db.execute(
                select(PromotionOffer)
                .where(
                    PromotionOffer.status == "active",
                    PromotionOffer.trust_status != "suppressed",
                    PromotionOffer.score >= minimum,
                )
                .order_by(
                    PromotionOffer.saved.desc(),
                    PromotionOffer.score.desc(),
                    PromotionOffer.expires_at.is_(None),
                    PromotionOffer.expires_at,
                )
                .limit(limit)
            ).scalars()
        )

    def preview(self) -> dict:
        offers = self.qualifying()
        return {"offers": offers, "message": self._format(offers) if offers else None}

    def send(self, *, force: bool = False) -> PromotionDigestRun:
        offers = self.qualifying()
        fingerprints = [v.campaign_fingerprint for v in offers]
        run = PromotionDigestRun(
            offers_considered=self.db.query(PromotionOffer)
            .filter(PromotionOffer.status == "active")
            .count(),
            offers_included=len(offers),
            campaign_fingerprints=fingerprints,
        )
        self.db.add(run)
        if not offers:
            run.delivery_status = "skipped_no_qualifying_offers"
        elif not self.settings.promotions_digest_enabled and not force:
            run.delivery_status = "skipped_disabled"
        elif not force and self._duplicate(fingerprints, offers):
            run.delivery_status = "skipped_duplicate"
        else:
            run.delivery_status = (
                "sent" if self.telegram.send_message(self._format(offers)) else "failed"
            )
        self.db.commit()
        self.db.refresh(run)
        return run

    def _duplicate(self, fingerprints: list[str], offers: list[PromotionOffer]) -> bool:
        prior = (
            self.db.execute(
                select(PromotionDigestRun)
                .where(PromotionDigestRun.delivery_status == "sent")
                .order_by(PromotionDigestRun.generated_at.desc())
            )
            .scalars()
            .first()
        )
        if not prior or set(prior.campaign_fingerprints) != set(fingerprints):
            return False
        urgent = any(
            v.expires_at and _aware(v.expires_at) <= datetime.now(UTC) + timedelta(days=2)
            for v in offers
        )
        return not urgent or _aware(prior.generated_at) >= datetime.now(UTC) - timedelta(days=1)

    def _format(self, offers: list[PromotionOffer]) -> str:
        groups: dict[str, list[PromotionOffer]] = defaultdict(list)
        for offer in offers:
            groups[offer.primary_category].append(offer)
        lines = ["<b>BEST DEALS FOR YOU</b>"]
        for category, values in groups.items():
            lines.append(f"\n<b>{escape(category)}</b>")
            for offer in values:
                lines.append(f"{escape(offer.merchant_normalized)} — {escape(offer.headline)}")
                reasons = offer.score_breakdown_json.get("reasons", [])
                if reasons:
                    lines.append(f"Relevant: {escape(str(reasons[0]))}")
                if offer.expires_at:
                    lines.append(f"Expires {offer.expires_at.date().isoformat()}")
        if self.settings.app_public_url:
            lines.append(
                f'\n<a href="{escape(self.settings.app_public_url)}?workspace=promotions">'
                "View all deals</a>"
            )
        return "\n".join(lines)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

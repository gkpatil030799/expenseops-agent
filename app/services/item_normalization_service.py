from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import HouseholdItem, HouseholdItemAlias, utc_now

_NOISE = {
    "ea",
    "each",
    "pkg",
    "pack",
    "ct",
    "count",
    "oz",
    "lb",
    "lbs",
    "organic",
}
_ABBREVIATIONS = {
    "mlk": "milk",
    "brd": "bread",
    "tlt": "toilet",
    "ppr": "paper",
    "det": "detergent",
}


def normalize_item_name(value: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", value.casefold())
    normalized = [_ABBREVIATIONS.get(token, token) for token in tokens]
    meaningful = [token for token in normalized if token not in _NOISE and not token.isdigit()]
    return " ".join(meaningful).strip()


def normalize_merchant(value: str | None) -> str:
    if not value:
        return ""
    tokens = re.findall(r"[a-z0-9]+", value.casefold())
    ignored = {"store", "inc", "llc", "com", "the"}
    return " ".join(token for token in tokens if token not in ignored)


@dataclass(frozen=True)
class ItemMatch:
    household_item: HouseholdItem | None
    confidence: float
    source: str


class ItemNormalizationService:
    def __init__(self, db: Session):
        self.db = db

    def match(self, raw_name: str, merchant: str | None = None) -> ItemMatch:
        normalized = normalize_item_name(raw_name)
        if not normalized:
            return ItemMatch(None, 0.0, "empty")
        merchant_key = normalize_merchant(merchant)
        aliases = list(
            self.db.execute(
                select(HouseholdItemAlias).where(HouseholdItemAlias.voided_at.is_(None))
            ).scalars()
        )
        for alias in aliases:
            if alias.normalized_alias == normalized and (
                not alias.merchant_normalized or alias.merchant_normalized == merchant_key
            ):
                return ItemMatch(alias.household_item, min(1.0, alias.confidence), "alias")

        items = list(
            self.db.execute(select(HouseholdItem).where(HouseholdItem.enabled.is_(True))).scalars()
        )
        scored = []
        normalized_tokens = set(normalized.split())
        for item in items:
            item_name = normalize_item_name(item.name)
            item_tokens = set(item_name.split())
            similarity = SequenceMatcher(None, normalized, item_name).ratio()
            if item_tokens and item_tokens.issubset(normalized_tokens):
                similarity = max(similarity, 0.92)
            scored.append((similarity, item))
        if not scored:
            return ItemMatch(None, 0.0, "none")
        score, item = max(scored, key=lambda pair: pair[0])
        return ItemMatch(item if score >= 0.65 else None, round(score, 4), "name_similarity")

    def learn_alias(
        self,
        household_item: HouseholdItem,
        raw_pattern: str,
        *,
        merchant: str | None = None,
        source: str = "user",
        confidence: float = 1.0,
    ) -> HouseholdItemAlias:
        normalized = normalize_item_name(raw_pattern)
        merchant_key = normalize_merchant(merchant)
        existing = self.db.execute(
            select(HouseholdItemAlias).where(
                HouseholdItemAlias.household_item_id == household_item.id,
                HouseholdItemAlias.merchant_normalized == merchant_key,
                HouseholdItemAlias.normalized_alias == normalized,
            )
        ).scalar_one_or_none()
        if existing:
            existing.raw_pattern = raw_pattern
            existing.confidence = max(existing.confidence, confidence)
            existing.voided_at = None
            return existing
        alias = HouseholdItemAlias(
            household_item_id=household_item.id,
            merchant_normalized=merchant_key,
            raw_pattern=raw_pattern,
            normalized_alias=normalized,
            confidence=confidence,
            source=source,
        )
        self.db.add(alias)
        return alias

    def void_alias(
        self,
        household_item: HouseholdItem,
        raw_pattern: str,
        *,
        merchant: str | None = None,
    ) -> None:
        normalized = normalize_item_name(raw_pattern)
        merchant_key = normalize_merchant(merchant)
        aliases = self.db.execute(
            select(HouseholdItemAlias).where(
                HouseholdItemAlias.household_item_id == household_item.id,
                HouseholdItemAlias.merchant_normalized == merchant_key,
                HouseholdItemAlias.normalized_alias == normalized,
                HouseholdItemAlias.voided_at.is_(None),
            )
        ).scalars()
        for alias in aliases:
            alias.voided_at = utc_now()

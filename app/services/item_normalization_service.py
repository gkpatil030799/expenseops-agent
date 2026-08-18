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
    ambiguous: bool = False
    runner_up_confidence: float = 0.0


class ItemNormalizationService:
    def __init__(self, db: Session):
        self.db = db
        self.workspace_id = db.info.get("workspace_id")

    def match(self, raw_name: str, merchant: str | None = None) -> ItemMatch:
        normalized = normalize_item_name(raw_name)
        if not normalized:
            return ItemMatch(None, 0.0, "empty")
        merchant_key = normalize_merchant(merchant)
        alias_query = (
            select(HouseholdItemAlias)
            .join(HouseholdItem, HouseholdItem.id == HouseholdItemAlias.household_item_id)
            .where(
                HouseholdItemAlias.voided_at.is_(None),
                HouseholdItem.enabled.is_(True),
            )
        )
        if self.workspace_id is not None:
            alias_query = alias_query.where(HouseholdItem.workspace_id == self.workspace_id)
        aliases = list(self.db.scalars(alias_query))
        matching_aliases = [
            alias
            for alias in aliases
            if alias.normalized_alias == normalized
            and (not alias.merchant_normalized or alias.merchant_normalized == merchant_key)
        ]
        alias_item_ids = {alias.household_item_id for alias in matching_aliases}
        if len(alias_item_ids) == 1:
            alias = max(matching_aliases, key=lambda candidate: candidate.confidence)
            return ItemMatch(alias.household_item, min(1.0, alias.confidence), "alias")
        if len(alias_item_ids) > 1:
            return ItemMatch(
                None,
                max(min(1.0, alias.confidence) for alias in matching_aliases),
                "alias_conflict",
                ambiguous=True,
            )

        item_query = select(HouseholdItem).where(HouseholdItem.enabled.is_(True))
        if self.workspace_id is not None:
            item_query = item_query.where(HouseholdItem.workspace_id == self.workspace_id)
        items = list(self.db.scalars(item_query))
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
        scored.sort(key=lambda pair: (-pair[0], pair[1].id))
        score, item = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        ambiguous = runner_up >= 0.65 and score - runner_up < 0.12
        return ItemMatch(
            item if score >= 0.65 and not ambiguous else None,
            round(score, 4),
            "name_similarity",
            ambiguous=ambiguous,
            runner_up_confidence=round(runner_up, 4),
        )

    def learn_alias(
        self,
        household_item: HouseholdItem,
        raw_pattern: str,
        *,
        merchant: str | None = None,
        source: str = "user",
        confidence: float = 1.0,
    ) -> HouseholdItemAlias:
        if self.workspace_id is not None and household_item.workspace_id != self.workspace_id:
            raise ValueError("Household item not found.")
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
            if source in {"confirmed_receipt", "user", "user_correction"}:
                existing.source = source
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
        sources: frozenset[str] | None = None,
    ) -> None:
        if self.workspace_id is not None and household_item.workspace_id != self.workspace_id:
            raise ValueError("Household item not found.")
        normalized = normalize_item_name(raw_pattern)
        merchant_key = normalize_merchant(merchant)
        statement = select(HouseholdItemAlias).where(
                HouseholdItemAlias.household_item_id == household_item.id,
                HouseholdItemAlias.merchant_normalized == merchant_key,
                HouseholdItemAlias.normalized_alias == normalized,
                HouseholdItemAlias.voided_at.is_(None),
            )
        if sources is not None:
            statement = statement.where(HouseholdItemAlias.source.in_(sources))
        aliases = self.db.execute(statement).scalars()
        for alias in aliases:
            alias.voided_at = utc_now()

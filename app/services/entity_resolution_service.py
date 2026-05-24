from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Literal

from app.services.agent_service import friend_display_name

EntityKind = Literal["person", "group"]

NOISE_WORDS = {
    "and",
    "with",
    "between",
    "split",
    "equally",
    "equal",
    "group",
    "in",
    "the",
    "this",
    "expense",
    "transaction",
}


@dataclass(frozen=True)
class ResolvedEntity:
    mention: str
    entity_id: int | str
    display_name: str
    entity: dict[str, Any]
    confidence: float
    kind: EntityKind
    source: str = "global"


@dataclass(frozen=True)
class AmbiguousEntity:
    mention: str
    candidates: list[ResolvedEntity]
    kind: EntityKind


@dataclass(frozen=True)
class EntityResolutionResult:
    resolved: list[ResolvedEntity] = field(default_factory=list)
    ambiguous: list[AmbiguousEntity] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.ambiguous and not self.unresolved


class EntityResolutionService:
    def resolve_group_mentions(
        self,
        mentions: list[str],
        groups: list[dict[str, Any]],
    ) -> EntityResolutionResult:
        return self._resolve_mentions(
            mentions=mentions,
            entities=groups,
            kind="group",
            source="group",
            display_name=lambda group: str(group.get("name") or group.get("id")),
        )

    def resolve_person_mentions(
        self,
        mentions: list[str],
        friends: list[dict[str, Any]],
        *,
        payer: dict[str, Any] | None = None,
        group_members: list[dict[str, Any]] | None = None,
    ) -> EntityResolutionResult:
        resolved: list[ResolvedEntity] = []
        ambiguous: list[AmbiguousEntity] = []
        unresolved: list[str] = []

        for mention in _clean_mentions(mentions):
            if _is_me(mention):
                if payer:
                    resolved.append(
                        ResolvedEntity(
                            mention=mention,
                            entity_id=int(payer["id"]),
                            display_name=friend_display_name(payer),
                            entity=payer,
                            confidence=1.0,
                            kind="person",
                            source="payer",
                        )
                    )
                else:
                    unresolved.append(mention)
                continue

            group_candidates = _rank_candidates(
                mention,
                group_members or [],
                kind="person",
                source="group_member",
                display_name=friend_display_name,
            )
            friend_candidates = _rank_candidates(
                mention,
                friends,
                kind="person",
                source="friend",
                display_name=friend_display_name,
            )
            candidates = _dedupe_candidates([*group_candidates, *friend_candidates])
            selected = _select_candidate(candidates)
            if selected:
                resolved.append(selected)
            elif candidates:
                ambiguous.append(
                    AmbiguousEntity(mention=mention, candidates=candidates, kind="person")
                )
            else:
                unresolved.append(mention)

        return EntityResolutionResult(
            resolved=resolved,
            ambiguous=ambiguous,
            unresolved=unresolved,
        )

    def resolve_people_within_group(
        self,
        mentions: list[str],
        group_members: list[dict[str, Any]],
        *,
        payer: dict[str, Any] | None = None,
        all_friends: list[dict[str, Any]] | None = None,
    ) -> EntityResolutionResult:
        return self.resolve_person_mentions(
            mentions,
            all_friends or [],
            payer=payer,
            group_members=group_members,
        )

    def _resolve_mentions(
        self,
        *,
        mentions: list[str],
        entities: list[dict[str, Any]],
        kind: EntityKind,
        source: str,
        display_name,
    ) -> EntityResolutionResult:
        resolved: list[ResolvedEntity] = []
        ambiguous: list[AmbiguousEntity] = []
        unresolved: list[str] = []

        for mention in _clean_mentions(mentions):
            candidates = _rank_candidates(
                mention,
                entities,
                kind=kind,
                source=source,
                display_name=display_name,
            )
            selected = _select_candidate(candidates)
            if selected:
                resolved.append(selected)
            elif candidates:
                ambiguous.append(
                    AmbiguousEntity(mention=mention, candidates=candidates, kind=kind)
                )
            else:
                unresolved.append(mention)

        return EntityResolutionResult(
            resolved=resolved,
            ambiguous=ambiguous,
            unresolved=unresolved,
        )


def normalize_mention(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    tokens = [token for token in normalized.split() if token and token not in NOISE_WORDS]
    return " ".join(tokens)


def _clean_mentions(mentions: list[str]) -> list[str]:
    cleaned = []
    for mention in mentions:
        normalized = normalize_mention(str(mention))
        if normalized:
            cleaned.append(normalized)
    return cleaned


def _is_me(mention: str) -> bool:
    return normalize_mention(mention) in {"me", "myself", "you", "payer"}


def _rank_candidates(
    mention: str,
    entities: list[dict[str, Any]],
    *,
    kind: EntityKind,
    source: str,
    display_name,
) -> list[ResolvedEntity]:
    ranked = []
    normalized_mention = normalize_mention(mention)
    for entity in entities:
        try:
            entity_id = int(entity["id"])
        except (KeyError, TypeError, ValueError):
            continue
        name = display_name(entity)
        normalized_name = normalize_mention(name)
        if not normalized_name:
            continue
        score = _similarity(normalized_mention, normalized_name)
        if normalized_mention in normalized_name.split():
            score = max(score, 0.92)
        if normalized_mention and normalized_mention in normalized_name:
            score = max(score, 0.9)
        if score < 0.65:
            continue
        ranked.append(
            ResolvedEntity(
                mention=mention,
                entity_id=entity_id,
                display_name=name,
                entity=entity,
                confidence=round(score, 4),
                kind=kind,
                source=source,
            )
        )
    return sorted(
        ranked,
        key=lambda item: (
            item.source == "group_member",
            item.confidence,
        ),
        reverse=True,
    )


def _select_candidate(candidates: list[ResolvedEntity]) -> ResolvedEntity | None:
    if not candidates:
        return None
    if len(candidates) == 1 and candidates[0].confidence >= 0.72:
        return candidates[0]

    top = candidates[0]
    second = candidates[1] if len(candidates) > 1 else None
    if top.source == "group_member" and top.confidence >= 0.9:
        group_ties = [
            candidate
            for candidate in candidates
            if candidate.source == "group_member" and candidate.confidence >= 0.9
        ]
        if len(group_ties) == 1:
            return top
    if top.confidence >= 0.94 and (second is None or top.confidence - second.confidence >= 0.08):
        return top
    return None


def _dedupe_candidates(candidates: list[ResolvedEntity]) -> list[ResolvedEntity]:
    by_id: dict[int | str, ResolvedEntity] = {}
    for candidate in candidates:
        existing = by_id.get(candidate.entity_id)
        if existing is None or (
            candidate.source == "group_member",
            candidate.confidence,
        ) > (
            existing.source == "group_member",
            existing.confidence,
        ):
            by_id[candidate.entity_id] = candidate
    return sorted(
        by_id.values(),
        key=lambda item: (
            item.source == "group_member",
            item.confidence,
        ),
        reverse=True,
    )


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    compact_left = left.replace(" ", "")
    compact_right = right.replace(" ", "")
    token_scores = [SequenceMatcher(None, left, token).ratio() for token in right.split()]
    return max(
        SequenceMatcher(None, left, right).ratio(),
        SequenceMatcher(None, compact_left, compact_right).ratio(),
        *token_scores,
    )

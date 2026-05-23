from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

ConversationAction = Literal["personal", "split_people", "split_group", "unknown"]


@dataclass(frozen=True)
class ParsedConversationIntent:
    action: ConversationAction
    group_name: str | None = None
    participant_names: list[str] = field(default_factory=list)


def parse_conversational_split(text: str) -> ParsedConversationIntent:
    normalized = " ".join(text.strip().split())
    lowered = normalized.lower()
    if not lowered:
        return ParsedConversationIntent(action="unknown")

    if lowered in {"personal", "mark personal", "mine", "mark as personal"}:
        return ParsedConversationIntent(action="personal")

    group_match = re.fullmatch(
        r"split\s+in\s+(?P<group>.+?)\s+with\s+(?P<people>.+)",
        normalized,
        flags=re.IGNORECASE,
    )
    if group_match:
        return ParsedConversationIntent(
            action="split_group",
            group_name=group_match.group("group").strip(),
            participant_names=_parse_names(group_match.group("people")),
        )

    people_match = re.fullmatch(
        r"split\s+with\s+(?P<people>.+)",
        normalized,
        flags=re.IGNORECASE,
    )
    if people_match:
        return ParsedConversationIntent(
            action="split_people",
            participant_names=_parse_names(people_match.group("people")),
        )

    return ParsedConversationIntent(action="unknown")


def _parse_names(value: str) -> list[str]:
    normalized = re.sub(r"\s+and\s+", ",", value.strip(), flags=re.IGNORECASE)
    return [part.strip() for part in normalized.split(",") if part.strip()]

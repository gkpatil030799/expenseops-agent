from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

CustomSplitAction = Literal["custom_split", "unknown"]
CustomSplitMode = Literal["equal", "exact_amounts", "percentages", "shares"]


@dataclass(frozen=True)
class ParsedCustomSplit:
    action: CustomSplitAction
    split_mode: CustomSplitMode | None = None
    payer_included: bool = True
    participant_names: list[str] = field(default_factory=list)
    values_by_name: dict[str, Decimal] = field(default_factory=dict)


def parse_custom_split_text(text: str) -> ParsedCustomSplit:
    normalized = " ".join(text.strip().split())
    lowered = normalized.lower()
    if not lowered.startswith("split"):
        return ParsedCustomSplit(action="unknown")

    payer_included = "exclude me" not in lowered
    equal_match = re.fullmatch(
        r"split\s+with\s+(?P<people>.+?)(?:,\s*exclude\s+me)?",
        normalized,
        flags=re.IGNORECASE,
    )
    if equal_match:
        return ParsedCustomSplit(
            action="custom_split",
            split_mode="equal",
            payer_included=payer_included,
            participant_names=_parse_names(equal_match.group("people")),
        )

    exact_parts = re.findall(
        (
            r"(?P<amount>\d+(?:\.\d{1,2})?)\s+with\s+"
            r"(?P<name>[A-Za-z][A-Za-z .'-]*?)(?=\s+and\s+\d|\s*$)"
        ),
        normalized,
        flags=re.IGNORECASE,
    )
    if exact_parts:
        values = {name.strip(): Decimal(amount) for amount, name in exact_parts}
        return ParsedCustomSplit(
            action="custom_split",
            split_mode="exact_amounts",
            payer_included=payer_included,
            participant_names=list(values),
            values_by_name=values,
        )

    percentage_parts = re.findall(
        (
            r"(?P<percentage>\d+(?:\.\d+)?)%\s+"
            r"(?P<name>[A-Za-z][A-Za-z .'-]*?)(?=\s+\d+(?:\.\d+)?%|\s*$)"
        ),
        normalized,
        flags=re.IGNORECASE,
    )
    if percentage_parts:
        values = {name.strip(): Decimal(percentage) for percentage, name in percentage_parts}
        return ParsedCustomSplit(
            action="custom_split",
            split_mode="percentages",
            payer_included=payer_included,
            participant_names=list(values),
            values_by_name=values,
        )

    if lowered.startswith("split shares "):
        shares_text = normalized[len("split shares ") :]
        share_parts = re.findall(
            r"(?P<name>[A-Za-z][A-Za-z .'-]*?)\s+(?P<shares>\d+(?:\.\d+)?)(?=\s+[A-Za-z]|\s*$)",
            shares_text,
            flags=re.IGNORECASE,
        )
        if share_parts:
            values = {name.strip(): Decimal(shares) for name, shares in share_parts}
            return ParsedCustomSplit(
                action="custom_split",
                split_mode="shares",
                payer_included=payer_included,
                participant_names=list(values),
                values_by_name=values,
            )

    return ParsedCustomSplit(action="unknown")


def _parse_names(value: str) -> list[str]:
    value = re.sub(r",\s*exclude\s+me\s*$", "", value, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+and\s+", ",", value.strip(), flags=re.IGNORECASE)
    return [part.strip() for part in normalized.split(",") if part.strip()]

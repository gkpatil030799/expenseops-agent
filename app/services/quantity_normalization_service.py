from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizedQuantity:
    quantity: float | None
    unit: str | None
    package_size: float | None
    confidence: float | None


_UNIT_RULES: dict[str, tuple[str, float]] = {
    "count": ("count", 1.0),
    "ct": ("count", 1.0),
    "each": ("count", 1.0),
    "egg": ("count", 1.0),
    "eggs": ("count", 1.0),
    "roll": ("roll", 1.0),
    "rolls": ("roll", 1.0),
    "gallon": ("fluid_ounce", 128.0),
    "gallons": ("fluid_ounce", 128.0),
    "gal": ("fluid_ounce", 128.0),
    "fl oz": ("fluid_ounce", 1.0),
    "fluid ounce": ("fluid_ounce", 1.0),
    "fluid ounces": ("fluid_ounce", 1.0),
    "liter": ("milliliter", 1000.0),
    "liters": ("milliliter", 1000.0),
    "l": ("milliliter", 1000.0),
    "ml": ("milliliter", 1.0),
    "milliliter": ("milliliter", 1.0),
    "milliliters": ("milliliter", 1.0),
    "lb": ("ounce", 16.0),
    "lbs": ("ounce", 16.0),
    "pound": ("ounce", 16.0),
    "pounds": ("ounce", 16.0),
    "oz": ("ounce", 1.0),
    "ounce": ("ounce", 1.0),
    "ounces": ("ounce", 1.0),
}


def normalize_quantity(
    quantity: float | None,
    unit: str | None,
    *,
    source_confidence: float | None,
) -> NormalizedQuantity:
    if quantity is None or quantity <= 0 or not unit:
        return NormalizedQuantity(None, None, None, None)
    confidence = min(1.0, max(0.0, source_confidence or 0.0))
    if confidence < 0.8:
        return NormalizedQuantity(None, None, None, None)
    key = " ".join(unit.casefold().strip().split())
    rule = _UNIT_RULES.get(key)
    if rule is None:
        # "pack" does not reveal what is inside the package and is intentionally unknown.
        return NormalizedQuantity(None, None, None, None)
    normalized_unit, multiplier = rule
    normalized = round(quantity * multiplier, 6)
    return NormalizedQuantity(
        quantity=normalized,
        unit=normalized_unit,
        package_size=normalized,
        confidence=confidence,
    )

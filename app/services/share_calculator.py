from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Literal


@dataclass(frozen=True)
class SplitShare:
    user_id: int
    paid_cents: int
    owed_cents: int


SplitMode = Literal["equal", "exact_amounts", "percentages", "shares"]


@dataclass(frozen=True)
class CustomSplitInput:
    user_id: int
    amount_cents: int | None = None
    percentage: Decimal | None = None
    shares: Decimal | None = None


def decimal_to_cents(value: Decimal | str | int | float) -> int:
    decimal_value = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(decimal_value * 100)


def cents_to_decimal_string(cents: int) -> str:
    return f"{Decimal(cents) / Decimal(100):.2f}"


def equal_owed_cents(total_cents: int, participant_count: int) -> list[int]:
    if total_cents <= 0:
        raise ValueError("total_cents must be positive")
    if participant_count <= 0:
        raise ValueError("participant_count must be positive")

    base = total_cents // participant_count
    remainder = total_cents % participant_count
    return [base + (1 if index < remainder else 0) for index in range(participant_count)]


def build_equal_split_shares(
    *, total_cents: int, payer_user_id: int, participant_user_ids: list[int]
) -> list[SplitShare]:
    if payer_user_id not in participant_user_ids:
        participant_user_ids = [payer_user_id, *participant_user_ids]

    seen: set[int] = set()
    ordered_user_ids: list[int] = []
    for user_id in participant_user_ids:
        if user_id not in seen:
            seen.add(user_id)
            ordered_user_ids.append(user_id)

    owed = equal_owed_cents(total_cents, len(ordered_user_ids))
    shares = [
        SplitShare(
            user_id=user_id,
            paid_cents=total_cents if user_id == payer_user_id else 0,
            owed_cents=owed[index],
        )
        for index, user_id in enumerate(ordered_user_ids)
    ]
    validate_shares(total_cents, shares)
    return shares


def build_custom_split_shares(
    *, total_cents: int, payer_user_id: int, owed_by_user_id: dict[int, int]
) -> list[SplitShare]:
    if payer_user_id not in owed_by_user_id:
        owed_by_user_id[payer_user_id] = 0
    shares = [
        SplitShare(
            user_id=user_id,
            paid_cents=total_cents if user_id == payer_user_id else 0,
            owed_cents=owed_cents,
        )
        for user_id, owed_cents in owed_by_user_id.items()
    ]
    validate_shares(total_cents, shares)
    return shares


def build_custom_split_shares_by_mode(
    *,
    total_cents: int,
    payer_user_id: int,
    payer_included: bool,
    split_mode: SplitMode,
    participant_splits: list[CustomSplitInput],
) -> list[SplitShare]:
    if total_cents <= 0:
        raise ValueError("total_cents must be positive")
    _validate_unique_participants(participant_splits)

    owed_by_user_id = _owed_cents_by_user(
        total_cents=total_cents,
        payer_user_id=payer_user_id,
        payer_included=payer_included,
        split_mode=split_mode,
        participant_splits=participant_splits,
    )
    if not payer_included:
        owed_by_user_id[payer_user_id] = 0
    elif payer_user_id not in owed_by_user_id:
        raise ValueError("payer must be included in participant_splits when payer_included is true")

    return build_custom_split_shares(
        total_cents=total_cents,
        payer_user_id=payer_user_id,
        owed_by_user_id=owed_by_user_id,
    )


def _owed_cents_by_user(
    *,
    total_cents: int,
    payer_user_id: int,
    payer_included: bool,
    split_mode: SplitMode,
    participant_splits: list[CustomSplitInput],
) -> dict[int, int]:
    if split_mode == "equal":
        user_ids = [split.user_id for split in participant_splits]
        if payer_included and payer_user_id not in user_ids:
            user_ids = [payer_user_id, *user_ids]
        if not user_ids:
            raise ValueError("at least one participant is required")
        return dict(zip(user_ids, equal_owed_cents(total_cents, len(user_ids)), strict=True))

    if not participant_splits:
        raise ValueError("at least one participant split is required")

    if split_mode == "exact_amounts":
        owed_by_user_id = {}
        for split in participant_splits:
            if split.amount_cents is None:
                raise ValueError("amount is required for exact_amounts split")
            if split.amount_cents < 0:
                raise ValueError("amount cannot be negative")
            owed_by_user_id[split.user_id] = split.amount_cents
        if sum(owed_by_user_id.values()) != total_cents:
            raise ValueError("exact amounts must match transaction amount")
        return owed_by_user_id

    if split_mode == "percentages":
        percentages = []
        for split in participant_splits:
            if split.percentage is None:
                raise ValueError("percentage is required for percentages split")
            if split.percentage < 0:
                raise ValueError("percentage cannot be negative")
            percentages.append(split.percentage)
        if sum(percentages, Decimal("0")) != Decimal("100"):
            raise ValueError("percentages must sum to 100")
        cents = _allocate_by_weights(total_cents, percentages)
        return {
            split.user_id: cents[index]
            for index, split in enumerate(participant_splits)
        }

    if split_mode == "shares":
        shares = []
        for split in participant_splits:
            if split.shares is None:
                raise ValueError("shares is required for shares split")
            if split.shares <= 0:
                raise ValueError("shares must be positive")
            shares.append(split.shares)
        cents = _allocate_by_weights(total_cents, shares)
        return {
            split.user_id: cents[index]
            for index, split in enumerate(participant_splits)
        }

    raise ValueError("unsupported split mode")


def _validate_unique_participants(participant_splits: list[CustomSplitInput]) -> None:
    user_ids = [split.user_id for split in participant_splits]
    if any(user_id <= 0 for user_id in user_ids):
        raise ValueError("participant user IDs must be positive")
    if len(set(user_ids)) != len(user_ids):
        raise ValueError("participant user IDs must be unique")


def _allocate_by_weights(total_cents: int, weights: list[Decimal]) -> list[int]:
    weight_total = sum(weights, Decimal("0"))
    if weight_total <= 0:
        raise ValueError("split weights must sum to a positive value")

    raw = [(Decimal(total_cents) * weight / weight_total) for weight in weights]
    floors = [int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in raw]
    remainder = total_cents - sum(floors)
    order = sorted(
        range(len(raw)),
        key=lambda index: raw[index] - Decimal(floors[index]),
        reverse=True,
    )
    for index in order[:remainder]:
        floors[index] += 1
    return floors


def validate_shares(total_cents: int, shares: list[SplitShare]) -> None:
    paid_total = sum(share.paid_cents for share in shares)
    owed_total = sum(share.owed_cents for share in shares)
    if any(share.paid_cents < 0 or share.owed_cents < 0 for share in shares):
        raise ValueError("shares cannot be negative")
    if len({share.user_id for share in shares}) != len(shares):
        raise ValueError("share user IDs must be unique")
    if paid_total != total_cents:
        raise ValueError(f"Paid shares must sum to {total_cents}, got {paid_total}")
    if owed_total != total_cents:
        raise ValueError(f"Owed shares must sum to {total_cents}, got {owed_total}")


def build_splitwise_by_shares_payload(
    *,
    total_cents: int,
    description: str,
    details: str | None,
    date_iso: str | None,
    currency_code: str,
    shares: list[SplitShare],
    group_id: int | None = None,
) -> dict[str, str | int | bool | None]:
    payload: dict[str, str | int | bool | None] = {
        "cost": cents_to_decimal_string(total_cents),
        "description": description,
        "details": details,
        "repeat_interval": "never",
        "currency_code": currency_code,
        "group_id": group_id or 0,
    }
    if date_iso:
        payload["date"] = date_iso

    for index, share in enumerate(shares):
        payload[f"users__{index}__user_id"] = share.user_id
        payload[f"users__{index}__paid_share"] = cents_to_decimal_string(share.paid_cents)
        payload[f"users__{index}__owed_share"] = cents_to_decimal_string(share.owed_cents)

    return payload

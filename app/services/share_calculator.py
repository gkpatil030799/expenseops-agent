from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class SplitShare:
    user_id: int
    paid_cents: int
    owed_cents: int


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


def validate_shares(total_cents: int, shares: list[SplitShare]) -> None:
    paid_total = sum(share.paid_cents for share in shares)
    owed_total = sum(share.owed_cents for share in shares)
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

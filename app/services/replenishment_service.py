from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    Errand,
    ErrandHouseholdItem,
    ErrandPriority,
    ErrandSource,
    ErrandStatus,
    ErrandType,
    HouseholdCadenceSource,
    HouseholdItem,
    ReplenishmentMode,
    utc_now,
)
from app.services.acquisition_service import AcquisitionService
from app.services.errand_service import ErrandService, HouseholdOpsNotFound


def due_score(
    last_acquired_at: datetime | None,
    cadence_days: int | None,
    *,
    now: datetime | None = None,
) -> float:
    if last_acquired_at is None or cadence_days is None:
        return 0.0
    if cadence_days <= 0:
        raise ValueError("cadence_days must be positive")
    current = _aware(now or utc_now())
    acquired = _aware(last_acquired_at)
    elapsed_days = max(0.0, (current - acquired).total_seconds() / 86_400)
    return round(min(1.0, elapsed_days / cadence_days), 4)


def due_state(score: float, *, has_history: bool = True) -> str:
    if not has_history:
        return "unknown"
    if score >= 0.9:
        return "likely_due"
    if score >= 0.7:
        return "probably_due"
    return "not_due"


class ReplenishmentService:
    def __init__(self, db: Session, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()

    def list_items(self) -> list[HouseholdItem]:
        return list(self.db.execute(select(HouseholdItem).order_by(HouseholdItem.name)).scalars())

    def get_item(self, item_id: int) -> HouseholdItem:
        item = self.db.get(HouseholdItem, item_id)
        if item is None:
            raise HouseholdOpsNotFound("Household item not found.")
        return item

    def create_item(self, values: dict) -> HouseholdItem:
        values.setdefault("cadence_source", HouseholdCadenceSource.CONFIGURED.value)
        item = HouseholdItem(**values)
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        return item

    def update_item(self, item_id: int, values: dict) -> HouseholdItem:
        item = self.get_item(item_id)
        if "cadence_days" in values:
            values["cadence_source"] = (
                HouseholdCadenceSource.CONFIGURED.value
                if values["cadence_days"] is not None
                else HouseholdCadenceSource.LEARNING.value
            )
        for field, value in values.items():
            setattr(item, field, value)
        item.updated_at = utc_now()
        self.db.commit()
        self.db.refresh(item)
        return item

    def delete_item(self, item_id: int) -> None:
        item = self.get_item(item_id)
        self.db.delete(item)
        self.db.commit()

    def mark_bought(self, item_id: int, *, now: datetime | None = None) -> HouseholdItem:
        item = self.get_item(item_id)
        current = now or utc_now()
        AcquisitionService(self.db).record(
            item,
            acquired_at=current,
            quantity=_float_or_none(item.quantity),
            unit=item.unit,
            source="manual_bought",
        )
        return item

    def still_have(
        self,
        item_id: int,
        *,
        snooze_days: int | None = None,
        now: datetime | None = None,
    ) -> HouseholdItem:
        item = self.get_item(item_id)
        current = now or utc_now()
        days = snooze_days or self.settings.household_snooze_days
        item.snoozed_until = current + timedelta(days=days)
        item.updated_at = current
        AcquisitionService(self.db).feedback(
            item,
            "still_have",
            occurred_at=current,
            metadata={"snooze_days": days},
            commit=False,
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def skip_once(self, item_id: int, *, now: datetime | None = None) -> HouseholdItem:
        item = self.still_have(item_id, now=now)
        AcquisitionService(self.db).feedback(item, "skipped", occurred_at=now or utc_now())
        return item

    def disable(self, item_id: int) -> HouseholdItem:
        return self.update_item(item_id, {"enabled": False})

    def should_surface(self, item: HouseholdItem, *, now: datetime | None = None) -> bool:
        current = _aware(now or utc_now())
        if not item.enabled or item.last_acquired_at is None:
            return False
        if item.snoozed_until is not None and _aware(item.snoozed_until) > current:
            return False
        return due_score(item.last_acquired_at, item.cadence_days, now=current) >= 0.7

    def due_items(self, *, now: datetime | None = None) -> list[HouseholdItem]:
        current = now or utc_now()
        items = self.list_items()
        return sorted(
            (item for item in items if self.should_surface(item, now=current)),
            key=lambda item: (
                -due_score(item.last_acquired_at, item.cadence_days, now=current),
                item.name.casefold(),
                item.id,
            ),
        )

    def active_linked_errand(self, item_id: int) -> Errand | None:
        return (
            self.db.execute(
                select(Errand)
                .join(ErrandHouseholdItem)
                .where(
                    ErrandHouseholdItem.household_item_id == item_id,
                    Errand.status.in_([ErrandStatus.OPEN.value, ErrandStatus.PLANNED.value]),
                )
                .order_by(Errand.id)
            )
            .scalars()
            .first()
        )

    def find_matching_trip(self, item: HouseholdItem) -> Errand | None:
        conditions = []
        if item.preferred_place_address:
            conditions.append(
                func.lower(Errand.place_address) == item.preferred_place_address.casefold()
            )
        if item.preferred_place_name:
            conditions.append(func.lower(Errand.place_name) == item.preferred_place_name.casefold())
        if not conditions:
            return None
        stmt = (
            select(Errand)
            .where(
                Errand.status.in_([ErrandStatus.OPEN.value, ErrandStatus.PLANNED.value]),
                Errand.included_in_next_plan.is_(True),
                or_(*conditions),
            )
            .order_by(Errand.due_at.is_(None), Errand.due_at, Errand.id)
        )
        return self.db.execute(stmt).scalars().first()

    def add_to_errands(self, item_id: int, errand_id: int | None = None) -> Errand:
        item = self.get_item(item_id)
        existing = self.active_linked_errand(item.id)
        if existing is not None:
            return ErrandService(self.db).get_errand(existing.id)
        target = (
            ErrandService(self.db).get_errand(errand_id)
            if errand_id
            else self.find_matching_trip(item)
        )
        if target is not None:
            return ErrandService(self.db).link_household_item(target, item)
        values = {
            "title": _purchase_title(item),
            "errand_type": ErrandType.PURCHASE.value,
            "place_name": item.preferred_place_name,
            "place_address": item.preferred_place_address,
            "estimated_duration_minutes": 10,
            "priority": ErrandPriority.NORMAL.value,
            "notes": "Created from Household Ops replenishment.",
            "included_in_next_plan": True,
        }
        return ErrandService(self.db).create_errand(
            values,
            source=ErrandSource.REPLENISHMENT.value,
            household_item=item,
        )

    def refresh(self, *, now: datetime | None = None) -> tuple[list[HouseholdItem], list[dict]]:
        due = self.due_items(now=now)
        suggestions: list[dict] = []
        for item in due:
            linked = self.active_linked_errand(item.id)
            if linked is not None:
                suggestions.append(_suggestion(item, "added_to_existing_stop", linked))
                continue
            matching = self.find_matching_trip(item)
            if matching is not None and item.replenishment_mode in {
                ReplenishmentMode.ERRAND.value,
                ReplenishmentMode.EITHER.value,
            }:
                linked = ErrandService(self.db).link_household_item(matching, item)
                suggestions.append(_suggestion(item, "added_to_existing_stop", linked))
            elif item.replenishment_mode == ReplenishmentMode.ERRAND.value:
                created = self.add_to_errands(item.id)
                suggestions.append(_suggestion(item, "created_errand", created))
            elif item.replenishment_mode == ReplenishmentMode.DELIVERY.value:
                suggestions.append(_suggestion(item, "delivery_only", None))
            else:
                suggestions.append(_suggestion(item, "due_no_trip", None))
        return due, suggestions

    def to_dict(self, item: HouseholdItem, *, now: datetime | None = None) -> dict:
        score = due_score(item.last_acquired_at, item.cadence_days, now=now)
        linked = self.active_linked_errand(item.id)
        return {
            "id": item.id,
            "name": item.name,
            "quantity": item.quantity,
            "unit": item.unit,
            "preferred_place_name": item.preferred_place_name,
            "preferred_place_address": item.preferred_place_address,
            "replenishment_mode": item.replenishment_mode,
            "cadence_days": item.cadence_days,
            "cadence_source": item.cadence_source,
            "last_acquired_at": item.last_acquired_at,
            "snoozed_until": item.snoozed_until,
            "enabled": item.enabled,
            "notes": item.notes,
            "due_score": score,
            "due_state": due_state(
                score,
                has_history=item.last_acquired_at is not None and item.cadence_days is not None,
            ),
            "should_surface": self.should_surface(item, now=now),
            "linked_errand_id": linked.id if linked else None,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }


def _purchase_title(item: HouseholdItem) -> str:
    quantity = " ".join(part for part in [item.quantity, item.unit] if part)
    return f"Buy {quantity + ' ' if quantity else ''}{item.name}".strip()


def _suggestion(item: HouseholdItem, action: str, errand: Errand | None) -> dict:
    place = item.preferred_place_name or item.preferred_place_address or "your next trip"
    messages = {
        "created_errand": f"Added {item.name} as a new errand for {place}.",
        "added_to_existing_stop": (
            f"You're already going to {place}. Added {item.name} to that stop."
        ),
        "delivery_only": f"{item.name} is due for delivery. Ordering is not automated.",
        "due_no_trip": f"{item.name} is due, but there is no matching {place} trip yet.",
    }
    return {
        "household_item_id": item.id,
        "household_item_name": item.name,
        "action": action,
        "errand_id": errand.id if errand else None,
        "message": messages[action],
    }


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _float_or_none(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None

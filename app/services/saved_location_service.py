from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SavedLocation, SavedLocationType, utc_now
from app.services.errand_service import HouseholdOpsError, HouseholdOpsNotFound


class SavedLocationService:
    def __init__(self, db: Session):
        self.db = db

    def list_locations(self) -> list[SavedLocation]:
        order = {
            SavedLocationType.HOME.value: 0,
            SavedLocationType.WORK.value: 1,
            SavedLocationType.CUSTOM.value: 2,
        }
        locations = list(self.db.execute(select(SavedLocation)).scalars())
        return sorted(
            locations,
            key=lambda item: (order.get(item.location_type, 2), item.label, item.id),
        )

    def get_location(self, location_id: int) -> SavedLocation:
        location = self.db.get(SavedLocation, location_id)
        if location is None:
            raise HouseholdOpsNotFound("Saved location not found.")
        return location

    def create_location(self, values: dict) -> SavedLocation:
        self._ensure_singleton(values["location_type"])
        location = SavedLocation(**values)
        self.db.add(location)
        self.db.commit()
        self.db.refresh(location)
        return location

    def update_location(self, location_id: int, values: dict) -> SavedLocation:
        location = self.get_location(location_id)
        location_type = values.get("location_type", location.location_type)
        self._ensure_singleton(location_type, excluding_id=location.id)
        for field, value in values.items():
            setattr(location, field, value)
        location.updated_at = utc_now()
        self.db.commit()
        self.db.refresh(location)
        return location

    def delete_location(self, location_id: int) -> None:
        location = self.get_location(location_id)
        self.db.delete(location)
        self.db.commit()

    def _ensure_singleton(self, location_type: str, excluding_id: int | None = None) -> None:
        if location_type not in {SavedLocationType.HOME.value, SavedLocationType.WORK.value}:
            return
        stmt = select(SavedLocation.id).where(SavedLocation.location_type == location_type)
        if excluding_id is not None:
            stmt = stmt.where(SavedLocation.id != excluding_id)
        if self.db.execute(stmt.limit(1)).scalar_one_or_none() is not None:
            raise HouseholdOpsError(f"Only one active {location_type.title()} location is allowed.")

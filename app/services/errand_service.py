from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Errand,
    ErrandHouseholdItem,
    ErrandSource,
    ErrandStatus,
    HouseholdItem,
    utc_now,
)
from app.services.managed_auth_service import record_audit_once


class HouseholdOpsError(RuntimeError):
    pass


class HouseholdOpsNotFound(HouseholdOpsError):
    pass


class ErrandService:
    def __init__(self, db: Session):
        self.db = db

    def list_errands(self, status: str | None = None) -> list[Errand]:
        stmt = (
            select(Errand)
            .options(
                selectinload(Errand.household_links).selectinload(
                    ErrandHouseholdItem.household_item
                )
            )
            .order_by(desc(Errand.created_at), desc(Errand.id))
        )
        if status:
            stmt = stmt.where(Errand.status == status)
        return list(self.db.execute(stmt).scalars())

    def get_errand(self, errand_id: int) -> Errand:
        stmt = (
            select(Errand)
            .where(Errand.id == errand_id)
            .options(
                selectinload(Errand.household_links).selectinload(
                    ErrandHouseholdItem.household_item
                )
            )
        )
        errand = self.db.execute(stmt).scalar_one_or_none()
        if errand is None:
            raise HouseholdOpsNotFound("Errand not found.")
        return errand

    def create_errand(
        self,
        values: dict,
        *,
        source: str = ErrandSource.MANUAL.value,
        household_item: HouseholdItem | None = None,
    ) -> Errand:
        errand = Errand(**values, source=source)
        self.db.add(errand)
        self.db.flush()
        if household_item is not None:
            self.db.add(
                ErrandHouseholdItem(errand_id=errand.id, household_item_id=household_item.id)
            )
        record_audit_once(
            self.db,
            event_type="first_errand_created",
            resource_type="errand",
            resource_id=str(errand.id),
        )
        self.db.commit()
        return self.get_errand(errand.id)

    def update_errand(self, errand_id: int, values: dict) -> Errand:
        errand = self.get_errand(errand_id)
        location_changed = any(
            field in values and values[field] != getattr(errand, field)
            for field in ("title", "place_name", "place_address")
        )
        for field, value in values.items():
            setattr(errand, field, value)
        if location_changed:
            from app.services.place_resolution_service import PlaceResolutionService

            PlaceResolutionService(self.db).clear_resolution(errand)
        errand.updated_at = utc_now()
        self.db.commit()
        return self.get_errand(errand.id)

    def delete_errand(self, errand_id: int) -> None:
        errand = self.get_errand(errand_id)
        self.db.delete(errand)
        self.db.commit()

    def complete_errand(
        self,
        errand_id: int,
        *,
        mark_linked_items_acquired: bool,
        acquired_at: datetime | None = None,
    ) -> Errand:
        errand = self.get_errand(errand_id)
        now = acquired_at or utc_now()
        errand.status = ErrandStatus.COMPLETED.value
        errand.updated_at = now
        if mark_linked_items_acquired:
            for link in errand.household_links:
                link.household_item.last_acquired_at = now
                link.household_item.snoozed_until = None
                link.household_item.updated_at = now
        self.db.commit()
        return self.get_errand(errand.id)

    def skip_errand(self, errand_id: int) -> Errand:
        return self.update_errand(errand_id, {"status": ErrandStatus.SKIPPED.value})

    def link_household_item(self, errand: Errand, item: HouseholdItem) -> Errand:
        existing = self.db.execute(
            select(ErrandHouseholdItem).where(
                ErrandHouseholdItem.errand_id == errand.id,
                ErrandHouseholdItem.household_item_id == item.id,
            )
        ).scalar_one_or_none()
        if existing is None:
            self.db.add(ErrandHouseholdItem(errand_id=errand.id, household_item_id=item.id))
            errand.updated_at = utc_now()
            self.db.commit()
        return self.get_errand(errand.id)


def errand_to_dict(errand: Errand) -> dict:
    return {
        "id": errand.id,
        "title": errand.title,
        "errand_type": errand.errand_type,
        "place_name": errand.place_name,
        "place_address": errand.place_address,
        "place_resolution_status": errand.place_resolution_status,
        "resolved_place_name": errand.resolved_place_name,
        "resolved_place_address": errand.resolved_place_address,
        "resolved_latitude": errand.resolved_latitude,
        "resolved_longitude": errand.resolved_longitude,
        "resolved_provider_place_id": errand.resolved_provider_place_id,
        "resolved_open_now": errand.resolved_open_now,
        "resolved_opening_hours": errand.resolved_opening_hours,
        "place_resolution_method": errand.place_resolution_method,
        "due_at": errand.due_at,
        "estimated_duration_minutes": errand.estimated_duration_minutes,
        "priority": errand.priority,
        "status": errand.status,
        "source": errand.source,
        "notes": errand.notes,
        "included_in_next_plan": errand.included_in_next_plan,
        "linked_household_items": [
            {
                "id": link.household_item.id,
                "name": link.household_item.name,
                "quantity": link.household_item.quantity,
                "unit": link.household_item.unit,
            }
            for link in errand.household_links
        ],
        "created_at": errand.created_at,
        "updated_at": errand.updated_at,
    }

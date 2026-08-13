from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ScheduledJobLease, utc_now


def acquire_job_lease(
    db: Session,
    *,
    workspace_id: int,
    job_name: str,
    lease_seconds: int = 900,
) -> str | None:
    now = utc_now()
    token = secrets.token_hex(16)
    current = db.scalar(
        select(ScheduledJobLease).where(
            ScheduledJobLease.workspace_id == workspace_id,
            ScheduledJobLease.job_name == job_name,
        )
    )
    if current is None:
        try:
            with db.begin_nested():
                db.add(
                    ScheduledJobLease(
                        workspace_id=workspace_id,
                        job_name=job_name,
                        lease_token=token,
                        lease_expires_at=now + timedelta(seconds=lease_seconds),
                        acquired_at=now,
                        updated_at=now,
                    )
                )
                db.flush()
            return token
        except IntegrityError:
            current = db.scalar(
                select(ScheduledJobLease).where(
                    ScheduledJobLease.workspace_id == workspace_id,
                    ScheduledJobLease.job_name == job_name,
                )
            )
    result = db.execute(
        update(ScheduledJobLease)
        .where(
            ScheduledJobLease.workspace_id == workspace_id,
            ScheduledJobLease.job_name == job_name,
            or_(
                ScheduledJobLease.lease_expires_at <= now,
                ScheduledJobLease.lease_token == token,
            ),
        )
        .values(
            lease_token=token,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            acquired_at=now,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    return token if result.rowcount == 1 else None


def release_job_lease(
    db: Session,
    *,
    workspace_id: int,
    job_name: str,
    lease_token: str,
) -> bool:
    now = utc_now()
    result = db.execute(
        update(ScheduledJobLease)
        .where(
            ScheduledJobLease.workspace_id == workspace_id,
            ScheduledJobLease.job_name == job_name,
            ScheduledJobLease.lease_token == lease_token,
        )
        .values(lease_expires_at=now, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount == 1

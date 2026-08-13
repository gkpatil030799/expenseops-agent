from __future__ import annotations

from datetime import timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import ScheduledJobLease, User, Workspace, WorkspaceMembership, utc_now
from app.services.job_lease_service import acquire_job_lease, release_job_lease
from app.tenancy import TenantContext, set_session_tenant


def test_scheduler_lease_blocks_overlap_and_expiry_recovers(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'leases.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        user = User(email="owner@example.test", display_name="Owner")
        db.add(user)
        db.flush()
        workspace = Workspace(name="Household", created_by_user_id=user.id)
        db.add(workspace)
        db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role="owner",
            )
        )
        db.commit()
        context = TenantContext(user.id, workspace.id)

        set_session_tenant(db, context)
        first = acquire_job_lease(
            db, workspace_id=workspace.id, job_name="gmail_receipts_sync"
        )
        assert first is not None
        db.commit()

    with factory() as concurrent:
        set_session_tenant(concurrent, context)
        assert (
            acquire_job_lease(
                concurrent,
                workspace_id=workspace.id,
                job_name="gmail_receipts_sync",
            )
            is None
        )
        lease = concurrent.scalar(select(ScheduledJobLease))
        lease.lease_expires_at = utc_now() - timedelta(seconds=1)
        concurrent.commit()
        recovered = acquire_job_lease(
            concurrent,
            workspace_id=workspace.id,
            job_name="gmail_receipts_sync",
        )
        assert recovered is not None
        assert recovered != first
        concurrent.commit()
        assert release_job_lease(
            concurrent,
            workspace_id=workspace.id,
            job_name="gmail_receipts_sync",
            lease_token=recovered,
        )
    engine.dispose()

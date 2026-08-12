from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.config import get_settings
from app.models import AuditEvent, User, Workspace, WorkspaceMembership

router = APIRouter(prefix="/api/admin", tags=["admin"])

FUNNEL_EVENTS = (
    "user_first_login",
    "workspace_created",
    "gmail_connected",
    "telegram_connected",
    "onboarding_completed",
)
WORKFLOW_EVENTS = (
    "first_receipt_processed",
    "first_promotion_processed",
    "first_replenishment_recommendation",
    "first_errand_created",
)


@router.get("/onboarding-funnel")
def onboarding_funnel(db: DbSession, user: CurrentUser) -> dict:
    allowed = {email.casefold() for email in get_settings().admin_user_emails}
    if user.email.casefold() not in allowed:
        raise HTTPException(status_code=404, detail="Not found")
    event_types = (*FUNNEL_EVENTS, *WORKFLOW_EVENTS)
    counts = dict(
        db.execute(
            select(AuditEvent.event_type, func.count(func.distinct(AuditEvent.user_id)))
            .execution_options(skip_tenant_scope=True)
            .where(AuditEvent.event_type.in_(event_types))
            .group_by(AuditEvent.event_type)
        ).all()
    )
    authenticated = db.scalar(
        select(func.count(func.distinct(AuditEvent.user_id)))
        .execution_options(skip_tenant_scope=True)
        .where(AuditEvent.event_type.in_(("login", "user_first_login")))
    )
    workflows = db.scalar(
        select(func.count(func.distinct(AuditEvent.user_id)))
        .execution_options(skip_tenant_scope=True)
        .where(AuditEvent.event_type.in_(WORKFLOW_EVENTS))
    )
    return {
        "authenticated_users": authenticated or 0,
        "users_with_workspace": db.scalar(
            select(func.count(func.distinct(WorkspaceMembership.user_id)))
        )
        or 0,
        "gmail_connected_users": counts.get("gmail_connected", 0),
        "telegram_connected_users": counts.get("telegram_connected", 0),
        "onboarding_completed_users": counts.get("onboarding_completed", 0),
        "users_with_successful_workflow": workflows or 0,
        "total_users": db.scalar(select(func.count(User.id))) or 0,
        "total_workspaces": db.scalar(select(func.count(Workspace.id))) or 0,
    }

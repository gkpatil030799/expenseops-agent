from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.config import get_settings
from app.models import (
    AuditEvent,
    FinancialOperation,
    GmailSyncCheckpoint,
    OutboxEvent,
    ScheduledJobLease,
    User,
    Workspace,
    WorkspaceMembership,
    utc_now,
)

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
    _require_admin(user)
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


@router.get("/operations")
def operational_status(db: DbSession, user: CurrentUser) -> dict:
    _require_admin(user)
    now = utc_now()
    pending_outbox = db.scalar(
        select(func.count(OutboxEvent.id)).where(OutboxEvent.state.in_(("pending", "processing")))
    ) or 0
    failed_outbox = db.scalar(
        select(func.count(OutboxEvent.id)).where(OutboxEvent.state == "dead_letter")
    ) or 0
    oldest_pending = db.scalar(
        select(func.min(OutboxEvent.created_at)).where(
            OutboxEvent.state.in_(("pending", "processing"))
        )
    )
    ambiguous_financial = db.scalar(
        select(func.count(FinancialOperation.id)).where(
            FinancialOperation.state.in_(("ambiguous", "failed", "dead_letter"))
        )
    ) or 0
    expired_leases = db.scalar(
        select(func.count(ScheduledJobLease.id)).where(ScheduledJobLease.lease_expires_at < now)
    ) or 0
    gmail_freshness = db.scalar(select(func.max(GmailSyncCheckpoint.updated_at)))
    queue_age_seconds = (
        max(0, int((now - _aware(oldest_pending)).total_seconds())) if oldest_pending else 0
    )
    status = "degraded" if failed_outbox or ambiguous_financial else "healthy"
    return {
        "status": status,
        "queue": {
            "pending": pending_outbox,
            "dead_letter": failed_outbox,
            "oldest_pending_age_seconds": queue_age_seconds,
        },
        "financial_operations": {"attention_required": ambiguous_financial},
        "scheduled_jobs": {"expired_leases": expired_leases},
        "gmail": {"latest_checkpoint_at": gmail_freshness},
        "alert_thresholds": {
            "queue_age_seconds": 300,
            "dead_letter_count": 1,
            "financial_attention_count": 1,
            "sync_freshness_seconds": 7200,
        },
    }


def _require_admin(user: User) -> None:
    allowed = {email.casefold() for email in get_settings().admin_user_emails}
    if user.email.casefold() not in allowed:
        raise HTTPException(status_code=404, detail="Not found")


def _aware(value):
    return value if value.tzinfo else value.replace(tzinfo=utc_now().tzinfo)

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.api.deps import CurrentUser, CurrentWorkspace, DbSession
from app.models import (
    AuthSession,
    User,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
    utc_now,
)
from app.rate_limit import rate_limiter
from app.services.managed_auth_service import record_audit
from app.tenancy import hash_api_token, set_trusted_workspace

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class WorkspaceRename(WorkspaceCreate):
    pass


class InvitationCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = "member"

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().casefold()
        if "@" not in value:
            raise ValueError("Enter a valid email")
        return value


class InvitationAccept(BaseModel):
    token: str = Field(min_length=20, max_length=500)


@router.get("")
def list_workspaces(db: DbSession, user: CurrentUser, current: CurrentWorkspace) -> list[dict]:
    rows = db.execute(
        select(Workspace, WorkspaceMembership)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
        .where(WorkspaceMembership.user_id == user.id)
        .order_by(Workspace.name)
    ).all()
    return [
        {
            "id": workspace.id,
            "name": workspace.name,
            "workspace_type": workspace.workspace_type,
            "role": membership.role,
            "current": workspace.id == current.id,
        }
        for workspace, membership in rows
    ]


@router.post("", status_code=201)
def create_workspace(
    payload: WorkspaceCreate,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    workspace = Workspace(
        name=payload.name.strip(), workspace_type="household", created_by_user_id=user.id
    )
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role="owner",
            is_default=False,
        )
    )
    record_audit(
        db,
        workspace_id=workspace.id,
        user_id=user.id,
        event_type="workspace_created",
        resource_type="workspace",
        resource_id=str(workspace.id),
    )
    db.commit()
    return {"id": workspace.id, "name": workspace.name, "role": "owner"}


@router.patch("/{workspace_id}")
def rename_workspace(
    workspace_id: int,
    payload: WorkspaceRename,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    membership = _membership(db, user.id, workspace_id)
    if membership.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    workspace = db.get(Workspace, workspace_id)
    workspace.name = payload.name.strip()
    workspace.updated_at = utc_now()
    db.commit()
    return {"id": workspace.id, "name": workspace.name}


@router.post("/{workspace_id}/switch")
def switch_workspace(
    workspace_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    _membership(db, user.id, workspace_id)
    for membership in db.scalars(
        select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)
    ):
        membership.is_default = membership.workspace_id == workspace_id
    session_id = getattr(request.state, "auth_session_id", None)
    if session_id is not None:
        auth_session = db.get(AuthSession, session_id)
        if auth_session is not None and auth_session.user_id == user.id:
            auth_session.selected_workspace_id = workspace_id
    record_audit(
        db,
        workspace_id=workspace_id,
        user_id=user.id,
        event_type="workspace_switched",
        resource_type="workspace",
        resource_id=str(workspace_id),
    )
    db.commit()
    return {"ok": True, "workspace_id": workspace_id}


@router.get("/{workspace_id}/members")
def members(workspace_id: int, db: DbSession, user: CurrentUser) -> list[dict]:
    _membership(db, user.id, workspace_id)
    rows = db.execute(
        select(WorkspaceMembership, User)
        .join(User, User.id == WorkspaceMembership.user_id)
        .where(WorkspaceMembership.workspace_id == workspace_id)
    ).all()
    return [
        {
            "user_id": member.user_id,
            "email": member_user.email,
            "display_name": member_user.display_name,
            "role": member.role,
        }
        for member, member_user in rows
    ]


@router.post("/{workspace_id}/invitations", status_code=201)
def invite(
    workspace_id: int,
    payload: InvitationCreate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    membership = _membership(db, user.id, workspace_id)
    if membership.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    rate_limiter.check(f"invite:{user.id}", limit=10, window_seconds=3600)
    raw = secrets.token_urlsafe(32)
    invitation = WorkspaceInvitation(
        workspace_id=workspace_id,
        email=payload.email,
        role="member",
        token_hash=hash_api_token(raw),
        status="pending",
        expires_at=utc_now() + timedelta(days=7),
        invited_by_user_id=user.id,
    )
    db.add(invitation)
    record_audit(
        db,
        workspace_id=workspace_id,
        user_id=user.id,
        event_type="member_invited",
        resource_type="invitation",
        request_id=request.headers.get("X-Request-ID"),
        metadata={"email_domain": payload.email.rpartition("@")[2]},
    )
    db.commit()
    return {
        "id": invitation.id,
        "email": invitation.email,
        "expires_at": invitation.expires_at,
        "invite_token": raw,
    }


@router.post("/invitations/accept")
def accept_invitation(
    payload: InvitationAccept,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    current: CurrentWorkspace,
) -> dict:
    rate_limiter.check(f"invite-accept:{user.id}", limit=20, window_seconds=3600)
    invitation = db.scalar(
        select(WorkspaceInvitation)
        .execution_options(skip_tenant_scope=True)
        .where(WorkspaceInvitation.token_hash == hash_api_token(payload.token))
    )
    if invitation is None:
        raise HTTPException(status_code=400, detail="Invitation is invalid")
    if invitation.status != "pending" or _aware(invitation.expires_at) <= utc_now():
        raise HTTPException(status_code=400, detail="Invitation is expired or already used")
    if invitation.email.casefold() != user.email.casefold():
        raise HTTPException(status_code=403, detail="Invitation email does not match")
    if (
        db.scalar(
            select(WorkspaceMembership.id).where(
                WorkspaceMembership.workspace_id == invitation.workspace_id,
                WorkspaceMembership.user_id == user.id,
            )
        )
        is None
    ):
        db.add(
            WorkspaceMembership(
                workspace_id=invitation.workspace_id,
                user_id=user.id,
                role=invitation.role,
                is_default=False,
            )
        )
    for membership in db.scalars(
        select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)
    ):
        membership.is_default = membership.workspace_id == invitation.workspace_id
    session_id = getattr(request.state, "auth_session_id", None)
    if session_id is not None:
        auth_session = db.get(AuthSession, session_id)
        if auth_session is not None and auth_session.user_id == user.id:
            auth_session.selected_workspace_id = invitation.workspace_id
    original_workspace = current.id
    set_trusted_workspace(db, invitation.workspace_id)
    invitation.status = "accepted"
    invitation.accepted_at = utc_now()
    record_audit(
        db,
        workspace_id=invitation.workspace_id,
        user_id=user.id,
        event_type="invitation_accepted",
        resource_type="invitation",
        resource_id=str(invitation.id),
        request_id=request.headers.get("X-Request-ID"),
    )
    db.commit()
    set_trusted_workspace(db, original_workspace)
    return {"ok": True, "workspace_id": invitation.workspace_id}


@router.delete("/{workspace_id}/members/{member_user_id}", status_code=204)
def remove_member(
    workspace_id: int,
    member_user_id: int,
    db: DbSession,
    user: CurrentUser,
) -> None:
    actor = _membership(db, user.id, workspace_id)
    if actor.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    if member_user_id == user.id:
        raise HTTPException(
            status_code=400,
            detail="Use Leave workspace to remove your own membership",
        )
    target = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == member_user_id,
        )
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Workspace member not found")
    record_audit(
        db,
        workspace_id=workspace_id,
        user_id=user.id,
        event_type="member_removed",
        resource_type="workspace_membership",
        resource_id=str(target.id),
        metadata={"removed_user_id": member_user_id},
    )
    db.delete(target)
    db.commit()


@router.post("/{workspace_id}/members/{member_user_id}/transfer-ownership")
def transfer_ownership(
    workspace_id: int,
    member_user_id: int,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    actor = _membership(db, user.id, workspace_id)
    if actor.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    if member_user_id == user.id:
        raise HTTPException(status_code=400, detail="Choose another workspace member")
    target = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == member_user_id,
        )
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Workspace member not found")
    target.role = "owner"
    actor.role = "member"
    record_audit(
        db,
        workspace_id=workspace_id,
        user_id=user.id,
        event_type="ownership_transferred",
        resource_type="workspace_membership",
        resource_id=str(target.id),
        metadata={"new_owner_user_id": member_user_id},
    )
    db.commit()
    return {"ok": True, "owner_user_id": member_user_id}


@router.post("/{workspace_id}/invitations/{invitation_id}/revoke")
def revoke_invitation(
    workspace_id: int,
    invitation_id: int,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    membership = _membership(db, user.id, workspace_id)
    if membership.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    invitation = db.get(WorkspaceInvitation, invitation_id)
    if invitation is None or invitation.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Invitation not found")
    invitation.status = "revoked"
    record_audit(
        db,
        workspace_id=workspace_id,
        user_id=user.id,
        event_type="invitation_revoked",
        resource_type="invitation",
        resource_id=str(invitation.id),
    )
    db.commit()
    return {"ok": True}


@router.delete("/{workspace_id}/membership", status_code=204)
def leave_workspace(
    workspace_id: int,
    db: DbSession,
    user: CurrentUser,
) -> None:
    membership = _membership(db, user.id, workspace_id)
    memberships = list(
        db.scalars(select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id))
    )
    if len(memberships) == 1:
        raise HTTPException(status_code=400, detail="Join another workspace before leaving")
    if membership.role == "owner":
        owner_count = db.scalar(
            select(func.count(WorkspaceMembership.id)).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.role == "owner",
            )
        )
        if owner_count == 1:
            raise HTTPException(status_code=400, detail="The last owner cannot leave")
    record_audit(
        db,
        workspace_id=workspace_id,
        user_id=user.id,
        event_type="member_removed",
        resource_type="workspace_membership",
        resource_id=str(membership.id),
    )
    db.delete(membership)
    db.commit()


def _membership(db: DbSession, user_id: int, workspace_id: int) -> WorkspaceMembership:
    membership = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.workspace_id == workspace_id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return membership


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)

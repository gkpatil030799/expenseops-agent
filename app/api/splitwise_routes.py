from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.api.deps import CurrentUser, CurrentWorkspace, DbSession
from app.config import get_settings
from app.models import SplitwiseIntegration, utc_now
from app.schemas import (
    CreateGroupRequest,
    FriendOut,
    GroupMemberRequest,
    GroupOut,
    InviteGroupMemberRequest,
    SplitwiseOAuthAuthorizeResponse,
    SplitwiseUserOut,
)
from app.security import encrypt_secret
from app.services.agent_service import friend_display_name
from app.services.managed_auth_service import record_audit
from app.services.oauth_state_service import (
    OAuthStateError,
    consume_oauth_state,
    create_oauth_state,
)
from app.services.splitwise_service import SplitwiseAPIError, SplitwiseService

router = APIRouter(prefix="/splitwise", tags=["splitwise"])


def _friend_out(friend: dict) -> FriendOut:
    return FriendOut(
        id=int(friend["id"]),
        first_name=friend.get("first_name"),
        last_name=friend.get("last_name"),
        email=friend.get("email"),
        display_name=friend_display_name(friend),
        registration_status=friend.get("registration_status"),
    )


def _group_out(group: dict) -> GroupOut:
    return GroupOut(
        id=int(group["id"]),
        name=group.get("name") or str(group["id"]),
        invite_link=group.get("invite_link"),
    )


@router.get("/me", response_model=SplitwiseUserOut)
def get_me() -> SplitwiseUserOut:
    try:
        user = SplitwiseService().get_current_user()
        return SplitwiseUserOut(**user)
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/oauth/authorize", response_model=SplitwiseOAuthAuthorizeResponse)
def get_oauth_authorize_url(
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> SplitwiseOAuthAuthorizeResponse:
    try:
        data = SplitwiseService().get_oauth_authorize_url()
        create_oauth_state(
            db,
            provider="splitwise",
            workspace_id=workspace.id,
            user_id=user.id,
            payload=data["oauth_token_secret"],
            raw_state=data["oauth_token"],
        )
        return SplitwiseOAuthAuthorizeResponse(
            authorize_url=data["authorize_url"], oauth_token=data["oauth_token"]
        )
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/oauth/callback")
def oauth_callback(
    oauth_token: str,
    oauth_verifier: str,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> RedirectResponse:
    try:
        _state, request_token_secret = consume_oauth_state(
            db,
            oauth_token,
            provider="splitwise",
            user_id=user.id,
            workspace_id=workspace.id,
        )
        if not request_token_secret:
            raise OAuthStateError("Splitwise request secret is missing")
        data = SplitwiseService().exchange_oauth_verifier(
            oauth_token=oauth_token,
            oauth_token_secret=request_token_secret,
            oauth_verifier=oauth_verifier,
        )
        integration = (
            db.query(SplitwiseIntegration)
            .filter(
                SplitwiseIntegration.workspace_id == workspace.id,
                SplitwiseIntegration.user_id == user.id,
            )
            .one_or_none()
        )
        identity = SplitwiseService(
            get_settings().model_copy(update=data),
            resolve_workspace_credentials=False,
        ).get_current_user()
        credentials = encrypt_secret(
            json.dumps(
                {
                    "splitwise_oauth_token": data["oauth_token"],
                    "splitwise_oauth_token_secret": data["oauth_token_secret"],
                }
            )
        )
        if integration is None:
            integration = SplitwiseIntegration(
                user_id=user.id,
                credentials_encrypted=credentials,
            )
            db.add(integration)
        else:
            integration.credentials_encrypted = credentials
            integration.enabled = True
            integration.updated_at = utc_now()
        integration.splitwise_user_id = str(identity.get("id") or "") or None
        integration.display_name = friend_display_name(identity)
        integration.email = str(identity.get("email") or "") or None
        integration.verified_at = utc_now()
        record_audit(
            db,
            workspace_id=workspace.id,
            user_id=user.id,
            event_type="splitwise_connected",
            resource_type="splitwise_integration",
        )
        db.commit()
        return RedirectResponse(url="/?workspace=settings", status_code=303)
    except OAuthStateError as exc:
        raise HTTPException(status_code=400, detail="Splitwise OAuth state is invalid") from exc
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/friends", response_model=list[FriendOut])
def list_friends(q: str = Query(default="")) -> list[FriendOut]:
    try:
        friends = SplitwiseService().search_friends(q) if q else SplitwiseService().get_friends()
        return [_friend_out(friend) for friend in friends]
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/groups", response_model=list[GroupOut])
def list_groups(q: str = Query(default="")) -> list[GroupOut]:
    try:
        groups = SplitwiseService().search_groups(q) if q else SplitwiseService().get_groups()
        return [_group_out(group) for group in groups]
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/groups", response_model=GroupOut, status_code=201)
def create_group(
    request: CreateGroupRequest,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> GroupOut:
    try:
        group = SplitwiseService().create_group(
            name=request.name,
            group_type=request.group_type,
            simplify_by_default=request.simplify_by_default,
            user_ids=request.user_ids,
        )
        record_audit(
            db,
            workspace_id=workspace.id,
            user_id=user.id,
            event_type="splitwise_group_created",
            resource_type="splitwise_group",
            resource_id=str(group.get("id") or ""),
        )
        db.commit()
        return _group_out(group)
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/groups/{group_id}", response_model=GroupOut)
def get_group(group_id: int) -> GroupOut:
    try:
        return _group_out(SplitwiseService().get_group(group_id))
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/groups/{group_id}/members", response_model=list[FriendOut])
def list_group_members(group_id: int) -> list[FriendOut]:
    try:
        members = SplitwiseService().get_group_members(group_id)
        return [_friend_out(member) for member in members]
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/groups/{group_id}/invite", response_model=list[FriendOut])
def invite_group_member(
    group_id: int,
    request: InviteGroupMemberRequest,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> list[FriendOut]:
    try:
        service = SplitwiseService()
        friend = service.create_friend(
            email=request.email,
            first_name=request.first_name,
            last_name=request.last_name,
        )
        service.add_user_to_group(group_id, int(friend["id"]))
        record_audit(
            db,
            workspace_id=workspace.id,
            user_id=user.id,
            event_type="splitwise_group_member_invited",
            resource_type="splitwise_group",
            resource_id=str(group_id),
            metadata={"invited_email_domain": request.email.rpartition("@")[2]},
        )
        db.commit()
        return [_friend_out(member) for member in service.get_group_members(group_id)]
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/groups/{group_id}/members", response_model=list[FriendOut])
def add_group_member(
    group_id: int,
    request: GroupMemberRequest,
    db: DbSession,
    user: CurrentUser,
    workspace: CurrentWorkspace,
) -> list[FriendOut]:
    try:
        service = SplitwiseService()
        service.add_user_to_group(group_id, request.user_id)
        record_audit(
            db,
            workspace_id=workspace.id,
            user_id=user.id,
            event_type="splitwise_group_member_added",
            resource_type="splitwise_group",
            resource_id=str(group_id),
            metadata={"splitwise_user_id": request.user_id},
        )
        db.commit()
        return [_friend_out(member) for member in service.get_group_members(group_id)]
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/groups/{group_id}/members/{user_id}", response_model=list[FriendOut])
def remove_group_member(
    group_id: int,
    user_id: int,
    db: DbSession,
    actor: CurrentUser,
    workspace: CurrentWorkspace,
) -> list[FriendOut]:
    try:
        service = SplitwiseService()
        service.remove_user_from_group(group_id, user_id)
        record_audit(
            db,
            workspace_id=workspace.id,
            user_id=actor.id,
            event_type="splitwise_group_member_removed",
            resource_type="splitwise_group",
            resource_id=str(group_id),
            metadata={"splitwise_user_id": user_id},
        )
        db.commit()
        return [_friend_out(member) for member in service.get_group_members(group_id)]
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

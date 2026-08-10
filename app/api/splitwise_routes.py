from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    CreateGroupRequest,
    FriendOut,
    GroupMemberRequest,
    GroupOut,
    InviteGroupMemberRequest,
    SplitwiseOAuthAccessTokenResponse,
    SplitwiseOAuthAuthorizeResponse,
    SplitwiseUserOut,
)
from app.services.agent_service import friend_display_name
from app.services.splitwise_service import SplitwiseAPIError, SplitwiseService

router = APIRouter(prefix="/splitwise", tags=["splitwise"])
_oauth_request_token_secrets: dict[str, str] = {}


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
def get_oauth_authorize_url() -> SplitwiseOAuthAuthorizeResponse:
    try:
        data = SplitwiseService().get_oauth_authorize_url()
        _oauth_request_token_secrets[data["oauth_token"]] = data["oauth_token_secret"]
        return SplitwiseOAuthAuthorizeResponse(**data)
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/oauth/callback", response_model=SplitwiseOAuthAccessTokenResponse)
def oauth_callback(
    oauth_token: str,
    oauth_verifier: str,
    oauth_token_secret: str | None = Query(default=None),
) -> SplitwiseOAuthAccessTokenResponse:
    request_token_secret = oauth_token_secret or _oauth_request_token_secrets.get(oauth_token)
    if not request_token_secret:
        raise HTTPException(
            status_code=400,
            detail=(
                "Missing OAuth request-token secret. Restart at /splitwise/oauth/authorize "
                "or pass oauth_token_secret explicitly."
            ),
        )

    try:
        data = SplitwiseService().exchange_oauth_verifier(
            oauth_token=oauth_token,
            oauth_token_secret=request_token_secret,
            oauth_verifier=oauth_verifier,
        )
        _oauth_request_token_secrets.pop(oauth_token, None)
        return SplitwiseOAuthAccessTokenResponse(
            **data,
            message=(
                "Set SPLITWISE_OAUTH_TOKEN and SPLITWISE_OAUTH_TOKEN_SECRET in .env, "
                "then restart the app."
            ),
        )
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
def create_group(request: CreateGroupRequest) -> GroupOut:
    try:
        group = SplitwiseService().create_group(
            name=request.name,
            group_type=request.group_type,
            simplify_by_default=request.simplify_by_default,
            user_ids=request.user_ids,
        )
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
def invite_group_member(group_id: int, request: InviteGroupMemberRequest) -> list[FriendOut]:
    try:
        service = SplitwiseService()
        friend = service.create_friend(
            email=request.email,
            first_name=request.first_name,
            last_name=request.last_name,
        )
        service.add_user_to_group(group_id, int(friend["id"]))
        return [_friend_out(member) for member in service.get_group_members(group_id)]
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/groups/{group_id}/members", response_model=list[FriendOut])
def add_group_member(group_id: int, request: GroupMemberRequest) -> list[FriendOut]:
    try:
        service = SplitwiseService()
        service.add_user_to_group(group_id, request.user_id)
        return [_friend_out(member) for member in service.get_group_members(group_id)]
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/groups/{group_id}/members/{user_id}", response_model=list[FriendOut])
def remove_group_member(group_id: int, user_id: int) -> list[FriendOut]:
    try:
        service = SplitwiseService()
        service.remove_user_from_group(group_id, user_id)
        return [_friend_out(member) for member in service.get_group_members(group_id)]
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

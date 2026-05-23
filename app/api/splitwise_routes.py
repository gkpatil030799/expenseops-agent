from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    FriendOut,
    GroupOut,
    SplitwiseOAuthAccessTokenResponse,
    SplitwiseOAuthAuthorizeResponse,
    SplitwiseUserOut,
)
from app.services.agent_service import friend_display_name
from app.services.splitwise_service import SplitwiseAPIError, SplitwiseService

router = APIRouter(prefix="/splitwise", tags=["splitwise"])
_oauth_request_token_secrets: dict[str, str] = {}


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
        return [
            FriendOut(
                id=int(friend["id"]),
                first_name=friend.get("first_name"),
                last_name=friend.get("last_name"),
                email=friend.get("email"),
                display_name=friend_display_name(friend),
            )
            for friend in friends
        ]
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/groups", response_model=list[GroupOut])
def list_groups(q: str = Query(default="")) -> list[GroupOut]:
    try:
        groups = SplitwiseService().search_groups(q) if q else SplitwiseService().get_groups()
        return [
            GroupOut(id=int(group["id"]), name=group.get("name") or str(group["id"]))
            for group in groups
        ]
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/groups/{group_id}/members", response_model=list[FriendOut])
def list_group_members(group_id: int) -> list[FriendOut]:
    try:
        members = SplitwiseService().get_group_members(group_id)
        return [
            FriendOut(
                id=int(member["id"]),
                first_name=member.get("first_name"),
                last_name=member.get("last_name"),
                email=member.get("email"),
                display_name=friend_display_name(member),
            )
            for member in members
        ]
    except SplitwiseAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

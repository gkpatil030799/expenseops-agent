from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OAuthState, utc_now
from app.security import decrypt_secret, encrypt_secret
from app.tenancy import hash_api_token


class OAuthStateError(ValueError):
    pass


def create_oauth_state(
    db: Session,
    *,
    provider: str,
    workspace_id: int | None,
    user_id: int | None,
    payload: str | None = None,
    redirect_after: str | None = None,
    ttl_minutes: int = 10,
    raw_state: str | None = None,
) -> str:
    raw = raw_state or secrets.token_urlsafe(32)
    db.add(
        OAuthState(
            provider=provider,
            workspace_id=workspace_id,
            user_id=user_id,
            state_hash=hash_api_token(raw),
            payload_encrypted=encrypt_secret(payload) if payload else None,
            redirect_after=redirect_after,
            expires_at=utc_now() + timedelta(minutes=ttl_minutes),
        )
    )
    db.commit()
    return raw


def consume_oauth_state(
    db: Session,
    raw: str,
    *,
    provider: str,
    user_id: int | None = None,
    workspace_id: int | None = None,
) -> tuple[OAuthState, str | None]:
    state = db.scalar(
        select(OAuthState).where(
            OAuthState.state_hash == hash_api_token(raw),
            OAuthState.provider == provider,
        )
    )
    if state is None or state.used_at is not None or _aware(state.expires_at) <= utc_now():
        raise OAuthStateError("OAuth state is invalid or expired")
    if user_id is not None and state.user_id != user_id:
        raise OAuthStateError("OAuth state does not belong to this user")
    if workspace_id is not None and state.workspace_id != workspace_id:
        raise OAuthStateError("OAuth state does not belong to this workspace")
    state.used_at = utc_now()
    payload = decrypt_secret(state.payload_encrypted) if state.payload_encrypted else None
    db.commit()
    return state, payload


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)

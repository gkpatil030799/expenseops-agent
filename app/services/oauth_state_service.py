from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import select, update
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
    state_hash = hash_api_token(raw)
    conditions = [
        OAuthState.state_hash == state_hash,
        OAuthState.provider == provider,
        OAuthState.used_at.is_(None),
        OAuthState.expires_at > utc_now(),
    ]
    if user_id is not None:
        conditions.append(OAuthState.user_id == user_id)
    if workspace_id is not None:
        conditions.append(OAuthState.workspace_id == workspace_id)
    result = db.execute(
        update(OAuthState)
        .where(*conditions)
        .values(used_at=utc_now())
    )
    if result.rowcount != 1:
        db.rollback()
        raise OAuthStateError("OAuth state is invalid or expired")
    db.commit()
    state = db.scalar(
        select(OAuthState).where(
            OAuthState.state_hash == state_hash,
            OAuthState.provider == provider,
        )
    )
    if state is None:  # Defensive: the consumed row must still exist after commit.
        raise OAuthStateError("OAuth state is invalid or expired")
    payload = decrypt_secret(state.payload_encrypted) if state.payload_encrypted else None
    return state, payload

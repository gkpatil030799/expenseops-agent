from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    AuditEvent,
    AuthIdentity,
    AuthSession,
    User,
    Workspace,
    WorkspaceMembership,
    utc_now,
)
from app.tenancy import DEFAULT_USER_EMAIL, TenantContext, hash_api_token


class OIDCValidationError(ValueError):
    pass


class OIDCVerifier:
    def __init__(
        self,
        settings: Settings | None = None,
        key_resolver: Callable[[str], object] | None = None,
    ):
        self.settings = settings or get_settings()
        self.key_resolver = key_resolver or self._jwks_key

    def validate(self, token: str) -> dict:
        try:
            header = jwt.get_unverified_header(token)
            algorithm = str(header.get("alg") or "")
            if algorithm not in self.settings.oidc_algorithms:
                raise OIDCValidationError("Unsupported identity-token algorithm")
            key = self.key_resolver(token)
            claims = jwt.decode(
                token,
                key,
                algorithms=self.settings.oidc_algorithms,
                audience=self.settings.oidc_audience,
                issuer=self.settings.oidc_issuer.rstrip("/"),
                options={"require_sub": True, "require_exp": True},
            )
        except (JWTError, OIDCValidationError, httpx.HTTPError, KeyError, ValueError) as exc:
            raise OIDCValidationError("Invalid identity token") from exc
        if not claims.get("sub"):
            raise OIDCValidationError("Identity token has no subject")
        return claims

    def discovery(self) -> dict:
        response = httpx.get(
            f"{self.settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration",
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def _jwks_key(self, token: str) -> object:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        discovery = self.discovery()
        response = httpx.get(discovery["jwks_uri"], timeout=10)
        response.raise_for_status()
        keys = response.json().get("keys", [])
        key = next((value for value in keys if value.get("kid") == kid), None)
        if key is None:
            raise OIDCValidationError("Signing key not found")
        return key


def provision_oidc_identity(
    db: Session,
    claims: dict,
    *,
    provider: str,
    settings: Settings | None = None,
) -> tuple[User, Workspace, bool]:
    settings = settings or get_settings()
    subject = str(claims.get("sub") or "").strip()
    email = str(claims.get("email") or "").strip().casefold()
    if not subject or not email:
        raise OIDCValidationError("Identity token must include subject and email")
    identity = db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.provider == provider,
            AuthIdentity.provider_subject == subject,
        )
    )
    created = identity is None
    if identity is None:
        user = None
        if claims.get("email_verified") is True:
            candidate = db.scalar(select(User).where(User.email == email))
            if (
                candidate is None
                and settings.oidc_bootstrap_email.strip().casefold() == email
            ):
                candidate = db.scalar(select(User).where(User.email == DEFAULT_USER_EMAIL))
            if candidate is not None:
                has_identity = db.scalar(
                    select(AuthIdentity.id).where(AuthIdentity.user_id == candidate.id)
                )
                if has_identity is None:
                    user = candidate
        if user is None:
            user = User(
                email=email,
                display_name=str(claims.get("name") or email.split("@", 1)[0]),
                status="active",
            )
            db.add(user)
            db.flush()
        else:
            user.email = email
            user.display_name = str(claims.get("name") or user.display_name)
            user.updated_at = utc_now()
        identity = AuthIdentity(
            user_id=user.id,
            provider=provider,
            provider_subject=subject,
            email=email,
            display_name=str(claims.get("name") or "") or None,
            avatar_url=str(claims.get("picture") or "") or None,
        )
        db.add(identity)
        db.flush()
    else:
        user = db.get(User, identity.user_id)
        if user is None or user.status != "active":
            raise OIDCValidationError("ExpenseOps account is inactive")
        identity.email = email
        identity.display_name = str(claims.get("name") or "") or identity.display_name
        identity.avatar_url = str(claims.get("picture") or "") or identity.avatar_url
        identity.last_login_at = utc_now()
        user.email = email
        user.display_name = identity.display_name or user.display_name
        user.updated_at = utc_now()

    membership = db.scalar(
        select(WorkspaceMembership)
        .where(WorkspaceMembership.user_id == user.id)
        .order_by(WorkspaceMembership.is_default.desc(), WorkspaceMembership.id)
    )
    if membership is None:
        workspace = Workspace(
            name=f"{user.display_name}'s workspace",
            workspace_type="personal",
            created_by_user_id=user.id,
        )
        db.add(workspace)
        db.flush()
        membership = WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role="owner",
            is_default=True,
        )
        db.add(membership)
        db.flush()
        record_audit(
            db,
            workspace_id=workspace.id,
            user_id=user.id,
            event_type="workspace_created",
            resource_type="workspace",
            resource_id=str(workspace.id),
        )
    else:
        workspace = db.get(Workspace, membership.workspace_id)
    record_audit(
        db,
        workspace_id=workspace.id,
        user_id=user.id,
        event_type="login",
        metadata={"provider": provider},
    )
    db.commit()
    return user, workspace, created


def create_auth_session(
    db: Session, user_id: int, workspace_id: int, settings: Settings
) -> tuple[str, AuthSession]:
    raw = secrets.token_urlsafe(48)
    session = AuthSession(
        user_id=user_id,
        selected_workspace_id=workspace_id,
        token_hash=hash_api_token(raw),
        expires_at=utc_now() + timedelta(hours=settings.auth_session_hours),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return raw, session


def resolve_auth_session(db: Session, raw_token: str) -> tuple[TenantContext, AuthSession] | None:
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hash_api_token(raw_token),
            AuthSession.revoked_at.is_(None),
        )
    )
    if session is None or _aware(session.expires_at) <= utc_now():
        return None
    membership = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.user_id == session.user_id,
            WorkspaceMembership.workspace_id == session.selected_workspace_id,
        )
    )
    if membership is None:
        return None
    session.last_seen_at = utc_now()
    db.commit()
    return TenantContext(session.user_id, session.selected_workspace_id), session


def record_audit(
    db: Session,
    *,
    workspace_id: int,
    user_id: int | None,
    event_type: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    request_id: str | None = None,
    metadata: dict | None = None,
) -> AuditEvent:
    safe_metadata = {
        key: value
        for key, value in (metadata or {}).items()
        if not any(word in key.casefold() for word in ("token", "secret", "password", "body"))
    }
    event = AuditEvent(
        workspace_id=workspace_id,
        user_id=user_id,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
        metadata_json=safe_metadata,
    )
    db.add(event)
    return event


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)

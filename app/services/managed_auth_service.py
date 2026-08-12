from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.logging_config import get_trace_id
from app.models import (
    AuditEvent,
    AuthIdentity,
    AuthSession,
    GmailAccount,
    SplitwiseIntegration,
    TelegramIdentity,
    User,
    Workspace,
    WorkspaceMembership,
    utc_now,
)
from app.security import encrypt_secret
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
    claimed_legacy_workspace = False
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
                    claimed_legacy_workspace = candidate.email == DEFAULT_USER_EMAIL
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
    if claimed_legacy_workspace:
        _claim_legacy_integrations(db, user.id, workspace.id, settings)
    record_audit(
        db,
        workspace_id=workspace.id,
        user_id=user.id,
        event_type="login",
        metadata={"provider": provider},
    )
    if created:
        record_audit_once(
            db,
            workspace_id=workspace.id,
            user_id=user.id,
            event_type="user_first_login",
            metadata={"provider": provider},
        )
        record_audit_once(
            db,
            workspace_id=workspace.id,
            user_id=user.id,
            event_type="onboarding_started",
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
        request_id=request_id or get_trace_id(),
        metadata_json=safe_metadata,
    )
    db.add(event)
    return event


def record_audit_once(
    db: Session,
    *,
    workspace_id: int | None = None,
    user_id: int | None = None,
    event_type: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: dict | None = None,
) -> AuditEvent | None:
    workspace_id = workspace_id or db.info.get("workspace_id")
    user_id = user_id if user_id is not None else db.info.get("user_id")
    if workspace_id is None:
        return None
    pending = any(
        isinstance(value, AuditEvent)
        and value.workspace_id == workspace_id
        and value.event_type == event_type
        for value in db.new
    )
    if pending:
        return None
    existing = db.scalar(
        select(AuditEvent.id).where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.event_type == event_type,
        )
    )
    if existing is not None:
        return None
    return record_audit(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=metadata,
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _claim_legacy_integrations(
    db: Session, user_id: int, workspace_id: int, settings: Settings
) -> None:
    gmail_connected = db.scalar(
        select(GmailAccount.id).where(GmailAccount.workspace_id == workspace_id)
    ) is not None
    if settings.gmail_refresh_token and not gmail_connected:
        db.add(
            GmailAccount(
                workspace_id=workspace_id,
                user_id=user_id,
                google_user_id=settings.gmail_user_id or "me",
                refresh_token_encrypted=encrypt_secret(settings.gmail_refresh_token),
            )
        )
        record_audit(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            event_type="gmail_connected",
            resource_type="gmail_account",
            metadata={"source": "verified_owner_bootstrap"},
        )
        gmail_connected = True
    telegram_connected = db.scalar(
        select(TelegramIdentity.id).where(TelegramIdentity.workspace_id == workspace_id)
    ) is not None
    if (
        settings.telegram_allowed_user_id
        and settings.telegram_chat_id
        and not telegram_connected
    ):
        db.add(
            TelegramIdentity(
                workspace_id=workspace_id,
                user_id=user_id,
                telegram_user_id=settings.telegram_allowed_user_id,
                chat_id=settings.telegram_chat_id,
            )
        )
        record_audit(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            event_type="telegram_connected",
            resource_type="telegram_identity",
            metadata={"source": "verified_owner_bootstrap"},
        )
        telegram_connected = True
    splitwise_credentials: dict[str, str] = {}
    if settings.splitwise_api_key:
        splitwise_credentials["splitwise_api_key"] = settings.splitwise_api_key
    elif settings.splitwise_access_token:
        splitwise_credentials.update(
            {
                "splitwise_access_token": settings.splitwise_access_token,
                "splitwise_auth_scheme": settings.splitwise_auth_scheme,
            }
        )
    elif settings.splitwise_oauth_token and settings.splitwise_oauth_token_secret:
        splitwise_credentials.update(
            {
                "splitwise_oauth_token": settings.splitwise_oauth_token,
                "splitwise_oauth_token_secret": settings.splitwise_oauth_token_secret,
            }
        )
    if splitwise_credentials and db.scalar(
        select(SplitwiseIntegration.id).where(
            SplitwiseIntegration.workspace_id == workspace_id
        )
    ) is None:
        db.add(
            SplitwiseIntegration(
                workspace_id=workspace_id,
                credentials_encrypted=encrypt_secret(json.dumps(splitwise_credentials)),
            )
        )
        record_audit(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            event_type="splitwise_connected",
            resource_type="splitwise_integration",
            metadata={"source": "verified_owner_bootstrap"},
        )
    if gmail_connected and telegram_connected:
        record_audit_once(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            event_type="onboarding_completed",
            metadata={"source": "verified_owner_bootstrap"},
        )

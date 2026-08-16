from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import GmailAccount, TelegramIdentity, User
from app.security import decrypt_secret
from app.tenancy import (
    DEFAULT_USER_EMAIL,
    clear_session_tenant,
    ensure_default_tenancy,
    set_trusted_workspace,
    workspace_ids,
)


@dataclass(frozen=True)
class WorkspaceJobContext:
    workspace_id: int
    settings: Settings


def all_workspace_job_contexts(db: Session, settings: Settings) -> list[WorkspaceJobContext]:
    clear_session_tenant(db)
    values = workspace_ids(db)
    if not values:
        values = [ensure_default_tenancy(db).workspace_id]
    return [
        WorkspaceJobContext(
            workspace_id=value,
            settings=telegram_settings_for_workspace(db, value, settings),
        )
        for value in values
    ]


def gmail_job_contexts(db: Session, settings: Settings) -> list[WorkspaceJobContext]:
    clear_session_tenant(db)
    contexts: list[WorkspaceJobContext] = []
    for workspace_id in workspace_ids(db):
        set_trusted_workspace(db, workspace_id)
        account = db.scalar(
            select(GmailAccount).where(GmailAccount.enabled.is_(True)).order_by(GmailAccount.id)
        )
        if account is not None:
            account_settings = settings.model_copy(
                update={
                    "gmail_refresh_token": decrypt_secret(account.refresh_token_encrypted),
                    "gmail_user_id": account.google_user_id,
                }
            )
            contexts.append(
                WorkspaceJobContext(
                    workspace_id=workspace_id,
                    settings=telegram_settings_for_workspace(
                        db,
                        workspace_id,
                        account_settings,
                    ),
                )
            )
    if contexts:
        return contexts
    # Existing installations keep working, but the legacy env token is bound only
    # to the backfilled default workspace rather than copied across tenants.
    default = ensure_default_tenancy(db)
    return [
        WorkspaceJobContext(
            workspace_id=default.workspace_id,
            settings=telegram_settings_for_workspace(db, default.workspace_id, settings),
        )
    ]


def enter_job_workspace(db: Session, workspace_id: int) -> None:
    db.rollback()
    db.expunge_all()
    set_trusted_workspace(db, workspace_id)
    from app.tenancy import set_active_workspace

    set_active_workspace(workspace_id)


def leave_job_workspace() -> None:
    from app.tenancy import set_active_workspace

    set_active_workspace(None)


def gmail_settings_for_session(db: Session, settings: Settings) -> Settings:
    workspace_id = db.info.get("workspace_id")
    if workspace_id is None:
        return settings
    account = db.scalar(
        select(GmailAccount).where(
            GmailAccount.workspace_id == workspace_id,
            GmailAccount.enabled.is_(True),
        )
    )
    if account is not None:
        return settings.model_copy(
            update={
                "gmail_refresh_token": decrypt_secret(account.refresh_token_encrypted),
                "gmail_user_id": account.google_user_id,
            }
        )
    default = ensure_default_tenancy(db)
    if default.workspace_id == workspace_id:
        default_user = db.get(User, default.user_id)
        if default_user is not None and default_user.email == DEFAULT_USER_EMAIL:
            return settings
    return settings.model_copy(update={"gmail_refresh_token": ""})


def telegram_settings_for_workspace(
    db: Session,
    workspace_id: int,
    settings: Settings,
    user_id: int | None = None,
) -> Settings:
    set_trusted_workspace(db, workspace_id)
    filters = [
        TelegramIdentity.workspace_id == workspace_id,
        TelegramIdentity.enabled.is_(True),
    ]
    if user_id is not None:
        filters.append(TelegramIdentity.user_id == user_id)
    identities = list(
        db.scalars(select(TelegramIdentity).where(*filters).order_by(TelegramIdentity.id).limit(2))
    )
    # Never guess a recipient when a workspace has several personal identities.
    identity = identities[0] if len(identities) == 1 else None
    if identity is not None:
        return settings.model_copy(update={"telegram_chat_id": identity.chat_id})
    default = ensure_default_tenancy(db)
    if default.workspace_id == workspace_id:
        default_user = db.get(User, default.user_id)
        if default_user is not None and default_user.email == DEFAULT_USER_EMAIL:
            return settings
    return settings.model_copy(update={"telegram_chat_id": ""})

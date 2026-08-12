from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import GmailAccount, TelegramIdentity, User, Workspace
from app.security import decrypt_secret
from app.tenancy import (
    DEFAULT_USER_EMAIL,
    clear_session_tenant,
    ensure_default_tenancy,
    set_trusted_workspace,
)


@dataclass(frozen=True)
class WorkspaceJobContext:
    workspace_id: int
    settings: Settings


def all_workspace_job_contexts(db: Session, settings: Settings) -> list[WorkspaceJobContext]:
    clear_session_tenant(db)
    workspace_ids = list(db.scalars(select(Workspace.id).order_by(Workspace.id)))
    if not workspace_ids:
        workspace_ids = [ensure_default_tenancy(db).workspace_id]
    return [
        WorkspaceJobContext(
            workspace_id=value,
            settings=telegram_settings_for_workspace(db, value, settings),
        )
        for value in workspace_ids
    ]


def gmail_job_contexts(db: Session, settings: Settings) -> list[WorkspaceJobContext]:
    clear_session_tenant(db)
    accounts = list(
        db.scalars(
            select(GmailAccount).where(GmailAccount.enabled.is_(True)).order_by(GmailAccount.id)
        )
    )
    if accounts:
        return [
            WorkspaceJobContext(
                workspace_id=account.workspace_id,
                settings=telegram_settings_for_workspace(
                    db,
                    account.workspace_id,
                    settings.model_copy(
                        update={
                            "gmail_refresh_token": decrypt_secret(account.refresh_token_encrypted),
                            "gmail_user_id": account.google_user_id,
                        }
                    ),
                ),
            )
            for account in accounts
        ]
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
    clear_session_tenant(db)
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
    db: Session, workspace_id: int, settings: Settings
) -> Settings:
    identity = db.scalar(
        select(TelegramIdentity)
        .execution_options(skip_tenant_scope=True)
        .where(
            TelegramIdentity.workspace_id == workspace_id,
            TelegramIdentity.enabled.is_(True),
        )
        .order_by(TelegramIdentity.id)
    )
    if identity is not None:
        return settings.model_copy(update={"telegram_chat_id": identity.chat_id})
    default = ensure_default_tenancy(db)
    if default.workspace_id == workspace_id:
        default_user = db.get(User, default.user_id)
        if default_user is not None and default_user.email == DEFAULT_USER_EMAIL:
            return settings
    return settings.model_copy(update={"telegram_chat_id": ""})

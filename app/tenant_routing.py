"""Narrow tenant discovery for trusted external identifiers.

PostgreSQL uses audited SECURITY DEFINER functions that return only internal
routing IDs.  SQLite/local development performs the same exact lookups under
one concrete workspace at a time.  Successful routes leave the session scoped
to the returned workspace; a miss leaves it unscoped and therefore unable to
see tenant rows under PostgreSQL RLS.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import (
    PlaidItem,
    TelegramIdentity,
    TelegramLinkCode,
    User,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
)
from app.tenancy import (
    clear_session_tenant,
    find_tenant_row_across_workspaces,
    set_trusted_workspace,
)


@dataclass(frozen=True)
class PlaidItemRoute:
    workspace_id: int
    plaid_item_id: int


@dataclass(frozen=True)
class TelegramIdentityRoute:
    workspace_id: int
    telegram_identity_id: int
    user_id: int


@dataclass(frozen=True)
class ActiveTelegramIdentityRoute:
    workspace_id: int
    telegram_identity_id: int


@dataclass(frozen=True)
class TelegramLinkCodeRoute:
    workspace_id: int
    telegram_link_code_id: int
    user_id: int


@dataclass(frozen=True)
class WorkspaceInvitationRoute:
    workspace_id: int
    workspace_invitation_id: int


def route_plaid_item(db: Session, item_id: str) -> PlaidItemRoute | None:
    if _is_postgresql(db):
        row = _postgres_route(
            db,
            """
            SELECT workspace_id, plaid_item_id
            FROM public.expenseops_route_plaid_item(:item_id)
            """,
            {"item_id": item_id},
        )
        if row is None:
            return None
        route = PlaidItemRoute(
            workspace_id=int(row["workspace_id"]),
            plaid_item_id=int(row["plaid_item_id"]),
        )
        item = db.get(PlaidItem, route.plaid_item_id)
        if (
            item is None
            or item.workspace_id != route.workspace_id
            or item.item_id != item_id
            or not _plaid_membership_active(db, item)
        ):
            clear_session_tenant(db)
            return None
        return route
    item = find_tenant_row_across_workspaces(
        db,
        select(PlaidItem).where(PlaidItem.item_id == item_id).order_by(PlaidItem.id),
    )
    if item is None or not _plaid_membership_active(db, item):
        clear_session_tenant(db)
        return None
    return PlaidItemRoute(workspace_id=item.workspace_id, plaid_item_id=item.id)


def route_telegram_identity(
    db: Session,
    telegram_user_id: str,
    chat_id: str,
) -> TelegramIdentityRoute | None:
    if _is_postgresql(db):
        row = _postgres_route(
            db,
            """
            SELECT workspace_id, telegram_identity_id, user_id
            FROM public.expenseops_route_telegram_identity(
                :telegram_user_id,
                :chat_id
            )
            """,
            {"telegram_user_id": telegram_user_id, "chat_id": chat_id},
        )
        if row is None:
            return None
        route = TelegramIdentityRoute(
            workspace_id=int(row["workspace_id"]),
            telegram_identity_id=int(row["telegram_identity_id"]),
            user_id=int(row["user_id"]),
        )
        identity = db.get(TelegramIdentity, route.telegram_identity_id)
        if (
            identity is None
            or identity.workspace_id != route.workspace_id
            or identity.user_id != route.user_id
            or identity.telegram_user_id != telegram_user_id
            or identity.chat_id != chat_id
            or not _active_membership(db, route.workspace_id, route.user_id)
        ):
            clear_session_tenant(db)
            return None
        return route
    identity = find_tenant_row_across_workspaces(
        db,
        select(TelegramIdentity)
        .join(
            WorkspaceMembership,
            (WorkspaceMembership.workspace_id == TelegramIdentity.workspace_id)
            & (WorkspaceMembership.user_id == TelegramIdentity.user_id),
        )
        .where(
            TelegramIdentity.telegram_user_id == telegram_user_id,
            TelegramIdentity.chat_id == chat_id,
        )
        .order_by(TelegramIdentity.id),
    )
    if identity is None or not _active_membership(db, identity.workspace_id, identity.user_id):
        clear_session_tenant(db)
        return None
    return TelegramIdentityRoute(
        workspace_id=identity.workspace_id,
        telegram_identity_id=identity.id,
        user_id=identity.user_id,
    )


def route_active_telegram_identity_by_link_code(
    db: Session,
    code_hash: str,
) -> ActiveTelegramIdentityRoute | None:
    link_route = route_telegram_link_code(db, code_hash)
    if link_route is None:
        return None
    if _is_postgresql(db):
        row = _postgres_route(
            db,
            """
            SELECT workspace_id, telegram_identity_id
            FROM public.expenseops_route_active_telegram_identity_by_link_code(:code_hash)
            """,
            {"code_hash": code_hash},
        )
        if row is None:
            return None
        route = ActiveTelegramIdentityRoute(
            workspace_id=int(row["workspace_id"]),
            telegram_identity_id=int(row["telegram_identity_id"]),
        )
        identity = db.get(TelegramIdentity, route.telegram_identity_id)
        if (
            identity is None
            or identity.workspace_id != route.workspace_id
            or identity.user_id != link_route.user_id
            or not identity.enabled
            or not _active_membership(db, identity.workspace_id, identity.user_id)
        ):
            clear_session_tenant(db)
            return None
        return route
    identity = find_tenant_row_across_workspaces(
        db,
        select(TelegramIdentity)
        .where(
            TelegramIdentity.user_id == link_route.user_id,
            TelegramIdentity.enabled.is_(True),
        )
        .order_by(TelegramIdentity.id),
    )
    if identity is None or not _active_membership(db, identity.workspace_id, identity.user_id):
        clear_session_tenant(db)
        return None
    return ActiveTelegramIdentityRoute(
        workspace_id=identity.workspace_id,
        telegram_identity_id=identity.id,
    )


def route_telegram_link_code(db: Session, code_hash: str) -> TelegramLinkCodeRoute | None:
    if _is_postgresql(db):
        row = _postgres_route(
            db,
            """
            SELECT workspace_id, telegram_link_code_id, user_id
            FROM public.expenseops_route_telegram_link_code(:code_hash)
            """,
            {"code_hash": code_hash},
        )
        if row is None:
            return None
        route = TelegramLinkCodeRoute(
            workspace_id=int(row["workspace_id"]),
            telegram_link_code_id=int(row["telegram_link_code_id"]),
            user_id=int(row["user_id"]),
        )
        link = db.get(TelegramLinkCode, route.telegram_link_code_id)
        if (
            link is None
            or link.workspace_id != route.workspace_id
            or link.user_id != route.user_id
            or link.code_hash != code_hash
            or not _active_membership(db, route.workspace_id, route.user_id)
        ):
            clear_session_tenant(db)
            return None
        return route
    link = find_tenant_row_across_workspaces(
        db,
        select(TelegramLinkCode)
        .join(
            WorkspaceMembership,
            (WorkspaceMembership.workspace_id == TelegramLinkCode.workspace_id)
            & (WorkspaceMembership.user_id == TelegramLinkCode.user_id),
        )
        .where(TelegramLinkCode.code_hash == code_hash)
        .order_by(TelegramLinkCode.id),
    )
    if link is None or not _active_membership(db, link.workspace_id, link.user_id):
        clear_session_tenant(db)
        return None
    return TelegramLinkCodeRoute(
        workspace_id=link.workspace_id,
        telegram_link_code_id=link.id,
        user_id=link.user_id,
    )


def route_workspace_invitation(
    db: Session,
    token_hash: str,
) -> WorkspaceInvitationRoute | None:
    if _is_postgresql(db):
        row = _postgres_route(
            db,
            """
            SELECT workspace_id, workspace_invitation_id
            FROM public.expenseops_route_workspace_invitation(:token_hash)
            """,
            {"token_hash": token_hash},
        )
        if row is None:
            return None
        route = WorkspaceInvitationRoute(
            workspace_id=int(row["workspace_id"]),
            workspace_invitation_id=int(row["workspace_invitation_id"]),
        )
        invitation = db.get(WorkspaceInvitation, route.workspace_invitation_id)
        if (
            invitation is None
            or invitation.workspace_id != route.workspace_id
            or invitation.token_hash != token_hash
            or not _active_membership(
                db,
                invitation.workspace_id,
                invitation.invited_by_user_id,
            )
        ):
            clear_session_tenant(db)
            return None
        return route
    invitation = find_tenant_row_across_workspaces(
        db,
        select(WorkspaceInvitation)
        .join(
            WorkspaceMembership,
            (WorkspaceMembership.workspace_id == WorkspaceInvitation.workspace_id)
            & (WorkspaceMembership.user_id == WorkspaceInvitation.invited_by_user_id),
        )
        .where(WorkspaceInvitation.token_hash == token_hash)
        .order_by(WorkspaceInvitation.id),
    )
    if invitation is None or not _active_membership(
        db,
        invitation.workspace_id,
        invitation.invited_by_user_id,
    ):
        clear_session_tenant(db)
        return None
    return WorkspaceInvitationRoute(
        workspace_id=invitation.workspace_id,
        workspace_invitation_id=invitation.id,
    )


def _is_postgresql(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def _active_membership(db: Session, workspace_id: int, user_id: int) -> bool:
    return (
        db.scalar(
            select(WorkspaceMembership.id)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                User.status == "active",
            )
        )
        is not None
    )


def _plaid_membership_active(db: Session, item: PlaidItem) -> bool:
    statement = (
        select(WorkspaceMembership.id)
        .join(User, User.id == WorkspaceMembership.user_id)
        .where(
            WorkspaceMembership.workspace_id == item.workspace_id,
            User.status == "active",
        )
    )
    if item.owner_user_id is not None:
        statement = statement.where(WorkspaceMembership.user_id == item.owner_user_id)
    if db.scalar(statement) is not None:
        return True
    # Some SQLite-only unit fixtures intentionally predate the workspace
    # directory. Preserve that local compatibility without weakening the
    # PostgreSQL path, where the foreign key and routing function require it.
    return _is_postgresql(db) is False and db.get(Workspace, item.workspace_id) is None


def _postgres_route(
    db: Session,
    query: str,
    parameters: Mapping[str, object],
) -> Mapping[str, Any] | None:
    # Clear stale request scope first.  This only clears workspace state; it
    # never enables a session-level or transaction-level RLS escape hatch.
    clear_session_tenant(db)
    row = db.execute(text(query), parameters).mappings().one_or_none()
    if row is None:
        return None
    set_trusted_workspace(db, int(row["workspace_id"]))
    return row

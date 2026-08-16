from __future__ import annotations

import hashlib
import secrets
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, with_loader_criteria
from sqlalchemy.sql import Select

from app.models import TenantScoped, User, Workspace, WorkspaceMembership

DEFAULT_USER_EMAIL = "local@expenseops.invalid"
DEFAULT_WORKSPACE_NAME = "Personal workspace"
_active_workspace_id: ContextVar[int | None] = ContextVar("active_workspace_id", default=None)
_active_user_id: ContextVar[int | None] = ContextVar("active_user_id", default=None)


@dataclass(frozen=True)
class TenantContext:
    user_id: int
    workspace_id: int


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_api_token() -> tuple[str, str]:
    token = f"exp_{secrets.token_urlsafe(32)}"
    return token, hash_api_token(token)


def ensure_default_tenancy(db: Session) -> TenantContext:
    # Migration 0012 and a fresh local database both reserve the first user as the
    # legacy/default owner. Its email may later change when a verified OIDC owner
    # claims the workspace, so the email cannot remain the lookup key.
    user = db.get(User, 1)
    if user is None:
        user = User(email=DEFAULT_USER_EMAIL, display_name="Local user", status="active")
        db.add(user)
        db.flush()
    membership = db.scalar(
        select(WorkspaceMembership)
        .where(WorkspaceMembership.user_id == user.id)
        .order_by(WorkspaceMembership.is_default.desc(), WorkspaceMembership.id)
    )
    if membership is None:
        workspace = Workspace(
            name=DEFAULT_WORKSPACE_NAME,
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
    db.commit()
    return TenantContext(user_id=user.id, workspace_id=membership.workspace_id)


def resolve_user_token(db: Session, token: str) -> TenantContext | None:
    user = db.scalar(
        select(User).where(
            User.api_token_hash == hash_api_token(token),
            User.status == "active",
        )
    )
    if user is None:
        return None
    membership = db.scalar(
        select(WorkspaceMembership)
        .where(WorkspaceMembership.user_id == user.id)
        .order_by(WorkspaceMembership.is_default.desc(), WorkspaceMembership.id)
    )
    if membership is None:
        return None
    return TenantContext(user_id=user.id, workspace_id=membership.workspace_id)


def require_membership(db: Session, user_id: int, workspace_id: int) -> WorkspaceMembership:
    membership = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.workspace_id == workspace_id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return membership


def set_session_tenant(db: Session, context: TenantContext) -> None:
    require_membership(db, context.user_id, context.workspace_id)
    db.info["user_id"] = context.user_id
    db.info["workspace_id"] = context.workspace_id
    db.info.pop("trusted_global_scope", None)
    _set_database_workspace(db, context.workspace_id)


def set_active_workspace(workspace_id: int):
    return _active_workspace_id.set(workspace_id)


def reset_active_workspace(token) -> None:
    _active_workspace_id.reset(token)


def get_active_workspace_id() -> int | None:
    return _active_workspace_id.get()


def set_active_user(user_id: int | None):
    return _active_user_id.set(user_id)


def reset_active_user(token) -> None:
    _active_user_id.reset(token)


def get_active_user_id() -> int | None:
    return _active_user_id.get()


def set_trusted_workspace(db: Session, workspace_id: int) -> None:
    """Set scope for a trusted webhook/job identifier, never from client workspace input."""
    if hasattr(db, "info"):
        db.info["workspace_id"] = workspace_id
        db.info.pop("trusted_global_scope", None)
        _set_database_workspace(db, workspace_id)


def clear_session_tenant(db: Session) -> None:
    """Clear application tenant context without granting database-wide access.

    Tenant-scoped rows are intentionally invisible while no workspace is set.
    Callers that need to discover a tenant from a trusted external identifier
    must use :func:`find_tenant_row_across_workspaces`, which evaluates the
    lookup under one concrete workspace at a time.
    """
    if not hasattr(db, "info"):
        return
    db.info.pop("user_id", None)
    db.info.pop("workspace_id", None)
    db.info.pop("trusted_global_scope", None)
    _clear_database_workspace(db)


def workspace_ids(db: Session) -> list[int]:
    """Return trusted workspace identifiers from the non-tenant directory."""
    return list(db.scalars(select(Workspace.id).order_by(Workspace.id)))


def find_tenant_row_across_workspaces(db: Session, statement: Select):
    """Resolve one tenant row without ever enabling a global RLS bypass.

    The statement must target a ``TenantScoped`` model and should contain a
    selective, server-owned predicate. The session is left scoped to the
    matching workspace so the caller can safely continue the operation.
    """
    clear_session_tenant(db)
    known_workspace_ids = workspace_ids(db)
    for workspace_id in known_workspace_ids:
        set_trusted_workspace(db, workspace_id)
        row = db.scalar(statement)
        if row is not None:
            return row
    # Legacy SQLite fixtures can contain tenant rows created before the
    # workspace directory was seeded. SQLite has no PostgreSQL RLS to bypass,
    # so a direct fallback keeps local migration/test compatibility without
    # weakening the production path.
    if not known_workspace_ids and db.get_bind().dialect.name != "postgresql":
        row = db.scalar(statement)
        if row is not None:
            set_trusted_workspace(db, row.workspace_id)
            return row
    clear_session_tenant(db)
    return None


def _set_database_workspace(db: Session, workspace_id: int) -> None:
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    db.execute(text("SELECT set_config('expenseops.bypass_rls', 'off', true)"))
    db.execute(
        text("SELECT set_config('expenseops.workspace_id', :value, true)"),
        {"value": str(workspace_id)},
    )


def _clear_database_workspace(db: Session) -> None:
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    db.execute(text("SELECT set_config('expenseops.bypass_rls', 'off', true)"))
    db.execute(text("SELECT set_config('expenseops.workspace_id', '', true)"))


@event.listens_for(Session, "after_begin")
def _restore_database_workspace(session: Session, _transaction, connection) -> None:
    workspace_id = session.info.get("workspace_id")
    if connection.dialect.name != "postgresql":
        return
    if workspace_id is None:
        connection.execute(text("SELECT set_config('expenseops.bypass_rls', 'off', true)"))
        connection.execute(text("SELECT set_config('expenseops.workspace_id', '', true)"))
        return
    connection.execute(text("SELECT set_config('expenseops.bypass_rls', 'off', true)"))
    connection.execute(
        text("SELECT set_config('expenseops.workspace_id', :value, true)"),
        {"value": str(workspace_id)},
    )


@event.listens_for(Session, "do_orm_execute")
def _apply_workspace_criteria(execute_state) -> None:
    workspace_id = execute_state.session.info.get("workspace_id")
    if workspace_id is None or execute_state.execution_options.get("skip_tenant_scope"):
        return
    if execute_state.is_select or execute_state.is_update or execute_state.is_delete:
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                TenantScoped,
                lambda model: model.workspace_id == workspace_id,
                include_aliases=True,
            )
        )


@event.listens_for(Session, "before_flush")
def _assign_and_validate_workspace(session: Session, _flush_context, _instances) -> None:
    workspace_id = session.info.get("workspace_id")
    if workspace_id is None:
        return
    for instance in session.new:
        if isinstance(instance, TenantScoped):
            current = getattr(instance, "workspace_id", None)
            if current is None:
                instance.workspace_id = workspace_id
            elif current != workspace_id:
                raise ValueError("Cannot create a resource for another workspace")
    for instance in session.dirty | session.deleted:
        if isinstance(instance, TenantScoped) and instance.workspace_id != workspace_id:
            raise ValueError("Cannot mutate a resource from another workspace")

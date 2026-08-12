from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User, Workspace
from app.tenancy import require_membership

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(request: Request, db: DbSession) -> User:
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = db.scalar(select(User).where(User.id == user_id, User.status == "active"))
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_workspace(request: Request, db: DbSession, user: CurrentUser) -> Workspace:
    workspace_id = getattr(request.state, "workspace_id", None)
    if workspace_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    require_membership(db, user.id, workspace_id)
    workspace = db.scalar(select(Workspace).where(Workspace.id == workspace_id))
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


CurrentWorkspace = Annotated[Workspace, Depends(get_current_workspace)]

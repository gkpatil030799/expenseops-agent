from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, CurrentWorkspace, DbSession
from app.models import AuthIdentity

router = APIRouter(prefix="/api", tags=["context"])


@router.get("/context")
def read_context(db: DbSession, user: CurrentUser, workspace: CurrentWorkspace) -> dict:
    avatar_url = db.scalar(
        select(AuthIdentity.avatar_url)
        .where(AuthIdentity.user_id == user.id)
        .order_by(AuthIdentity.last_login_at.desc())
    )
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "avatar_url": avatar_url,
        },
        "workspace": {
            "id": workspace.id,
            "name": workspace.name,
            "workspace_type": workspace.workspace_type,
        },
    }

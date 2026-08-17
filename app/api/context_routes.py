from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, CurrentWorkspace, DbSession
from app.config import get_settings
from app.models import AuthIdentity

router = APIRouter(prefix="/api", tags=["context"])


@router.get("/context")
def read_context(db: DbSession, user: CurrentUser, workspace: CurrentWorkspace) -> dict:
    settings = get_settings()
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
        "features": {
            "agent": {
                "enabled": settings.agent_enabled and settings.agent_read_tools_enabled,
                "read_only": not (settings.agent_enabled and settings.agent_write_actions_enabled),
                "proactive_enabled": (
                    settings.agent_enabled
                    and settings.agent_read_tools_enabled
                    and settings.agent_proactive_enabled
                ),
            }
        },
    }

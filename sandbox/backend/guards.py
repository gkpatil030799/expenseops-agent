from __future__ import annotations

from fastapi import HTTPException

from sandbox.backend.config import SandboxSettings, get_sandbox_settings


def require_sandbox_lab_enabled() -> SandboxSettings:
    settings = get_sandbox_settings()
    if not settings.enabled:
        raise HTTPException(status_code=404, detail="Sandbox Lab is disabled.")
    return settings


def require_sandbox_plaid_env() -> SandboxSettings:
    settings = require_sandbox_lab_enabled()
    if settings.plaid_env != "sandbox":
        raise HTTPException(
            status_code=403,
            detail="Sandbox Lab requires PLAID_ENV=sandbox.",
        )
    return settings


def ensure_sandbox_access_token(access_token: str | None) -> None:
    if not access_token or not access_token.startswith("access-sandbox-"):
        raise HTTPException(
            status_code=403,
            detail="Sandbox Lab refuses non-sandbox Plaid access tokens.",
        )

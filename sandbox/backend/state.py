from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sandbox.backend.config import STATE_PATH


def utc_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def redact_token(token: str | None) -> str | None:
    if not token:
        return None
    if len(token) <= 16:
        return "[REDACTED]"
    return f"{token[:15]}...{token[-4:]}"


def redact_cursor(cursor: str | None) -> str | None:
    if not cursor:
        return None
    if len(cursor) <= 12:
        return "[REDACTED]"
    return f"{cursor[:8]}...{cursor[-4:]}"


@dataclass
class SandboxState:
    item_id: str | None = None
    item_db_id: int | None = None
    access_token: str | None = None
    access_token_redacted: str | None = None
    transactions_cursor: str | None = None
    webhook_url: str | None = None
    webhook_attached: bool = False
    latest_trace_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SandboxState:
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in data.items() if key in allowed})

    def to_safe_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["access_token"] = bool(self.access_token)
        data["access_token_redacted"] = self.access_token_redacted or redact_token(
            self.access_token
        )
        return data


class SandboxStateStore:
    def __init__(self, path: Path = STATE_PATH):
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> SandboxState:
        if not self.path.exists():
            return SandboxState()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return SandboxState.from_dict(data)

    def save(self, state: SandboxState) -> SandboxState:
        now = utc_iso()
        if not state.created_at:
            state.created_at = now
        state.updated_at = now
        state.access_token_redacted = redact_token(state.access_token)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
        return state

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()

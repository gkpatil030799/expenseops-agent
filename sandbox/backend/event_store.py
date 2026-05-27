from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sandbox.backend.config import EVENT_LOG_PATH
from sandbox.backend.state import utc_iso


@dataclass
class SandboxEvent:
    id: str
    trace_id: str
    event_type: str
    status: str
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    plaid_request_id: str | None = None
    plaid_item_id: str | None = None
    created_at: str = field(default_factory=utc_iso)


class SandboxEventStore:
    def __init__(self, path: Path = EVENT_LOG_PATH):
        self.path = path

    def append(
        self,
        *,
        trace_id: str,
        event_type: str,
        status: str,
        message: str = "",
        payload: dict[str, Any] | None = None,
        plaid_request_id: str | None = None,
        plaid_item_id: str | None = None,
    ) -> SandboxEvent:
        event = SandboxEvent(
            id=uuid.uuid4().hex,
            trace_id=trace_id,
            event_type=event_type,
            status=status,
            message=message,
            payload=payload or {},
            plaid_request_id=plaid_request_id,
            plaid_item_id=plaid_item_id,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), default=str) + "\n")
        return event

    def read(self, *, trace_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if trace_id and event.get("trace_id") != trace_id:
                continue
            events.append(event)
        return events[-limit:]

    def latest(self) -> dict[str, Any] | None:
        events = self.read(limit=1)
        return events[-1] if events else None

    def has_event(self, *, trace_id: str, event_type: str) -> bool:
        return any(
            event.get("event_type") == event_type
            for event in self.read(trace_id=trace_id, limit=1000)
        )

    def count_recent(
        self,
        *,
        trace_id: str,
        event_type: str,
        seconds: int,
    ) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=seconds)
        count = 0
        for event in self.read(trace_id=trace_id, limit=1000):
            if event.get("event_type") != event_type:
                continue
            created_at = str(event.get("created_at") or "")
            try:
                event_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if event_time >= cutoff:
                count += 1
        return count

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


def new_trace_id() -> str:
    return f"sandbox_{utc_iso()[:10].replace('-', '')}_{uuid.uuid4().hex[:8]}"

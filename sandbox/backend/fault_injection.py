from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sandbox.backend.event_store import SandboxEventStore


@dataclass
class SandboxFault:
    name: str
    trace_id: str
    expires_at: float
    consumed: bool = False
    payload: dict[str, Any] | None = None


class SandboxFaultStore:
    def __init__(self):
        self._faults: dict[tuple[str, str], SandboxFault] = {}

    def enable(
        self,
        *,
        name: str,
        trace_id: str,
        event_store: SandboxEventStore,
        ttl_seconds: int = 300,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._faults[(trace_id, name)] = SandboxFault(
            name=name,
            trace_id=trace_id,
            expires_at=time.monotonic() + ttl_seconds,
            payload=payload or {},
        )
        event_store.append(
            trace_id=trace_id,
            event_type="sandbox_fault_enabled",
            status="info",
            payload={"fault": name, **(payload or {})},
        )

    def consume(
        self,
        *,
        name: str,
        trace_id: str,
        event_store: SandboxEventStore,
    ) -> bool:
        key = (trace_id, name)
        fault = self._faults.get(key)
        if fault is None:
            return False
        if fault.consumed:
            return False
        if time.monotonic() > fault.expires_at:
            self._faults.pop(key, None)
            event_store.append(
                trace_id=trace_id,
                event_type="sandbox_fault_expired",
                status="info",
                payload={"fault": name},
            )
            return False
        fault.consumed = True
        event_store.append(
            trace_id=trace_id,
            event_type="sandbox_fault_consumed",
            status="info",
            payload={"fault": name, **(fault.payload or {})},
        )
        return True

    def clear(self, *, trace_id: str, event_store: SandboxEventStore) -> None:
        keys = [key for key in self._faults if key[0] == trace_id]
        for key in keys:
            self._faults.pop(key, None)
        event_store.append(
            trace_id=trace_id,
            event_type="sandbox_fault_cleared",
            status="info",
            payload={"cleared_count": len(keys)},
        )


fault_store = SandboxFaultStore()

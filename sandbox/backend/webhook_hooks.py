from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sandbox.backend.config import get_sandbox_settings
from sandbox.backend.event_store import SandboxEventStore
from sandbox.backend.state import SandboxStateStore

_TRACE_PATTERN = re.compile(r"\[trace:((?:sandbox|scenario)_[^\]]+)\]")
_SYNC_KEYS_IN_PROGRESS: set[str] = set()


@dataclass(frozen=True)
class SandboxSyncGuard:
    key: str
    trace_id: str
    plaid_item_id: str


def sandbox_trace_for_item(
    plaid_item_id: str | None,
    *,
    state_store: SandboxStateStore | None = None,
) -> str | None:
    try:
        settings = get_sandbox_settings()
        if not settings.enabled or settings.plaid_env != "sandbox" or not plaid_item_id:
            return None
        state = (state_store or SandboxStateStore()).load()
        if state.item_id != plaid_item_id:
            return None
        return state.latest_trace_id
    except Exception:
        return None


def sandbox_sync_guard_start(
    plaid_item_id: str | None,
    *,
    source_action: str,
    state_store: SandboxStateStore | None = None,
    event_store: SandboxEventStore | None = None,
) -> tuple[bool, SandboxSyncGuard | None]:
    trace_id = sandbox_trace_for_item(plaid_item_id, state_store=state_store)
    if not trace_id or not plaid_item_id:
        return False, None
    store = event_store or SandboxEventStore()
    if (
        store.count_recent(
            trace_id=trace_id,
            event_type="plaid_transactions_sync_started",
            seconds=60,
        )
        >= 3
    ):
        store.append(
            trace_id=trace_id,
            event_type="sandbox_loop_guard_triggered",
            status="failed",
            message="More than 3 sync attempts for this trace within 60 seconds.",
            payload={
                "source_action": source_action,
                "item_id": plaid_item_id,
                "reason": "sync_attempt_limit_exceeded",
            },
            plaid_item_id=plaid_item_id,
        )
        return True, None

    key = f"{plaid_item_id}:{trace_id}"
    if key in _SYNC_KEYS_IN_PROGRESS:
        store.append(
            trace_id=trace_id,
            event_type="sandbox_sync_skipped_already_running",
            status="info",
            payload={
                "source_action": source_action,
                "item_id": plaid_item_id,
                "reason": "sync_already_running",
            },
            plaid_item_id=plaid_item_id,
        )
        return True, None

    _SYNC_KEYS_IN_PROGRESS.add(key)
    return False, SandboxSyncGuard(
        key=key,
        trace_id=trace_id,
        plaid_item_id=plaid_item_id,
    )


def sandbox_sync_guard_finish(guard: SandboxSyncGuard | None) -> None:
    if guard:
        _SYNC_KEYS_IN_PROGRESS.discard(guard.key)


def maybe_log_sandbox_webhook(payload: dict[str, Any]) -> None:
    try:
        settings = get_sandbox_settings()
        if not settings.enabled or settings.plaid_env != "sandbox":
            return
        state = SandboxStateStore().load()
        plaid_item_id = payload.get("item_id")
        if state.item_id and plaid_item_id != state.item_id:
            return
        trace_id = state.latest_trace_id or "sandbox_unknown"
        SandboxEventStore().append(
            trace_id=trace_id,
            event_type="plaid_webhook_received",
            status="info",
            message="Existing Plaid webhook endpoint received a sandbox webhook.",
            payload={
                "webhook_type": payload.get("webhook_type"),
                "webhook_code": payload.get("webhook_code"),
                "source_action": "webhook_handler",
                "item_id": str(plaid_item_id) if plaid_item_id else None,
            },
            plaid_item_id=str(plaid_item_id) if plaid_item_id else None,
        )
    except Exception:
        return


def maybe_log_sandbox_telegram_event(
    tx: Any,
    *,
    event_type: str,
    status: str,
    payload: dict[str, Any] | None = None,
) -> None:
    try:
        settings = get_sandbox_settings()
        if not settings.enabled or settings.plaid_env != "sandbox":
            return
        trace_id = _trace_id_from_transaction(tx)
        if not trace_id:
            return
        state = SandboxStateStore().load()
        SandboxEventStore().append(
            trace_id=trace_id,
            event_type=event_type,
            status=status,
            payload={
                "transaction_id": getattr(tx, "id", None),
                "plaid_transaction_id": getattr(tx, "plaid_transaction_id", None),
                "source_action": "webhook_handler",
                "item_id": state.item_id,
                **(payload or {}),
            },
            plaid_item_id=state.item_id,
        )
    except Exception:
        return


def _trace_id_from_transaction(tx: Any) -> str | None:
    for value in (getattr(tx, "name", None), getattr(tx, "merchant_name", None)):
        if not value:
            continue
        match = _TRACE_PATTERN.search(str(value))
        if match:
            return match.group(1)
    return None

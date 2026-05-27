from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.logging_config import log_event
from sandbox.backend.config import SandboxSettings
from sandbox.backend.event_store import SandboxEventStore, new_trace_id
from sandbox.backend.guards import ensure_sandbox_access_token
from sandbox.backend.plaid_sandbox_service import (
    SANDBOX_USERNAME,
    PlaidSandboxService,
    SandboxPlaidError,
)
from sandbox.backend.schemas import CreateTransactionRequest, StepStatus
from sandbox.backend.state import SandboxState, SandboxStateStore, redact_cursor
from sandbox.backend.transaction_sync_adapter import SandboxTransactionSyncAdapter
from sandbox.backend.webhook_hooks import sandbox_sync_guard_finish, sandbox_sync_guard_start

logger = logging.getLogger(__name__)


class SandboxOrchestrator:
    def __init__(
        self,
        *,
        db: Session,
        settings: SandboxSettings,
        state_store: SandboxStateStore | None = None,
        event_store: SandboxEventStore | None = None,
        plaid: PlaidSandboxService | None = None,
    ):
        self.db = db
        self.settings = settings
        self.state_store = state_store or SandboxStateStore()
        self.event_store = event_store or SandboxEventStore()
        self.plaid = plaid or PlaidSandboxService()
        self.sync_adapter = SandboxTransactionSyncAdapter(db)

    def status(self) -> dict[str, Any]:
        state = self.state_store.load()
        latest = self.event_store.latest()
        return {
            "enabled": self.settings.enabled,
            "plaid_env": self.settings.plaid_env,
            "webhook_url_configured": bool(self.settings.webhook_url),
            "webhook_url": self.settings.webhook_url or None,
            "state_exists": self.state_store.exists(),
            "item_id": state.item_id,
            "item_db_id": state.item_db_id,
            "access_token_exists": bool(state.access_token),
            "access_token_redacted": state.access_token_redacted,
            "transactions_cursor_exists": bool(state.transactions_cursor),
            "latest_event_timestamp": latest.get("created_at") if latest else None,
            "latest_known_trace_id": state.latest_trace_id,
        }

    def create_item(self, trace_id: str | None = None) -> dict[str, Any]:
        trace_id = trace_id or new_trace_id()
        steps: list[StepStatus] = []
        self._event(trace_id, "sandbox_item_create_started", "started")
        try:
            public_token_data = self.plaid.create_public_token()
            exchange = self.plaid.exchange_public_token(public_token_data["public_token"])
            access_token = str(exchange["access_token"])
            ensure_sandbox_access_token(access_token)
            item_id = str(exchange["item_id"])
            app_item = self.sync_adapter.ensure_app_plaid_item(
                item_id=item_id,
                access_token=access_token,
            )
            state = self.state_store.save(
                SandboxState(
                    item_id=item_id,
                    item_db_id=app_item.id,
                    access_token=access_token,
                    webhook_url=None,
                    webhook_attached=False,
                    latest_trace_id=trace_id,
                )
            )
            self._event(
                trace_id,
                "sandbox_item_create_succeeded",
                "succeeded",
                plaid_item_id=item_id,
                payload={"item_db_id": app_item.id, "webhook_attached": False},
            )
            log_event(
                logger,
                "sandbox_item_create_succeeded",
                trace_id=trace_id,
                plaid_item_id=item_id,
                plaid_item_db_id=app_item.id,
                sandbox_username=SANDBOX_USERNAME,
            )
            steps.append(StepStatus(name="sandbox_item_create", status="success"))
            return {
                "trace_id": trace_id,
                "item_id": item_id,
                "access_token": state.access_token_redacted,
                "webhook_url": self.settings.webhook_url or "",
                "steps": steps,
            }
        except Exception as exc:
            self._event(trace_id, "sandbox_item_create_failed", "failed", message=str(exc))
            raise

    def ensure_item_ready(self, trace_id: str) -> SandboxState:
        state = self.state_store.load()
        if state.item_id and state.access_token:
            state.latest_trace_id = trace_id
            self.state_store.save(state)
            return self._mirror_cursor_from_app_item(state, trace_id, source="ensure_item_ready")
        self.create_item(trace_id=trace_id)
        return self._mirror_cursor_from_app_item(
            self.state_store.load(),
            trace_id,
            source="ensure_item_ready",
        )

    def init_sync(self, trace_id: str | None = None) -> dict[str, Any]:
        trace_id = trace_id or new_trace_id()
        state = self._mirror_cursor_from_app_item(
            self.state_store.load(),
            trace_id,
            source="sandbox_init_sync",
        )
        self._require_state(state)
        self._event(trace_id, "sandbox_sync_init_started", "started", plaid_item_id=state.item_id)
        try:
            result = self._direct_sync_with_state_cursor(
                state,
                cursor=state.transactions_cursor,
                trace_id=trace_id,
                source="sandbox_init_sync",
            )
            self._event(
                trace_id,
                "sandbox_sync_init_succeeded",
                "succeeded",
                plaid_item_id=state.item_id,
                payload=result,
            )
            return {"trace_id": trace_id, "item_id": state.item_id, **result}
        except Exception as exc:
            self._event(trace_id, "sandbox_sync_init_failed", "failed", message=str(exc))
            raise

    def create_transaction(
        self,
        payload: CreateTransactionRequest,
        *,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        trace_id = trace_id or new_trace_id()
        state = self._mirror_cursor_from_app_item(
            self.state_store.load(),
            trace_id,
            source="sandbox_create_transaction",
        )
        self._require_state(state)
        ensure_sandbox_access_token(state.access_token)
        state = self._ensure_webhook_detached_for_create(state, trace_id)
        date_transacted = payload.date_transacted or date.today()
        date_posted = payload.date_posted or date.today()
        self._validate_sandbox_date(date_transacted)
        self._validate_sandbox_date(date_posted)
        description = self._description_with_trace(payload.description, trace_id)
        self._event(
            trace_id,
            "sandbox_transaction_create_started",
            "started",
            plaid_item_id=state.item_id,
            payload={
                "description": description,
                "amount": str(payload.amount),
                "auto_fire_webhook": payload.auto_fire_webhook,
                "auto_sync_after": payload.auto_sync_after,
            },
            source_action="create_only",
        )
        try:
            response = self.plaid.create_transaction(
                access_token=state.access_token or "",
                description=description,
                amount=payload.amount,
                iso_currency_code=payload.iso_currency_code,
                date_transacted=date_transacted,
                date_posted=date_posted,
            )
            request_id = response.get("request_id")
            self._event(
                trace_id,
                "sandbox_transaction_create_succeeded",
                "succeeded",
                plaid_request_id=request_id,
                plaid_item_id=state.item_id,
                payload={
                    "description": description,
                    "amount": str(payload.amount),
                    "auto_fire_webhook": payload.auto_fire_webhook,
                    "auto_sync_after": payload.auto_sync_after,
                },
                source_action="create_only",
            )
            steps = [StepStatus(name="transaction_created", status="success")]
            if payload.auto_fire_webhook:
                self.fire_webhook(trace_id=trace_id)
            if payload.auto_sync_after:
                self.sync_now(trace_id=trace_id)
            return {
                "trace_id": trace_id,
                "transaction": {
                    "description": description,
                    "amount": str(payload.amount),
                    "iso_currency_code": payload.iso_currency_code,
                    "date_transacted": date_transacted.isoformat(),
                    "date_posted": date_posted.isoformat(),
                },
                "plaid_request_id": request_id,
                "created": True,
                "steps": steps,
            }
        except SandboxPlaidError as exc:
            self._event(
                trace_id,
                "sandbox_transaction_create_failed",
                "failed",
                message=str(exc),
                plaid_request_id=exc.request_id,
            )
            raise

    def fire_webhook(
        self,
        *,
        trace_id: str | None = None,
        webhook_type: str = "TRANSACTIONS",
        webhook_code: str = "SYNC_UPDATES_AVAILABLE",
    ) -> dict[str, Any]:
        trace_id = trace_id or new_trace_id()
        state = self._mirror_cursor_from_app_item(
            self.state_store.load(),
            trace_id,
            source="sandbox_fire_webhook",
        )
        self._require_state(state)
        ensure_sandbox_access_token(state.access_token)
        state = self._ensure_webhook_attached(state, trace_id)
        state.latest_trace_id = trace_id
        self.state_store.save(state)
        self._event(
            trace_id,
            "sandbox_webhook_fire_started",
            "started",
            plaid_item_id=state.item_id,
            source_action="fire_webhook",
        )
        try:
            response = self.plaid.fire_webhook(
                access_token=state.access_token or "",
                webhook_type=webhook_type,
                webhook_code=webhook_code,
            )
            request_id = response.get("request_id")
            self._event(
                trace_id,
                "sandbox_webhook_fire_succeeded",
                "succeeded",
                plaid_request_id=request_id,
                plaid_item_id=state.item_id,
                payload={"webhook_type": webhook_type, "webhook_code": webhook_code},
                source_action="fire_webhook",
            )
            return {
                "trace_id": trace_id,
                "webhook_type": webhook_type,
                "webhook_code": webhook_code,
                "webhook_fired": True,
                "plaid_request_id": request_id,
            }
        except SandboxPlaidError as exc:
            self._event(
                trace_id,
                "sandbox_webhook_fire_failed",
                "failed",
                message=str(exc),
                plaid_request_id=exc.request_id,
            )
            raise

    def sync_now(self, trace_id: str | None = None) -> dict[str, Any]:
        trace_id = trace_id or new_trace_id()
        state = self._mirror_cursor_from_app_item(
            self.state_store.load(),
            trace_id,
            source="sandbox_sync_now",
        )
        self._require_state(state)
        ensure_sandbox_access_token(state.access_token)
        cursor_before = state.transactions_cursor
        skipped, sync_guard = sandbox_sync_guard_start(
            state.item_id,
            source_action="manual_sync",
            state_store=self.state_store,
            event_store=self.event_store,
        )
        if skipped:
            return {
                "trace_id": trace_id,
                "added_count": 0,
                "modified_count": 0,
                "removed_count": 0,
                "cursor_present": bool(cursor_before),
                "cursor_updated": False,
                "next_cursor_present": bool(cursor_before),
                "added_transactions": [],
                "skipped": True,
            }
        self._event(
            trace_id,
            "plaid_transactions_sync_cursor_loaded",
            "info",
            plaid_item_id=state.item_id,
            payload={
                "source": "sandbox_sync_now",
                "cursor_present": bool(cursor_before),
                "cursor": redact_cursor(cursor_before),
            },
        )
        log_event(
            logger,
            "plaid_transactions_sync_cursor_loaded",
            trace_id=trace_id,
            source="sandbox_sync_now",
            plaid_item_id=state.item_id,
            cursor_present=bool(cursor_before),
            cursor=redact_cursor(cursor_before),
        )
        self._event(
            trace_id,
            "plaid_transactions_sync_started",
            "started",
            plaid_item_id=state.item_id,
            payload={
                "source": "sandbox_sync_now",
                "cursor_present": bool(cursor_before),
                "cursor": redact_cursor(cursor_before),
            },
        )
        try:
            app_item = self.sync_adapter.ensure_app_plaid_item(
                item_id=state.item_id or "",
                access_token=state.access_token or "",
                cursor=state.transactions_cursor,
            )
            before_ids = self.sync_adapter.transaction_ids_for_item(app_item)
            result = self.sync_adapter.sync_existing_pipeline(app_item)
            after_ids = self.sync_adapter.transaction_ids_for_item(app_item)
            state.transactions_cursor = app_item.cursor
            state.item_db_id = app_item.id
            state.latest_trace_id = trace_id
            self.state_store.save(state)
            added_transactions = self.sync_adapter.transactions_by_ids(after_ids - before_ids)
            if not added_transactions:
                added_transactions = self.sync_adapter.latest_added_transactions(app_item)
            cursor_updated = cursor_before != app_item.cursor
            payload = {
                "added_count": result.get("added", 0),
                "modified_count": result.get("modified", 0),
                "removed_count": result.get("removed", 0),
                "cursor_present": bool(cursor_before),
                "cursor_updated": cursor_updated,
                "next_cursor_present": bool(app_item.cursor),
                "added_transactions": added_transactions,
            }
            self._event(
                trace_id,
                "plaid_transactions_sync_cursor_saved",
                "info",
                plaid_item_id=state.item_id,
                payload={
                    "source": "sandbox_sync_now",
                    "cursor_updated": cursor_updated,
                    "next_cursor_present": bool(app_item.cursor),
                    "next_cursor": redact_cursor(app_item.cursor),
                },
            )
            log_event(
                logger,
                "plaid_transactions_sync_cursor_saved",
                trace_id=trace_id,
                source="sandbox_sync_now",
                plaid_item_id=state.item_id,
                cursor_updated=cursor_updated,
                next_cursor_present=bool(app_item.cursor),
                next_cursor=redact_cursor(app_item.cursor),
            )
            self._event(
                trace_id,
                "plaid_transactions_sync_completed",
                "succeeded",
                plaid_item_id=state.item_id,
                payload=payload,
            )
            return {"trace_id": trace_id, **payload}
        except Exception as exc:
            payload = self._safe_exception_payload(exc, trace_id=trace_id, state=state)
            if isinstance(exc, IntegrityError):
                self._event(
                    trace_id,
                    "sandbox_integrity_error",
                    "failed",
                    message=payload.get("exception_class") or type(exc).__name__,
                    plaid_item_id=state.item_id,
                    payload=payload,
                )
                log_event(
                    logger,
                    "sandbox_integrity_error",
                    level=logging.WARNING,
                    **payload,
                )
            self._event(
                trace_id,
                "plaid_transactions_sync_failed",
                "failed",
                message=payload.get("exception_class") or type(exc).__name__,
                plaid_item_id=state.item_id,
                payload=payload,
            )
            raise
        finally:
            sandbox_sync_guard_finish(sync_guard)

    def run_e2e(self) -> dict[str, Any]:
        trace_id = new_trace_id()
        steps: list[StepStatus] = []
        details: dict[str, Any] = {}
        self._event(trace_id, "sandbox_e2e_started", "started", source_action="e2e")
        try:
            state = self.ensure_item_ready(trace_id)
            steps.append(StepStatus(name="sandbox_item_ready", status="success"))
            if not state.transactions_cursor:
                details["init_sync"] = self.init_sync(trace_id=trace_id)
                steps.append(StepStatus(name="sync_initialized", status="success"))
            else:
                steps.append(StepStatus(name="sync_initialized", status="success"))
            details["transaction"] = self.create_transaction(
                CreateTransactionRequest(),
                trace_id=trace_id,
            )
            steps.append(StepStatus(name="transaction_created", status="success"))
            details["webhook"] = self.fire_webhook(trace_id=trace_id)
            steps.append(StepStatus(name="webhook_fired", status="success"))
            webhook_received = self._webhook_received_for_trace(trace_id)
            steps.append(
                StepStatus(
                    name="webhook_received",
                    status="success" if webhook_received else "unknown",
                )
            )
            sync_attempts: list[dict[str, Any]] = []
            sync_success = False
            for attempt in range(1, 5):
                sync_result = self.sync_now(trace_id=trace_id)
                sync_result["attempt"] = attempt
                sync_attempts.append(sync_result)
                if self._sync_result_contains_trace(sync_result, trace_id):
                    sync_success = True
                    break
                if sync_result.get("added_count", 0) > 0:
                    sync_success = True
                    break
                if attempt < 4:
                    time.sleep(1)
            details["fallback_sync_attempts"] = sync_attempts
            steps.append(
                StepStatus(
                    name="sync_completed",
                    status="fallback" if sync_success else "unknown",
                    detail=f"poll_attempts={len(sync_attempts)}",
                )
            )
            self._event(
                trace_id,
                "sandbox_e2e_completed",
                "succeeded",
                payload=details,
                source_action="e2e",
            )
            return {
                "trace_id": trace_id,
                "status": "completed",
                "steps": steps,
                "details": details,
            }
        except Exception as exc:
            self._event(
                trace_id,
                "sandbox_e2e_failed",
                "failed",
                message=type(exc).__name__,
                source_action="e2e",
            )
            steps.append(StepStatus(name="failed", status="failed", detail=type(exc).__name__))
            return {
                "trace_id": trace_id,
                "status": "failed",
                "steps": steps,
                "details": details,
            }

    def _direct_sync_with_state_cursor(
        self,
        state: SandboxState,
        *,
        cursor: str | None,
        trace_id: str,
        source: str,
    ) -> dict[str, Any]:
        has_more = True
        added = 0
        modified = 0
        removed = 0
        next_cursor = cursor
        cursor_before = cursor
        page = 0
        self._event(
            trace_id,
            "plaid_transactions_sync_cursor_loaded",
            "info",
            plaid_item_id=state.item_id,
            payload={
                "source": source,
                "cursor_present": bool(cursor_before),
                "cursor": redact_cursor(cursor_before),
                "sandbox_username": SANDBOX_USERNAME,
            },
        )
        log_event(
            logger,
            "plaid_transactions_sync_cursor_loaded",
            trace_id=trace_id,
            source=source,
            plaid_item_id=state.item_id,
            cursor_present=bool(cursor_before),
            cursor=redact_cursor(cursor_before),
            sandbox_username=SANDBOX_USERNAME,
        )
        while has_more:
            request_cursor = next_cursor
            response = self.plaid.transactions_sync(
                access_token=state.access_token or "",
                cursor=request_cursor,
            )
            page += 1
            page_added = len(response.get("added", []))
            page_modified = len(response.get("modified", []))
            page_removed = len(response.get("removed", []))
            added += page_added
            modified += page_modified
            removed += page_removed
            next_cursor = response.get("next_cursor")
            has_more = bool(response.get("has_more"))
            page_payload = {
                "source": source,
                "page": page,
                "request_cursor_present": bool(request_cursor),
                "request_cursor": redact_cursor(request_cursor),
                "has_more": has_more,
                "added_count": page_added,
                "modified_count": page_modified,
                "removed_count": page_removed,
                "next_cursor_present": bool(next_cursor),
                "next_cursor": redact_cursor(next_cursor),
            }
            self._event(
                trace_id,
                "plaid_transactions_sync_page_received",
                "info",
                plaid_item_id=state.item_id,
                payload=page_payload,
            )
            log_event(
                logger,
                "plaid_transactions_sync_page_received",
                trace_id=trace_id,
                plaid_item_id=state.item_id,
                **page_payload,
            )
        state.transactions_cursor = next_cursor
        state.latest_trace_id = state.latest_trace_id
        if state.item_id and state.access_token:
            app_item = self.sync_adapter.ensure_app_plaid_item(
                item_id=state.item_id,
                access_token=state.access_token,
                cursor=next_cursor,
            )
            state.item_db_id = app_item.id
        self.state_store.save(state)
        cursor_updated = cursor_before != next_cursor
        self._event(
            trace_id,
            "plaid_transactions_sync_cursor_saved",
            "info",
            plaid_item_id=state.item_id,
            payload={
                "source": source,
                "cursor_updated": cursor_updated,
                "next_cursor_present": bool(next_cursor),
                "next_cursor": redact_cursor(next_cursor),
            },
        )
        log_event(
            logger,
            "plaid_transactions_sync_cursor_saved",
            trace_id=trace_id,
            source=source,
            plaid_item_id=state.item_id,
            cursor_updated=cursor_updated,
            next_cursor_present=bool(next_cursor),
            next_cursor=redact_cursor(next_cursor),
        )
        return {
            "added_count": added,
            "modified_count": modified,
            "removed_count": removed,
            "has_cursor": bool(next_cursor),
            "cursor_present": bool(cursor_before),
            "cursor_updated": cursor_updated,
            "next_cursor_present": bool(next_cursor),
        }

    def _mirror_cursor_from_app_item(
        self,
        state: SandboxState,
        trace_id: str,
        *,
        source: str,
    ) -> SandboxState:
        if not state.item_id:
            return state
        app_item = self.sync_adapter.get_app_plaid_item(state.item_id)
        if app_item is None:
            return state
        state.item_db_id = app_item.id
        if app_item.cursor and app_item.cursor != state.transactions_cursor:
            state.transactions_cursor = app_item.cursor
            self.state_store.save(state)
            self._event(
                trace_id,
                "sandbox_state_cursor_mirrored",
                "info",
                plaid_item_id=state.item_id,
                payload={
                    "source": source,
                    "cursor_source": "app_plaid_item",
                    "cursor_present": True,
                    "cursor": redact_cursor(app_item.cursor),
                },
            )
            log_event(
                logger,
                "sandbox_state_cursor_mirrored",
                trace_id=trace_id,
                source=source,
                cursor_source="app_plaid_item",
                plaid_item_id=state.item_id,
                cursor_present=True,
                cursor=redact_cursor(app_item.cursor),
            )
        return state

    def _ensure_webhook_detached_for_create(
        self,
        state: SandboxState,
        trace_id: str,
    ) -> SandboxState:
        actual_webhook_url = self.plaid.get_item_webhook(access_token=state.access_token or "")
        if not actual_webhook_url and not state.webhook_attached and not state.webhook_url:
            self._event(
                trace_id,
                "sandbox_webhook_already_detached",
                "info",
                plaid_item_id=state.item_id,
                payload={
                    "reason": "create_only_boundary",
                    "actual_webhook_attached": False,
                    "state_webhook_attached": False,
                },
                source_action="create_only",
            )
            return state
        self._event(
            trace_id,
            "sandbox_item_webhook_detach_started",
            "started",
            plaid_item_id=state.item_id,
            payload={
                "reason": "create_only_boundary",
                "actual_webhook_attached": bool(actual_webhook_url),
                "state_webhook_attached": state.webhook_attached,
            },
            source_action="create_only",
        )
        self.plaid.update_webhook(access_token=state.access_token or "", webhook_url=None)
        state.webhook_url = None
        state.webhook_attached = False
        state.latest_trace_id = trace_id
        self.state_store.save(state)
        self._event(
            trace_id,
            "sandbox_item_webhook_detached",
            "succeeded",
            plaid_item_id=state.item_id,
            payload={
                "reason": "create_only_boundary",
                "actual_webhook_attached": bool(actual_webhook_url),
            },
            source_action="create_only",
        )
        log_event(
            logger,
            "sandbox_item_webhook_detached",
            trace_id=trace_id,
            plaid_item_id=state.item_id,
            reason="create_only_boundary",
        )
        return state

    def _ensure_webhook_attached(self, state: SandboxState, trace_id: str) -> SandboxState:
        webhook_url = self._require_webhook_url()
        actual_webhook_url = self.plaid.get_item_webhook(access_token=state.access_token or "")
        if (
            actual_webhook_url == webhook_url
            and state.webhook_attached
            and state.webhook_url == webhook_url
        ):
            self._event(
                trace_id,
                "sandbox_webhook_already_attached",
                "info",
                plaid_item_id=state.item_id,
                payload={
                    "webhook_url_configured": True,
                    "actual_webhook_matches": True,
                },
                source_action="fire_webhook",
            )
            return state
        self._event(
            trace_id,
            "sandbox_item_webhook_attach_started",
            "started",
            plaid_item_id=state.item_id,
            payload={
                "actual_webhook_attached": bool(actual_webhook_url),
                "actual_webhook_matches": actual_webhook_url == webhook_url,
                "state_webhook_attached": state.webhook_attached,
            },
            source_action="fire_webhook",
        )
        self.plaid.update_webhook(access_token=state.access_token or "", webhook_url=webhook_url)
        state.webhook_url = webhook_url
        state.webhook_attached = True
        state.latest_trace_id = trace_id
        self.state_store.save(state)
        self._event(
            trace_id,
            "sandbox_item_webhook_attached",
            "succeeded",
            plaid_item_id=state.item_id,
            payload={"webhook_url_configured": True},
            source_action="fire_webhook",
        )
        log_event(
            logger,
            "sandbox_item_webhook_attached",
            trace_id=trace_id,
            plaid_item_id=state.item_id,
        )
        return state

    def _require_webhook_url(self) -> str:
        if not self.settings.webhook_url:
            raise HTTPException(
                status_code=400,
                detail="PLAID_WEBHOOK_URL or SANDBOX_PUBLIC_WEBHOOK_URL is required.",
            )
        return self.settings.webhook_url

    def _require_state(self, state: SandboxState) -> None:
        if not state.item_id or not state.access_token:
            raise HTTPException(status_code=400, detail="Create a sandbox item first.")

    def _validate_sandbox_date(self, value: date) -> None:
        today = date.today()
        if value > today or value < today - timedelta(days=14):
            raise HTTPException(
                status_code=400,
                detail="Sandbox transaction dates must be today or within the last 14 days.",
            )

    def _description_with_trace(self, description: str, trace_id: str) -> str:
        if "[trace:" in description:
            return description
        return f"{description} [trace:{trace_id}]"

    def _webhook_received_for_trace(self, trace_id: str) -> bool:
        return any(
            event.get("event_type") == "plaid_webhook_received"
            for event in self.event_store.read(trace_id=trace_id, limit=20)
        )

    def _sync_result_contains_trace(self, sync_result: dict[str, Any], trace_id: str) -> bool:
        return any(
            trace_id in str(tx.get("name") or tx.get("merchant_name") or "")
            for tx in sync_result.get("added_transactions", [])
        )

    def _safe_exception_payload(
        self,
        exc: Exception,
        *,
        trace_id: str,
        state: SandboxState,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "trace_id": trace_id,
            "plaid_item_id": state.item_id,
            "plaid_item_db_id": state.item_db_id,
            "exception_class": type(exc).__name__,
        }
        original = getattr(exc, "orig", None)
        text = " ".join(str(original or exc).split())
        payload["original_error"] = text[:500]
        if isinstance(exc, IntegrityError):
            payload.update(_parse_integrity_error_text(text))
        return payload

    def _event(
        self,
        trace_id: str,
        event_type: str,
        status: str,
        *,
        message: str = "",
        payload: dict[str, Any] | None = None,
        plaid_request_id: str | None = None,
        plaid_item_id: str | None = None,
        source_action: str | None = None,
    ) -> None:
        event_payload = dict(payload or {})
        event_payload.setdefault(
            "source_action",
            source_action or _source_action_for_event(event_type),
        )
        event_payload.setdefault("item_id", plaid_item_id)
        self.event_store.append(
            trace_id=trace_id,
            event_type=event_type,
            status=status,
            message=message,
            payload=event_payload,
            plaid_request_id=plaid_request_id,
            plaid_item_id=plaid_item_id,
        )


def _parse_integrity_error_text(text: str) -> dict[str, Any]:
    details: dict[str, Any] = {
        "constraint_name": None,
        "table_name": None,
        "column_name": None,
        "plaid_transaction_id": None,
        "account_id": None,
    }
    marker = "UNIQUE constraint failed:"
    if marker in text:
        details["constraint_name"] = "unique"
        target = text.split(marker, 1)[1].strip().split()[0].strip(",")
        table, _, column = target.partition(".")
        details["table_name"] = table or None
        details["column_name"] = column or None
    marker = "NOT NULL constraint failed:"
    if marker in text:
        details["constraint_name"] = "not_null"
        target = text.split(marker, 1)[1].strip().split()[0].strip(",")
        table, _, column = target.partition(".")
        details["table_name"] = table or None
        details["column_name"] = column or None
    if "FOREIGN KEY constraint failed" in text:
        details["constraint_name"] = "foreign_key"
    return details


def _source_action_for_event(event_type: str) -> str:
    if event_type.startswith("sandbox_webhook_fire"):
        return "fire_webhook"
    if event_type.startswith("sandbox_transaction_create"):
        return "create_only"
    if event_type.startswith("plaid_transactions_sync"):
        return "manual_sync"
    if event_type.startswith("sandbox_e2e"):
        return "e2e"
    return "sandbox_lab"

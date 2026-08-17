from __future__ import annotations

import hashlib
import html
import time
from datetime import UTC, datetime, timedelta
from datetime import time as day_time
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.contracts import AgentAttentionSummaryBlock, AgentStructuredResponse
from app.agent.read_tools import build_read_tool_registry
from app.agent.runtime import ReadToolEvidence, compose_proactive_attention_response
from app.agent.tooling import AgentToolContext
from app.attention_schemas import AttentionCategory, AttentionPreferencePatch
from app.config import Settings, get_settings
from app.models import (
    ProactiveAttentionDelivery,
    ProactiveAttentionPreference,
    TelegramIdentity,
    utc_now,
)
from app.services.telegram_service import TelegramService

DEFAULT_CATEGORIES: tuple[AttentionCategory, ...] = (
    "transactions",
    "receipts",
    "integrations",
    "replenishment",
    "deals",
    "errands",
)

_CATEGORY_CALLS: dict[AttentionCategory, tuple[str, dict]] = {
    "transactions": (
        "search_transactions",
        {"review_type": "attention", "include_pending": False, "limit": 25},
    ),
    "receipts": ("get_receipts", {"view": "needs_review", "limit": 10}),
    "integrations": ("get_integration_status", {}),
    "replenishment": (
        "get_household_replenishment",
        {"view": "due", "horizon_days": 7, "limit": 10},
    ),
    "deals": (
        "get_relevant_deals",
        {"expiring_within_days": 7, "need_related_only": True, "limit": 8},
    ),
    "errands": (
        "get_errands_and_plan",
        {"status": "active", "include_latest_plan": True, "limit": 20},
    ),
}


class ProactiveAttentionDisabledError(RuntimeError):
    pass


class ProactiveAttentionService:
    def __init__(
        self,
        db: Session,
        settings: Settings | None = None,
        telegram: TelegramService | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.telegram = telegram or TelegramService(self.settings)

    def preferences(self) -> ProactiveAttentionPreference:
        workspace_id, user_id = self._scope()
        value = self.db.scalar(
            select(ProactiveAttentionPreference).where(
                ProactiveAttentionPreference.workspace_id == workspace_id,
                ProactiveAttentionPreference.user_id == user_id,
            )
        )
        if value is None:
            value = ProactiveAttentionPreference(
                workspace_id=workspace_id,
                user_id=user_id,
                categories_json=list(DEFAULT_CATEGORIES),
            )
            self.db.add(value)
            try:
                self.db.commit()
            except IntegrityError:
                self.db.rollback()
                value = self.db.scalar(
                    select(ProactiveAttentionPreference).where(
                        ProactiveAttentionPreference.workspace_id == workspace_id,
                        ProactiveAttentionPreference.user_id == user_id,
                    )
                )
                if value is None:
                    raise
            else:
                self.db.refresh(value)
        return value

    def update_preferences(self, patch: AttentionPreferencePatch) -> ProactiveAttentionPreference:
        value = self.preferences()
        values = patch.model_dump(exclude_none=True)
        if "timezone" in values:
            _zone(values["timezone"])
        if "categories" in values:
            values["categories_json"] = values.pop("categories")
        for name, field_value in values.items():
            setattr(value, name, field_value)
        value.updated_at = utc_now()
        self.db.commit()
        self.db.refresh(value)
        return value

    def build_center(
        self, *, now: datetime | None = None
    ) -> tuple[AgentStructuredResponse | None, ProactiveAttentionPreference]:
        self._require_feature()
        preferences = self.preferences()
        if not preferences.enabled or not preferences.in_app_enabled:
            return None, preferences
        response = self._build_response(preferences, now=now)
        return response, preferences

    def deliver_telegram_digest(self, *, now: datetime | None = None) -> dict[str, int | str]:
        self._require_feature()
        current = _aware(now or utc_now())
        preferences = self.preferences()
        if not preferences.enabled:
            return _delivery("skipped_disabled")
        if not preferences.telegram_enabled:
            return _delivery("skipped_channel_disabled")
        if preferences.delivery_mode != "digest":
            return _delivery("skipped_delivery_mode")
        workspace_id, user_id = self._scope()
        identities = list(
            self.db.scalars(
                select(TelegramIdentity)
                .where(
                    TelegramIdentity.workspace_id == workspace_id,
                    TelegramIdentity.user_id == user_id,
                    TelegramIdentity.enabled.is_(True),
                )
                .limit(2)
            )
        )
        if len(identities) != 1:
            return _delivery("skipped_channel_unavailable")
        if self._quiet(preferences, current):
            return _delivery("skipped_quiet_hours")

        response = self._build_response(preferences, now=current)
        block = next(
            (item for item in response.blocks if isinstance(item, AgentAttentionSummaryBlock)),
            None,
        )
        if block is None or not block.items:
            return _delivery("skipped_empty")
        fingerprint = hashlib.sha256(
            response.model_dump_json(exclude_none=True).encode("utf-8")
        ).hexdigest()
        local_date = current.astimezone(_zone(preferences.timezone)).date().isoformat()
        dedupe_key = hashlib.sha256(f"{fingerprint}:{local_date}".encode()).hexdigest()
        if self.db.scalar(
            select(ProactiveAttentionDelivery.id).where(
                ProactiveAttentionDelivery.workspace_id == workspace_id,
                ProactiveAttentionDelivery.user_id == user_id,
                ProactiveAttentionDelivery.channel == "telegram",
                ProactiveAttentionDelivery.dedupe_key == dedupe_key,
            )
        ):
            return _delivery("skipped_duplicate", len(block.items))
        day_start_local = datetime.combine(
            current.astimezone(_zone(preferences.timezone)).date(),
            day_time.min,
            _zone(preferences.timezone),
        )
        day_start = day_start_local.astimezone(UTC)
        sent_today = int(
            self.db.scalar(
                select(func.count(ProactiveAttentionDelivery.id)).where(
                    ProactiveAttentionDelivery.workspace_id == workspace_id,
                    ProactiveAttentionDelivery.user_id == user_id,
                    ProactiveAttentionDelivery.channel == "telegram",
                    ProactiveAttentionDelivery.status == "sent",
                    ProactiveAttentionDelivery.delivered_at >= day_start,
                )
            )
            or 0
        )
        if sent_today >= preferences.max_alerts_per_day:
            return _delivery("skipped_daily_cap", len(block.items))
        last_delivery = self.db.scalar(
            select(ProactiveAttentionDelivery.delivered_at)
            .where(
                ProactiveAttentionDelivery.workspace_id == workspace_id,
                ProactiveAttentionDelivery.user_id == user_id,
                ProactiveAttentionDelivery.channel == "telegram",
                ProactiveAttentionDelivery.status == "sent",
            )
            .order_by(ProactiveAttentionDelivery.delivered_at.desc())
            .limit(1)
        )
        if last_delivery and _aware(last_delivery) > current - timedelta(
            minutes=preferences.cooldown_minutes
        ):
            return _delivery("skipped_cooldown", len(block.items))
        delivery = ProactiveAttentionDelivery(
            workspace_id=workspace_id,
            user_id=user_id,
            channel="telegram",
            dedupe_key=dedupe_key,
            fingerprint=fingerprint,
            attention_count=len(block.items),
            status="pending",
        )
        self.db.add(delivery)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            return _delivery("skipped_duplicate", len(block.items))
        try:
            sent = self.telegram.send_message(
                _telegram_message(block),
                chat_id=identities[0].chat_id,
            )
        except Exception:
            delivery.status = "ambiguous"
            self.db.commit()
            raise
        if not sent:
            delivery.status = "failed"
            self.db.commit()
            return _delivery("failed", len(block.items))
        delivery.status = "sent"
        delivery.delivered_at = current
        self.db.commit()
        return _delivery("sent", len(block.items))

    def _build_response(
        self,
        preferences: ProactiveAttentionPreference,
        *,
        now: datetime | None,
    ) -> AgentStructuredResponse:
        current = _aware(now or utc_now())
        registry = build_read_tool_registry(self.settings)
        context = AgentToolContext.from_session(self.db, request_id="proactive-attention")
        evidence: list[ReadToolEvidence] = []
        categories = _validated_categories(preferences.categories_json)
        for sequence, category in enumerate(categories):
            tool_name, arguments = _CATEGORY_CALLS[category]
            started = time.monotonic()
            prepared = registry.prepare(tool_name, arguments, context=context)
            executed = registry.execute_read(prepared, context=context)
            evidence.append(
                ReadToolEvidence(
                    tool_name=tool_name,
                    tool_version=executed.tool_version,
                    sequence=sequence,
                    arguments=executed.normalized_arguments,
                    output=executed.output or {},
                    latency_ms=max(0, int((time.monotonic() - started) * 1_000)),
                )
            )
        return compose_proactive_attention_response(
            evidence,
            current_date=current.date(),
        )

    def _require_feature(self) -> None:
        if not self.settings.agent_proactive_enabled:
            raise ProactiveAttentionDisabledError("Proactive attention is disabled.")

    def _scope(self) -> tuple[int, int]:
        workspace_id = self.db.info.get("workspace_id")
        user_id = self.db.info.get("user_id")
        if not isinstance(workspace_id, int) or not isinstance(user_id, int):
            raise ValueError("Proactive attention requires authenticated tenant scope.")
        return workspace_id, user_id

    @staticmethod
    def _quiet(preferences: ProactiveAttentionPreference, now: datetime) -> bool:
        hour = now.astimezone(_zone(preferences.timezone)).hour
        start = preferences.quiet_start_hour
        end = preferences.quiet_end_hour
        if start == end:
            return False
        return start <= hour < end if start < end else hour >= start or hour < end


def preference_out(value: ProactiveAttentionPreference) -> dict:
    return {
        "enabled": value.enabled,
        "categories": list(value.categories_json),
        "in_app_enabled": value.in_app_enabled,
        "telegram_enabled": value.telegram_enabled,
        "delivery_mode": value.delivery_mode,
        "quiet_start_hour": value.quiet_start_hour,
        "quiet_end_hour": value.quiet_end_hour,
        "timezone": value.timezone,
        "max_alerts_per_day": value.max_alerts_per_day,
        "cooldown_minutes": value.cooldown_minutes,
    }


def _validated_categories(value: object) -> tuple[AttentionCategory, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(category, str) for category in value)
        or len(value) != len(set(value))
    ):
        raise ValueError("Stored attention categories are invalid.")
    if any(category not in _CATEGORY_CALLS for category in value):
        raise ValueError("Stored attention categories are invalid.")
    return cast(tuple[AttentionCategory, ...], tuple(value))


def _zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Unsupported timezone.") from exc


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _delivery(status: str, count: int = 0) -> dict[str, int | str]:
    return {"status": status, "attention_count": count}


def _telegram_message(block: AgentAttentionSummaryBlock) -> str:
    lines = ["<b>EXPENSEOPS ATTENTION DIGEST</b>"]
    for item in block.items:
        lines.append(f"• <b>{html.escape(item.title)}</b>")
        if item.detail:
            lines.append(html.escape(item.detail))
    return "\n".join(lines)

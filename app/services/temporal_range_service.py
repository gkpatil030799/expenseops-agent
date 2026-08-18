from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProactiveAttentionPreference

DEFAULT_RECENT_DAYS = 30
EXPENSEOPS_WEEK_START = 0  # Monday, matching Python's date.weekday().


class TemporalPreset(StrEnum):
    TODAY = "today"
    YESTERDAY = "yesterday"
    THIS_WEEK = "this_week"
    LAST_WEEK = "last_week"
    LAST_7_DAYS = "last_7_days"
    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"
    LAST_30_DAYS = "last_30_days"
    THIS_QUARTER = "this_quarter"
    LAST_QUARTER = "last_quarter"
    LAST_90_DAYS = "last_90_days"
    YEAR_TO_DATE = "year_to_date"
    THIS_YEAR = "this_year"
    LAST_YEAR = "last_year"
    RECENTLY = "recently"
    PAGE_CONTEXT = "page_context"
    EXPLICIT_RANGE = "explicit_range"
    PREVIOUS_PERIOD = "previous_period"


class InsightsDatePreset(StrEnum):
    LAST_7_DAYS = "7d"
    LAST_30_DAYS = "30d"
    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"
    LAST_90_DAYS = "90d"
    THIS_QUARTER = "this_quarter"
    LAST_QUARTER = "last_quarter"
    YEAR_TO_DATE = "ytd"


class InsightsGranularity(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


@dataclass(frozen=True, slots=True)
class ResolvedDateRange:
    preset: TemporalPreset
    start_date: date
    end_date: date
    timezone: str
    label: str

    def __post_init__(self) -> None:
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        if not self.timezone.strip():
            raise ValueError("timezone must not be empty")

    def tool_arguments(self) -> dict[str, str]:
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ResolvedInsightsDateRange:
    preset: InsightsDatePreset
    start_date: date
    end_date: date
    granularity: InsightsGranularity
    timezone: str


_INSIGHTS_PRESET_MAP: dict[
    InsightsDatePreset,
    tuple[TemporalPreset, InsightsGranularity],
] = {
    InsightsDatePreset.LAST_7_DAYS: (
        TemporalPreset.LAST_7_DAYS,
        InsightsGranularity.DAY,
    ),
    InsightsDatePreset.LAST_30_DAYS: (
        TemporalPreset.LAST_30_DAYS,
        InsightsGranularity.DAY,
    ),
    InsightsDatePreset.THIS_MONTH: (
        TemporalPreset.THIS_MONTH,
        InsightsGranularity.DAY,
    ),
    InsightsDatePreset.LAST_MONTH: (
        TemporalPreset.LAST_MONTH,
        InsightsGranularity.DAY,
    ),
    InsightsDatePreset.LAST_90_DAYS: (
        TemporalPreset.LAST_90_DAYS,
        InsightsGranularity.WEEK,
    ),
    InsightsDatePreset.THIS_QUARTER: (
        TemporalPreset.THIS_QUARTER,
        InsightsGranularity.WEEK,
    ),
    InsightsDatePreset.LAST_QUARTER: (
        TemporalPreset.LAST_QUARTER,
        InsightsGranularity.WEEK,
    ),
    InsightsDatePreset.YEAR_TO_DATE: (
        TemporalPreset.YEAR_TO_DATE,
        InsightsGranularity.MONTH,
    ),
}


def configured_zone(timezone_name: str | None) -> tuple[str, ZoneInfo]:
    clean = (timezone_name or "UTC").strip() or "UTC"
    if len(clean) > 64 or clean.startswith(("/", ".")) or "\x00" in clean:
        return "UTC", ZoneInfo("UTC")
    try:
        return clean, ZoneInfo(clean)
    except (ValueError, ZoneInfoNotFoundError):
        return "UTC", ZoneInfo("UTC")


def configured_user_timezone(
    db: Session,
    *,
    workspace_id: int,
    user_id: int,
) -> str:
    """Read one tenant/user preference without creating or mutating it."""

    value = db.scalar(
        select(ProactiveAttentionPreference.timezone).where(
            ProactiveAttentionPreference.workspace_id == workspace_id,
            ProactiveAttentionPreference.user_id == user_id,
        )
    )
    timezone, _zone = configured_zone(value if isinstance(value, str) else "UTC")
    return timezone


def resolve_temporal_range(
    preset: TemporalPreset,
    *,
    now: datetime,
    timezone_name: str | None,
    page_start_date: date | None = None,
    page_end_date: date | None = None,
) -> ResolvedDateRange:
    timezone, zone = configured_zone(timezone_name)
    current = (now if now.tzinfo is not None else now.replace(tzinfo=UTC)).astimezone(zone).date()
    month_start = current.replace(day=1)
    week_start = current - timedelta(days=(current.weekday() - EXPENSEOPS_WEEK_START) % 7)
    quarter_month = ((current.month - 1) // 3) * 3 + 1
    quarter_start = date(current.year, quarter_month, 1)

    if preset is TemporalPreset.TODAY:
        start = end = current
        label = "today"
    elif preset is TemporalPreset.YESTERDAY:
        start = end = current - timedelta(days=1)
        label = "yesterday"
    elif preset is TemporalPreset.THIS_WEEK:
        start, end, label = week_start, current, "this week"
    elif preset is TemporalPreset.LAST_WEEK:
        end = week_start - timedelta(days=1)
        start, label = end - timedelta(days=6), "last week"
    elif preset is TemporalPreset.LAST_7_DAYS:
        start, end, label = current - timedelta(days=6), current, "the last 7 days"
    elif preset is TemporalPreset.THIS_MONTH:
        start, end, label = month_start, current, "this month"
    elif preset is TemporalPreset.LAST_MONTH:
        end = month_start - timedelta(days=1)
        start, label = end.replace(day=1), "last month"
    elif preset in {TemporalPreset.LAST_30_DAYS, TemporalPreset.RECENTLY}:
        start = current - timedelta(days=DEFAULT_RECENT_DAYS - 1)
        end, label = current, f"the last {DEFAULT_RECENT_DAYS} days"
    elif preset is TemporalPreset.THIS_QUARTER:
        start, end, label = quarter_start, current, "this quarter"
    elif preset is TemporalPreset.LAST_QUARTER:
        end = quarter_start - timedelta(days=1)
        prior_quarter_month = ((end.month - 1) // 3) * 3 + 1
        start, label = date(end.year, prior_quarter_month, 1), "last quarter"
    elif preset is TemporalPreset.LAST_90_DAYS:
        start, end, label = current - timedelta(days=89), current, "the last 90 days"
    elif preset in {TemporalPreset.YEAR_TO_DATE, TemporalPreset.THIS_YEAR}:
        start, end = date(current.year, 1, 1), current
        label = "year to date" if preset is TemporalPreset.YEAR_TO_DATE else "this year"
    elif preset is TemporalPreset.LAST_YEAR:
        start, end, label = (
            date(current.year - 1, 1, 1),
            date(current.year - 1, 12, 31),
            "last year",
        )
    elif preset is TemporalPreset.PAGE_CONTEXT:
        if page_start_date is None or page_end_date is None or page_start_date > page_end_date:
            raise ValueError("page context requires one valid date range")
        start, end, label = page_start_date, page_end_date, "the selected period"
    else:  # pragma: no cover - the closed enum makes this defensive only.
        raise ValueError("unsupported temporal preset")
    return ResolvedDateRange(
        preset=preset,
        start_date=start,
        end_date=end,
        timezone=timezone,
        label=label,
    )


def resolve_insights_date_range(
    preset: InsightsDatePreset,
    *,
    now: datetime,
    timezone_name: str | None,
) -> ResolvedInsightsDateRange:
    temporal_preset, granularity = _INSIGHTS_PRESET_MAP[preset]
    resolved = resolve_temporal_range(
        temporal_preset,
        now=now,
        timezone_name=timezone_name,
    )
    return ResolvedInsightsDateRange(
        preset=preset,
        start_date=resolved.start_date,
        end_date=resolved.end_date,
        granularity=granularity,
        timezone=resolved.timezone,
    )

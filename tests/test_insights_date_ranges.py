from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.agent.query_planning import resolve_temporal_range
from app.api import insights_routes
from app.api.deps import get_current_user, get_current_workspace
from app.db import Base, get_db
from app.models import ProactiveAttentionPreference, User, Workspace
from app.services.temporal_range_service import (
    InsightsDatePreset,
    InsightsGranularity,
    TemporalPreset,
    resolve_insights_date_range,
)


@pytest.mark.parametrize(
    ("timezone_name", "now", "insights_preset", "agent_preset", "expected"),
    [
        (
            "America/Phoenix",
            datetime(2026, 8, 18, 1, 30, tzinfo=UTC),
            InsightsDatePreset.LAST_30_DAYS,
            TemporalPreset.LAST_30_DAYS,
            (date(2026, 7, 19), date(2026, 8, 17)),
        ),
        (
            "America/New_York",
            datetime(2026, 3, 8, 4, 30, tzinfo=UTC),
            InsightsDatePreset.THIS_MONTH,
            TemporalPreset.THIS_MONTH,
            (date(2026, 3, 1), date(2026, 3, 7)),
        ),
        (
            "America/New_York",
            datetime(2026, 3, 8, 5, 30, tzinfo=UTC),
            InsightsDatePreset.THIS_MONTH,
            TemporalPreset.THIS_MONTH,
            (date(2026, 3, 1), date(2026, 3, 8)),
        ),
    ],
)
def test_insights_and_agent_share_timezone_boundary_resolution(
    timezone_name: str,
    now: datetime,
    insights_preset: InsightsDatePreset,
    agent_preset: TemporalPreset,
    expected: tuple[date, date],
) -> None:
    insights = resolve_insights_date_range(
        insights_preset,
        now=now,
        timezone_name=timezone_name,
    )
    agent = resolve_temporal_range(
        agent_preset,
        now=now,
        timezone_name=timezone_name,
    )

    assert (insights.start_date, insights.end_date) == expected
    assert (insights.start_date, insights.end_date) == (
        agent.start_date,
        agent.end_date,
    )
    assert insights.timezone == agent.timezone == timezone_name


@pytest.mark.parametrize(
    ("preset", "granularity"),
    [
        (InsightsDatePreset.LAST_7_DAYS, InsightsGranularity.DAY),
        (InsightsDatePreset.LAST_30_DAYS, InsightsGranularity.DAY),
        (InsightsDatePreset.THIS_MONTH, InsightsGranularity.DAY),
        (InsightsDatePreset.LAST_MONTH, InsightsGranularity.DAY),
        (InsightsDatePreset.LAST_90_DAYS, InsightsGranularity.WEEK),
        (InsightsDatePreset.THIS_QUARTER, InsightsGranularity.WEEK),
        (InsightsDatePreset.LAST_QUARTER, InsightsGranularity.WEEK),
        (InsightsDatePreset.YEAR_TO_DATE, InsightsGranularity.MONTH),
    ],
)
def test_insights_product_preset_granularity_is_backend_owned(
    preset: InsightsDatePreset,
    granularity: InsightsGranularity,
) -> None:
    resolved = resolve_insights_date_range(
        preset,
        now=datetime(2026, 8, 18, 1, 30, tzinfo=UTC),
        timezone_name="America/Phoenix",
    )

    assert resolved.granularity is granularity


def test_authenticated_date_range_endpoint_reads_exact_user_preference_without_writes(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'insights-date-range.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    user = User(email="viewer@example.test", display_name="Viewer")
    db.add(user)
    db.flush()
    workspace = Workspace(name="Viewer workspace", created_by_user_id=user.id)
    db.add(workspace)
    db.flush()
    db.add(
        ProactiveAttentionPreference(
            workspace_id=workspace.id,
            user_id=user.id,
            timezone="America/Phoenix",
        )
    )
    db.commit()

    application = FastAPI()
    application.include_router(insights_routes.router)
    application.dependency_overrides[get_db] = lambda: db
    client = TestClient(application)

    assert client.get("/api/insights/date-range?preset=30d").status_code == 401

    application.dependency_overrides[get_current_user] = lambda: user
    application.dependency_overrides[get_current_workspace] = lambda: workspace
    monkeypatch.setattr(
        insights_routes,
        "utc_now",
        lambda: datetime(2026, 8, 18, 1, 30, tzinfo=UTC),
    )
    before = int(db.scalar(select(func.count(ProactiveAttentionPreference.id))) or 0)

    response = client.get("/api/insights/date-range?preset=30d")

    assert response.status_code == 200
    assert response.json() == {
        "preset": "30d",
        "start_date": "2026-07-19",
        "end_date": "2026-08-17",
        "granularity": "day",
        "timezone": "America/Phoenix",
    }
    assert int(db.scalar(select(func.count(ProactiveAttentionPreference.id))) or 0) == before
    assert client.get("/api/insights/date-range?preset=custom").status_code == 422

    db.close()
    engine.dispose()


def test_date_range_endpoint_falls_back_to_utc_without_creating_preferences(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'insights-date-range-utc.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    user = User(email="utc@example.test", display_name="UTC Viewer")
    db.add(user)
    db.flush()
    workspace = Workspace(name="UTC workspace", created_by_user_id=user.id)
    db.add(workspace)
    db.commit()

    application = FastAPI()
    application.include_router(insights_routes.router)
    application.dependency_overrides[get_db] = lambda: db
    application.dependency_overrides[get_current_user] = lambda: user
    application.dependency_overrides[get_current_workspace] = lambda: workspace
    monkeypatch.setattr(
        insights_routes,
        "utc_now",
        lambda: datetime(2026, 8, 18, 1, 30, tzinfo=UTC),
    )

    response = TestClient(application).get("/api/insights/date-range?preset=7d")

    assert response.status_code == 200
    assert response.json() == {
        "preset": "7d",
        "start_date": "2026-08-12",
        "end_date": "2026-08-18",
        "granularity": "day",
        "timezone": "UTC",
    }
    assert int(db.scalar(select(func.count(ProactiveAttentionPreference.id))) or 0) == 0

    db.close()
    engine.dispose()

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import RateLimitEvent
from app.services import outbox_service  # noqa: F401 - loads model graph


def test_shared_rate_limiter_persists_fixed_window_counts(tmp_path, monkeypatch):
    import app.rate_limit as rate_limit

    engine = create_engine(f"sqlite:///{tmp_path / 'rate-limit.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(rate_limit, "SessionLocal", factory)
    limiter = rate_limit.PostgresRateLimiter()

    limiter.check("auth-login:198.51.100.7", limit=2, window_seconds=60)
    limiter.check("auth-login:198.51.100.7", limit=2, window_seconds=60)

    try:
        limiter.check("auth-login:198.51.100.7", limit=2, window_seconds=60)
    except HTTPException as exc:
        assert exc.status_code == 429
    else:
        raise AssertionError("The shared limit must reject requests beyond the window budget")

    with factory() as db:
        event = db.scalar(select(RateLimitEvent))
        assert event is not None
        assert event.request_count == 2

    limiter.clear()
    with factory() as db:
        assert db.scalar(select(RateLimitEvent)) is None
    engine.dispose()

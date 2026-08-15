from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import SessionLocal
from app.models import RateLimitEvent, utc_now


class InMemoryRateLimiter:
    def __init__(self):
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            while events and events[0] <= now - window_seconds:
                events.popleft()
            if len(events) >= limit:
                raise HTTPException(status_code=429, detail="Too many requests")
            events.append(now)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


class PostgresRateLimiter:
    """Shared fixed-window limiter used by every web replica."""

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = utc_now()
        window_started_at = now.replace(microsecond=0) - timedelta(
            seconds=int(now.timestamp()) % window_seconds
        )
        with SessionLocal() as db:
            try:
                event = db.scalar(
                    select(RateLimitEvent)
                    .where(
                        RateLimitEvent.key == key,
                        RateLimitEvent.window_started_at == window_started_at,
                    )
                    .with_for_update()
                )
                if event is None:
                    event = RateLimitEvent(
                        key=key,
                        window_started_at=window_started_at,
                        window_seconds=window_seconds,
                        request_count=1,
                    )
                    db.add(event)
                elif event.request_count >= limit:
                    raise HTTPException(status_code=429, detail="Too many requests")
                else:
                    event.request_count += 1
                if int(now.timestamp()) % 100 == 0:
                    db.execute(
                        delete(RateLimitEvent)
                        .where(
                            RateLimitEvent.window_started_at
                            < now - timedelta(seconds=max(window_seconds, 3600))
                        )
                        .execution_options(synchronize_session=False)
                    )
                db.commit()
            except HTTPException:
                db.rollback()
                raise
            except IntegrityError:
                db.rollback()
                count = (
                    db.scalar(
                        select(func.sum(RateLimitEvent.request_count)).where(
                            RateLimitEvent.key == key,
                            RateLimitEvent.window_started_at == window_started_at,
                        )
                    )
                    or 0
                )
                if count >= limit:
                    raise HTTPException(status_code=429, detail="Too many requests") from None
                # A concurrent creator won. Retry against the now-existing row.
                self.check(key, limit=limit, window_seconds=window_seconds)

    def clear(self) -> None:
        with SessionLocal() as db:
            db.execute(delete(RateLimitEvent))
            db.commit()


def build_rate_limiter():
    if get_settings().rate_limit_backend == "postgres":
        return PostgresRateLimiter()
    return InMemoryRateLimiter()


rate_limiter = build_rate_limiter()

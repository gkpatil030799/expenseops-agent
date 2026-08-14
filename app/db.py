from __future__ import annotations

import logging
from collections.abc import Generator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings
from app.logging_config import log_event

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


settings = get_settings()
connect_args = (
    {"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {
        "options": (
            f"-c statement_timeout={settings.database_statement_timeout_ms} "
            f"-c lock_timeout={settings.database_lock_timeout_ms}"
        )
    }
)
engine_kwargs: dict = {
    "connect_args": connect_args,
    "future": True,
    "pool_pre_ping": True,
}
if settings.database_url.startswith("postgresql"):
    engine_kwargs.update(
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_recycle=settings.database_pool_recycle_seconds,
    )
engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, class_=Session)


def get_db(request: Request) -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        user_id = getattr(request.state, "user_id", None)
        workspace_id = getattr(request.state, "workspace_id", None)
        if user_id is not None and workspace_id is not None:
            from app.tenancy import TenantContext, set_session_tenant

            set_session_tenant(db, TenantContext(user_id=user_id, workspace_id=workspace_id))
        yield db
    finally:
        db.close()


def init_db() -> None:
    if settings.environment == "production":
        log_event(logger, "db_initialized", mode="migration_managed")
        return

    # Import models before creating tables.
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    from app.tenancy import ensure_default_tenancy

    with SessionLocal() as db:
        ensure_default_tenancy(db)
    log_event(logger, "db_initialized", mode="create_all")

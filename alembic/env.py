from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context
from app import models  # noqa: F401
from app.config import get_settings
from app.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata
_MIGRATION_ADVISORY_LOCK_KEY = 0x4558504F5053  # "EXPOPS"


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=settings.database_url.startswith("sqlite"),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=settings.database_url.startswith("sqlite"),
        )

        with context.begin_transaction():
            if connection.dialect.name == "postgresql":
                # One migration transaction per database. A second release
                # fails immediately instead of racing DDL/data repair or
                # waiting indefinitely behind a lost deployment.
                connection.execute(
                    text("SELECT set_config('lock_timeout', :value, true)"),
                    {"value": f"{settings.database_lock_timeout_ms}ms"},
                )
                connection.execute(
                    text("SELECT set_config('statement_timeout', :value, true)"),
                    {"value": f"{settings.database_statement_timeout_ms}ms"},
                )
                acquired = connection.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:key)"),
                    {"key": _MIGRATION_ADVISORY_LOCK_KEY},
                )
                if not acquired:
                    raise RuntimeError("Another ExpenseOps database migration is already running")
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

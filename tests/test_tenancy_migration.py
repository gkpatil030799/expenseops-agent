from __future__ import annotations

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.config import get_settings


def _upgrade(monkeypatch, database_url: str, revision: str) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), revision)
    get_settings.cache_clear()


def test_clean_database_migrates_to_multitenant_head(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'clean.db'}"
    _upgrade(monkeypatch, database_url, "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert {
        "users",
        "workspaces",
        "workspace_memberships",
        "auth_identities",
        "auth_sessions",
        "workspace_invitations",
        "oauth_states",
        "telegram_link_codes",
        "audit_events",
    }.issubset(inspector.get_table_names())
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM users")) == 1
        assert connection.scalar(text("SELECT COUNT(*) FROM workspaces")) == 1
        assert connection.scalar(text("SELECT COUNT(*) FROM workspace_memberships")) == 1


def test_existing_single_user_rows_are_backfilled_without_data_loss(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'existing.db'}"
    _upgrade(monkeypatch, database_url, "20260810_0011")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO household_items "
                "(name, replenishment_mode, cadence_days, enabled, created_at, updated_at) "
                "VALUES ('Legacy detergent', 'either', 30, true, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            )
        )

    _upgrade(monkeypatch, database_url, "head")
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT h.name, h.workspace_id, w.name, u.email "
                "FROM household_items h "
                "JOIN workspaces w ON w.id = h.workspace_id "
                "JOIN users u ON u.id = w.created_by_user_id"
            )
        ).one()
    assert row == (
        "Legacy detergent",
        1,
        "Personal workspace",
        "local@expenseops.invalid",
    )

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


def test_tenant_scoped_unique_keys_allow_same_values_in_two_workspaces(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'tenant-collisions.db'}"
    _upgrade(monkeypatch, database_url, "head")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, display_name, status, created_at, updated_at) "
                "VALUES (2, 'second@example.test', 'Second', 'active', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO workspaces "
                "(id, name, workspace_type, created_by_user_id, created_at, updated_at) "
                "VALUES (2, 'Second workspace', 'personal', 2, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        for workspace_id in (1, 2):
            connection.execute(
                text(
                    "INSERT INTO preferred_places "
                    "(workspace_id, preference_key, canonical_name, full_address, "
                    "created_at, updated_at) "
                    "VALUES (:workspace_id, 'aldi', 'Aldi', '123 Main St', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"workspace_id": workspace_id},
            )
            connection.execute(
                text(
                    "INSERT INTO replenishment_job_runs "
                    "(workspace_id, run_key, trigger, status, started_at, dataset_size, "
                    "metrics_json, created_at) "
                    "VALUES (:workspace_id, 'weekly-2026-08-12', 'schedule', 'complete', "
                    "CURRENT_TIMESTAMP, 0, '{}', CURRENT_TIMESTAMP)"
                ),
                {"workspace_id": workspace_id},
            )
            connection.execute(
                text(
                    "INSERT INTO replenishment_model_versions "
                    "(workspace_id, version, algorithm, status, trained_at, training_rows, "
                    "metrics_json, created_at) "
                    "VALUES (:workspace_id, 'v1', 'baseline', 'active', "
                    "CURRENT_TIMESTAMP, 30, '{}', CURRENT_TIMESTAMP)"
                ),
                {"workspace_id": workspace_id},
            )

    inspector = inspect(engine)
    indexes = {
        (table, index["name"]): index
        for table in (
            "preferred_places",
            "replenishment_job_runs",
            "replenishment_model_versions",
        )
        for index in inspector.get_indexes(table)
    }
    assert indexes[("preferred_places", "ix_preferred_places_preference_key")]["unique"] == 0
    assert indexes[("replenishment_job_runs", "ix_replenishment_job_runs_run_key")]["unique"] == 0
    assert (
        indexes[("replenishment_model_versions", "uq_replenishment_workspace_single_active_model")][
            "unique"
        ]
        == 1
    )


def test_alembic_head_matches_sqlalchemy_metadata(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'schema-check.db'}"
    _upgrade(monkeypatch, database_url, "head")
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    command.check(Config("alembic.ini"))
    get_settings.cache_clear()

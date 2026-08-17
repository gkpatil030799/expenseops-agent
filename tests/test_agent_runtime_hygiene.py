from __future__ import annotations

import tomllib
from importlib.metadata import requires
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.config import get_settings

ROOT = Path(__file__).resolve().parents[1]
SDK_VERSION = "0.20.0"
GREENLET_VERSION = "3.5.4"


def _upgrade(monkeypatch, database_url: str, revision: str) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    command.upgrade(Config(ROOT / "alembic.ini"), revision)
    get_settings.cache_clear()


def _locked_requirements() -> dict[str, Requirement]:
    requirements = {}
    for raw_line in (ROOT / "requirements.lock").read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        requirements[canonicalize_name(requirement.name)] = requirement
    return requirements


def _insert_run(connection, *, public_id: str, trigger_message_id: int | None) -> None:
    connection.execute(
        text(
            "INSERT INTO agent_runs "
            "(workspace_id, public_id, conversation_id, owner_user_id, trigger_message_id, "
            "status, metadata_json, created_at, updated_at) "
            "VALUES (1, :public_id, 1, 1, :trigger_message_id, 'queued', '{}', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {
            "public_id": public_id,
            "trigger_message_id": trigger_message_id,
        },
    )


def test_runtime_lock_exactly_pins_the_declared_openai_agents_sdk() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    declared = [Requirement(value) for value in project["project"]["dependencies"]]
    declared_by_name = {
        canonicalize_name(requirement.name): requirement for requirement in declared
    }
    locked = _locked_requirements()

    sdk_requirement = declared_by_name["openai-agents"]
    assert str(sdk_requirement.specifier) == f"=={SDK_VERSION}"
    assert str(locked["openai-agents"].specifier) == f"=={SDK_VERSION}"
    assert set(declared_by_name).issubset(locked)
    assert all(str(requirement.specifier).startswith("==") for requirement in locked.values())


def test_runtime_lock_pins_sqlalchemy_dependency_for_linux_release_target() -> None:
    release_environment = default_environment()
    release_environment.update(
        {
            "extra": "",
            "platform_machine": "x86_64",
            "platform_system": "Linux",
            "python_full_version": "3.11.13",
            "python_version": "3.11",
            "sys_platform": "linux",
        }
    )
    sqlalchemy_requirements = [Requirement(value) for value in (requires("SQLAlchemy") or ())]
    greenlet_requirement = next(
        requirement
        for requirement in sqlalchemy_requirements
        if canonicalize_name(requirement.name) == "greenlet"
        and (requirement.marker is None or requirement.marker.evaluate(release_environment))
    )

    locked_greenlet = _locked_requirements()["greenlet"]
    assert str(locked_greenlet.specifier) == f"=={GREENLET_VERSION}"
    assert Version(GREENLET_VERSION) in greenlet_requirement.specifier


def test_read_only_runtime_migration_repairs_and_prevents_duplicate_trigger_runs(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'agent-runtime-index.db'}"
    _upgrade(monkeypatch, database_url, "20260815_0026")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO agent_conversations "
                "(id, workspace_id, public_id, owner_user_id, status, metadata_json, "
                "created_at, updated_at) "
                "VALUES (1, 1, 'runtime-conversation', 1, 'active', '{}', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO agent_messages "
                "(id, workspace_id, public_id, conversation_id, owner_user_id, role, status, "
                "content, metadata_json, created_at, updated_at) "
                "VALUES (1, 1, 'runtime-trigger', 1, 1, 'user', 'completed', "
                "'Show my spending', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        _insert_run(connection, public_id="original-trigger-run", trigger_message_id=1)
        _insert_run(connection, public_id="duplicate-trigger-run", trigger_message_id=1)
        _insert_run(connection, public_id="untriggered-run-one", trigger_message_id=None)
        _insert_run(connection, public_id="untriggered-run-two", trigger_message_id=None)

    _upgrade(monkeypatch, database_url, "head")

    heads = ScriptDirectory.from_config(Config(ROOT / "alembic.ini")).get_heads()
    assert len(heads) == 1
    assert heads[0] in {
        "20260815_0028",
        "20260815_0029",
        "20260817_0030",
        "20260817_0031",
    }
    index = next(
        value
        for value in inspect(engine).get_indexes("agent_runs")
        if value["name"] == "uq_agent_runs_workspace_owner_trigger"
    )
    assert index["unique"] == 1
    assert index["column_names"] == ["workspace_id", "owner_user_id", "trigger_message_id"]
    assert "trigger_message_id IS NOT NULL" in str(index["dialect_options"]["sqlite_where"])

    with engine.connect() as connection:
        repaired = connection.execute(
            text("SELECT public_id, trigger_message_id FROM agent_runs ORDER BY id")
        ).all()
    assert repaired == [
        ("original-trigger-run", 1),
        ("duplicate-trigger-run", None),
        ("untriggered-run-one", None),
        ("untriggered-run-two", None),
    ]

    with pytest.raises(IntegrityError), engine.begin() as connection:
        _insert_run(connection, public_id="blocked-trigger-run", trigger_message_id=1)

    with engine.begin() as connection:
        _insert_run(connection, public_id="allowed-untriggered-run", trigger_message_id=None)

    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT COUNT(*) FROM agent_runs WHERE trigger_message_id IS NULL")
            )
            == 4
        )


def test_read_only_runtime_migration_repairs_under_postgres_rls(monkeypatch) -> None:
    migration = (
        ScriptDirectory.from_config(Config(ROOT / "alembic.ini"))
        .get_revision("20260815_0027")
        .module
    )
    operations: list[tuple[str, str]] = []

    class FakeOp:
        @staticmethod
        def get_bind():
            return type("Bind", (), {"dialect": type("Dialect", (), {"name": "postgresql"})()})()

        @staticmethod
        def execute(statement):
            operations.append(("execute", str(statement)))

        @staticmethod
        def create_index(name, *_args, **_kwargs):
            operations.append(("create_index", name))

    monkeypatch.setattr(migration, "op", FakeOp())

    migration.upgrade()

    assert [operation for operation, _value in operations] == [
        "execute",
        "execute",
        "execute",
        "execute",
        "create_index",
    ]
    assert "set_config('expenseops.bypass_rls', 'on', true)" in operations[0][1]
    assert "LOCK TABLE agent_runs IN SHARE ROW EXCLUSIVE MODE" in operations[1][1]
    assert "UPDATE agent_runs AS later_run" in operations[2][1]
    assert "set_config('expenseops.bypass_rls', 'off', true)" in operations[3][1]

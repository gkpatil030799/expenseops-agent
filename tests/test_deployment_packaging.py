from pathlib import Path


def test_dockerfile_packages_sandbox_for_app_import():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "app ./app" in dockerfile
    assert "sandbox ./sandbox" in dockerfile
    assert "USER expenseops" in dockerfile


def test_dockerfile_builds_frontend_and_packages_migrations():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "npm run build" in dockerfile
    assert "/frontend/dist ./app/static" in dockerfile
    assert "alembic ./alembic" in dockerfile
    assert "alembic.ini ./" in dockerfile
    # Migrations run once as a deployment pre-step, not concurrently in every app replica.
    assert "alembic upgrade head" not in dockerfile


def test_release_gate_verifies_migrations_before_building_the_image():
    workflow = Path(".github/workflows/release-gate.yml").read_text(encoding="utf-8")

    assert "alembic upgrade head" in workflow
    assert "alembic check" in workflow
    assert "docker/build-push-action" in workflow

from pathlib import Path


def test_dockerfile_packages_sandbox_for_app_import():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "COPY app ./app" in dockerfile
    assert "COPY sandbox ./sandbox" in dockerfile


def test_dockerfile_builds_frontend_and_packages_migrations():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "npm run build" in dockerfile
    assert "COPY --from=frontend-build /frontend/dist ./app/static" in dockerfile
    assert "COPY alembic ./alembic" in dockerfile
    assert "COPY alembic.ini ./" in dockerfile
    assert "alembic upgrade head" in dockerfile

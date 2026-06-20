from pathlib import Path


def test_dockerfile_packages_sandbox_for_app_import():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "COPY app ./app" in dockerfile
    assert "COPY sandbox ./sandbox" in dockerfile

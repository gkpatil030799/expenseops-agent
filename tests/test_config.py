from app.config import Settings


def test_frontend_origin_parses_csv():
    settings = Settings(frontend_origin="https://a.example,https://b.example")

    assert settings.frontend_origin == ["https://a.example", "https://b.example"]


def test_docs_disabled_by_default_in_production():
    settings = Settings(environment="production", enable_docs=False)

    assert settings.docs_enabled is False


def test_docs_can_be_enabled_in_production():
    settings = Settings(environment="production", enable_docs=True)

    assert settings.docs_enabled is True

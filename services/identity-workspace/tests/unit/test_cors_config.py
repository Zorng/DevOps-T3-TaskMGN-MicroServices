from pydantic import ValidationError
import pytest

from identity_workspace.config import ConfigurationError, Settings, load_settings

URL = "postgresql+psycopg://fixture_user:synthetic_password@localhost/fixture_db"


def test_origins_are_closed_by_default():
    assert Settings(_env_file=None, database_url=URL).allowed_origins == ()


def test_explicit_local_and_https_origins_are_supported_and_deduplicated():
    settings = Settings(_env_file=None, database_url=URL, allowed_origins=("http://localhost:5173", "https://frontend.example", "http://localhost:5173"))
    assert settings.allowed_origins == ("http://localhost:5173", "https://frontend.example")


@pytest.mark.parametrize("origin", ["*", "null", "https://*.example", "http://frontend.example", "https://user:synthetic-secret@example.test", "https://example.test/path", "https://example.test/", "https://example.test?q=synthetic-secret", "https://example.test#fragment", "http://localhost:0", "http://localhost:invalid", "https://EXAMPLE.test"])
def test_invalid_origins_are_rejected_without_input_in_error(origin):
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, database_url=URL, allowed_origins=(origin,))
    assert "synthetic-secret" not in str(caught.value)


def test_production_origins_require_https():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production", database_url=URL, allowed_origins=("http://localhost:5173",))
    assert Settings(_env_file=None, environment="production", database_url=URL, allowed_origins=("https://frontend.example",)).allowed_origins


def test_origin_list_loads_from_json_environment_variable(monkeypatch):
    monkeypatch.setenv("IDENTITY_DATABASE_URL", URL)
    monkeypatch.setenv("IDENTITY_ALLOWED_ORIGINS", '["http://localhost:5173"]')
    assert load_settings(env_file=None).allowed_origins == ("http://localhost:5173",)


def test_invalid_origin_environment_error_is_safe(monkeypatch):
    monkeypatch.setenv("IDENTITY_DATABASE_URL", URL)
    monkeypatch.setenv("IDENTITY_ALLOWED_ORIGINS", 'synthetic-secret-invalid-json')
    with pytest.raises(ConfigurationError) as caught:
        load_settings(env_file=None)
    assert "synthetic-secret" not in str(caught.value)

import traceback

import pytest
from pydantic import ValidationError

from identity_workspace.config import ConfigurationError, Settings, load_settings


def test_missing_database_url_has_safe_actionable_error():
    with pytest.raises(ConfigurationError, match="IDENTITY_DATABASE_URL") as caught:
        load_settings(env_file=None)
    assert caught.value.__context__ is None


def test_environment_overrides_dotenv(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "IDENTITY_DATABASE_URL=postgresql+psycopg://fixture_user:synthetic_password@localhost/fixture_db\n"
        "IDENTITY_DB_POOL_SIZE=3\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("IDENTITY_DB_POOL_SIZE", "7")
    settings = load_settings(env_file=env_file)
    assert settings.db_pool_size == 7
    assert settings.database_url.get_secret_value().endswith("/fixture_db")


@pytest.mark.parametrize(
    "url",
    [
        "not-a-url-with-synthetic_password",
        "sqlite:///synthetic_password.db",
        "postgresql://fixture_user:synthetic_password@localhost/fixture_db",
        "postgresql+psycopg://fixture_user:synthetic_password@localhost:invalid/fixture_db",
        "postgresql+psycopg://fixture_user:synthetic_password@localhost:65536/fixture_db",
        "postgresql+psycopg://fixture_user@localhost/fixture_db",
        "postgresql+psycopg://fixture_user:synthetic_password@/fixture_db",
        "postgresql+psycopg://fixture_user:synthetic_password@localhost/",
        "postgresql+psycopg://fixture_user:synthetic_password@localhost/fixture_db?connect_timeout=0",
        "postgresql+psycopg://fixture_user:synthetic_password@localhost/fixture_db?options=-c%20statement_timeout=0",
        "postgresql+psycopg://fixture_user:synthetic_password@localhost/fixture_db?sslmode=require&sslmode=disable",
        "postgresql+psycopg://fixture_user:synthetic_password@localhost/fixture_db?sslmode=invalid",
        "postgresql+psycopg://fixture_user:synthetic_password@localhost/fixture_db\n",
    ],
)
def test_invalid_database_url_is_rejected_without_credential_leak(monkeypatch, url):
    monkeypatch.setenv("IDENTITY_DATABASE_URL", url)
    with pytest.raises(ConfigurationError) as caught:
        load_settings(env_file=None)
    rendered = "".join(traceback.format_exception(caught.value))
    assert "synthetic_password" not in rendered
    assert url not in str(caught.value)
    assert "IDENTITY_DATABASE_URL" in str(caught.value)
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("IDENTITY_DB_CONNECT_TIMEOUT_SECONDS", "0"),
        ("IDENTITY_DB_CONNECT_TIMEOUT_SECONDS", "31"),
        ("IDENTITY_DB_POOL_TIMEOUT_SECONDS", "0"),
        ("IDENTITY_DB_POOL_TIMEOUT_SECONDS", "nan"),
        ("IDENTITY_DB_STATEMENT_TIMEOUT_MS", "0"),
        ("IDENTITY_DB_POOL_SIZE", "0"),
        ("IDENTITY_DB_MAX_OVERFLOW", "-1"),
        ("IDENTITY_ENVIRONMENT", "unknown"),
        ("IDENTITY_LOG_LEVEL", "unknown"),
        ("IDENTITY_DB_POOL_SIZE", "synthetic-secret-in-invalid-setting"),
    ],
)
def test_invalid_setting_values_are_rejected_and_omitted(monkeypatch, name, value):
    monkeypatch.setenv("IDENTITY_DATABASE_URL", "postgresql+psycopg://fixture_user:synthetic_password@localhost/fixture_db")
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError) as caught:
        load_settings(env_file=None)
    assert name in str(caught.value)
    assert "synthetic-secret-in-invalid-setting" not in "".join(traceback.format_exception(caught.value))


def test_database_secret_is_hidden_in_representations_and_serialization(settings):
    for rendered in (str(settings), repr(settings), str(settings.model_dump()), settings.model_dump_json()):
        assert "synthetic_password" not in rendered
        assert "example_user" not in rendered


def test_direct_validation_error_hides_inputs():
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, database_url="synthetic-secret-invalid-url")
    assert "synthetic-secret-invalid-url" not in str(caught.value)


def test_settings_are_immutable(settings):
    with pytest.raises(ValidationError):
        settings.db_pool_size = 10


def test_tls_options_and_encoded_password_are_supported():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://fixture_user:synthetic%40password@localhost/fixture_db?sslmode=require",
    )
    assert settings.database_url.get_secret_value().endswith("sslmode=require")


def test_unknown_dotenv_fields_fail_without_exposing_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("IDENTITY_UNKNOWN=synthetic-secret-unknown\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as caught:
        load_settings(env_file=env_file)
    assert "synthetic-secret-unknown" not in "".join(traceback.format_exception(caught.value))


def test_unreadable_dotenv_has_safe_error(monkeypatch):
    def fail_loading(*args, **kwargs):
        raise OSError("synthetic-secret-from-file-error")

    monkeypatch.setattr("identity_workspace.config.Settings", fail_loading)
    with pytest.raises(ConfigurationError) as caught:
        load_settings(env_file=".env")
    assert "synthetic-secret-from-file-error" not in "".join(traceback.format_exception(caught.value))

import pytest
from pydantic import ValidationError

from identity_workspace.config import ConfigurationError, Settings, load_settings

URL = "postgresql+psycopg://fixture_user:synthetic_password@localhost/fixture_db"


def test_google_client_id_is_optional_until_separately_provisioned():
    assert Settings(_env_file=None, database_url=URL, google_client_id="").google_client_id is None
    assert Settings(_env_file=None, database_url=URL, google_client_id="synthetic-client.apps.googleusercontent.com").google_client_id


@pytest.mark.parametrize("client", ["synthetic-sensitive-client-secret", "https://example.com", " x.apps.googleusercontent.com", "x.apps.googleusercontent.com/path"])
def test_invalid_google_configuration_is_rejected_without_values(monkeypatch, client):
    monkeypatch.setenv("IDENTITY_DATABASE_URL", URL)
    monkeypatch.setenv("IDENTITY_GOOGLE_CLIENT_ID", client)
    with pytest.raises(ConfigurationError) as error:
        load_settings(env_file=None)
    assert "IDENTITY_GOOGLE_CLIENT_ID" in str(error.value)
    assert client not in str(error.value)


@pytest.mark.parametrize("override", [{"google_timeout_seconds": 0}, {"google_timeout_seconds": 6}, {"google_timeout_seconds": float("inf")}, {"auth_challenge_limit_per_minute": 0}, {"auth_exchange_limit_per_minute": 1001}])
def test_provider_waits_and_rate_budgets_are_bounded(override):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=URL, **override)

"""Keep configuration tests independent of real machine/local credentials."""

import os

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.upper().startswith("IDENTITY_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def settings():
    from identity_workspace.config import Settings

    return Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+psycopg://example_user:synthetic_password@localhost:5432/example_db",
    )

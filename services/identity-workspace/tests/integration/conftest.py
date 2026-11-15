"""Disposable real PostgreSQL; never connect tests to a developer's database."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import secrets
import subprocess
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text

from identity_workspace.config import Settings
from identity_workspace.database import create_database

SERVICE_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Postgres:
    compose_args: list[str]
    environment: dict[str, str] = field(repr=False)
    password: str = field(repr=False)
    port: int = 0

    def compose(self, *args: str) -> str:
        result = subprocess.run(
            [*self.compose_args, *args], env=self.environment,
            capture_output=True, text=True, timeout=90, check=False,
        )
        if result.returncode:
            # Docker sees mounted secret paths, not passwords in argv/env values.
            raise RuntimeError(f"Disposable PostgreSQL command failed: {result.stderr.strip()}")
        return result.stdout.strip()

    def settings(self, **overrides) -> Settings:
        return Settings(
            _env_file=None,
            environment="test",
            database_url=f"postgresql+psycopg://identity_app:{self.password}@127.0.0.1:{self.port}/identity_db",
            **overrides,
        )


@pytest.fixture(scope="session")
def postgres(tmp_path_factory):
    directory = tmp_path_factory.mktemp("identity-postgres")
    (directory / "database-owner-password").write_text(secrets.token_urlsafe(32), encoding="utf-8")
    password = secrets.token_urlsafe(32)
    (directory / "database-app-password").write_text(password, encoding="utf-8")
    env_file = directory / "compose.env"
    env_file.write_text("", encoding="utf-8")
    environment = dict(os.environ)
    environment.update(IDENTITY_DB_SECRETS_DIR=str(directory), IDENTITY_DB_PORT="0")
    database = Postgres(
        compose_args=[
            "docker", "compose", "--file", str(SERVICE_ROOT / "compose.yaml"),
            "--project-name", f"identity-it-{uuid4().hex[:12]}", "--env-file", str(env_file),
        ],
        environment=environment,
        password=password,
    )
    try:
        database.compose("up", "--detach", "--wait", "--wait-timeout", "60", "db")
        binding = database.compose("port", "db", "5432")
        database.port = int(binding.rsplit(":", 1)[1])
        # Keep the automatically selected port if a recovery test recreates db,
        # so a running application's connection URL remains valid.
        database.environment["IDENTITY_DB_PORT"] = str(database.port)
        yield database
    finally:
        # Only this unique test project's containers and volume are disposable.
        database.compose("down", "--volumes", "--remove-orphans")


def run_migration(database, operation="upgrade", revision="head"):
    config = Config(str(SERVICE_ROOT / "alembic.ini"))
    with database.engine.connect() as connection:
        config.attributes["connection"] = connection
        if operation == "check":
            command.check(config)
        else:
            getattr(command, operation)(config, revision)


@pytest.fixture
def database(postgres):
    database = create_database(postgres.settings())
    try:
        run_migration(database)
        with database.engine.begin() as connection:
            connection.execute(text(
                "TRUNCATE identity_workspace.outbox, identity_workspace.sessions, "
                "identity_workspace.auth_challenges, identity_workspace.users, identity_workspace.auth_rate_limits CASCADE"
            ))
        yield database
    finally:
        database.close()


@pytest.fixture
def migrate(database):
    return lambda operation="upgrade", revision="head": run_migration(database, operation, revision)

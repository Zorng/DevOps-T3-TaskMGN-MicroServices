"""Lazy service-owned database resources and explicit transaction scopes."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from identity_workspace.config import Settings
from identity_workspace.persistence import SCHEMA_REVISION

READINESS_QUERY = text(
    "SELECT version_num = :expected_revision "
    "AND current_setting('transaction_read_only') = 'off' "
    "AND NOT pg_is_in_recovery() "
    + "".join(
        f"AND has_table_privilege('identity_workspace.{table}', '{privilege}') "
        for table in ("outbox", "users", "auth_challenges", "sessions", "auth_rate_limits",
                      "workspaces", "memberships", "invitations", "idempotency_records", "workspace_cursors")
        for privilege in ("SELECT", "INSERT", "UPDATE")
    ) +
    "FROM public.alembic_version"
)


class DatabaseResources(Protocol):
    """The lifecycle boundary that application tests can replace."""

    def close(self) -> None: ...

    def check_readiness(self) -> bool: ...


class Database:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """Commit on success; roll back on failure and always close the session."""
        with self._sessions.begin() as session:
            yield session

    def close(self) -> None:
        self.engine.dispose()

    def check_readiness(self) -> bool:
        """Check migrated, writable storage without inserting operational data."""
        with self.engine.connect() as connection:
            result = connection.execute(READINESS_QUERY, {"expected_revision": SCHEMA_REVISION})
            return result.scalar_one() is True


def create_database(settings: Settings) -> Database:
    """Configure the pool without opening a database connection."""
    engine = create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        hide_parameters=True,
        echo=False,
        connect_args={
            "connect_timeout": settings.db_connect_timeout_seconds,
            "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
            "application_name": "identity-workspace",
        },
    )
    return Database(engine)

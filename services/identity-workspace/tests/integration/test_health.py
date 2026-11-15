from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, text

from identity_workspace.database import create_database
from identity_workspace.main import create_app


def assert_health(client, ready_status):
    response = client.get("/health/ready")
    assert response.status_code == ready_status
    assert response.json() == {"status": "ok" if ready_status == 200 else "unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    live = client.get("/health/live")
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    expected = "1.0" if ready_status == 200 else "0.0"
    assert f"identity_readiness_status {expected}" in metrics.text


def test_readiness_uses_real_migrated_database_without_a_broker(database, postgres):
    app = create_app(postgres.settings(), database_factory=Mock(return_value=database))
    with TestClient(app) as client:
        assert_health(client, 200)
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM identity_workspace.outbox")) == 0


def test_readiness_rejects_missing_migration_and_recovers(database, postgres, migrate):
    migrate("downgrade", "base")
    app = create_app(postgres.settings(), database_factory=Mock(return_value=database))
    try:
        with TestClient(app) as client:
            assert_health(client, 503)
            migrate()
            assert_health(client, 200)
    finally:
        migrate()


def test_readiness_rejects_wrong_schema_revision(database, postgres):
    with database.engine.begin() as connection:
        connection.execute(text("UPDATE public.alembic_version SET version_num = 'unexpected_revision'"))
    try:
        app = create_app(postgres.settings(), database_factory=Mock(return_value=database))
        with TestClient(app) as client:
            assert_health(client, 503)
    finally:
        with database.engine.begin() as connection:
            connection.execute(text("UPDATE public.alembic_version SET version_num = '0001_identity_foundation'"))


def test_readiness_rejects_missing_outbox(database, postgres):
    with database.engine.begin() as connection:
        connection.execute(text("ALTER TABLE identity_workspace.outbox RENAME TO unavailable_outbox"))
    try:
        app = create_app(postgres.settings(), database_factory=Mock(return_value=database))
        with TestClient(app) as client:
            assert_health(client, 503)
    finally:
        with database.engine.begin() as connection:
            connection.execute(text("ALTER TABLE identity_workspace.unavailable_outbox RENAME TO outbox"))


def test_readiness_rejects_read_only_connections(database, postgres):
    readonly = create_database(postgres.settings())

    @event.listens_for(readonly.engine, "begin")
    def set_read_only(connection):
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")

    app = create_app(postgres.settings(), database_factory=Mock(return_value=readonly))
    with TestClient(app) as client:
        assert_health(client, 503)


@pytest.mark.parametrize("privilege", ["SELECT", "INSERT", "UPDATE"])
def test_readiness_rejects_missing_outbox_permission(database, postgres, privilege):
    with database.engine.begin() as connection:
        connection.execute(text(f"REVOKE {privilege} ON identity_workspace.outbox FROM identity_app"))
    try:
        app = create_app(postgres.settings(), database_factory=Mock(return_value=database))
        with TestClient(app) as client:
            assert_health(client, 503)
    finally:
        with database.engine.begin() as connection:
            connection.execute(text(f"GRANT {privilege} ON identity_workspace.outbox TO identity_app"))


def test_readiness_times_out_on_locked_schema_and_recovers(database, postgres):
    probe_database = create_database(postgres.settings(db_statement_timeout_ms=50))
    app = create_app(postgres.settings(), database_factory=Mock(return_value=probe_database))
    with TestClient(app) as client:
        with database.engine.begin() as blocker:
            blocker.execute(text("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE"))
            assert_health(client, 503)
        assert_health(client, 200)


def test_readiness_rejects_exhausted_connection_pool_and_recovers(database, postgres):
    settings = postgres.settings(
        db_pool_size=1, db_max_overflow=0, db_pool_timeout_seconds=0.05,
    )
    probe_database = create_database(settings)
    app = create_app(settings, database_factory=Mock(return_value=probe_database))
    with TestClient(app) as client:
        assert_health(client, 200)
        with probe_database.engine.connect():
            # The only connection is held: readiness must fail within its pool
            # wait limit while liveness and metrics remain independent of it.
            assert_health(client, 503)
        assert_health(client, 200)


def test_database_outage_preserves_liveness_and_readiness_recovers(database, postgres):
    app = create_app(postgres.settings(), database_factory=Mock(return_value=database))
    with TestClient(app) as client:
        assert_health(client, 200)
        try:
            postgres.compose("stop", "--timeout", "10", "db")
            assert_health(client, 503)
        finally:
            postgres.compose("up", "--detach", "--wait", "--wait-timeout", "60", "db")
        assert_health(client, 200)

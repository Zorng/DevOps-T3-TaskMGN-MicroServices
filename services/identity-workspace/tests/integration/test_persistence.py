from copy import deepcopy
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from identity_workspace.database import create_database
from identity_workspace.persistence import SCHEMA_REVISION, metadata, outbox

CONTRACT_ROOT = Path(__file__).resolve().parents[4] / "api_contracts"


@pytest.fixture
def event():
    examples = json.loads((CONTRACT_ROOT / "examples/events.json").read_text(encoding="utf-8"))
    event = deepcopy(next(item for item in examples if item["type"] == "invitation.created.v1"))
    event["id"] = str(uuid4())
    return event


def append_event(session, event, **overrides):
    values = {"event_id": UUID(event["id"]), "event_type": event["type"], "payload": event}
    values.update(overrides)
    session.execute(outbox.insert().values(**values))


def test_migration_applies_to_empty_database_and_repeated_upgrade_is_safe(database, migrate):
    migrate()
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_REVISION
        assert set(inspect(connection).get_table_names(schema="identity_workspace")) == {table.name for table in metadata.tables.values()}
        assert "outbox_pending" in {index["name"] for index in inspect(connection).get_indexes("outbox", schema="identity_workspace")}
    migrate("check")


def test_database_uses_expected_version_and_non_superuser_role(database):
    with database.engine.connect() as connection:
        assert connection.scalar(text("SHOW server_version_num")) == "180007"
        assert connection.scalar(text("SELECT current_database()")) == "identity_db"
        role = connection.execute(text("SELECT rolname, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user")).one()
        assert tuple(role) == ("identity_app", False, False, False)


def test_downgrade_and_upgrade_restore_the_schema(database, migrate):
    try:
        migrate("downgrade", "base")
        with database.engine.connect() as connection:
            assert not inspect(connection).has_schema("identity_workspace")
        migrate()
        with database.engine.connect() as connection:
            assert inspect(connection).has_table("outbox", schema="identity_workspace")
    finally:
        migrate()


def test_committed_event_is_durable_and_pending_without_a_broker(database, event):
    with database.transaction() as session:
        append_event(session, event)
    with database.engine.connect() as connection:
        row = connection.execute(select(outbox).where(outbox.c.event_id == UUID(event["id"]))).one()
        assert row.payload == event
        assert row.attempts == 0
        assert row.published_at is None
        assert row.created_at.tzinfo is not None
        assert row.next_attempt_at.tzinfo is not None


@pytest.fixture
def transaction_probe(database):
    with database.engine.begin() as connection:
        connection.execute(text("CREATE TABLE identity_workspace.transaction_probe (id uuid PRIMARY KEY)"))
    try:
        yield
    finally:
        with database.engine.begin() as connection:
            connection.execute(text("DROP TABLE identity_workspace.transaction_probe"))


def test_business_change_and_event_commit_together(database, transaction_probe, event):
    identifier = uuid4()
    with database.transaction() as session:
        session.execute(text("INSERT INTO identity_workspace.transaction_probe VALUES (:id)"), {"id": identifier})
        append_event(session, event)
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM identity_workspace.transaction_probe")) == 1
        assert connection.scalar(select(func.count()).select_from(outbox)) == 1


def test_business_change_and_event_roll_back_together(database, transaction_probe, event):
    with pytest.raises(RuntimeError, match="Synthetic business failure"):
        with database.transaction() as session:
            session.execute(text("INSERT INTO identity_workspace.transaction_probe VALUES (:id)"), {"id": uuid4()})
            append_event(session, event)
            raise RuntimeError("Synthetic business failure")
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM identity_workspace.transaction_probe")) == 0
        assert connection.scalar(select(func.count()).select_from(outbox)) == 0


def test_duplicate_event_id_rejects_the_transaction(database, transaction_probe, event):
    with database.transaction() as session:
        append_event(session, event)
    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(text("INSERT INTO identity_workspace.transaction_probe VALUES (:id)"), {"id": uuid4()})
            append_event(session, event)
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM identity_workspace.transaction_probe")) == 0
        assert connection.scalar(select(func.count()).select_from(outbox)) == 1


@pytest.mark.parametrize("damage", ["mismatched_id", "null_type", "missing_producer", "wrong_producer", "negative_attempts", "oversize", "task_event"])
def test_outbox_constraints_reject_invalid_storage(database, event, damage):
    overrides = {}
    if damage == "mismatched_id":
        overrides["event_id"] = uuid4()
    elif damage == "null_type":
        event["type"] = None
        overrides["event_type"] = "invitation.created.v1"
    elif damage == "missing_producer":
        del event["producer"]
    elif damage == "wrong_producer":
        event["producer"] = "task"
    elif damage == "negative_attempts":
        overrides["attempts"] = -1
    elif damage == "oversize":
        event["data"]["padding"] = "x" * 65536
    else:
        event["type"] = "task.assigned.v1"
    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            append_event(session, event, **overrides)
    with database.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(outbox)) == 0


def test_statement_timeout_cancels_query_and_connection_recovers(postgres):
    database = create_database(postgres.settings(db_statement_timeout_ms=50))
    try:
        with pytest.raises(OperationalError) as caught:
            with database.transaction() as session:
                session.execute(text("SELECT pg_sleep(1)"))
        assert caught.value.orig.sqlstate == "57014"
        with database.transaction() as session:
            assert session.scalar(text("SELECT 1")) == 1
    finally:
        database.close()


def test_pending_event_survives_database_restart(database, postgres, event):
    with database.transaction() as session:
        append_event(session, event)
    postgres.compose("restart", "--timeout", "10", "db")
    postgres.compose("up", "--detach", "--wait", "--wait-timeout", "60", "db")
    # Docker may allocate a new host port when restarting a port-0 mapping.
    postgres.port = int(postgres.compose("port", "db", "5432").rsplit(":", 1)[1])
    reconnected = create_database(postgres.settings())
    try:
        with reconnected.engine.connect() as connection:
            row = connection.execute(select(outbox).where(outbox.c.event_id == UUID(event["id"]))).one()
            assert row.payload == event
            assert row.published_at is None
    finally:
        reconnected.close()
